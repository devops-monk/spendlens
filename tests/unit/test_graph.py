"""The resource graph: queries, collection, cleanup groups, impact, rendering and history (no AWS, no Steampipe)."""

import sqlite3
from datetime import UTC, datetime

import pytest

from spendlens.ai.catalog import load as load_catalog
from spendlens.ai.sql_check import check
from spendlens.config import GraphConfig, load_config
from spendlens.graph import render
from spendlens.graph.analyze import cleanup_groups, impact, link_findings
from spendlens.graph.collect import collect
from spendlens.graph.demo import ACCOUNT, FakeClient, rows
from spendlens.graph.model import Edge, Graph, Node
from spendlens.graph.sources import SOURCES
from spendlens.history import HistoryStore
from spendlens.models import Finding, Status

NOW = datetime(2026, 11, 9, 6, 20, tzinfo=UTC)


@pytest.fixture
def demo():
    cfg = load_config(environ={})
    g, meta = collect(cfg, client=FakeClient(rows()))
    return g, meta, cfg


def groups(g, meta, **kw):
    return {grp.kind + ":" + grp.anchor.rsplit("/", 1)[-1].rsplit(":", 1)[-1]: grp
            for grp in cleanup_groups(g, GraphConfig(**kw), now=NOW, collected=set(meta["counts"]))}


def test_every_query_passes_the_checker_against_the_aws_plugin_catalog():
    cat = load_catalog()
    for s in SOURCES:
        res = check(s.sql, cat)
        assert res.ok, (s.name, res.as_dict())
        assert res.tables == [s.table]
        assert s.sql.lstrip().lower().startswith("select ")


def test_collect_builds_nodes_and_resolves_links_by_aws_id(demo):
    g, meta, _ = demo
    assert not meta["skipped"] and set(meta["counts"]) == {s.name for s in SOURCES}
    inst = g.find("legacy-reporting")[0]
    labels = {(e.label, g.nodes[e.to_id].short_id) for e in g.out(inst.id)}
    assert {("mounts", "vol-0rptdata"), ("mounts", "vol-0rptroot"), ("eni", "eni-0legacyrpt"),
            ("in subnet", "subnet-0priv-a"), ("launched from", "ami-0golden2024")} <= labels
    vol = g.find("vol-0rptdata")[0]
    assert vol.monthly_cost == 50.0 and vol.cost_basis == "estimate"  # 500 GB gp2 at the default price book
    assert [g.nodes[e.to_id].short_id for e in g.out(vol.id) if e.label == "snapshot"] == [
        f"snap-0rptdata{i}" for i in range(4)]
    assert inst.monthly_cost == 0.0 and inst.attrs["stopped_since"].startswith("2026-08-02")
    # links to resources that were not collected are dropped, not invented
    assert not any("vol-0deleted01" in e.to_id or "vol-0deleted01" in e.from_id for e in g.edges)


def test_a_failing_source_is_skipped_with_the_reason_and_its_rules_do_not_run():
    cfg = load_config(environ={})
    g, meta = collect(cfg, client=FakeClient(rows(), fail={"aws_ebs_volume": "AccessDenied: ec2:DescribeVolumes"}))
    assert "ebs_volume" not in meta["counts"] and "no permission" in meta["skipped"]["ebs_volume"]
    kinds = {grp.kind for grp in cleanup_groups(g, GraphConfig(), now=NOW, collected=set(meta["counts"]))}
    # with no volumes collected every snapshot would look orphaned: those rules are switched off instead
    assert not kinds & {"orphan_snapshots", "unattached_volume", "stopped_instance"}
    assert "unused_ami" in kinds


def test_cleanup_groups(demo):
    g, meta, _ = demo
    gs = groups(g, meta)
    stopped = gs["stopped_instance:i-0legacyrpt"]
    assert stopped.monthly_saving == 163.65  # 2 volumes 60 + EIP 3.65 + 4 snapshots 100
    assert {g.nodes[m].short_id for m in stopped.members} == {
        "i-0legacyrpt", "vol-0rptroot", "vol-0rptdata", "eipalloc-0rpt", *(f"snap-0rptdata{i}" for i in range(4))}
    assert stopped.owner == "data" and "98 days" in stopped.evidence[0]
    assert gs["orphan_snapshots:snap-0oldvol4"].monthly_saving == 62.5
    assert gs["unused_ami:ami-0exp2023"].monthly_saving == 13.0  # its 2 snapshots
    assert gs["idle_load_balancer:7c"].members[1].endswith("old-promo-tg/2b")
    assert gs["rds_orphan_snapshots:reports-db-final-1"].monthly_saving == 19.0
    assert "unattached_volume:vol-0olddata" in gs and "unassociated_eip:eipalloc-0spare" in gs
    # not cleanup candidates: stopped last week, an AMI a stopped instance was launched from, AMI-backing snapshots,
    # a load balancer with healthy targets, a live database's snapshot
    flat = {m for grp in gs.values() for m in grp.members}
    for keep in ("i-0batchwkr", "ami-0golden2024", "snap-0golden", "snap-0ami7root", "payments-alb",
                 "payments-db-pre-upgrade", "eipalloc-0nat"):
        assert not any(keep in m for m in flat), keep
    # each resource in at most one group
    members = [m for grp in gs.values() for m in grp.members]
    assert len(members) == len(set(members))


