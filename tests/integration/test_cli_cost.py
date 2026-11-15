import json
from datetime import date
from typing import ClassVar

import pytest
from sqlcheck import check_sql
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.engines import cost as ce
from spendlens.features import cost as cost_feature

runner = CliRunner()


# ------------------------------------------------------------------ demo data


def test_diff_demo_flags_new_and_gone_services():
    r = runner.invoke(app, ["cost", "diff", "--source", "demo", "--show", "all", "-f", "json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    rows = {x["group"]: x for x in doc["rows"]}
    assert rows["Amazon Redshift"]["status"] == "gone" and rows["Amazon Redshift"]["flagged"]
    assert rows["Amazon Bedrock"]["flagged"]
    assert doc["rows"][-1]["group"] == "TOTAL"
    assert doc["meta"]["extra"]["base"]["label"] == "2026-09" and doc["meta"]["extra"]["compare"]["label"] == "2026-10"


def test_diff_gate_and_show_filters():
    r = runner.invoke(app, ["cost", "diff", "--source", "demo", "--show", "flagged", "--no-total", "--fail-on-flag",
                            "-f", "json"])
    assert r.exit_code == 3
    assert all(x["flagged"] for x in json.loads(r.stdout)["rows"])
    r = runner.invoke(app, ["cost", "diff", "--source", "demo", "--show", "decreases", "--top", "2", "--no-total",
                            "-f", "json"])
    rows = json.loads(r.stdout)["rows"]
    assert len(rows) == 2 and all(x["change"] < 0 for x in rows)


def test_trend_showback_resources_demo():
    r = runner.invoke(app, ["cost", "trend", "--source", "demo", "--top", "3", "-f", "json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)["rows"]
    assert len(rows) == 3 and "2026-10" in rows[0] and "last_change_pct" in rows[0]
    r = runner.invoke(app, ["cost", "showback", "--source", "demo", "-f", "json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)["rows"]
    owners = [x for x in rows if x["tag_key"] == "Service Owner"]
    assert round(sum(x["share_pct"] for x in owners)) == 100
    assert "has no 'Service Owner' tag" in r.output
    r = runner.invoke(app, ["cost", "resources", "--source", "demo", "-f", "json"])
    rows = json.loads(r.stdout)["rows"]
    assert any(x["status"] == "new" and "bedrock" in x["resource"] for x in rows)


@pytest.mark.parametrize("args", [
    ["--from", "2026-09"],  # --to missing
    ["--mtd", "--from", "2026-09", "--to", "2026-10"],
    ["--from", "2026-13", "--to", "2026-10"],
    ["-g", "colour"],
    ["--metric", "free"],
    ["--show", "some"],
])
def test_diff_rejects_bad_options(args):
    assert runner.invoke(app, ["cost", "diff", "--source", "demo", *args]).exit_code == 2


# ------------------------------------------------------------------ live path (fake Steampipe)


class FakeClient:
    calls: ClassVar[list] = []
    data: ClassVar[dict] = {}

    def __init__(self, cfg, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def query(self, sql, params=None, **kw):
        FakeClient.calls.append((sql, params))
        # same contract as psycopg: every %(name)s placeholder must have a value, and vice versa
        import re
        names = set(re.findall(r"%\((\w+)\)s", sql))
        assert names == set(params), f"placeholders {names} != params {set(params)}"
        return FakeClient.data.get(params["start"], [])


@pytest.fixture
def fake_steampipe(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr(cost_feature, "SteampipeClient", FakeClient)
    return FakeClient


def test_diff_live_custom_ranges_and_replay(fake_steampipe):
    fake_steampipe.data = {
        "2026-07-01": [{"grp": "EC2", "amount": 300, "currency": "USD"}, {"grp": "S3", "amount": 90, "currency": "USD"}],
        "2026-10-01": [{"grp": "EC2", "amount": 450, "currency": "USD"}],
    }
    r = runner.invoke(app, ["cost", "diff", "--from", "2026-07..2026-09", "--to", "2026-10..2026-12", "-g", "tag:Team",
                            "-f", "json", "--show", "all"])
    assert r.exit_code == 0, r.output
    (sql0, p0), (_sql1, p1) = fake_steampipe.calls
    assert p0 == {"start": "2026-07-01", "end": "2026-10-01", "tag": "Team"}
    assert p1 == {"start": "2026-10-01", "end": "2027-01-01", "tag": "Team"}
    assert "aws_cost_by_tag" in sql0
    rows = {x["group"]: x for x in json.loads(r.stdout)["rows"]}
    assert rows["EC2"]["change"] == 150 and rows["S3"]["status"] == "gone"
    # replay offline from history: same answer, no Steampipe calls
    fake_steampipe.calls = []
    r = runner.invoke(app, ["cost", "diff", "--source", "last", "--show", "all", "-f", "json"])
    assert r.exit_code == 0 and fake_steampipe.calls == []
    assert {x["group"]: x for x in json.loads(r.stdout)["rows"]}["EC2"]["change"] == 150


def test_diff_live_default_periods_and_credit_exclusion(fake_steampipe):
    r = runner.invoke(app, ["cost", "diff", "--today", "2026-11-15", "-f", "json",
                            "--set", "cost.exclude_record_types=[Credit, Refund, Tax]"])
    assert r.exit_code == 0, r.output
    (_sql, p0), (_, p1) = fake_steampipe.calls
    assert p0 == {"start": "2026-09-01", "end": "2026-10-01", "excluded": ["Credit", "Refund", "Tax"]}
    assert (p1["start"], p1["end"]) == ("2026-10-01", "2026-11-01")
    r = runner.invoke(app, ["cost", "diff", "--today", "2026-11-15", "--include-credits", "--mtd", "-f", "json"])
    assert fake_steampipe.calls[-2][1] == {"start": "2026-10-01", "end": "2026-10-15"}


def test_resources_live_buckets_days(fake_steampipe):
    fake_steampipe.data = {"2026-11-01": [
        {"grp": "i-1", "day": date(2026, 11, 2), "amount": 10, "currency": "USD"},
        {"grp": "i-1", "day": date(2026, 11, 9), "amount": 40, "currency": "USD"},
    ]}
    r = runner.invoke(app, ["cost", "resources", "--today", "2026-11-15", "-f", "json"])
    assert r.exit_code == 0, r.output
    assert fake_steampipe.calls[0][1] == {"start": "2026-11-01", "end": "2026-11-15"}
    row = json.loads(r.stdout)["rows"][0]
    assert (row["resource"], row["base"], row["compare"], row["flagged"]) == ("i-1", 10, 40, True)


# ------------------------------------------------------------------ SQL validity (offline)


@pytest.mark.parametrize("group", ["service", "account", "region", "usage-type", "record-type", "tag:Team"])
@pytest.mark.parametrize("metric", list(ce.METRICS))
def test_cost_sql_valid(group, metric):
    assert check_sql(ce.cost_sql(ce.GroupBy.parse(group), metric, ["Credit"])) == []


def test_resource_sql_valid():
    assert check_sql(ce.resource_sql("net_unblended")) == []
