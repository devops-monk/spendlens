"""How old resources are and what they cost: the FinOps take on the Insights age reports, from the saved graph.

Old is not wrong, but old and costly is worth a look: a snapshot from two years ago, an instance launched in 2023
that nobody has touched. Ages come from each resource's own date (creation, launch or snapshot time). Two types have
no creation date in the API: Lambda functions (age since their last change) and network interfaces (since attached).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from .analyze import _when
from .model import Graph, type_label

BUCKETS = (("under 30 days", 0, 30), ("30-90 days", 30, 90), ("3-6 months", 90, 182), ("6-12 months", 182, 365),
           ("1-2 years", 365, 730), ("2+ years", 730, 10 ** 6))
SINCE = {"lambda_function": "since its last change", "ec2_network_interface": "since attached"}


def bucket(days: int) -> str:
    return next(name for name, lo, hi in BUCKETS if lo <= days < hi)


def age_report(g: Graph, *, now: datetime | None = None, min_days: int = 0, types: list[str] | None = None,
               owner: Any = None, groups: dict[str, str] | None = None) -> dict[str, Any]:
    """owner(node) -> str; groups: resource id -> cleanup group title."""
    now = now or datetime.now(UTC)
    rows, undated = [], 0
    for n in g.nodes.values():
        if types and n.type not in types:
            continue
        when = _when(n.created)
        if when is None:
            undated += 1
            continue
        days = max((now - when).days, 0)
        if days < min_days:
            continue
        rows.append({"id": n.id, "resource": n.short_id, "name": n.label, "type": type_label(n.type), "kind": n.type,
                     "age_days": days, "age": bucket(days), "created": when.date().isoformat(),
                     "since": SINCE.get(n.type, ""), "state": n.state or "", "monthly_cost": n.monthly_cost or 0.0,
                     "cost_basis": n.cost_basis, "owner": owner(n) if owner else "",
                     "cleanup_group": (groups or {}).get(n.id, ""), "region": n.region or ""})
    rows.sort(key=lambda r: (-r["monthly_cost"], -r["age_days"]))
    by_bucket: dict[str, dict[str, float]] = {name: {"count": 0, "cost": 0.0} for name, _, _ in BUCKETS}
    by_type: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "cost": 0.0, "oldest": 0})
    for r in rows:
        by_bucket[r["age"]]["count"] += 1
        by_bucket[r["age"]]["cost"] += r["monthly_cost"]
        t = by_type[r["type"]]
        t["count"] += 1
        t["cost"] += r["monthly_cost"]
        t["oldest"] = max(t["oldest"], r["age_days"])
    return {
        "as_of": now.date().isoformat(), "min_days": min_days, "rows": rows, "undated": undated,
        "monthly_cost": round(sum(r["monthly_cost"] for r in rows), 2),
        "buckets": [{"age": k, "count": int(v["count"]), "monthly_cost": round(v["cost"], 2)}
                    for k, v in by_bucket.items() if v["count"]],
        "by_type": sorted(({"type": k, "count": v["count"], "monthly_cost": round(v["cost"], 2),
                            "oldest_days": v["oldest"]} for k, v in by_type.items()),
                          key=lambda x: -x["monthly_cost"]),
    }
