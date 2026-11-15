"""End-to-end anomalies and forecast: spendlens -> Steampipe -> Moto (Cost Explorer queue + AWS Budgets)."""

from __future__ import annotations

from datetime import date, timedelta

import boto3
import pytest
from test_cost_end_to_end import _queue

pytestmark = pytest.mark.system
TODAY = date(2026, 11, 15)


def _day(d: date, groups: list[tuple[list[str], float]]) -> dict:
    return {"TimePeriod": {"Start": d.isoformat(), "End": (d + timedelta(days=1)).isoformat()}, "Total": {},
            "Estimated": False,
            "Groups": [{"Keys": k, "Metrics": {"NetUnblendedCost": {"Amount": f"{a}", "Unit": "USD"},
                                                "UnblendedCost": {"Amount": f"{a}", "Unit": "USD"}}} for k, a in groups]}


def test_anomaly_spike_detected(system_env, spendlens):
    days = []
    for i in range(33, 0, -1):
        d = TODAY - timedelta(days=i)
        ec2 = 1500.0 if i == 2 else 400.0 + (i % 4) * 5
        days.append(_day(d, [(["Amazon Elastic Compute Cloud - Compute", "Usage"], ec2),
                             (["Amazon Simple Storage Service", "Usage"], 60.0)]))
    _queue(system_env["endpoint"], {"ResultsByTime": days})
    code, doc, err = spendlens("anomalies", "--today", TODAY.isoformat(), "--fail-on-anomaly", "-f", "json")
    assert code == 3, err
    (row,) = doc["rows"]
    assert row["group"] == "Amazon Elastic Compute Cloud - Compute" and row["date"] == "2026-11-13"
    assert row["baseline"] == pytest.approx(407.5, abs=5) and row["severity"] in ("high", "critical")


def test_forecast_with_aws_budget_and_linear_fallback(system_env, spendlens):
    budgets = boto3.client("budgets", region_name="us-east-1", endpoint_url=system_env["endpoint"],
                           aws_access_key_id="testing", aws_secret_access_key="testing")
    budgets.create_budget(AccountId="123456789012", Budget={
        "BudgetName": "e2e-platform", "BudgetType": "COST", "TimeUnit": "MONTHLY",
        "BudgetLimit": {"Amount": "1000", "Unit": "USD"},
        "CalculatedSpend": {"ActualSpend": {"Amount": "600", "Unit": "USD"},
                            "ForecastedSpend": {"Amount": "1150", "Unit": "USD"}}})
    # month-to-date actual (Moto has no GetCostForecast -> spendlens must fall back to a linear projection)
    _queue(system_env["endpoint"], {"ResultsByTime": [{
        "TimePeriod": {"Start": "2026-11-01", "End": TODAY.isoformat()}, "Total": {}, "Estimated": True,
        "Groups": [{"Keys": ["Amazon Elastic Compute Cloud - Compute", "Usage"],
                    "Metrics": {"UnblendedCost": {"Amount": "1400", "Unit": "USD"}}}]}]})
    code, doc, err = spendlens("forecast", "--today", TODAY.isoformat(), "-f", "json",
                               "--set", "budgets.monthly_total=2500")
    assert code == 0, err
    rows = {r["scope"]: r for r in doc["rows"]}
    acct = rows["account"]
    assert acct["actual"] == 1400 and acct["method"] == "linear_run_rate" and acct["forecast"] == 3000
    assert acct["status"] == "alarm"  # 3000 projected > 2500 budget
    assert "budget:e2e-platform" in rows, err
    b = rows["budget:e2e-platform"]
    assert (b["status"], b["label"], b["forecast_pct_of_limit"]) == ("alarm", "forecast over budget", 115.0)
