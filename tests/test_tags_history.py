import json

import pytest
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.features import tags as tags_feature
from spendlens.history import HistoryStore, demo_db_path, diff_findings
from spendlens.models import Finding, Status

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPENDLENS_CONFIG", raising=False)
    monkeypatch.setenv("SPENDLENS__HISTORY__PATH", str(tmp_path / "h.db"))
    # never shell out to aws in tests
    monkeypatch.setattr("spendlens.common.caller_identity", lambda: {"Account": "999", "Arn": "arn:test"})


def F(control, resource, status, **kw):
    return Finding(control=control, resource=resource, status=Status(status), **kw)


# ------------------------------------------------------------------ history store


def test_store_roundtrip_and_prune(tmp_path):
    with HistoryStore(tmp_path / "x.db") as s:
        r1 = s.save_run("tags", [F("c", "r1", "alarm", tags={"a": "b"}, est_monthly_saving=1.5)], account_id="1")
        s.save_run("tags", [F("c", "r1", "ok")])
        s.save_run("tags", [F("c", "r1", "ok")])
        got = s.findings(r1)[0]
        assert got.tags == {"a": "b"} and got.est_monthly_saving == 1.5 and got.status is Status.ALARM
        assert s.prune(2) == 1
        assert [r.id for r in s.runs("tags")] == [3, 2]
        assert s.findings(r1) == []  # cascade


def test_diff_classification():
    old = [F("c", "a", "alarm"), F("c", "b", "ok"), F("c", "gone", "ok"), F("c", "flip", "ok")]
    new = [F("c", "a", "ok"), F("c", "b", "ok"), F("c", "new", "ok"), F("c", "flip", "alarm")]
    changes = {r["resource"]: r["change"] for r in diff_findings(old, new)}
    assert changes == {"a": "resolved", "gone": "resource_gone", "new": "new_resource", "flip": "new_alarm"}


def test_demo_db_is_shipped():
    with HistoryStore(demo_db_path(), read_only=True) as s:
        runs = s.runs("tags")
        assert len(runs) >= 2
        assert all(f.account_id == "123456789012" for f in s.findings(runs[0].id))


# ------------------------------------------------------------------ tags command


def test_tags_demo_summary_json():
    r = runner.invoke(app, ["tags", "--source", "demo", "--summary", "-f", "json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)["rows"]
    assert {"check", "resource_type", "compliance_pct"} <= rows[0].keys()


def test_tags_demo_filters_and_gate():
    r = runner.invoke(app, ["tags", "--source", "demo", "-k", "mandatory", "-r", "s3_bucket", "-f", "json",
                            "--fail-on-alarm"])
    assert r.exit_code == 3
    rows = json.loads(r.stdout)["rows"]
    assert rows and all(x["check"] == "tags_mandatory" and x["resource_type"] == "s3_bucket"
                        and x["status"] == "alarm" for x in rows)


def test_tags_unknown_resource_type():
    r = runner.invoke(app, ["tags", "--source", "demo", "-r", "s3_buckets"])
    assert r.exit_code == 2 and "Unknown resource type" in r.output


def test_tags_live_saves_history_then_last(monkeypatch):
    calls = {}

    def fake_run_live(cfg, checks, rtypes):
        calls["checks"], calls["rtypes"] = checks, rtypes
        calls["mandatory"] = cfg.tagging.mandatory
        return [F("tags_mandatory_s3_bucket", "arn:b", "alarm", check="tags_mandatory", resource_type="s3_bucket")]

    monkeypatch.setattr(tags_feature, "run_live", fake_run_live)
    r = runner.invoke(app, ["tags", "-k", "mandatory", "-s", "tagging.mandatory=[Team]", "-f", "csv"])
    assert r.exit_code == 0, r.output
    assert calls == {"checks": ["mandatory"], "rtypes": [], "mandatory": ["Team"]}
    assert "arn:b" in r.stdout

    r = runner.invoke(app, ["tags", "--source", "last", "-f", "json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    assert doc["meta"]["account_id"] == "999" and doc["rows"][0]["resource"] == "arn:b"

    r = runner.invoke(app, ["history", "list", "-f", "json"])
    assert json.loads(r.stdout)["rows"][0]["alarms"] == 1


def test_last_without_history_fails():
    r = runner.invoke(app, ["tags", "--source", "last"])
    assert r.exit_code == 1


def test_history_diff_demo():
    r = runner.invoke(app, ["history", "diff", "--source", "demo", "-f", "json"])
    assert r.exit_code == 0, r.output
    kinds = {row["change"] for row in json.loads(r.stdout)["rows"]}
    assert {"new_alarm", "resolved"} <= kinds
