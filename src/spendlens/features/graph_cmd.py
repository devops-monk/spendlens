"""`spendlens graph`: how resources connect, what they cost together, and what can be cleaned up together.

  graph collect          read the resources and their links from AWS (read-only) and save them to history
  graph show <id|name>   a resource and its neighbours: terminal tree, or -o graph.html/.svg/.mmd/.dot/.json
  graph orphans          cleanup groups: connected resources that can be removed together, with one total saving
  graph impact <id>      if this resource is deleted: what breaks, what is left behind unused, what stays
  graph owners           suggested owner tags for untagged resources, from what they are attached to
  graph traffic          data transfer paths from VPC flow logs (Tailpipe): cross-AZ, NAT, internet, with costs
  graph stats            what was collected, and which sources were skipped (and why)

show, impact, owners and stats read the saved graph (no AWS calls). orphans collects first by default (--source live),
like the other checks; --source last/demo replays a saved run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from ..common import (
    BRAND,
    ConfigOpt,
    FormatOpt,
    OutOpt,
    ProfileOpt,
    SetOpt,
    Source,
    caller_identity,
    ci_meta,
    console,
    emit,
    err,
    history_path,
    load,
)
from ..config import Config
from ..graph.analyze import OWNER_KEYS, Group, cleanup_groups, impact, shared_costs
from ..graph.model import Graph, type_label
from ..graph.render import money
from ..models import Finding, ReportMeta
from ..plugins import Registry
from ..render import Format

graph_app = typer.Typer(help="How resources connect, what they cost together, and what can be cleaned up together.",
                        no_args_is_help=True)

GraphSource = Annotated[Source, typer.Option(
    "--source", help="last = the saved graph (no AWS calls); live = collect it now; demo = bundled sample data.")]


class Loaded:
    def __init__(self, g: Graph, meta: dict[str, Any], run_id: int | None, started_at: str, account: str | None,
                 waste: list[Finding], source: Source):
        self.g, self.meta, self.run_id, self.started_at = g, meta, run_id, started_at
        self.account, self.waste, self.source = account, waste, source

    @property
    def now(self) -> datetime:
        try:
            d = datetime.fromisoformat(self.started_at)
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        except ValueError:
            return datetime.now(UTC)

    @property
    def collected(self) -> set[str] | None:
        counts = self.meta.get("counts")
        return set(counts) if isinstance(counts, dict) else None

    @property
    def alarms(self) -> set[str]:
        out: set[str] = set()
        for f in self.waste:
            if f.status.value == "alarm":
                out.add(f.resource)
        short = {r.rsplit("/", 1)[-1].rsplit(":", 1)[-1] for r in out}
        return {n.id for n in self.g.nodes.values() if n.id in out or n.short_id in short}


NOT_OWNERS = {"environment", "env", "stage", "name"}


def owner_keys(cfg: Config) -> tuple[str, ...]:
    """Tags that name an owner: Owner/Team first, then the showback keys that are not environments."""
    extra = [k for k in cfg.cost.showback_tag_keys if k.lower() not in NOT_OWNERS]
    return tuple(dict.fromkeys([*OWNER_KEYS, *extra]))


def tag_keys(cfg: Config) -> list[str]:
    """Tags worth suggesting: the policy's mandatory tags, then the usual owner tags."""
    return [*cfg.tagging.mandatory, *owner_keys(cfg)]


def suggestions_for(g: Graph, cfg: Config, min_confidence: str = "low") -> list[Any]:
    from ..graph.owners import suggest_owners

    return suggest_owners(g, tag_keys(cfg), min_confidence=min_confidence)


def saved_graph(source: Source, cfg: Config) -> Loaded | None:
    """The latest saved graph (demo DB for --source demo) with the latest waste findings, quietly; None when there
    is none. Never collects: other commands use it to annotate their output without extra AWS calls."""
    from ..history import HistoryStore, demo_db_path

    path = demo_db_path() if source is Source.demo else history_path(cfg)
    try:
        with HistoryStore(path, read_only=True) as store:
            run = store.latest_run("graph")
            if run is None:
                return None
            g = Graph.from_rows(*store.graph(run.id))
            waste = _latest_waste(store)
    except (FileNotFoundError, OSError):
        return None
    return Loaded(g, run.meta, run.id, run.started_at, run.account_id, waste, source)


