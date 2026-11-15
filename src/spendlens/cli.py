"""spendlens command-line entry point."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from . import __version__
from .common import (
    BRAND,
    ConfigOpt,
    FormatOpt,
    OutOpt,
    ProfileOpt,
    SetOpt,
    VarOpt,
    console,
    emit,
    err,
    load,
)
from .config import flatten, param_keys, template_text
from .plugins import load_plugins, registry
from .render import Format
from .steampipe import SteampipeClient, SteampipeError

app = typer.Typer(
    name="spendlens",
    help="FinOps lens for AWS: tag compliance, rightsizing, cost diff, savings and AI Q&A (Steampipe-powered).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _version_cb(value: bool) -> None:
    if value:
        typer.echo(f"spendlens {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_cb, is_eager=True, help="Show version and exit.")
    ] = False,
) -> None:
    pass


# ----------------------------------------------------------- core commands


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Where to write the policy file.")] = Path("finops.yaml"),
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Create a starter [bold]finops.yaml[/] with your company FinOps policy."""
    if path.exists() and not force:
        err.print(f"[yellow]{path} already exists[/] (use --force to overwrite)")
        raise typer.Exit(1)
    path.write_text(template_text())
    console.print(f"[green]Created[/] {path}. Edit tagging/sizing/budgets/llm, then run [bold]spendlens doctor[/].")


@app.command()
def doctor(
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    offline: Annotated[bool, typer.Option("--offline", help="Skip Steampipe service / Cost Explorer checks.")] = False,
) -> None:
    """Check steampipe, powerpipe, aws plugin, AWS identity and Cost Explorer access."""
    from .doctor import run_checks

    cfg = load(config, profile, sets)
    checks = run_checks(cfg, skip_service=offline, extra=registry.doctor_checks)
    icon = {"ok": "[green]✔[/]", "warn": "[yellow]![/]", "fail": "[red]✘[/]"}
    t = Table(title="spendlens doctor", header_style=f"bold {BRAND}")
    t.add_column("")
    t.add_column("check")
    t.add_column("detail", overflow="fold")
    t.add_column("fix", style="cyan", overflow="fold")
    for c in checks:
        t.add_row(icon[c.status], c.name, c.detail, c.fix)
    console.print(t)
    console.print(f"[dim]config: {cfg.source or 'built-in defaults'}[/]")
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(1)


@app.command()
def query(
    sql: Annotated[str, typer.Argument(help="SQL to run against Steampipe (read-only), or @file.sql.")],
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    limit: Annotated[int, typer.Option(help="Max rows.")] = 1000,
) -> None:
    """Run an ad-hoc read-only SQL query against Steampipe."""
    cfg = load(config, profile)
    if sql.startswith("@"):
        sql = Path(sql[1:]).read_text()
    try:
        with SteampipeClient(cfg.steampipe) as c:
            rows = c.query(sql, limit=limit)
    except SteampipeError as e:
        err.print(f"[red]Query failed:[/] {e}")
        raise typer.Exit(1) from e
    emit(rows, fmt, out, "query")


@app.command()
def params(
    config: ConfigOpt = None,
    sets: SetOpt = None,
    pp_vars: VarOpt = None,
    filter_: Annotated[str | None, typer.Option("--filter", help="Only keys containing this text.")] = None,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
) -> None:
    """List every tunable parameter, its effective value and where it came from."""
    cfg = load(config, None, sets, pp_vars)
    values = flatten(cfg.model_dump(mode="json"))
    keys = param_keys() + sorted(k for k in values if k.startswith(("plugins.", "powerpipe_vars.")))
    rows = [
        {"parameter": k, "value": values.get(k), "source": cfg.param_source(k),
         "env": "SPENDLENS__" + k.upper().replace(".", "__")}
        for k in keys
        if not filter_ or filter_.lower() in k.lower()
    ]
    emit(rows, fmt, out, f"spendlens parameters ({cfg.source or 'built-in defaults'})")


@app.command("config")
def show_config(config: ConfigOpt = None, sets: SetOpt = None, pp_vars: VarOpt = None) -> None:
    """Show the effective configuration and the Powerpipe variables it produces."""
    cfg = load(config, None, sets, pp_vars)
    console.print(f"[dim]source: {cfg.source or 'built-in defaults'}[/]")
    console.print_json(json.dumps({"config": cfg.model_dump(mode="json"), "powerpipe_vars": cfg.to_powerpipe_vars()}))


@app.command("plugins")
def list_plugins() -> None:
    """List loaded features/plugins and what they contribute."""
    t = Table(title="spendlens plugins", header_style=f"bold {BRAND}")
    for col in ("name", "origin", "status"):
        t.add_column(col)
    for p in registry.plugins:
        t.add_row(p.name, p.origin, f"[red]{p.error}[/]" if p.error else "[green]loaded[/]")
    console.print(t if registry.plugins else "[dim]No plugins loaded.[/]")
    console.print(
        f"[dim]AI tools: {', '.join(registry.ai_tools) or '-'} · extra mods: "
        f"{', '.join(map(str, registry.mod_paths)) or '-'}[/]"
    )


# ----------------------------------------------------------- features & plugins

load_plugins(registry)
registry.apply(app)


if __name__ == "__main__":
    app()
