"""Draw a graph: a terminal tree, Mermaid, Graphviz DOT, JSON, and a self-contained SVG.

The SVG uses a layered layout computed here (no JavaScript library): each resource type has a column, left to right
from network to compute to storage to snapshots (model.TYPES), and nodes in a column are ordered by where their
neighbours sit to keep lines short. Colours come from the report's CSS variables, with built-in light/dark
fallbacks so a standalone .svg also follows the viewer's theme. Every name is escaped.
"""

from __future__ import annotations

import json
from collections import defaultdict
from html import escape
from typing import Any

from markupsafe import Markup

from .model import Edge, Graph, Node, layer, type_label

NODE_W, NODE_H, COL_GAP, ROW_GAP, PAD = 196, 54, 76, 14, 16
MAX_PER_COLUMN = 12

STYLE = """
.slg{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.slg .n rect{fill:var(--surface,#fff);stroke:var(--axis,#d2d2d7);stroke-width:1.2}
.slg .n .t{fill:var(--ink-2,#6e6e73);font-size:10px}
.slg .n .l{fill:var(--ink,#1d1d1f);font-size:12px;font-weight:600}
.slg .n .c{fill:var(--ink-2,#6e6e73);font-size:10.5px}
.slg .n.focus rect{stroke:var(--series-1,#2a78d6);stroke-width:2.4}
.slg .n.grp rect{stroke:var(--warning,#fab219);stroke-width:2}
.slg .n.alarm rect{stroke:var(--critical,#d03b3b);stroke-width:2}
.slg .n.more rect{stroke-dasharray:4 3}
.slg .n .bar{fill:var(--critical,#d03b3b)}
.slg .n.grp .bar{fill:var(--warning,#fab219)}
.slg .e path{fill:none;stroke:var(--muted,#86868b);stroke-opacity:.7;stroke-width:1.2}
.slg .e.ref path{stroke-dasharray:4 4}
.slg .e text{fill:var(--ink-2,#6e6e73);font-size:9.5px;paint-order:stroke;stroke:var(--page,#f5f5f7);stroke-width:3px}
.slg .n:hover rect{stroke:var(--series-1,#2a78d6)}
.slg.dim .n:not(.hi),.slg.dim .e:not(.hi){opacity:.25}
@media (prefers-color-scheme:dark){.slg:not(.in-page) .n rect{fill:#1c1c1e;stroke:#3a3a3c}
.slg:not(.in-page) .n .l{fill:#f5f5f7}.slg:not(.in-page) .n .t,.slg:not(.in-page) .n .c,.slg:not(.in-page) .e text{fill:#a1a1a6}
.slg:not(.in-page) .e text{stroke:#000}}
"""


def money(v: float | None, cur: str = "USD") -> str:
    if v is None:
        return ""
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur, "")
    return f"{sym}{v:,.2f}/mo" if sym else f"{v:,.2f} {cur}/mo"


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ layout


def layout(g: Graph, focus: str | None = None) -> tuple[dict[str, tuple[float, float]], list[Node], list[Edge], int, int]:
    """Positions by column (type) and barycentre order. Columns longer than MAX_PER_COLUMN fold the rest into one
    "+N more" node, and edges to folded nodes go to it."""
    cols: dict[int, list[Node]] = defaultdict(list)
    for n in sorted(g.nodes.values(), key=lambda n: (n.type, n.label, n.id)):
        cols[layer(n.type)].append(n)
    order = sorted(cols)
    folded: dict[str, str] = {}
    shown: dict[int, list[Node]] = {}
    for c in order:
        ns = cols[c]
        if focus:
            ns.sort(key=lambda n: n.id != focus)
        if len(ns) > MAX_PER_COLUMN:
            keep, rest = ns[: MAX_PER_COLUMN - 1], ns[MAX_PER_COLUMN - 1:]
            more = Node(f"more:{c}", "more", f"+{len(rest)} more", monthly_cost=round(sum(n.monthly_cost or 0 for n in rest), 2),
                        attrs={"types": sorted({type_label(n.type) for n in rest})})
            for n in rest:
                folded[n.id] = more.id
            ns = [*keep, more]
        shown[c] = ns
    nodes = [n for c in order for n in shown[c]]
    ids = {n.id for n in nodes}
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for e in g.edges:
        a, b = folded.get(e.from_id, e.from_id), folded.get(e.to_id, e.to_id)
        if a in ids and b in ids and a != b and (a, b) not in seen:
            seen.add((a, b))
            edges.append(Edge(a, b, e.label if a == e.from_id and b == e.to_id else "", e.kind))
    nbrs: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        nbrs[e.from_id].append(e.to_id)
        nbrs[e.to_id].append(e.from_id)
    pos_idx: dict[str, float] = {}
    for c in order:  # initial order
        for i, n in enumerate(shown[c]):
            pos_idx[n.id] = i
    for _ in range(3):  # barycentre sweeps
        for c in order:
            def bary(n: Node) -> float:
                ps = [pos_idx[m] for m in nbrs[n.id] if m in pos_idx]
                return sum(ps) / len(ps) if ps else pos_idx[n.id]
            if focus and any(n.id == focus for n in shown[c]):
                continue
            shown[c].sort(key=bary)
            for i, n in enumerate(shown[c]):
                pos_idx[n.id] = i
    tallest = max((len(v) for v in shown.values()), default=1)
    height = PAD * 2 + tallest * NODE_H + (tallest - 1) * ROW_GAP
    pos: dict[str, tuple[float, float]] = {}
    for ci, c in enumerate(order):
        col = shown[c]
        h = len(col) * NODE_H + (len(col) - 1) * ROW_GAP
        y0 = (height - h) / 2
        for i, n in enumerate(col):
            pos[n.id] = (PAD + ci * (NODE_W + COL_GAP), y0 + i * (NODE_H + ROW_GAP))
    width = PAD * 2 + len(order) * NODE_W + max(len(order) - 1, 0) * COL_GAP
    return pos, nodes, edges, int(width), int(height)


