"""`spendlens sources`: where each kind of data comes from right now, and why (see sources.py)."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.table import Table

from ..common import BRAND, ConfigOpt, ProfileOpt, SetOpt, console, history_path, load
from ..plugins import Registry


def sources(
    aws: Annotated[bool, typer.Option("--aws", help="Also look up the trail's CloudWatch Logs group through Steampipe "
                                                    "(read-only).")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable.")] = False,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
) -> None:
    """Where each kind of data comes from: CloudTrail events, resource costs, flow logs and everything else.

    Shows every source in order, whether it is ready, and which one spendlens uses now. Local (Tailpipe and the
    history), unless --aws.
    Examples:
      spendlens sources
      spendlens sources --aws
    """
    from ..sources import report

    cfg = load(config, profile, sets)
    sp = None
    if aws:
        from ..steampipe import SteampipeClient

        sp = SteampipeClient(cfg.steampipe, timeout_s=120)
    try:
        rows = report(cfg, steampipe=sp, history_db=history_path(cfg))
    finally:
        if sp is not None:
            sp.close()
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    t = Table(title="Data sources · best first", header_style=f"bold {BRAND}", show_lines=True)
    for c in ("data", "sources", "used now", "used by"):
        t.add_column(c, overflow="fold")
    for r in rows:
        lines = [f"{'[green]✓[/]' if s['ready'] else '[dim]·[/]'} {s['source']} [dim]{s['detail']}[/]"
                 for s in r["sources"]]
        t.add_row(r["data"], "\n".join(lines), f"[bold]{r['used']}[/]", r["commands"])
    console.print(t)
    console.print("[dim]Tailpipe is used only when enabled, installed and configured (spendlens tailpipe init); "
                  "tailpipe.enabled: false turns it off. CloudTrail: --source on spendlens cloudtrail picks one.[/]")


def register(reg: Registry) -> None:
    reg.add_command(sources, "sources")
