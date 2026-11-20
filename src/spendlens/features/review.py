"""`spendlens review`: the full FinOps review on one page, for people who decide.

Everything `brief` has (the FinOps AI analyst's summary, insights, next steps and risks) plus every detail from the
dashboard, explained with charts made for it:
  * spend: monthly spend vs budget, a bridge from last month to this month, the trend of the top services, where the
    money goes (service -> workload flow, when `spendlens cost breakdown` has run), spend by owner, the full table;
  * anomalies: usual vs actual for each spike, with the AI's likely cause;
  * savings: by source and service, a source -> service flow, an effort x value matrix, the kind of change each
    proposal is (configuration change, removal, commitment);
  * cleanup groups: connected resources that can be removed together (spendlens graph), drawn as small graphs;
  * a review queue: approve, defer or reject every proposal with a note, then export the decisions as CSV;
  * budgets, tag governance, and the data and method behind each figure.

spendlens computes every number from saved runs; the AI only writes the words (or a rule-based summary does, with
--no-ai). Nothing on the page can change AWS: decisions stay in the browser until you export them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from ..common import ConfigOpt, ProfileOpt, SetOpt, Source, err, history_path, load
from ..config import Config
from ..engines import cost as ce
from ..history import HistoryStore, demo_db_path
from ..plugins import Registry
from ..render import charts as ch
from ..render.html import render_template
from .brief import Analysis, _anomaly_rows, _effort, _human, _short, ai_analysis, facts, page, rules
from .dashboard import FEATURES, Data, merged_savings, savings_category, view_model

BANDS = [("< $50", 0, 50), ("$50–250", 50, 250), ("$250–1K", 250, 1000), ("≥ $1K", 1000, float("inf"))]
EFFORTS = ("low", "medium", "high")
KINDS = {
    "change": ("Configuration change", "Resize, change type or storage class. Reversible: change it back."),
    "removal": ("Removal", "Delete or release something unused. Hard to undo: back up or snapshot first."),
    "commitment": ("Commitment", "Buy Savings Plans or reservations. A 1 or 3 year financial commitment."),
}
OWNER_KEYS = ("Owner", "owner", "Team", "team")
SERVICE_NAMES = {"ebs": "EBS", "ec2": "EC2", "rds": "RDS", "s3": "S3", "vpc": "VPC", "elb": "Load balancing",
                 "elbv2": "Load balancing", "lambda": "Lambda", "savingsplans": "Savings Plans",
                 "secretsmanager": "Secrets Manager", "dynamodb": "DynamoDB", "elasticache": "ElastiCache",
                 "redshift": "Redshift", "eks": "EKS", "ecs": "ECS", "cloudwatch": "CloudWatch", "efs": "EFS",
                 "opensearch": "OpenSearch", "kinesis": "Kinesis", "commitments": "Commitments"}
WATERFALL_STEPS = 8
REVIEW_FEATURES = (*FEATURES, "graph")  # the review also shows cleanup groups from the resource graph
GROUP_SERVICE = {"stopped_instance": "EC2", "idle_load_balancer": "Load balancing", "unused_ami": "EC2",
                 "orphan_snapshots": "EBS", "unattached_volume": "EBS", "unassociated_eip": "VPC",
                 "rds_orphan_snapshots": "RDS"}
GROUPS_SHOWN = 8


def _kind(control: str | None, reason: str | None) -> str:
    c = f"{control or ''} {(reason or '')[:60]}".lower()
    if re.search(r"purchase|savings ?plan|reserv|commitment", c):
        return "commitment"
    if re.search(r"unattached|unused|delete|snapshot|orphan|eip|secret|idle|coh_stop|retention", c):
        return "removal"
    return "change"


def service_name(code: str) -> str:
    """'savingsplans' -> 'Savings Plans'; names that are already readable are kept."""
    return SERVICE_NAMES.get(code.lower(), code) if code and code == code.lower() else (code or "-")


def _band(v: float) -> str:
    return next(name for name, lo, hi in BANDS if lo <= v < hi)


def _region(resource: str) -> str:
    p = resource.split(":")
    return p[3] if resource.startswith("arn:") and len(p) > 3 and p[3] else ""


def _owners(d: Data, cfg: Config) -> dict[str, str]:
    """resource -> owner, from the resource's tags in any saved check (the first showback tag key, then Owner/Team)."""
    keys = [*cfg.cost.showback_tag_keys[:1], *OWNER_KEYS]
    out: dict[str, str] = {}
    for fs in d.findings.values():
        for f in fs:
            if f.tags and f.resource not in out and (v := next((f.tags[k] for k in keys if f.tags.get(k)), None)):
                out[f.resource] = v
    return out


