"""Dependency-free chart primitives (inline SVG / HTML) for the self-contained dashboard.

Mark specs: bars <= 16px thick with a 4px rounded data-end and a square baseline end; 2px lines; >= 8px end
markers with a 2px surface ring; hairline solid gridlines; text never in series colours. Colours are CSS
variables (--series-N, --div-pos, --div-neg, --status-*) defined in the page so light/dark swap in one place.

Every mark carries data-tip (hover + keyboard focus tooltip) and every chart has a table twin built by
the caller, so a tooltip never gates a value.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from html import escape
from typing import Any

from markupsafe import Markup  # re-exported for plugin dashboard sections

BAR = 16  # bar thickness (<= 24px rule)
GAP = 10  # space between bar rows
LABEL_W = 230  # left label column
VALUE_W = 92  # space reserved for value labels at the bar tip


__all__ = ["Markup", "columns", "count", "diverging_hbars", "dumbbell", "esc", "flow", "hbars", "legend", "lines", "matrix",
           "meter", "money", "segmented", "waterfall"]


def esc(v: Any) -> str:
    return escape(str(v), quote=True)


def money(v: float | None, cur: str = "USD", compact: bool = True) -> str:
    if v is None:
        return "–"
    sym = {"USD": "$", "GBP": "£", "EUR": "€"}.get(cur, "")
    sign = "-" if v < 0 else ""
    a = abs(v)
    if compact and a >= 1_000_000:
        s = f"{a / 1_000_000:.1f}M"
    elif compact and a >= 10_000:
        s = f"{a / 1_000:.1f}K"
    else:
        s = f"{a:,.0f}" if a >= 100 else f"{a:,.2f}"
    return f"{sign}{sym}{s}" + ("" if sym else f" {cur}")


def count(v: float | None, cur: str = "") -> str:
    """Plain number (a formatter for hbars when the values are counts, not money)."""
    return "–" if v is None else f"{v:,.0f}"


def signed_money(v: float, cur: str = "USD") -> str:
    return ("+" if v > 0 else "") + money(v, cur)


def pct(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "–"
    return (f"{v:+.1f}%" if signed else f"{v:.1f}%")


def _label_w(width: int) -> int:
    return min(LABEL_W, int(width * 0.3))


def _chars(lw: int) -> int:
    return max(10, lw // 7)


def _truncate(text: str, n: int = 34) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _bar_path(x0: float, y: float, length: float, h: float, r: float = 4, leftward: bool = False) -> str:
    """Bar from a square baseline end to a rounded data end."""
    length = max(length, 0.0)
    r = min(r, length, h / 2)
    if leftward:  # baseline on the right at x0, grows left
        x1 = x0 - length
        return (f"M{x0:.1f},{y:.1f} H{x1 + r:.1f} A{r},{r} 0 0 0 {x1:.1f},{y + r:.1f} V{y + h - r:.1f} "
                f"A{r},{r} 0 0 0 {x1 + r:.1f},{y + h:.1f} H{x0:.1f} Z")
    x1 = x0 + length
    return (f"M{x0:.1f},{y:.1f} H{x1 - r:.1f} A{r},{r} 0 0 1 {x1:.1f},{y + r:.1f} V{y + h - r:.1f} "
            f"A{r},{r} 0 0 1 {x1 - r:.1f},{y + h:.1f} H{x0:.1f} Z")


def hbars(items: list[dict[str, Any]], *, label: str, fill: str = "var(--series-1)", width: int = 760,
          fmt=money, cur: str = "USD") -> str:
    """Ranked horizontal bars, one series. items: {label, value, tip?, muted?}."""
    if not items:
        return '<p class="empty">No data.</p>'
    vmax = max(i["value"] for i in items) or 1
    lw = _label_w(width)
    plot = width - lw - VALUE_W
    h = len(items) * (BAR + GAP) + GAP
    rows = []
    for k, it in enumerate(items):
        y = GAP + k * (BAR + GAP)
        length = plot * it["value"] / vmax
        color = "var(--muted-fill)" if it.get("muted") else fill
        tip = it.get("tip") or f"{it['label']}: {fmt(it['value'], cur)}"
        rows.append(
            f'<g class="mark" tabindex="0" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
            f'<rect class="hit" x="0" y="{y - GAP / 2}" width="{width}" height="{BAR + GAP}"/>'
            f'<text class="lbl" x="{lw - 10}" y="{y + BAR - 4}" text-anchor="end">{esc(_truncate(it["label"], _chars(lw)))}</text>'
            f'<path d="{_bar_path(lw, y, length, BAR)}" fill="{color}"/>'
            f'<text class="val" x="{lw + length + 6}" y="{y + BAR - 4}">{esc(fmt(it["value"], cur))}</text></g>'
        )
    return (f'<svg class="chart" style="max-width:{width}px" viewBox="0 0 {width} {h}" role="img" aria-label="{esc(label)}">'
            f'<line class="axis" x1="{lw}" x2="{lw}" y1="0" y2="{h}"/>{"".join(rows)}</svg>')


def diverging_hbars(items: list[dict[str, Any]], *, label: str, width: int = 760, cur: str = "USD") -> str:
    """Change bars around a zero baseline: increases right (--div-pos), decreases left (--div-neg)."""
    if not items:
        return '<p class="empty">No changes.</p>'
    vmax = max(abs(i["value"]) for i in items) or 1
    lw = _label_w(width)
    half = (width - lw - 2 * VALUE_W) / 2
    zero = lw + VALUE_W + half
    h = len(items) * (BAR + GAP) + GAP
    rows = []
    for k, it in enumerate(items):
        y = GAP + k * (BAR + GAP)
        v = it["value"]
        length = half * abs(v) / vmax
        tip = it.get("tip") or f"{it['label']}: {signed_money(v, cur)}"
        if v >= 0:
            bar = f'<path d="{_bar_path(zero, y, length, BAR)}" fill="var(--div-pos)"/>'
            val = f'<text class="val" x="{zero + length + 6}" y="{y + BAR - 4}">{esc(signed_money(v, cur))}</text>'
        else:
            bar = f'<path d="{_bar_path(zero, y, length, BAR, leftward=True)}" fill="var(--div-neg)"/>'
            val = (f'<text class="val" x="{zero - length - 6}" y="{y + BAR - 4}" text-anchor="end">'
                   f'{esc(signed_money(v, cur))}</text>')
        rows.append(
            f'<g class="mark" tabindex="0" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
            f'<rect class="hit" x="0" y="{y - GAP / 2}" width="{width}" height="{BAR + GAP}"/>'
            f'<text class="lbl" x="{lw - 10}" y="{y + BAR - 4}" text-anchor="end">{esc(_truncate(it["label"], _chars(lw)))}</text>'
            f"{bar}{val}</g>"
        )
    return (f'<svg class="chart" style="max-width:{width}px" viewBox="0 0 {width} {h}" role="img" aria-label="{esc(label)}">'
            f'<line class="axis" x1="{zero}" x2="{zero}" y1="0" y2="{h}"/>{"".join(rows)}</svg>')


def _nice_ticks(vmax: float, n: int = 4) -> list[float]:
    if vmax <= 0:
        return [0, 1]
    raw = vmax / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    return [step * i for i in range(math.ceil(vmax / step) + 1)]


def _tick(v: float) -> str:
    return f"{v / 1000:g}K" if v >= 10_000 else f"{v:,.0f}"


def lines(series: list[dict[str, Any]], x_labels: list[str], *, label: str, width: int = 760,
          height: int = 300, cur: str = "USD") -> str:
    """Multi-series line chart (<= 4 series, slots 1..4). series: {name, values, slot}.
    Legend is rendered by the caller; direct end labels are added unless they would collide."""
    if not series or not x_labels:
        return '<p class="empty">No data.</p>'
    left, right, top, bottom = 56, 190, 16, 34
    pw, ph = width - left - right, height - top - bottom
    vmax = max(max(s["values"]) for s in series) or 1
    ticks = _nice_ticks(vmax)
    ymax = ticks[-1]
    n = len(x_labels)

    def X(i: int) -> float:
        return left + (pw * i / (n - 1) if n > 1 else pw / 2)

    def Y(v: float) -> float:
        return top + ph - ph * v / ymax

    parts = []
    for t in ticks:
        parts.append(f'<line class="grid" x1="{left}" x2="{left + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                     f'<text class="tick" x="{left - 8}" y="{Y(t) + 4:.1f}" text-anchor="end">{_tick(t)}</text>')
    for i, xl in enumerate(x_labels):
        parts.append(f'<text class="tick" x="{X(i):.1f}" y="{height - 10}" text-anchor="middle">{esc(xl)}</text>')
    parts.append(f'<line class="axis" x1="{left}" x2="{left + pw}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    ends = []
    for s in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s["values"]))
        color = f"var(--series-{s['slot']})"
        parts.append(f'<polyline class="line" points="{pts}" stroke="{color}"/>')
        ex, ey = X(n - 1), Y(s["values"][-1])
        parts.append(f'<circle class="dot" cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="{color}"/>')
        ends.append((ey, s))
    # direct end labels only when they don't collide (never stack/nudge them; the legend carries identity)
    ys = sorted(e[0] for e in ends)
    if all(b - a >= 14 for a, b in itertools.pairwise(ys)):
        for ey, s in ends:
            parts.append(f'<text class="lbl" x="{X(n - 1) + 10:.1f}" y="{ey + 4:.1f}">'
                         f'{esc(_truncate(s["name"], 24))} {esc(money(s["values"][-1], cur))}</text>')
    # hover bands: one per x, tooltip lists every series at that x
    band = pw / max(n - 1, 1)
    for i, xl in enumerate(x_labels):
        tip = f"{xl}: " + "; ".join(f"{s['name']} {money(s['values'][i], cur)}" for s in series)
        x0 = X(i) - band / 2
        parts.append(f'<rect class="hit band" tabindex="0" x="{max(x0, left - 4):.1f}" y="{top}" '
                     f'width="{band:.1f}" height="{ph}" data-tip="{esc(tip)}" aria-label="{esc(tip)}"/>')
    return (f'<svg class="chart" style="max-width:{width}px" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}">'
            f'{"".join(parts)}</svg>')


def columns(items: list[dict[str, Any]], *, label: str, width: int = 760, height: int = 300, cur: str = "USD",
            budget: float | None = None) -> str:
    """Vertical columns, one per period (e.g. monthly spend). items: {label, value, tip?, kind?}; kind "forecast"
    draws a hatched projection column, "latest" the highlighted last actual. Optional dashed budget line."""
    if not items:
        return '<p class="empty">No data.</p>'
    left, right, top, bottom = 56, 16, 40, 34
    pw, ph = width - left - right, height - top - bottom
    vmax = max([i["value"] for i in items] + [budget or 0]) or 1
    ticks = _nice_ticks(vmax)
    ymax = ticks[-1]
    n = len(items)
    slot = pw / n
    bw = min(56.0, slot * 0.62)

    def Y(v: float) -> float:
        return top + ph - ph * v / ymax

    parts = [('<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
              'patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--series-1)" fill-opacity=".18"/>'
              '<line x1="0" y1="0" x2="0" y2="6" stroke="var(--series-1)" stroke-width="2.5"/></pattern></defs>')]
    for t in ticks:
        parts.append(f'<line class="grid" x1="{left}" x2="{left + pw}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                     f'<text class="tick" x="{left - 8}" y="{Y(t) + 4:.1f}" text-anchor="end">{_tick(t)}</text>')
    for i, it in enumerate(items):
        cx = left + slot * i + slot / 2
        x0, y0 = cx - bw / 2, Y(it["value"])
        h = max(Y(0) - y0, 0)
        kind = it.get("kind", "")
        fill = "url(#hatch)" if kind == "forecast" else "var(--series-1)"
        op = "" if kind in ("latest", "forecast") else ' fill-opacity=".45"'
        r = min(4, h / 2, bw / 2)
        path = (f"M{x0:.1f},{Y(0):.1f} V{y0 + r:.1f} Q{x0:.1f},{y0:.1f} {x0 + r:.1f},{y0:.1f} H{x0 + bw - r:.1f} "
                f"Q{x0 + bw:.1f},{y0:.1f} {x0 + bw:.1f},{y0 + r:.1f} V{Y(0):.1f} Z")
        tip = it.get("tip") or f"{it['label']}: {money(it['value'], cur)}"
        parts.append(f'<g class="mark" tabindex="0" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
                     f'<rect class="hit" x="{left + slot * i:.1f}" y="{top}" width="{slot:.1f}" height="{ph}"/>'
                     f'<path d="{path}" fill="{fill}"{op}/>'
                     f'<text class="val" x="{cx:.1f}" y="{y0 - 6:.1f}" text-anchor="middle">{esc(money(it["value"], cur))}</text>'
                     f'<text class="tick" x="{cx:.1f}" y="{height - 10}" text-anchor="middle">{esc(it["label"])}</text></g>')
    parts.append(f'<line class="axis" x1="{left}" x2="{left + pw}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    if budget:
        yb = Y(budget)
        parts.append(f'<line class="budget" x1="{left}" x2="{left + pw}" y1="{yb:.1f}" y2="{yb:.1f}"/>')
    parts.append(f'<g class="col-key" transform="translate({left},{8})">'
                 f'<rect width="10" height="10" y="-8" rx="2" fill="var(--series-1)"/>'
                 f'<text class="tick" x="15" y="1">actual</text>'
                 + ('<rect x="70" width="10" height="10" y="-8" rx="2" fill="url(#hatch)"/><text class="tick" x="85" y="1">'
                    'forecast</text>' if any(i.get("kind") == "forecast" for i in items) else "")
                 + (f'<line class="budget" x1="150" x2="170" y1="-3" y2="-3"/><text class="tick" x="175" y="1">'
                    f'budget {esc(money(budget, cur))}</text>' if budget else "") + '</g>')
    return (f'<svg class="chart" style="max-width:{width}px" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}">'
            f'{"".join(parts)}</svg>')


STATUS_ICON = {"good": "✓", "warning": "!", "serious": "!", "critical": "✕", "neutral": "•"}


def meter(name: str, value_pct: float | None, status: str, detail: str) -> str:
    """Horizontal meter: fill = value % (capped at 100 visually), colour = status, with icon + label text."""
    v = 0 if value_pct is None else value_pct
    fill = min(max(v, 0), 100)
    over = f'<span class="over">{v:.0f}%</span>' if v > 100 else ""
    return (f'<div class="meter" tabindex="0" data-tip="{esc(name + ": " + detail)}">'
            f'<div class="meter-head"><span class="meter-name">{esc(name)}</span>'
            f'<span class="badge {status}"><span aria-hidden="true">{STATUS_ICON.get(status, "•")}</span> '
            f'{esc(detail)}</span></div>'
            f'<div class="meter-track {status}"><div class="meter-fill {status}" style="width:{fill:.1f}%"></div></div>'
            f"{over}</div>")


def segmented(parts: list[dict[str, Any]], *, label: str, cur: str = "USD") -> str:
    """Tall segmented bar (part-to-whole, <= 6 parts) + legend in block order."""
    total = sum(p["value"] for p in parts) or 1
    blocks, legend = [], []
    for p in parts:
        if p["value"] <= 0:
            continue
        share = p["value"] / total * 100
        text = f"{share:.0f}%" if share >= 9 else ""
        tip = f"{p['label']}: {money(p['value'], cur)} ({share:.1f}%)"
        blocks.append(f'<div class="seg" tabindex="0" style="flex:{p["value"]};background:var(--series-{p["slot"]})" '
                      f'data-tip="{esc(tip)}" aria-label="{esc(tip)}"><span class="seg-txt {p.get("ink", "light")}">'
                      f"{text}</span></div>")
        legend.append(f'<span class="key"><i style="background:var(--series-{p["slot"]})"></i>{esc(p["label"])} '
                      f"<b>{esc(money(p['value'], cur))}</b></span>")
    return (f'<div class="segbar" role="img" aria-label="{esc(label)}">{"".join(blocks)}</div>'
            f'<div class="legend">{"".join(legend)}</div>')


def legend(series: list[dict[str, Any]]) -> str:
    return '<div class="legend">' + "".join(
        f'<span class="key"><i class="line-key" style="background:var(--series-{s["slot"]})"></i>{esc(s["name"])}</span>'
        for s in series) + "</div>"


def waterfall(start: dict[str, Any], steps: list[dict[str, Any]], end: dict[str, Any], *, label: str,
              width: int = 1080, cur: str = "USD") -> str:
    """Spend bridge, one row per step: the start total (--neutral), a floating bar per change (increase --div-pos, decrease
    --div-neg) from the running total, then the end total. start/end/steps: {label, value, tip?}.
    The axis starts near the lowest running total so changes stay visible; the start value is printed under it."""
    if not steps:
        return '<p class="empty">No changes.</p>'
    run, pts = start["value"], [start["value"]]
    for st in steps:
        run += st["value"]
        pts.append(run)
    lo_raw, hi = min([*pts, end["value"]]), max([*pts, end["value"]])
    span = (hi - lo_raw) or abs(hi) or 1
    ticks = _nice_ticks(span * 1.6)
    step = ticks[1] - ticks[0] if len(ticks) > 1 else span
    lo = max(0.0, math.floor((lo_raw - span * 0.6) / step) * step)
    hi = math.ceil(hi / step) * step
    lw = _label_w(width)
    plot = width - lw - VALUE_W - 10
    row = BAR + 2 * GAP

    def X(v: float) -> float:
        return lw + plot * (v - lo) / ((hi - lo) or 1)

    rows_n = len(steps) + 2
    h = rows_n * row + 30
    parts = []
    for t in [lo + step * i for i in range(round((hi - lo) / step) + 1)]:
        parts.append(f'<line class="grid" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="0" y2="{rows_n * row}"/>'
                     f'<text class="tick" x="{X(t):.1f}" y="{rows_n * row + 16}" text-anchor="middle">{_tick(t)}</text>')

    def total_row(k: int, it: dict[str, Any]) -> str:
        y = GAP + k * row
        tip = it.get("tip") or f"{it['label']}: {money(it['value'], cur)}"
        return (f'<g class="mark" tabindex="0" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
                f'<rect class="hit" x="0" y="{y - GAP}" width="{width}" height="{row}"/>'
                f'<text class="lbl strong" x="{lw - 10}" y="{y + BAR - 4}" text-anchor="end">{esc(_truncate(it["label"], _chars(lw)))}</text>'
                f'<path d="{_bar_path(X(lo), y, X(it["value"]) - X(lo), BAR)}" fill="var(--neutral)"/>'
                f'<text class="val strong" x="{X(it["value"]) + 6:.1f}" y="{y + BAR - 4}">{esc(money(it["value"], cur))}</text></g>')

    parts.append(total_row(0, start))
    run = start["value"]
    for k, st in enumerate(steps, 1):
        y = GAP + k * row
        v = st["value"]
        a, b = sorted((X(run), X(run + v)))
        color = "var(--div-pos)" if v > 0 else "var(--div-neg)"
        tip = st.get("tip") or f"{st['label']}: {signed_money(v, cur)}"
        parts.append(f'<line class="link" x1="{X(run):.1f}" x2="{X(run):.1f}" y1="{y - 2 * GAP + BAR:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<g class="mark" tabindex="0" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
                     f'<rect class="hit" x="0" y="{y - GAP}" width="{width}" height="{row}"/>'
                     f'<text class="lbl" x="{lw - 10}" y="{y + BAR - 4}" text-anchor="end">{esc(_truncate(st["label"], _chars(lw)))}</text>'
                     f'<rect x="{a:.1f}" y="{y}" width="{max(b - a, 2):.1f}" height="{BAR}" rx="3" fill="{color}"/>'
                     f'<text class="val" x="{b + 6:.1f}" y="{y + BAR - 4}">{esc(signed_money(v, cur))}</text></g>')
        run += v
    y_end = GAP + (len(steps) + 1) * row
    parts.append(f'<line class="link" x1="{X(run):.1f}" x2="{X(run):.1f}" y1="{y_end - 2 * GAP + BAR:.1f}" y2="{y_end:.1f}"/>')
    parts.append(total_row(len(steps) + 1, end))
    return (f'<svg class="chart" style="max-width:{width}px" viewBox="0 0 {width} {h}" role="img" aria-label="{esc(label)}">'
            f'{"".join(parts)}</svg>'
            + (f'<p class="note">The axis starts at {esc(money(lo, cur))} so the changes are visible.</p>' if lo > 0 else ""))


def flow(links: list[dict[str, Any]], *, label: str, left: str = "", right: str = "", width: int = 1080,
         cur: str = "USD", max_nodes: int = 8) -> str:
    """Two-column flow (Sankey) diagram: bands from left nodes to right nodes, thickness = value.
    links: {source, target, value}. Up to max_nodes per side; the rest are grouped as "Other". The four largest
    left nodes take series colours 1-4, the rest a muted fill; every band and node has a tooltip."""
    links = [x for x in links if (x.get("value") or 0) > 0]
    if not links:
        return '<p class="empty">No data.</p>'

    def top(key: str) -> list[str]:
        acc: dict[str, float] = defaultdict(float)
        for x in links:
            acc[x[key]] += x["value"]
        names = sorted(acc, key=lambda n: -acc[n])
        return names if len(names) <= max_nodes else [*names[: max_nodes - 1], "Other"]

    lefts, rights = top("source"), top("target")
    agg: dict[tuple[str, str], float] = defaultdict(float)
    for x in links:
        s = x["source"] if x["source"] in lefts else "Other"
        t = x["target"] if x["target"] in rights else "Other"
        agg[(s, t)] += x["value"]
    total = sum(agg.values())
    lt = {n: sum(v for (s, _), v in agg.items() if s == n) for n in lefts}
    rt = {n: sum(v for (_, t), v in agg.items() if t == n) for n in rights}
    lefts = [n for n in lefts if lt[n] > 0]
    rights = [n for n in rights if rt[n] > 0]
    top_pad, gap, node_w, slot = 34, 6, 12, 30  # slot: the least height a node takes, so labels never overlap
    n_max = max(len(lefts), len(rights))
    plot_h = max(240, n_max * 40)
    k = (plot_h - gap * (n_max - 1)) / total
    lw = min(250, int(width * 0.26))
    x1, x2 = lw, width - lw - node_w

    def stack(names: list[str], totals: dict[str, float]) -> tuple[dict[str, float], float]:
        y, pos = float(top_pad), {}
        for n in names:
            pos[n] = y
            y += max(totals[n] * k, slot) + gap
        return pos, y - gap

    (ly, l_end), (ry, r_end) = stack(lefts, lt), stack(rights, rt)
    for pos, end in ((ly, l_end), (ry, r_end)):  # centre the shorter side
        shift = (max(l_end, r_end) - end) / 2
        for n in pos:
            pos[n] += shift
    plot_h = max(l_end, r_end) - top_pad
    color = {n: (f"var(--series-{i + 1})" if i < 4 and n != "Other" else "var(--muted-fill)") for i, n in enumerate(lefts)}
    parts = [f'<text class="tick strong" x="{x1}" y="16">{esc(left)}</text>',
             f'<text class="tick strong" x="{x2 + node_w}" y="16" text-anchor="end">{esc(right)}</text>']
    l_off = dict(ly)
    r_off = dict(ry)
    xm = (x1 + node_w + x2) / 2
    for s in lefts:
        for t in rights:
            v = agg.get((s, t), 0)
            if v <= 0:
                continue
            hh = max(v * k, 1)
            a, b = l_off[s], r_off[t]
            l_off[s] += v * k
            r_off[t] += v * k
            tip = f"{s} → {t}: {money(v, cur)} ({v / total * 100:.1f}%)"
            d = (f"M{x1 + node_w},{a:.1f} C{xm:.1f},{a:.1f} {xm:.1f},{b:.1f} {x2},{b:.1f} V{b + hh:.1f} "
                 f"C{xm:.1f},{b + hh:.1f} {xm:.1f},{a + hh:.1f} {x1 + node_w},{a + hh:.1f} Z")
            parts.append(f'<path class="band" d="{d}" fill="{color[s]}" tabindex="0" data-tip="{esc(tip)}" '
                         f'aria-label="{esc(tip)}"/>')
    for names, pos, totals, x, side in ((lefts, ly, lt, x1, "l"), (rights, ry, rt, x2, "r")):
        for n in names:
            hh = max(totals[n] * k, 2)
            tip = f"{n}: {money(totals[n], cur)} ({totals[n] / total * 100:.1f}%)"
            fill = color[n] if side == "l" else "var(--ink-2)"
            tx, anchor = (x - 8, "end") if side == "l" else (x + node_w + 8, "start")
            ty = pos[n] + hh / 2
            parts.append(f'<g class="mark" tabindex="0" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
                         f'<rect x="{x}" y="{pos[n]:.1f}" width="{node_w}" height="{hh:.1f}" rx="3" fill="{fill}"/>'
                         f'<text class="lbl" x="{tx}" y="{ty - 1:.1f}" text-anchor="{anchor}">{esc(_truncate(n, 30))}</text>'
                         f'<text class="tick" x="{tx}" y="{ty + 12:.1f}" text-anchor="{anchor}">{esc(money(totals[n], cur))}</text></g>')
    h = top_pad + plot_h + 12
    return (f'<svg class="chart flow" style="max-width:{width}px" viewBox="0 0 {width} {h}" role="img" '
            f'aria-label="{esc(label)}">{"".join(parts)}</svg>')


def matrix(rows: list[str], cols: list[str], cells: dict[tuple[str, str], dict[str, float]], *, label: str,
           row_title: str = "", cur: str = "USD", fill: str = "var(--series-3)") -> str:
    """Heat-map table: one cell per (row, column) with a count and a total; cell shade = share of the largest total.
    cells: {(row, col): {count, total}}. Rendered as a real table, so it reads without colour too."""
    vmax = max((c.get("total") or 0 for c in cells.values()), default=0) or 1
    head = f'<th scope="col">{esc(row_title)}</th>' + "".join(f'<th scope="col" class="num">{esc(c)}</th>' for c in cols)
    body = []
    for r in rows:
        tds = []
        for c in cols:
            cell = cells.get((r, c)) or {}
            n, tot = int(cell.get("count") or 0), cell.get("total") or 0
            if not n:
                tds.append('<td class="mx-cell empty-cell">–</td>')
                continue
            share = max(8, round(tot / vmax * 70))
            tip = f"{r} effort, {c}: {n} proposal(s), {money(tot, cur)}/month"
            tds.append(f'<td class="mx-cell" tabindex="0" data-tip="{esc(tip)}" '
                       f'style="background:color-mix(in srgb, {fill} {share}%, transparent)">'
                       f'<b>{esc(money(tot, cur))}</b><span>{n} item{"s" if n != 1 else ""}</span></td>')
        body.append(f'<tr><th scope="row">{esc(r)}</th>{"".join(tds)}</tr>')
    return (f'<div class="tablewrap"><table class="mx" aria-label="{esc(label)}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


SEV_FILL = {"critical": "var(--div-pos)", "high": "var(--div-pos)", "medium": "var(--series-4)", "low": "var(--series-1)"}


def dumbbell(items: list[dict[str, Any]], *, label: str, width: int = 1080, cur: str = "USD") -> str:
    """Usual vs actual, one row per item: a muted dot at the baseline, a coloured dot (by severity) at the value,
    joined by a line whose length is the excess. items: {label, base, value, severity?, tip?}."""
    if not items:
        return '<p class="empty">No data.</p>'
    vmax = max(max(i["value"], i["base"]) for i in items) or 1
    ticks = _nice_ticks(vmax)
    lw = _label_w(width)
    plot = width - lw - 150
    row = BAR + 2 * GAP

    def X(v: float) -> float:
        return lw + plot * v / ticks[-1]

    h = len(items) * row + 46
    parts = [(f'<g transform="translate({lw},12)"><circle r="5" cx="5" cy="-4" fill="var(--muted-fill)"/>'
              '<text class="tick" x="15" y="0">usual (baseline)</text><circle r="5" cx="140" cy="-4" fill="var(--div-pos)"/>'
              '<text class="tick" x="150" y="0">on the day</text></g>')]
    y0 = 28
    for t in ticks:
        parts.append(f'<line class="grid" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{y0}" y2="{y0 + len(items) * row}"/>'
                     f'<text class="tick" x="{X(t):.1f}" y="{y0 + len(items) * row + 16}" text-anchor="middle">{_tick(t)}</text>')
    for k, it in enumerate(items):
        cy = y0 + k * row + row / 2
        fill = SEV_FILL.get(it.get("severity", ""), "var(--div-pos)")
        excess = it["value"] - it["base"]
        pct_s = f" ({excess / it['base'] * 100:+.0f}%)" if it["base"] else ""
        tip = it.get("tip") or f"{it['label']}: {money(it['value'], cur)} vs usual {money(it['base'], cur)}"
        parts.append(f'<g class="mark" tabindex="0" data-tip="{esc(tip)}" aria-label="{esc(tip)}">'
                     f'<rect class="hit" x="0" y="{cy - row / 2:.1f}" width="{width}" height="{row}"/>'
                     f'<text class="lbl" x="{lw - 10}" y="{cy + 4:.1f}" text-anchor="end">{esc(_truncate(it["label"], _chars(lw)))}</text>'
                     f'<line x1="{X(it["base"]):.1f}" x2="{X(it["value"]):.1f}" y1="{cy:.1f}" y2="{cy:.1f}" '
                     f'stroke="{fill}" stroke-opacity=".45" stroke-width="4" stroke-linecap="round"/>'
                     f'<circle cx="{X(it["base"]):.1f}" cy="{cy:.1f}" r="5.5" fill="var(--muted-fill)"/>'
                     f'<circle cx="{X(it["value"]):.1f}" cy="{cy:.1f}" r="6.5" fill="{fill}" stroke="var(--surface)" stroke-width="2"/>'
                     f'<text class="val" x="{max(X(it["value"]), X(it["base"])) + 12:.1f}" y="{cy + 4:.1f}">'
                     f'{esc(signed_money(excess, cur))}{esc(pct_s)}</text></g>')
    return (f'<svg class="chart" style="max-width:{width}px" viewBox="0 0 {width} {h}" role="img" aria-label="{esc(label)}">'
            f'{"".join(parts)}</svg>')
