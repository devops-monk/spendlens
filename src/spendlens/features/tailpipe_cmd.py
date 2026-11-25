"""`spendlens tailpipe`: set up, collect and check Tailpipe for CloudTrail logs, VPC flow logs and CUR.

  tailpipe init      find the S3 locations (read-only) and write ~/.tailpipe/config/spendlens.tpc
  tailpipe collect   tailpipe collect for each configured kind, over tailpipe.collect_days
  tailpipe status    what is configured and collected
"""

from __future__ import annotations

import subprocess
from typing import Annotated

import typer
from rich.table import Table

from .. import tailpipe as tp
from ..common import BRAND, ConfigOpt, ProfileOpt, SetOpt, console, err, load
from ..plugins import Registry

app = typer.Typer(help="Tailpipe for CloudTrail logs, VPC flow logs and Cost and Usage Reports: set up, collect, check.",
                  no_args_is_help=True)


@app.command("init")
def init(
    discover: Annotated[bool, typer.Option("--discover/--no-discover", help="Find the S3 locations from the account "
                                                                         "(read-only), besides finops.yaml.")] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the config instead of writing it.")] = False,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
) -> None:
    """Find where CloudTrail logs, VPC flow logs and Cost and Usage Reports land in S3, and write the Tailpipe config.

    Read-only: trails and flow logs through Steampipe, CUR exports through the AWS CLI. Locations set in finops.yaml
    (tailpipe.cloudtrail / flow_logs / cur) are always used. Writes only ~/.tailpipe/config/spendlens.tpc.
    Examples:
      spendlens tailpipe init
      spendlens tailpipe init --dry-run
      spendlens tailpipe init --no-discover --set 'tailpipe.cur=[{bucket: my-cur-bucket, prefix: cur}]'
    """
    cfg = load(config, profile, sets)
    from ..ui import activity

    with activity("Finding CloudTrail, flow log and CUR locations [dim](read-only)[/]"):
        found = tp.discover(cfg, discover=discover)
    t = Table(title="Tailpipe sources", header_style=f"bold {BRAND}")
    for c in ("data", "bucket", "prefix", "found in"):
        t.add_column(c, overflow="fold")
    for kind, items in found.sources.items():
        for s in items:
            t.add_row(tp.LABELS[kind], s["bucket"], s["prefix"], s["origin"])
        if not items:
            t.add_row(tp.LABELS[kind], "[dim]none found[/]", "", "")
    console.print(t)
    for n in found.notes:
        err.print(f"[yellow]{n}[/]")
    if dry_run:
        console.print(tp.render(cfg, found))
        return
    if not any(found.sources.values()):
        err.print("Nothing to configure. Set locations in finops.yaml (tailpipe.cloudtrail, flow_logs, cur) or check "
                  "that the role can read CloudTrail trails and flow logs.")
        raise typer.Exit(1)
    path = tp.write(cfg, found)
    err.print(f"[green]Wrote[/] {path}. Next: spendlens tailpipe collect")


@app.command("collect")
def collect(
    kind: Annotated[list[str] | None, typer.Argument(help="cloudtrail, flow_logs or cur (default: all configured).")] = None,
    days: Annotated[int | None, typer.Option("--days", help="How far back (default tailpipe.collect_days).")] = None,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """Collect the configured CloudTrail logs, flow logs and CURs into Tailpipe (reads S3; changes nothing in AWS).

    Examples:
      spendlens tailpipe collect
      spendlens tailpipe collect cloudtrail --days 90
    """
    cfg = load(config, None, sets)
    if [k for k in kind or [] if k not in tp.KINDS]:
        raise typer.BadParameter(f"one of: {', '.join(tp.KINDS)}", param_hint="KIND")
    exe = tp.binary(cfg)
    if not exe:
        err.print("[red]Tailpipe is not installed.[/] Reinstall spendlens (the installer adds it) or see "
                  "https://tailpipe.io.")
        raise typer.Exit(1)
    have = tp.configured()
    todo = [k for k in (kind or tp.KINDS) if have.get(k)]
    if not todo:
        err.print("[red]Nothing configured to collect.[/] Run: spendlens tailpipe init")
        raise typer.Exit(1)
    failed = []
    for k in todo:
        cmd = tp.collect_cmd(exe, k, days or cfg.tailpipe.collect_days)
        err.print(f"[bold]{tp.LABELS[k]}[/] [dim]{' '.join(cmd[1:])}[/]")
        if subprocess.run(cmd, check=False).returncode != 0:
            failed.append(k)
    if failed:
        err.print(f"[red]Collection failed for {', '.join(failed)}.[/] Check the bucket access for the profile.")
        raise typer.Exit(1)
    err.print("[green]Collected.[/] spendlens tailpipe status shows what is there.")


@app.command("status")
def status(config: ConfigOpt = None, sets: SetOpt = None) -> None:
    """What is configured, and how much of each kind has been collected (local; no AWS calls)."""
    from ..graph.costs import CostSourceError, tailpipe_runner

    cfg = load(config, None, sets)
    have = tp.configured()
    t = Table(title=f"Tailpipe · {tp.managed_path()}", header_style=f"bold {BRAND}")
    for c in ("data", "partitions", "rows collected", "from", "to"):
        t.add_column(c)
    exe = tp.binary(cfg)
    run = tailpipe_runner(exe, timeout=120) if exe else None
    for k, table in tp.KINDS.items():
        rows, lo, hi = "", "", ""
        if run:
            try:
                r = run(f"select count(*) as n, min(tp_date) as lo, max(tp_date) as hi from {table}")[0]
                rows, lo, hi = f"{int(r.get('n') or 0):,}", str(r.get("lo") or ""), str(r.get("hi") or "")
            except (CostSourceError, IndexError, ValueError):
                rows = "not collected"
        t.add_row(tp.LABELS[k], str(have.get(k, 0)), rows or "Tailpipe not installed", lo[:10], hi[:10])
    console.print(t)


def register(reg: Registry) -> None:
    reg.add_typer(app, "tailpipe")
