"""`spendlens cost`: month-over-month / custom-period diff, trend, showback by tag, and resource-level changes."""

from __future__ import annotations

from datetime import date
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
    costs_from_source,
    emit,
    err,
    load,
)
from ..config import Config
from ..engines import cost as ce
from ..plugins import Registry
from ..render import Format
from ..steampipe import SteampipeClient, SteampipeError
from ..ui import activity

app = typer.Typer(help="AWS spend: what changed, trends, showback by team/tag, resource-level changes.",
                  no_args_is_help=True)

GroupOpt = Annotated[str | None, typer.Option("--group-by", "-g", help="service | account | region | usage-type | "
                                                                        "instance-type | operation | record-type | tag:<Key>")]
MetricOpt = Annotated[str | None, typer.Option("--metric", help=f"{' | '.join(ce.METRICS)} (default: cost.metric)")]
CreditsOpt = Annotated[bool, typer.Option("--include-credits", help="Keep credit/refund record types.")]
TodayOpt = Annotated[str | None, typer.Option("--today", hidden=True, help="Override today's date (testing).")]

DIFF_COLUMNS = ["group", "base", "compare", "change", "change_pct", "status", "flagged", "currency"]
SHOW = ("changed", "flagged", "increases", "decreases", "all")


def _today(override: str | None) -> date:
    return date.fromisoformat(override) if override else date.today()


def _group(value: str | None, cfg: Config) -> ce.GroupBy:
    try:
        return ce.GroupBy.parse(value or cfg.cost.default_group_by)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--group-by") from e


def _metric(value: str | None, cfg: Config) -> str:
    m = value or cfg.cost.metric
    if m not in ce.METRICS:
        raise typer.BadParameter(f"Unknown metric '{m}'. Use: {', '.join(ce.METRICS)}", param_hint="--metric")
    return m


def _query_periods(cfg: Config, group: ce.GroupBy, periods: list[ce.Period], metric: str,
                   include_credits: bool) -> list[dict[str, Any]]:
    excluded = [] if include_credits else cfg.cost.exclude_record_types
    sql = ce.cost_sql(group, metric, excluded)
    rows: list[dict[str, Any]] = []
    try:
        with activity("Querying Cost Explorer [dim](Steampipe)[/]"), SteampipeClient(cfg.steampipe) as client:
            for i, p in enumerate(periods):
                for r in client.query(sql, ce.cost_params(group, p, excluded)):
                    rows.append({"kind": f"p{i}", "period": p.label, "dimension": group.label, "grp": r["grp"],
                                 "amount": float(r["amount"] or 0), "currency": r.get("currency") or "USD"})
    except SteampipeError as e:
        hint = "\n[dim]The role needs ce:GetCostAndUsage.[/]" if "AccessDenied" in str(e) else ""
        err.print(f"[red]Cost Explorer query failed:[/] {e}{hint}")
        raise typer.Exit(1) from e
    return rows


def _split(rows: list[dict[str, Any]], periods: list[ce.Period]) -> list[tuple[ce.Period, dict[str, float]]]:
    out = []
    for i, p in enumerate(periods):
        totals, _ = ce.totals_by_group(r for r in rows if r["kind"] == f"p{i}")
        out.append((p, totals))
    return out


def _currency(rows: list[dict[str, Any]], cfg: Config) -> str:
    return next((r["currency"] for r in rows if r.get("currency")), cfg.pricing.currency)


# ------------------------------------------------------------------ diff


