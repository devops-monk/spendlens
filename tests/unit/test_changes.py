"""CloudTrail: the engine (cloudtrail.py), `spendlens cloudtrail`, `spendlens changes` and the cloudtrail_events tool.

Fakes only: no Steampipe, Tailpipe or AWS.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest
from typer.testing import CliRunner

from spendlens import changes as ch
from spendlens import cloudtrail as ct
from spendlens.ai.catalog import load as load_catalog
from spendlens.ai.sql_check import check
from spendlens.ai.tailpipe_tools import check as tailpipe_check
from spendlens.cli import app
from spendlens.config import load_config
from spendlens.graph.costs import CostSourceError
from spendlens.steampipe import SteampipeError

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
EVENTS = [
    {"event_time": "2026-11-27T10:00:00+00:00", "who": "arn:aws:sts::1:assumed-role/Admin/alice",
     "event_name": "AuthorizeSecurityGroupIngress", "event_source": "ec2.amazonaws.com", "region": "eu-west-1",
     "source_ip": "10.1.2.3", "error_code": None, "read_only": "false", "resources": "sg-0abc"},
    {"event_time": "2026-11-27T09:00:00+00:00", "who": "arn:aws:iam::1:user/bob", "event_name": "DeleteBucket",
     "event_source": "s3.amazonaws.com", "region": "eu-west-1", "source_ip": "5.6.7.8", "error_code": "AccessDenied",
     "read_only": "false", "resources": "old-bucket"},
    {"event_time": "2026-11-27T08:00:00+00:00", "who": "arn:aws:sts::1:assumed-role/Admin/alice",
     "event_name": "TerminateInstances", "event_source": "ec2.amazonaws.com", "region": "eu-west-1",
     "source_ip": "10.1.2.3", "error_code": None, "read_only": "false", "resources": "i-1"},
    {"event_time": "2026-11-27T07:00:00+00:00", "who": "arn:aws:sts::1:assumed-role/Admin/alice",
     "event_name": "DescribeInstances", "event_source": "ec2.amazonaws.com", "region": "eu-west-1",
     "source_ip": "10.1.2.3", "error_code": None, "read_only": "true", "resources": None},
]
LOOKUP_KEYS = ("event_id", "event_name", "event_source", "read_only", "username", "resource_name", "resource_type",
               "access_key_id")


def F(**kw: Any) -> ct.Filters:
    return ct.Filters.build(end=NOW, **{"hours": 24, **kw})


class Steampipe:
    def __init__(self, rows: list[dict[str, Any]] | None = None, log_group: str | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.rows = EVENTS if rows is None else rows
        self.log_group = log_group

    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        if "from aws_cloudtrail_trail " in sql:
            return [{"log_group_arn": self.log_group}] if self.log_group else []
        self.calls.append((sql, params))
        return [dict(r) for r in self.rows]


def no_tailpipe(sql: str) -> list:
    raise CostSourceError("Catalog Error: Table with name aws_cloudtrail_log does not exist!\n          ^")


def _pushed(sql: str) -> list[str]:
    """Filters Steampipe sends to LookupEvents: a bare key column compared with '='."""
    where = sql.split(" where ", 1)[1]
    return [k for k in LOOKUP_KEYS if re.search(rf"(?:^| and ){k} = ", where)]


# ------------------------------------------------------------------ filters


def test_time_parsing() -> None:
    assert ct.parse_time("2026-11-01") == datetime(2026, 11, 1, tzinfo=UTC)
    assert ct.parse_time("2026-11-20", end=True) == datetime(2026, 11, 21, tzinfo=UTC)
    assert ct.parse_time("2026-11-01 14:30") == datetime(2026, 11, 1, 14, 30, tzinfo=UTC)
    assert ct.parse_time("2026-11-01T14:30:00Z").tzinfo is not None
    assert abs((datetime.now(UTC) - ct.parse_time("2d")) - timedelta(days=2)) < timedelta(seconds=5)
    with pytest.raises(ct.CloudTrailError):
        ct.parse_time("last tuesday")
    with pytest.raises(ct.CloudTrailError):
        ct.Filters.build(start="2026-11-20", end="2026-11-01")


def test_presets_and_overrides() -> None:
    f = ct.Filters.build(preset="console-logins", user="alice", end=NOW)
    assert f.events == ["ConsoleLogin"] and f.reads == "all" and f.user == "alice"
    assert ct.Filters.build(preset="iam", end=NOW).service == "iam.amazonaws.com"
    f = ct.Filters.build(events=["UpdateService,CreateService"], end=NOW)
    assert f.events == ["UpdateService", "CreateService"]
    with pytest.raises(ct.CloudTrailError):
        ct.Filters.build(preset="nope")
    with pytest.raises(ct.CloudTrailError):
        ct.Filters.build(source_ip="10.0.0.0/99")


def test_python_filters() -> None:
    rows = [ct._row(r) for r in EVENTS]

    def keep(**kw: Any) -> list[str]:
        return [r["event_name"] for r in rows if ct.matches(r, F(**kw))]
    assert keep() == ["AuthorizeSecurityGroupIngress", "DeleteBucket", "TerminateInstances"]  # writes by default
    assert "DescribeInstances" in keep(reads="all") and keep(reads="reads") == ["DescribeInstances"]
    assert keep(events=["Delete*", "Terminate*"]) == ["DeleteBucket", "TerminateInstances"]
    assert keep(errors_only=True) == ["DeleteBucket"] and keep(errors=["Access*"]) == ["DeleteBucket"]
    assert keep(source_ip="10.0.0.0/8") == ["AuthorizeSecurityGroupIngress", "TerminateInstances"]
    assert keep(source_ip="5.6.7.8") == ["DeleteBucket"]
    assert keep(user="BOB") == ["DeleteBucket"] and keep(service="s3") == ["DeleteBucket"]


# ------------------------------------------------------------------ SQL


@pytest.mark.parametrize("kw", [{}, {"user": "alice"}, {"resource": "sg-1"}, {"service": "ec2", "events": ["X"]},
                                {"events": ["Delete*"], "errors_only": True, "source_ip": "1.2.3.4", "reads": "all"},
                                {"resource_type": "AWS::ECS::Service", "access_key": "AKIA1", "errors": ["Access*"]},
                                {"preset": "root"}, {"user": "o'brien"}])
def test_generated_sql_passes_the_guards(kw: dict[str, Any]) -> None:
    f = F(**kw)
    sql, params = ct.lookup_sql(f, f.start, f.end, (f.events or [None])[0])
    sql = sql.replace("%(attr)s", ct._q(params["attr"])) if params else sql
    assert check(sql, load_catalog()).ok, check(sql, load_catalog())
    assert check(ct.cloudwatch_sql(f, "aws-cloudtrail-logs", "eu-west-1", f.start, f.end), load_catalog()).ok
    assert tailpipe_check(ct.tailpipe_sql(f), 500)


def test_quotes_are_escaped() -> None:
    assert "'%o''brien%'" in ct.tailpipe_sql(F(user="o'brien"))


@pytest.mark.parametrize("kw", [{}, {"service": "ecs"}, {"user": "alice", "service": "ecs"},
                                {"events": ["UpdateService"], "service": "ecs", "user": "bob"},
                                {"resource": "svc-1", "events": ["UpdateService"]}, {"reads": "reads"},
                                {"access_key": "AKIA1", "resource_type": "AWS::S3::Bucket"}])
def test_lookup_events_gets_at_most_one_attribute_and_no_sort(kw: dict[str, Any]) -> None:
    f = F(**kw)
    sql, _ = ct.lookup_sql(f, f.start, f.end, (f.events or [None])[0])
    assert len(_pushed(sql)) == 1, (_pushed(sql), sql)
    assert "order by" not in sql.lower() and sql.rstrip().endswith(f"limit {ct.MAX_ROWS}")


def test_reads_all_with_nothing_else_sends_no_attribute() -> None:
    f = F(reads="all")
    sql, params = ct.lookup_sql(f, f.start, f.end)
    assert _pushed(sql) == [] and params == {}


def test_cloudwatch_sends_the_filters_together() -> None:
    f = F(service="ecs", events=["UpdateService"], access_key="AKIA1")
    sql = ct.cloudwatch_sql(f, "ct-logs", "eu-west-1", f.start, f.end, "UpdateService")
    for part in ("log_group_name = 'ct-logs'", "region = 'eu-west-1'", "timestamp >= '2026-11-26 12:00:00+00'",
                 "event_name = 'UpdateService'", "event_source = 'ecs.amazonaws.com'", "read_only = false",
                 "access_key_id = 'AKIA1'"):
        assert part in sql


def test_windows_widen_newest_first_and_cover_everything() -> None:
    w = ct.windows(NOW - timedelta(days=7), NOW, broad=True)
    assert [(NOW - lo).total_seconds() / 3600 for lo, _ in w] == [1, 6, 24, 72, 168]
    assert all(a[0] == b[1] for a, b in pairwise(w)) and w[0][1] == NOW
    assert ct.windows(NOW - timedelta(days=7), NOW, broad=False) == [(NOW - timedelta(days=7), NOW)]
    w = ct.windows(NOW - timedelta(days=30), NOW, broad=True)
    assert w[-1][0] == NOW - timedelta(days=30)


# ------------------------------------------------------------------ search


def tailpipe(lo: str, hi: str, rows: list[dict[str, Any]] | None = None) -> Any:
    """A fake Tailpipe holding CloudTrail logs from lo to hi."""
    calls: list[str] = []

    def run(sql: str) -> list[dict[str, Any]]:
        calls.append(sql)
        return [{"lo": lo, "hi": hi}] if "min(tp_date)" in sql else list(rows if rows is not None else EVENTS[:1])
    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_tailpipe_only_when_configured_and_covering_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_config(environ={})
    # no CloudTrail sources configured for Tailpipe: straight to Steampipe, Tailpipe never asked
    monkeypatch.setattr(ct, "tailpipe_configured", lambda: False)
    res = ct.search(cfg, F(), steampipe=Steampipe())
    assert res.source == "lookup" and len(res.rows) == 3 and not any("Tailpipe" in n for n in res.notes)

    monkeypatch.setattr(ct, "tailpipe_configured", lambda: True)
    run = tailpipe("2026-11-01", "2026-11-27")
    res = ct.search(cfg, F(), tailpipe_run=run, steampipe=Steampipe())
    assert res.source == "tailpipe" and len(res.rows) == 1 and "aws_cloudtrail_log" in res.queries[0]["sql"]

    # collected, but stale for this window: read from AWS, and say why
    res = ct.search(cfg, F(), tailpipe_run=tailpipe("2026-11-01", "2026-11-20"), steampipe=Steampipe())
    assert res.source == "lookup" and any("cover 2026-11-01 to 2026-11-20" in n for n in res.notes)
    # ... unless AWS is not allowed (ask --no-live): the logs, with the gap named
    res = ct.search(cfg, F(), tailpipe_run=tailpipe("2026-11-01", "2026-11-20"), allow_aws=False)
    assert res.source == "tailpipe" and any("not included" in n for n in res.notes)
    # --source tailpipe forces it
    assert ct.search(cfg, F(), source="tailpipe", tailpipe_run=tailpipe("2026-11-01", "2026-11-20")).source == "tailpipe"

    # configured but nothing collected (the table does not exist yet): a clean note, then AWS
    res = ct.search(cfg, F(), tailpipe_run=no_tailpipe, steampipe=Steampipe())
    assert res.source == "lookup" and any("Tailpipe has no CloudTrail logs collected yet" in n for n in res.notes)
    assert not any("^" in n for n in res.notes)  # no DuckDB caret lines

    # turned off: never asked
    off = load_config(environ={}, sets=["tailpipe.enabled=false"])
    run = tailpipe("2026-11-01", "2026-11-27")
    assert ct.search(off, F(), tailpipe_run=run, steampipe=Steampipe()).source == "lookup" and not run.calls

    sp = Steampipe(log_group="arn:aws:logs:eu-west-1:1:log-group:aws-cloudtrail-logs:*")
    res = ct.search(cfg, F(), tailpipe_run=no_tailpipe, steampipe=sp)
    assert res.source == "cloudwatch" and "log_group_name = 'aws-cloudtrail-logs'" in sp.calls[0][0]
    assert "region = 'eu-west-1'" in sp.calls[0][0]
    with pytest.raises(ct.CloudTrailError):
        ct.search(cfg, F(), tailpipe_run=no_tailpipe, allow_aws=False)


def test_cloudwatch_failure_falls_back_to_lookup() -> None:
    class Denied(Steampipe):
        def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
            if "aws_cloudtrail_trail_event" in sql:
                raise SteampipeError("AccessDeniedException: not authorized to perform logs:FilterLogEvents")
            return super().query(sql, params, **kw)

    sp = Denied(log_group="arn:aws:logs:eu-west-1:1:log-group:ct:*")
    res = ct.search(load_config(environ={}), F(), source="aws", steampipe=sp)
    assert res.source == "lookup" and any("could not be read" in n for n in res.notes)


def test_config_log_group_wins_and_lookup_is_cut_to_90_days() -> None:
    cfg = load_config(environ={})
    cfg.cloudtrail.log_group, cfg.cloudtrail.log_group_region = "my-group", "eu-west-2"
    sp = Steampipe()
    assert ct.search(cfg, F(), source="aws", steampipe=sp).source == "cloudwatch"
    assert "log_group_name = 'my-group'" in sp.calls[0][0]
    res = ct.search(load_config(environ={}), ct.Filters.build(hours=24 * 200, events=["X"]), source="lookup",
                    steampipe=Steampipe(rows=[]))
    assert any("90 days" in n for n in res.notes)


def test_one_lookup_call_per_exact_event() -> None:
    sp = Steampipe(rows=[])
    ct.search(load_config(environ={}), F(preset="security-groups"), source="lookup", steampipe=sp)
    names = [p["attr"] for _, p in sp.calls]
    assert names == ct.PRESETS["security-groups"]["events"]


class SlowSteampipe(Steampipe):
    """Each query takes 25 s on a fake clock."""

    def __init__(self, clock: list[float], error: str | None = None) -> None:
        super().__init__()
        self.clock, self.error, self.timeouts = clock, error, []

    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        self.timeouts.append(kw.get("timeout_s"))
        self.clock[0] += 25
        if self.error and len(self.calls) >= 2:
            raise SteampipeError(self.error)
        self.calls.append((sql, params))
        return [dict(EVENTS[0], event_time=f"2026-11-27T0{len(self.calls)}:00:00+00:00")]


def test_broad_search_stops_at_the_time_budget_with_what_it_found() -> None:
    clock = [0.0]
    sp = SlowSteampipe(clock)
    res = ct.search(load_config(environ={}), F(hours=168, service="ec2"), source="lookup", steampipe=sp,
                    max_wait_s=60, clock=lambda: clock[0])
    assert len(sp.calls) == 3 and sp.timeouts == [60, 35, 10]
    assert res.partial and res.covered_from == NOW - timedelta(hours=24) and len(res.rows) == 3
    assert [r["event_time"] for r in res.rows] == sorted((r["event_time"] for r in res.rows), reverse=True)
    assert any("still reading after 60s" in n for n in res.notes)


def test_statement_timeout_returns_partial_but_other_errors_raise() -> None:
    clock = [0.0]
    sp = SlowSteampipe(clock, error="ERROR: canceling statement due to statement timeout")
    res = ct.search(load_config(environ={}), F(hours=168), source="lookup", steampipe=sp, max_wait_s=600,
                    clock=lambda: clock[0])
    assert res.partial and res.covered_from == NOW - timedelta(hours=6) and len(res.rows) == 2
    with pytest.raises(SteampipeError):
        ct.search(load_config(environ={}), F(hours=168), source="lookup", max_wait_s=600,
                  steampipe=SlowSteampipe([0.0], error="AccessDenied"), clock=lambda: 0.0)


def test_selective_search_is_one_call_and_complete() -> None:
    sp = Steampipe()
    res = ct.search(load_config(environ={}), F(hours=168, events=["UpdateService"]), source="lookup", steampipe=sp)
    assert len(sp.calls) == 1 and not res.partial


def test_summary_counts() -> None:
    s = ct.summarise([ct._row(r) for r in EVENTS])
    assert s["by_who"][0] == {"name": "arn:aws:sts::1:assumed-role/Admin/alice", "count": 3}
    assert s["by_error"] == [{"name": "AccessDenied", "count": 1}] and s["failed"] == 1
    assert s["by_day"] == [{"name": "2026-11-27", "count": 4}]


# ------------------------------------------------------------------ commands


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(cfg: Any, f: ct.Filters, **kw: Any) -> ct.Result:
        seen.update(filters=f, **kw)
        rows = [r for r in (ct._row(x) for x in EVENTS) if ct.matches(r, f)]
        return ct.Result("lookup", f, rows, f.start, [{"source": "lookup", "sql": "select 1"}], ["From LookupEvents"])
    monkeypatch.setattr(ct, "search", fake)
    return seen


def test_cli_events_filters_formats_and_sql(tmp_path, fake_search: dict[str, Any]) -> None:
    r = CliRunner().invoke(app, ["cloudtrail", "events", "--from", "2026-11-01", "--to", "2026-11-20", "-e",
                                 "Delete*", "--event", "TerminateInstances", "--source-ip", "10.0.0.0/8",
                                 "--reads", "all", "--show-sql"])
    assert r.exit_code == 0, r.output
    f = fake_search["filters"]
    assert f.start == datetime(2026, 11, 1, tzinfo=UTC) and f.end == datetime(2026, 11, 21, tzinfo=UTC)
    assert f.events == ["Delete*", "TerminateInstances"] and f.reads == "all" and "1 event(s)" in r.output  # TerminateInstances from 10.x
    assert "select 1" in r.output
    for ext in ("csv", "json", "jsonl", "md", "html", "yaml", "xlsx", "tsv"):
        out = tmp_path / f"e.{ext}"
        r = CliRunner().invoke(app, ["cloudtrail", "events", "--days", "7", "-o", str(out)])
        assert r.exit_code == 0 and out.stat().st_size > 0, (ext, r.output)
    assert (tmp_path / "e.csv").read_text().splitlines()[0].startswith("event_time,who,event_name")
    assert len((tmp_path / "e.jsonl").read_text().splitlines()) == 3
    assert CliRunner().invoke(app, ["cloudtrail", "events", "-f", "xlsx"]).exit_code == 2  # needs -o
    assert CliRunner().invoke(app, ["cloudtrail", "events", "--reads", "x"]).exit_code == 2
    assert CliRunner().invoke(app, ["cloudtrail", "events", "--preset", "x"]).exit_code == 2


def test_cli_summary_and_presets(tmp_path, fake_search: dict[str, Any]) -> None:
    r = CliRunner().invoke(app, ["cloudtrail", "summary", "--preset", "deletes", "--days", "30"])
    assert r.exit_code == 0 and "By who" in r.stdout and "bob" in r.stdout
    out = tmp_path / "s.xlsx"
    assert CliRunner().invoke(app, ["cloudtrail", "summary", "-o", str(out)]).exit_code == 0
    import openpyxl

    assert "by who" in openpyxl.load_workbook(out).sheetnames
    r = CliRunner().invoke(app, ["cloudtrail", "presets"])
    assert r.exit_code == 0 and "console-logins" in r.stdout


def test_cli_changes_is_the_short_form(tmp_path, fake_search: dict[str, Any]) -> None:
    out = tmp_path / "c.csv"
    r = CliRunner().invoke(app, ["changes", "--days", "7", "--service", "ec2", "-f", "csv", "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert fake_search["filters"].service == "ec2.amazonaws.com" and fake_search["source"] == "auto"
    assert "TerminateInstances" in out.read_text()
    r = CliRunner().invoke(app, ["changes"])
    assert r.exit_code == 0 and "Who changed most" in r.stdout and "alice" in r.stdout
    assert CliRunner().invoke(app, ["changes", "--source", "x"]).exit_code == 2


def test_changes_wrapper_shape() -> None:
    res = ch.find_changes(load_config(environ={}), 24, source="lookup_events", steampipe=Steampipe())
    assert res["source"] == "lookup_events" and res["by_who"][0]["changes"] == 2 and res["hours"] == 24


# ------------------------------------------------------------------ the AI tool


def test_ai_tool_filters_sql_and_full_export(tmp_path) -> None:
    from spendlens.ai.files import OutputDir
    from spendlens.ai.tools import Toolbox

    cfg = load_config(environ={})
    assert "cloudtrail_events" not in {s.name for s in Toolbox(cfg, tmp_path / "h.db").specs()}
    cfg.llm.max_rows = 1
    tb = Toolbox(cfg, tmp_path / "h.db", live=True)
    tb.outdir = OutputDir(tmp_path / "out")
    tb._tailpipe_ok = False
    sp = Steampipe()
    tb._client = lambda: sp  # type: ignore[method-assign]
    assert "cloudtrail_events" in {s.name for s in tb.specs()}
    out = json.loads(tb.execute("1", "cloudtrail_events", {"hours": 12, "user": "alice", "reads": "all",
                                                           "events": ["Terminate*", "Describe*"]}).content)
    assert out["source"] == "lookup" and out["count"] == 2 and out["row_count"] == 1  # one row sent to the model
    assert "_export_rows" not in out and "aws_cloudtrail_lookup_event" in out["queries"][0]["sql"]
    w = json.loads(tb.execute("2", "write_file", {"name": "events.csv", "result_id": out["result_id"]}).content)
    assert w["rows"] == 2  # ... and both rows written to the file
    bad = tb.execute("3", "cloudtrail_events", {"preset": "nope"})
    assert bad.is_error and "unknown preset" in bad.content
