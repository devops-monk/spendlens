from datetime import date, timedelta

import pytest

from spendlens.engines import anomaly as an
from spendlens.engines import forecast as fc
from spendlens.engines import savings as sv

TODAY = date(2026, 11, 15)
S = an.AnomalySettings(lookback_days=30, threshold=3.0, min_daily_cost=1.0, min_increase=10.0)


def series(base: float, overrides: dict[int, float] | None = None, days: int = 40, noise: float = 0.0) -> dict[date, float]:
    """Daily costs ending yesterday; overrides by days-ago."""
    out = {}
    for i in range(1, days + 1):
        d = TODAY - timedelta(days=i)
        out[d] = base + (noise if i % 2 else -noise)
    for ago, v in (overrides or {}).items():
        out[TODAY - timedelta(days=ago)] = v
    return out


# ------------------------------------------------------------------ anomaly


def test_windows():
    assert an.detection_days(TODAY, 3) == [date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 14)]
    assert an.query_window(TODAY, 3, 30) == (date(2026, 10, 13), TODAY)


def test_spike_detected_with_robust_stats():
    found = an.detect({"EC2": series(100, {1: 400}, noise=5)}, an.detection_days(TODAY, 3), S)
    assert len(found) == 1
    a = found[0]
    assert (a["date"], a["group"], a["direction"], a["baseline"], a["delta"]) == ("2026-11-14", "EC2", "spike", 100.0, 300.0)
    assert a["z"] >= 3 and a["severity"] in ("high", "critical")


def test_previous_spike_does_not_mask_new_one():
    # robust baseline (median/MAD): a spike 5 days ago does not inflate the baseline like a mean/stddev would
    found = an.detect({"EC2": series(100, {5: 900, 1: 300}, noise=3)}, an.detection_days(TODAY, 1), S)
    assert [a["delta"] for a in found] == [200.0]


def test_flat_baseline_and_small_changes():
    # MAD = 0 must not divide by zero; +5/day is below min_increase
    assert an.detect({"KMS": series(20, {1: 25})}, an.detection_days(TODAY, 1), S) == []
    assert len(an.detect({"KMS": series(20, {1: 60})}, an.detection_days(TODAY, 1), S)) == 1


def test_new_service_without_history_is_skipped_and_drops_optional():
    new = {TODAY - timedelta(days=1): 500.0}
    assert an.detect({"Bedrock": new}, an.detection_days(TODAY, 1), S) == []
    drop = {"RDS": series(300, {1: 20}, noise=4)}
    assert an.detect(drop, an.detection_days(TODAY, 1), S) == []
    found = an.detect(drop, an.detection_days(TODAY, 1), an.AnomalySettings(include_drops=True))
    assert found[0]["direction"] == "drop" and found[0]["delta"] == -280.0


def test_series_from_rows_and_severity():
    s = an.series_from_rows([{"grp": "a", "day": "2026-11-01", "amount": 1}, {"grp": "a", "day": date(2026, 11, 1), "amount": 2}])
    assert s == {"a": {date(2026, 11, 1): 3.0}}
    assert [an.severity(z) for z in (3, 4.5, 7, 12, -12)] == ["low", "medium", "high", "critical", "critical"]


# ------------------------------------------------------------------ forecast


def test_month_bounds_and_linear_projection():
    assert fc.month_bounds(date(2026, 12, 31)) == (date(2026, 12, 1), date(2027, 1, 1))
    assert fc.month_bounds(date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 3, 1))
    # 14 complete days at 100/day in a 30-day month
    assert fc.linear_projection(1400, TODAY) == 3000
    assert fc.linear_projection(0, date(2026, 11, 1)) == 0


def test_project_prefers_cost_explorer():
    assert fc.project(1000, TODAY, 2500)["method"] == "cost_explorer_forecast"
    p = fc.project(1400, TODAY, None)
    assert (p["projected"], p["forecast_remaining"], p["method"]) == (3000, 1600, "linear_run_rate")


