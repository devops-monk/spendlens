import json
from datetime import date, timedelta

import pytest
from fakes import FakeSteampipe
from sqlcheck import check_sql
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.engines import cost as ce
from spendlens.engines import forecast as fc
from spendlens.engines import savings as sv

runner = CliRunner()


def j(r):
    assert r.exit_code in (0, 3), r.output
    return json.loads(r.stdout)


# ------------------------------------------------------------------ demo data


def test_demo_commands():
    assert len(j(runner.invoke(app, ["anomalies", "--source", "demo", "-f", "json"]))["rows"]) == 3
    rows = j(runner.invoke(app, ["forecast", "--source", "demo", "-f", "json"]))["rows"]
    assert {r["status"] for r in rows} == {"alarm", "info", "ok"}
    assert runner.invoke(app, ["forecast", "--source", "demo", "--fail-on-risk"]).exit_code == 3
    hub = j(runner.invoke(app, ["savings", "hub", "--source", "demo", "--action", "Rightsize", "-f", "json"]))["rows"]
    assert len(hub) == 1 and hub[0]["action"] == "Rightsize"
    cov = j(runner.invoke(app, ["savings", "commitments", "--source", "demo", "-f", "json"]))["rows"]
    assert all(r["coverage_pct"] < 70 for r in cov)


def test_savings_summary_demo_dedupes():
    r = runner.invoke(app, ["savings", "summary", "--source", "demo", "--by", "none", "--top", "500", "-f", "json"])
    doc = j(r)
    resources = [x["resource"] for x in doc["rows"]]
    assert len(resources) == len(set(resources))
    assert not any(x["control"] == "commitment_coverage" for x in doc["rows"])  # replaced by the hub SP purchase
    assert doc["meta"]["extra"]["est_monthly_saving_total"] == round(sum(x["est_monthly_saving"] for x in doc["rows"]), 2)
    assert "counted once" in r.output or "folded" in r.output


def test_savings_summary_needs_runs():
    assert runner.invoke(app, ["savings", "summary"]).exit_code == 1


# ------------------------------------------------------------------ live paths (fake Steampipe)


def _daily_rows(today, base, spike):
    rows = []
    for i in range(1, 34):
        d = today - timedelta(days=i)
        rows.append({"grp": "EC2", "day": d, "amount": spike if i == 1 else base + (i % 3), "currency": "USD"})
        rows.append({"grp": "S3", "day": d, "amount": 40.0, "currency": "USD"})
    return rows


def test_anomalies_live_and_gate(monkeypatch):
    today = date(2026, 11, 15)
    fake = FakeSteampipe.install(monkeypatch, {"granularity = 'DAILY'": _daily_rows(today, 100, 480)})
    r = runner.invoke(app, ["anomalies", "--today", "2026-11-15", "--fail-on-anomaly", "-f", "json"])
    assert r.exit_code == 3, r.output
    rows = json.loads(r.stdout)["rows"]
    assert [(x["group"], x["date"]) for x in rows] == [("EC2", "2026-11-14")]
    (_sql, params), = fake.calls
    assert params == {"start": "2026-10-13", "end": "2026-11-15", "excluded": ["Credit", "Refund"]}
    # stricter threshold suppresses it; replay from history is unaffected by flags
    r = runner.invoke(app, ["anomalies", "--today", "2026-11-15", "--threshold", "500", "-f", "json"])
    assert json.loads(r.stdout)["rows"] == []


def test_forecast_live_uses_ce_and_budgets(monkeypatch):
    FakeSteampipe.install(monkeypatch, {
        "aws_cost_forecast_monthly": [{"amount": 1800}],
        "aws_budgets_budget": [
            {"name": "platform", "type": "COST", "limit_amount": 3000, "actual_amount": "1000", "forecast_amount": "2700"},
            {"name": "usage-units", "type": "USAGE", "limit_amount": 10, "actual_amount": "1", "forecast_amount": "1"},
        ],
        "aws_cost_usage": [{"grp": "EC2", "amount": 1400, "currency": "USD"}],
    })
    r = runner.invoke(app, ["forecast", "--today", "2026-11-15", "-f", "json", "--set", "budgets.monthly_total=3000"])
    rows = {x["scope"]: x for x in j(r)["rows"]}
    assert rows["account"]["forecast"] == 3200 and rows["account"]["method"] == "cost_explorer_forecast"
    assert rows["account"]["status"] == "alarm"
    assert rows["budget:platform"]["status"] == "info" and "budget:usage-units" not in rows


