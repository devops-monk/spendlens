from datetime import date

import pytest

from spendlens.engines import cost as ce

T = ce.DiffThresholds(abs_change=10, pct_change=20, min_amount=0.01)


# ------------------------------------------------------------------ periods


def test_last_full_months():
    base, cmp = ce.last_full_months(date(2026, 1, 15))
    assert (base.start, base.end, base.label) == (date(2025, 11, 1), date(2025, 12, 1), "2025-11")
    assert (cmp.start, cmp.end, cmp.label) == (date(2025, 12, 1), date(2026, 1, 1), "2025-12")


def test_month_to_date_same_days_and_month_end_clipping():
    base, cmp = ce.month_to_date(date(2026, 11, 15))
    assert (base.start, base.end) == (date(2026, 10, 1), date(2026, 10, 15))
    assert (cmp.start, cmp.end) == (date(2026, 11, 1), date(2026, 11, 15))
    # 31 March: 30 days of March vs all 28 days of February (clipped, labelled honestly)
    base, cmp = ce.month_to_date(date(2026, 3, 31))
    assert base.end == date(2026, 3, 1) and base.label == "2026-02 (1-28)" and cmp.label == "2026-03 (1-30)"
    # on the 1st there is no complete day yet: fall back to last full months
    assert ce.month_to_date(date(2026, 11, 1)) == ce.last_full_months(date(2026, 11, 1))


def test_parse_period_and_errors():
    p = ce.parse_period("2026-07..2026-09")
    assert (p.start, p.end, p.days) == (date(2026, 7, 1), date(2026, 10, 1), 92)
    assert ce.parse_period("2026-12").end == date(2027, 1, 1)
    for bad in ("2026-13", "26-01", "2026-09..2026-07", "last month"):
        with pytest.raises(ValueError):
            ce.parse_period(bad)


def test_trailing_months():
    labels = [p.label for p in ce.trailing_months(date(2026, 2, 10), 3, include_current=True)]
    assert labels == ["2025-11", "2025-12", "2026-01", "2026-02 (MTD)"]
    assert ce.Period.from_dict(ce.trailing_months(date(2026, 2, 10), 2)[0].as_dict()).label == "2025-12"


def test_resource_windows_bounds():
    a, b = ce.resource_windows(date(2026, 11, 15), 7)
    assert (a.start, a.end, b.start, b.end) == (date(2026, 11, 1), date(2026, 11, 8), date(2026, 11, 8), date(2026, 11, 15))
    with pytest.raises(ValueError):
        ce.resource_windows(date(2026, 11, 15), 8)


# ------------------------------------------------------------------ group-by & SQL


def test_group_by_parse():
    assert ce.GroupBy.parse("account") == ce.GroupBy("dimension", "LINKED_ACCOUNT", "account")
    assert ce.GroupBy.parse("tag:Team") == ce.GroupBy("tag", "Team", "tag:Team")
    for bad in ("tag:", "colour"):
        with pytest.raises(ValueError):
            ce.GroupBy.parse(bad)


def test_cost_sql_uses_exactly_one_time_qual_pair():
    sql = ce.cost_sql(ce.GroupBy.parse("service"), "net_unblended", ["Credit"])
    # the plugin maps one period_start >= and one period_end <= to the API TimePeriod (bounded single call)
    assert sql.count("period_start >=") == 1 and sql.count("period_end <=") == 1
    assert "dimension_type_1 = 'SERVICE'" in sql and "dimension_type_2 = 'RECORD_TYPE'" in sql
    assert "net_unblended_cost_amount" in sql and "dimension_2 <> all(%(excluded)s::text[])" in sql
    assert "$" not in sql  # psycopg placeholders, not Powerpipe's $n
    assert "<> all" not in ce.cost_sql(ce.GroupBy.parse("service"), "amortized", [])
    rec = ce.cost_sql(ce.GroupBy.parse("record-type"), "unblended", ["Credit"])
    assert "dimension_type_2 = 'SERVICE'" in rec and "dimension_1 <> all" in rec


