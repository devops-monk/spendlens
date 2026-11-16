"""HTML rendering: Jinja2 templates (src/spendlens/templates/report/*.j2) fed by plain view models.

Design:
  * Python builds a view model of plain data (numbers, rows, chart items) -- no markup.
  * Templates own structure and copy; style.css / app.js own look and behaviour. All are package files, so a
    design/brand team can change them without touching Python. Autoescape is on: values are escaped by default.
  * Charts are server-side SVG from render/charts.py, exposed to templates as `charts.*` (safe Markup). The
    page works without JavaScript, prints, and opens offline (CI artifacts, email).
  * The page embeds its view model as JSON (<script type="application/json" id="spendlens-data">) so tools can
    read the report's numbers without scraping HTML.

Brand colours (finops.yaml `dashboard.*`) style chrome only. Chart data colours are a fixed palette validated
for colour-vision deficiency in light and dark mode (dataviz validator: categorical slots 1-4 adjacent CVD ΔE >= 8.4,
blue/red diverging ΔE 21.6); aqua/yellow sit below 3:1 on the light surface, so charts using them carry direct
labels and a table view.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from ..config import DashboardConfig
from ..models import ReportMeta
from . import charts as ch

THEME: DashboardConfig = DashboardConfig()  # set by common.load() from finops.yaml

HEADERS = {
    "est_monthly_saving": "Saving / month", "account_id": "Account", "resource_type": "Resource type",
    "change_pct": "Change %", "change_pct_daily": "Daily-rate change %", "share_pct": "Share %",
    "compliance_pct": "Compliance %", "coverage_pct": "Coverage %", "target_pct": "Target %",
    "forecast_pct_of_limit": "Forecast % of limit", "delta_pct": "Change %", "also_flagged_by": "Also flagged by",
    "budget_variance": "Over / under budget", "over_budget": "Over budget", "tag_value": "Tag value",
    "tag_key": "Tag key", "last_change": "Last change", "last_change_pct": "Last change %",
    "uncovered_to_target": "Uncovered to target", "on_demand": "On-demand", "z": "z-score",
}
NOWRAP = {"status", "account_id", "region", "service", "check", "severity", "date", "currency", "source", "flagged"}


def set_theme(theme: DashboardConfig) -> None:
    global THEME
    THEME = theme


def header(col: str) -> str:
    return HEADERS.get(col) or (col[:1].upper() + col[1:]).replace("_", " ")


# ------------------------------------------------------------------ table view model


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _cell(col: str, v: Any) -> dict[str, Any]:
    if v is None:
        return {"text": "", "cls": "", "sort": ""}
    if isinstance(v, (dict, list)):
        return {"text": json.dumps(v, default=str) if v else "", "cls": "wrap", "sort": None}
    if col == "status":
        return {"text": str(v), "cls": f"st {v}", "sort": None}
    if _is_num(v):
        txt = f"{v:,.2f}" if isinstance(v, float) else f"{v:,}"
        if col.endswith("pct"):
            txt = f"{v:+.1f}%" if ("change" in col or "delta" in col) else f"{v:.1f}%"
        return {"text": txt, "cls": "num", "sort": v}
    return {"text": str(v), "cls": "nw" if col in NOWRAP else "wrap", "sort": None}


def table_model(records: list[dict[str, Any]], cols: list[str], limit: int | None = None) -> dict[str, Any]:
    shown = records[:limit] if limit else records
    return {
        "cols": [{"key": c, "label": header(c), "numeric": any(_is_num(r.get(c)) for r in shown)} for c in cols],
        "rows": [[_cell(c, r.get(c)) for c in cols] for r in shown],
        "total": len(records),
    }


# ------------------------------------------------------------------ templates


class _Charts:
    """Chart helpers exposed to templates; each returns safe Markup (inputs are escaped inside charts.py)."""

    def __getattr__(self, name: str) -> Any:
        fn = getattr(ch, name)

        def wrapped(*a: Any, **kw: Any) -> Markup:
            return Markup(fn(*a, **kw))

        return wrapped

    @staticmethod
    def count(v: float, _cur: str = "") -> str:
        return f"{v:,.0f}"


def _template_dir() -> str:
    return str(resources.files("spendlens.templates").joinpath("report"))


@cache
def environment() -> Environment:
    env = Environment(loader=FileSystemLoader(_template_dir()), autoescape=True, undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True)
    env.globals["charts"] = _Charts()
    return env


@cache
def assets() -> dict[str, Markup]:
    d = resources.files("spendlens.templates").joinpath("report")
    return {"css": Markup(d.joinpath("style.css").read_text()), "js": Markup(d.joinpath("app.js").read_text())}


def data_json(view_model: Any) -> Markup:
    """JSON safe to embed in <script type="application/json">: <, > and & are \\u-escaped (still valid JSON)."""
    text = json.dumps(view_model, default=str, separators=(",", ":"))
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def render_template(name: str, *, title: str, subtitle: str = "", footer: str = "",
                    tabs: list[tuple[str, str]] | None = None, view_model: Any = None,
                    theme: DashboardConfig | None = None, **ctx: Any) -> str:
    return environment().get_template(name).render(
        title=title, subtitle=subtitle, footer=footer, tabs=tabs, theme=theme or THEME, assets=assets(),
        data_json=data_json(view_model) if view_model is not None else None, **ctx,
    )


# ------------------------------------------------------------------ findings report (tags / rightsize / waste)

STATUS_ORDER = ["alarm", "error", "info", "skip", "ok"]
STATUS_LABEL = {"alarm": "Alarm", "error": "Error", "info": "Info", "skip": "Skip", "ok": "OK"}
_FINDING_KEYS = {"control", "resource", "status"}


def is_findings(records: list[dict[str, Any]]) -> bool:
    return bool(records) and all(_FINDING_KEYS <= r.keys() for r in records)


def _short(resource: str) -> str:
    """Readable resource name from an ARN or path: the last ':' or '/' segment."""
    tail = resource.rsplit(":", 1)[-1]
    return tail.rsplit("/", 1)[-1] or resource


def _money(v: float) -> str:
    return f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def findings_model(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Group findings by control, worst first: the structure of the findings report."""
    rank = {s: i for i, s in enumerate(STATUS_ORDER)}
    totals = dict.fromkeys(STATUS_ORDER, 0)
    by_ctl: dict[str, dict[str, Any]] = {}
    for i, r in enumerate(records):
        st = str(r.get("status") or "info")
        totals[st] = totals.get(st, 0) + 1
        saving = r.get("est_monthly_saving") if st == "alarm" else None
        c = by_ctl.setdefault(r["control"], {
            "id": r["control"], "title": r.get("title") or r["control"], "service": r.get("service") or "",
            "resource_type": r.get("resource_type") or "", "counts": dict.fromkeys(STATUS_ORDER, 0),
            "saving": 0.0, "rows": []})
        c["counts"][st] = c["counts"].get(st, 0) + 1
        c["saving"] += float(saving or 0)
        c["rows"].append({
            "i": i, "status": st, "label": STATUS_LABEL.get(st, st), "resource": r["resource"],
            "name": _short(r["resource"]),
            "reason": r.get("reason") or "", "account": r.get("account_id") or "", "region": r.get("region") or "",
            "severity": r.get("severity") or "", "saving": float(saving) if saving else None,
            "saving_text": _money(float(saving)) if saving else ""})
    controls = []
    for c in by_ctl.values():
        c["rows"].sort(key=lambda x: (rank.get(x["status"], 9), -(x["saving"] or 0), x["name"]))
        c["worst"] = next((s for s in STATUS_ORDER if c["counts"].get(s)), "ok")
        c["saving_text"] = _money(c["saving"]) if c["saving"] else ""
        c["statuses"] = " ".join(s for s in STATUS_ORDER if c["counts"].get(s))
        controls.append(c)
    controls.sort(key=lambda c: (rank.get(c["worst"], 9), -c["counts"]["alarm"], -c["saving"], c["title"]))
    judged = totals["ok"] + totals["alarm"]
    saving = sum(c["saving"] for c in controls)
    alarm_rows = [dict(row, control=c["title"]) for c in controls for row in c["rows"]
                  if row["status"] == "alarm" and row["saving"]]
    top = sorted(alarm_rows, key=lambda x: -x["saving"])[:5]
    return {
        "controls": controls,
        "totals": totals,
        "total": len(records),
        "resources": len({r["resource"] for r in records}),
        "alarm_resources": len({r["resource"] for r in records if r.get("status") == "alarm"}),
        "compliance": round(100 * totals["ok"] / judged, 1) if judged else None,
        "saving": saving, "saving_text": _money(saving) if saving else "$0",
        "saving_year": _money(saving * 12) if saving else "",
        "services": sorted({c["service"] for c in controls if c["service"]}),
        "top": top,
        "segments": [{"status": s, "label": STATUS_LABEL[s], "n": totals[s],
                      "pct": round(100 * totals[s] / len(records), 2) if records else 0}
                     for s in STATUS_ORDER if totals.get(s)],
    }


