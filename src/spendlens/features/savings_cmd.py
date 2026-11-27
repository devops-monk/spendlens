"""`spendlens savings`: AWS Cost Optimization Hub, commitment coverage and one de-duplicated savings summary."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import typer

from ..common import (
    ConfigOpt,
    FormatOpt,
    OutOpt,
    ProfileOpt,
    SetOpt,
    Source,
    SourceOpt,
    emit,
    err,
    findings_from_source,
    history_path,
    load,
    steampipe_rows,
)
from ..config import Config
from ..engines import cost as ce
from ..engines import savings as sv
from ..models import Finding, Severity, Status
from ..plugins import Registry
from ..render import Format

app = typer.Typer(help="Savings opportunities: AWS Cost Optimization Hub, Savings Plan/RI coverage, summary.",
                  no_args_is_help=True)

HUB_COLUMNS = ["est_monthly_saving", "action", "service", "resource", "reason", "account_id", "region"]


# ------------------------------------------------------------------ Cost Optimization Hub


def hub_live(cfg: Config) -> list[Finding]:
    rows = steampipe_rows(cfg, sv.HUB_SQL, what="Cost Optimization Hub query",
                          hint="Opt in to Cost Optimization Hub and grant cost-optimization-hub:ListRecommendations.")
    out = []
    for r in rows:
        saving = float(r["saving"] or 0)
        if saving < cfg.savings.hub_min_saving:
            continue
        out.append(Finding(
            control=f"coh_{(r.get('action_type') or 'other').lower()}", title="Cost Optimization Hub recommendation",
            check="savings", service=sv.service_from_arn(r.get("resource_arn")),
            resource=r.get("resource_arn") or r.get("resource_id") or "-", account_id=r.get("account_id"),
            region=r.get("region"), status=Status.ALARM,
            severity=Severity.HIGH if saving >= 100 else Severity.MEDIUM if saving >= 20 else Severity.LOW,
            reason=sv.hub_reason(r), est_monthly_saving=round(saving, 2),
            dimensions={"action": r.get("action_type"), "effort": r.get("implementation_effort"),
                        "saving_pct": float(r["saving_pct"]) if r.get("saving_pct") is not None else None,
                        "current": r.get("current_resource_type"), "recommended": r.get("recommended_resource_type"),
                        "restart_needed": r.get("restart_needed"), "source": "cost_optimization_hub"},
        ))
    return out


@app.command("hub")
def hub(
    action: Annotated[list[str] | None, typer.Option("--action", help="Filter action types, e.g. Rightsize, Stop, PurchaseSavingsPlans.")] = None,
    min_saving: Annotated[float | None, typer.Option("--min-saving", help="Only recommendations saving at least this per month.")] = None,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
) -> None:
    """AWS Cost Optimization Hub recommendations (rightsizing, Graviton, idle, commitments) with AWS's estimates."""
    cfg = load(config, profile, sets)
    findings, meta = findings_from_source("savings_hub", source, cfg, lambda: hub_live(cfg))
    wanted = {a.lower() for a in action or []}
    rows = [f for f in findings if (not wanted or str(f.dimensions.get("action", "")).lower() in wanted)
            and (min_saving is None or (f.est_monthly_saving or 0) >= min_saving)]
    rows.sort(key=lambda f: -(f.est_monthly_saving or 0))
    meta.title = "Cost Optimization Hub recommendations"
    emit([{**f.model_dump(mode="json"), "action": f.dimensions.get("action")} for f in rows], fmt, out, meta.title,
         columns=HUB_COLUMNS, meta=meta)
    total = sum(f.est_monthly_saving or 0 for f in rows)
    err.print(f"[bold]{len(rows)} recommendation(s) · est. saving {cfg.pricing.currency} {total:,.2f}/month[/]")


# ------------------------------------------------------------------ commitments