def saved_suggestions(source: Source, cfg: Config) -> dict[str, list[Any]]:
    """Owner suggestions from the latest saved graph, quietly; {} when there is none."""
    from ..graph.owners import by_resource

    ld = saved_graph(source, cfg)
    return by_resource(suggestions_for(ld.g, cfg)) if ld else {}


def graph_hints(findings: list[Finding], source: Source, cfg: Config) -> list[str]:
    """One or two lines for a check's table output: which of its alarms belong to cleanup groups, and how to see
    what a resource connects to. From the saved graph only; [] when there is none or nothing to say."""
    alarms = [f for f in findings if f.status.value == "alarm"]
    if not alarms:
        return []
    ld = saved_graph(source, cfg)
    if ld is None:
        return []
    cur = cfg.pricing.currency
    src = "demo" if source is Source.demo else "last"
    short = {r.rsplit("/", 1)[-1].rsplit(":", 1)[-1]: r for r in (f.resource for f in alarms)}
    ids = {n.id for n in ld.g.nodes.values() if n.id in {f.resource for f in alarms} or n.short_id in short}
    out = []
    groups = [grp for grp in groups_for(ld, cfg) if ids & set(grp.members)]
    if groups:
        hit = sum(1 for f in alarms if f.resource in {m for grp in groups for m in grp.members}
                  or f.resource.rsplit("/", 1)[-1] in {ld.g.nodes[m].short_id for grp in groups for m in grp.members})
        out.append(f"{hit} of these alarm(s) belong to {len(groups)} cleanup group(s): "
                   f"{money(sum(grp.monthly_saving for grp in groups), cur)} with what is attached to them. "
                   f"See: spendlens graph orphans --source {src}")
    linked = sorted((n for n in (ld.g.nodes[i] for i in ids) if ld.g.neighbours(n.id)),
                    key=lambda n: -(n.monthly_cost or 0))
    if linked:
        out.append(f"What one connects to: spendlens graph show {linked[0].short_id}"
                   + (" --source demo" if source is Source.demo else ""))
    return out


def describe(sugg: list[Any]) -> str:
    """'Team=payments, Environment=prod (from EC2 instance payments-api-1)'."""
    if not sugg:
        return ""
    tags = ", ".join(f"{x.key}={x.value}" for x in sugg)
    return f"{tags} ({sugg[0].confidence}: {sugg[0].reason})"


def _latest_waste(store: Any) -> list[Finding]:
    run = store.latest_run("waste")
    return store.findings(run.id) if run else []


def collect_live(cfg: Config) -> Loaded:
    """Collect the graph now and save it as a `graph` run."""
    from ..graph.collect import collect
    from ..graph.costs import price
    from ..history import HistoryStore
    from ..ui import activity

    with activity("Reading resources and their links [dim](Steampipe, read-only)[/]") as act:
        g, meta = collect(cfg, progress=lambda s: act.update(f"Reading {s} [dim](Steampipe, read-only)[/]"))
    for why in meta["skipped"].values():
        err.print(f"[yellow]Skipped[/] {why}")
    with activity("Pricing resources [dim](CUR via Tailpipe, Cost Explorer data, or the price book)[/]"):
        try:
            with HistoryStore(history_path(cfg), read_only=True) as hist:
                meta["costs"] = price(g, cfg, store=hist)
        except FileNotFoundError:
            meta["costs"] = price(g, cfg)
    c = meta["costs"]
    if c["source"] == "estimate":
        err.print("[dim]Costs: price-book estimates" + (f" ({'; '.join(c.get('notes', []))})" if c.get("notes") else "")
                  + ". For billed costs: Tailpipe with the CUR, or run `spendlens cost resources` first.[/]")
    else:
        err.print(f"[green]Costs:[/] {c['matched']} resources priced from "
                  f"{'the CUR (Tailpipe)' if c['source'] == 'cur' else 'Cost Explorer resource data'}; the rest are "
                  "price-book estimates.")
    ident = caller_identity()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    run_id, waste = None, []
    if cfg.history.enabled:
        try:
            with HistoryStore(history_path(cfg)) as store:
                run_id = store.save_run("graph", account_id=ident.get("Account"), caller_arn=ident.get("Arn"),
                                        params=cfg.graph.model_dump(mode="json"), meta={**ci_meta(), **meta})
                store.save_graph(run_id, *g.to_rows())
                store.prune(cfg.history.keep_runs)
                waste = _latest_waste(store)
                started = store.get_run(run_id).started_at  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001 - history must never break a live run
            err.print(f"[yellow]Warning: could not save the graph to history: {e}[/]")
    return Loaded(g, meta, run_id, started, ident.get("Account"), waste, Source.live)


