"""Cost anomaly detection on daily spend per group (service, account, tag value ...).

Method: for each checked day, compare the cost with the trailing `lookback_days` baseline using a robust z-score
(median and MAD, scaled to be comparable to a standard deviation). Robust statistics are not dragged around by the
very spikes we are looking for, unlike mean/stddev. A spike must also exceed `min_increase` in absolute terms, so
tiny services jumping from $0.10 to $0.50 are ignored.

Pure functions only: input is {group: {date: cost}}, output is a list of anomaly dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any

MAD_TO_SIGMA = 1.4826  # MAD * 1.4826 ~ standard deviation for normally distributed data


@dataclass(frozen=True)
class AnomalySettings:
    lookback_days: int = 30
    threshold: float = 3.0
    min_daily_cost: float = 1.0
    min_increase: float = 10.0
    include_drops: bool = False


def detection_days(today: date, detect_days: int) -> list[date]:
    """Most recent complete days (Cost Explorer daily data lags ~24h, so up to yesterday)."""
    return [today - timedelta(days=i) for i in range(detect_days, 0, -1)]


def query_window(today: date, detect_days: int, lookback_days: int) -> tuple[date, date]:
    """[start, end) covering the baseline of the earliest checked day through yesterday."""
    first = today - timedelta(days=detect_days)
    return first - timedelta(days=lookback_days), today


def robust_z(value: float, baseline: list[float], floor: float) -> tuple[float, float, float]:
    """(z, median, scale). The scale has a floor so flat baselines (MAD = 0) don't produce infinite z-scores."""
    med = median(baseline)
    mad = median(abs(x - med) for x in baseline)
    scale = max(mad * MAD_TO_SIGMA, abs(med) * 0.05, floor)
    return (value - med) / scale, med, scale


def severity(z: float) -> str:
    z = abs(z)
    return "critical" if z >= 10 else "high" if z >= 6 else "medium" if z >= 4 else "low"


def detect(series: dict[str, dict[date, float]], days: list[date], s: AnomalySettings) -> list[dict[str, Any]]:
    out = []
    for group, by_day in series.items():
        for day in days:
            value = by_day.get(day, 0.0)
            baseline_days = [day - timedelta(days=i) for i in range(s.lookback_days, 0, -1)]
            baseline = [by_day.get(d, 0.0) for d in baseline_days]
            if sum(1 for v in baseline if v > 0) < max(3, s.lookback_days // 4):
                continue  # not enough history (e.g. brand-new service): reported by `cost diff` as "new"
            z, med, _ = robust_z(value, baseline, max(s.min_daily_cost, 0.01))
            delta = value - med
            spike = z >= s.threshold and delta >= s.min_increase and value >= s.min_daily_cost
            drop = s.include_drops and z <= -s.threshold and -delta >= s.min_increase and med >= s.min_daily_cost
            if not (spike or drop):
                continue
            out.append({
                "date": day.isoformat(), "group": group, "direction": "spike" if spike else "drop",
                "cost": round(value, 2), "baseline": round(med, 2), "delta": round(delta, 2),
                "delta_pct": round(delta / med * 100, 1) if med else None, "z": round(z, 1),
                "severity": severity(z),
            })
    out.sort(key=lambda a: (-abs(a["delta"]), a["date"], a["group"]))
    return out


def series_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    for r in rows:
        d = r["day"] if isinstance(r["day"], date) else date.fromisoformat(str(r["day"])[:10])
        out.setdefault(str(r["grp"]), {})
        out[str(r["grp"])][d] = out[str(r["grp"])].get(d, 0.0) + float(r["amount"] or 0)
    return out