def commitments_live(cfg: Config, today: date) -> list[Finding]:
    period = ce.last_full_months(today)[1]
    rows = steampipe_rows(cfg, sv.PURCHASE_TYPE_SQL, {"start": period.start.isoformat(), "end": period.end.isoformat()},
                          what="Purchase-type cost query", hint="The role needs ce:GetCostAndUsage.")
    s = cfg.savings
    out = []
    for c in sv.coverage(rows, s.commitment_services, s.commitment_coverage_target_pct, s.commitment_min_on_demand,
                         cfg.pricing.reservation_saving_pct):
        out.append(Finding(
            control="commitment_coverage", title="Savings Plan / RI coverage (amortized spend mix)", check="savings",
            resource=f"service:{c['service']}", status=Status(c["status"]),
            severity=Severity.MEDIUM if c["status"] == "alarm" else Severity.LOW,
            reason=f"{c['service']}: {c['coverage_pct']}% of {period.label} eligible spend committed "
                   f"(target {c['target_pct']}%); on-demand {c['on_demand']:,.2f}.",
            est_monthly_saving=c["est_monthly_saving"], dimensions={**c, "period": period.label},
        ))
    try:
        plans = steampipe_rows(cfg, sv.SAVINGS_PLANS_SQL, what="Savings Plans query")
    except typer.Exit:
        plans = []
    horizon = datetime.now(UTC) + timedelta(days=s.savings_plan_expiration_warning_days)
    for p in plans:
        end = p.get("end_time")
        if isinstance(end, str):
            end = datetime.fromisoformat(end)
        expiring = end is not None and end <= horizon
        out.append(Finding(
            control="savings_plan_expiring", title="Savings Plan expiry", check="savings",
            resource=p.get("arn") or p.get("savings_plan_id"), account_id=p.get("account_id"), region=p.get("region"),
            status=Status.ALARM if expiring else Status.OK, severity=Severity.HIGH if expiring else Severity.LOW,
            reason=f"{p.get('savings_plan_type')} plan ({p.get('commitment')}/h) ends {end:%Y-%m-%d}." if end else "No end date.",
            dimensions={"type": p.get("savings_plan_type"), "commitment_per_hour": p.get("commitment"),
                        "end_time": end.isoformat() if end else None},
        ))
    return out


@app.command("commitments")
def commitments(
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    today: Annotated[str | None, typer.Option("--today", hidden=True)] = None,
) -> None:
    """Savings Plan / RI coverage per service (last full month, amortized) and Savings Plans about to expire."""
    cfg = load(config, profile, sets)
    t = date.fromisoformat(today) if today else date.today()
    findings, meta = findings_from_source("savings_commitments", source, cfg, lambda: commitments_live(cfg, t))
    cov = [{**f.dimensions} for f in findings if f.control == "commitment_coverage"]
    meta.title = "Commitment coverage (amortized spend mix)"
    emit(cov, fmt, out, meta.title, columns=["status", "service", "coverage_pct", "target_pct", "on_demand", "committed",
                                             "spot", "uncovered_to_target", "est_monthly_saving"], meta=meta)
    for f in findings:
        if f.control == "savings_plan_expiring" and f.status is Status.ALARM:
            err.print(f"[yellow]Expiring: {f.reason}[/]")
    total = sum(f.est_monthly_saving or 0 for f in findings)
    err.print(f"[bold]Est. saving from closing coverage gaps: {cfg.pricing.currency} {total:,.2f}/month[/] "
              f"(at {cfg.pricing.reservation_saving_pct:.0%} commitment discount; confirm with AWS purchase recommendations)")


# ------------------------------------------------------------------ summary


