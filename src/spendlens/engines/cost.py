"""Cost engine: periods, Cost Explorer SQL (via the Steampipe aws plugin) and diff/trend/showback maths.

Pure functions only (no I/O) so everything here is unit-testable. Notes on the plugin (steampipe-plugin-aws):
  * aws_cost_usage / aws_cost_by_tag map ONE `period_start >= X` qual and ONE `period_end <= Y` qual to the
    Cost Explorer TimePeriod (end exclusive). We always emit exactly that pair, so each query is one bounded call.
  * Only the selected metric column is requested from the API (fewer bytes, lower latency).
  * dimension_type_2 is required on aws_cost_usage; we use RECORD_TYPE to exclude credits/refunds, like the
    legacy `full_month_cost_changes` control did.
  * Cost Explorer data lags ~24h and the current month is an estimate.
"""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

METRICS = {
    "unblended": "unblended_cost_amount",
    "net_unblended": "net_unblended_cost_amount",
    "amortized": "amortized_cost_amount",
    "net_amortized": "net_amortized_cost_amount",
    "blended": "blended_cost_amount",
}

# --group-by value -> Cost Explorer dimension
DIMENSIONS = {
    "service": "SERVICE",
    "account": "LINKED_ACCOUNT",
    "region": "REGION",
    "usage-type": "USAGE_TYPE",
    "instance-type": "INSTANCE_TYPE",
    "operation": "OPERATION",
    "record-type": "RECORD_TYPE",
    "purchase-type": "PURCHASE_TYPE",
}

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


# ------------------------------------------------------------------ periods


@dataclass(frozen=True)
class Period:
    start: date  # inclusive
    end: date  # exclusive
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def as_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> Period:
        return cls(date.fromisoformat(d["start"]), date.fromisoformat(d["end"]), d["label"])


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def parse_month(s: str) -> date:
    m = _MONTH_RE.match(s.strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"Expected YYYY-MM, got '{s}'")
    return date(int(m.group(1)), int(m.group(2)), 1)


def parse_period(s: str) -> Period:
    """'2026-10' (one month) or '2026-07..2026-09' (inclusive month range)."""
    if ".." in s:
        a, b = s.split("..", 1)
        first, last = parse_month(a), parse_month(b)
        if last < first:
            raise ValueError(f"Range end before start: '{s}'")
        return Period(first, add_months(last, 1), f"{a.strip()}..{b.strip()}")
    first = parse_month(s)
    return Period(first, add_months(first, 1), s.strip())


def last_full_months(today: date) -> tuple[Period, Period]:
    """(month before last, last full month)."""
    this = month_start(today)
    last = add_months(this, -1)
    prev = add_months(this, -2)
    return (Period(prev, last, prev.strftime("%Y-%m")), Period(last, this, last.strftime("%Y-%m")))


def month_to_date(today: date) -> tuple[Period, Period]:
    """Current month to yesterday vs the same number of days of the previous month.

    Cost Explorer end dates are exclusive and data lags ~24h, so 'to date' means up to (not including) today.
    On the 1st of the month there is no complete day yet: compare yesterday's month instead.
    """
    if today.day == 1:
        return last_full_months(today)
    this = month_start(today)
    prev = add_months(this, -1)
    days = (today - this).days
    prev_end = min(prev + timedelta(days=days), this)
    prev_days = (prev_end - prev).days
    return (Period(prev, prev_end, f"{prev:%Y-%m} (1-{prev_days})"), Period(this, today, f"{this:%Y-%m} (1-{days})"))


def trailing_months(today: date, n: int, include_current: bool = False) -> list[Period]:
    """The last `n` full months (oldest first), optionally followed by the current month to date."""
    this = month_start(today)
    periods = [Period(s, add_months(s, 1), f"{s:%Y-%m}") for s in (add_months(this, -i) for i in range(n, 0, -1))]
    if include_current and today > this:
        periods.append(Period(this, today, f"{this:%Y-%m} (MTD)"))
    return periods


# ------------------------------------------------------------------ grouping & SQL


