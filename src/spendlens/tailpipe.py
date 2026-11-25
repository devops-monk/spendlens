"""Tailpipe setup: find where CloudTrail logs, VPC flow logs and Cost and Usage Reports land in S3, write the
Tailpipe config, collect, and report what is there.

Discovery is read-only: CloudTrail trails and flow logs through Steampipe (aws_cloudtrail_trail, aws_vpc_flow_log),
CUR exports through the AWS CLI (bcm-data-exports list-exports / get-export, cur describe-report-definitions).
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

KINDS = {"cloudtrail": "aws_cloudtrail_log", "flow_logs": "aws_vpc_flow_log", "cur": "aws_cost_and_usage_report"}
LABELS = {"cloudtrail": "CloudTrail logs", "flow_logs": "VPC flow logs", "cur": "Cost and Usage Reports"}
MANAGED_FILE = "spendlens.tpc"
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_PREFIX = re.compile(r"^[A-Za-z0-9!_.*'()/=+-]*$")


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
        if not any(s["bucket"] == bucket and s["prefix"] == prefix for s in self.sources[kind]):
            self.sources[kind].append({"bucket": bucket, "prefix": prefix, "origin": origin})


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


def from_steampipe(cfg: Config, found: Found, client: Any = None) -> None:
    from .steampipe import SteampipeClient, SteampipeError

    c = client or SteampipeClient(cfg.steampipe)
    try:
        try:
            for r in c.query("select name, s3_bucket_name, s3_key_prefix from aws_cloudtrail_trail "
                             "where s3_bucket_name is not null"):
                found.add("cloudtrail", r["s3_bucket_name"], r.get("s3_key_prefix") or "", f"trail {r.get('name')}")
        except SteampipeError as e:
            found.notes.append(f"CloudTrail trails not read: {str(e).splitlines()[0][:160]}")
        try:
            for r in c.query("select bucket_name, log_destination from aws_vpc_flow_log "
                             "where log_destination_type = 's3'"):
                dest = str(r.get("log_destination") or "")
                path = dest.split(":::", 1)[1] if ":::" in dest else ""
                bucket = r.get("bucket_name") or path.split("/", 1)[0]
                prefix = path.split("/", 1)[1] if "/" in path else ""
                found.add("flow_logs", bucket, prefix, "flow log")
        except SteampipeError as e:
            found.notes.append(f"Flow logs not read: {str(e).splitlines()[0][:160]}")
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
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1][:200] if (p.stderr or p.stdout) else "failed")
    return json.loads(p.stdout or "{}")


def from_aws_cli(cfg: Config, found: Found, run: Any = None) -> None:
    """CUR 2.0 exports (Data Exports) and legacy CUR report definitions. Both APIs live in us-east-1."""
    run = run or (lambda *a: _aws(cfg, *a))
    try:
        for e in run("bcm-data-exports", "list-exports", "--region", "us-east-1").get("Exports", []):
            arn = e.get("ExportArn")
            if not arn:
                continue
            ex = run("bcm-data-exports", "get-export", "--export-arn", arn, "--region", "us-east-1").get("Export", {})
            if "COST_AND_USAGE_REPORT" not in str((ex.get("DataQuery") or {}).get("QueryStatement", "")).upper():
                continue
            s3 = ((ex.get("DestinationConfigurations") or {}).get("S3Destination") or {})
            if s3.get("S3Bucket"):
                found.add("cur", s3["S3Bucket"], s3.get("S3Prefix", ""), f"export {ex.get('Name', '')}")
    except (RuntimeError, ValueError, subprocess.SubprocessError) as e:
        found.notes.append(f"CUR 2.0 exports not read: {e}")
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


def _name(kind: str, i: int, bucket: str) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", bucket.lower())[:40].strip("_") or "bucket"
    return f"spendlens_{kind}_{i + 1}_{base}"


def render(cfg: Config, found: Found) -> str:
    lines = ["# Managed by spendlens (`spendlens tailpipe init`): rewritten on every init. Change locations in",
             "# finops.yaml (tailpipe.cloudtrail / flow_logs / cur) rather than here; your other .tpc files are",
             "# never touched.", ""]
    prof = profile_of(cfg)
    lines += ['connection "aws" "spendlens" {'] + ([f'  profile = "{prof}"'] if prof else []) + ["}", ""]
    for kind, table in KINDS.items():
        for i, s in enumerate(found.sources[kind]):
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
    """Partitions per kind in the managed file."""
    p = path or managed_path()
    text = p.read_text() if p.is_file() else ""
    return {k: len(re.findall(rf'partition "{t}"', text)) for k, t in KINDS.items()}
