"""Threat detections on the logs Tailpipe collected, from Turbot's official detection mods (Apache-2.0, vendored and
pinned in spendlens/detections/):

  cloudtrail  tailpipe-mod-aws-cloudtrail-log-detections v0.3.0       86 detections: root logins, logging stopped,
                                                                       public sharing, IAM keys, KMS deletion ...
  s3_access   tailpipe-mod-aws-s3-server-access-log-detections v0.2.0  6 detections: insecure TLS, suspicious agents,
                                                                       large transfers, off-hours access
  flow_logs   tailpipe-mod-aws-vpc-flow-log-detections v0.1.0          6 detections: risky ports and protocols, large
                                                                       data transfers

Each mod runs as a Powerpipe benchmark against Tailpipe's local database (no AWS calls), and every detection hit
becomes a security finding with its severity, MITRE ATT&CK ids, resource, actor and source IP. A pack runs only when
Tailpipe has that kind of log configured. The detections read everything collected; spendlens keeps the hits inside
the window asked for.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

PACKS = {  # name -> (vendored dir, mod name, top benchmark, Tailpipe kind, hub slug)
    "cloudtrail": ("cloudtrail", "aws_cloudtrail_log_detections", "cloudtrail_log_detections", "cloudtrail",
                   "tailpipe-mod-aws-cloudtrail-log-detections"),
    "s3_access": ("s3_access", "aws_s3_server_access_log_detections", "s3_server_access_log_detections",
                  "s3_access_logs", "tailpipe-mod-aws-s3-server-access-log-detections"),
    "flow_logs": ("flow_logs", "aws_vpc_flow_log_detections", "vpc_flow_log_detections", "flow_logs",
                  "tailpipe-mod-aws-vpc-flow-log-detections"),
}
MAX_HITS_PER_DETECTION = 200


class DetectionError(RuntimeError):
    pass


def mod_dir(pack: str) -> Path:
    return Path(str(resources.files("spendlens").joinpath("detections", PACKS[pack][0])))


def command(cfg: Any, pack: str) -> list[str]:
    exe = shutil.which(cfg.powerpipe.binary)
    if not exe:
        raise DetectionError("powerpipe is not installed (the spendlens installer adds it)")
    _, mod, bench, _, _ = PACKS[pack]
    return [exe, "benchmark", "run", f"{mod}.benchmark.{bench}", "--mod-location", str(mod_dir(pack)),
            "--output", "json", "--mod-install=false", "--max-parallel", str(cfg.powerpipe.max_parallel)]


def _runs(group: dict[str, Any]) -> list[dict[str, Any]]:
    out = list(group.get("detection_runs") or [])
    for g in group.get("groups") or []:
        out += _runs(g)
    return out


def parse(doc: dict[str, Any], pack: str, since: datetime | None = None) -> tuple[list[Any], list[dict[str, Any]]]:
    """(findings, per-detection summary) from one benchmark's JSON."""
    from .security import SecFinding

    findings: list[Any] = []
    summary: list[dict[str, Any]] = []
    slug = PACKS[pack][4]
    seen: set[str] = set()
    for run in _runs(doc):
        name = str(run.get("name") or "").rsplit(".", 1)[-1]
        if not name or name in seen:
            continue
        seen.add(name)
        props = run.get("properties") if isinstance(run.get("properties"), dict) else {}
        tags = run.get("tags") if isinstance(run.get("tags"), dict) else {}
        severity = str(props.get("severity") or "low").upper()
        rows = (run.get("data") or {}).get("rows") or []
        kept = 0
        for r in rows:
            ts = str(r.get("timestamp") or r.get("tp_timestamp") or "")
            if since is not None and ts:
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00")[:26])
                    if (when if when.tzinfo else when.replace(tzinfo=UTC)) < since:
                        continue
                except ValueError:
                    pass
            kept += 1
            if kept > MAX_HITS_PER_DETECTION:
                continue
            findings.append(SecFinding(
                source="detections", id=f"{name}:{r.get('source_id') or r.get('tp_id') or kept}", severity=severity,
                title=str(run.get("title") or name), resource_type=str(tags.get("service") or pack),
                resource=str(r.get("resource") or ""), region=str(r.get("region") or r.get("aws_region") or ""),
                account_id=str(r.get("account_id") or ""), status="ACTIVE", vulnerability_id=name,
                first_seen=ts[:19], url=f"https://hub.powerpipe.io/mods/turbot/{slug}/detections/{name}",
                actor=str(r.get("actor") or ""), source_ip=str(r.get("source_ip") or ""),
                mitre=str(tags.get("mitre_attack_ids") or ""), action=str(r.get("operation") or "")))
        if kept:
            summary.append({"pack": pack, "detection": name, "title": run.get("title"), "severity": severity,
                            "hits": kept, "mitre": tags.get("mitre_attack_ids")})
    summary.sort(key=lambda d: (["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"].index(d["severity"])
                                if d["severity"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL") else 9,
                                -d["hits"]))
    return findings, summary


def run(cfg: Any, packs: list[str] | None = None, days: int | None = None, *, runner: Any = None,
        ready: Any = None) -> dict[str, Any]:
    """{findings, summary, ran, skipped, notes}. A pack runs when Tailpipe has its kind of log configured."""
    from .sources import tailpipe_ready

    ready = ready or (lambda kind: tailpipe_ready(cfg, kind))
    since = datetime.now(UTC) - timedelta(days=days) if days else None
    out: dict[str, Any] = {"findings": [], "summary": [], "ran": [], "skipped": {}, "notes": []}
    for pack in packs or list(PACKS):
        if pack not in PACKS:
            raise DetectionError(f"unknown detection pack {pack!r}: one of {', '.join(PACKS)}")
        kind = PACKS[pack][3]
        if not ready(kind):
            out["skipped"][pack] = f"no {kind.replace('_', ' ')} in Tailpipe (spendlens tailpipe init, then collect)"
            continue
        try:
            if runner is not None:
                text = runner(pack)
            else:
                p = subprocess.run(command(cfg, pack), capture_output=True, text=True, timeout=1800, check=False,
                                   cwd=str(mod_dir(pack)))
                text = p.stdout
                if not text.strip().startswith("{"):
                    raise DetectionError((p.stderr or p.stdout or "no output").strip().splitlines()[-1][:300])
            doc = json.loads(text)
        except (DetectionError, ValueError, subprocess.SubprocessError, OSError) as e:
            out["skipped"][pack] = f"could not run: {str(e)[:300]}"
            continue
        f, s = parse(doc, pack, since)
        out["findings"] += f
        out["summary"] += s
        out["ran"].append(pack)
    for pack, why in out["skipped"].items():
        out["notes"].append(f"{pack} detections: {why}.")
    return out