@pytest.mark.parametrize(("forecast", "actual", "want"), [
    (1200, 500, ("alarm", "forecast over budget")),
    (900, 500, ("info", "at risk")),
    (500, 300, ("ok", "on track")),
    (900, 1100, ("alarm", "over budget")),
    (None, 900, ("info", "at risk")),
    (None, None, ("skip", "no spend data")),
])
def test_budget_status(forecast, actual, want):
    assert fc.budget_status(forecast, actual, 1000, 80) == want
    assert fc.budget_status(1, 1, 0, 80)[0] == "skip"


# ------------------------------------------------------------------ savings


def test_purchase_buckets_and_coverage():
    assert [sv.purchase_bucket(p) for p in ("On Demand Instances", "Savings Plans", "Standard Reserved Instances",
                                            "Spot Instances", "Dedicated Usage")] == [
        "on_demand", "committed", "committed", "spot", "other"]
    rows = [{"purchase_type": "On Demand Instances", "service": "EC2", "amount": 600},
            {"purchase_type": "Savings Plans", "service": "EC2", "amount": 400},
            {"purchase_type": "Spot Instances", "service": "EC2", "amount": 300},
            {"purchase_type": "On Demand Instances", "service": "Tiny", "amount": 50},
            {"purchase_type": "On Demand Instances", "service": "Other", "amount": 999}]
    out = {r["service"]: r for r in sv.coverage(rows, ["EC2", "Tiny"], 70, 100, 0.3)}
    ec2 = out["EC2"]
    # spot excluded from eligible spend; target 70% of 1000 = 700 -> gap 300 -> 30% saving = 90
    assert (ec2["coverage_pct"], ec2["uncovered_to_target"], ec2["est_monthly_saving"], ec2["status"]) == (40.0, 300.0, 90.0, "alarm")
    assert out["Tiny"]["status"] == "skip" and "Other" not in out


def test_hub_helpers():
    assert sv.service_from_arn("arn:aws:rds:eu-west-1:1:db:x") == "rds" and sv.service_from_arn("i-123") is None
    r = sv.hub_reason({"action_type": "Rightsize", "current_resource_type": "m5.xlarge", "recommended_resource_type": "m7g.large",
                       "implementation_effort": "Low", "restart_needed": True, "rollback_possible": False})
    assert r == "Rightsize: m5.xlarge -> m7g.large; effort Low, restart needed, no rollback."


def R(resource, control, saving):
    return {"resource": resource, "control": control, "est_monthly_saving": saving, "service": "-", "reason": ""}


def test_merge_savings_never_double_counts():
    spendlens = [R("i-1", "ec2_instance_low_utilization", 50), R("i-1", "ec2_instance_without_graviton", 20),
                 R("i-2", "ec2_instance_reservation_candidate", 30), R("service:EC2", "commitment_coverage", 400),
                 R("vol-1", "ebs_volume_unattached", 8)]
    rows, notes = sv.merge_savings(spendlens, [])
    by = {r["resource"]: r for r in rows}
    assert by["i-1"]["est_monthly_saving"] == 50  # overlapping actions on one resource are not additive
    assert "i-2" not in by and "service:EC2" in by  # reservation candidate folded into coverage
    assert any("reservation candidate" in n for n in notes)

    hub = [R("i-1", "coh_rightsize", 70), R("arn:sp", "coh_purchasesavingsplans", 350)]
    rows, notes = sv.merge_savings(spendlens, hub)
    by = {r["resource"]: r for r in rows}
    assert by["i-1"]["source"] == "cost_optimization_hub" and by["i-1"]["also_flagged_by"] == "ec2_instance_low_utilization"
    assert "service:EC2" not in by  # AWS commitment recommendation replaces the coverage-gap estimate
    assert round(sum(r["est_monthly_saving"] for r in rows)) == 70 + 350 + 8
    assert len(notes) == 3
