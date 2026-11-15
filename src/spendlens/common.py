"""Shared CLI building blocks for core commands, built-in features and plugins.

A feature command typically looks like:

    @app.command()
    def mycheck(config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
                pp_vars: VarOpt = None, source: SourceOpt = Source.live, fmt: FormatOpt = Format.table,
                out: OutOpt = None):
        cfg = load(config, profile, sets, pp_vars)
        findings, run = findings_from_source("mycheck", source, cfg, lambda: run_my_check(cfg))
        emit(findings, fmt, out, "My check")
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from .config import Config, ConfigError, load_config
from .models import Finding, ReportMeta
from .render import Format, render

BRAND = "#E6007E"
console = Console()
err = Console(stderr=True)


class Source(str, Enum):
    live = "live"  # query AWS now (and save to history)
    last = "last"  # re-use the latest saved run from the history DB, no AWS calls
    demo = "demo"  # bundled sample data, no AWS calls


ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Policy file (default: ./finops.yaml).")]
ProfileOpt = Annotated[str | None, typer.Option("--profile", "-p", help="AWS profile (default: current credentials).")]
SetOpt = Annotated[
    list[str] | None,
    typer.Option("--set", "-s", help="Override a parameter, e.g. --set sizing.ec2_cpu_low=10 (repeatable)."),
]
VarOpt = Annotated[
    list[str] | None,
    typer.Option("--var", help="Raw Powerpipe mod variable, e.g. --var my_threshold=5 (repeatable)."),
]
FormatOpt = Annotated[Format, typer.Option("--format", "-f", help="Output format.")]
OutOpt = Annotated[Path | None, typer.Option("--out", "-o", help="Write output to this file.")]
SourceOpt = Annotated[
    Source, typer.Option("--source", help="live = query AWS; last = latest saved run; demo = bundled sample data.")
]


def load(
    config: Path | None = None,
    profile: str | None = None,
    sets: list[str] | None = None,
    pp_vars: list[str] | None = None,
) -> Config:
    """Resolve config for a command."""
    try:
        cfg = load_config(config, sets=sets, pp_vars=pp_vars)
    except (FileNotFoundError, ConfigError) as e:
        err.print(f"[red]Config error:[/] {e}")
        raise typer.Exit(2) from e
    if profile:
        cfg.aws.profile = profile
    if cfg.aws.profile:
        # Steampipe's aws plugin and the aws CLI both honour AWS_PROFILE.
        os.environ["AWS_PROFILE"] = cfg.aws.profile
    return cfg


def emit(rows: Any, fmt: Format, out: Path | None, title: str, columns: list[str] | None = None,
         meta: ReportMeta | None = None) -> None:
    """Render rows in the chosen format."""
    try:
        text = render(rows, fmt, meta=meta or ReportMeta(title=title), out=out, columns=columns)
    except NotImplementedError as e:
        err.print(f"[yellow]{e}[/]")
        raise typer.Exit(2) from e
    if text is not None and out is None:
        typer.echo(text, nl=False)
    elif out is not None:
        err.print(f"[green]Wrote[/] {out}")


# ------------------------------------------------------------------ run metadata


def sts_command(profile: str | None = None) -> list[str] | None:
    """`aws sts get-caller-identity` honouring endpoint overrides explicitly.

    AWS_ENDPOINT_URL[_STS] is only read natively by AWS CLI >= 2.13; passing --endpoint-url works with every
    version, so emulators (Moto/LocalStack) never fall through to real AWS.
    """
    exe = shutil.which("aws")
    if not exe:
        return None
    cmd = [exe, "sts", "get-caller-identity", "--output", "json"]
    if profile:
        cmd += ["--profile", profile]
    if endpoint := os.environ.get("AWS_ENDPOINT_URL_STS") or os.environ.get("AWS_ENDPOINT_URL"):
        cmd += ["--endpoint-url", endpoint]
    return cmd


def caller_identity() -> dict[str, str]:
    cmd = sts_command()
    if not cmd:
        return {}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
        return json.loads(p.stdout) if p.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


CI_ENV = ("CI_PIPELINE_ID", "CI_JOB_ID", "CI_COMMIT_SHA", "CI_PROJECT_PATH", "GITHUB_RUN_ID", "GITHUB_SHA",
          "GITHUB_REPOSITORY")


def ci_meta() -> dict[str, str]:
    return {k: os.environ[k] for k in CI_ENV if os.environ.get(k)}


# ------------------------------------------------------------------ sources


def history_path(cfg: Config) -> Path:
    from .history import default_history_path

    return Path(cfg.history.path).expanduser() if cfg.history.path else default_history_path()


def findings_from_source(
    feature: str,
    source: Source,
    cfg: Config,
    run_live: Callable[[], list[Finding]],
    *,
    meta: dict[str, Any] | None = None,
) -> tuple[list[Finding], ReportMeta]:
    """Get findings live (and persist them) or replay them from the history/demo DB."""
    from .history import HistoryStore, demo_db_path

    if source is Source.live:
        findings = run_live()
        ident = caller_identity()
        report = ReportMeta(title=feature, account_id=ident.get("Account"), caller_arn=ident.get("Arn"),
                            extra={"source": "live"})
        if cfg.history.enabled:
            try:
                with HistoryStore(history_path(cfg)) as store:
                    run_id = store.save_run(
                        feature, findings, account_id=report.account_id, caller_arn=report.caller_arn,
                        params=cfg.to_powerpipe_vars(), meta={**ci_meta(), **(meta or {})},
                    )
                    store.prune(cfg.history.keep_runs)
                report.extra["run_id"] = run_id
            except Exception as e:  # noqa: BLE001 - history must never break a live run
                err.print(f"[yellow]Warning: could not save run to history: {e}[/]")
        return findings, report

    path = demo_db_path() if source is Source.demo else history_path(cfg)
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/]" + ("" if source is Source.demo else " (run once with --source live first)"))
        raise typer.Exit(1) from e
    with store:
        run = store.latest_run(feature)
        if run is None:
            err.print(f"[red]No saved '{feature}' run in {path}[/]")
            raise typer.Exit(1)
        findings = store.findings(run.id)
    err.print(f"[dim]Using {source.value} data: run #{run.id} from {run.started_at}"
              + (f", account {run.account_id}" if run.account_id else "") + "[/]")
    return findings, ReportMeta(title=feature, account_id=run.account_id, caller_arn=run.caller_arn,
                                extra={"source": source.value, "run_id": run.id, "run_started_at": run.started_at})


def costs_from_source(
    feature: str,
    source: Source,
    cfg: Config,
    run_live: Callable[[], tuple[list[dict[str, Any]], dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any], ReportMeta]:
    """Cost rows (kind, period, dimension, grp, amount, currency) + run meta, live (saved) or replayed."""
    from .history import HistoryStore, demo_db_path

    if source is Source.live:
        rows, run_meta = run_live()
        ident = caller_identity()
        report = ReportMeta(title=feature, account_id=ident.get("Account"), caller_arn=ident.get("Arn"),
                            extra={"source": "live"})
        if cfg.history.enabled:
            try:
                with HistoryStore(history_path(cfg)) as store:
                    report.extra["run_id"] = store.save_run(
                        feature, cost_rows=rows, account_id=report.account_id, caller_arn=report.caller_arn,
                        params=cfg.cost.model_dump(mode="json"), meta={**ci_meta(), **run_meta},
                    )
                    store.prune(cfg.history.keep_runs)
            except Exception as e:  # noqa: BLE001 - history must never break a live run
                err.print(f"[yellow]Warning: could not save run to history: {e}[/]")
        return rows, run_meta, report

    path = demo_db_path() if source is Source.demo else history_path(cfg)
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/]" + ("" if source is Source.demo else " (run once with --source live first)"))
        raise typer.Exit(1) from e
    with store:
        run = store.latest_run(feature)
        if run is None:
            err.print(f"[red]No saved '{feature}' run in {path}[/]")
            raise typer.Exit(1)
        rows = store.cost_rows(run.id)
    err.print(f"[dim]Using {source.value} data: run #{run.id} from {run.started_at}"
              + (f", account {run.account_id}" if run.account_id else "") + "[/]")
    return rows, run.meta, ReportMeta(title=feature, account_id=run.account_id, caller_arn=run.caller_arn,
                                      extra={"source": source.value, "run_id": run.id,
                                             "run_started_at": run.started_at})


def steampipe_rows(cfg: Config, sql: str, params: Any = None, *, what: str = "query",
                   hint: str = "") -> list[dict[str, Any]]:
    """Run one read-only query for a feature; exit with a readable message (and optional hint) on failure."""
    from .steampipe import SteampipeClient, SteampipeError

    try:
        with SteampipeClient(cfg.steampipe) as c:
            return c.query(sql, params)
    except SteampipeError as e:
        tip = f"\n[dim]{hint}[/]" if hint and ("AccessDenied" in str(e) or "not authorized" in str(e).lower()) else ""
        err.print(f"[red]{what} failed:[/] {e}{tip}")
        raise typer.Exit(1) from e