def load_graph(source: Source, cfg: Config) -> Loaded:
    if source is Source.live:
        return collect_live(cfg)
    from ..history import HistoryStore, demo_db_path

    path = demo_db_path() if source is Source.demo else history_path(cfg)
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/] (run [bold]spendlens graph collect[/] first, or try --source demo)")
        raise typer.Exit(1) from e
    with store:
        run = store.latest_run("graph")
        if run is None:
            err.print(f"[red]No saved graph in {path}.[/] Run [bold]spendlens graph collect[/] (read-only) or try "
                      "--source demo.")
            raise typer.Exit(1)
        g = Graph.from_rows(*store.graph(run.id))
        waste = _latest_waste(store)
    err.print(f"[dim]Using {source.value} graph: run #{run.id} from {run.started_at}"
              + (f", account {run.account_id}" if run.account_id else "") + f" · {len(g.nodes):,} resources[/]")
    return Loaded(g, run.meta, run.id, run.started_at, run.account_id, waste, source)


def groups_for(ld: Loaded, cfg: Config) -> list[Group]:
    return cleanup_groups(ld.g, cfg.graph, now=ld.now, collected=ld.collected, findings=ld.waste,
                          owner_keys=owner_keys(cfg))


def pick(g: Graph, target: str) -> str:
    found = g.find(target)
    if not found:
        err.print(f"[red]No resource matching {target!r} in the saved graph.[/] Use an ARN, an AWS id (i-..., vol-...) "
                  "or a Name tag. `spendlens graph collect` refreshes it.")
        raise typer.Exit(1)
    if len(found) > 1:
        t = Table(title=f"{len(found)} resources match {target!r}: use one of these ids", header_style=f"bold {BRAND}")
        for c in ("type", "name", "id"):
            t.add_column(c, overflow="fold")
        for n in found[:25]:
            t.add_row(type_label(n.type), n.label, n.id)
        console.print(t)
        raise typer.Exit(1)
    return found[0].id


def _row(n: Any, cur: str) -> dict[str, Any]:
    return {"type": type_label(n.type), "name": n.label, "state": n.state or "", "cost": money(n.monthly_cost, cur),
            "basis": {"cur": "CUR", "cost_explorer": "Cost Explorer", "estimate": "estimate"}.get(n.cost_basis, ""),
            "amount": n.monthly_cost or 0, "region": n.region or "", "id": n.id}


def _page(path: Path, vm: dict[str, Any], title: str, subtitle: str, cfg: Config) -> None:
    from ..render.html import render_template

    gen = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_template(
        "graph.html.j2", title=title, subtitle=f"{subtitle} · {gen}", vm=vm,
        footer=f"Generated by spendlens graph {gen}. Read-only: spendlens never changes or deletes resources. Costs are "
               "estimates from the finops.yaml price book."))
    err.print(f"[green]Wrote[/] {path}")


def group_view(grp: Group, g: Graph, cur: str, *, alarms: set[str], svg: bool = True) -> dict[str, Any]:
    from ..graph.render import svg as draw

    # the group plus its direct neighbours, for context (subnet, the AMI an instance came from ...)
    ids = set(grp.members)
    for m in grp.members:
        ids.update(o for _, o in g.neighbours(m) if g.nodes[o].type not in ("vpc", "vpc_subnet"))
    sub = g.subgraph(ids)
    return {**grp.to_dict(), "saving": money(grp.monthly_saving, cur).removesuffix("/mo"),
            "findings_saving": money(grp.findings_saving, cur).removesuffix("/mo"),
            "rows": [_row(g.nodes[m], cur) for m in grp.members],
            # a drawing only helps when there are links to show
            "svg": draw(sub, focus=grp.anchor, highlight=set(grp.members), alarms=alarms, currency=cur, in_page=True,
                        title=grp.title) if svg and sub.edges else ""}


# ------------------------------------------------------------------ commands


