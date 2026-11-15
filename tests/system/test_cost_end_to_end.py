"""End-to-end Cost Explorer: spendlens cost -> Steampipe aws_cost_usage / aws_cost_by_tag -> Moto CE.

Moto returns queued GetCostAndUsage results, one per distinct request body, in order. Each test queues exactly
the responses its command will request (explicit periods keep the requests deterministic) and runs it at once.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

pytestmark = pytest.mark.system


def _metric(amount: float) -> dict:
    return {"NetUnblendedCost": {"Amount": f"{amount}", "Unit": "USD"}}


def _month(start: str, end: str, groups: list[tuple[list[str], float]]) -> dict:
    return {"TimePeriod": {"Start": start, "End": end}, "Total": {}, "Estimated": False,
            "Groups": [{"Keys": keys, "Metrics": _metric(a)} for keys, a in groups]}


def _queue(endpoint: str, *results: dict) -> None:
    req = urllib.request.Request(f"{endpoint}/moto-api/static/ce/cost-and-usage-results",
                                 data=json.dumps({"results": list(results)}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    assert urllib.request.urlopen(req).status == 201


def test_cost_diff_month_over_month(system_env, spendlens):
    _queue(system_env["endpoint"],
           {"ResultsByTime": [_month("2026-09-01", "2026-10-01", [
               (["Amazon Elastic Compute Cloud - Compute", "Usage"], 1000.0),
               (["Amazon Redshift", "Usage"], 400.0),
               (["Amazon Simple Storage Service", "Usage"], 100.0),
               (["Amazon Simple Storage Service", "Credit"], -50.0),
           ])]},
           {"ResultsByTime": [_month("2026-10-01", "2026-11-01", [
               (["Amazon Elastic Compute Cloud - Compute", "Usage"], 1300.0),
               (["Amazon Bedrock", "Usage"], 220.0),
               (["Amazon Simple Storage Service", "Usage"], 100.0),
           ])]})
    code, doc, err = spendlens("cost", "diff", "--from", "2026-09", "--to", "2026-10", "--show", "all", "-f", "json")
    assert code == 0, err
    rows = {r["group"]: r for r in doc["rows"]}
    ec2 = rows["Amazon Elastic Compute Cloud - Compute"]
    assert (ec2["base"], ec2["compare"], ec2["change"], ec2["status"], ec2["flagged"]) == (1000, 1300, 300, "increase", True)
    assert rows["Amazon Redshift"]["status"] == "gone" and rows["Amazon Bedrock"]["status"] == "new"
    # credits excluded by default (cost.exclude_record_types): S3 stays flat at 100
    assert rows["Amazon Simple Storage Service"]["base"] == 100 and rows["Amazon Simple Storage Service"]["status"] == "flat"
    assert rows["TOTAL"]["change"] == 120


def test_cost_showback_by_tag(system_env, spendlens):
    _queue(system_env["endpoint"],
           {"ResultsByTime": [_month("2026-10-01", "2026-11-01", [
               (["Team$payments"], 700.0), (["Team$search"], 200.0), (["Team$"], 100.0),
           ])]})
    code, doc, err = spendlens("cost", "showback", "-t", "Team", "--month", "2026-10", "-f", "json",
                               "--set", "budgets.per_tag={Team: {payments: 600}}")
    assert code == 0, err
    rows = {r["tag_value"]: r for r in doc["rows"]}
    assert rows["payments"]["share_pct"] == 70.0 and rows["payments"]["over_budget"] is True
    assert rows["(untagged)"]["amount"] == 100
    assert "10.0% of spend" in err


def test_cost_diff_replays_offline(spendlens):
    code, doc, _ = spendlens("cost", "diff", "--source", "last", "--show", "all", "-f", "json")
    assert code == 0 and doc["meta"]["extra"]["source"] == "last"
    assert any(r["group"] == "Amazon Bedrock" for r in doc["rows"])
