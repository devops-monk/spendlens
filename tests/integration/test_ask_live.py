"""spendlens ask --live end to end: real OpenAI SDK -> stub LLM, and a fake Steampipe (no network, no AWS).

Covers the schema-aware workflow (find -> describe -> checked query), self-correction after a wrong column,
waiting for a slow query, and the two caches (live results and whole answers)."""

import json
import threading
import time

import pytest
from stubllm import StubLLM
from typer.testing import CliRunner

from spendlens.ai import tools as tools_mod
from spendlens.cli import app

runner = CliRunner()

LIVE_COLUMNS = [("volume_id", "text"), ("size", "bigint"), ("state", "text"), ("attachments", "jsonb"),
                ("region", "text")]


class FakeSteampipe:
    """Answers information_schema and aws_ebs_volume queries; records every data query."""

    def __init__(self, delay: float = 0.0):
        self.delay, self.data_queries, self.lock = delay, [], threading.Lock()

    def client(self, _toolbox=None):
        fake = self

        class C:
            _conn = None

            def query(self, sql, params=None, limit=None):
                if "information_schema.columns" in sql:
                    return [{"table_name": "aws_ebs_volume", "column_name": c, "data_type": t} for c, t in LIVE_COLUMNS]
                with fake.lock:
                    fake.data_queries.append(sql)
                time.sleep(fake.delay)
                return [{"volume_id": "vol-0abc", "size": 50}]

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return C()


@pytest.fixture
def steampipe(monkeypatch):
    fake = FakeSteampipe()
    monkeypatch.setattr(tools_mod.Toolbox, "_client", lambda self: fake.client())
    return fake


def ask(stub, *args):
    cmd = ["ask", "--source", "demo", "--live", "--json", "--set", "llm.provider=openai_compatible",
           "--set", "llm.model=stub-model", "--set", f"llm.base_url={stub.url}/v1", *args]
    r = runner.invoke(app, cmd, env={"SPENDLENS_NO_PROGRESS": "1"})
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout[r.stdout.index("{"):])


BAD = "select volume_id, size_gb from aws_ebs_volume where state = 'available'"
GOOD = "select volume_id, size from aws_ebs_volume where state = 'available'"


def test_finds_describes_corrects_and_answers(steampipe):
    script = [("tool", "aws_find_tables", {"question": "unattached EBS volumes"}),
              ("tool", "aws_query", {"sql": BAD}),
              ("tool", "aws_describe_table", {"table": "aws_ebs_volume"}),
              ("tool", "aws_query", {"sql": GOOD}),
              ("final", "Unattached: {last}")]
    with StubLLM(script) as stub:
        doc = ask(stub, "Which EBS volumes are unattached?")
    steps = doc["steps"]
    assert [s["tool"] for s in steps] == ["aws_find_tables", "aws_query", "aws_describe_table", "aws_query"]
    assert [s["ok"] for s in steps] == [True, False, True, True]
    assert "size_gb does not exist" in steps[1]["error"] and '"did_you_mean":["size"]' in steps[1]["error"]
    assert "vol-0abc" in doc["answer"] and '"cached":false' in doc["answer"]
    assert len(steampipe.data_queries) == 1 and steampipe.data_queries[0].endswith("limit 501")  # bad SQL never ran
    described = [m for m in stub.requests[3]["body"]["messages"] if m["role"] == "tool"][-1]["content"]
    cols = [c["name"] for c in json.loads(described)["columns"]]
    assert cols == [c for c, _ in LIVE_COLUMNS] and "installed plugin" in described  # live columns win
    system = stub.requests[0]["body"]["messages"][0]["content"]
    assert "aws_find_tables" in system and "Never guess a column name" in system


def test_slow_query_is_awaited_without_using_steps(monkeypatch):
    fake = FakeSteampipe(delay=1.5)
    monkeypatch.setattr(tools_mod.Toolbox, "_client", lambda self: fake.client())
    script = [("tool", "aws_query", {"sql": GOOD}), ("tool", "aws_query_result", {"job_id": "q1"}),
              ("final", "Rows: {last}")]
    with StubLLM(script) as stub:
        doc = ask(stub, "Volumes?", "--set", "llm.query_wait_s=1", "--max-steps", "1")
    assert not doc["hit_step_limit"]
    first = [m for m in stub.requests[1]["body"]["messages"] if m["role"] == "tool"][-1]["content"]
    assert '"status":"running"' in first and '"job_id":"q1"' in first
    assert "vol-0abc" in doc["answer"] and [s["tool"] for s in doc["steps"]] == ["aws_query", "aws_query_result"]


def test_answer_and_query_caches(steampipe):
    script = [("tool", "aws_query", {"sql": GOOD}), ("final", "Answer: {last}")]
    with StubLLM(script) as stub:
        first = ask(stub, "Which volumes are unattached?")
    assert len(steampipe.data_queries) == 1 and not first.get("cached")

    with StubLLM([("final", "should not be asked")]) as stub:  # same question, same data: no tokens at all
        again = ask(stub, "which volumes are unattached")
    assert again["cached"] and again["answer"] == first["answer"] and stub.requests == []

    with StubLLM(script) as stub:  # another question, same SQL: the live result comes from the cache
        other = ask(stub, "List unattached volumes please")
    assert '"cached":true' in other["answer"] and len(steampipe.data_queries) == 1

    with StubLLM(script) as stub:  # --fresh: ask the model and query AWS again
        fresh = ask(stub, "Which volumes are unattached?", "--fresh")
    assert stub.requests and '"cached":false' in fresh["answer"] and len(steampipe.data_queries) == 2


def test_steampipe_down_still_guides_from_bundled_catalog(monkeypatch):
    def down(self):
        from spendlens.steampipe import SteampipeError

        class C:
            _conn = None

            def query(self, *a, **k):
                raise SteampipeError("Cannot connect to Steampipe")

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return C()

    monkeypatch.setattr(tools_mod.Toolbox, "_client", down)
    script = [("tool", "aws_describe_table", {"table": "aws_ebs_volume"}), ("tool", "aws_query", {"sql": GOOD}),
              ("final", "{last}")]
    with StubLLM(script) as stub:
        doc = ask(stub, "volumes")
    assert doc["steps"][0]["ok"] and doc["steps"][1]["ok"] is False
    assert "Cannot connect to Steampipe" in doc["steps"][1]["error"]
