"""`spendlens unused`: resources nobody uses, from their access logs (Tailpipe) and the inventory (Steampipe).

  unused buckets          S3 buckets with no requests in the window (S3 server access logs)
  unused load-balancers   ALB, NLB and Classic load balancers with no requests (their access logs)

Read-only. "unused" needs logs that cover the whole window; without them a resource is "unknown".
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ..common import ConfigOpt, FormatOpt, OutOpt, ProfileOpt, SetOpt, emit, err, load
from ..plugins import Registry
from ..render import Format

app = typer.Typer(help="Resources nobody uses, from their access logs: S3 buckets and load balancers (read-only).",
                  no_args_is_help=True)

DaysOpt = Annotated[int, typer.Option("--days", "-d", min=1, max=400, help="The window, in days.")]
StatusOpt = Annotated[str, typer.Option("--status", help="all, unused, unknown or active.")]


def _show(res: Any, key: str, status: str, fmt: Format, out: Any, title: str, columns: list[str]) -> None:
    if status not in ("all", "unused", "unknown", "active"):
        raise typer.BadParameter("all, unused, unknown or active", param_hint="--status")
    rows = [r for r in res.rows if status == "all" or r["status"] == status]
    c = res.counts
    emit(rows, fmt, out, title, columns=columns)
    if fmt is Format.table:
        err.print(f"[bold]{c['unused']} unused[/] · {c['unknown']} unknown (no access logs covering the window) · "
                  f"{c['active']} active · last {res.days} days")
    for n in res.notes:
        err.print(f"[dim]{n}[/]")


def _client(cfg: Any) -> Any:
    from ..steampipe import SteampipeClient

    return SteampipeClient(cfg.steampipe, timeout_s=300)


@app.command("buckets")
def buckets(
    days: DaysOpt = 30, status: StatusOpt = "all", fmt: FormatOpt = Format.table, out: OutOpt = None,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """S3 buckets nobody read or wrote in the last N days, from S3 server access logs.

    Needs server access logging on the buckets and the logs collected by Tailpipe (spendlens tailpipe init, then
    spendlens tailpipe collect s3_access_logs). AWS's own lifecycle, inventory and replication don't count as use.
    Examples:
      spendlens unused buckets
      spendlens unused buckets --days 90 --status unused -f csv -o unused-buckets.csv
    """
    from .. import usage
    from ..steampipe import SteampipeError

    cfg = load(config, profile, sets)
    try:
        with _client(cfg) as c:
            res = usage.buckets(cfg, days, steampipe=c)
    except SteampipeError as e:
        err.print(f"[red]Steampipe:[/] {str(e).splitlines()[0]}")
        raise typer.Exit(1) from e
    _show(res, "bucket", status, fmt, out, f"S3 buckets by use · last {days} days",
          ["bucket", "status", "requests", "reads", "writes", "last_access", "region", "access_logging"])


@app.command("load-balancers")
def load_balancers(
    days: DaysOpt = 30, status: StatusOpt = "all", fmt: FormatOpt = Format.table, out: OutOpt = None,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Load balancers (ALB, NLB, Classic) with no requests in the last N days, from their access logs.

    Stronger than a CloudWatch check: it counts the actual requests. Needs access logging on the load balancers and
    the logs collected by Tailpipe (spendlens tailpipe init, then spendlens tailpipe collect).
    Examples:
      spendlens unused load-balancers
      spendlens unused load-balancers --days 14 --status unused
    """
    from .. import usage
    from ..steampipe import SteampipeError

    cfg = load(config, profile, sets)
    try:
        with _client(cfg) as c:
            res = usage.load_balancers(cfg, days, steampipe=c)
    except SteampipeError as e:
        err.print(f"[red]Steampipe:[/] {str(e).splitlines()[0]}")
        raise typer.Exit(1) from e
    _show(res, "load_balancer", status, fmt, out, f"Load balancers by use · last {days} days",
          ["load_balancer", "type", "status", "requests", "last_access", "region", "access_logging"])


def register(reg: Registry) -> None:
    reg.add_typer(app, "unused")