# ------------------------------------------------------------------ sections


def bridge(vm: dict[str, Any], cur: str) -> dict[str, Any] | None:
    """Last period's total -> each big change -> this period's total."""
    cd = vm.get("cost_diff")
    if not cd:
        return None
    rows = sorted((r for r in cd["rows"] if r["change"]), key=lambda r: -abs(r["change"]))
    top, rest = rows[:WATERFALL_STEPS], rows[WATERFALL_STEPS:]
    steps = [{"label": r["group"], "value": r["change"],
              "tip": f"{r['group']}: {ch.money(r['base'], cur)} → {ch.money(r['compare'], cur)} "
                     f"({ch.signed_money(r['change'], cur)})"} for r in top]
    if rest and (other := round(sum(r["change"] for r in rest), 2)):
        steps.append({"label": f"{len(rest)} other {cd['group_by']}s", "value": other})
    t = cd["total"]
    return {"start": {"label": f"Total {cd['p0']}", "value": t["base"]}, "steps": steps,
            "end": {"label": f"Total {cd['p1']}", "value": t["compare"]}, "p0": cd["p0"], "p1": cd["p1"]}


def breakdown(store: HistoryStore, cur: str) -> dict[str, Any] | None:
    """Service -> workload flow and the biggest movers, from the latest `spendlens cost breakdown` run."""
    run = store.latest_run("cost_breakdown")
    if run is None:
        return None
    rows = store.cost_rows(run.id)
    periods = [ce.Period.from_dict(p) for p in run.meta.get("periods", [])]
    if not rows or not periods:
        return None
    by: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if "\x1f" in (r["grp"] or ""):
            svc, sub = r["grp"].split("\x1f", 1)
            by[r["kind"]][(svc, sub)] += r["amount"]
    last = f"p{len(periods) - 1}"
    dim = run.meta.get("breakdown", "")
    sub_name = dim.removeprefix("tag:") if dim.startswith("tag:") else dim.replace("_", " ") or "item"
    links = [{"source": s, "target": t, "value": round(v, 2)} for (s, t), v in by[last].items() if v > 0]
    movers = []
    if len(periods) > 1:
        keys = set(by["p0"]) | set(by[last])
        for k in keys:
            a, b = by["p0"].get(k, 0.0), by[last].get(k, 0.0)
            if abs(b - a) >= 0.01:
                movers.append({"service": k[0], "item": k[1], "before": ch.money(a, cur), "after": ch.money(b, cur),
                               "change": round(b - a, 2), "change_s": ch.signed_money(b - a, cur),
                               "pct": ch.pct((b - a) / a * 100, True) if a else "new"})
        movers.sort(key=lambda m: -abs(m["change"]))
    return {"links": links, "sub": sub_name, "period": periods[-1].label, "p0": periods[0].label,
            "movers": movers[:12], "run": run.id}


def cleanup(store: HistoryStore, d: Data, cfg: Config) -> dict[str, Any] | None:
    """Cleanup groups from the latest saved resource graph (spendlens graph orphans), linked to the waste alarms."""
    from ..graph.analyze import cleanup_groups
    from ..graph.model import Graph
    from .graph_cmd import Loaded, owner_keys

    run = store.latest_run("graph")
    if run is None:
        return None
    g = Graph.from_rows(*store.graph(run.id))
    ld = Loaded(g, run.meta, run.id, run.started_at, run.account_id, d.findings.get("waste", []), Source.last)
    groups = cleanup_groups(g, cfg.graph, now=ld.now, collected=ld.collected, findings=ld.waste,
                            owner_keys=owner_keys(cfg))
    return {"graph": g, "groups": groups, "alarms": ld.alarms, "run": run.id,
            "date": run.started_at[:16].replace("T", " ")}