def svg(g: Graph, *, focus: str | None = None, highlight: set[str] | None = None, alarms: set[str] | None = None,
        currency: str = "USD", in_page: bool = False, title: str = "Resource graph") -> str:
    """An inline SVG. highlight = members of a cleanup group, alarms = resources with a failing check."""
    highlight, alarms = highlight or set(), alarms or set()
    if not g.nodes:
        return Markup("")
    pos, nodes, edges, w, h = layout(g, focus)
    out = [(f'<svg class="slg{" in-page" if in_page else ""}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{escape(title)}" xmlns="http://www.w3.org/2000/svg">'),
           f"<title>{escape(title)}</title>"]
    if not in_page:
        out.append(f"<style>{STYLE}</style>")
    labelled: set[tuple[str, str]] = set()
    for e in edges:
        (x1, y1), (x2, y2) = pos[e.from_id], pos[e.to_id]
        if x1 > x2 or (x1 == x2 and y1 > y2):
            (x1, y1), (x2, y2) = (x2, y2), (x1, y1)
        if x1 == x2:  # same column: loop out to the right
            sx, sy, ex, ey = x1 + NODE_W, y1 + NODE_H / 2, x2 + NODE_W, y2 + NODE_H / 2
            d = f"M{sx:.0f},{sy:.0f} C{sx + 40:.0f},{sy:.0f} {ex + 40:.0f},{ey:.0f} {ex:.0f},{ey:.0f}"
            lx, ly = sx + 30, (sy + ey) / 2
        else:
            sx, sy, ex, ey = x1 + NODE_W, y1 + NODE_H / 2, x2, y2 + NODE_H / 2
            mx = (sx + ex) / 2
            d = f"M{sx:.0f},{sy:.0f} C{mx:.0f},{sy:.0f} {mx:.0f},{ey:.0f} {ex:.0f},{ey:.0f}"
            lx, ly = mx, (sy + ey) / 2 - 3
        key = (e.from_id, e.label)
        lab = ""
        if e.label and key not in labelled:  # one label per resource and kind of link
            labelled.add(key)
            lab = f'<text x="{lx:.0f}" y="{ly:.0f}" text-anchor="middle">{escape(e.label)}</text>'

        out.append(f'<g class="e{" ref" if e.kind == "ref" else ""}" data-a="{escape(e.from_id)}" '
                   f'data-b="{escape(e.to_id)}"><path d="{d}"/>{lab}</g>')
    for n in nodes:
        x, y = pos[n.id]
        cls = ["n"]
        if n.id == focus:
            cls.append("focus")
        if n.id in highlight:
            cls.append("grp")
        elif n.id in alarms:
            cls.append("alarm")
        if n.type == "more":
            cls.append("more")
        kind = ", ".join(n.attrs.get("types", [])) if n.type == "more" else type_label(n.type)
        sub = money(n.monthly_cost, currency) if n.monthly_cost else (n.state or "")
        if n.type == "more":
            sub = (money(n.monthly_cost, currency) + " in total") if n.monthly_cost else ""
        tip = f"{kind}: {n.label}\n{n.id}" + (f"\n{n.state}" if n.state else "") + (f"\n{sub}" if sub else "")
        bar = '<rect class="bar" x="0" y="0" width="4" height="54" rx="2"/>' if ("grp" in cls or "alarm" in cls) else ""
        out.append(f'<g class="{" ".join(cls)}" data-id="{escape(n.id)}" transform="translate({x:.0f},{y:.0f})">'
                   f"<title>{escape(tip)}</title>"
                   f'<rect width="{NODE_W}" height="{NODE_H}" rx="10"/>{bar}'
                   f'<text class="t" x="12" y="16">{escape(_clip(kind, 30))}</text>'
                   f'<text class="l" x="12" y="32">{escape(_clip(n.label, 26))}</text>'
                   f'<text class="c" x="12" y="46">{escape(_clip(sub, 30))}</text></g>')
    out.append("</svg>")
    # every name and id above is escaped, so the SVG is safe to place in a page as markup
    return Markup("".join(out))




