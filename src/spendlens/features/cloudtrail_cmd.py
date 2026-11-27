"""`spendlens cloudtrail`: who did what, when. Search, filter, summarise and export CloudTrail events (read-only).

  cloudtrail events    the events, newest first, with every filter; export to csv, xlsx, json, jsonl, md, html, yaml
  cloudtrail summary   counts by who, API call, service, source IP, error and day for the same filters
  cloudtrail trails    the account's trails, where they deliver, and which sources spendlens will read
  cloudtrail presets   ready-made filters: console logins, deletes, deployments, security groups, IAM, errors ...
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from .. import cloudtrail as ct
from ..common import BRAND, ConfigOpt, ProfileOpt, SetOpt, console, err, load
from ..plugins import Registry

app = typer.Typer(help="CloudTrail: who did what, when. Search, filter, summarise and export events (read-only).",
                  no_args_is_help=True)

FORMATS = ("table", "csv", "tsv", "json", "jsonl", "md", "html", "xlsx", "yaml")

FromOpt = Annotated[str | None, typer.Option("--from", help="Start: 2026-11-01, '2026-11-01 14:30' (UTC), or 6h / 2d / "
                                                            "1w ago.", rich_help_panel="Time")]
ToOpt = Annotated[str | None, typer.Option("--to", help="End (default now). A date alone means the end of that day.",
                                           rich_help_panel="Time")]
HoursOpt = Annotated[float | None, typer.Option("--hours", help="The last N hours (default 24).", rich_help_panel="Time")]
DaysOpt = Annotated[float | None, typer.Option("--days", help="The last N days.", rich_help_panel="Time")]
UserOpt = Annotated[str | None, typer.Option("--user", "-u", help="Part of a user, role or session name or ARN.",
                                             rich_help_panel="Filters")]
EventOpt = Annotated[list[str] | None, typer.Option("--event", "-e", help="API call, exact or with * (Delete*). "
                                                                         "Repeat or comma-separate.",
                                                    rich_help_panel="Filters")]
ServiceOpt = Annotated[str | None, typer.Option("--service", "-S", help="ec2, iam, s3, ecs ... or "
                                                                        "ecs.amazonaws.com.", rich_help_panel="Filters")]
ResourceOpt = Annotated[str | None, typer.Option("--resource", "-r", help="A resource id, name or ARN.",
                                                 rich_help_panel="Filters")]
TypeOpt = Annotated[str | None, typer.Option("--resource-type", help="e.g. AWS::ECS::Service, AWS::S3::Bucket.",
                                             rich_help_panel="Filters")]
KeyOpt = Annotated[str | None, typer.Option("--access-key", help="An access key id (AKIA... or ASIA...).",
                                            rich_help_panel="Filters")]
IpOpt = Annotated[str | None, typer.Option("--source-ip", help="An IP address or CIDR range (10.0.0.0/8).",
                                           rich_help_panel="Filters")]
ErrorOpt = Annotated[list[str] | None, typer.Option("--error", help="Error code, exact or with * (AccessDenied*).",
                                                    rich_help_panel="Filters")]
ErrorsOnlyOpt = Annotated[bool, typer.Option("--errors-only", help="Only failed calls.", rich_help_panel="Filters")]
ReadsOpt = Annotated[str, typer.Option("--reads", help="writes (default), reads or all.", rich_help_panel="Filters")]
RegionOpt = Annotated[str | None, typer.Option("--region", help="Only events in this region.",
                                               rich_help_panel="Filters")]
NoiseOpt = Annotated[bool, typer.Option("--include-noise", help="Keep routine calls (AssumeRole, KMS, log "
                                                                "delivery).", rich_help_panel="Filters")]
PresetOpt = Annotated[str | None, typer.Option("--preset", "-P", help="Ready-made filters: see `spendlens cloudtrail "
                                                                      "presets`.", rich_help_panel="Filters")]
SourceOpt = Annotated[str, typer.Option("--source", help="auto (Tailpipe when configured and up to date, else "
                                                         "Steampipe); aws (Steampipe only: CloudWatch Logs, then "
                                                         "LookupEvents); tailpipe; cloudwatch; lookup.", rich_help_panel="Source")]
WaitOpt = Annotated[float | None, typer.Option("--max-wait", help="Seconds before a slow AWS search stops and shows what "
                                                                  "it found (default cloudtrail.max_wait_s).",
                                               rich_help_panel="Source")]
LimitOpt = Annotated[int | None, typer.Option("--limit", help="Most events (default cloudtrail.max_rows).",
                                              rich_help_panel="Source")]
FmtOpt = Annotated[str | None, typer.Option("--format", "-f", help=f"{' | '.join(FORMATS)} (default: from the -o "
                                                                   "extension, else table).", rich_help_panel="Output")]
OutOpt = Annotated[Path | None, typer.Option("--out", "-o", help="Write to this file.", rich_help_panel="Output")]
SqlOpt = Annotated[bool, typer.Option("--show-sql", help="Print the queries that were run.", rich_help_panel="Output")]


def _filters(*, start: str | None, end: str | None, hours: float | None, days: float | None, **kw: Any) -> ct.Filters:
    try:
        return ct.Filters.build(start=start, end=end, hours=(days * 24) if days else hours, **kw)
    except ct.CloudTrailError as e:
        raise typer.BadParameter(str(e)) from e


def _format(fmt: str | None, out: Path | None) -> str:
    f = (fmt or (out.suffix.lstrip(".").lower() if out and out.suffix else "table")).lower()
    f = {"yml": "yaml", "jsonlines": "jsonl", "markdown": "md", "excel": "xlsx"}.get(f, f)
    if f not in FORMATS:
        raise typer.BadParameter(f"one of {', '.join(FORMATS)}", param_hint="--format")
    if f == "xlsx" and out is None:
        raise typer.BadParameter("an Excel file needs -o, e.g. -o events.xlsx", param_hint="--out")
    return f


def _run(cfg: Any, f: ct.Filters, source: str, max_wait: float | None, limit: int | None) -> ct.Result:
    from ..steampipe import SteampipeError
    from ..ui import activity

    if source not in ct.SOURCES:
        raise typer.BadParameter(f"one of {', '.join(ct.SOURCES)}", param_hint="--source")
    try:
        with activity("Reading CloudTrail [dim](read-only)[/]"):
            return ct.search(cfg, f, source=source, limit=limit or cfg.cloudtrail.max_rows,
                             max_wait_s=max_wait or cfg.cloudtrail.max_wait_s)
    except (ct.CloudTrailError, SteampipeError) as e:
        from ..doctor import credential_fix

        err.print(f"[red]CloudTrail:[/] {credential_fix(str(e), cfg.aws.profile, str(e).splitlines()[0])}")
        raise typer.Exit(1) from e


def _write(fmt: str, out: Path | None, sheets: list[tuple[str, list[str], list[list[Any]]]]) -> None:
    from ..ai.files import table_data

    data = table_data(f".{fmt}", sheets[0][0], sheets)
    if out is None:
        typer.echo(data if isinstance(data, str) else "", nl=False)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        out.write_bytes(data)
    else:
        out.write_text(data, encoding="utf-8")
    err.print(f"[green]Wrote[/] {out}")


def _footer(res: ct.Result, show_sql: bool) -> None:
    span = f"{res.covered_from:%Y-%m-%d %H:%M} to {res.filters.end:%Y-%m-%d %H:%M} UTC"
    err.print(f"[dim]{len(res.rows)} event(s) · {span} · from {res.source}"
              + (" · [yellow]partial[/]" if res.partial else "") + (" · limit reached" if res.truncated else "") + "[/]")
    for n in res.notes:
        err.print(f"[dim]{n}[/]")
    if show_sql:
        from rich.syntax import Syntax

        for q in res.queries:
            err.print(f"[bold]{q['source']}[/]")
            err.print(Syntax(q["sql"], "sql", word_wrap=True, background_color="default"))


@app.command("events")
def events(
    start: FromOpt = None, end: ToOpt = None, hours: HoursOpt = None, days: DaysOpt = None,
    user: UserOpt = None, event: EventOpt = None, service: ServiceOpt = None, resource: ResourceOpt = None,
    resource_type: TypeOpt = None, access_key: KeyOpt = None, source_ip: IpOpt = None, error: ErrorOpt = None,
    errors_only: ErrorsOnlyOpt = False, reads: ReadsOpt = "writes", region: RegionOpt = None,
    include_noise: NoiseOpt = False, preset: PresetOpt = None,
    source: SourceOpt = "auto", max_wait: WaitOpt = None, limit: LimitOpt = None,
    fmt: FmtOpt = None, out: OutOpt = None, show_sql: SqlOpt = False,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """CloudTrail events, newest first: who did what, when, from where, and whether it failed.

    Read-only. Reads the trail's logs collected by Tailpipe when there are any, else the trail's CloudWatch Logs
    group, else LookupEvents (90 days of management events, no setup).
    Examples:
      spendlens cloudtrail events --days 7 --event UpdateService
      spendlens cloudtrail events --from 2026-11-01 --to 2026-11-20 --service iam -o iam.xlsx
      spendlens cloudtrail events --preset console-logins --days 30 --reads all -f csv -o logins.csv
      spendlens cloudtrail events --user alice --event 'Delete*' --days 30
      spendlens cloudtrail events --errors-only --error 'AccessDenied*' --reads all --hours 6
      spendlens cloudtrail events --resource sg-0abc123 --days 90 --show-sql
    """
    form = _format(fmt, out)
    cfg = load(config, profile, sets)
    f = _filters(start=start, end=end, hours=hours, days=days, user=user, events=event, service=service,
                 resource=resource, resource_type=resource_type, access_key=access_key, source_ip=source_ip,
                 errors=error, errors_only=errors_only or None, reads=reads if reads != "writes" else None,
                 region=region, include_noise=include_noise or None, preset=preset)
    res = _run(cfg, f, source, max_wait, limit)
    if form == "table":
        if not res.rows:
            console.print("[green]No matching events.[/]")
        else:
            t = Table(title=f"CloudTrail events · {res.source}", header_style=f"bold {BRAND}")
            for c in ("time (UTC)", "who", "event", "service", "region", "source ip", "error"):
                t.add_column(c, overflow="fold")
            for r in res.rows:
                who = str(r["who"] or "")
                t.add_row(r["event_time"][:19].replace("T", " "), who.split("/")[-1] if "/" in who else who,
                          r["event_name"] or "", str(r["event_source"] or "").split(".")[0], r["region"] or "",
                          r["source_ip"] or "", f"[red]{r['error_code']}[/]" if r["error_code"] else "")
            console.print(t)
    else:
        _write(form, out, [("events", ct.COLUMNS, [[r[c] for c in ct.COLUMNS] for r in res.rows])])
    _footer(res, show_sql)


@app.command("summary")
def summary(
    start: FromOpt = None, end: ToOpt = None, hours: HoursOpt = None, days: DaysOpt = None,
    user: UserOpt = None, event: EventOpt = None, service: ServiceOpt = None, resource: ResourceOpt = None,
    resource_type: TypeOpt = None, access_key: KeyOpt = None, source_ip: IpOpt = None, error: ErrorOpt = None,
    errors_only: ErrorsOnlyOpt = False, reads: ReadsOpt = "writes", region: RegionOpt = None,
    include_noise: NoiseOpt = False, preset: PresetOpt = None,
    source: SourceOpt = "auto", max_wait: WaitOpt = None, limit: LimitOpt = None,
    fmt: FmtOpt = None, out: OutOpt = None, show_sql: SqlOpt = False,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Who did most, which API calls, services, source IPs, errors and days, for the same filters as `events`.

    Examples:
      spendlens cloudtrail summary --days 7
      spendlens cloudtrail summary --preset deletes --days 30 -o deletes.xlsx
    """
    form = _format(fmt, out)
    cfg = load(config, profile, sets)
    f = _filters(start=start, end=end, hours=hours, days=days, user=user, events=event, service=service,
                 resource=resource, resource_type=resource_type, access_key=access_key, source_ip=source_ip,
                 errors=error, errors_only=errors_only or None, reads=reads if reads != "writes" else None,
                 region=region, include_noise=include_noise or None, preset=preset)
    res = _run(cfg, f, source, max_wait, limit)
    s = ct.summarise(res.rows)
    groups = [("who", "by_who"), ("API call", "by_event"), ("service", "by_service"), ("source IP", "by_source_ip"),
              ("error", "by_error"), ("day", "by_day")]
    if form == "table":
        for title, key in groups:
            if not s[key]:
                continue
            t = Table(title=f"By {title}", header_style=f"bold {BRAND}")
            t.add_column(title, overflow="fold")
            t.add_column("events", justify="right")
            for x in s[key][:10]:
                t.add_row(str(x["name"]), f"{x['count']:,}")
            console.print(t)
        if not res.rows:
            console.print("[green]No matching events.[/]")
    elif form == "json":
        import json

        text = json.dumps({**res.to_dict(rows=0), "rows": None}, default=str, indent=2) + "\n"
        if out:
            out.write_text(text, encoding="utf-8")
            err.print(f"[green]Wrote[/] {out}")
        else:
            typer.echo(text, nl=False)
    else:
        sheets = [(f"by {title}", [title, "events"], [[x["name"], x["count"]] for x in s[key]])
                  for title, key in groups]
        sheets.append(("events", ct.COLUMNS, [[r[c] for c in ct.COLUMNS] for r in res.rows]))
        _write(form, out, sheets)
    _footer(res, show_sql)


