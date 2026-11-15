import json

from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.features import tags as tags_feature
from spendlens.history import HistoryStore, demo_db_path
from spendlens.models import Finding, Status

runner = CliRunner()




def F(control, resource, status, **kw):
    return Finding(control=control, resource=resource, status=Status(status), **kw)


def test_demo_db_is_shipped():
    with HistoryStore(demo_db_path(), read_only=True) as s:
        runs = s.runs("tags")
        assert len(runs) >= 2
        assert all(f.account_id == "123456789012" for f in s.findings(runs[0].id))



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

