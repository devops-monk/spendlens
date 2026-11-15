"""Month-end projection and budget evaluation. Pure functions.

Projection = month-to-date actual + forecast for the rest of the month. The forecast comes from Cost Explorer
(GetCostForecast, via aws_cost_forecast_monthly) when available; otherwise a linear run-rate projection is used,
which is less accurate (no seasonality, no committed spend) and is labelled as such.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

FORECAST_SQL = (
    "select period_start, period_end, mean_value::numeric as amount\n"
    "from aws_cost_forecast_monthly\n"
    "where period_start >= %(start)s::timestamptz and period_end <= %(end)s::timestamptz"
)

BUDGETS_SQL = (
    "select name, type, time_unit, limit_amount::numeric as limit_amount, limit_unit,\n"
    "  calculated_spend_actual_spend as actual_amount,\n"
    "  calculated_spend_forecasted_spend as forecast_amount, account_id\n"
    "from aws_budgets_budget\n"
    "where time_unit = 'MONTHLY'"
)


def month_bounds(today: date) -> tuple[date, date]:
    first = today.replace(day=1)
    return first, first.replace(day=calendar.monthrange(today.year, today.month)[1]) + timedelta(days=1)


def linear_projection(mtd: float, today: date) -> float:
    """MTD actual covers days 1..today-1 (data lags ~24h); scale the daily run-rate to the full month."""
    first, nxt = month_bounds(today)
    elapsed = max((today - first).days, 1)
    return mtd / elapsed * (nxt - first).days


def project(mtd: float, today: date, ce_remaining: float | None) -> dict[str, Any]:
    if ce_remaining is not None:
        return {"mtd_actual": round(mtd, 2), "forecast_remaining": round(ce_remaining, 2),
                "projected": round(mtd + ce_remaining, 2), "method": "cost_explorer_forecast"}
    projected = linear_projection(mtd, today)
    return {"mtd_actual": round(mtd, 2), "forecast_remaining": round(projected - mtd, 2),
            "projected": round(projected, 2), "method": "linear_run_rate"}


def budget_status(forecast: float | None, actual: float | None, limit: float, warn_pct: float) -> tuple[str, str]:
    """(status, label): alarm = forecast (or actual) over the limit, info = above warn_pct, ok otherwise."""
    if limit <= 0:
        return "skip", "no limit"
    basis = forecast if forecast is not None else actual
    if basis is None:
        return "skip", "no spend data"
    if (actual or 0) > limit:
        return "alarm", "over budget"
    if basis > limit:
        return "alarm", "forecast over budget"
    if basis >= limit * warn_pct / 100:
        return "info", "at risk"
    return "ok", "on track"
