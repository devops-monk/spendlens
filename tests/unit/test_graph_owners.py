"""Suggested owner tags from the resource graph."""

import shlex

from spendlens.config import load_config
from spendlens.graph.collect import collect
from spendlens.graph.demo import FakeClient, rows
from spendlens.graph.model import Edge, Graph, Node
from spendlens.graph.owners import by_resource, script, suggest_owners

KEYS = ["Environment", "Team", "team", "Owner"]


def demo():
    g, _ = collect(load_config(environ={}), client=FakeClient(rows()))
    return g


def get(sugg, name, key):
    return next((s for s in sugg if s.name == name and s.key.lower() == key.lower()), None)


def test_rules_and_confidence():
    s = suggest_owners(demo(), KEYS)
    v = get(s, "vol-0payapi1", "Team")
    assert (v.value, v.confidence) == ("payments", "high") and v.reason == "mounted by EC2 instance payments-api-1"
    assert get(s, "eni-0legacyrpt", "Team").value == "data"
    snap = get(s, "snap-0rptdata3", "Team")  # untagged snapshot of an untagged volume: via the volume's instance
    assert (snap.value, snap.confidence) == ("data", "medium") and "legacy-reporting" in snap.reason
    low = get(s, "report-export", "Team")
    assert (low.value, low.confidence) == ("platform", "low") and "3 of 3" in low.reason
    # tagged resources, AWS-managed interfaces, hubs and resources with nothing related get nothing
    for name in ("payments-api-1", "eni-0nat", "priv-a", "vol-0olddata", "snap-0oldvol0"):
        assert get(s, name, "Team") is None, name
    # Team and team are one key; the suggestion uses the spelling of the resource it comes from
    assert sum(1 for x in s if x.name == "vol-0payapi1" and x.key.lower() == "team") == 1
    assert [x.confidence for x in suggest_owners(demo(), KEYS, min_confidence="high")] == ["high"] * len(
        [x for x in s if x.confidence == "high"])


def test_disagreeing_sources_give_no_suggestion_and_subnet_needs_a_clear_majority():
    sub = Node("sub", "vpc_subnet", "s1")
    nodes = [sub, Node("vol", "ebs_volume", "shared"),
             Node("i1", "ec2_instance", "a", tags={"Team": "x"}), Node("i2", "ec2_instance", "b", tags={"Team": "y"}),
             Node("fn", "lambda_function", "f")]
    edges = [Edge("i1", "vol", "mounts"), Edge("i2", "vol", "mounts"), Edge("i1", "sub", "in subnet"),
             Edge("i2", "sub", "in subnet"), Edge("fn", "sub", "in subnet")]
    s = suggest_owners(Graph(nodes, edges), ["Team"])
    assert get(s, "shared", "Team") is None  # multi-attached to two teams: no guess
    assert get(s, "f", "Team") is None  # 1 x vs 1 y in the subnet: no majority
    nodes += [Node(f"i{k}", "ec2_instance", f"n{k}", tags={"Team": "x"}) for k in (3, 4)]
    edges += [Edge(f"i{k}", "sub", "in subnet") for k in (3, 4)]
    assert get(suggest_owners(Graph(nodes, edges), ["Team"]), "f", "Team").value == "x"  # 3 of 4


def test_script_is_quoted_and_low_confidence_is_commented_out():
    g = demo()
    g.nodes[g.find("payments-api-1")[0].id].tags["Team"] = "pay'ments; rm -rf /"
    s = suggest_owners(g, KEYS)
    text = script(s)
    assert text.startswith("#!/bin/sh") and "REVIEW BEFORE RUNNING" in text
    line = next(ln for ln in text.splitlines() if "eni-0payapi1" in ln and ln.startswith("aws "))
    assert shlex.split(line)[-1] == '{"Environment": "prod", "Team": "pay\'ments; rm -rf /"}'
    low = next(ln for ln in text.splitlines() if "function:report-export" in ln)
    assert low.startswith("# aws ")
    assert by_resource(s)["vol-0payapi1"][0].value
