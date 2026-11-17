"""Plain-English "How to read this" text for every dashboard section and single-command HTML report.

Each guide is a list of (heading, sentence) pairs built from the configuration actually used, so the text always
states the real thresholds and names the setting that changes them.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..render import charts as ch

Guide = list[tuple[str, str]]

METRIC_TEXT = {
    "net_unblended": "net unblended cost: what you are charged after discounts (e.g. Savings Plans, EDP) are applied",
    "unblended": "unblended cost: the on-demand rate actually charged per line item, before net discounts",
    "amortized": "amortized cost: Savings Plan and RI upfront fees spread over their term",
    "net_amortized": "net amortized cost: amortized cost after discounts",
    "blended": "blended cost: rates averaged across an AWS Organization's accounts",
}


def flag_rule(cfg: Config, cur: str = "USD") -> str:
    c = cfg.cost
    amount, pct = ch.money(c.change_alarm_abs, cur), f"{c.change_alarm_pct:g}%"
    if c.change_alarm_pct <= 0 and c.change_alarm_abs <= 0:
        return "every change is flagged (cost.change_alarm_abs and cost.change_alarm_pct are both 0)"
    if c.change_alarm_pct <= 0:
        return (f"a change is flagged when it is at least {amount}, whatever its percentage "
                "(cost.change_alarm_abs; cost.change_alarm_pct is 0)")
    if c.change_alarm_abs <= 0:
        return (f"a change is flagged when it is at least {pct}, whatever its size "
                "(cost.change_alarm_pct; cost.change_alarm_abs is 0)")
    return (f"a change is flagged when it is at least {amount} AND at least {pct} "
            f"(cost.change_alarm_abs / cost.change_alarm_pct); a group that is new or gone only needs the {amount}")


def cost_diff(cfg: Config, p0: str, p1: str, group: str, metric: str | None = None, cur: str = "USD") -> Guide:
    m = metric or cfg.cost.metric
    excluded = ", ".join(cfg.cost.exclude_record_types) or "nothing"
    return [
        ("What it shows", (f"Spend per {group} in {p0} compared with {p1}. Bars to the right are increases, bars to "
                          "the left are decreases; the longest bars are the biggest changes in money.")),
        ("Flagged", flag_rule(cfg, cur).capitalize() + ". Flagged rows are the ones worth a look first."),
        ("Statuses", ("increase / decrease: spend went up or down · new: no spend in the earlier period · "
                     "gone: no spend in the later period · flat: below cost.min_amount in both.")),
        ("How it is measured", (f"{METRIC_TEXT.get(m, m)}. Excluded record types: {excluded} "
                               "(cost.exclude_record_types). Cost Explorer data lags about a day.")),
        ("What to do next", ("For the biggest increases, see which resources or apps caused them: "
                            f"spendlens cost diff -g {group if not group.startswith('tag:') else 'service'} -b tag:Name")),
    ]


def trend(cfg: Config, group: str) -> Guide:
    return [
        ("What it shows", (f"Monthly spend per {group} for the top groups, oldest month on the left. Hover a point "
                          "for its value; the table lists every group with its latest month-over-month change.")),
        ("How to read it", ("A rising line is growing spend; a step up usually means something new was deployed or "
                           "scaled. For the latest month in detail, use spendlens cost diff (or the Cost diff section).")),
    ]


def showback(cfg: Config, key: str) -> Guide:
    budgets = "budgets.per_tag" if cfg.budgets.per_tag.get(key) else "budgets.per_tag (none set for this key)"
    return [
        ("What it shows", (f"Last month's spend split by the value of the '{key}' tag, largest first, with each "
                          f"value's share of the total. Variance compares each value against {budgets}.")),
        ("Untagged", (f"The grey bar is spend on resources without a '{key}' tag, or from before the tag was activated "
                     "as a cost allocation tag in Billing. Nobody owns that spend until it is tagged: "
                     f"find the resources with spendlens tags -k mandatory.")),
    ]


def anomalies(cfg: Config, cur: str = "USD") -> Guide:
    a = cfg.anomaly
    return [
        ("What it shows", (f"Days in the last {a.detect_days} complete days when a {a.group_by}'s spend jumped well "
                          "above its normal level; the bar is the extra spend that day.")),
        ("How a day is judged", (f"Its cost is compared with the median of the previous {a.lookback_days} days. It is "
                                f"an anomaly when it is at least {a.threshold:g} robust standard deviations above "
                                f"(anomaly.threshold) AND at least {ch.money(a.min_increase, cur)} more "
                                "(anomaly.min_increase). Severity: low, medium (z ≥ 4), high (≥ 6), critical (≥ 10).")),
        ("What to do next", ("Check what was deployed or scaled that day; spendlens ask \"what drove the spike on "
                            "<date>?\" explains it from your data.")),
    ]


def forecast(cfg: Config) -> Guide:
    b = cfg.budgets
    budget = ch.money(b.monthly_total) if b.monthly_total else "not set (budgets.monthly_total)"
    return [
        ("What it shows", ("Where this month will end: spend so far plus Cost Explorer's forecast for the remaining "
                          f"days, against your budget ({budget}) and every monthly AWS Budget.")),
        ("Statuses", (f"over budget: already above the limit · forecast over budget: will exceed it · at risk: "
                     f"forecast above {b.warn_pct:g}% of the limit (budgets.warn_pct) · on track: below that.")),
    ]


def tags(cfg: Config) -> Guide:
    t = cfg.tagging
    return [
        ("What it shows", (f"How well resources follow the tagging policy. Mandatory tags: {', '.join(t.mandatory)} "
                          "(tagging.mandatory). Each bar is one check's compliance; the chart counts alarms by "
                          "resource type.")),
        ("Checks", ("mandatory: required tags missing · untagged: no tags at all · prohibited: a forbidden key "
                   "(tagging.prohibited) · limit: too many tags · expected_values: a value outside "
                   "tagging.allowed_values.")),
        ("Why it matters", "Without tags, spend cannot be shown per team or environment (see Showback)."),
    ]


def findings(cfg: Config, feature: str) -> Guide:
    what = ("resources that are oversized or do not follow your company standards (instance families, gp3, "
            "Multi-AZ by environment, versions in paid Extended Support, …)" if feature == "rightsize" else
            "resources you pay for that are idle or orphaned: unattached volumes, old snapshots, idle load "
            "balancers and NAT gateways, unused IPs and secrets, …")
    return [
        ("What it shows", f"Alarms for {what}. The chart is the estimated monthly saving per service."),
        ("Savings", ("Estimates use the unit prices in finops.yaml (pricing.*; approximate us-east-1 list prices by "
                    "default). Use them to rank work, then review each item before acting.")),
        ("Thresholds", ("Every check states the setting it used in the full report "
                       f"(spendlens {feature} --status all -f html); change them in finops.yaml (sizing.*).")),
    ]


def savings() -> Guide:
    return [
        ("What it shows", ("One monthly saving across spendlens rightsizing and waste, AWS Cost Optimization Hub "
                          "and Savings Plan / RI coverage.")),
        ("No double counting", ("Each resource counts once (the largest action); when AWS and spendlens both "
                               "estimate the same resource, AWS's figure is used; commitments are counted once.")),
        ("Before acting", "These are proposals for a person to review. spendlens never changes resources."),
    ]


def overview() -> Guide:
    return [
        ("Start here", ("The big number is last month's spend and how it changed. The tiles summarise each area; "
                       "open a tab for the details. Every tab has a 'How to read this' box like this one.")),
    ]


def as_items(g: Guide) -> list[dict[str, Any]]:
    return [{"q": q, "a": a} for q, a in g]