SUMMARY_FEATURES = ("rightsize", "waste", "savings_hub", "savings_commitments")
SOURCE_HINTS = {
    "rightsize": ("oversized resources and company standards", "spendlens rightsize"),
    "waste": ("idle and orphaned resources", "spendlens waste"),
    "savings_hub": ("AWS Cost Optimization Hub recommendations", "spendlens savings hub"),
    "savings_commitments": ("Savings Plan / RI purchase recommendations", "spendlens savings commitments"),
}
@app.command("summary")
def summary(
    by: Annotated[str, typer.Option("--by", help="Group by: source | service | control | none.")] = "source",
    top: Annotated[int | None, typer.Option("--top", help="With --by none: largest N items.")] = 25,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    source: Annotated[Source, typer.Option(
        "--source", help="live = reuse today's saved runs and run the other sources now; last = saved runs only, "
                         "no AWS calls; demo = bundled sample DB.")] = Source.live,
    refresh: Annotated[bool, typer.Option(
        "--refresh", help="Run every source live now, even ones already checked today.")] = False,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
) -> None:
    """One de-duplicated view of every saving from rightsize, waste, Cost Optimization Hub and commitments.

    Every total covers all four sources: a source already checked today is read from your history, the others
    are run live first (read-only AWS calls; Cost Explorer for commitments). --refresh runs all four now;
    --source last never calls AWS.
    Examples:
      spendlens savings summary
      spendlens savings summary --by service --refresh
      spendlens savings summary --source last
    """
    from ..history import HistoryStore, demo_db_path

    if by not in ("source", "service", "control", "none"):
        raise typer.BadParameter("Use source, service, control or none.", param_hint="--by")
    cfg = load(config)
    path = demo_db_path() if source is Source.demo else history_path(cfg)
    fetched: list[str] = []
    failed: dict[str, str] = {}
    if source is Source.live:
        from .. import refresh as rf

        fetched, failed = rf.ensure_fresh(path, SUMMARY_FEATURES, config=config, profile=profile, refresh=refresh)
    elif refresh:
        raise typer.BadParameter("--refresh runs sources live: drop --source last/demo.", param_hint="--refresh")
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e
    spendlens_rows: list[dict[str, Any]] = []
    hub_rows: list[dict[str, Any]] = []
    used = []
    missing: list[str] = []
    with store:
        for feat in SUMMARY_FEATURES:
            run = store.latest_run(feat)
            if not run:
                missing.append(feat)
                continue
            how = "run now" if feat in fetched else f"saved {run.started_at[:10]}"
            used.append(f"{feat} #{run.id} ({how})")
            for f in store.findings(run.id):
                if f.status is not Status.ALARM or not f.est_monthly_saving:
                    continue
                row = {"resource": f.resource, "control": f.control, "service": f.service or "-",
                       "est_monthly_saving": f.est_monthly_saving, "reason": f.reason, "feature": feat}
                (hub_rows if feat == "savings_hub" else spendlens_rows).append(row)
    if not used:
        err.print("[red]No saved runs yet.[/] savings summary adds up saved runs; run these first:")
        for what, cmd in SOURCE_HINTS.values():
            err.print(f"  [bold]{cmd}[/]  [dim]# {what}[/]")
        raise typer.Exit(1)
    merged, notes = sv.merge_savings(spendlens_rows, hub_rows)
    if by == "none":
        rows: list[dict[str, Any]] = merged[: top or None]
        cols = ["est_monthly_saving", "source", "service", "control", "resource", "reason", "also_flagged_by"]
    else:
        key = {"source": "source", "service": "service", "control": "control"}[by]
        agg: dict[str, dict[str, Any]] = {}
        for r in merged:
            a = agg.setdefault(r[key], {by: r[key], "items": 0, "est_monthly_saving": 0.0, "_top": None})
            a["items"] += 1
            a["est_monthly_saving"] = round(a["est_monthly_saving"] + r["est_monthly_saving"], 2)
            if a["_top"] is None or r["est_monthly_saving"] > a["_top"]["est_monthly_saving"]:
                a["_top"] = r
        rows = sorted(agg.values(), key=lambda r: -r["est_monthly_saving"])
        for a in rows:  # the single biggest item in each group: what to look at first
            t = a.pop("_top")
            a["biggest_item"] = f"{t['resource']} ({t['control']})" if t else ""
            a["biggest_saving"] = t["est_monthly_saving"] if t else 0.0
        cols = [by, "items", "est_monthly_saving", "biggest_saving", "biggest_item"]
    total = round(sum(r["est_monthly_saving"] for r in merged), 2)
    from ..models import ReportMeta

    meta = ReportMeta(title="Savings summary", extra={"source": source.value, "runs": used, "not_run": missing,
                                                      "est_monthly_saving_total": total})
    emit(rows, fmt, out, "Savings summary", columns=cols, meta=meta)
    err.print(f"[bold]Total est. saving {cfg.pricing.currency} {total:,.2f}/month · {cfg.pricing.currency} "
              f"{total * 12:,.0f}/year[/] across {len(merged)} item(s)")
    for n in notes:
        err.print(f"[dim]- {n}[/]")
    err.print(f"[dim]From: {', '.join(used)}. Indicative; review before acting.[/]")
    missing = [f for f in missing if f not in failed]
    if missing:
        err.print("[yellow]Not included yet[/] (no saved run). For a complete total, run:")
        for feat in missing:
            what, cmd = SOURCE_HINTS[feat]
            err.print(f"  [bold]{cmd}[/]  [dim]# {what}[/]")
        err.print("[dim]then run spendlens savings summary again.[/]")


