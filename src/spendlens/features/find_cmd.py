"""`spendlens find <text>`: everything spendlens has saved about a resource, offline.

Searches the latest saved runs for a resource id, ARN, Name or tag value: the resource graph (type, state, cost and
its basis, links, cleanup group, suggested owner tags), the findings of every check (tags, rightsize, waste, savings
hub) and Cost Explorer resource-level cost (cost resources). No AWS calls; --source demo searches the sample data.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Annotated, Any

import typer
from rich.table import Table

from ..common import BRAND, ConfigOpt, SetOpt, Source, console, err, history_path, load
from ..config import Config
from ..plugins import Registry

FEATURES = ("tags", "rightsize", "waste", "savings_hub")
BASIS_ONE = {"cur": "billed, from the CUR", "cost_explorer": "billed, from Cost Explorer", "estimate": "price-book estimate",
             "none": "no price"}
MAX = 25


def _short(r: str) -> str:
    return r.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if r.startswith("arn:") else r


def search(text: str, source: Source, cfg: Config) -> dict[str, dict[str, Any]]:
    """resource key (ARN where known) -> what was found about it."""
    from ..graph.model import type_label
    from ..history import HistoryStore, demo_db_path
    from .graph_cmd import groups_for, saved_graph, suggestions_for

    q = text.strip().lower()
    hits: dict[str, dict[str, Any]] = defaultdict(lambda: {"findings": [], "cost_explorer": []})
    path = demo_db_path() if source is Source.demo else history_path(cfg)
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/] (run some checks first, or try --source demo)")
        raise typer.Exit(1) from e
    with store:
        for feat in FEATURES:
            run = store.latest_run(feat)
            if run is None:
                continue
            for f in store.findings(run.id):
                tagv = " ".join(str(v) for v in (f.tags or {}).values()).lower()
                if q in f.resource.lower() or q == tagv or q in tagv.split():
                    hits[f.resource]["findings"].append(
                        {"check": feat, "control": f.control, "status": f.status.value, "reason": f.reason,
                         "est_monthly_saving": f.est_monthly_saving, "run": run.id})
        run = store.latest_run("cost_resources")
        if run is not None:
            for r in store.cost_rows(run.id):
                if r["dimension"] == "resource" and q in (r["grp"] or "").lower():
                    hits[r["grp"]]["cost_explorer"].append({"period": r["period"], "amount": r["amount"]})
    ld = saved_graph(source, cfg)
    if ld is not None:
        g = ld.g
        nodes = g.find(text)
        nodes += [n for n in g.nodes.values() if n not in nodes and any(str(v).lower() == q for v in n.tags.values())]
        groups = {m: grp for grp in groups_for(ld, cfg) for m in grp.members}
        sugg = defaultdict(list)
        for s in suggestions_for(g, cfg):
            sugg[s.resource].append(f"{s.key}={s.value} ({s.confidence}: {s.reason})")
        for n in nodes[: MAX * 2]:
            key = next((k for k in hits if _short(k) == n.short_id), n.id)
            grp = groups.get(n.id)
            hits[key]["graph"] = {
                "id": n.id, "type": type_label(n.type), "name": n.label, "state": n.state, "region": n.region,
                "monthly_cost": n.monthly_cost, "cost_basis": BASIS_ONE.get(n.cost_basis, n.cost_basis),
                "links": [f"{e.label} → {g.nodes[o].label}" if e.from_id == n.id else f"{g.nodes[o].label} {e.label} it"
                          for e, o in g.neighbours(n.id)][:12],
                "cleanup_group": {"title": grp.title, "monthly_saving": grp.monthly_saving} if grp else None,
                "suggested_tags": sugg.get(n.id, []), "tags": n.tags}
    # findings use ARNs, the graph knows the short id too: fold findings found by short id into the graph's entry
    for key in [k for k in hits if "graph" not in hits[k]]:
        twin = next((k for k in hits if k != key and "graph" in hits[k] and _short(k) == _short(key)), None)
        if twin:
            hits[twin]["findings"] += hits[key]["findings"]
            hits[twin]["cost_explorer"] += hits[key]["cost_explorer"]
            del hits[key]
    return dict(hits)


def find(
    text: Annotated[str, typer.Argument(help="A resource id (i-..., vol-...), ARN, Name, or tag value (e.g. a team).")],
    source: Annotated[Source, typer.Option("--source", help="last = your saved runs (default); demo = sample data.")] = Source.last,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """Everything spendlens has saved about a resource: findings, cost, links, cleanup group, suggested owner.

    Offline: searches the latest saved runs, never AWS.
    Examples:
      spendlens find vol-0abc123
      spendlens find payments-api
      spendlens find payments          # every resource tagged payments
      spendlens find legacy --source demo
    """
    if source is Source.live:
        raise typer.BadParameter("find searches saved runs: use --source last (default) or demo.", param_hint="--source")
    cfg = load(config, None, sets)
    cur = cfg.pricing.currency
    hits = search(text, source, cfg)
    if json_out:
        typer.echo(json.dumps(hits, indent=2, default=str))
        return
    if not hits:
        console.print(f"Nothing saved matches {text!r}. [dim]Ids, ARNs, Name tags and tag values are searched in the "
                      "latest runs of tags, rightsize, waste, savings hub, cost resources and the resource graph.[/]")
        raise typer.Exit(1)
    if len(hits) > 1:
        t = Table(title=f"{len(hits)} resources match {text!r}", header_style=f"bold {BRAND}")
        for c in ("resource", "type", "state", "cost/month", "alarms", "cleanup group"):
            t.add_column(c, overflow="fold")
        for key, h in sorted(hits.items(), key=lambda kv: -((kv[1].get("graph") or {}).get("monthly_cost") or 0))[:MAX]:
            gr = h.get("graph") or {}
            alarms = [f["control"] for f in h["findings"] if f["status"] == "alarm"]
            t.add_row(gr.get("name") or _short(key), gr.get("type", ""), gr.get("state") or "",
                      f"{cur} {gr['monthly_cost']:,.2f}" if gr.get("monthly_cost") else "",
                      f"{len(alarms)}: {', '.join(alarms[:2])}" if alarms else "",
                      (gr.get("cleanup_group") or {}).get("title", ""))
        console.print(t)
        if len(hits) > MAX:
            err.print(f"[dim]Showing {MAX} of {len(hits)}. Narrow the search.[/]")
        err.print("[dim]One resource in detail: spendlens find <id>[/]")
        return
    key, h = next(iter(hits.items()))
    gr = h.get("graph")
    console.print(f"[bold {BRAND}]{(gr or {}).get('name') or _short(key)}[/] [dim]{key}[/]")
    if gr:
        cost = f"{cur} {gr['monthly_cost']:,.2f}/month ({gr['cost_basis']})" if gr.get("monthly_cost") is not None else ""
        console.print(f"{gr['type']} · {gr.get('state') or ''} · {gr.get('region') or ''} · {cost}")
        for link in gr["links"]:
            console.print(f"  · {link}")
        if gr["cleanup_group"]:
            console.print(f"[yellow]Cleanup group:[/] {gr['cleanup_group']['title']} · "
                          f"{cur} {gr['cleanup_group']['monthly_saving']:,.2f}/month")
        for s in gr["suggested_tags"]:
            console.print(f"[cyan]Suggested tag:[/] {s}")
    for f in h["findings"]:
        style = {"alarm": "red", "ok": "green"}.get(f["status"], "yellow")
        saving = f" · saves {cur} {f['est_monthly_saving']:,.2f}/month" if f.get("est_monthly_saving") else ""
        console.print(f"[{style}]{f['status']}[/] {f['check']} · {f['control']}: {f['reason']}{saving}")
    for c in h["cost_explorer"]:
        console.print(f"Cost Explorer {c['period']}: {cur} {c['amount']:,.2f}")
    if gr:
        demo = " --source demo" if source is Source.demo else ""
        err.print(f"[dim]More: spendlens graph show {_short(gr['id'])}{demo} · spendlens graph impact "
                  f"{_short(gr['id'])}{demo}[/]")


def register(reg: Registry) -> None:
    reg.add_command(find, "find")
