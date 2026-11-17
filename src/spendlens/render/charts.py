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
from html import escape
from typing import Any

from markupsafe import Markup  # re-exported for plugin dashboard sections

BAR = 16  # bar thickness (<= 24px rule)
GAP = 10  # space between bar rows
LABEL_W = 230  # left label column
VALUE_W = 92  # space reserved for value labels at the bar tip


__all__ = ["Markup", "columns", "diverging_hbars", "esc", "hbars", "legend", "lines", "meter", "money", "segmented"]


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
