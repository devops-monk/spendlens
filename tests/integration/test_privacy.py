"""What reaches the AI provider (redaction, the data note) and what stays local (owner-only files). Stub LLM only."""

import json
import stat

from stubllm import StubLLM
from test_ask import ask

from spendlens.ai.session import Sessions
from spendlens.history import HistoryStore
from spendlens.privacy import Redactor, leaves_account

SQL = ("select '123456789012' as account, 'alice@example.com' as who, '203.0.113.7' as ip, "
       "'arn:aws:iam::123456789012:role/Admin' as arn")


def test_redactor_round_trip() -> None:
    r = Redactor(["account_ids", "emails", "ips"])
    text = r.redact("acct 123456789012 and 210987654321, alice@example.com from 203.0.113.7, again 123456789012")
    assert text == "acct ACCOUNT_1 and ACCOUNT_2, EMAIL_1 from IP_1, again ACCOUNT_1"
    assert r.restore({"sql": "where account_id = 'ACCOUNT_2'", "x": ["EMAIL_1"]}) == {
        "sql": "where account_id = '210987654321'", "x": ["alice@example.com"]}
    assert Redactor([]).redact("123456789012") == "123456789012"  # off by default
    assert r.redact("version 1.2.3.4.5 and 1234567890123") == "version 1.2.3.4.5 and 1234567890123"
    assert leaves_account("openai_compatible") and not leaves_account("bedrock (eu-west-1)")


def test_the_provider_gets_placeholders_and_you_get_the_real_values() -> None:
    script = [("tool", "history_query", {"sql": SQL}), ("final", "Done: {last}")]
    with StubLLM(script) as stub:
        r = ask(stub, "Who is it?", "--json", extra_sets=["llm.redact=[account_ids, emails, ips]"])
        assert r.exit_code == 0, r.output
        # what spendlens sends: the tool results (the scripted model's own SQL is echoed back as its message)
        sent = json.dumps([m for q in stub.requests for m in q["body"]["messages"] if m.get("role") == "tool"])
    assert sent != "[]" and "123456789012" not in sent and "alice@example.com" not in sent and "203.0.113.7" not in sent
    assert "ACCOUNT_1" in sent and "EMAIL_1" in sent and "IP_1" in sent
    answer = json.loads(r.stdout[r.stdout.index("{"):])["answer"]
    assert "123456789012" in answer and "alice@example.com" in answer and "ACCOUNT_1" not in answer
    with StubLLM(script) as stub:  # without llm.redact the values go as they are
        ask(stub, "Who is it?", "--json", "--fresh")
        assert "123456789012" in json.dumps([m for q in stub.requests for m in q["body"]["messages"]
                                             if m.get("role") == "tool"])


def test_data_note_for_providers_outside_the_account(tmp_path) -> None:
    f = tmp_path / "budget.csv"
    f.write_text("service,budget\nEC2,100\n")
    with StubLLM([("final", "ok")]) as stub:
        r = ask(stub, "Summarise", "-a", str(f))
    assert "data note" in r.output and "outside your AWS account" in " ".join(r.output.split())
    with StubLLM([("final", "ok")]) as stub:
        r = ask(stub, "Summarise", "-a", str(f), extra_sets=["llm.egress_notice=false"])
    assert "data note" not in r.output


def test_local_stores_are_owner_only(tmp_path) -> None:
    db = tmp_path / "h.db"
    HistoryStore(db).close()
    s = Sessions(tmp_path / "sessions").open("x")
    s.add("q", "a", model="m", live=False, source="demo", tool_calls=0)
    for p in (db, s.path):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p
    assert stat.S_IMODE((tmp_path / "sessions").stat().st_mode) == 0o700