def _group_rows(groups: list[Any], cur: str) -> list[dict[str, Any]]:
    out = []
    for grp in groups:
        amt = round(max(grp.monthly_saving, grp.findings_saving), 2)
        effort = "medium" if grp.kind in ("stopped_instance", "unused_ami", "idle_load_balancer") else "low"
        out.append({
            "id": f"group|{grp.id}", "what": f"Clean up: {grp.title}", "resource": _short(grp.anchor),
            "full": grp.anchor, "service": GROUP_SERVICE.get(grp.kind, "EC2"), "region": grp.region or "",
            "owner": grp.owner, "reason": " ".join(grp.evidence), "control": f"graph:{grp.kind}", "amount": amt,
            "monthly": ch.money(amt, cur, compact=False), "yearly": ch.money(amt * 12, cur), "effort": effort,
            "kind": "removal", "kind_label": KINDS["removal"][0], "band": _band(amt), "category": "Cleanup groups",
            "source": "spendlens graph",
            "also": f"{len(grp.findings)} waste alarm(s) included" if grp.findings else "",
        })
    return out


def review_rows(d: Data, cfg: Config, groups: list[Any] | None = None) -> list[dict[str, Any]]:
    """Every proposal to decide. A cleanup group replaces the waste alarms about its own resources, so a saving is
    never counted twice."""
    cur = cfg.pricing.currency
    m = merged_savings(d)
    covered = {(f["control"], f["resource"]) for grp in groups or [] for f in grp.findings}
    if m is None and not groups:
        return []
    owners = _owners(d, cfg)
    out = []
    for r in sorted((m or ([], []))[0], key=lambda r: -(r["est_monthly_saving"] or 0)):
        if (r["control"], r["resource"]) in covered:
            continue
        amt = r["est_monthly_saving"] or 0
        kind = _kind(r["control"], r.get("reason"))
        out.append({
            "id": f"{r['control']}|{r['resource']}", "what": _human(r["control"], r.get("reason")),
            "resource": _short(r["resource"]), "full": r["resource"], "service": service_name(r["service"]),
            "region": _region(r["resource"]), "owner": owners.get(r["resource"], ""),
            "reason": r.get("reason") or "", "control": r["control"], "amount": round(amt, 2),
            "monthly": ch.money(amt, cur, compact=False), "yearly": ch.money(amt * 12, cur),
            "effort": _effort(r["control"]), "kind": kind,
            "kind_label": KINDS[kind][0], "band": _band(amt), "category": savings_category(r),
            "source": "AWS Cost Optimization Hub" if r["source"] == "cost_optimization_hub" else "spendlens",
            "also": r.get("also_flagged_by") or "",
        })
    out += _group_rows(groups or [], cur)
    out.sort(key=lambda r: -r["amount"])
    return out


def savings_views(rows: list[dict[str, Any]], cur: str) -> dict[str, Any]:
    cells: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"count": 0, "total": 0.0})
    kinds: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "total": 0.0})
    for r in rows:
        c = cells[(r["effort"], r["band"])]
        c["count"] += 1
        c["total"] += r["amount"]
        kinds[r["kind"]]["count"] += 1
        kinds[r["kind"]]["total"] += r["amount"]
    quick = [r for r in rows if r["effort"] == "low"]
    return {
        "flow": [{"source": r["category"], "target": r["service"], "value": r["amount"]} for r in rows],
        "cells": dict(cells), "efforts": [e for e in EFFORTS if any(k[0] == e for k in cells)],
        "bands": [b[0] for b in BANDS],
        "kinds": [{"key": k, "label": KINDS[k][0], "explain": KINDS[k][1], "count": int(v["count"]),
                   "total": ch.money(v["total"], cur)} for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]["total"])],
        "quick_total": ch.money(sum(r["amount"] for r in quick), cur), "quick_count": len(quick),
        "services": sorted({r["service"] for r in rows}),
    }