def test_thresholds_come_from_config(demo):
    g, meta, _ = demo
    assert "stopped_instance:i-0batchwkr" in groups(g, meta, stopped_days=2)
    assert "stopped_instance:i-0legacyrpt" not in groups(g, meta, stopped_days=200)


def test_waste_alarms_are_linked_not_added(demo):
    g, meta, _ = demo
    vol = g.find("vol-0rptdata")[0]
    fs = [Finding(control="ebs_volume_on_stopped_instance", resource=vol.id, status=Status.ALARM, est_monthly_saving=50),
          Finding(control="ebs_volume_unattached", resource="vol-0olddata", status=Status.ALARM, est_monthly_saving=20),
          Finding(control="ebs_volume_unattached", resource="vol-elsewhere", status=Status.ALARM, est_monthly_saving=9)]
    gs = cleanup_groups(g, GraphConfig(), now=NOW, collected=set(meta["counts"]))
    link_findings(gs, g, fs)
    by = {grp.kind + ":" + grp.anchor.rsplit("/", 1)[-1]: grp for grp in gs}
    assert by["stopped_instance:i-0legacyrpt"].findings_saving == 50
    assert by["stopped_instance:i-0legacyrpt"].monthly_saving == 163.65  # the group total is not inflated
    assert by["unattached_volume:vol-0olddata"].findings[0]["control"] == "ebs_volume_unattached"


def test_impact(demo):
    g, _, _ = demo
    r = impact(g, g.find("vol-0rptdata")[0].id)
    assert [x["why"] for x in r["breaks"]] == ["EC2 instance legacy-reporting mounts it"]
    assert len(r["kept"]) == 4 and r["monthly_cost"] == 50.0
    r = impact(g, g.find("legacy-reporting")[0].id)
    assert {x["id"].rsplit("/", 1)[-1] for x in r["left_behind"]} >= {"vol-0rptroot", "vol-0rptdata"}
    assert r["left_behind_cost"] >= 60
    r = impact(g, g.find("snap-0golden")[0].id)
    assert "golden-base-2024 backed by it" in r["breaks"][0]["why"]


def test_around_does_not_walk_through_hubs(demo):
    g, _, _ = demo
    sub = g.around(g.find("legacy-reporting")[0].id, 2)
    names = {n.label for n in sub.nodes.values()}
    assert "priv-a" in names and "payments-api-1" not in names and "payments-db" not in names


def test_svg_is_escaped_deterministic_and_folds_long_columns():
    nodes = [Node("i-1", "ec2_instance", '<script>alert(1)</script>', monthly_cost=5)]
    nodes += [Node(f"v-{i}", "ebs_volume", f"v{i}", monthly_cost=1) for i in range(20)]
    g = Graph(nodes, [Edge("i-1", f"v-{i}", "mounts") for i in range(20)])
    a = render.svg(g, focus="i-1")
    assert a == render.svg(g, focus="i-1")
    assert "<script>" not in a and "&lt;script&gt;" in a
    assert "+9 more" in a  # 12 per column: 11 shown, the rest folded into one node
    assert "mounts</text>" in a and a.count("mounts</text>") == 1  # one label per resource and kind of link
    md = render.mermaid(g, focus="i-1")
    assert md.startswith("flowchart LR") and "-->|mounts|" in md
    dot = render.dot(g)
    assert dot.startswith("digraph") and '"i-1" -> "v-0"' in dot


def test_history_round_trip_and_migration_from_v2(tmp_path, demo):
    g, meta, _ = demo
    path = tmp_path / "h.db"
    with HistoryStore(path) as s:
        run = s.save_run("graph", account_id=ACCOUNT, meta=meta)
        s.save_graph(run, *g.to_rows())
        back = Graph.from_rows(*s.graph(run))
    assert back.stats() == g.stats()
    assert back.find("vol-0rptdata")[0].attrs["attached_to"] == ["i-0legacyrpt"]
    # an older history (schema v2) gains the graph tables, keeping its runs
    old = tmp_path / "old.db"
    with HistoryStore(old) as s:
        s.save_run("waste", [Finding(control="c", resource="r", status=Status.OK)])
        s.conn.execute("drop table graph_nodes")
        s.conn.execute("drop table graph_edges")
        s.conn.execute("pragma user_version = 2")
        s.conn.commit()
    with HistoryStore(old) as s:
        assert s.latest_run("waste") is not None
        s.save_graph(s.save_run("graph"), *g.to_rows())
    assert sqlite3.connect(old).execute("pragma user_version").fetchone()[0] == 3


def test_expired_login_stops_collection_once():
    from spendlens.graph.collect import LoginExpired

    cfg = load_config(environ={}, sets=["aws.profile=non-prd-fs"])
    client = FakeClient(rows(), fail={"aws_ec2_instance": "get credentials: the SSO session has expired or is invalid"})
    with pytest.raises(LoginExpired, match="aws sso login --profile non-prd-fs"):
        collect(cfg, client=client)
    assert len(client.sql) == 1  # did not try the other sources
