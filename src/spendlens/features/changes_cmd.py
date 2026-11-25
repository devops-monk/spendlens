"""`spendlens changes`: what changed in the AWS account, and who changed it (CloudTrail, read-only)."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from ..common import BRAND, ConfigOpt, FormatOpt, OutOpt, ProfileOpt, SetOpt, console, emit, err, load
from ..models import ReportMeta
from ..plugins import Registry
from ..render import Format


def changes(
    hours: Annotated[int, typer.Option("--hours", help="How far back, in hours.")] = 24,
    days: Annotated[int | None, typer.Option("--days", help="How far back, in days (instead of --hours).")] = None,
    user: Annotated[str | None, typer.Option("--user", "-u", help="Only changes by this user or role (part of the "
                                                                  "name or ARN).")] = None,
    resource: Annotated[str | None, typer.Option("--resource", "-r", help="Only changes to this resource (id or "
                                                                          "name).")] = None,
    service: Annotated[str | None, typer.Option("--service", "-S", help="Only this service, e.g. ec2, iam, s3.")] = None,
    event: Annotated[str | None, typer.Option("--event", "-e", help="Only this API call, e.g. "
                                                                    "AuthorizeSecurityGroupIngress.")] = None,
    source: Annotated[str, typer.Option("--source", help="auto: Tailpipe's CloudTrail logs if collected, else "
                                                         "LookupEvents; tailpipe; lookup_events.")] = "auto",
    top: Annotated[int, typer.Option("--top", help="Events to list.")] = 50,
    fmt: FormatOpt = Format.table,
    out: OutOpt = None,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
) -> None:
    """What changed in the AWS account, and who changed it: CloudTrail write events, newest first.

    Read-only. Uses the trail's logs collected by Tailpipe when there are any (any age, data events), else
    CloudTrail LookupEvents (90 days, management events). Routine calls (role assumptions, KMS, log delivery) are
    left out.
    Examples:
      spendlens changes
      spendlens changes --days 7 --service ec2
      spendlens changes --user alice --days 30
      spendlens changes --resource sg-0abc123 -f csv -o sg-changes.csv
    """
    from ..changes import find_changes
    from ..steampipe import SteampipeError

    if source not in ("auto", "tailpipe", "lookup_events"):
        raise typer.BadParameter("auto, tailpipe or lookup_events", param_hint="--source")
    cfg = load(config, profile, sets)
    window = (days * 24) if days else hours
    from ..ui import activity

    try:
        with activity("Reading CloudTrail [dim](read-only)[/]"):
            res = find_changes(cfg, window, user=user, resource=resource, service=service, event=event, source=source)
    except SteampipeError as e:
        from ..doctor import credential_fix

        err.print(f"[red]CloudTrail:[/] {credential_fix(str(e), cfg.aws.profile, str(e).splitlines()[0])}")
        raise typer.Exit(1) from e
    rows = [{"time": str(r.get("event_time") or "")[:19].replace("T", " "), "who": r.get("who") or "",
             "service": str(r.get("event_source") or "").split(".")[0], "event": r.get("event_name") or "",
             "region": r.get("region") or "", "source_ip": r.get("source_ip") or "",
             "error": r.get("error_code") or "", "details": str(r.get("details") or "")[:200]} for r in res["rows"]]
    meta = ReportMeta(title="AWS changes", extra={"source": res["source"], "hours": res["hours"], "count": res["count"]})
    if fmt is not Format.table:
        emit(rows, fmt, out, "AWS changes", meta=meta)
        return
    if not rows:
        console.print(f"[green]No changes found[/] in the last {res['hours']} hours ({res['source']}).")
    else:
        t = Table(title=f"Who changed most · last {res['hours']} hours", header_style=f"bold {BRAND}")
        t.add_column("who", overflow="fold")
        t.add_column("changes", justify="right")
        for w in res["by_who"][:8]:
            t.add_row(w["who"], str(w["changes"]))
        console.print(t)
        emit(rows[:top], fmt, None, f"{res['count']} change(s), newest first",
             columns=["time", "who", "service", "event", "region", "error"], meta=meta)
    for n in res["notes"]:
        err.print(f"[dim]{n}[/]")


def register(reg: Registry) -> None:
    reg.add_command(changes, "changes")
