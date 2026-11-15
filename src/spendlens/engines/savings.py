"""Savings sources beyond spendlens's own controls: AWS Cost Optimization Hub, commitment coverage, Savings Plans.

Pure functions + SQL. Commitment coverage is computed on AMORTIZED cost by PURCHASE_TYPE: that is the standard way
to see how much compute spend runs on Savings Plans / Reserved Instances vs on-demand (upfront fees spread over
the term). It is a spend mix, not AWS's hour-based coverage report, and is labelled as such.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

HUB_SQL = (
    "select resource_arn, resource_id, recommendation_account_id as account_id, resource_region as region,\n"
    "  action_type, current_resource_type, recommended_resource_type, current_resource_summary,\n"
    "  recommended_resource_summary, estimated_monthly_savings::numeric as saving,\n"
    "  estimated_savings_percentage::numeric as saving_pct, estimated_monthly_cost::numeric as monthly_cost,\n"
    "  implementation_effort, restart_needed, rollback_possible, currency_code, source\n"
    "from aws_costoptimizationhub_recommendation"
)

PURCHASE_TYPE_SQL = (
    "select dimension_1 as purchase_type, dimension_2 as service, sum(amortized_cost_amount::numeric) as amount\n"
    "from aws_cost_usage\n"
    "where granularity = 'MONTHLY' and dimension_type_1 = 'PURCHASE_TYPE' and dimension_type_2 = 'SERVICE'\n"
    "  and period_start >= %(start)s::timestamptz and period_end <= %(end)s::timestamptz\n"
    "group by 1, 2"
)

SAVINGS_PLANS_SQL = (
    "select arn, savings_plan_id, savings_plan_type, payment_option, commitment::numeric as commitment,\n"
    "  end_time, state, region, account_id\n"
    "from aws_savingsplans_savings_plan\n"
    "where state = 'active'"
)


def purchase_bucket(purchase_type: str) -> str:
    p = purchase_type.lower()
    if "spot" in p:
        return "spot"
    if "reserved" in p or "savings plan" in p:
        return "committed"
    if "on demand" in p or "on-demand" in p:
        return "on_demand"
    return "other"


def coverage(rows: list[dict[str, Any]], services: list[str], target_pct: float, min_on_demand: float,
             saving_pct: float) -> list[dict[str, Any]]:
    """Per-service spend mix and the estimated monthly saving if on-demand spend were covered up to target_pct."""
    mix: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if services and r["service"] not in services:
            continue
        mix[r["service"]][purchase_bucket(str(r["purchase_type"]))] += float(r["amount"] or 0)
    out = []
    for svc, m in mix.items():
        on_demand, committed, spot = m["on_demand"], m["committed"], m["spot"]
        eligible = on_demand + committed  # spot is already discounted and not a commitment candidate
        if eligible <= 0:
            continue
        pct = committed / eligible * 100
        gap = max(target_pct / 100 * eligible - committed, 0.0)
        status = "skip" if on_demand < min_on_demand else ("alarm" if pct < target_pct else "ok")
        out.append({
            "service": svc, "on_demand": round(on_demand, 2), "committed": round(committed, 2),
            "spot": round(spot, 2), "coverage_pct": round(pct, 1), "target_pct": target_pct,
            "uncovered_to_target": round(gap, 2),
            "est_monthly_saving": round(gap * saving_pct, 2) if status == "alarm" else None, "status": status,
        })
    out.sort(key=lambda r: -(r["est_monthly_saving"] or 0))
    return out


_ARN_SERVICE = re.compile(r"^arn:[^:]+:([^:]+):")


def service_from_arn(arn: str | None) -> str | None:
    m = _ARN_SERVICE.match(arn or "")
    return m.group(1) if m else None


def hub_reason(r: dict[str, Any]) -> str:
    cur = r.get("current_resource_type") or "-"
    rec = r.get("recommended_resource_type") or ""
    detail = r.get("recommended_resource_summary") or r.get("current_resource_summary") or ""
    move = f"{cur} -> {rec}" if rec and rec != cur else cur
    flags = [f"effort {r.get('implementation_effort') or '?'}"]
    if r.get("restart_needed"):
        flags.append("restart needed")
    if r.get("rollback_possible") is False:
        flags.append("no rollback")
    return f"{r.get('action_type')}: {move}{(' (' + detail + ')') if detail else ''}; {', '.join(flags)}."


HUB_COMMITMENT_CONTROLS = {"coh_purchasesavingsplans", "coh_purchasereservedinstances"}


def merge_savings(spendlens: list[dict[str, Any]], hub: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per resource across sources, without double counting. Returns (rows, notes).

    * Same resource found by several spendlens controls: keep the largest single saving (actions overlap,
      e.g. downsizing and buying a reservation for the same instance are not additive).
    * Same resource in Cost Optimization Hub and spendlens: AWS's usage-based estimate wins.
    * Commitments are counted once: Cost Optimization Hub purchase recommendations if present, else spendlens's
      coverage-gap estimate; per-instance *_reservation_candidate findings are dropped when either exists.
    """
    notes: list[str] = []
    hub_commit = any(r["control"] in HUB_COMMITMENT_CONTROLS for r in hub)
    spend_commit = any(r["control"] == "commitment_coverage" for r in spendlens)
    kept = []
    dropped_cov = dropped_ri = 0
    for r in spendlens:
        if hub_commit and r["control"] == "commitment_coverage":
            dropped_cov += 1
            continue
        if (hub_commit or spend_commit) and r["control"].endswith("_reservation_candidate"):
            dropped_ri += 1
            continue
        kept.append(r)
    if dropped_cov:
        notes.append(f"{dropped_cov} coverage-gap estimate(s) replaced by Cost Optimization Hub purchase recommendations")
    if dropped_ri:
        notes.append(f"{dropped_ri} per-instance reservation candidate(s) folded into service-level commitment savings")

    by_resource: dict[str, dict[str, Any]] = {}
    for r in kept:
        cur = by_resource.get(r["resource"])
        if cur is None or (r["est_monthly_saving"] or 0) > (cur["est_monthly_saving"] or 0):
            by_resource[r["resource"]] = {**r, "source": "spendlens"}
    overlap = 0
    for r in hub:
        prev = by_resource.get(r["resource"])
        overlap += prev is not None
        by_resource[r["resource"]] = {**r, "source": "cost_optimization_hub",
                                      "also_flagged_by": prev["control"] if prev else None}
    if overlap:
        notes.append(f"{overlap} resource(s) found by both spendlens and Cost Optimization Hub counted once (AWS figure)")
    rows = sorted(by_resource.values(), key=lambda r: -(r["est_monthly_saving"] or 0))
    return rows, notes