REC_TABLE = "aws_cost_optimization_recommendation"


def history_sql(days: int) -> tuple[str, str]:
    """(trend per refresh day, recommendations still open at the latest refresh, oldest first)."""
    days = int(days)
    window = f"tp_date >= current_date - interval {days} day"
    trend = (f"select cast(tp_date as varchar) as refreshed, count(distinct recommendation_id) as recommendations, "
             f"round(sum(estimated_monthly_savings_after_discount), 2) as monthly_savings from {REC_TABLE} "
             f"where {window} group by tp_date order by tp_date")
    oldest = (f"select recommendation_id, any_value(action_type) as action, any_value(current_resource_type) as "
              f"resource_type, any_value(resource_arn) as resource, cast(min(tp_date) as varchar) as first_seen, "
              f"date_diff('day', min(tp_date), max(tp_date)) as open_days, "
              f"round(max(estimated_monthly_savings_after_discount), 2) as monthly_savings from {REC_TABLE} "
              f"where {window} group by recommendation_id "
              f"having max(tp_date) = (select max(tp_date) from {REC_TABLE} where {window}) "
              f"order by open_days desc, monthly_savings desc")
    return trend, oldest


@app.command("history")
def history(
    days: Annotated[int, typer.Option("--days", "-d", min=1, max=400, help="The window, in days.")] = 90,
    top: Annotated[int, typer.Option("--top", help="Recommendations to list.")] = 15,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """AWS's savings recommendations over time: the open savings at each refresh, and the ones left open longest.

    From the Cost Optimization Hub Data Export collected by Tailpipe (local, no AWS calls). Set it up once: create
    a Cost Optimization Recommendations export in Billing > Data Exports, then spendlens tailpipe init and collect.
    Examples:
      spendlens savings history
      spendlens savings history --days 180 -f csv -o open-recommendations.csv
    """
    from ..graph.costs import CostSourceError, tailpipe_runner
    from ..sources import tailpipe_ready

    cfg = load(config, None, sets)
    if not tailpipe_ready(cfg, "cost_recommendations"):
        err.print("[yellow]No Cost Optimization Hub export in Tailpipe.[/] Create a Cost Optimization Recommendations "
                  "export (Billing > Data Exports), then: spendlens tailpipe init && spendlens tailpipe collect "
                  "cost_recommendations. For today's recommendations without it: spendlens savings hub")
        raise typer.Exit(1)
    trend_sql, oldest_sql = history_sql(days)
    try:
        run = tailpipe_runner(cfg.graph.tailpipe_path, timeout=600)
        trend, oldest = run(trend_sql), run(oldest_sql)
    except CostSourceError as e:
        err.print(f"[red]Tailpipe:[/] {str(e).splitlines()[0]}")
        raise typer.Exit(1) from e
    if fmt is not Format.table:
        emit(oldest[:top] if out is None else oldest, fmt, out, "Open savings recommendations, oldest first")
        return
    emit(trend, fmt, None, f"Open savings by refresh · last {days} days",
         columns=["refreshed", "recommendations", "monthly_savings"])
    emit(oldest[:top], fmt, None, "Still open at the latest refresh, oldest first",
         columns=["action", "resource_type", "resource", "first_seen", "open_days", "monthly_savings"])
    if trend:
        first, last = trend[0], trend[-1]
        err.print(f"[dim]{len(oldest)} open now, {last.get('monthly_savings')} a month · "
                  f"{first.get('refreshed')}: {first.get('monthly_savings')} a month[/]")


def register(reg: Registry) -> None:
    reg.add_typer(app, "savings")