def render_findings_html(records: list[dict[str, Any]], cols: list[str], meta: ReportMeta) -> str:
    """`--format html` for control-based commands: summary, filters and one section per check."""
    gen = (meta.generated_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    hide = {"est_monthly_saving_total", "currency"}  # already shown as summary tiles
    extra = {k: v for k, v in meta.extra.items()
             if k not in hide and not isinstance(v, (dict, list)) and v not in (None, "")}
    scope = [f"{k.replace('_', ' ')}: {v}" for k, v in extra.items()]
    sub = [f"Account {meta.account_id}" if meta.account_id else "", gen]
    return render_template(
        "findings.html.j2", title=meta.title, subtitle=" · ".join(x for x in sub if x),
        footer=f"Generated by spendlens · {gen} · read-only: spendlens never changes resources; "
               "savings are estimates for review",
        fm=findings_model(records), scope=scope, csv_columns=cols,
        view_model={"meta": meta.model_dump(mode="json"), "columns": cols, "rows": records},
    )


def render_html(records: list[dict[str, Any]], cols: list[str], meta: ReportMeta) -> str:
    """`--format html` for any command: a findings report for control results, else a branded sortable table."""
    if is_findings(records):
        return render_findings_html(records, cols, meta)
    extra = {k: v for k, v in meta.extra.items() if not isinstance(v, (dict, list))}
    facts = " · ".join(f"{k}: {v}" for k, v in extra.items())
    gen = (meta.generated_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return render_template(
        "table.html.j2", title=meta.title, heading=meta.title, facts=facts,
        subtitle=f"{len(records)} rows" + (f" · account {meta.account_id}" if meta.account_id else ""),
        footer=f"Generated by spendlens · {gen}", table=table_model(records, cols),
        view_model={"meta": meta.model_dump(mode="json"), "columns": cols, "rows": records},
    )