@dataclass(frozen=True)
class GroupBy:
    kind: str  # "dimension" | "tag"
    key: str  # CE dimension (SERVICE ...) or tag key
    label: str  # what the user typed

    @classmethod
    def parse(cls, s: str) -> GroupBy:
        s = s.strip()
        if s.lower().startswith("tag:"):
            key = s[4:].strip()
            if not key:
                raise ValueError("tag group-by needs a key, e.g. tag:Team")
            return cls("tag", key, s)
        if s.lower() not in DIMENSIONS:
            raise ValueError(f"Unknown group-by '{s}'. Use one of: {', '.join(DIMENSIONS)}, tag:<Key>")
        return cls("dimension", DIMENSIONS[s.lower()], s.lower())


def cost_sql(group: GroupBy, metric: str, exclude_record_types: Iterable[str]) -> str:
    """SQL returning (grp, amount, currency) for one period. psycopg named params: start, end (exclusive),
    excluded (record types) or tag (tag key) -- see cost_params()."""
    col = METRICS[metric]
    unit = col.replace("_amount", "_unit")
    if group.kind == "tag":
        return (
            "select coalesce(nullif(tag_value_1, ''), '(untagged)') as grp,\n"
            f"  sum({col}::numeric) as amount, max({unit}) as currency\n"
            "from aws_cost_by_tag\n"
            "where granularity = 'MONTHLY' and tag_key_1 = %(tag)s\n"
            "  and period_start >= %(start)s::timestamptz and period_end <= %(end)s::timestamptz\n"
            "group by 1"
        )
    excluded = [r for r in exclude_record_types]
    if group.key == "RECORD_TYPE":
        dim, dim2, rec = "dimension_1", "SERVICE", "dimension_1"
    else:
        dim, dim2, rec = "dimension_1", "RECORD_TYPE", "dimension_2"
    record_filter = (f"\n  and {rec} <> all(%(excluded)s::text[])" if excluded else "")
    return (
        f"select coalesce(nullif({dim}, ''), '(none)') as grp,\n"
        f"  sum({col}::numeric) as amount, max({unit}) as currency\n"
        "from aws_cost_usage\n"
        f"where granularity = 'MONTHLY' and dimension_type_1 = '{group.key}' and dimension_type_2 = '{dim2}'\n"
        "  and period_start >= %(start)s::timestamptz and period_end <= %(end)s::timestamptz"
        f"{record_filter}\n"
        "group by 1"
    )


def cost_params(group: GroupBy, period: Period, exclude_record_types: Iterable[str]) -> dict[str, Any]:
    params: dict[str, Any] = {"start": period.start.isoformat(), "end": period.end.isoformat()}
    if group.kind == "tag":
        params["tag"] = group.key
    elif excluded := list(exclude_record_types):
        params["excluded"] = excluded
    return params


def resource_sql(metric: str) -> str:
    """Daily cost per resource (Cost Explorer resource-level data: last 14 days only, needs opt-in)."""
    col = METRICS[metric]
    return (
        "select resource_id as grp, period_start::date as day,\n"
        f"  sum({col}::numeric) as amount, max({col.replace('_amount', '_unit')}) as currency\n"
        "from aws_cost_by_resource_daily\n"
        "where period_start >= %(start)s::timestamptz and period_end <= %(end)s::timestamptz\n"
        "group by 1, 2"
    )


# ------------------------------------------------------------------ maths


def totals_by_group(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, float], str]:
    out: dict[str, float] = defaultdict(float)
    currency = "USD"
    for r in rows:
        out[str(r["grp"])] += float(r["amount"] or 0)
        currency = r.get("currency") or currency
    return dict(out), currency


@dataclass(frozen=True)
class DiffThresholds:
    abs_change: float = 10.0  # flag when |change| >= this (legacy cost_explorer_service_cost_max_cost_units)
    pct_change: float = 20.0  # ... and |change %| >= this (new/gone groups only need abs_change)
    min_amount: float = 0.01  # ignore groups below this in both periods