def review_page(d: Data, cfg: Config, source: Source, analysis: Analysis, fx: dict[str, Any],
                extra: dict[str, Any]) -> dict[str, Any]:
    cur = cfg.pricing.currency
    vm = page(d, cfg, source, analysis, fx)  # everything the brief shows
    full = view_model(d, cfg, source)
    cl = extra.get("cleanup")
    groups = cl["groups"] if cl else []
    rows = review_rows(d, cfg, groups)
    notes = {(n.date, n.group): n.explanation for n in analysis.anomaly_notes}
    spikes = [r for r in _anomaly_rows(d) if r.get("direction", "spike") == "spike"]
    vm.update({
        "bridge": bridge(full, cur), "breakdown": extra.get("breakdown"), "showback_all": full["showback"],
        "cost_table": (full["cost_diff"] or {}).get("table"), "trend_table": (full["trend"] or {}).get("table"),
        "dumbbell": [{"label": f"{r['date']} · {r['group']}", "base": r.get("baseline") or 0, "value": r.get("cost") or 0,
                      "severity": r.get("severity"),
                      "tip": f"{r['group']} on {r['date']}: {ch.money(r.get('cost'), cur)} against a usual "
                             f"{ch.money(r.get('baseline'), cur)} (z={r.get('z')}, {r.get('severity')})"}
                     for r in spikes[:10]],
        "anomaly_rows": [{**r, "cost_s": ch.money(r.get("cost"), cur), "baseline_s": ch.money(r.get("baseline"), cur),
                          "delta_s": ch.signed_money(r.get("delta") or 0, cur),
                          "pct_s": ch.pct(r.get("delta_pct"), True) if r.get("delta_pct") is not None else "",
                          "note": notes.get((str(r.get("date")), str(r.get("group"))), "")} for r in _anomaly_rows(d)],
        "review": rows, "sv": savings_views(rows, cur) if rows else None,
        "by_service_named": [{**b, "label": service_name(b["label"]),
                              "tip": f"{service_name(b['label'])}: {ch.money(b['value'], cur)}/month"}
                             for b in vm["by_service"]],
        "savings_notes": (merged_savings(d) or ([], []))[1],
        "tags_by_type": (full["tags"] or {}).get("by_type", []),
        "metric": cfg.cost.metric.replace("_", " "),
        "skipped": [s.replace("_", " ") for s in extra.get("skipped", [])],
        "skip_tags": "tags" in extra.get("skipped", []),
        "review_key": d.account or ("demo" if source is Source.demo else "local"),
        "findings": {f: (full[f] or {}).get("hint") for f in ("rightsize", "waste")},
        "cleanup": cleanup_view(cl, cur) if cl else None,
        "skip_graph": "graph" in extra.get("skipped", []),
    })
    return vm


def cleanup_view(cl: dict[str, Any], cur: str) -> dict[str, Any]:
    from .graph_cmd import group_view

    groups = cl["groups"]
    total = round(sum(g.monthly_saving for g in groups), 2)
    new = round(sum(max(g.monthly_saving - g.findings_saving, 0) for g in groups), 2)
    return {"groups": [group_view(g, cl["graph"], cur, alarms=cl["alarms"]) for g in groups[:GROUPS_SHOWN]],
            "count": len(groups), "more": max(len(groups) - GROUPS_SHOWN, 0), "total": ch.money(total, cur),
            "new": ch.money(new, cur), "new_amount": new, "members": sum(len(g.members) for g in groups),
            "run": cl["run"], "date": cl["date"]}


def build(d: Data, cfg: Config, source: Source, analysis: Analysis, fx: dict[str, Any], extra: dict[str, Any]) -> str:
    vm = review_page(d, cfg, source, analysis, fx, extra)
    demo = " · DEMO DATA" if source is Source.demo else ""
    gen = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    by = f"AI analysis by {analysis.model}" if analysis.by == "ai" else "rule-based summary"
    footer = (f"Generated by spendlens {gen} from saved runs ({source.value}){demo} · {by}. Every figure is computed "
              "from your data; savings are estimates for human review. Decisions are kept in this browser until "
              "you export them; spendlens never changes resources.")
    payload = {"review": [{k: r[k] for k in ("id", "what", "full", "service", "region", "owner", "amount", "effort",
                                              "kind_label", "source", "reason", "control")} for r in vm["review"]],
               "key": vm["review_key"], "currency": cfg.pricing.currency, "generated": gen,
               "facts": fx, "analysis": analysis.model_dump(mode="json")}
    return render_template(
        "review.html.j2", title="FinOps review", footer=footer,
        subtitle=" · ".join([cfg.dashboard.title, f"AWS account {d.account}" if d.account else "AWS", gen]) + demo,
        vm=vm, view_model=payload,
    )