def test_forecast_falls_back_to_linear(monkeypatch):
    FakeSteampipe.install(monkeypatch, {
        "aws_cost_forecast_monthly": RuntimeError("DataUnavailableException: insufficient amount of historical data"),
        "aws_budgets_budget": RuntimeError("AccessDeniedException"),
        "aws_cost_usage": [{"grp": "EC2", "amount": 1400, "currency": "USD"}],
    })
    r = runner.invoke(app, ["forecast", "--today", "2026-11-15", "-f", "json"])
    rows = j(r)["rows"]
    assert len(rows) == 1 and rows[0]["method"] == "linear_run_rate" and rows[0]["forecast"] == 3000
    assert "linear run-rate" in r.output and "budgets:ViewBudget" in r.output


def test_savings_hub_and_commitments_live(monkeypatch):
    FakeSteampipe.install(monkeypatch, {
        "aws_costoptimizationhub_recommendation": [
            {"resource_arn": "arn:aws:ec2:eu-west-1:1:instance/i-1", "account_id": "1", "region": "eu-west-1",
             "action_type": "Rightsize", "current_resource_type": "m5.xlarge", "recommended_resource_type": "m7g.large",
             "saving": 88.5, "saving_pct": 41, "implementation_effort": "Low", "restart_needed": True},
            {"resource_arn": "arn:aws:ec2:eu-west-1:1:volume/vol-1", "action_type": "Delete", "saving": 0.4},
        ],
        "PURCHASE_TYPE": [{"purchase_type": "On Demand Instances", "service": "Amazon Elastic Compute Cloud - Compute",
                           "amount": 1000}],
        "aws_savingsplans_savings_plan": [{"arn": "arn:sp/1", "savings_plan_type": "Compute", "commitment": 2,
                                           "end_time": (date.today() + timedelta(days=10)).isoformat() + "T00:00:00+00:00"}],
    })
    rows = j(runner.invoke(app, ["savings", "hub", "--min-saving", "1", "-f", "json"]))["rows"]
    assert len(rows) == 1 and rows[0]["service"] == "ec2" and rows[0]["est_monthly_saving"] == 88.5
    r = runner.invoke(app, ["savings", "commitments", "--today", "2026-11-15", "-f", "json"])
    cov = j(r)["rows"]
    assert cov[0]["coverage_pct"] == 0 and cov[0]["est_monthly_saving"] == 210.0  # 70% of 1000 * 0.3
    assert "Expiring" in r.output
    r = runner.invoke(app, ["savings", "summary", "-f", "json"])
    # --min-saving only filters the display; the saved run keeps every recommendation (incl. the 0.40 one)
    assert j(r)["meta"]["extra"]["est_monthly_saving_total"] == round(88.5 + 210.0 + 0.4, 2)


# ------------------------------------------------------------------ SQL validity (offline)


@pytest.mark.parametrize("sql", [
    fc.FORECAST_SQL, fc.BUDGETS_SQL, sv.HUB_SQL, sv.PURCHASE_TYPE_SQL, sv.SAVINGS_PLANS_SQL,
    ce.cost_sql(ce.GroupBy.parse("service"), "net_unblended", ["Credit"], "DAILY"),
    ce.cost_sql(ce.GroupBy.parse("tag:Team"), "unblended", [], "DAILY"),
], ids=["forecast", "budgets", "hub", "purchase_type", "savings_plans", "daily", "daily_tag"])
def test_phase5_sql_valid(sql):
    assert check_sql(sql) == []


def test_savings_summary_says_what_is_missing_and_names_the_biggest_item(tmp_path, monkeypatch):
    import shutil
    import sqlite3

    from spendlens.history import demo_db_path

    db = tmp_path / "h.db"
    shutil.copy(demo_db_path(), db)
    with sqlite3.connect(db) as c:  # only a rightsize run, like a first-time user
        c.execute("delete from findings where run_id in (select id from runs where feature != 'rightsize')")
        c.execute("delete from runs where feature != 'rightsize'")
    monkeypatch.setenv("SPENDLENS__HISTORY__PATH", str(db))
    r = runner.invoke(app, ["savings", "summary", "--by", "service", "-f", "json"])
    doc = j(r)
    assert doc["meta"]["extra"]["not_run"] == ["waste", "savings_hub", "savings_commitments"]
    assert "Not included yet" in r.output and "spendlens waste" in r.output and "spendlens savings hub" in r.output
    top = doc["rows"][0]
    assert top["biggest_item"] and 0 < top["biggest_saving"] <= top["est_monthly_saving"]
