"""unused buckets / load balancers (access logs + inventory), savings history and FOCUS pricing. Fakes only."""

from datetime import date
from typing import Any

import pytest
from typer.testing import CliRunner

from spendlens import usage
from spendlens.ai.tailpipe_tools import check as tailpipe_check
from spendlens.cli import app
from spendlens.config import load_config
from spendlens.features.savings_cmd import history_sql
from spendlens.graph.costs import focus_sql, from_cur

TODAY = date(2026, 11, 27)


class Steampipe:
    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        if "from aws_s3_bucket" in sql:
            return [{"name": "busy", "region": "eu-west-1", "logging": {"TargetBucket": "logs"}},
                    {"name": "quiet", "region": "eu-west-1", "logging": {"TargetBucket": "logs"}},
                    {"name": "nolog", "region": "eu-west-1", "logging": None}]
        if "aws_ec2_application_load_balancer" in sql:
            return [{"name": "web", "arn": "arn:alb/web", "load_balancer_attributes": [
                {"Key": "access_logs.s3.enabled", "Value": "true"}]},
                {"name": "idle", "arn": "arn:alb/idle", "load_balancer_attributes": [
                    {"Key": "access_logs.s3.enabled", "Value": "true"}]}]
        if "aws_ec2_classic_load_balancer" in sql:
            return [{"name": "legacy", "arn": "arn:clb", "access_log_enabled": False}]
        return []


def tailpipe(lo: str = "2026-10-01", hi: str = "2026-11-26") -> Any:
    def run(sql: str) -> list[dict[str, Any]]:
        if "min(tp_date)" in sql:
            return [{"lo": lo, "hi": hi}]
        if "aws_s3_server_access_log" in sql:
            assert "operation not like 'S3.%'" in sql  # lifecycle housekeeping is not use
            return [{"bucket": "busy", "requests": 120, "reads": 100, "writes": 20, "last_access": "2026-11-26 10:00",
                     "bytes_sent": 5000}]
        if "aws_alb_access_log" in sql:
            return [{"elb": "app/web/50dc6c495c0c9188", "requests": 9000, "last_access": "2026-11-26", "bytes": 1}]
        return []
    return run


def test_buckets_unused_needs_logs_that_cover_the_window() -> None:
    res = usage.buckets(load_config(environ={}), 30, steampipe=Steampipe(), run=tailpipe(), today=TODAY)
    by = {r["bucket"]: r for r in res.rows}
    assert by["busy"]["status"] == "active" and by["busy"]["reads"] == 100
    assert by["quiet"]["status"] == "unused"  # logged, covered, no requests
    assert by["nolog"]["status"] == "unknown"  # no logging: never called unused
    assert [r["bucket"] for r in res.rows] == ["quiet", "nolog", "busy"]
    stale = usage.buckets(load_config(environ={}), 30, steampipe=Steampipe(), run=tailpipe(hi="2026-11-10"),
                          today=TODAY)
    assert {r["bucket"]: r["status"] for r in stale.rows}["quiet"] == "unknown"
    assert any("not the last 30 days" in n for n in stale.notes)


def test_load_balancers_match_log_names_and_respect_logging() -> None:
    res = usage.load_balancers(load_config(environ={}), 30, steampipe=Steampipe(), run=tailpipe(), today=TODAY)
    by = {r["load_balancer"]: r for r in res.rows}
    assert by["web"]["status"] == "active" and by["web"]["requests"] == 9000
    assert by["idle"]["status"] == "unused" and by["legacy"]["status"] == "unknown"
    assert by["legacy"]["type"] == "classic"


def test_no_tailpipe_means_unknown_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("spendlens.sources.tailpipe_ready", lambda cfg, kind=None: False)
    res = usage.buckets(load_config(environ={}), 30, steampipe=Steampipe(), today=TODAY)
    assert {r["status"] for r in res.rows} == {"unknown"} and "No S3 server access logs" in res.notes[0]


def test_generated_tailpipe_sql_passes_the_guard() -> None:
    for sql in [usage.bucket_sql(30), usage.lb_sql("aws_alb_access_log", 30), focus_sql(30), *history_sql(90)]:
        assert tailpipe_check(sql, 500)


def test_focus_costs_feed_the_graph() -> None:
    rows = [{"resource_id": "i-1", "cost": 31.0, "first_seen": "2026-10-28", "last_seen": "2026-11-26",
             "currency": "USD"}]
    seen: list[str] = []

    def run(sql: str) -> list[dict[str, Any]]:
        seen.append(sql)
        return rows
    out = from_cur(run, 30, focus=True)
    assert "aws_cost_and_usage_focus" in seen[0] and "effective_cost" in seen[0] and out["i-1"]["cost"] == 31.0


def test_unused_and_savings_history_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client(Steampipe):
        def __enter__(self):
            return self

        def __exit__(self, *a: object) -> None:
            pass
    monkeypatch.setattr("spendlens.features.unused_cmd._client", lambda cfg: Client())
    monkeypatch.setattr("spendlens.sources.tailpipe_ready", lambda cfg, kind=None: False)
    r = CliRunner().invoke(app, ["unused", "buckets", "--status", "unknown"])
    assert r.exit_code == 0 and "nolog" in r.stdout and "3 unknown" in " ".join(r.output.split())
    assert CliRunner().invoke(app, ["unused", "buckets", "--status", "bad"]).exit_code == 2
    r = CliRunner().invoke(app, ["savings", "history"])
    assert r.exit_code == 1 and "Cost Optimization Hub export" in " ".join(r.output.split())
