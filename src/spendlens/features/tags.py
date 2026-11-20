"""`spendlens tags`: company tagging-policy compliance (mandatory, untagged, prohibited, limit, values)."""

from __future__ import annotations

from enum import Enum
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
from ..plugins import Registry
from ..render import Format
from .summary import headline, summarise
from .tags_catalog import TAGGABLE_RESOURCES
from .tags_checks import TAG_CHECKS

FEATURE = "tags"
TagCheckName = Enum("TagCheckName", {k: k for k in [*TAG_CHECKS, "all"]}, type=str)  # type: ignore[misc]

FINDING_COLUMNS = ["status", "check", "resource_type", "resource", "reason", "account_id", "region", "severity"]


def build_where(resource_types: list[str]) -> str | None:
    if not resource_types:
        return None
    unknown = sorted(set(resource_types) - TAGGABLE_RESOURCES.keys())
    if unknown:
        raise typer.BadParameter(
            f"Unknown resource type(s): {', '.join(unknown)}. Run `spendlens tags types` to list them.",
            param_hint="--resource-type",
        )
    quoted = ", ".join("'" + t.replace("'", "''") + "'" for t in sorted(set(resource_types)))
    return f"tags->>'resource_type' in ({quoted})"


def run_live(cfg: Config, checks: list[str], resource_types: list[str]) -> list[Finding]:
    from ..powerpipe import PowerpipeError, PowerpipeRunner

    targets = ["tags"] if set(checks) == set(TAG_CHECKS) else [f"tags_{c}" for c in checks]
    try:
        return PowerpipeRunner(cfg).run(targets, where=build_where(resource_types)).findings
    except PowerpipeError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e


def select(findings: list[Finding], checks: list[str], resource_types: list[str],
           statuses: list[str]) -> list[Finding]:
    wanted_checks = {f"tags_{c}" for c in checks}
    out = [f for f in findings if f.check in wanted_checks or f.check is None]
    if resource_types:
        out = [f for f in out if f.resource_type in set(resource_types)]
    if "all" not in statuses:
        out = [f for f in out if f.status.value in statuses]
    return out


app = typer.Typer(help="Tag compliance against the company tagging policy.", invoke_without_command=True,
                  no_args_is_help=False)


@app.callback()
def tags(
    ctx: typer.Context,
    check: Annotated[
        list[TagCheckName] | None, typer.Option("--check", "-k", help="Which check(s) to run.")  # type: ignore[valid-type]
    ] = None,
    resource_type: Annotated[
        list[str] | None, typer.Option("--resource-type", "-r", help="Limit to resource types, e.g. s3_bucket.")
    ] = None,
    status: Annotated[
        list[str] | None, typer.Option("--status", help="Statuses to show: alarm, ok, skip, error, info or all.")
    ] = None,
    summary: Annotated[bool, typer.Option("--summary", help="Compliance % per check and resource type.")] = False,
    fail_on_alarm: Annotated[bool, typer.Option("--fail-on-alarm", help="Exit 3 if any alarm (for CI gates).")] = False,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    pp_vars: VarOpt = None,
    source: SourceOpt = Source.live,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
) -> None:
    """Check resources against mandatory/prohibited tags, tag limit and allowed values.

    Examples:
      spendlens tags
      spendlens tags -k mandatory -r s3_bucket -r ec2_instance
      spendlens tags --set 'tagging.mandatory=[Environment, CostCentre]' -f csv -o tags.csv
      spendlens tags --summary --source demo
    """
    if ctx.invoked_subcommand:
        return
    checks = list(TAG_CHECKS) if not check or any(c.value == "all" for c in check) else [c.value for c in check]
    rtypes = resource_type or []
    statuses = [s.lower() for s in (status or ["alarm", "error"])]
    bad = set(statuses) - {s.value for s in Status} - {"all"}
    if bad:
        raise typer.BadParameter(f"Unknown status: {', '.join(sorted(bad))}", param_hint="--status")
    build_where(rtypes)  # validate early

    cfg = load(config, profile, sets, pp_vars)
    findings, meta = findings_from_source(
        FEATURE, source, cfg, lambda: run_live(cfg, checks, rtypes),
        meta={"checks": checks, "resource_types": rtypes},
    )
    scoped = select(findings, checks, rtypes, ["all"])
    meta.title = "Tag compliance"
    if summary:
        emit(summarise(scoped), fmt, out, "Tag compliance summary", meta=meta)
    else:
        rows: list = select(scoped, checks, rtypes, statuses)
        columns = FINDING_COLUMNS
        if sugg := _suggested(rows, source, cfg):
            rows = [{**f.model_dump(mode="json"), "suggested": sugg.get(f.resource, "")} for f in rows]
            columns = [*FINDING_COLUMNS[:5], "suggested", *FINDING_COLUMNS[5:]]
        emit(rows, fmt, out, "Tag compliance", columns=columns, meta=meta)
        if sugg:
            err.print(f"[dim]{len(sugg)} resource(s) have suggested tags from what they are attached to "
                      "(the saved resource graph). Review and apply: spendlens graph owners --script tag-review.sh[/]")
    err.print(f"[bold]{headline(scoped)}[/]")
    if fail_on_alarm and any(f.status is Status.ALARM for f in scoped):
        raise typer.Exit(3)


def _suggested(findings: list[Finding], source: Source, cfg: Config) -> dict[str, str]:
    """resource -> suggested tags, for missing-tag alarms, from the saved resource graph (no AWS calls)."""
    wanted = [f for f in findings if f.status is Status.ALARM and f.check in ("tags_mandatory", "tags_untagged")]
    if not wanted:
        return {}
    from .graph_cmd import describe, saved_suggestions

    by = saved_suggestions(source, cfg)
    if not by:
        return {}
    out = {}
    for f in wanted:
        short = f.resource.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if f.resource.startswith("arn:") else f.resource
        if s := by.get(f.resource) or by.get(short):
            out[f.resource] = describe(s)
    return out


@app.command("types")
def types(fmt: FormatOpt = Format.table, out: OutOpt = None) -> None:
    """List the resource types checked (add more in features/tags_catalog.py)."""
    rows = [{"resource_type": k, "table": t, "name": n} for k, (t, n) in TAGGABLE_RESOURCES.items()]
    emit(rows, fmt, out, f"Taggable resource types ({len(rows)})")


def register(reg: Registry) -> None:
    reg.add_typer(app, "tags")
