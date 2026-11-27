"""Tailpipe setup: find where AWS writes logs, findings and billing exports, write the Tailpipe config, collect,
and report what is there.

Twelve kinds of data (the Tailpipe AWS plugin's tables), from two kinds of source:
  S3 buckets             every kind: CloudTrail, VPC flow logs, CUR 2.0, FOCUS 1.0, Cost Optimization Hub
                         recommendations, S3 server access logs, ALB / NLB / CLB access logs, GuardDuty findings,
                         Security Hub findings, WAF logs
  CloudWatch Logs groups CloudTrail, VPC flow logs and WAF logs that go to CloudWatch Logs (no S3 access needed)

Discovery is read-only: trails, flow logs, bucket logging, load balancer attributes, GuardDuty publishing
destinations and WAF logging through Steampipe; Data Exports and legacy CUR definitions through the AWS CLI.
Locations in finops.yaml `tailpipe:` always come first. spendlens owns one file, ~/.tailpipe/config/spendlens.tpc,
and never edits the user's other Tailpipe files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import Config

KINDS = {
    "cloudtrail": "aws_cloudtrail_log",
    "flow_logs": "aws_vpc_flow_log",
    "cur": "aws_cost_and_usage_report",
    "focus": "aws_cost_and_usage_focus",
    "cost_recommendations": "aws_cost_optimization_recommendation",
    "s3_access_logs": "aws_s3_server_access_log",
    "alb_access_logs": "aws_alb_access_log",
    "nlb_access_logs": "aws_nlb_access_log",
    "clb_access_logs": "aws_clb_access_log",
    "guardduty": "aws_guardduty_finding",
    "securityhub": "aws_securityhub_finding",
    "waf": "aws_waf_traffic_log",
}
LABELS = {
    "cloudtrail": "CloudTrail logs", "flow_logs": "VPC flow logs", "cur": "Cost and Usage Reports",
    "focus": "FOCUS cost exports", "cost_recommendations": "Cost Optimization Hub recommendations",
    "s3_access_logs": "S3 server access logs", "alb_access_logs": "ALB access logs",
    "nlb_access_logs": "NLB access logs", "clb_access_logs": "Classic LB access logs",
    "guardduty": "GuardDuty findings", "securityhub": "Security Hub findings", "waf": "WAF logs",
}
# kinds Tailpipe can also collect from a CloudWatch Logs group, and their finops.yaml field
LOG_GROUP_FIELDS = {"cloudtrail": "cloudtrail_log_groups", "flow_logs": "flow_log_groups", "waf": "waf_log_groups"}
EXPORTS = {"COST_AND_USAGE_REPORT": "cur", "FOCUS_1_0": "focus", "COST_OPTIMIZATION_RECOMMENDATIONS":
           "cost_recommendations"}  # Data Exports query table -> kind
MANAGED_FILE = "spendlens.tpc"
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_PREFIX = re.compile(r"^[A-Za-z0-9!_.*'()/=+-]*$")
_GROUP = re.compile(r"^[A-Za-z0-9_./#-]{1,512}$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")


@dataclass
class Found:
    sources: dict[str, list[dict[str, str]]] = field(default_factory=lambda: {k: [] for k in KINDS})
    notes: list[str] = field(default_factory=list)

    def add(self, kind: str, bucket: str, prefix: str, origin: str) -> None:
        prefix = (prefix or "").strip("/")
        prefix = f"{prefix}/" if prefix else ""
        if not _BUCKET.match(bucket or "") or not _PREFIX.match(prefix):
            self.notes.append(f"skipped {LABELS[kind]} location {bucket!r} {prefix!r} (not a plain bucket/prefix)")
            return
        if not any(s.get("bucket") == bucket and s.get("prefix") == prefix for s in self.sources[kind]):
            self.sources[kind].append({"bucket": bucket, "prefix": prefix, "origin": origin})

    def add_group(self, kind: str, name: str, region: str, origin: str) -> None:
        """A CloudWatch Logs group as the source (CloudTrail, flow logs, WAF)."""
        if kind not in LOG_GROUP_FIELDS or not _GROUP.match(name or "") or not _REGION.match(region or ""):
            self.notes.append(f"skipped {LABELS.get(kind, kind)} log group {name!r} in {region!r}")
            return
        if not any(s.get("log_group") == name and s.get("region") == region for s in self.sources[kind]):
            self.sources[kind].append({"log_group": name, "region": region, "origin": origin})


def config_dir() -> Path:
    return Path(os.environ.get("TAILPIPE_INSTALL_DIR") or Path.home() / ".tailpipe") / "config"


def managed_path() -> Path:
    return config_dir() / MANAGED_FILE


def profile_of(cfg: Config) -> str | None:
    return cfg.tailpipe.profile or cfg.aws.profile


# ------------------------------------------------------------------ discovery


def from_config(cfg: Config, found: Found) -> None:
    for kind in KINDS:
        for loc in getattr(cfg.tailpipe, kind):
            found.add(kind, loc.bucket, loc.prefix, "finops.yaml")
    for kind, field_name in LOG_GROUP_FIELDS.items():
        for g in getattr(cfg.tailpipe, field_name):
            found.add_group(kind, g.name, g.region, "finops.yaml")


def _s3_arn(arn: str) -> tuple[str, str]:
    """arn:aws:s3:::bucket/prefix -> (bucket, prefix)."""
    path = arn.split(":::", 1)[1] if ":::" in arn else ""
    return path.split("/", 1)[0], (path.split("/", 1)[1] if "/" in path else "")


def _log_group_arn(arn: str) -> tuple[str, str] | None:
    """arn:aws:logs:<region>:<acct>:log-group:<name>[:*] -> (name, region)."""
    m = re.match(r"^arn:[^:]+:logs:([a-z0-9-]+):\d*:log-group:([^:*]+)", arn or "")
    return (m.group(2), m.group(1)) if m else None


def _common_prefix(prefixes: list[str]) -> str:
    p = os.path.commonprefix([x or "" for x in prefixes])
    return p[: p.rfind("/") + 1] if "/" in p else ""


def _why(text: str) -> str:
    """One readable line for a failed AWS call: name the fix for the common cases."""
    t = str(text)
    low = t.lower()
    if "sso" in low and ("expired" in low or "refresh cached credentials" in low):
        return "the AWS SSO session has expired: run `aws sso login` for this profile, then try again"
    if "usage: aws" in low or low.strip().endswith("aws help") or low.strip() == "help":
        return "this AWS CLI has no `bcm-data-exports` command: upgrade the AWS CLI (2.15 or later)"
    lines = [ln for ln in t.strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else "failed")[:300]


def from_steampipe(cfg: Config, found: Found, client: Any = None) -> None:
    from .steampipe import SteampipeClient, SteampipeError

    c = client or SteampipeClient(cfg.steampipe)

    def each(label: str, sql: str) -> list[dict[str, Any]]:
        try:
            return c.query(sql)
        except SteampipeError as e:
            found.notes.append(f"{label} not read: {_why(e)}")
            return []

    try:
        for r in each("CloudTrail trails", "select name, s3_bucket_name, s3_key_prefix, log_group_arn "
                      "from aws_cloudtrail_trail where s3_bucket_name is not null or log_group_arn is not null"):
            if r.get("s3_bucket_name"):
                found.add("cloudtrail", r["s3_bucket_name"], r.get("s3_key_prefix") or "", f"trail {r.get('name')}")
            if g := _log_group_arn(str(r.get("log_group_arn") or "")):
                found.add_group("cloudtrail", g[0], g[1], f"trail {r.get('name')}")
        for r in each("Flow logs", "select bucket_name, log_destination, log_destination_type, log_group_name, region "
                      "from aws_vpc_flow_log"):
            kind = str(r.get("log_destination_type") or "")
            if kind == "s3":
                bucket, prefix = _s3_arn(str(r.get("log_destination") or ""))
                found.add("flow_logs", r.get("bucket_name") or bucket, prefix, "flow log")
            elif kind == "cloud-watch-logs" and r.get("log_group_name"):
                found.add_group("flow_logs", r["log_group_name"], str(r.get("region") or ""), "flow log")
        targets: dict[str, list[str]] = {}
        for r in each("S3 bucket logging", "select name, logging from aws_s3_bucket where logging is not null"):
            lg = r.get("logging") if isinstance(r.get("logging"), dict) else {}
            if lg.get("TargetBucket"):
                targets.setdefault(lg["TargetBucket"], []).append(lg.get("TargetPrefix") or "")
        for bucket, prefixes in targets.items():  # one partition per log bucket, not one per logged bucket
            found.add("s3_access_logs", bucket, _common_prefix(prefixes), f"access logs of {len(prefixes)} bucket(s)")
        for kind, table in (("alb_access_logs", "aws_ec2_application_load_balancer"),
                            ("nlb_access_logs", "aws_ec2_network_load_balancer")):
            for r in each(LABELS[kind], f"select name, load_balancer_attributes from {table}"):
                attrs = {a.get("Key"): a.get("Value") for a in (r.get("load_balancer_attributes") or [])
                         if isinstance(a, dict)}
                if str(attrs.get("access_logs.s3.enabled")).lower() == "true" and attrs.get("access_logs.s3.bucket"):
                    found.add(kind, attrs["access_logs.s3.bucket"], attrs.get("access_logs.s3.prefix") or "",
                              f"load balancer {r.get('name')}")
        for r in each(LABELS["clb_access_logs"], "select name, access_log_s3_bucket_name, access_log_s3_bucket_prefix "
                      "from aws_ec2_classic_load_balancer where access_log_enabled"):
            if r.get("access_log_s3_bucket_name"):
                found.add("clb_access_logs", r["access_log_s3_bucket_name"], r.get("access_log_s3_bucket_prefix") or "",
                          f"load balancer {r.get('name')}")
        for r in each("GuardDuty publishing destinations", "select destination_arn from "
                      "aws_guardduty_publishing_destination where destination_type = 'S3'"):
            bucket, prefix = _s3_arn(str(r.get("destination_arn") or ""))
            if bucket:
                found.add("guardduty", bucket, prefix, "GuardDuty export")
        for r in each("WAF logging", "select name, logging_configuration from aws_wafv2_web_acl "
                      "where logging_configuration is not null"):
            conf = r.get("logging_configuration") if isinstance(r.get("logging_configuration"), dict) else {}
            for arn in conf.get("LogDestinationConfigs") or []:
                if ":s3:::" in str(arn):
                    bucket, prefix = _s3_arn(str(arn))
                    found.add("waf", bucket, prefix, f"web ACL {r.get('name')}")
                elif g := _log_group_arn(str(arn)):
                    found.add_group("waf", g[0], g[1], f"web ACL {r.get('name')}")
    finally:
        if client is None:
            c.close()


def _aws(cfg: Config, *args: str) -> Any:
    exe = shutil.which("aws")
    if not exe:
        raise RuntimeError("the AWS CLI is not installed")
    cmd = [exe, *args, "--output", "json"]
    if prof := profile_of(cfg):
        cmd += ["--profile", prof]
    if endpoint := os.environ.get("AWS_ENDPOINT_URL"):
        cmd += ["--endpoint-url", endpoint]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if p.returncode != 0:
        raise RuntimeError(_why(p.stderr or p.stdout or "failed"))
    return json.loads(p.stdout or "{}")


def from_aws_cli(cfg: Config, found: Found, run: Any = None) -> None:
    """Data Exports (CUR 2.0, FOCUS 1.0, Cost Optimization Hub recommendations) and legacy CUR report definitions.
    Both APIs live in us-east-1."""
    run = run or (lambda *a: _aws(cfg, *a))
    try:
        for e in run("bcm-data-exports", "list-exports", "--region", "us-east-1").get("Exports", []):
            arn = e.get("ExportArn")
            if not arn:
                continue
            ex = run("bcm-data-exports", "get-export", "--export-arn", arn, "--region", "us-east-1").get("Export", {})
            query = str((ex.get("DataQuery") or {}).get("QueryStatement", "")).upper()
            kind = next((k for t, k in EXPORTS.items() if t in query), None)
            s3 = ((ex.get("DestinationConfigurations") or {}).get("S3Destination") or {})
            if kind and s3.get("S3Bucket"):
                found.add(kind, s3["S3Bucket"], s3.get("S3Prefix", ""), f"export {ex.get('Name', '')}")
    except (RuntimeError, ValueError, subprocess.SubprocessError) as e:
        found.notes.append(f"Data exports (CUR 2.0, FOCUS, recommendations) not read: {e}")
    try:
        for d in run("cur", "describe-report-definitions", "--region", "us-east-1").get("ReportDefinitions", []):
            if d.get("S3Bucket"):
                found.add("cur", d["S3Bucket"], d.get("S3Prefix", ""), f"report {d.get('ReportName', '')}")
    except (RuntimeError, ValueError, subprocess.SubprocessError) as e:
        found.notes.append(f"Legacy CUR reports not read: {e}")


def discover(cfg: Config, *, steampipe: Any = None, aws_cli: Any = None, discover: bool | None = None) -> Found:
    found = Found()
    from_config(cfg, found)
    if cfg.tailpipe.discover if discover is None else discover:
        from_steampipe(cfg, found, steampipe)
        from_aws_cli(cfg, found, aws_cli)
    return found


# ------------------------------------------------------------------ config file


def _name(kind: str, i: int, where: str) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", where.lower())[:40].strip("_") or "source"
    return f"spendlens_{kind}_{i + 1}_{base}"


def render(cfg: Config, found: Found) -> str:
    lines = ["# Managed by spendlens (`spendlens tailpipe init`): rewritten on every init. Change locations in",
             "# finops.yaml (tailpipe: cloudtrail, flow_logs, cur, ... *_log_groups) rather than here; your other .tpc files are",
             "# never touched.", ""]
    prof = profile_of(cfg)
    lines += ['connection "aws" "spendlens" {'] + ([f'  profile = "{prof}"'] if prof else []) + ["}", ""]
    for kind, table in KINDS.items():
        for i, s in enumerate(found.sources[kind]):
            if s.get("log_group"):
                lines += [f'# {LABELS[kind]}: {s["origin"]} (CloudWatch Logs)',
                          f'partition "{table}" "{_name(kind, i, s["log_group"])}" {{',
                          '  source "aws_cloudwatch_log_group" {', "    connection     = connection.aws.spendlens",
                          f'    log_group_name = "{s["log_group"]}"', f'    region         = "{s["region"]}"',
                          "  }", "}", ""]
                continue
            lines += [f'# {LABELS[kind]}: {s["origin"]}', f'partition "{table}" "{_name(kind, i, s["bucket"])}" {{',
                      '  source "aws_s3_bucket" {', "    connection = connection.aws.spendlens",
                      f'    bucket     = "{s["bucket"]}"'] + ([f'    prefix     = "{s["prefix"]}"'] if s["prefix"] else []) \
                + ["  }", "}", ""]
    return "\n".join(lines)


def write(cfg: Config, found: Found) -> Path:
    path = managed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tpc.tmp")
    tmp.write_text(render(cfg, found))
    tmp.replace(path)
    return path


# ------------------------------------------------------------------ collect and status


def binary(cfg: Config) -> str | None:
    return shutil.which(cfg.graph.tailpipe_path)


def collect_cmd(exe: str, kind: str, days: int, today: date | None = None) -> list[str]:
    start = (today or date.today()) - timedelta(days=int(days))
    return [exe, "collect", KINDS[kind], "--from", start.isoformat(), "--progress=false"]


def configured(path: Path | None = None) -> dict[str, int]:
    """Partitions per kind: in `path`, or in every Tailpipe config file (spendlens.tpc and your own .tpc files)."""
    files = [path] if path else sorted(config_dir().glob("*.tpc"))
    text = "\n".join(p.read_text(errors="replace") for p in files if p.is_file())
    return {k: len(re.findall(rf'partition\s+"{t}"', text)) for k, t in KINDS.items()}
