"""The assistant on the resource graph: its tools, and ```graph blocks drawn in saved answer pages (stub LLM)."""

import json

import pytest
from stubllm import StubLLM
from test_ask import ask

from spendlens.ai.tools import Toolbox
from spendlens.config import load_config
from spendlens.history import HistoryStore, demo_db_path


def tools(db=None):
    return Toolbox(load_config(environ={}), db or demo_db_path())


def call(tb, name, **args):
    r = tb.execute("1", name, args)
    assert not r.is_error, r.content
    return json.loads(r.content)


def test_resource_context():
    d = call(tools(), "resource_context", resource="vol-0rptdata")
    assert d["resource"]["monthly_cost"] == 50.0 and d["resource"]["facts"]["attached_to"] == ["i-0legacyrpt"]
    assert {"id": "i-0legacyrpt", "link": "mounts (it -> this)"}.items() <= next(
        x for x in d["neighbours"] if x["id"] == "i-0legacyrpt").items()
    assert d["cleanup_group"]["kind"] == "stopped_instance" and d["cleanup_group"]["monthly_saving"] == 163.65
    assert {x["tag"] if "tag" in x else x["key"] for x in d["suggested_tags"]} >= {"Team"}
    assert d["if_deleted"]["breaks"] == ["EC2 instance legacy-reporting mounts it"]
    nat = call(tools(), "resource_context", resource="prod-nat")
    assert [x["target_name"] for x in nat["shared_costs"]] == ["priv-a", "priv-b"]
    assert nat["shared_costs"][0]["amount"] == 16.43
    many = call(tools(), "resource_context", resource="i-0")
    assert len(many["matches"]) > 1 and "call again" in many["note"]


def test_cleanup_groups_and_suggested_owners():
    d = call(tools(), "cleanup_groups", top=3)
    assert d["group_count"] == 8 and len(d["groups"]) == 3 and d["new_beyond_waste_alarms"] == 258.15
    d = call(tools(), "cleanup_groups", kind="unused_ami")
    assert [g["title"] for g in d["groups"]] == ["Unused AMI experiment-gpu-2023 with 2 snapshots"]
    d = call(tools(), "suggested_owners", key="Team", min_confidence="medium")
    assert d["suggestions"] and all(x["confidence"] in ("high", "medium") for x in d["suggestions"])
    d = call(tools(), "suggested_owners", resource="vol-0payapi1")
    assert {(x["tag"], x["suggested"]) for x in d["suggestions"]} == {("Team", "payments"), ("Environment", "prod")}


def test_no_saved_graph_says_how_to_get_one(tmp_path):
    db = tmp_path / "h.db"
    with HistoryStore(db) as s:
        s.save_run("waste")
    d = call(tools(db), "resource_context", resource="x")
    assert "spendlens graph collect" in d["note"]


@pytest.mark.parametrize("provider", ["openai_compatible", "anthropic"])
def test_answer_with_a_graph_block_is_drawn_in_the_saved_page(tmp_path, provider):
    answer = ("legacy-reporting is stopped but still costs **$163.65/month** (cleanup group).\n\n"
              "```graph\ni-0legacyrpt\n```\n\n```graph\nno-such-resource\n```")
    script = [("tool", "resource_context", {"resource": "legacy-reporting"}), ("final", answer)]
    out = tmp_path / "a.html"
    with StubLLM(script) as stub:
        r = ask(stub, "What is attached to legacy-reporting?", "-o", str(out), provider=provider)
        assert r.exit_code == 0, r.output
        names = {t.get("name") or t["function"]["name"] for t in stub.requests[0]["body"]["tools"]}
        assert {"resource_context", "cleanup_groups", "suggested_owners"} <= names
        system = json.dumps(stub.requests[0]["body"])
        assert "resource_context" in system and "```graph" in system
    html = out.read_text()
    assert 'class="gw ans-graph"' in html and '<svg class="slg in-page"' in html and "vol-0rptdata" in html
    assert "language-graph\">no-such-resource" in html  # an unknown id stays a code block