def test_cost_params():
    p = ce.parse_period("2026-10")
    assert ce.cost_params(ce.GroupBy.parse("tag:Team"), p, ["Credit"]) == {
        "start": "2026-10-01", "end": "2026-11-01", "tag": "Team"}
    assert ce.cost_params(ce.GroupBy.parse("region"), p, []) == {"start": "2026-10-01", "end": "2026-11-01"}
    assert ce.cost_params(ce.GroupBy.parse("region"), p, ["Credit"])["excluded"] == ["Credit"]


# ------------------------------------------------------------------ maths


def test_diff_statuses_include_new_and_gone_services():
    """Regression for the legacy control, whose `prev.cost != base.cost` dropped new/removed services."""
    rows = {r["group"]: r for r in ce.diff({"EC2": 100, "Redshift": 500, "S3": 50, "KMS": 1},
                                            {"EC2": 150, "Bedrock": 40, "S3": 50, "KMS": 1.5}, T)}
    assert rows["EC2"]["status"] == "increase" and rows["EC2"]["flagged"] and rows["EC2"]["change_pct"] == 50.0
    assert rows["Redshift"]["status"] == "gone" and rows["Redshift"]["flagged"]
    assert rows["Bedrock"]["status"] == "new" and rows["Bedrock"]["change_pct"] is None and rows["Bedrock"]["flagged"]
    assert rows["S3"]["status"] == "flat" and not rows["S3"]["flagged"]
    assert not rows["KMS"]["flagged"]  # +50% but only +0.50: below the absolute threshold
    order = [r["group"] for r in ce.diff({"a": 1, "b": 100}, {"a": 50, "b": 0}, T)]
    assert order == ["b", "a"]  # largest absolute change first


def test_diff_threshold_needs_both_abs_and_pct():
    rows = {r["group"]: r for r in ce.diff({"big": 10_000, "small": 20}, {"big": 10_500, "small": 40}, T)}
    assert not rows["big"]["flagged"]  # +500 but only +5%
    assert rows["small"]["flagged"]  # +20 and +100%


def test_diff_daily_rate_for_unequal_periods_and_total():
    rows = ce.diff({"EC2": 300}, {"EC2": 310}, T, base_days=30, compare_days=31)
    assert rows[0]["change_pct"] == 3.3 and rows[0]["change_pct_daily"] == 0.0
    total = ce.diff_total(ce.diff({"a": 100, "b": 50}, {"a": 120, "c": 10}, T))
    assert total == {"group": "TOTAL", "base": 150.0, "compare": 130.0, "change": -20.0, "change_pct": -13.3,
                     "status": "decrease", "flagged": False, "currency": "USD"}


def test_trend_pivot():
    ps = ce.trailing_months(date(2026, 4, 2), 3)
    rows = ce.trend([(ps[0], {"EC2": 100}), (ps[1], {"EC2": 110, "S3": 5}), (ps[2], {"EC2": 99})], top=1)
    assert rows == [{"group": "EC2", "2026-01": 100.0, "2026-02": 110.0, "2026-03": 99.0, "total": 309.0,
                     "last_change": -11.0, "last_change_pct": -10.0}]


def test_showback_shares_budgets_and_untagged():
    rows = ce.showback({"payments": 600, "(untagged)": 400}, "Team", {"payments": 500})
    assert rows[0] == {"tag_key": "Team", "tag_value": "payments", "amount": 600, "share_pct": 60.0, "budget": 500,
                       "budget_variance": 100, "over_budget": True, "currency": "USD"}
    assert rows[1]["tag_value"] == "(untagged)" and rows[1]["budget"] is None
    assert ce.showback({}, "Team") == []


def test_totals_by_group():
    totals, cur = ce.totals_by_group([{"grp": "a", "amount": 1.5, "currency": "EUR"}, {"grp": "a", "amount": "2"}])
    assert totals == {"a": 3.5} and cur == "EUR"
