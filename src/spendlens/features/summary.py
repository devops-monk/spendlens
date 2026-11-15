"""Reusable aggregation of Findings (compliance %, counts) for any control-based feature."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from ..models import Finding, Status


def compliance_pct(ok: int, alarm: int) -> float | None:
    total = ok + alarm
    return round(ok / total * 100, 1) if total else None


def summarise(findings: Iterable[Finding], by: tuple[str, ...] = ("check", "resource_type")) -> list[dict[str, Any]]:
    groups: dict[tuple, Counter] = defaultdict(Counter)
    for f in findings:
        groups[tuple(getattr(f, k) or "-" for k in by)][f.status.value] += 1
    rows = []
    for key, c in sorted(groups.items()):
        rows.append({
            **dict(zip(by, key, strict=True)),
            "total": sum(c.values()),
            "ok": c[Status.OK.value],
            "alarm": c[Status.ALARM.value],
            "skip": c[Status.SKIP.value],
            "error": c[Status.ERROR.value],
            "compliance_pct": compliance_pct(c[Status.OK.value], c[Status.ALARM.value]),
        })
    rows.sort(key=lambda r: (-r["alarm"], *(str(r[k]) for k in by)))
    return rows


def headline(findings: list[Finding]) -> str:
    c = Counter(f.status.value for f in findings)
    pct = compliance_pct(c["ok"], c["alarm"])
    resources = len({f.resource for f in findings if f.status is Status.ALARM})
    return (
        f"{c['alarm']} alarm(s) on {resources} resource(s) · ok {c['ok']} · skip {c['skip']} · error {c['error']}"
        + (f" · compliance {pct}%" if pct is not None else "")
    )