def diff(base: dict[str, float], compare: dict[str, float], t: DiffThresholds, currency: str = "USD",
         base_days: int | None = None, compare_days: int | None = None) -> list[dict[str, Any]]:
    """Per-group change between two periods. Includes groups only present in one period (fixes the legacy
    control's `where prev.cost != base.cost`, which silently dropped new and removed services).

    When the periods differ in length (e.g. 30 vs 31 days), change_pct_daily compares daily run-rates."""
    rows = []
    for grp in sorted(base.keys() | compare.keys()):
        b, c = round(base.get(grp, 0.0), 2), round(compare.get(grp, 0.0), 2)
        if abs(b) < t.min_amount and abs(c) < t.min_amount:
            continue
        change = round(c - b, 2)
        pct = round(change / b * 100, 1) if b else None
        if grp not in base or abs(b) < t.min_amount:
            status = "new"
        elif grp not in compare or abs(c) < t.min_amount:
            status = "gone"
        elif abs(change) < 0.01:
            status = "flat"
        else:
            status = "increase" if change > 0 else "decrease"
        if status in ("new", "gone"):
            flagged = abs(change) >= t.abs_change
        else:
            flagged = abs(change) >= t.abs_change and pct is not None and abs(pct) >= t.pct_change
        row = {"group": grp, "base": b, "compare": c, "change": change, "change_pct": pct, "status": status,
               "flagged": flagged, "currency": currency}
        if base_days and compare_days and base_days != compare_days and b:
            rb, rc = b / base_days, c / compare_days
            row["change_pct_daily"] = round((rc - rb) / rb * 100, 1)
        rows.append(row)
    rows.sort(key=lambda r: (-abs(r["change"]), r["group"]))
    return rows


def diff_total(rows: list[dict[str, Any]], currency: str = "USD") -> dict[str, Any]:
    b = round(sum(r["base"] for r in rows), 2)
    c = round(sum(r["compare"] for r in rows), 2)
    change = round(c - b, 2)
    return {"group": "TOTAL", "base": b, "compare": c, "change": change,
            "change_pct": round(change / b * 100, 1) if b else None,
            "status": "increase" if change > 0 else "decrease" if change < 0 else "flat",
            "flagged": False, "currency": currency}


def trend(per_period: list[tuple[Period, dict[str, float]]], top: int | None = None) -> list[dict[str, Any]]:
    """Pivot: one row per group, one column per period, plus last-vs-previous change."""
    groups = sorted({g for _, totals in per_period for g in totals})
    rows = []
    for g in groups:
        row: dict[str, Any] = {"group": g}
        vals = []
        for p, totals in per_period:
            v = round(totals.get(g, 0.0), 2)
            row[p.label] = v
            vals.append(v)
        row["total"] = round(sum(vals), 2)
        if len(vals) >= 2:
            row["last_change"] = round(vals[-1] - vals[-2], 2)
            row["last_change_pct"] = round((vals[-1] - vals[-2]) / vals[-2] * 100, 1) if vals[-2] else None
        rows.append(row)
    rows.sort(key=lambda r: -r["total"])
    return rows[:top] if top else rows


def showback(totals: dict[str, float], tag_key: str, budgets: dict[str, float] | None = None,
             currency: str = "USD") -> list[dict[str, Any]]:
    grand = sum(totals.values()) or 0.0
    budgets = budgets or {}
    rows = []
    for value, amount in sorted(totals.items(), key=lambda kv: -kv[1]):
        budget = budgets.get(value)
        rows.append({
            "tag_key": tag_key, "tag_value": value, "amount": round(amount, 2),
            "share_pct": round(amount / grand * 100, 1) if grand else None,
            "budget": budget, "budget_variance": round(amount - budget, 2) if budget is not None else None,
            "over_budget": (amount > budget) if budget is not None else None, "currency": currency,
        })
    return rows


def resource_windows(today: date, days: int) -> tuple[Period, Period]:
    """Two adjacent windows of `days` ending yesterday (resource-level data: 14 days max, so days <= 7)."""
    if not 1 <= days <= 7:
        raise ValueError("--days must be between 1 and 7 (Cost Explorer keeps 14 days of resource-level data)")
    end = today
    mid = end - timedelta(days=days)
    start = mid - timedelta(days=days)
    return (Period(start, mid, f"{start}..{mid - timedelta(days=1)}"), Period(mid, end, f"{mid}..{end - timedelta(days=1)}"))