@graph_app.command("collect")
def graph_collect(
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: Annotated[Source, typer.Option("--source", help="live (default): collect now. Accepted for symmetry with "
                                                            "the other checks.")] = Source.live,
) -> None:
    """Read the resources and how they link (read-only) and save them to history. About 24 Steampipe queries."""
    cfg = load(config, profile, sets)
    ld = load_graph(source, cfg)
    _print_stats(ld)


@graph_app.command("show")
def graph_show(
    target: Annotated[str, typer.Argument(help="ARN, AWS id (i-..., vol-..., snap-...) or Name tag.")],
    depth: Annotated[int, typer.Option("--depth", "-d", min=1, max=4, help="Links to follow from the resource.")] = 2,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Save as .html (interactive page), .svg, .mmd "
                                                                 "(Mermaid), .dot (Graphviz) or .json.")] = None,
    source: GraphSource = Source.last,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """A resource and what it connects to, with the cost of the whole group.

    Examples:
      spendlens graph show i-0abc123
      spendlens graph show payments-api -d 3 -o payments.html
      spendlens graph show vol-0abc123 -o vol.mmd
    """
    from ..graph import render as rd

    cfg = load(config, None, sets)
    cur = cfg.pricing.currency
    ld = load_graph(source, cfg)
    root = pick(ld.g, target)
    sub = ld.g.around(root, depth)
    if len(sub.nodes) > cfg.graph.max_nodes:
        err.print(f"[yellow]{len(sub.nodes)} resources within {depth} links; drawing the closest "
                  f"{cfg.graph.max_nodes} (graph.max_nodes). Try --depth 1.[/]")
        sub = ld.g.subgraph(list(ld.g.around(root, 1).nodes) + list(sub.nodes)[: cfg.graph.max_nodes])
    groups = [grp for grp in groups_for(ld, cfg) if set(grp.members) & set(sub.nodes)]
    in_groups = {m for grp in groups for m in grp.members}
    alarms = ld.alarms
    n = ld.g.nodes[root]
    shared = [x for x in shared_costs(ld.g) if x["source"] == root or x["target"] == root
              or (x["source"] in sub.nodes and x["target"] in sub.nodes)]
    if out is None:
        console.print(rd.tree(sub, root, currency=cur, alarms=alarms, groups=in_groups, depth=depth))
        console.print(f"[bold]{len(sub.nodes)}[/] resources · [bold]{money(sub.total_cost(), cur)}[/] together "
                      f"(estimate) · {n.label} itself {money(n.monthly_cost, cur) or 'no cost estimate'}")
        for grp in groups:
            console.print(f"[yellow]Cleanup group:[/] {grp.title} · {money(grp.monthly_saving, cur)} "
                          f"[dim](spendlens graph orphans)[/]")
        for x in shared:
            console.print(f"[cyan]Shared cost:[/] {money(x['amount'], cur)} of {x['source_name']} → {x['target_name']} "
                          f"[dim]({x['basis']}; an allocation estimate)[/]")
        return
    suffix = out.suffix.lower()
    out.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".svg":
        out.write_text(rd.svg(sub, focus=root, highlight=in_groups, alarms=alarms, currency=cur, title=f"{n.label} graph"))
    elif suffix in (".mmd", ".mermaid", ".md"):
        text = rd.mermaid(sub, focus=root, currency=cur)
        out.write_text(f"```mermaid\n{text}```\n" if suffix == ".md" else text)
    elif suffix in (".dot", ".gv"):
        out.write_text(rd.dot(sub, focus=root, currency=cur))
    elif suffix == ".json":
        out.write_text(rd.to_json(sub, focus=root, total_monthly_cost=sub.total_cost(),
                                  cleanup_groups=[grp.to_dict() for grp in groups], shared_costs=shared))
    else:
        vm = {"traffic": None, "focus": {"label": n.label, "type": type_label(n.type), "cost": money(n.monthly_cost, cur),
                        "basis": BASIS.get(n.cost_basis, "estimate").replace("price-book estimates", "estimate")},
              "total": money(sub.total_cost(), cur).removesuffix("/mo"), "count": len(sub.nodes), "depth": depth,
              "svg": rd.svg(sub, focus=root, highlight=in_groups, alarms=alarms, currency=cur, in_page=True,
                            title=f"{n.label} and what it connects to"),
              "groups": [group_view(grp, ld.g, cur, alarms=alarms, svg=False) for grp in groups],
              "groups_saving": money(sum(grp.monthly_saving for grp in groups), cur).removesuffix("/mo"),
              "rows": sorted((_row(x, cur) for x in sub.nodes.values()), key=lambda r: -r["amount"]),
              "shared": [{**x, "amount_s": money(x["amount"], cur)} for x in shared],
              "notes": _notes(ld)}
        _page(out, vm, f"{n.label}", f"{type_label(n.type)} · {n.region or 'global'}", cfg)
        return
    err.print(f"[green]Wrote[/] {out} ({len(sub.nodes)} resources)")


