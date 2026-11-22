"""`spendlens history`: inspect and diff saved runs (local SQLite store or the bundled demo DB)."""

from __future__ import annotations

from typing import Annotated

import typer

from ..common import ConfigOpt, FormatOpt, OutOpt, Source, emit, err, history_path, load
from ..history import HistoryStore, demo_db_path, diff_findings
from ..plugins import Registry
from ..render import Format

app = typer.Typer(help="Saved runs: list them and see what changed between runs.", no_args_is_help=True)

DbSourceOpt = Annotated[Source, typer.Option("--source", help="last = your history DB; demo = bundled sample DB.")]


def _open(cfg_path, source: Source) -> HistoryStore:
    path = demo_db_path() if source is Source.demo else history_path(load(cfg_path))
    try:
        return HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/]\n[dim]Nothing is saved yet: run a check (spendlens report, or spendlens tags), or "
                  "look at the sample data with --source demo.[/]")
        raise typer.Exit(1) from e


@app.command("list")
def list_runs(
    feature: Annotated[str | None, typer.Option("--feature", help="e.g. tags, rightsize.")] = None,
    limit: Annotated[int, typer.Option(help="Max runs.")] = 20,
    config: ConfigOpt = None,
    source: DbSourceOpt = Source.last,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
) -> None:
    """List saved runs, newest first."""
    with _open(config, source) as store:
        rows = []
        for r in store.runs(feature, limit):
            fs = store.findings(r.id)
            rows.append({"run": r.id, "feature": r.feature, "started_at": r.started_at, "account_id": r.account_id,
                         "findings": len(fs), "alarms": sum(f.status.value == "alarm" for f in fs),
                         "ci": r.meta.get("CI_PIPELINE_ID") or r.meta.get("GITHUB_RUN_ID")})
    emit(rows, fmt, out, "Saved runs")


@app.command("diff")
def diff(
    feature: Annotated[str, typer.Option("--feature", help="Feature to compare.")] = "tags",
    from_run: Annotated[int | None, typer.Option("--from", help="Older run id (default: previous run).")] = None,
    to_run: Annotated[int | None, typer.Option("--to", help="Newer run id (default: latest run).")] = None,
    config: ConfigOpt = None,
    source: DbSourceOpt = Source.last,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
) -> None:
    """What changed between two runs: new alarms, resolved alarms, removed resources."""
    with _open(config, source) as store:
        new = store.get_run(to_run) if to_run else store.latest_run(feature)
        old = store.get_run(from_run) if from_run else store.latest_run(feature, offset=1)
        if not new or not old:
            err.print(f"[red]Need two '{feature}' runs to diff.[/]")
            raise typer.Exit(1)
        rows = diff_findings(store.findings(old.id), store.findings(new.id))
    emit(rows, fmt, out, f"{feature}: run #{old.id} ({old.started_at}) → #{new.id} ({new.started_at})")
    counts = {k: sum(r["change"] == k for r in rows) for k in ("new_alarm", "resolved", "resource_gone", "new_resource", "changed")}
    err.print("[bold]" + " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items()) + "[/]")


@app.command("path")
def path(config: ConfigOpt = None) -> None:
    """Print the history database path (useful for CI cache/S3 sync)."""
    typer.echo(history_path(load(config)))


def register(reg: Registry) -> None:
    reg.add_typer(app, "history")
