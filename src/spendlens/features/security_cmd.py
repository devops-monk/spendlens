"""`spendlens security`: vulnerabilities, threats and guardrails from AWS's own security services. Read-only.

  security status       which security services are on, per region, and what Inspector scans
  security findings     every finding, with filters: severity, source, resource type, CVE, package, fix, exploit
  security image        vulnerabilities in one container image (Inspector, else ECR basic scanning)
  security lambda       vulnerabilities in one Lambda function
  security instance     vulnerabilities and threats on one EC2 instance
  security resource     everything about one resource, from every source
  security detections   threat detections on the CloudTrail, S3 access and VPC flow logs Tailpipe collected
  security summary      counts by severity, source and resource type; the CVEs and resources to fix first
  security gate         check the guardrails in finops.yaml `security:`; exit 1 on a violation (for CI)
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from .. import security as sec
from ..common import BRAND, ConfigOpt, ProfileOpt, SetOpt, console, err, history_path, load
from ..plugins import Registry
from .cloudtrail_cmd import FmtOpt, OutOpt, _format, _write

app = typer.Typer(help="Security guardrails: vulnerabilities (Inspector, ECR), threats (GuardDuty, log detections), "
                       "findings (Security Hub, Access Analyzer) and a CI gate. Read-only.", no_args_is_help=True)

SevOpt = Annotated[str, typer.Option("--severity", help="Lowest severity to show: critical, high, medium, low.",
                                     rich_help_panel="Filters")]
SourceOpt = Annotated[list[str] | None, typer.Option("--source", help="inspector, ecr, securityhub, guardduty, "
                                                                      "access_analyzer, detections (repeatable; "
                                                                      "default security.sources).",
                                                     rich_help_panel="Filters")]
TypeOpt = Annotated[str | None, typer.Option("--type", help="image, lambda or instance.", rich_help_panel="Filters")]
CveOpt = Annotated[str | None, typer.Option("--cve", help="One vulnerability, e.g. CVE-2024-3094.",
                                            rich_help_panel="Filters")]
PkgOpt = Annotated[str | None, typer.Option("--package", help="Part of a package name, e.g. openssl.",
                                            rich_help_panel="Filters")]
FixOpt = Annotated[bool, typer.Option("--fixable", help="Only findings with a fix available.", rich_help_panel="Filters")]
ExpOpt = Annotated[bool, typer.Option("--exploitable", help="Only findings with a known exploit.",
                                      rich_help_panel="Filters")]
RegionOpt = Annotated[str | None, typer.Option("--region", help="Only this region.", rich_help_panel="Filters")]
LimitOpt = Annotated[int | None, typer.Option("--limit", help="Most findings (default security.max_rows).",
                                              rich_help_panel="Output")]
SaveOpt = Annotated[bool, typer.Option("--save/--no-save", help="Save the run to the history (spendlens history diff "
                                                                "shows new and resolved findings).",
                                       rich_help_panel="Output")]


def _scope(cfg: Any, **kw: Any) -> sec.Scope:
    sources = kw.pop("sources", None) or list(cfg.security.sources)
    try:
        sec.at_least(kw.get("min_severity") or "LOW")
    except sec.SecurityError as e:
        raise typer.BadParameter(str(e), param_hint="--severity") from e
    if kw.get("resource_type") and kw["resource_type"] not in sec.RESOURCE_KINDS:
        raise typer.BadParameter("image, lambda or instance", param_hint="--type")
    kw["min_severity"] = (kw.get("min_severity") or "LOW").upper()
    kw["limit"] = kw.get("limit") or cfg.security.max_rows
    return sec.Scope(sources=sources, **{k: v for k, v in kw.items() if v is not None})


def _search(cfg: Any, scope: sec.Scope) -> sec.Result:
    from ..steampipe import SteampipeError
    from ..ui import activity

    try:
        with activity("Reading AWS security findings [dim](read-only)[/]"):
            return sec.search(cfg, scope)
    except (sec.SecurityError, SteampipeError) as e:
        from ..doctor import credential_fix

        err.print(f"[red]Security:[/] {credential_fix(str(e), cfg.aws.profile, str(e).splitlines()[0])}")
        raise typer.Exit(1) from e


def _save(cfg: Any, res: sec.Result) -> None:
    if not cfg.history.enabled:
        return
    from ..history import HistoryStore

    try:
        with HistoryStore(history_path(cfg)) as store:
            store.save_run("security", sec.to_history(res), meta={"scope": res.to_dict(rows=0)["scope"]})
            store.prune(cfg.history.keep_runs)
    except Exception as e:  # noqa: BLE001 - history must never break a run
        err.print(f"[yellow]Not saved to history: {e}[/]")


SEV_STYLE = {"CRITICAL": "bold red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "dim", "INFORMATIONAL": "dim"}


def _table(res: sec.Result, title: str, top: int) -> None:
    if not res.findings:
        console.print("[green]No findings[/] for this scope.")
        return
    t = Table(title=title, header_style=f"bold {BRAND}")
    for c in ("severity", "source", "resource", "finding", "package / actor", "fix", "age"):
        t.add_column(c, overflow="fold")
    for f in res.findings[:top]:
        fix = {"YES": "[green]yes[/]", "NO": "no", "PARTIAL": "partial"}.get(f.fix_available, "")
        if f.exploit_available == "YES":
            fix += " [red]exploit[/]"
        t.add_row(f"[{SEV_STYLE.get(f.severity, '')}]{f.severity.lower()}[/]", f.source, escape(f.resource[:80]),
                  escape((f.vulnerability_id + " · " if f.vulnerability_id and f.source != "detections" else "")
                         + f.title[:110]),
                  escape(f.package + (f" → {f.fixed_in}" if f.fixed_in else "") or f.actor[:60]), fix,
                  "" if f.age_days is None else f"{f.age_days}d")
    console.print(t)
    if len(res.findings) > top:
        console.print(f"[dim]… {len(res.findings) - top} more: -f csv -o findings.csv for every one[/]")


def _footer(res: sec.Result) -> None:
    s = res.summary()
    bysev = " · ".join(f"{n} {k.lower()}" for k, n in s["by_severity"].items() if n)
    err.print(f"[dim]{s['total']} finding(s){': ' + bysev if bysev else ''}"
              + (f" · fix first (high+, exploit known, fix available): {s['fix_first']}" if s["fix_first"] else "")
              + "[/]")
    for n in res.notes:
        err.print(f"[dim]{n}[/]")


def _output(res: sec.Result, fmt: str | None, out: Path | None, title: str, top: int = 50) -> None:
    form = _format(fmt, out)
    if form == "table":
        _table(res, title, top)
    else:
        _write(form, out, [("findings", sec.COLUMNS, [[f.row()[c] for c in sec.COLUMNS] for f in res.findings])])
    _footer(res)


@app.command("findings")
def findings(
    severity: SevOpt = "low", source: SourceOpt = None, rtype: TypeOpt = None, cve: CveOpt = None,
    package: PkgOpt = None, fixable: FixOpt = False, exploitable: ExpOpt = False, region: RegionOpt = None,
    resource: Annotated[str | None, typer.Option("--resource", "-r", help="Part of a resource id, name or ARN.",
                                                 rich_help_panel="Filters")] = None,
    days: Annotated[int | None, typer.Option("--days", help="Detections: only hits in the last N days.",
                                             rich_help_panel="Filters")] = None,
    limit: LimitOpt = None, fmt: FmtOpt = None, out: OutOpt = None, save: SaveOpt = True,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Every security finding, worst first: Inspector, Security Hub, GuardDuty, Access Analyzer (and log detections).

    Examples:
      spendlens security findings --severity high
      spendlens security findings --type image --fixable --exploitable
      spendlens security findings --cve CVE-2024-3094
      spendlens security findings --package openssl -o openssl.xlsx
      spendlens security findings --source detections --days 7
    """
    cfg = load(config, profile, sets)
    scope = _scope(cfg, sources=source, min_severity=severity, resource_type=rtype, cve=cve, package=package,
                   fixable=fixable, exploitable=exploitable, region=region, resource=resource, limit=limit, days=days)
    res = _search(cfg, scope)
    if save and not (rtype or cve or package or resource or fixable or exploitable or region):
        _save(cfg, res)
    _output(res, fmt, out, "Security findings, worst first")


