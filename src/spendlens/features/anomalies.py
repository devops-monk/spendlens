"""`spendlens anomalies`: sudden daily cost spikes (and optional drops) per service/account/tag."""

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
    steampipe_rows,
)
from ..config import Config
from ..engines import anomaly as an
from ..engines import cost as ce
from ..models import Finding, Severity, Status
from ..plugins import Registry
from ..render import Format

FEATURE = "anomalies"
COLUMNS = ["date", "group", "direction", "cost", "baseline", "delta", "delta_pct", "z", "severity"]


def settings(cfg: Config) -> an.AnomalySettings:
    a = cfg.anomaly
    return an.AnomalySettings(a.lookback_days, a.threshold, a.min_daily_cost, a.min_increase, a.include_drops)


def run_live(cfg: Config, group: ce.GroupBy, today: date) -> list[Finding]:
    a = cfg.anomaly
    start, end = an.query_window(today, a.detect_days, a.lookback_days)
    excluded = cfg.cost.exclude_record_types
    period = ce.Period(start, end, f"{start}..{end}")
    rows = steampipe_rows(cfg, ce.cost_sql(group, cfg.cost.metric, excluded, "DAILY"),
                          ce.cost_params(group, period, excluded), what="Daily cost query",
                          hint="The role needs ce:GetCostAndUsage.")
    found = an.detect(an.series_from_rows(rows), an.detection_days(today, a.detect_days), settings(cfg))
    return [to_finding(x, group) for x in found]


def to_finding(a: dict, group: ce.GroupBy) -> Finding:
    sign = "+" if a["delta"] >= 0 else ""
    pct = f" ({sign}{a['delta_pct']}%)" if a["delta_pct"] is not None else ""
    return Finding(
        control=f"cost_anomaly_{a['direction']}", title=f"Daily cost {a['direction']}", check="anomaly",
        resource=f"{group.label}:{a['group']}", status=Status.ALARM, severity=Severity(a["severity"]),
        reason=f"{a['group']} cost {a['cost']:,.2f} on {a['date']} vs baseline {a['baseline']:,.2f}: "
               f"{sign}{a['delta']:,.2f}{pct}, z={a['z']}.",
        dimensions={k: a[k] for k in ("date", "group", "direction", "cost", "baseline", "delta", "delta_pct", "z")},
    )


def as_rows(findings: list[Finding]) -> list[dict]:
    return [{**f.dimensions, "severity": f.severity.value} for f in findings]


def anomalies(
    group_by: Annotated[str | None, typer.Option("--group-by", "-g", help="service | account | region | usage-type | tag:<Key>")] = None,
    days: Annotated[int | None, typer.Option("--days", help="Recent days to check (default: anomaly.detect_days).")] = None,
    threshold: Annotated[float | None, typer.Option("--threshold", help="Robust z-score (default: anomaly.threshold).")] = None,
    drops: Annotated[bool, typer.Option("--drops", help="Also report sudden decreases.")] = False,
    fail_on_anomaly: Annotated[bool, typer.Option("--fail-on-anomaly", help="Exit 3 if any anomaly (CI).")] = False,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    today: Annotated[str | None, typer.Option("--today", hidden=True)] = None,
) -> None:
    """Detect daily cost spikes vs each group's trailing baseline (median + MAD robust z-score).

    Examples:
      spendlens anomalies
      spendlens anomalies -g account --days 7 --threshold 4
      spendlens anomalies --fail-on-anomaly -f json -o anomalies.json
    """
    cfg = load(config, profile, sets)
    if days is not None:
        cfg.anomaly.detect_days = days
    if threshold is not None:
        cfg.anomaly.threshold = threshold
    if drops:
        cfg.anomaly.include_drops = True
    try:
        group = ce.GroupBy.parse(group_by or cfg.anomaly.group_by)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--group-by") from e
    t = date.fromisoformat(today) if today else date.today()
    findings, meta = findings_from_source(FEATURE, source, cfg, lambda: run_live(cfg, group, t),
                                          meta={"group_by": group.label, "anomaly": cfg.anomaly.model_dump()})
    meta.title = f"Cost anomalies by {group.label}"
    emit(as_rows(findings), fmt, out, meta.title, columns=COLUMNS, meta=meta)
    excess = sum(f.dimensions.get("delta", 0) for f in findings if f.dimensions.get("direction") == "spike")
    err.print(f"[bold]{len(findings)} anomaly(ies)[/]" + (f" · excess spend {excess:,.2f}" if excess else "")
              + f" · threshold z>={cfg.anomaly.threshold}, min +{cfg.anomaly.min_increase}/day,"
                f" baseline {cfg.anomaly.lookback_days}d")
    if fail_on_anomaly and findings:
        raise typer.Exit(3)


def register(reg: Registry) -> None:
    reg.add_command(anomalies, "anomalies")