@app.command("trails")
def trails(config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None) -> None:
    """The account's trails, where each delivers (S3, CloudWatch Logs), and which sources spendlens will read.

    Read-only (cloudtrail:DescribeTrails, GetTrailStatus, GetEventSelectors through Steampipe)."""
    from .. import tailpipe as tp
    from ..steampipe import SteampipeClient, SteampipeError

    cfg = load(config, profile, sets)
    sql = ("select distinct name, home_region, is_multi_region_trail as multi_region, is_organization_trail as org, "
           "is_logging as logging, s3_bucket_name, log_group_arn, has_custom_event_selectors as custom_events "
           "from aws_cloudtrail_trail order by name")
    try:
        with SteampipeClient(cfg.steampipe, timeout_s=120) as c:
            rows = c.query(sql)
            lg = ct.log_group_of(cfg, c)
    except SteampipeError as e:
        from ..doctor import credential_fix

        err.print(f"[red]CloudTrail:[/] {credential_fix(str(e), cfg.aws.profile, str(e).splitlines()[0])}")
        raise typer.Exit(1) from e
    t = Table(title="CloudTrail trails", header_style=f"bold {BRAND}")
    for col in ("trail", "home region", "multi-region", "organisation", "logging", "S3 bucket", "CloudWatch Logs"):
        t.add_column(col, overflow="fold")
    for r in rows:
        group = str(r.get("log_group_arn") or "").split(":log-group:")[-1].rstrip(":*") if r.get("log_group_arn") else ""
        t.add_row(str(r["name"]), str(r.get("home_region") or ""), "yes" if r.get("multi_region") else "no",
                  "yes" if r.get("org") else "no", "[green]yes[/]" if r.get("logging") else "[red]no[/]",
                  str(r.get("s3_bucket_name") or ""), group)
    console.print(t if rows else "[yellow]No trails in this account.[/] LookupEvents still has the last 90 days of "
                                 "management events (an organisation trail in another account may record more).")
    have = tp.configured().get("cloudtrail", 0)
    console.print("[bold]Sources spendlens reads, best first[/]")
    console.print(f"  1. Tailpipe:        {'[green]' + str(have) + ' CloudTrail partition(s)[/]' if have else '[dim]not set up (spendlens tailpipe init)[/]'}")
    console.print(f"  2. CloudWatch Logs: {'[green]' + lg[0] + ' (' + str(lg[1] or 'default region') + ')[/]' if lg else '[dim]none (the trail does not send to CloudWatch Logs)[/]'}")
    console.print("  3. LookupEvents:    [green]always[/] (90 days, management events, one filter per call)")


@app.command("presets")
def presets() -> None:
    """Ready-made filters for common questions: use with --preset on events and summary."""
    t = Table(title="CloudTrail presets", header_style=f"bold {BRAND}")
    for col in ("preset", "what it finds", "filters"):
        t.add_column(col, overflow="fold")
    for name, p in ct.PRESETS.items():
        rules = [f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in p.items() if k != "help"]
        t.add_row(name, p["help"], "; ".join(rules))
    console.print(t)
    console.print("[dim]Example: spendlens cloudtrail events --preset deletes --days 30 -o deletes.xlsx[/]")


def register(reg: Registry) -> None:
    reg.add_typer(app, "cloudtrail")
