"""spendlens changes / the aws_changes tool: CloudTrail write events from Tailpipe or LookupEvents (fakes only)."""

import json
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

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        self.calls.append((sql, params))
        return list(EVENTS)


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
    res = ch.find_changes(cfg, 24, tailpipe_run=lambda sql: [], steampipe=sp)
    assert res["source"] == "lookup_events" and res["count"] == 3 and len(sp.calls) == 1
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
    assert params == {"user": "alice"} and "username = %(user)s" in sql and "event_source =" not in sql
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
