"""spendlens changes / the aws_changes tool: CloudTrail write events from Tailpipe or LookupEvents (fakes only)."""

import json
import re
from itertools import pairwise
from typing import Any

import pytest
from typer.testing import CliRunner

from spendlens import changes as ch
from spendlens.ai.catalog import load as load_catalog
from spendlens.ai.sql_check import check
from spendlens.ai.tailpipe_tools import check as tailpipe_check
from spendlens.cli import app
from spendlens.config import load_config
from spendlens.graph.costs import CostSourceError
from spendlens.steampipe import SteampipeError

EVENTS = [
    {"event_time": "2026-11-25T10:00:00", "who": "arn:aws:sts::1:assumed-role/Admin/alice", "event_name":
     "AuthorizeSecurityGroupIngress", "event_source": "ec2.amazonaws.com", "region": "eu-west-1",
     "source_ip": "1.2.3.4", "error_code": None, "details": "sg-0abc"},
    {"event_time": "2026-11-25T09:00:00", "who": "arn:aws:iam::1:user/bob", "event_name": "DeleteBucket",
     "event_source": "s3.amazonaws.com", "region": "eu-west-1", "source_ip": "5.6.7.8", "error_code": "AccessDenied",
     "details": "old-bucket"},
    {"event_time": "2026-11-25T08:00:00", "who": "arn:aws:sts::1:assumed-role/Admin/alice", "event_name":
     "TerminateInstances", "event_source": "ec2.amazonaws.com", "region": "eu-west-1", "source_ip": "1.2.3.4",
     "error_code": None, "details": "i-1"},
]


class Steampipe:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, params))
        return list(EVENTS)


class OnceSteampipe(Steampipe):
    """The events land in the first (newest) window only."""

    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, params))
        return list(EVENTS) if len(self.calls) == 1 else []


@pytest.mark.parametrize("kw", [{}, {"user": "alice"}, {"resource": "sg-1"}, {"service": "ec2", "event": "X"}])
def test_generated_sql_passes_both_guards(kw: dict[str, str]) -> None:
    sql, params = ch.lookup_sql(48, **kw)
    for k in params:
        sql = sql.replace(f"%({k})s", "'x'")
    assert check(sql, load_catalog()).ok
    assert tailpipe_check(ch.tailpipe_sql(48, **kw), 500)


def test_user_and_quotes_are_escaped() -> None:
    sql = ch.tailpipe_sql(24, user="o'brien")
    assert "'%o''brien%'" in sql


def test_tailpipe_first_then_lookup_events() -> None:
    cfg = load_config(environ={})
    res = ch.find_changes(cfg, 24, tailpipe_run=lambda sql: EVENTS[:1], steampipe=Steampipe())
    assert res["source"] == "tailpipe" and res["count"] == 1

    sp = Steampipe()
    res = ch.find_changes(cfg, 24, event="X", tailpipe_run=lambda sql: [], steampipe=sp)
    assert res["source"] == "lookup_events" and len(sp.calls) == 1
    sp = Steampipe()
    res = ch.find_changes(cfg, 24, tailpipe_run=lambda sql: [], steampipe=sp)
    assert len(sp.calls) == 3  # broad: the last 1, 6 and 24 hours, one window at a time
    res = ch.find_changes(cfg, 24, tailpipe_run=lambda sql: [], steampipe=OnceSteampipe())
    assert res["count"] == 3
    assert res["by_who"][0] == {"who": "arn:aws:sts::1:assumed-role/Admin/alice", "changes": 2}
    assert res["failed"] == 1 and any("No matching events" in n for n in res["notes"])

    def broken(sql: str) -> list:
        raise CostSourceError("no tailpipe")
    res = ch.find_changes(cfg, 24 * 200, tailpipe_run=broken, steampipe=Steampipe())
    assert res["source"] == "lookup_events" and res["hours"] == 90 * 24
    assert any("unavailable" in n for n in res["notes"]) and any("90 days" in n for n in res["notes"])


def test_lookup_events_sends_one_attribute_and_filters_the_rest() -> None:
    sp = Steampipe()
    res = ch.find_changes(load_config(environ={}), 24, user="alice", service="ec2", source="lookup_events",
                          steampipe=sp)
    sql, params = sp.calls[0]
    assert params == {"user": "alice", "service": "ec2.amazonaws.com"} and "username = %(user)s" in sql
    assert "lower(event_source) = %(service)s" in sql and "read_only = 'false'" not in sql
    assert [r["event_name"] for r in res["rows"]] == ["AuthorizeSecurityGroupIngress", "TerminateInstances"]


