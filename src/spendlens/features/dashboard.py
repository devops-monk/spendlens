"""`spendlens dashboard`: one self-contained HTML report built from the latest saved run of every feature.

No AWS calls: it reads the history DB (or the bundled demo DB), so it can be regenerated anywhere -- a laptop, a CI
artifact step, or offline.

Python here only builds the VIEW MODEL (plain data). Layout and copy live in templates/report/dashboard.html.j2,
look in style.css. Plugins can add tabs with registry.add_dashboard_section(id, title, fn(data, cfg) -> html).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from ..common import ConfigOpt, ProfileOpt, Source, err, history_path, load
from ..config import Config
from ..engines import cost as ce
from ..engines import savings as sv
from ..history import HistoryStore, Run, demo_db_path
from ..models import Finding, Status
from ..plugins import Registry, registry
from ..render import charts as ch
from ..render.html import render_template, table_model
from . import guides
from .summary import compliance_pct, summarise

FEATURES = ("cost_diff", "cost_trend", "cost_showback", "forecast", "anomalies", "tags", "rightsize", "waste",
            "savings_hub", "savings_commitments")
TABS = [("overview", "Overview"), ("cost", "Cost diff & trend"), ("showback", "Showback"), ("anomalies", "Anomalies"),
        ("forecast", "Forecast"), ("tags", "Tags"), ("rightsize", "Rightsizing"), ("waste", "Waste"),
        ("savings", "Savings")]
STATUS_FOR = {"alarm": "critical", "info": "warning", "ok": "good", "skip": "neutral"}


class Data:
    """Latest run + findings/cost rows per feature (also passed to plugin dashboard sections)."""

    def __init__(self, store: HistoryStore):
        self.runs: dict[str, Run] = {}
        self.findings: dict[str, list[Finding]] = {}
        self.cost: dict[str, list[dict[str, Any]]] = {}
        for f in FEATURES:
            run = store.latest_run(f)
            if run is None:
                continue
            self.runs[f] = run
            self.findings[f] = store.findings(run.id)
            self.cost[f] = store.cost_rows(run.id)

    @classmethod
    def single(cls, feature: str, run_meta: dict[str, Any], *, cost: list[dict[str, Any]] | None = None,
               findings: list[Finding] | None = None, run_id: int | None = None, account: str | None = None,
               started_at: str | None = None) -> Data:
        """Data for one run held in memory (e.g. the command that just ran), without a history store."""
        d = cls.__new__(cls)
        d.runs = {feature: Run(id=run_id or 0, feature=feature,
                               started_at=started_at or datetime.now(UTC).isoformat(timespec="seconds"),
                               account_id=account, caller_arn=None, params={}, meta=run_meta)}
        d.findings = {feature: findings or []}
        d.cost = {feature: cost or []}
        return d

    def has(self, f: str) -> bool:
        return f in self.runs

    def provenance(self, f: str) -> str:
        r = self.runs[f]
        ci = r.meta.get("CI_PIPELINE_ID") or r.meta.get("GITHUB_RUN_ID")
        run = f"Run #{r.id} · " if r.id else ""
        return f"{run}{r.started_at[:16].replace('T', ' ')} UTC" + (f" · pipeline {ci}" if ci else "")

    @property
    def account(self) -> str | None:
        return next((r.account_id for r in self.runs.values() if r.account_id), None)


def _periods(run: Run) -> list[ce.Period]:
    return [ce.Period.from_dict(p) for p in run.meta.get("periods", [])]


def _totals(rows: list[dict[str, Any]], kind: str, dimension: str | None = None) -> dict[str, float]:
    t, _ = ce.totals_by_group(r for r in rows if r["kind"] == kind and (dimension is None or r["dimension"] == dimension))
    return t


# ------------------------------------------------------------------ view-model sections (plain data only)


def vm_cost_diff(d: Data, cfg: Config, cur: str) -> dict[str, Any] | None:
    if not d.has("cost_diff"):
        return None
    run, rows = d.runs["cost_diff"], d.cost["cost_diff"]
    p0, p1 = _periods(run)
    t = ce.DiffThresholds(cfg.cost.change_alarm_abs, cfg.cost.change_alarm_pct, cfg.cost.min_amount)
    diff = ce.diff(_totals(rows, "p0"), _totals(rows, "p1"), t, cur, p0.days, p1.days)
    flagged = [r for r in diff if r["flagged"]]
    group_by = run.meta.get("group_by", "service")
    changed = [x for x in diff if x["status"] != "flat"]
    up = max(changed, key=lambda r: r["change"], default=None)
    down = min(changed, key=lambda r: r["change"], default=None)
    spend = sorted((x for x in diff if x["compare"] > 0), key=lambda r: -r["compare"])
    total_cmp = sum(x["compare"] for x in diff) or 1
    return {
        "group_by": group_by, "rule": guides.flag_rule(cfg, cur),
        "rule_short": f"≥ {ch.money(cfg.cost.change_alarm_abs, cur)} and ≥ {cfg.cost.change_alarm_pct:g}% change",
        "guide": guides.as_items(guides.cost_diff(cfg, p0.label, p1.label, group_by, run.meta.get("metric"), cur)),
        "up": up if up and up["change"] > 0 else None, "down": down if down and down["change"] < 0 else None,
        "flagged_rows": flagged,
        "spend_bars": [{"label": r["group"], "value": r["compare"],
                        "tip": f"{r['group']}: {ch.money(r['compare'], cur)} in {p1.label} "
                               f"({ch.pct(100 * r['compare'] / total_cmp)} of spend)"}
                       for r in spend[: cfg.dashboard.top_n]],
        "p0": p0.label, "p1": p1.label, "total": ce.diff_total(diff, cur), "flagged": len(flagged),
        "chart_label": f"Cost change by {group_by}",
        "hint": (f"{p0.label} → {p1.label} by {group_by} · {len(flagged)} flagged (≥ {ch.money(cfg.cost.change_alarm_abs, cur)} "
                 f"and ≥ {cfg.cost.change_alarm_pct:g}%) · {d.provenance('cost_diff')}"),
        "bars": [{"label": r["group"], "value": r["change"],
                   "tip": f"{r['group']}: {ch.money(r['base'], cur)} → {ch.money(r['compare'], cur)} "
                          f"({ch.signed_money(r['change'], cur)}, {r['status']}{', flagged' if r['flagged'] else ''})"}
                  for r in [x for x in diff if x["status"] != "flat"][: cfg.dashboard.top_n]],
        "table": table_model(diff, ["group", "base", "compare", "change", "change_pct", "status", "flagged"]),
        "rows": diff,
    }


def vm_trend(d: Data) -> dict[str, Any] | None:
    if not d.has("cost_trend"):
        return None
    run, rows = d.runs["cost_trend"], d.cost["cost_trend"]
    periods = _periods(run)
    pivot = ce.trend([(p, _totals(rows, f"p{i}")) for i, p in enumerate(periods)])
    # <= 4 series: the validated adjacent slots 1-4 (direct labels where they don't collide + legend + table)
    series = [{"name": r["group"], "values": [r[p.label] for p in periods], "slot": i + 1} for i, r in enumerate(pivot[:4])]
    return {"series": series, "x_labels": [p.label for p in periods],
            "guide": guides.as_items(guides.trend(Config(), run.meta.get("group_by", "service"))),
            "hint": f"By {run.meta.get('group_by', 'service')} · {d.provenance('cost_trend')}",
            "table": table_model(pivot, ["group", *[p.label for p in periods], "total", "last_change", "last_change_pct"])}


def vm_showback(d: Data, cfg: Config, cur: str) -> list[dict[str, Any]]:
    if not d.has("cost_showback"):
        return []
    run, rows = d.runs["cost_showback"], d.cost["cost_showback"]
    period = (run.meta.get("periods") or [{}])[0].get("label", "")
    out = []
    for key in run.meta.get("tag_keys", []):
        data = ce.showback(_totals(rows, "p0", f"tag:{key}"), key, cfg.budgets.per_tag.get(key), cur)
        untagged = next((r for r in data if r["tag_value"] == "(untagged)"), None)
        out.append({
            "key": key, "hint": f"{period} · {d.provenance('cost_showback')}",
            "guide": guides.as_items(guides.showback(cfg, key)),
            "untagged_pct": ch.pct(untagged["share_pct"]) if untagged else None,
            "bars": [{"label": r["tag_value"], "value": r["amount"], "muted": r["tag_value"] == "(untagged)",
                       "tip": f"{r['tag_value']}: {ch.money(r['amount'], cur)} ({ch.pct(r['share_pct'])})"
                              + (f", budget {ch.money(r['budget'], cur)}" if r["budget"] is not None else "")}
                      for r in data[: cfg.dashboard.top_n]],
            "table": table_model(data, ["tag_value", "amount", "share_pct", "budget", "budget_variance", "over_budget"]),
        })
    return out


def vm_forecast(d: Data, cur: str) -> dict[str, Any] | None:
    if not d.has("forecast"):
        return None
    fs = d.findings["forecast"]
    meters = []
    for f in fs:
        x = f.dimensions
        limit, fc_ = x.get("limit"), x.get("forecast")
        meters.append({
            "name": "Account (projected month end)" if f.resource == "account" else f.resource.removeprefix("budget:"),
            "pct": (fc_ / limit * 100) if limit and fc_ is not None else None,
            "status": STATUS_FOR.get(f.status.value, "neutral"),
            "detail": (f"{ch.money(fc_, cur)} of {ch.money(limit, cur)} · {x.get('label')}" if limit
                       else f"projected {ch.money(fc_, cur)} · no budget set"),
        })
    rows = [{"status": f.status.value, **{k: f.dimensions.get(k) for k in
             ("scope", "limit", "actual", "forecast", "forecast_pct_of_limit", "method")}} for f in fs]
    acct = next((f.dimensions for f in fs if f.resource == "account"), None)
    return {"meters": meters, "account": acct,
            "hint": f"{(acct or {}).get('method', '').replace('_', ' ')} · {d.provenance('forecast')}",
            "table": table_model(rows, list(rows[0]) if rows else [])}


def vm_anomalies(d: Data, cur: str) -> dict[str, Any] | None:
    if not d.has("anomalies"):
        return None
    rows = [{**f.dimensions, "severity": f.severity.value} for f in d.findings["anomalies"]]
    return {
        "count": len(rows), "hint": f"Daily spend above its trailing baseline · {d.provenance('anomalies')}",
        "bars": [{"label": f"{r['group']} ({r['date']})", "value": r["delta"],
                   "tip": f"{r['group']} on {r['date']}: {ch.money(r['cost'], cur)} vs baseline "
                          f"{ch.money(r['baseline'], cur)} (z={r['z']}, {r['severity']})"}
                  for r in rows if r.get("direction") == "spike"],
        "table": table_model(rows, ["date", "group", "direction", "cost", "baseline", "delta", "delta_pct", "z", "severity"]),
    }


def vm_tags(d: Data, cfg: Config) -> dict[str, Any] | None:
    if not d.has("tags"):
        return None
    fs = d.findings["tags"]
    by_check = summarise(fs, by=("check",))
    meters = []
    for r in by_check:
        p = r["compliance_pct"]
        meters.append({"name": (r["check"] or "-").removeprefix("tags_").replace("_", " ").capitalize(), "pct": p,
                       "status": "neutral" if p is None else "good" if p >= 95 else "warning" if p >= 80 else "critical",
                       "detail": f"{ch.pct(p)} compliant · {r['alarm']} alarm(s)"})
    by_type = [r for r in summarise(fs, by=("resource_type",)) if r["alarm"]][: cfg.dashboard.top_n]
    alarms = [f.model_dump(mode="json") for f in fs if f.status is Status.ALARM]
    return {
        "overall": compliance_pct(sum(r["ok"] for r in by_check), sum(r["alarm"] for r in by_check)),
        "meters": meters,
        "by_type": [{"label": r["resource_type"], "value": r["alarm"],
                     "tip": f"{r['resource_type']}: {r['alarm']} alarm(s), {ch.pct(r['compliance_pct'])} compliant"}
                    for r in by_type],
        "hint": f"Policy: mandatory {', '.join(cfg.tagging.mandatory)} · {d.provenance('tags')}",
        "table": table_model(alarms, ["check", "resource_type", "resource", "reason", "account_id", "region"],
                             cfg.dashboard.table_rows),
    }


def vm_findings(d: Data, feature: str, cfg: Config, cur: str) -> dict[str, Any] | None:
    if not d.has(feature):
        return None
    fs = [f for f in d.findings[feature] if f.status is Status.ALARM]
    by_service: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for f in fs:
        by_service[f.service or "-"] += f.est_monthly_saving or 0
        counts[f.service or "-"] += 1
    rows = sorted((f.model_dump(mode="json") for f in fs), key=lambda r: -(r["est_monthly_saving"] or 0))
    return {
        "hint": f"{len(fs)} alarm(s) · est. {ch.money(sum(by_service.values()), cur)}/month · {d.provenance(feature)}",
        "bars": [{"label": s, "value": round(v, 2), "tip": f"{s}: {counts[s]} finding(s), est. {ch.money(v, cur)}/month"}
                  for s, v in sorted(by_service.items(), key=lambda kv: -kv[1]) if v > 0][: cfg.dashboard.top_n],
        "table": table_model(rows, ["est_monthly_saving", "service", "control", "resource", "reason", "region"],
                             cfg.dashboard.table_rows),
    }


def merged_savings(d: Data) -> tuple[list[dict[str, Any]], list[str]] | None:
    """Every saving across rightsize, waste, Cost Optimization Hub and commitments, de-duplicated (largest first)."""
    sources = [f for f in ("rightsize", "waste", "savings_hub", "savings_commitments") if d.has(f)]
    if not sources:
        return None
    spendlens_rows, hub_rows = [], []
    for feat in sources:
        for f in d.findings[feat]:
            if f.status is Status.ALARM and f.est_monthly_saving:
                row = {"resource": f.resource, "control": f.control, "service": f.service or "-",
                       "est_monthly_saving": f.est_monthly_saving, "reason": f.reason}
                (hub_rows if feat == "savings_hub" else spendlens_rows).append(row)
    return sv.merge_savings(spendlens_rows, hub_rows)


def savings_category(r: dict[str, Any]) -> str:
    return ("AWS Cost Optimization Hub" if r["source"] == "cost_optimization_hub" else
            "Commitments" if r["control"] == "commitment_coverage" else "spendlens rightsizing & waste")


def vm_savings(d: Data, cfg: Config, cur: str) -> dict[str, Any] | None:
    if (m := merged_savings(d)) is None:
        return None
    merged, notes = m
    total = round(sum(r["est_monthly_saving"] for r in merged), 2)
    by_cat: dict[str, float] = defaultdict(float)
    for r in merged:
        by_cat[savings_category(r)] += r["est_monthly_saving"]
    order = ["spendlens rightsizing & waste", "AWS Cost Optimization Hub", "Commitments"]
    return {
        "amount": total, "total": ch.money(total, cur), "yearly": ch.money(total * 12, cur), "count": len(merged),
        "parts": [{"label": k, "value": round(by_cat[k], 2), "slot": i + 1, "ink": "light"}
                  for i, k in enumerate(order) if by_cat.get(k)],
        "notes": notes,
        "table": table_model(merged, ["est_monthly_saving", "source", "service", "control", "resource", "reason",
                                      "also_flagged_by"], cfg.dashboard.table_rows),
    }


def view_model(d: Data, cfg: Config, source: Source) -> dict[str, Any]:
    cur = cfg.pricing.currency
    vm: dict[str, Any] = {
        "currency": cur, "source": source.value, "account": d.account,
        "runs": {k: {"id": r.id, "started_at": r.started_at} for k, r in d.runs.items()},
        "cost_diff": vm_cost_diff(d, cfg, cur), "trend": vm_trend(d), "showback": vm_showback(d, cfg, cur),
        "forecast": vm_forecast(d, cur), "anomalies": vm_anomalies(d, cur), "tags": vm_tags(d, cfg),
        "rightsize": vm_findings(d, "rightsize", cfg, cur), "waste": vm_findings(d, "waste", cfg, cur),
        "savings": vm_savings(d, cfg, cur),
        "guides": {"overview": guides.as_items(guides.overview()),
                   "anomalies": guides.as_items(guides.anomalies(cfg, cur)),
                   "forecast": guides.as_items(guides.forecast(cfg)), "tags": guides.as_items(guides.tags(cfg)),
                   "rightsize": guides.as_items(guides.findings(cfg, "rightsize")),
                   "waste": guides.as_items(guides.findings(cfg, "waste")),
                   "savings": guides.as_items(guides.savings())},
    }
    # overview: exactly one hero figure + stat tiles
    tiles, hero = [], None
    if cd := vm["cost_diff"]:
        t = cd["total"]
        hero = {"label": f"Spend in {cd['p1']}", "value": ch.money(t["compare"], cur),
                "direction": "up" if t["change"] > 0 else "down",
                "delta": f"{ch.signed_money(t['change'], cur)} ({ch.pct(t['change_pct'], True)}) vs {cd['p0']}"}
        tiles.append({"label": "Flagged cost changes", "value": str(cd["flagged"]),
                      "detail": f"≥ {ch.money(cfg.cost.change_alarm_abs, cur)} and ≥ {cfg.cost.change_alarm_pct:g}% "
                                f"vs {cd['p0']}"})
    if (fc := vm["forecast"]) and (a := fc["account"]):
        tiles.append({"label": "Projected month end", "value": ch.money(a.get("forecast"), cur),
                      "detail": f"budget {ch.money(a.get('limit'), cur)} · {a.get('label')}" if a.get("limit") else "no budget set"})
    if s := vm["savings"]:
        tiles.append({"label": "Potential saving", "value": f"{s['total']}/mo", "detail": f"{s['yearly']} a year"})
    if (g := vm["tags"]) and g["overall"] is not None:
        tiles.append({"label": "Tag compliance", "value": ch.pct(g["overall"]),
                      "detail": f"mandatory: {', '.join(cfg.tagging.mandatory)}"})
    if an := vm["anomalies"]:
        tiles.append({"label": "Cost anomalies", "value": str(an["count"]), "detail": "in the last check"})
    vm["hero"], vm["tiles"] = hero, tiles
    return vm


def build(d: Data, cfg: Config, source: Source) -> str:
    vm = view_model(d, cfg, source)
    extra = []
    for sid, title, fn in registry.dashboard_sections:
        try:
            extra.append({"id": sid, "title": title, "html": ch.Markup(fn(d, cfg))})
        except Exception as e:  # noqa: BLE001 - a plugin section must not break the report
            extra.append({"id": sid, "title": title, "html": ch.Markup(f'<p class="empty">Section failed: {ch.esc(e)}</p>')})
    demo = " · DEMO DATA" if source is Source.demo else ""
    footer = (f"Generated by spendlens {datetime.now(UTC):%Y-%m-%d %H:%M UTC} from saved runs ({source.value}){demo}. "
              "Savings are indicative estimates for human review; spendlens never changes resources. Runs: "
              + ", ".join(f"{k} #{r.id}" for k, r in d.runs.items()))
    return render_template(
        "dashboard.html.j2", title=cfg.dashboard.title, footer=footer,
        subtitle=(f"AWS account {d.account}" if d.account else "AWS") + demo,
        tabs=TABS + [(s["id"], s["title"]) for s in extra], vm={**vm, "extra_sections": extra},
        view_model=vm,  # embedded as JSON for tools / the AI assistant
    )


def single_report(kind: str, cfg: Config, meta: Any, run_meta: dict[str, Any], *,
                  cost: list[dict[str, Any]] | None = None, findings: list[Finding] | None = None) -> str:
    """`--format html` for cost diff / trend / showback / anomalies / forecast: the dashboard's charts and guides
    for the run that was just made (or replayed), plus the full table."""
    d = Data.single(kind, run_meta, cost=cost, findings=findings, run_id=(meta.extra or {}).get("run_id"),
                    account=meta.account_id,
                    started_at=meta.generated_at.astimezone(UTC).isoformat(timespec="seconds"))
    vm = view_model(d, cfg, Source.last)
    gen = meta.generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    src = (meta.extra or {}).get("source", "")
    return render_template(
        "single.html.j2", title=meta.title, kind=kind, vm=vm, view_model=vm,
        subtitle=" · ".join(x for x in [f"AWS account {meta.account_id}" if meta.account_id else "", gen] if x),
        footer=f"Generated by spendlens · {gen}" + (f" · source {src}" if src else "")
               + " · Cost Explorer data (read-only); estimates are for review",
    )


def write_report(html: str, out: Path | None) -> None:
    if out is None:
        typer.echo(html)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    err.print(f"[green]Wrote[/] {out}")


def dashboard(
    out: Annotated[Path, typer.Option("--out", "-o", help="Output HTML file.")] = Path("spendlens-report.html"),
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    source: Annotated[Source, typer.Option(
        "--source", help="live = reuse today's saved runs and run every other section now; last = saved runs only, "
                         "no AWS calls; demo = bundled sample DB.")] = Source.live,
    refresh: Annotated[bool, typer.Option("--refresh", help="Re-run every section live now, even ones run today.")] = False,
) -> None:
    """Build a self-contained HTML dashboard covering every section, at most a day old.

    A section already run today is read from your history; the others are run live first (read-only AWS calls
    and Cost Explorer). --refresh re-runs them all; --source last never calls AWS.
    Examples:
      spendlens dashboard -o report.html
      spendlens dashboard --refresh -o report.html
      spendlens dashboard --source last -o report.html
    """
    cfg = load(config)
    path = demo_db_path() if source is Source.demo else history_path(cfg)
    if source is Source.live:
        from .. import refresh as rf

        rf.ensure_fresh(path, FEATURES, config=config, profile=profile, refresh=refresh)
    elif refresh:
        raise typer.BadParameter("--refresh runs sections live: drop --source last/demo.", param_hint="--refresh")
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/] (run some checks first, or try --source demo)")
        raise typer.Exit(1) from e
    with store:
        data = Data(store)
    html = build(data, cfg, Source.last if source is Source.live else source)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    err.print(f"[green]Wrote[/] {out} ({len(html) // 1024} KiB, {len(data.runs)} section source(s))")


def register(reg: Registry) -> None:
    reg.add_command(dashboard, "dashboard")