def _one(cfg: Any, title: str, fmt: str | None, out: Path | None, **kw: Any) -> None:
    sources = kw.pop("sources", None) or ["inspector", "ecr", "securityhub", "guardduty", "access_analyzer"]
    res = _search(cfg, _scope(cfg, sources=sources, **kw))
    _output(res, fmt, out, title, top=100)


@app.command("image")
def image(
    image: Annotated[str, typer.Argument(help="repository, repository:tag or repository@sha256:digest.")],
    severity: SevOpt = "low", fixable: FixOpt = False, package: PkgOpt = None,
    fmt: FmtOpt = None, out: OutOpt = None,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Vulnerabilities in one container image: Amazon Inspector, or ECR basic scanning when Inspector has none.

    Examples:
      spendlens security image payments-api:1.4.2
      spendlens security image payments-api@sha256:4f53… --severity high --fixable
    """
    if not sec.IMAGE_RE.match(image):
        raise typer.BadParameter("repository, repository:tag or repository@sha256:<64 hex>", param_hint="IMAGE")
    cfg = load(config, profile, sets)
    _one(cfg, f"Vulnerabilities in {image}", fmt, out, image=image, min_severity=severity, fixable=fixable,
         package=package, sources=["inspector", "ecr"])


@app.command("lambda")
def lambda_(
    function: Annotated[str, typer.Argument(help="Lambda function name.")],
    severity: SevOpt = "low", fixable: FixOpt = False,
    fmt: FmtOpt = None, out: OutOpt = None,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Vulnerabilities in one Lambda function: its packages and layers, and its code (Inspector).

    Example:
      spendlens security lambda orders-processor --severity high
    """
    cfg = load(config, profile, sets)
    _one(cfg, f"Vulnerabilities in Lambda {function}", fmt, out, function=function, min_severity=severity,
         fixable=fixable, sources=["inspector"])


@app.command("instance")
def instance(
    instance_id: Annotated[str, typer.Argument(help="EC2 instance id, e.g. i-0abc123.")],
    severity: SevOpt = "low", fmt: FmtOpt = None, out: OutOpt = None,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Vulnerabilities (Inspector) and threats (GuardDuty, Security Hub) on one EC2 instance.

    Example:
      spendlens security instance i-0abc1234567890def
    """
    cfg = load(config, profile, sets)
    _one(cfg, f"Security findings on {instance_id}", fmt, out, instance=instance_id, min_severity=severity,
         sources=["inspector", "securityhub", "guardduty"])


@app.command("resource")
def resource_(
    resource: Annotated[str, typer.Argument(help="Any resource id, name or ARN (or part of it).")],
    severity: SevOpt = "low", fmt: FmtOpt = None, out: OutOpt = None,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Everything every security source says about one resource.

    Example:
      spendlens security resource my-data-bucket
    """
    cfg = load(config, profile, sets)
    _one(cfg, f"Security findings on {resource}", fmt, out, resource=resource, min_severity=severity,
         sources=["inspector", "securityhub", "guardduty", "access_analyzer"])


@app.command("detections")
def detections(
    days: Annotated[int, typer.Option("--days", "-d", min=1, max=400, help="Only hits in the last N days.")] = 7,
    pack: Annotated[list[str] | None, typer.Option("--pack", help="cloudtrail, s3_access or flow_logs "
                                                                  "(repeatable; default: every pack with logs).")] = None,
    severity: SevOpt = "low", fmt: FmtOpt = None, out: OutOpt = None,
    config: ConfigOpt = None, sets: SetOpt = None,
) -> None:
    """Threat detections on the logs Tailpipe collected: 98 detections from Turbot's detection mods, MITRE mapped.

    CloudTrail (86: IAM, S3, EC2, KMS, CloudTrail, GuardDuty, VPC changes and risky calls), S3 server access logs
    (6) and VPC flow logs (6). Local: Powerpipe reads Tailpipe's database, no AWS calls.
    Examples:
      spendlens security detections
      spendlens security detections --days 30 --pack cloudtrail --severity high -o detections.xlsx
    """
    from .. import log_detections as ld
    from ..ui import activity

    cfg = load(config, None, sets)
    try:
        sec.at_least(severity)
        with activity("Running log detections [dim](Powerpipe on Tailpipe's database, local)[/]"):
            got = ld.run(cfg, pack, days)
    except (ld.DetectionError, sec.SecurityError) as e:
        raise typer.BadParameter(str(e)) from e
    res = sec.Result([f for f in got["findings"] if sec.RANK.get(f.severity, 9) <= sec.RANK[severity.upper()]],
                     sec.Scope(sources=["detections"], days=days), notes=got["notes"])
    form = _format(fmt, out)
    if form == "table":
        if got["summary"]:
            t = Table(title=f"Detections with hits · last {days} days", header_style=f"bold {BRAND}")
            for c in ("severity", "detection", "hits", "MITRE ATT&CK"):
                t.add_column(c, overflow="fold")
            for d in got["summary"]:
                if sec.RANK.get(d["severity"], 9) <= sec.RANK[severity.upper()]:
                    t.add_row(f"[{SEV_STYLE.get(d['severity'], '')}]{d['severity'].lower()}[/]", escape(d["title"] or ""),
                              str(d["hits"]), escape(d.get("mitre") or ""))
            console.print(t)
        _table(res, "Latest hits", 30)
    else:
        _write(form, out, [("detections", sec.COLUMNS, [[f.row()[c] for c in sec.COLUMNS] for f in res.findings])])
    if not got["ran"]:
        err.print("[yellow]No detections ran:[/] they need CloudTrail, S3 access or VPC flow logs in Tailpipe "
                  "(spendlens tailpipe init, then spendlens tailpipe collect).")
    _footer(res)


@app.command("summary")
def summary(
    severity: SevOpt = "low", source: SourceOpt = None, fmt: FmtOpt = None, out: OutOpt = None,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Counts by severity, source and resource type, the most widespread CVEs and the resources with most findings.

    Examples:
      spendlens security summary
      spendlens security summary -o security.xlsx
    """
    cfg = load(config, profile, sets)
    res = _search(cfg, _scope(cfg, sources=source, min_severity=severity))
    s = res.summary()
    form = _format(fmt, out)
    if form == "json":
        import json

        text = json.dumps({**res.to_dict(rows=0), "findings": None}, default=str, indent=2) + "\n"
        (out.write_text(text) if out else typer.echo(text, nl=False))
    elif form == "table":
        for title, rows, cols in (
                ("By severity", [[k.lower(), v] for k, v in s["by_severity"].items()], ["severity", "findings"]),
                ("By source", [[k, v] for k, v in s["by_source"].items()], ["source", "findings"]),
                ("By resource type", [[k, v] for k, v in s["by_resource_type"].items()], ["type", "findings"]),
                ("Most widespread CVEs", [[x["cve"], x["resources"]] for x in s["top_cves"]], ["CVE", "findings"]),
                ("Resources with most findings", [[x["resource_type"], x["resource"], x["findings"]]
                                                  for x in s["top_resources"]], ["type", "resource", "findings"])):
            if not rows:
                continue
            t = Table(title=title, header_style=f"bold {BRAND}")
            for c in cols:
                t.add_column(c, overflow="fold")
            for r in rows:
                t.add_row(*[escape(str(x)) for x in r])
            console.print(t)
    else:
        _write(form, out, [
            ("by severity", ["severity", "findings"], [[k, v] for k, v in s["by_severity"].items()]),
            ("by resource type", ["type", "findings"], [[k, v] for k, v in s["by_resource_type"].items()]),
            ("top CVEs", ["cve", "findings"], [[x["cve"], x["resources"]] for x in s["top_cves"]]),
            ("findings", sec.COLUMNS, [[f.row()[c] for c in sec.COLUMNS] for f in res.findings])])
    _footer(res)


@app.command("status")
def status(
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable.")] = False,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Which security services are on in each configured region, and what Inspector scans.

    Example:
      spendlens security status
    """
    import json

    cfg = load(config, profile, sets)
    try:
        st = sec.status(cfg)
    except Exception as e:
        err.print(f"[red]Security:[/] {str(e).splitlines()[0]}")
        raise typer.Exit(1) from e
    if json_out:
        typer.echo(json.dumps(st, default=str, indent=2))
        return
    t = Table(title=f"Security services · regions {', '.join(st['regions'])}", header_style=f"bold {BRAND}")
    for c in ("service", "on in", "off in / why"):
        t.add_column(c, overflow="fold")
    names = {"inspector": "Amazon Inspector", "securityhub": "Security Hub", "guardduty": "GuardDuty",
             "access_analyzer": "IAM Access Analyzer", "ecr_scanning": "ECR enhanced scanning"}
    for svc, info in st["services"].items():
        t.add_row(names.get(svc, svc), "[green]" + ", ".join(info.get("on") or []) + "[/]" if info.get("on") else "-",
                  f"[red]{info['error']}[/]" if info.get("error") else ", ".join(info.get("off") or []))
    console.print(t)
    if st["inspector_coverage"]:
        c = Table(title="Inspector coverage", header_style=f"bold {BRAND}")
        for col in ("resource type", "scan status", "resources"):
            c.add_column(col)
        for r in st["inspector_coverage"]:
            c.add_row(str(r.get("resource_type")), str(r.get("scan_status_code")), str(r.get("n")))
        console.print(c)
    for n in st["notes"]:
        err.print(f"[dim]{n}[/]")


@app.command("gate")
def gate(
    image: Annotated[str | None, typer.Option("--image", help="Gate one container image (repository:tag).")] = None,
    function: Annotated[str | None, typer.Option("--lambda", help="Gate one Lambda function.")] = None,
    fail_on: Annotated[str | None, typer.Option("--fail-on", help="critical, high, medium or low (default "
                                                                  "security.fail_on).")] = None,
    services: Annotated[bool, typer.Option("--services/--no-services", help="Also require security.required_services "
                                                                            "to be on.")] = True,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable result.")] = False,
    config: ConfigOpt = None, profile: ProfileOpt = None, sets: SetOpt = None,
) -> None:
    """Check the guardrails in finops.yaml `security:` and exit 1 on a violation: for CI, before a deploy.

    Fails on: a finding at or above fail_on; a finding open longer than its max_age_days; an accepted finding past its
    expiry; a required service that is off. Accepted findings (security.ignore) with a reason and expiry pass.
    Examples:
      spendlens security gate
      spendlens security gate --image payments-api:1.4.2 --fail-on high
      spendlens security gate --lambda orders-processor --json
    """
    import json

    cfg = load(config, profile, sets)
    if image and not sec.IMAGE_RE.match(image):
        raise typer.BadParameter("repository, repository:tag or repository@sha256:<64 hex>", param_hint="--image")
    try:
        if fail_on:
            sec.at_least(fail_on)
    except sec.SecurityError as e:
        raise typer.BadParameter(str(e), param_hint="--fail-on") from e
    scoped = bool(image or function)
    srcs = ["inspector", "ecr"] if scoped else list(cfg.security.sources)
    res = _search(cfg, _scope(cfg, sources=srcs, image=image, function=function))
    st = sec.status(cfg) if services and cfg.security.required_services and not scoped else None
    v = sec.gate(cfg, res, st, fail_on=fail_on)
    if json_out:
        typer.echo(json.dumps({"passed": not v, "violations": [x.__dict__ for x in v], **res.summary(),
                               "unavailable": res.unavailable}, default=str, indent=2))
    elif not v:
        console.print(f"[bold green]✓ Security gate passed[/] · {len(res.findings)} finding(s) checked "
                      f"(fail on {(fail_on or cfg.security.fail_on).lower()} and above)")
    else:
        t = Table(title=f"✗ Security gate failed: {len(v)} violation(s)", header_style="bold red")
        for c in ("rule", "detail"):
            t.add_column(c, overflow="fold")
        for x in v[:100]:
            t.add_row(x.rule, escape(x.detail[:200]))
        console.print(t)
    for n in res.notes:
        err.print(f"[dim]{n}[/]")
    if res.unavailable and not res.findings:
        err.print("[yellow]No source could be read: the gate cannot pass on no data.[/]")
        raise typer.Exit(2)
    raise typer.Exit(1 if v else 0)


def register(reg: Registry) -> None:
    reg.add_typer(app, "security")
