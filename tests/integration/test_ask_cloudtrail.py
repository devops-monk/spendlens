"""CloudTrail questions with --live: the right table is found, every query is bounded in time before it reaches
AWS, and an unbounded query is corrected from the checker's advice (stub LLM, fake Steampipe; no AWS)."""

import json

import pytest
from stubllm import StubLLM
from typer.testing import CliRunner

from spendlens.ai import tools as tools_mod
from spendlens.ai.catalog import load
from spendlens.ai.prompts import system_prompt
from spendlens.ai.sql_check import check
from spendlens.cli import app
from spendlens.config import load_config

runner = CliRunner()
CAT = load()
LOOKUP = "aws_cloudtrail_lookup_event"


@pytest.mark.parametrize("question", ["who deleted the s3 bucket", "who created this instance",
                                      "which security group rules changed yesterday", "show console logins this week",
                                      "when was the payments-db snapshot deleted", "api calls by role last hour"])
def test_activity_questions_find_cloudtrail(question):
    assert LOOKUP in [r["table"] for r in CAT.find(question, k=3)]


@pytest.mark.parametrize("question,table", [("list my s3 buckets", "aws_s3_bucket"),
                                            ("which instances are stopped", "aws_ec2_instance"),
                                            ("instances created last week", "aws_ec2_instance"),
                                            ("unattached volumes", "aws_ebs_volume")])
def test_inventory_questions_still_find_the_resource(question, table):
    found = [r["table"] for r in CAT.find(question, k=8)]
    assert found[0] == table and (LOOKUP not in found[:1])


def test_cloudtrail_queries_must_be_bounded_where_aws_sees_it():
    def errors(sql):
        return [p.problem + " " + p.fix for p in check(sql, CAT).errors]

    e = errors(f"select event_name from {LOOKUP} where event_name = 'DeleteBucket'")
    assert e and "start_time = now() - interval '24 hours' AND end_time = now()" in e[0]
    assert errors(f"select event_name from {LOOKUP} where event_time > now() - interval '1 day'")  # not sent to AWS
    assert not errors(f"select event_name, username from {LOOKUP} where start_time = now() - interval '24 hours' "
                      "and end_time = now() and event_name = 'DeleteBucket'")
    trail = "select event_name from aws_cloudtrail_trail_event where log_group_name = 'g'"
    assert errors(trail) and not errors(trail + " and timestamp >= now() - interval '1 day'")
    assert not errors(trail + " and timestamp between now() - interval '2 days' and now()")
    note = CAT.brief(LOOKUP)["note"]
    assert "start_time = <from> AND end_time = <to>" in note and "90 days" in note


def test_prompt_explains_cloudtrail():
    p = system_prompt(load_config(environ={}), True)
    assert "cloudtrail_events first" in p and "console-logins" in p and "data events" in p and "ONE" in p
    assert "aws_cloudtrail_lookup_event first" not in system_prompt(load_config(environ={}), False)  # live only


class Fake:
    def __init__(self):
        self.queries = []

    def client(self):
        fake = self

        class C:
            _conn = None

            def query(self, sql, params=None, limit=None):
                if "information_schema.columns" in sql:
                    return [{"table_name": LOOKUP, "column_name": c, "data_type": "text"} for c in CAT.get(LOOKUP).columns]
                fake.queries.append(sql)
                return [{"event_name": "DeleteBucket", "username": "alice", "event_time": "2026-11-19T10:02:00Z",
                         "resource_name": "old-reports"}]

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return C()


def test_unbounded_query_is_refused_then_corrected(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(tools_mod.Toolbox, "_client", lambda self: fake.client())
    bad = f"select username, event_time from {LOOKUP} where event_name = 'DeleteBucket'"
    good = (f"select username, event_time, resource_name from {LOOKUP} where start_time = now() - interval '24 hours' "
            "and end_time = now() and event_name = 'DeleteBucket' order by event_time desc")
    script = [("tool", "aws_find_tables", {"question": "who deleted the bucket"}), ("tool", "aws_query", {"sql": bad}),
              ("tool", "aws_query", {"sql": good}), ("final", "alice deleted old-reports at 10:02 UTC yesterday.")]
    with StubLLM(script) as stub:
        r = runner.invoke(app, ["ask", "--source", "demo", "--live", "--json", "--set", "llm.provider=openai_compatible",
                                "--set", "llm.model=stub-model", "--set", f"llm.base_url={stub.url}/v1",
                                "Who deleted the old-reports bucket?"], env={"SPENDLENS_NO_PROGRESS": "1"})
        assert r.exit_code == 0, r.output
        found = [m for m in stub.requests[1]["body"]["messages"] if m["role"] == "tool"][-1]["content"]
    doc = json.loads(r.stdout[r.stdout.index("{"):])
    assert found.startswith(f'{{"tables":[{{"table":"{LOOKUP}"')  # first match
    steps = doc["steps"]
    assert [s["ok"] for s in steps] == [True, False, True]
    assert "needs a time range" in steps[1]["error"] and "start_time = now()" in steps[1]["error"]
    assert len(fake.queries) == 1 and "start_time = now() - interval '24 hours'" in fake.queries[0]  # bad one never ran
    assert "alice" in doc["answer"]