@graph_app.command("orphans")
def graph_orphans(
    source: Annotated[Source, typer.Option("--source", help="live = collect the graph now (read-only); last = the "
                                                            "saved graph; demo = bundled sample data.")] = Source.live,
    min_saving: Annotated[float, typer.Option("--min-saving", help="Only groups saving at least this per month.")] = 0,
    top: Annotated[int | None, typer.Option("--top", help="Only the largest N groups.")] = None,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
) -> None:
    """Cleanup groups: connected resources that can be removed together, each with one total monthly saving.

    A stopped instance with its volumes, Elastic IP and snapshots; snapshots of deleted volumes; unused AMIs with
    their snapshots; load balancers with no targets; unattached volumes and Elastic IPs; manual snapshots of deleted
    databases. Each resource is in at most one group. Waste alarms about the same resources are listed, not added.
    Examples:
      spendlens graph orphans
      spendlens graph orphans --source last --min-saving 20
      spendlens graph orphans --source demo -f html -o cleanup.html
      spendlens graph orphans -f csv -o cleanup.csv
    """
    cfg = load(config, profile, sets)
    cur = cfg.pricing.currency
    ld = load_graph(source, cfg)
    groups = [grp for grp in groups_for(ld, cfg) if grp.monthly_saving >= min_saving][: top or None]
    total = round(sum(grp.monthly_saving for grp in groups), 2)
    flagged = round(sum(grp.findings_saving for grp in groups), 2)
    meta = ReportMeta(title="Cleanup groups", account_id=ld.account,
                      extra={"source": ld.source.value, "run_id": ld.run_id, "total_monthly_saving": total,
                             "already_flagged_by_waste": flagged})
    if fmt is Format.html:
        alarms = ld.alarms
        vm = {"traffic": None, "focus": None, "rows": [], "shared": [], "groups": [group_view(grp, ld.g, cur, alarms=alarms) for grp in groups],
              "groups_saving": money(total, cur).removesuffix("/mo"), "members": sum(len(grp.members) for grp in groups),
              "flagged": money(flagged, cur).removesuffix("/mo"), "notes": _notes(ld)}
        _page(out or Path("spendlens-cleanup.html"), vm, "Cleanup groups",
              f"AWS account {ld.account}" if ld.account else "AWS", cfg)
        return
    if fmt is Format.json:
        rows: list[dict[str, Any]] = [{**grp.to_dict(), "resources": [ld.g.nodes[m].to_dict() for m in grp.members]}
                                      for grp in groups]
        emit(rows, fmt, out, "Cleanup groups", meta=meta)
        return
    rows = [{"saving_per_month": grp.monthly_saving, "group": grp.kind_label, "what": grp.title,
             "resources": len(grp.members), "region": grp.region or "", "owner": grp.owner,
             "already_flagged": f"{len(grp.findings)} · {money(grp.findings_saving, cur)}" if grp.findings else "",
             "action": grp.action, "anchor": grp.anchor} for grp in groups]
    if fmt is Format.table:
        if not rows:
            console.print("[green]No cleanup groups.[/] [dim]Thresholds: graph.* in finops.yaml.[/]")
        else:
            emit(rows, fmt, out, f"Cleanup groups · {money(total, cur)} in {len(rows)} group(s)",
                 columns=["saving_per_month", "what", "resources", "region", "owner", "already_flagged"], meta=meta)
            new = round(sum(max(grp.monthly_saving - grp.findings_saving, 0) for grp in groups), 2)
            err.print(f"[bold]{money(total, cur)}[/] in {len(rows)} group(s); {money(flagged, cur)} of it is already "
                      f"flagged by waste checks, [bold]{money(new, cur)}[/] is new. Details: "
                      "spendlens graph show <anchor> · -f html -o cleanup.html")
        for n in _notes(ld):
            err.print(f"[dim]{n}[/]")
        return
    emit(rows, fmt, out, "Cleanup groups", meta=meta)


