"""`spendlens forecast`: month-end projection vs the account budget and every monthly AWS Budget."""

from __future__ import annotations

from datetime import date
from typing import Annotated

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
    load,
)
from ..config import Config
from ..engines import cost as ce
from ..engines import forecast as fc
from ..models import Finding, Severity, Status
from ..plugins import Registry
from ..render import Format
from ..steampipe import SteampipeClient, SteampipeError

FEATURE = "forecast"
COLUMNS = ["status", "scope", "limit", "actual", "forecast", "forecast_pct_of_limit", "label", "method"]


def _mtd_actual(client: SteampipeClient, cfg: Config, today: date) -> float:
    first, _ = fc.month_bounds(today)
    if today <= first:
        return 0.0
    g = ce.GroupBy.parse("service")
    period = ce.Period(first, today, "mtd")
    # forecast is UNBLENDED (plugin), so compare against unblended actuals
    rows = client.query(ce.cost_sql(g, "unblended", cfg.cost.exclude_record_types),
                        ce.cost_params(g, period, cfg.cost.exclude_record_types))
    return sum(float(r["amount"] or 0) for r in rows)


def _ce_remaining(client: SteampipeClient, today: date) -> float | None:
    _, nxt = fc.month_bounds(today)
    try:
        rows = client.query(fc.FORECAST_SQL, {"start": today.isoformat(), "end": nxt.isoformat()})
    except SteampipeError as e:
        err.print(f"[yellow]Cost Explorer forecast unavailable ({str(e).splitlines()[0][:120]}); "
                  "using a linear run-rate projection.[/]")
        return None
    return sum(float(r["amount"] or 0) for r in rows) if rows else None


def run_live(cfg: Config, today: date) -> list[Finding]:
    findings = []
    warn = cfg.budgets.warn_pct
    try:
        with SteampipeClient(cfg.steampipe) as client:
            mtd = _mtd_actual(client, cfg, today)
            proj = fc.project(mtd, today, _ce_remaining(client, today))
            try:
                budgets = client.query(fc.BUDGETS_SQL)
            except SteampipeError as e:
                denied = "AccessDenied" in str(e) or "not authorized" in str(e).lower()
                err.print(f"[yellow]AWS Budgets not readable ({str(e).splitlines()[0][:120]})"
                          + ("; the role needs budgets:ViewBudget" if denied else "") + ".[/]")
                budgets = []
    except SteampipeError as e:
        err.print(f"[red]Cost query failed:[/] {e}")
        raise typer.Exit(1) from e

    limit = cfg.budgets.monthly_total
    status, label = fc.budget_status(proj["projected"], proj["mtd_actual"], limit, warn) if limit else ("info", "no budget set")
    findings.append(_finding("account", status, label, limit, proj["mtd_actual"], proj["projected"], proj["method"],
                             proj["forecast_remaining"]))
    for b in budgets:
        if (b.get("type") or "COST") != "COST":
            continue
        lim = float(b["limit_amount"] or 0)
        actual = float(b["actual_amount"]) if b.get("actual_amount") else None
        forecast = float(b["forecast_amount"]) if b.get("forecast_amount") else None
        st, lb = fc.budget_status(forecast, actual, lim, warn)
        findings.append(_finding(f"budget:{b['name']}", st, lb, lim, actual, forecast, "aws_budgets", None))
    return findings


def _finding(scope, status, label, limit, actual, forecast, method, remaining) -> Finding:
    pct = round(forecast / limit * 100, 1) if limit and forecast is not None else None
    sev = Severity.HIGH if status == "alarm" else Severity.MEDIUM if status == "info" else Severity.LOW
    reason = (f"{scope}: actual {actual if actual is not None else '-'}, forecast {forecast if forecast is not None else '-'}"
              + (f" vs limit {limit:,.2f} ({pct}%)" if limit else "") + f" -> {label}.")
    return Finding(control="budget_forecast", title="Month-end forecast vs budget", check="forecast", resource=scope,
                   status=Status(status), severity=sev, reason=reason,
                   dimensions={"scope": scope, "limit": limit, "actual": actual, "forecast": forecast,
                               "forecast_pct_of_limit": pct, "label": label, "method": method,
                               "forecast_remaining": remaining})


def forecast(
    fail_on_risk: Annotated[bool, typer.Option("--fail-on-risk", help="Exit 3 if any budget is forecast over its limit.")] = False,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    today: Annotated[str | None, typer.Option("--today", hidden=True)] = None,
) -> None:
    """Projected month-end spend vs budgets.monthly_total and every monthly AWS Budget.

    Projection = month-to-date actual + Cost Explorer forecast for the rest of the month (falls back to a linear
    run-rate when the forecast API has too little history). AWS Budgets use AWS's own forecasted spend.
    """
    cfg = load(config, profile, sets)
    t = date.fromisoformat(today) if today else date.today()
    findings, meta = findings_from_source(FEATURE, source, cfg, lambda: run_live(cfg, t))
    meta.title = "Month-end forecast vs budgets"
    rows = [{"status": f.status.value, **{k: f.dimensions.get(k) for k in COLUMNS if k != "status"}} for f in findings]
    emit(rows, fmt, out, meta.title, columns=COLUMNS, meta=meta)
    acct = next((f for f in findings if f.resource == "account"), None)
    if acct:
        d = acct.dimensions
        err.print(f"[bold]Month to date {d['actual']:,.2f} → projected {d['forecast']:,.2f}[/] ({d['method']})"
                  + (f" · budget {d['limit']:,.2f}" if d.get("limit") else " · set budgets.monthly_total to check it"))
    if fail_on_risk and any(f.status is Status.ALARM for f in findings):
        raise typer.Exit(3)


def register(reg: Registry) -> None:
    reg.add_command(forecast, "forecast")
