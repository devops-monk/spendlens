"""Generic builder for control-based features (rightsize, waste, and plugin-provided benchmarks).

    app = make_control_app(ControlFeature(name="rightsize", benchmark="rightsize", title="Rightsizing", ...))

Every such command gets the same options: --service/--control filters, --status, --summary [--by],
--min-saving, --fail-on-alarm, savings estimates, --source live|last|demo, all output formats, and
the standard --config/--profile/--set/--var parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
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
    VarOpt,
    emit,
    err,
    findings_from_source,
    load,
)
from ..config import Config
from ..models import Finding, Status
from ..plugins import registry
from ..render import Format
from .savings import apply_estimates, total_saving
from .summary import headline, summarise

COLUMNS = ["status", "est_monthly_saving", "service", "control", "resource", "reason", "account_id", "region",
           "severity"]
SUMMARY_KEYS = ("service", "control", "resource_type", "account_id", "region")


@dataclass(frozen=True)
class ControlFeature:
    name: str  # history feature key and command name
    benchmark: str  # benchmark in the mod
    title: str
    help: str
    mod_dir: Path | None = None  # None = bundled mod
    default_statuses: tuple[str, ...] = ("alarm", "error")
    examples: tuple[str, ...] = field(default_factory=tuple)


@cache
def _index(mod_dir: Path | None) -> dict[str, dict[str, str]]:
    from ..powerpipe import bundled_mod_dir, mod_controls

    return mod_controls(mod_dir or bundled_mod_dir())


def feature_controls(feat: ControlFeature) -> dict[str, dict[str, str]]:
    return {k: v for k, v in _index(feat.mod_dir).items() if v.get("spendlens_check") == feat.benchmark}


def _sql_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(set(values)))


def resolve_filters(feat: ControlFeature, services: list[str], controls: list[str]) -> str | None:
    """Validate filters and return a Powerpipe --where clause (single condition only: AND is unsupported)."""
    known = feature_controls(feat)
    known_services = sorted({v.get("service", "") for v in known.values()})
    bad = sorted(set(services) - set(known_services))
    if bad:
        raise typer.BadParameter(f"Unknown service(s): {', '.join(bad)}. Valid: {', '.join(known_services)}",
                                 param_hint="--service")
    bad = sorted(set(controls) - known.keys())
    if bad:
        raise typer.BadParameter(f"Unknown control(s): {', '.join(bad)}. Run `spendlens {feat.name} controls`.",
                                 param_hint="--control")
    scope = set(services) | {known[c].get("service", "") for c in controls}
    return f"tags->>'service' in ({_sql_list(list(scope))})" if scope else None


def run_live(feat: ControlFeature, cfg: Config, where: str | None) -> list[Finding]:
    from ..powerpipe import PowerpipeError, PowerpipeRunner

    runner = PowerpipeRunner(cfg, mod_dir=feat.mod_dir) if feat.mod_dir else PowerpipeRunner(cfg)
    try:
        return runner.run([feat.benchmark], where=where).findings
    except PowerpipeError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e


def select(findings: list[Finding], services: list[str], controls: list[str], statuses: list[str],
           min_saving: float | None) -> list[Finding]:
    out = findings
    if services:
        out = [f for f in out if f.service in set(services)]
    if controls:
        out = [f for f in out if f.control in set(controls)]
    if "all" not in statuses:
        out = [f for f in out if f.status.value in statuses]
    if min_saving is not None:
        out = [f for f in out if (f.est_monthly_saving or 0) >= min_saving]
    return out


def make_control_app(feat: ControlFeature) -> typer.Typer:
    app = typer.Typer(help=feat.help, invoke_without_command=True, no_args_is_help=False)
    examples = "\n".join(f"  {e}" for e in feat.examples)

    @app.callback()
    def run(
        ctx: typer.Context,
        service: Annotated[list[str] | None, typer.Option("--service", "-S", help="Limit to services, e.g. ec2, rds.")] = None,
        control: Annotated[list[str] | None, typer.Option("--control", "-C", help="Limit to specific controls.")] = None,
        status: Annotated[
            list[str] | None, typer.Option("--status", help="Statuses to show: alarm, ok, info, skip, error or all.")
        ] = None,
        summary: Annotated[bool, typer.Option("--summary", help="Counts, compliance % and savings per group.")] = False,
        by: Annotated[str, typer.Option("--by", help=f"Summary grouping: {', '.join(SUMMARY_KEYS)} (comma-separated).")] = "service,control",
        min_saving: Annotated[float | None, typer.Option("--min-saving", help="Only findings saving at least this much per month.")] = None,
        fail_on_alarm: Annotated[bool, typer.Option("--fail-on-alarm", help="Exit 3 if any alarm (for CI gates).")] = False,
        config: ConfigOpt = None,
        profile: ProfileOpt = None,
        sets: SetOpt = None,
        pp_vars: VarOpt = None,
        source: SourceOpt = Source.live,
        fmt: FormatOpt = Format.table,
        out: OutOpt = None,
    ) -> None:
        if ctx.invoked_subcommand:
            return
        services, controls = service or [], control or []
        statuses = [s.lower() for s in (status or list(feat.default_statuses))]
        bad = set(statuses) - {s.value for s in Status} - {"all"}
        if bad:
            raise typer.BadParameter(f"Unknown status: {', '.join(sorted(bad))}", param_hint="--status")
        group_by = tuple(k.strip() for k in by.split(",") if k.strip())
        if not group_by or set(group_by) - set(SUMMARY_KEYS):
            raise typer.BadParameter(f"--by must be from: {', '.join(SUMMARY_KEYS)}", param_hint="--by")
        where = resolve_filters(feat, services, controls)

        cfg = load(config, profile, sets, pp_vars)
        findings, meta = findings_from_source(
            feat.name, source, cfg,
            lambda: apply_estimates(run_live(feat, cfg, where), cfg.pricing, registry.estimators),
            meta={"services": services, "controls": controls},
        )
        scoped = select(findings, services, controls, ["all"], None)
        meta.title = feat.title
        meta.extra["est_monthly_saving_total"] = total_saving(scoped)
        meta.extra["currency"] = cfg.pricing.currency
        if summary:
            rows = summarise(scoped, by=group_by)
            emit(rows, fmt, out, f"{feat.title} summary", meta=meta)
        else:
            rows = select(scoped, services, controls, statuses, min_saving)
            rows.sort(key=lambda f: (f.status is not Status.ALARM, -(f.est_monthly_saving or 0), f.service or "",
                                     f.control))
            emit(rows, fmt, out, feat.title, columns=COLUMNS, meta=meta)
        err.print(f"[bold]{headline(scoped)}[/] · est. saving [bold]{cfg.pricing.currency} "
                  f"{total_saving(scoped):,.2f}/month[/] (indicative, review before acting)")
        if fmt is Format.table and not summary:
            from .graph_cmd import graph_hints

            for line in graph_hints(scoped, source, cfg):
                err.print(f"[dim]{line}[/]")
        if fail_on_alarm and any(f.status is Status.ALARM for f in scoped):
            raise typer.Exit(3)

    run.__doc__ = f"{feat.help}\n\nExamples:\n{examples}" if examples else feat.help

    @app.command("controls")
    def list_controls(fmt: FormatOpt = Format.table, out: OutOpt = None) -> None:
        """List the controls in this check, with their service and resource type."""
        rows = [{"control": k, "service": v.get("service"), "resource_type": v.get("resource_type"),
                 "title": v.get("title")} for k, v in sorted(feature_controls(feat).items())]
        emit(rows, fmt, out, f"{feat.title}: {len(rows)} controls")

    return app