@app.command("diff")
def diff_cmd(
    base: Annotated[str | None, typer.Option("--from", help="Base period: YYYY-MM or YYYY-MM..YYYY-MM.")] = None,
    compare: Annotated[str | None, typer.Option("--to", help="Compare period: YYYY-MM or YYYY-MM..YYYY-MM.")] = None,
    mtd: Annotated[bool, typer.Option("--mtd", help="This month to date vs the same days last month.")] = False,
    group_by: GroupOpt = None,
    metric: MetricOpt = None,
    include_credits: CreditsOpt = False,
    show: Annotated[str, typer.Option("--show", help=f"Rows to show: {', '.join(SHOW)}.")] = "changed",
    top: Annotated[int | None, typer.Option("--top", help="Only the N largest changes.")] = None,
    total: Annotated[bool, typer.Option("--total/--no-total", help="Append a TOTAL row.")] = True,
    fail_on_flag: Annotated[bool, typer.Option("--fail-on-flag", help="Exit 3 if any change is flagged (CI).")] = False,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    today: TodayOpt = None,
) -> None:
    """What changed between two periods, per service/account/region/tag.

    Default: last full month vs the month before. Examples:
      spendlens cost diff
      spendlens cost diff --mtd -g account
      spendlens cost diff --from 2026-07..2026-09 --to 2026-10..2026-12 -g tag:Team
      spendlens cost diff --show flagged --fail-on-flag
    """
    if show not in SHOW:
        raise typer.BadParameter(f"Use one of: {', '.join(SHOW)}", param_hint="--show")
    if (base is None) != (compare is None):
        raise typer.BadParameter("Give both --from and --to (or neither).", param_hint="--from/--to")
    if mtd and base:
        raise typer.BadParameter("--mtd cannot be combined with --from/--to.", param_hint="--mtd")
    cfg = load(config, profile, sets)
    group, m = _group(group_by, cfg), _metric(metric, cfg)
    try:
        if base and compare:
            periods = [ce.parse_period(base), ce.parse_period(compare)]
        else:
            t = _today(today)
            periods = list(ce.month_to_date(t) if mtd else ce.last_full_months(t))
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--from/--to") from e

    run_meta = {"periods": [p.as_dict() for p in periods], "group_by": group.label, "metric": m,
                "include_credits": include_credits}
    rows, meta_saved, meta = costs_from_source(
        "cost_diff", source, cfg, lambda: (_query_periods(cfg, group, periods, m, include_credits), run_meta))
    if source is not Source.live:
        periods = [ce.Period.from_dict(d) for d in meta_saved.get("periods", [])] or periods
    (p0, base_totals), (p1, cmp_totals) = _split(rows, periods)
    currency = _currency(rows, cfg)
    t = ce.DiffThresholds(cfg.cost.change_alarm_abs, cfg.cost.change_alarm_pct, cfg.cost.min_amount)
    result = ce.diff(base_totals, cmp_totals, t, currency, p0.days, p1.days)
    tot = ce.diff_total(result, currency)
    shown = {
        "all": result,
        "changed": [r for r in result if r["status"] != "flat"],
        "flagged": [r for r in result if r["flagged"]],
        "increases": [r for r in result if r["change"] > 0],
        "decreases": [r for r in result if r["change"] < 0],
    }[show][: top or None]
    if total:
        shown = [*shown, tot]
    meta.title = f"Cost diff by {meta_saved.get('group_by', group.label)}: {p0.label} → {p1.label}"
    meta.extra.update({"base": p0.as_dict(), "compare": p1.as_dict(), "metric": meta_saved.get("metric", m),
                       "total": tot})
    emit(shown, fmt, out, meta.title, columns=DIFF_COLUMNS, meta=meta)
    flagged = [r for r in result if r["flagged"]]
    sign = "+" if tot["change"] >= 0 else ""
    pct = f" ({sign}{tot['change_pct']}%)" if tot["change_pct"] is not None else ""
    err.print(f"[bold]Total {currency} {tot['base']:,.2f} → {tot['compare']:,.2f}: {sign}{tot['change']:,.2f}{pct}[/]"
              f" · {len(flagged)} flagged change(s)"
              + (" · periods differ in length, see change_pct_daily (json/csv)" if p0.days != p1.days else ""))
    if group.kind == "tag":
        err.print("[dim]Tag grouping cannot exclude credits/refunds (Cost Explorer limitation).[/]")
    if fail_on_flag and flagged:
        raise typer.Exit(3)


# ------------------------------------------------------------------ trend


@app.command("trend")
def trend_cmd(
    months: Annotated[int | None, typer.Option("--months", "-n", help="Full months to show (default: cost.trend_months).")] = None,
    include_current: Annotated[bool, typer.Option("--include-current", help="Add the current month to date.")] = False,
    group_by: GroupOpt = None,
    metric: MetricOpt = None,
    include_credits: CreditsOpt = False,
    top: Annotated[int | None, typer.Option("--top", help="Largest N groups by total.")] = 15,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    today: TodayOpt = None,
) -> None:
    """Monthly cost per group for the last N months, with the latest month-over-month change."""
    cfg = load(config, profile, sets)
    group, m = _group(group_by, cfg), _metric(metric, cfg)
    n = months or cfg.cost.trend_months
    if not 2 <= n <= 13:
        raise typer.BadParameter("Use 2-13 months (Cost Explorer history limit).", param_hint="--months")
    periods = ce.trailing_months(_today(today), n, include_current)
    run_meta = {"periods": [p.as_dict() for p in periods], "group_by": group.label, "metric": m}
    rows, meta_saved, meta = costs_from_source(
        "cost_trend", source, cfg, lambda: (_query_periods(cfg, group, periods, m, include_credits), run_meta))
    if source is not Source.live:
        periods = [ce.Period.from_dict(d) for d in meta_saved.get("periods", [])] or periods
    result = ce.trend(_split(rows, periods), top)
    meta.title = f"Cost trend by {meta_saved.get('group_by', group.label)} ({_currency(rows, cfg)})"
    emit(result, fmt, out, meta.title,
         columns=["group", *[p.label for p in periods], "total", "last_change", "last_change_pct"], meta=meta)


# ------------------------------------------------------------------ showback


