"""Measured per-resource cost for the graph, instead of price-book estimates.

Sources, best first (graph.cost_source = auto tries them in this order; each node records which one it used):

  cur            the Cost and Usage Report, collected and queried with Tailpipe (`tailpipe query`, table
                 aws_cost_and_usage_report): what each resource was actually billed over graph.cur_days.
  cost_explorer  Cost Explorer resource-level data that `spendlens cost resources` already saved to the history
                 (the last window, up to 7 days): no extra AWS calls.
  estimate       the finops.yaml price book (what the graph has without either).

A measured cost becomes a monthly figure from the days the resource was billed in the window (cost / active days x
30.4), so a resource created last week is not under-counted. Resources the bill does not mention keep their estimate.
Nothing here calls AWS: Tailpipe reads the CUR files it has already collected.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from .model import Graph, Node
from .sources import aliases

DAYS_PER_MONTH = 30.4375
CUR_TABLE = "aws_cost_and_usage_report"

Runner = Callable[[str], list[dict[str, Any]]]


class CostSourceError(RuntimeError):
    pass


def cur_sql(days: int) -> str:
    days = int(days)
    if not 1 <= days <= 92:
        raise ValueError("graph.cur_days must be between 1 and 92")
    # full days only: today's lines are still arriving
    return (f"select line_item_resource_id as resource_id, sum(line_item_unblended_cost) as cost, "
            f"min(line_item_usage_start_date) as first_seen, max(line_item_usage_start_date) as last_seen, "
            f"any_value(line_item_currency_code) as currency from {CUR_TABLE} "
            f"where line_item_resource_id is not null and line_item_resource_id <> '' "
            f"and line_item_usage_start_date >= current_date - interval {days} day "
            f"and line_item_usage_start_date < current_date group by line_item_resource_id")


def tailpipe_runner(path: str = "tailpipe", timeout: int = 600) -> Runner:
    exe = shutil.which(path)
    if not exe:
        raise CostSourceError("Tailpipe is not installed (see https://tailpipe.io and its AWS plugin's CUR table)")

    def run(sql: str) -> list[dict[str, Any]]:
        try:
            p = subprocess.run([exe, "query", sql, "--output", "json"], capture_output=True, text=True,
                               timeout=timeout, check=False)
        except subprocess.TimeoutExpired as e:
            raise CostSourceError(f"tailpipe query timed out after {timeout}s") from e
        if p.returncode != 0:
            raise CostSourceError((p.stderr or p.stdout).strip().splitlines()[-1][:300] if (p.stderr or p.stdout)
                                  else f"tailpipe exited {p.returncode}")
        return parse_json(p.stdout)

    return run


def parse_json(text: str) -> list[dict[str, Any]]:
    """Tailpipe/Steampipe JSON output: {"columns": [...], "rows": [{...}]} or a plain list of rows."""
    text = text.strip()
    if not text:
        return []
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise CostSourceError(f"could not read tailpipe output: {e}") from e
    rows = doc.get("rows", []) if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise CostSourceError("unexpected tailpipe output")
    return [r for r in rows if isinstance(r, dict)]


def _day(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v).replace(" ", "T")[:19]).date()
    except ValueError:
        return None


def active_days(first: Any, last: Any, window_days: int) -> int:
    a, b = _day(first), _day(last)
    active = (b - a).days + 1 if a and b else window_days
    return max(1, min(active, window_days))


def monthly(cost: float, first: Any, last: Any, window_days: int) -> float:
    return round(cost / active_days(first, last, window_days) * DAYS_PER_MONTH, 2)


def from_cur(run: Runner, days: int) -> dict[str, dict[str, Any]]:
    """resource id -> {monthly, cost, days, currency} from the CUR."""
    out = {}
    for r in run(cur_sql(days)):
        rid = str(r.get("resource_id") or "")
        try:
            cost = float(r.get("cost") or 0)
        except (TypeError, ValueError):
            continue
        if rid:
            out[rid] = {"monthly": monthly(cost, r.get("first_seen"), r.get("last_seen"), days),
                        "cost": round(cost, 2), "window_days": active_days(r.get("first_seen"), r.get("last_seen"), days),
                        "currency": r.get("currency") or "USD"}
    return out


def from_cost_explorer(store: Any, *, max_age_days: int = 14, today: date | None = None) -> tuple[dict[str, Any], str]:
    """The last window of the latest saved `cost resources` run (resource ARN -> monthly), and its label."""
    run = store.latest_run("cost_resources")
    if run is None:
        return {}, ""
    when = _day(run.started_at)
    if when and ((today or date.today()) - when).days > max_age_days:
        return {}, ""
    periods = run.meta.get("periods") or []
    if not periods:
        return {}, ""
    last = periods[-1]
    start, end = _day(last.get("start")), _day(last.get("end"))
    days = (end - start).days if start and end else 7
    out: dict[str, dict[str, Any]] = {}
    for r in store.cost_rows(run.id, f"p{len(periods) - 1}"):
        if r["dimension"] == "resource" and r["grp"] and not r["grp"].startswith("("):
            cost = float(r["amount"] or 0)
            out[r["grp"]] = {"monthly": round(cost / max(days, 1) * DAYS_PER_MONTH, 2), "cost": round(cost, 2),
                             "window_days": days, "currency": r.get("currency") or "USD"}
    return out, last.get("label") or ""


def _keys(n: Node) -> list[str]:
    return list(dict.fromkeys(aliases(n)))


def apply(g: Graph, costs: dict[str, dict[str, Any]], basis: str, label: str) -> int:
    """Replace estimates with measured costs where the bill names the resource. Returns how many matched."""
    if not costs:
        return 0
    index = dict(costs)
    for k, v in costs.items():  # match by AWS id too: CUR uses i-..., vol-...; Cost Explorer uses ARNs
        short = k.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if k.startswith("arn:") else k
        index.setdefault(short, v)
    matched = 0
    for n in g.nodes.values():
        hit = next((index[k] for k in _keys(n) if k in index), None)
        if hit is None:
            continue
        n.attrs["estimate"] = n.monthly_cost
        n.monthly_cost = hit["monthly"]
        n.cost_basis = basis
        n.attrs["cost_note"] = (f"{label}: {hit['cost']:.2f} {hit['currency']} billed over {hit['window_days']} "
                                "day(s), as a monthly rate")
        matched += 1
    return matched


def price(g: Graph, cfg: Any, *, store: Any = None, runner: Runner | None = None) -> dict[str, Any]:
    """Apply the best available measured costs. Returns what was used (for the run's meta and the user)."""
    mode = cfg.graph.cost_source
    info: dict[str, Any] = {"source": "estimate", "matched": 0}
    if mode == "estimate":
        return info
    notes = []
    from ..tailpipe import configured

    if mode == "auto" and not cfg.tailpipe.enabled:
        notes.append("Tailpipe is turned off (tailpipe.enabled: false): the CUR was not read")
    elif mode == "auto" and runner is None and not configured().get("cur"):
        pass  # no CUR configured for Tailpipe (spendlens tailpipe init): use Cost Explorer data or estimates
    elif mode in ("auto", "cur"):
        try:
            run = runner or tailpipe_runner(cfg.graph.tailpipe_path)
            costs = from_cur(run, cfg.graph.cur_days)
            n = apply(g, costs, "cur", f"CUR, last {cfg.graph.cur_days} days")
            if n:
                return {"source": "cur", "matched": n, "billed_resources": len(costs), "days": cfg.graph.cur_days}
            notes.append("the CUR in Tailpipe has no line items for these resources in the window")
        except (CostSourceError, ValueError) as e:
            notes.append(f"CUR via Tailpipe unavailable: {e}")
        if mode == "cur":
            return {**info, "notes": notes}
    if store is not None:
        costs, label = from_cost_explorer(store)
        n = apply(g, costs, "cost_explorer", f"Cost Explorer resource data, {label}")
        if n:
            return {"source": "cost_explorer", "matched": n, "window": label, "notes": notes}
        notes.append("no recent `spendlens cost resources` run to take resource costs from")
    return {**info, "notes": notes}
