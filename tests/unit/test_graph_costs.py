"""Billed per-resource cost for the graph: CUR via Tailpipe, Cost Explorer resource data, or the price book."""

import json
import stat
from datetime import UTC, date, datetime

import pytest

from spendlens.config import ConfigError, load_config
from spendlens.doctor import check_tailpipe
from spendlens.graph.collect import collect
from spendlens.graph.costs import (
    CostSourceError,
    apply,
    cur_sql,
    from_cost_explorer,
    monthly,
    parse_json,
    price,
    tailpipe_runner,
)
from spendlens.graph.demo import FakeClient, rows
from spendlens.history import HistoryStore

CUR_ROWS = [
    {"resource_id": "vol-0rptdata", "cost": 40.0, "first_seen": "2026-10-10 00:00:00", "last_seen": "2026-11-08 23:00:00",
     "currency": "USD"},
    {"resource_id": "arn:aws:ec2:eu-west-1::snapshot/snap-0oldvol0", "cost": 3.1, "first_seen": "2026-10-10",
     "last_seen": "2026-11-08", "currency": "USD"},
    {"resource_id": "i-0payapi1", "cost": 10.0, "first_seen": "2026-11-04", "last_seen": "2026-11-08", "currency": "USD"},
    {"resource_id": "i-not-in-graph", "cost": 99.0, "first_seen": "2026-10-10", "last_seen": "2026-11-08"},
]


def graph(cfg):
    return collect(cfg, client=FakeClient(rows()))[0]


def fake_tailpipe(tmp_path, out: str, code: int = 0):
    p = tmp_path / "tailpipe"
    (tmp_path / "out.json").write_text(out)
    p.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\ncat "{tmp_path}/out.json"\nexit {code}\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_sql_and_parsing():
    sql = cur_sql(30)
    assert "from aws_cost_and_usage_report" in sql and "interval 30 day" in sql and "< current_date" in sql
    with pytest.raises(ValueError):
        cur_sql(0)
    assert parse_json('{"columns": [], "rows": [{"a": 1}]}') == [{"a": 1}]
    assert parse_json('[{"a": 1}]') == [{"a": 1}] and parse_json("") == []
    with pytest.raises(CostSourceError):
        parse_json("not json")
    # a monthly rate from the days actually billed: 10 over 5 days is not 10 a month
    assert monthly(10, "2026-11-04", "2026-11-08", 30) == 60.88
    assert monthly(40, "2026-10-10", "2026-11-08", 30) == 40.58


def test_cur_prices_matching_resources_and_keeps_estimates_elsewhere():
    cfg = load_config(environ={})
    g = graph(cfg)
    info = price(g, cfg, runner=lambda sql: CUR_ROWS)
    assert info == {"source": "cur", "matched": 3, "billed_resources": 4, "days": 30}
    vol, snap, inst = (g.find(k)[0] for k in ("vol-0rptdata", "snap-0oldvol0", "payments-api-1"))
    assert (vol.monthly_cost, vol.cost_basis, vol.attrs["estimate"]) == (40.58, "cur", 50.0)
    assert snap.monthly_cost == 3.15 and "billed over 30 day(s)" in snap.attrs["cost_note"]
    assert inst.monthly_cost == 60.88 and "over 5 day(s)" in inst.attrs["cost_note"]
    other = g.find("vol-0rptroot")[0]
    assert (other.cost_basis, other.monthly_cost) == ("estimate", 10.0)


def test_fallback_order_and_modes(tmp_path):
    cfg = load_config(environ={}, sets=["graph.tailpipe_path=/nonexistent/tailpipe"])
    db = tmp_path / "h.db"
    with HistoryStore(db) as s:  # a recent `cost resources` run: 7-day windows, resource ARNs
        s.save_run("cost_resources", cost_rows=[
            {"kind": "p1", "period": "w2", "dimension": "resource", "amount": 14.0,
             "grp": "arn:aws:ec2:eu-west-1:123456789012:volume/vol-0rptroot"}],
            meta={"periods": [{"start": "2026-10-26", "end": "2026-11-02", "label": "w1"},
                              {"start": "2026-11-02", "end": "2026-11-09", "label": "w2"}]},
            started_at=datetime.now(UTC))
    g = graph(cfg)
    with HistoryStore(db, read_only=True) as s:
        info = price(g, cfg, store=s)
    assert info["source"] == "cost_explorer" and info["matched"] == 1  # no CUR configured for Tailpipe: not tried
    assert g.find("vol-0rptroot")[0].monthly_cost == round(14 / 7 * 30.4375, 2)
    g = graph(cfg)
    assert price(g, load_config(environ={}, sets=["graph.cost_source=estimate"]), store=None)["source"] == "estimate"
    only_cur = load_config(environ={}, sets=["graph.cost_source=cur", "graph.tailpipe_path=/nonexistent/tailpipe"])
    with HistoryStore(db, read_only=True) as s:
        assert price(graph(cfg), only_cur, store=s)["source"] == "estimate"  # no fallback when cur is required


def test_old_cost_explorer_data_is_not_used(tmp_path):
    db = tmp_path / "h.db"
    with HistoryStore(db) as s:
        s.save_run("cost_resources", cost_rows=[{"kind": "p0", "period": "w", "dimension": "resource", "grp": "x",
                                                 "amount": 1}],
                   meta={"periods": [{"start": "2026-01-01", "end": "2026-01-08", "label": "w"}]},
                   started_at=datetime(2026, 1, 9, tzinfo=UTC))
    with HistoryStore(db, read_only=True) as s:
        assert from_cost_explorer(s, today=date(2026, 11, 9)) == ({}, "")


def test_tailpipe_runner_and_doctor(tmp_path, monkeypatch):
    exe = fake_tailpipe(tmp_path, json.dumps({"columns": [], "rows": CUR_ROWS}))
    run = tailpipe_runner(str(exe))
    assert len(run(cur_sql(30))) == 4
    args = (tmp_path / "args").read_text()
    assert args.startswith("query select line_item_resource_id") and args.rstrip().endswith("--output json")
    g = graph(load_config(environ={}))
    assert apply(g, {"vol-0rptdata": {"monthly": 1.0, "cost": 1, "window_days": 30, "currency": "USD"}}, "cur", "x") == 1
    (tmp_path / "out.json").write_text(json.dumps({"rows": [{"n": 1234}]}))
    monkeypatch.setenv("TAILPIPE_INSTALL_DIR", str(tmp_path / "tp"))
    c = check_tailpipe(load_config(environ={}, sets=[f"graph.tailpipe_path={exe}"]))
    assert c.status == "warn" and "spendlens tailpipe init" in c.fix  # installed, nothing configured
    (tmp_path / "tp" / "config").mkdir(parents=True)
    (tmp_path / "tp" / "config" / "spendlens.tpc").write_text('partition "aws_cost_and_usage_report" "x" {}\n')
    c = check_tailpipe(load_config(environ={}, sets=[f"graph.tailpipe_path={exe}"]))
    assert (c.status, c.detail) == ("ok", "Cost and Usage Reports: 1,234 rows")
    bad = fake_tailpipe(tmp_path, "", code=1)
    with pytest.raises(CostSourceError):
        tailpipe_runner(str(bad))(cur_sql(30))
    assert check_tailpipe(load_config(environ={}, sets=["graph.tailpipe_path=/nonexistent/tailpipe"])).status == "ok"
    with pytest.raises(ConfigError):  # shell metacharacters are refused in the path
        load_config(environ={}, sets=["graph.tailpipe_path=tailpipe;rm -rf /"])