# ------------------------------------------------------------------ command


def review(
    out: Annotated[Path, typer.Option("--out", "-o", help="Output HTML file.")] = Path("spendlens-review.html"),
    ai: Annotated[bool, typer.Option("--ai/--no-ai", help="Write the analysis with your AI provider (llm.* in "
                                                          "finops.yaml), or use the built-in rule-based summary.")] = True,
    source: Annotated[Source, typer.Option(
        "--source", help="live = reuse today's saved runs and run every other section now; last = saved runs only, "
                         "no AWS calls; demo = bundled sample DB.")] = Source.live,
    refresh: Annotated[bool, typer.Option("--refresh", help="Re-run every section live now, even ones run today.")] = False,
    max_steps: Annotated[int | None, typer.Option("--max-steps", help="Tool calls the AI may make (default 6).")] = None,
    fresh: Annotated[bool, typer.Option("--fresh", help="Write a new AI analysis even if one exists for this data.")] = False,
    skip: Annotated[list[str] | None, typer.Option(
        "--skip", help=f"Leave a check out: not run, not sent to the AI, not shown (repeatable). One of: {', '.join(REVIEW_FEATURES)}.")] = None,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
) -> None:
    """The full FinOps review on one page: AWS spend, anomalies and savings for human review, explained in detail.

    The FinOps AI analyst's summary, then spend (bridge, trend, flow by workload, owners), anomalies (usual vs
    actual, likely cause), savings (by source and service, effort x value, kind of change) and a review queue:
    approve, defer or reject each proposal with a note and export the decisions as CSV. spendlens computes every
    figure; the AI only writes the words (--no-ai: a rule-based summary, nothing sent to an AI provider).
    Examples:
      spendlens review -o review.html
      spendlens review --source demo -o review.html
      spendlens review --source last --no-ai -o review.html
      spendlens review --refresh -o review.html
      spendlens review --skip tags -o review.html     # no tagging check
    """
    skipped = sorted(set(skip or []))
    if bad := [s for s in skipped if s not in REVIEW_FEATURES]:
        raise typer.BadParameter(f"Unknown check(s): {', '.join(bad)}. Checks: {', '.join(REVIEW_FEATURES)}",
                                 param_hint="--skip")
    features = tuple(f for f in REVIEW_FEATURES if f not in skipped)
    cfg = load(config, profile, sets)
    path = demo_db_path() if source is Source.demo else history_path(cfg)
    if source is Source.live:
        from .. import refresh as rf

        rf.ensure_fresh(path, features, config=config, profile=profile, refresh=refresh)
    elif refresh:
        raise typer.BadParameter("--refresh runs sections live: drop --source last/demo.", param_hint="--refresh")
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/] (run some checks first, or try --source demo)")
        raise typer.Exit(1) from e
    cur = cfg.pricing.currency
    with store:
        data = Data(store)
        for s in skipped:  # left out on request: not in the facts, the AI analysis or the page
            for part in (data.runs, data.findings, data.cost):
                part.pop(s, None)
        extra = {"breakdown": breakdown(store, cur), "skipped": skipped,
                 "cleanup": None if "graph" in skipped else cleanup(store, data, cfg)}
    shown = Source.last if source is Source.live else source
    fx = facts(data, view_model(data, cfg, shown), cfg)
    analysis = rules(fx)
    if ai and data.runs:
        try:
            analysis = ai_analysis(cfg, path, fx, max_steps, fresh=fresh)
        except Exception as e:  # noqa: BLE001 - the review must still be written
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            err.print(f"[yellow]AI analysis unavailable ({msg}); using the rule-based summary.[/]")
            analysis.notes.append(f"AI analysis unavailable ({msg}); this is the rule-based summary.")
    html = build(data, cfg, shown, analysis, fx, extra)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    by = f"AI analysis by {analysis.model}" if analysis.by == "ai" else "rule-based summary"
    groups = (extra["cleanup"] or {}).get("groups") or []
    err.print(f"[green]Wrote[/] {out} ({len(html) // 1024} KiB · {by} · {len(review_rows(data, cfg, groups))} proposals "
              f"to review{f', {len(groups)} cleanup groups' if groups else ''})")


def register(reg: Registry) -> None:
    reg.add_command(review, "review")