@app.command("showback")
def showback_cmd(
    tag_key: Annotated[list[str] | None, typer.Option("--tag-key", "-t", help="Tag key(s) (default: cost.showback_tag_keys).")] = None,
    month: Annotated[str | None, typer.Option("--month", help="YYYY-MM or YYYY-MM..YYYY-MM (default: last full month).")] = None,
    mtd: Annotated[bool, typer.Option("--mtd", help="Current month to date.")] = False,
    metric: MetricOpt = None,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    today: TodayOpt = None,
) -> None:
    """Cost per tag value (team, cost centre ...), share of total, untagged spend and budget variance."""
    cfg = load(config, profile, sets)
    m = _metric(metric, cfg)
    keys = tag_key or cfg.cost.showback_tag_keys
    if not keys:
        raise typer.BadParameter("No tag keys: pass --tag-key or set cost.showback_tag_keys.", param_hint="--tag-key")
    t = _today(today)
    try:
        period = ce.parse_period(month) if month else (ce.month_to_date(t)[1] if mtd else ce.last_full_months(t)[1])
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--month") from e

    def live() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for k in keys:
            rows += _query_periods(cfg, ce.GroupBy("tag", k, f"tag:{k}"), [period], m, True)
        return rows, {"periods": [period.as_dict()], "tag_keys": keys, "metric": m}

    rows, meta_saved, meta = costs_from_source("cost_showback", source, cfg, live)
    keys = meta_saved.get("tag_keys", keys)
    label = (meta_saved.get("periods") or [period.as_dict()])[0]["label"]
    out_rows: list[dict[str, Any]] = []
    currency = _currency(rows, cfg)
    for k in keys:
        totals, _ = ce.totals_by_group(r for r in rows if r["dimension"] == f"tag:{k}")
        out_rows += ce.showback(totals, k, cfg.budgets.per_tag.get(k), currency)
    meta.title = f"Showback {label} by {', '.join(keys)}"
    emit(out_rows, fmt, out, meta.title,
         columns=["tag_key", "tag_value", "amount", "share_pct", "budget", "budget_variance", "over_budget", "currency"],
         meta=meta)
    for k in keys:
        untagged = next((r for r in out_rows if r["tag_key"] == k and r["tag_value"] == "(untagged)"), None)
        if untagged and untagged["share_pct"]:
            err.print(f"[yellow]{untagged['share_pct']}% of spend ({currency} {untagged['amount']:,.2f}) has no '{k}' tag.[/]")


# ------------------------------------------------------------------ resources


@app.command("resources")
def resources_cmd(
    days: Annotated[int | None, typer.Option("--days", help="Window size, 1-7 (default: cost.resource_days).")] = None,
    metric: MetricOpt = None,
    top: Annotated[int | None, typer.Option("--top", help="Largest N changes.")] = 20,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    today: TodayOpt = None,
) -> None:
    """Which resources' cost rose or fell: the last N days vs the N days before.

    Uses Cost Explorer resource-level data (opt-in in the Billing console; 14 days of history, so N <= 7).
    For longer resource history use the Cost and Usage Report (CUR).
    """
    cfg = load(config, profile, sets)
    m = _metric(metric, cfg)
    try:
        windows = list(ce.resource_windows(_today(today), days or cfg.cost.resource_days))
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--days") from e

    def live() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        sql = ce.resource_sql(m)
        rows = []
        try:
            with activity("Querying Cost Explorer [dim](Steampipe)[/]"), SteampipeClient(cfg.steampipe) as client:
                result = client.query(sql, {"start": windows[0].start.isoformat(), "end": windows[1].end.isoformat()})
        except SteampipeError as e:
            err.print(f"[red]Resource-level cost query failed:[/] {e}\n"
                      "[dim]Enable 'resource-level data at daily granularity' in Cost Explorer settings.[/]")
            raise typer.Exit(1) from e
        for r in result:
            day = r["day"] if isinstance(r["day"], date) else date.fromisoformat(str(r["day"])[:10])
            kind = "p0" if day < windows[1].start else "p1"
            rows.append({"kind": kind, "period": windows[int(kind[1])].label, "dimension": "resource",
                         "grp": r["grp"] or "(no resource id)", "amount": float(r["amount"] or 0),
                         "currency": r.get("currency") or "USD"})
        return rows, {"periods": [w.as_dict() for w in windows], "metric": m}

    rows, meta_saved, meta = costs_from_source("cost_resources", source, cfg, live)
    if source is not Source.live:
        windows = [ce.Period.from_dict(d) for d in meta_saved.get("periods", [])] or windows
    (p0, b), (p1, c) = _split(rows, windows)
    t = ce.DiffThresholds(cfg.cost.change_alarm_abs, cfg.cost.change_alarm_pct, cfg.cost.min_amount)
    result = [r for r in ce.diff(b, c, t, _currency(rows, cfg)) if r["status"] != "flat"][: top or None]
    for r in result:
        r["resource"] = r.pop("group")
    meta.title = f"Resource cost change: {p0.label} → {p1.label}"
    emit(result, fmt, out, meta.title,
         columns=["resource", "base", "compare", "change", "change_pct", "status", "flagged", "currency"], meta=meta)


def register(reg: Registry) -> None:
    reg.add_typer(app, "cost")