def test_cli_changes_csv(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake(cfg: Any, hours: int, **kw: Any) -> dict[str, Any]:
        seen.update(hours=hours, **kw)
        return {**ch.summarise(EVENTS, "lookup_events", hours), "notes": ["From LookupEvents"]}
    monkeypatch.setattr(ch, "find_changes", fake)
    out = tmp_path / "c.csv"
    r = CliRunner().invoke(app, ["changes", "--days", "7", "--service", "ec2", "-f", "csv", "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert seen["hours"] == 168 and seen["service"] == "ec2" and seen["source"] == "auto"
    text = out.read_text()
    assert text.splitlines()[0].startswith("time,who,service,event") and "DeleteBucket" in text
    r = CliRunner().invoke(app, ["changes"])
    assert r.exit_code == 0 and "Who changed most" in r.stdout and "alice" in r.stdout
    assert CliRunner().invoke(app, ["changes", "--source", "x"]).exit_code == 2


def test_ai_tool_offered_only_with_a_source(tmp_path) -> None:
    from spendlens.ai.tools import Toolbox

    cfg = load_config(environ={})
    assert "aws_changes" not in {s.name for s in Toolbox(cfg, tmp_path / "h.db").specs()}
    tb = Toolbox(cfg, tmp_path / "h.db", live=True)
    sp = Steampipe()
    tb._client = lambda: sp  # type: ignore[method-assign]
    assert "aws_changes" in {s.name for s in tb.specs()}
    out = json.loads(tb.execute("1", "aws_changes", {"hours": 12, "user": "bob"}).content)
    assert out["source"] == "lookup_events" and out["count"] == 1 and out["rows"][0]["event_name"] == "DeleteBucket"


LOOKUP_KEYS = ("event_id", "event_name", "event_source", "read_only", "username", "resource_name", "resource_type",
               "access_key_id")


def _pushed(sql: str) -> list[str]:
    """Filters Steampipe sends to the LookupEvents API: a bare key column compared with '='."""
    where = sql.split(" where ", 1)[1]
    return [k for k in LOOKUP_KEYS if re.search(rf"(?:^| and ){k} = ", where)]


@pytest.mark.parametrize("kw", [{}, {"service": "ecs"}, {"user": "alice", "service": "ecs"},
                                {"event": "UpdateService", "service": "ecs", "user": "bob"},
                                {"resource": "svc-1", "event": "UpdateService"}])
def test_lookup_events_gets_exactly_one_attribute_and_no_sort(kw: dict[str, str]) -> None:
    sql, _ = ch.lookup_sql(168, **kw)
    assert len(_pushed(sql)) == 1, (_pushed(sql), sql)
    assert "order by" not in sql.lower() and sql.rstrip().endswith(f"limit {ch.MAX_ROWS}")
    assert ("read_only = 'false'" in sql) == (not kw)


def test_windows_widen_newest_first_and_cover_everything() -> None:
    assert ch.windows(168, broad=True) == [(0, 1), (1, 6), (6, 24), (24, 72), (72, 168)]
    assert ch.windows(168, broad=False) == [(0, 168)]
    assert ch.windows(2, broad=True) == [(0, 1), (1, 2)]
    w = ch.windows(24 * 30, broad=True)
    assert w[0] == (0, 1) and w[-1][1] == 24 * 30 and all(a[1] == b[0] for a, b in pairwise(w))
    sql, _ = ch.lookup_sql(24, service="ecs", since_hours=6)
    assert "start_time = now() - interval '24 hours'" in sql and "end_time = now() - interval '6 hours'" in sql


class SlowSteampipe(Steampipe):
    """Each LookupEvents call takes 25 s on a fake clock."""

    def __init__(self, clock: list[float], error: str | None = None) -> None:
        super().__init__()
        self.clock, self.error, self.timeouts = clock, error, []

    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        self.timeouts.append(kw.get("timeout_s"))
        self.clock[0] += 25
        if self.error and len(self.calls) >= 2:
            raise SteampipeError(self.error)
        self.calls.append((sql, params))
        return [dict(EVENTS[0], event_time=f"2026-11-25T0{len(self.calls)}:00:00")]


def test_broad_search_stops_at_the_time_budget_with_what_it_found() -> None:
    clock = [0.0]
    sp = SlowSteampipe(clock)
    res = ch.find_changes(load_config(environ={}), 168, service="ec2", source="lookup_events", steampipe=sp,
                          max_wait_s=60, clock=lambda: clock[0])
    assert len(sp.calls) == 3 and sp.timeouts == [60, 35, 10]
    assert res["partial"] and res["covered_hours"] == 24 and res["count"] == 3
    assert [r["event_time"] for r in res["rows"]] == sorted((r["event_time"] for r in res["rows"]), reverse=True)
    assert any("last 24 of 168 hours" in n and "UpdateService" in n for n in res["notes"])


def test_statement_timeout_returns_partial_but_other_errors_raise() -> None:
    clock = [0.0]
    sp = SlowSteampipe(clock, error="ERROR: canceling statement due to statement timeout")
    res = ch.find_changes(load_config(environ={}), 168, source="lookup_events", steampipe=sp, max_wait_s=600,
                          clock=lambda: clock[0])
    assert res["partial"] and res["covered_hours"] == 6 and res["count"] == 2
    with pytest.raises(SteampipeError):
        ch.find_changes(load_config(environ={}), 168, source="lookup_events", max_wait_s=600,
                        steampipe=SlowSteampipe([0.0], error="AccessDenied"), clock=lambda: 0.0)


def test_selective_search_is_one_call_and_complete() -> None:
    sp = Steampipe()
    res = ch.find_changes(load_config(environ={}), 168, event="UpdateService", source="lookup_events", steampipe=sp)
    assert len(sp.calls) == 1 and not res["partial"] and res["covered_hours"] == 168