# ------------------------------------------------------------------ text formats


def mermaid(g: Graph, *, focus: str | None = None, currency: str = "USD") -> str:
    ids = {n.id: f"n{i}" for i, n in enumerate(g.nodes.values())}
    lines = ["flowchart LR"]
    for n in g.nodes.values():
        label = f"{type_label(n.type)}<br/>{n.label}" + (f"<br/>{money(n.monthly_cost, currency)}" if n.monthly_cost else "")
        label = label.replace('"', "'")
        lines.append(f'  {ids[n.id]}["{label}"]')
    for e in g.edges:
        arrow = "-.->" if e.kind == "ref" else "-->"
        lines.append(f"  {ids[e.from_id]} {arrow}|{e.label}| {ids[e.to_id]}")
    if focus and focus in ids:
        lines.append(f"  style {ids[focus]} stroke-width:3px")
    return "\n".join(lines) + "\n"


def dot(g: Graph, *, focus: str | None = None, currency: str = "USD") -> str:
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = ["digraph spendlens {", "  rankdir=LR; node [shape=box, style=rounded, fontname=Helvetica];"]
    for n in g.nodes.values():
        label = f"{type_label(n.type)}\\n{n.label}" + (f"\\n{money(n.monthly_cost, currency)}" if n.monthly_cost else "")
        extra = ", penwidth=2.5" if n.id == focus else ""
        lines.append(f"  {q(n.id)} [label=\"{label.replace(chr(34), chr(39))}\"{extra}];")
    for e in g.edges:
        style = ", style=dashed" if e.kind == "ref" else ""
        lines.append(f"  {q(e.from_id)} -> {q(e.to_id)} [label={q(e.label)}{style}];")
    return "\n".join([*lines, "}"]) + "\n"


def to_json(g: Graph, **extra: Any) -> str:
    nodes, edges = g.to_rows()
    return json.dumps({**extra, "nodes": nodes, "edges": edges}, indent=2, default=str) + "\n"


def tree(g: Graph, root: str, *, currency: str = "USD", alarms: set[str] | None = None,
         groups: set[str] | None = None, depth: int = 2) -> Any:
    """A rich Tree from the root outwards (each resource shown once)."""
    from rich.markup import escape as resc
    from rich.tree import Tree

    alarms, groups = alarms or set(), groups or set()

    def line(n: Node, via: str = "") -> str:
        cost = f" [bold]{money(n.monthly_cost, currency)}[/]" if n.monthly_cost else ""
        state = f" [dim]{resc(n.state)}[/]" if n.state else ""
        flag = " [yellow]cleanup group[/]" if n.id in groups else " [red]alarm[/]" if n.id in alarms else ""
        arrow = f"[dim]{resc(via)} →[/] " if via else ""
        return f"{arrow}{type_label(n.type)} [cyan]{resc(n.label)}[/]{state}{cost}{flag} [dim]{resc(n.short_id)}[/]"

    t = Tree(line(g.nodes[root]))
    seen = {root}

    def walk(node_id: str, branch: Any, d: int) -> None:
        if d >= depth:
            return
        for e, other in sorted(g.neighbours(node_id), key=lambda x: (layer(g.nodes[x[1]].type), g.nodes[x[1]].label)):
            if other in seen:
                continue
            seen.add(other)
            via = e.label if e.from_id == node_id else f"{e.label} (from)"
            sub = branch.add(line(g.nodes[other], via))
            if g.nodes[other].type not in ("vpc", "vpc_subnet"):
                walk(other, sub, d + 1)

    walk(root, t, 0)
    return t