@graph_app.command("impact")
def graph_impact(
    target: Annotated[str, typer.Argument(help="ARN, AWS id or Name tag.")],
    source: GraphSource = Source.last,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """If this resource were deleted: what breaks or must go first, what is left behind unused, and what stays.

    Examples:
      spendlens graph impact vol-0abc123
      spendlens graph impact ami-0abc123 --json
    """
    cfg = load(config, None, sets)
    cur = cfg.pricing.currency
    ld = load_graph(source, cfg)
    root = pick(ld.g, target)
    r = impact(ld.g, root)
    if json_out:
        typer.echo(json.dumps(r, indent=2, default=str))
        return
    n = ld.g.nodes[root]
    console.print(f"[bold]Deleting {type_label(n.type)} {n.label}[/] [dim]{n.id}[/]")
    for title, key, style in (("Breaks or must go first", "breaks", "red"), ("Left behind, unused", "left_behind",
                                                                              "yellow"), ("Stays (independent)", "kept", "dim")):
        items = r[key]
        console.print(f"[{style}]{title}:[/] " + ("none" if not items else ""))
        for x in items:
            cost = f" · {money(x['monthly_cost'], cur)}" if x.get("monthly_cost") else ""
            console.print(f"  · {x['why']}{cost}")
    console.print(f"Saves [bold]{money(r['monthly_cost'], cur) or 'no estimate'}[/]"
                  + (f", plus {money(r['left_behind_cost'], cur)} if what is left behind is removed too"
                     if r["left_behind_cost"] else "") + ". [dim]Read-only: nothing was changed.[/]")


def load_traffic(source: Source, cfg: Config) -> tuple[dict[str, Any], Loaded]:
    """(traffic result, the graph it was matched against). live runs the flow-log query now (Tailpipe, local)."""
    from ..history import HistoryStore, demo_db_path

    if source is Source.live:
        from ..graph.costs import CostSourceError, tailpipe_runner
        from ..graph.traffic import analyse, flow_sql
        from ..ui import activity

        ld = load_graph(Source.last, cfg)
        try:
            run = tailpipe_runner(cfg.graph.tailpipe_path)
            with activity(f"Reading {cfg.graph.flow_days} days of VPC flow logs [dim](Tailpipe, local)[/]"):
                flows = run(flow_sql(cfg.graph.flow_days))
        except CostSourceError as e:
            err.print(f"[red]VPC flow logs unavailable:[/] {e}\n[dim]Needs Tailpipe with the AWS plugin and the "
                      "aws_vpc_flow_log table collected (see the Resource graph docs). Try --source demo.[/]")
            raise typer.Exit(1) from e
        result = analyse(ld.g, flows, cfg.pricing, cfg.graph.flow_days)
        if cfg.history.enabled:
            try:
                with HistoryStore(history_path(cfg)) as store:
                    saved = {**result, "rows": result["rows"][:500]}
                    store.save_run("graph_traffic", account_id=ld.account, params=cfg.graph.model_dump(mode="json"),
                                   meta={**ci_meta(), "traffic": saved, "graph_run": ld.run_id})
                    store.prune(cfg.history.keep_runs)
            except Exception as e:  # noqa: BLE001 - history must never break a live run
                err.print(f"[yellow]Warning: could not save to history: {e}[/]")
        return result, ld
    path = demo_db_path() if source is Source.demo else history_path(cfg)
    try:
        with HistoryStore(path, read_only=True) as store:
            run = store.latest_run("graph_traffic")
    except FileNotFoundError:
        run = None
    if run is None:
        err.print(f"[red]No saved traffic analysis in {path}.[/] Run [bold]spendlens graph traffic[/] (needs Tailpipe "
                  "with VPC flow logs) or try --source demo.")
        raise typer.Exit(1)
    err.print(f"[dim]Using {source.value} traffic: run #{run.id} from {run.started_at}[/]")
    return run.meta["traffic"], load_graph(source, cfg)


@graph_app.command("traffic")
def graph_traffic(
    source: Annotated[Source, typer.Option("--source", help="live = read the VPC flow logs now with Tailpipe (local, "
                                                            "no AWS calls); last = the saved analysis; demo = sample "
                                                            "data.")] = Source.live,
    path: Annotated[str | None, typer.Option("--path", help="Only one path: cross_az, nat, internet, endpoint, "
                                                            "same_az or vpc_other.")] = None,
    top: Annotated[int, typer.Option("--top", help="Rows to show.")] = 30,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """Data transfer paths from VPC flow logs, priced: cross-AZ, through a NAT gateway, to the internet.

    Each flow is matched to the resource that sent it (from the saved resource graph). Proposes a gateway endpoint
    where S3 or DynamoDB traffic goes through a NAT gateway (gateway endpoints are free). Needs Tailpipe with the
    AWS plugin's aws_vpc_flow_log table; prices from finops.yaml pricing.*_gb.
    Examples:
      spendlens graph traffic
      spendlens graph traffic --path cross_az
      spendlens graph traffic --source demo -f html -o traffic.html
    """
    from ..graph.render import svg as draw
    from ..graph.traffic import PATHS, drawing

    if path and path not in PATHS:
        raise typer.BadParameter(f"one of: {', '.join(PATHS)}", param_hint="--path")
    cfg = load(config, None, sets)
    cur = cfg.pricing.currency
    result, ld = load_traffic(source, cfg)
    rows = [r for r in result["rows"] if not path or r["path"] == path]
    meta = ReportMeta(title="Data transfer", account_id=ld.account,
                      extra={"source": source.value, "window_days": result["window_days"],
                             "monthly_cost": result["monthly_cost"]})
    table = [{"resource": r["resource_name"], "path": r["path_label"], "to": r["peer_name"], "via": r["via_name"],
              "gb_in_per_month": r["gb_in_month"], "gb_out_per_month": r["gb_out_month"],
              "cost_per_month": r["monthly_cost"], "region": r["region"]} for r in rows[:top]]
    if fmt is Format.html:
        pic, cols = drawing(ld.g, result)
        vm = {"focus": None, "rows": [], "shared": [], "groups": [], "notes": [],
              "traffic": {**result, "rows": rows[:top], "total": money(result["monthly_cost"], cur).removesuffix("/mo"),
                          "svg": draw(pic, currency=cur, in_page=True, title="Costliest data transfer paths",
                                      columns=cols)
                          if pic.edges else "",
                          "by_path_bars": [{"label": v["label"], "value": v["monthly_cost"],
                                            "tip": f"{v['label']}: {v['gb_month']:,.0f} GB, {money(v['monthly_cost'], cur)}"}
                                           for v in result["by_path"].values()]}}
        _page(out or Path("spendlens-traffic.html"), vm, "Data transfer",
              f"VPC flow logs, last {result['window_days']} days", cfg)
        return
    if fmt is not Format.table:
        emit(table if fmt is not Format.json else [{**result, "rows": rows}], fmt, out, "Data transfer", meta=meta)
        return
    if not table:
        console.print("[green]No priced data transfer in the window.[/]")
    else:
        emit(table, fmt, out, f"Data transfer · {money(result['monthly_cost'], cur)} (monthly rate, last "
                              f"{result['window_days']} days of flow logs)", meta=meta)
    for v in result["by_path"].values():
        err.print(f"  {v['label']}: {v['gb_month']:,.0f} GB/month · {money(v['monthly_cost'], cur)}")
    for prop in result["proposals"]:
        console.print(f"[yellow]Proposal:[/] {prop['title']} · saves about {money(prop['monthly_saving'], cur)} "
                      f"[dim]({prop['why']})[/]")
    if result["unmatched_interfaces"]:
        err.print(f"[dim]{result['unmatched_interfaces']} interface(s) in the flow logs are not in the saved graph "
                  "(another account or region, or collected since): `spendlens graph collect` refreshes it.[/]")


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


@graph_app.command("owners")
def graph_owners(
    source: GraphSource = Source.last,
    key: Annotated[list[str] | None, typer.Option("--key", "-k", help="Only these tag keys (repeatable).")] = None,
    min_confidence: Annotated[Confidence, typer.Option("--min-confidence", help="high: directly attached or "
                                                       "managed; medium: one step removed; low: the subnet's "
                                                       "majority.")] = Confidence.low,
    script: Annotated[Path | None, typer.Option("--script", help="Also write the tagging commands as a shell script "
                                                "for a person to review and run (spendlens never runs it).")] = None,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """Suggested owner tags for untagged resources, from what they are attached to.

    A volume without a Team tag gets the Team of the instance that mounts it; a network interface or Elastic IP
    the tags of its instance; an instance those of its Auto Scaling group; a snapshot those of its volume. Each
    suggestion says where it came from and how sure it is. Suggests the tagging.mandatory tags and owner tags
    (Owner, Team, Service Owner, CostCentre, the showback keys).
    Examples:
      spendlens graph owners
      spendlens graph owners --min-confidence high -f csv -o owners.csv
      spendlens graph owners --script tag-review.sh
      spendlens graph owners --source demo
    """
    from ..graph.owners import script as make_script

    cfg = load(config, None, sets)
    ld = load_graph(source, cfg)
    sugg = suggestions_for(ld.g, cfg, min_confidence.value)
    if key:
        wanted = {k.lower() for k in key}
        sugg = [x for x in sugg if x.key.lower() in wanted]
    resources = len({x.resource for x in sugg})
    meta = ReportMeta(title="Suggested owner tags", account_id=ld.account,
                      extra={"source": ld.source.value, "run_id": ld.run_id, "resources": resources})
    rows = [{"resource": ld.g.nodes[x.resource].short_id, "type": type_label(x.type), "name": x.name, "tag": x.key,
             "suggested": x.value, "confidence": x.confidence, "because": x.reason, "region": x.region,
             "arn": x.resource} for x in sugg]
    if fmt is Format.table and not rows:
        console.print("[green]No suggestions:[/] every collected resource has the policy and owner tags, or nothing "
                      "related carries them.")
    elif fmt is Format.table:
        emit(rows, fmt, out, f"Suggested owner tags · {resources} resource(s)", meta=meta,
             columns=["resource", "type", "tag", "suggested", "confidence", "because"])
        by_conf = {c: sum(1 for x in sugg if x.confidence == c) for c in ("high", "medium", "low")}
        err.print(f"{len(sugg)} tag(s) for {resources} resource(s): {by_conf['high']} high, {by_conf['medium']} "
                  f"medium, {by_conf['low']} low confidence. Apply after review: --script tag-review.sh")
    else:
        emit(rows if fmt is not Format.json else [x.to_dict() for x in sugg], fmt, out, "Suggested owner tags",
             meta=meta)
    if script:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(make_script(sugg))
        err.print(f"[green]Wrote[/] {script} [dim](review it before running; spendlens never runs it)[/]")


@graph_app.command("stats")
def graph_stats(source: GraphSource = Source.last, config: ConfigOpt = None, sets: SetOpt = None) -> None:
    """What the saved graph holds: resources and links by type, and sources that were skipped (and why)."""
    cfg = load(config, None, sets)
    _print_stats(load_graph(source, cfg))


BASIS = {"cur": "from the CUR", "cost_explorer": "from Cost Explorer resource data", "estimate": "price-book estimates",
         "none": "unpriced"}


def _notes(ld: Loaded) -> list[str]:
    return [f"Skipped {why}" for why in (ld.meta.get("skipped") or {}).values()]


def _print_stats(ld: Loaded) -> None:
    s = ld.g.stats()
    basis: dict[str, int] = {}
    for n in ld.g.nodes.values():
        if n.monthly_cost is not None:
            basis[n.cost_basis] = basis.get(n.cost_basis, 0) + 1
    t = Table(title=f"Resource graph · {s['nodes']:,} resources · {s['edges']:,} links", header_style=f"bold {BRAND}")
    for c in ("type", "resources", "cost estimate"):
        t.add_column(c, overflow="fold")
    for typ, count in sorted(s["by_type"].items(), key=lambda kv: -kv[1]):
        cost = ld.g.total_cost(n.id for n in ld.g.of_type(typ))
        t.add_row(type_label(typ), f"{count:,}", money(cost) if cost else "")
    console.print(t)
    if basis:
        console.print("[dim]Costs: " + " · ".join(f"{v:,} {BASIS.get(k, k)}" for k, v in sorted(basis.items())) + "[/]")
    if s["by_label"]:
        console.print("[dim]Links: " + " · ".join(f"{k} {v:,}" for k, v in sorted(s["by_label"].items(),
                                                                               key=lambda kv: -kv[1])) + "[/]")
    for why in (ld.meta.get("skipped") or {}).values():
        err.print(f"[yellow]Skipped[/] {why}")
    console.print("[dim]Next: spendlens graph orphans --source last · spendlens graph show <id>[/]")


def register(reg: Registry) -> None:
    reg.add_typer(graph_app, "graph")
