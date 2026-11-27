#!/usr/bin/env python3
"""Read-only smoke test against a REAL AWS account: run it yourself before a release.

It checks what the offline tests can only fake: that Steampipe sends filters to the AWS APIs the way spendlens
assumes, that LIMIT stops LookupEvents paging, that CloudWatch Logs and ECS deployment data look as expected, and
that the main commands work end to end. Everything is read-only (Describe/List/Get/Lookup calls and S3 reads for
Tailpipe only if you pass --tailpipe). Nothing is created, changed or deleted.

    python scripts/smoke_live.py --profile non-prd-fs --region eu-west-1
    python scripts/smoke_live.py --profile non-prd-fs --ai        # also ask the AI three questions (your provider)

Output: smoke-<timestamp>/ with each check's output, timings and a summary.json to share. Review it before
sharing: it contains account data (ARNs, user names, IPs).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHECKS: list[tuple[str, list[str], str]] = [
    ("doctor", ["doctor"], "tools, AWS identity, Cost Explorer access, Tailpipe status"),
    ("cloudtrail-trails", ["cloudtrail", "trails"], "trails, where they deliver, sources spendlens will read"),
    ("cloudtrail-exact-event", ["cloudtrail", "events", "--days", "7", "--event", "UpdateService", "--source", "lookup",
                                "--show-sql"], "one exact event over 7 days: should take seconds"),
    ("cloudtrail-broad", ["cloudtrail", "events", "--days", "7", "--service", "ecs", "--source", "lookup",
                          "--max-wait", "60", "--show-sql"], "a broad search: widening windows, stops at 60 s"),
    ("cloudtrail-reads-logins", ["cloudtrail", "events", "--preset", "console-logins", "--days", "7", "--source",
                                 "lookup"], "reads included; ConsoleLogin is recorded in the sign-in region"),
    ("cloudtrail-auto", ["cloudtrail", "events", "--days", "1", "--show-sql"], "auto source choice and its notes"),
    ("cloudtrail-summary", ["cloudtrail", "summary", "--days", "1"], "breakdowns"),
    ("cloudtrail-export", ["cloudtrail", "events", "--days", "1", "-o", "{out}/events.xlsx"], "Excel export"),
    ("changes", ["changes", "--days", "1"], "the short form"),
    ("ecs-deployments-json", ["query", "-f", "json", "--limit", "1",
                              "select service_name, deployments from aws_ecs_service limit 1"],
     "the JSON keys inside deployments (CreatedAt, Status ...)"),
    ("ecs-latest-deployment", ["query", "--limit", "5",
                               ("select service_name, d ->> 'Status' as status, (d ->> 'CreatedAt')::timestamptz as at "
                               "from aws_ecs_service, jsonb_array_elements(deployments) d order by at desc limit 5")],
     "the query the AI uses for 'deployed last'"),
    ("account", ["query", "select account_id, account_aliases from aws_account"], "account id and alias"),
    ("waste", ["waste", "-f", "json", "-o", "{out}/waste.json"], "a full check"),
]

PUSHDOWN = [
    # (name, sql, params, what it proves)
    ("limit-stops-paging",
     ("select event_name from aws_cloudtrail_lookup_event where start_time = now() - interval '7 days' and "
     "end_time = now() and read_only = 'false' limit 5"), None,
     "a plain LIMIT returns in seconds even over 7 days (LookupEvents is not read to the end)"),
    ("param-attribute-pushdown",
     ("select count(*) as n from aws_cloudtrail_lookup_event where start_time = now() - interval '7 days' and "
     "end_time = now() and event_name = %(attr)s"), {"attr": "UpdateService"},
     "a %(attr)s parameter is still sent to the API (fast, and the same count as the literal below)"),
    ("literal-attribute",
     ("select count(*) as n from aws_cloudtrail_lookup_event where start_time = now() - interval '7 days' and "
     "end_time = now() and event_name = 'UpdateService'"), None, "the literal version of the query above"),
    ("two-attributes-rejected",
     ("select count(*) as n from aws_cloudtrail_lookup_event where start_time = now() - interval '1 day' and "
     "end_time = now() and event_source = 'ecs.amazonaws.com' and read_only = 'false'"), None,
     "two lookup attributes: expect 0 rows or an error (why spendlens sends one)"),
]

AI_QUESTIONS = [
    "How many EC2 instances are running in this account, by instance type?",
    "Which ECS service was deployed most recently, and when?",
    "What changed in the last 24 hours, and who changed it?",
]


def run(cmd: list[str], out: Path, name: str, env: dict[str, str], timeout: int = 600) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
        code, text = p.returncode, (p.stdout or "") + ("\n--- stderr ---\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired:
        code, text = -1, f"timed out after {timeout}s"
    secs = round(time.monotonic() - t0, 1)
    (out / f"{name}.txt").write_text(f"$ {' '.join(cmd)}\n# exit {code} · {secs}s\n\n{text}")
    print(f"  {'ok ' if code == 0 else 'ERR'} {name:28} {secs:7.1f}s")
    return {"name": name, "exit": code, "seconds": secs, "command": " ".join(cmd)}


def pushdown(out: Path, profile_env: dict[str, str]) -> list[dict[str, Any]]:
    os.environ.update(profile_env)
    from spendlens.config import load_config
    from spendlens.steampipe import SteampipeClient, SteampipeError

    cfg = load_config()
    results = []
    with SteampipeClient(cfg.steampipe, timeout_s=180) as c:
        for name, sql, params, why in PUSHDOWN:
            t0 = time.monotonic()
            try:
                rows, err = c.query(sql, params), None
            except SteampipeError as e:
                rows, err = [], str(e).splitlines()[0][:300]
            secs = round(time.monotonic() - t0, 1)
            results.append({"name": name, "seconds": secs, "rows": rows[:5], "error": err, "proves": why})
            print(f"  {'ok ' if not err else 'ERR'} {name:28} {secs:7.1f}s {json.dumps(rows[:1], default=str)[:60]}")
    (out / "pushdown.json").write_text(json.dumps(results, default=str, indent=2))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="AWS profile (default: current credentials)")
    ap.add_argument("--region", help="AWS region for Steampipe and Bedrock")
    ap.add_argument("--ai", action="store_true", help="also ask the AI a few questions (uses your AI provider)")
    ap.add_argument("--tailpipe", action="store_true", help="also run tailpipe init --dry-run and status")
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    a = ap.parse_args()
    exe = shutil.which("spendlens")
    if not exe:
        print("spendlens is not on PATH")
        return 2
    if not a.yes:
        print("This runs read-only queries against your REAL AWS account"
              + (f" (profile {a.profile})" if a.profile else "") + ". Continue? [y/N] ", end="")
        if input().strip().lower() not in ("y", "yes"):
            return 1
    env = dict(os.environ)
    if a.profile:
        env["AWS_PROFILE"] = a.profile
    if a.region:
        env["AWS_REGION"] = a.region
    out = Path(f"smoke-{datetime.now(UTC):%Y%m%d-%H%M%S}")
    out.mkdir()
    prof = ["--profile", a.profile] if a.profile else []
    print(f"spendlens smoke test -> {out}/")
    results = []
    for name, args, _why in CHECKS:
        cmd = [exe, *[x.replace("{out}", str(out)) for x in args]]
        if args[0] not in ("doctor", "query") and "-p" not in args:
            cmd += prof
        results.append(run(cmd, out, name, env))
    if a.tailpipe:
        results.append(run([exe, "tailpipe", "init", "--dry-run", *prof], out, "tailpipe-init-dry-run", env))
        results.append(run([exe, "tailpipe", "status"], out, "tailpipe-status", env))
    print("Steampipe pushdown checks")
    push = pushdown(out, {k: v for k, v in env.items() if k in ("AWS_PROFILE", "AWS_REGION")})
    if a.ai:
        print("AI questions")
        for i, q in enumerate(AI_QUESTIONS, 1):
            results.append(run([exe, "ask", "--show-tools", "--fresh", q], out, f"ai-{i}", env, timeout=900))
    summary = {"at": datetime.now(UTC).isoformat(), "checks": results, "pushdown": push,
               "failed": [r["name"] for r in results if r["exit"] != 0]}
    (out / "summary.json").write_text(json.dumps(summary, default=str, indent=2))
    print(f"\n{len(results) - len(summary['failed'])}/{len(results)} commands ok · details in {out}/ "
          "(review before sharing: it contains account data)")
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
