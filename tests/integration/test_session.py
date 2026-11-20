"""`spendlens ask` sessions across separate commands, with the real OpenAI / Anthropic SDKs against the stub LLM."""

import json

import pytest
from stubllm import StubLLM
from test_ask import ask
from typer.testing import CliRunner

from spendlens.ai.session import CONTEXT_TURNS, SessionError, Sessions
from spendlens.cli import app

runner = CliRunner()


def msgs(stub, i=-1):
    return stub.requests[i]["body"]["messages"]


@pytest.mark.parametrize("provider", ["openai_compatible", "anthropic"])
def test_follow_up_in_a_new_command_remembers_the_conversation(provider):
    with StubLLM([("final", "There are 149 over-provisioned ECS tasks.")]) as stub:
        r = ask(stub, "Which ECS services are over-provisioned?", "--new", provider=provider)
        assert r.exit_code == 0, r.output
        assert "session s-" in r.output and "spendlens ask -C" in r.output
    with StubLLM([("final", "uat1 has 72 of them.")]) as stub:
        r = ask(stub, "Which cluster has most?", "-C", provider=provider)
        assert r.exit_code == 0, r.output
        m = [x for x in msgs(stub) if x["role"] != "system"]
    assert [x["role"] for x in m] == ["user", "assistant", "user"]
    assert m[0]["content"] == "Which ECS services are over-provisioned?"
    assert m[1]["content"] == "There are 149 over-provisioned ECS tasks." and m[2]["content"] == "Which cluster has most?"
    assert "1 earlier question" in r.output


def test_named_sessions_are_separate_and_new_starts_over(tmp_path):
    with StubLLM([("final", "A1")]) as stub:
        ask(stub, "about ecs", "-S", "ecs")
    with StubLLM([("final", "B1")]) as stub:
        ask(stub, "about rds", "-S", "rds")
    with StubLLM([("final", "A2")]) as stub:
        ask(stub, "more ecs", "-S", "ecs")
        assert [x["content"] for x in msgs(stub) if x["role"] != "system"] == ["about ecs", "A1", "more ecs"]
    with StubLLM([("final", "A3")]) as stub:
        ask(stub, "fresh start", "-S", "ecs", "--new")
        assert [x["content"] for x in msgs(stub) if x["role"] != "system"] == ["fresh start"]
    r = runner.invoke(app, ["session", "list"])
    assert r.exit_code == 0 and "ecs" in r.output and "rds" in r.output
    r = runner.invoke(app, ["session", "show", "rds"])
    assert "about rds" in r.output and "B1" in r.output
    assert runner.invoke(app, ["session", "delete", "rds"]).exit_code == 0
    assert runner.invoke(app, ["session", "show", "rds"]).exit_code == 1
    assert "Deleted 1 session" in runner.invoke(app, ["session", "clear", "--yes"]).output


def test_follow_ups_skip_the_answer_cache_and_context_is_capped(tmp_path):
    with StubLLM([("final", "first")]) as stub:
        ask(stub, "same question", "-S", "c")
    with StubLLM([("final", "second")]) as stub:  # same text, but a follow-up: never served from the cache
        r = ask(stub, "same question", "-S", "c", "--json")
        assert len(stub.requests) == 1 and json.loads(r.stdout[r.stdout.index("{"):])["answer"] == "second"
    s = Sessions(tmp_path / "s").open("cap")
    for i in range(CONTEXT_TURNS + 5):
        s.add(f"q{i}", "a" * 5000, model="m", live=False, source="demo", tool_calls=0)
    ctx = s.context()
    assert len(ctx) == CONTEXT_TURNS and ctx[0][0] == "q5" and ctx[-1][1].endswith("[answer shortened]")


def test_continue_needs_a_session_and_names_are_checked(tmp_path):
    with pytest.raises(SessionError):
        Sessions(tmp_path / "none").open(None, resume=True)
    with pytest.raises(SessionError):
        Sessions(tmp_path).open("../etc/passwd")
    with StubLLM([("final", "x")]) as stub:
        assert ask(stub, "q", "-C").exit_code == 2


ANSWER_MD = """**92 services** can be right-sized.

| Cluster | Service | vCPU | Memory |
|---|---|---|---|
| uat1 | stored-card-service | 2.00 | 4 GB |

<script>alert(1)</script> [x](javascript:alert(1))"""


def test_answer_saved_as_page_and_markdown(tmp_path):
    out = tmp_path / "ecs.html"
    script = [("tool", "history_overview", {}), ("final", ANSWER_MD)]
    with StubLLM(script) as stub:
        r = ask(stub, "Which ECS services should be right-sized?", "-o", str(out))
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert "Which ECS services should be right-sized?" in html and "<table>" in html and "stored-card-service" in html
    assert "<strong>92 services</strong>" in html and "1 tool call" in html and "history_overview" in html
    assert "<script>alert(1)</script>" not in html and 'href="javascript:' not in html  # account text stays text
    with StubLLM([("final", ANSWER_MD)]) as stub:
        ask(stub, "same as markdown", "-o", str(tmp_path / "a.md"), "--fresh")
    md = (tmp_path / "a.md").read_text()
    assert md.startswith("## same as markdown") and "| uat1 | stored-card-service |" in md


def test_whole_session_saved_as_one_page(tmp_path):
    with StubLLM([("final", "first answer")]) as stub:
        ask(stub, "q one", "-S", "pg")
    with StubLLM([("final", "second answer")]) as stub:
        ask(stub, "q two", "-S", "pg")
    out = tmp_path / "s.html"
    r = runner.invoke(app, ["session", "show", "pg", "-o", str(out)])
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert "Session pg" in html and "Question 1" in html and "q two" in html and "second answer" in html


def test_prompt_never_writes_html():
    from spendlens.ai.prompts import system_prompt
    from spendlens.config import load_config

    p = system_prompt(load_config(environ={}), False)
    assert "Never write HTML" in p and "-o report.html" in p
