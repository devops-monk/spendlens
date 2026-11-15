#!/usr/bin/env python3
"""Build the bundled demo database (src/spendlens/data/demo.db) with deterministic sample data.

    python scripts/build_demo_db.py

Each feature contributes a `demo_<feature>()` generator; add one when a new feature lands.
All data is synthetic: account 123456789012, made-up resource names.
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spendlens.features.tags_checks import TAG_CHECKS
from spendlens.history import HistoryStore
from spendlens.models import Finding, Severity, Status

OUT = ROOT / "src" / "spendlens" / "data" / "demo.db"
ACCOUNT = "123456789012"
CALLER = f"arn:aws:sts::{ACCOUNT}:assumed-role/FinOpsReadOnly/demo"
REGIONS = ["eu-west-1", "eu-west-2", "us-east-1"]
TEAMS = ["payments", "search", "checkout", "platform", "data"]
ENVS = ["prod", "staging", "dev"]
MANDATORY = ["Environment", "Service Owner", "CostCentre"]
ALLOWED_ENV = ["prod", "staging", "dev", "test"]

RESOURCE_TYPES = {
    "s3_bucket": ("arn:aws:s3:::{name}", "S3 buckets", 14),
    "ec2_instance": ("arn:aws:ec2:{region}:{acct}:instance/i-{hex}", "EC2 instances", 18),
    "rds_db_instance": ("arn:aws:rds:{region}:{acct}:db:{name}", "RDS DB instances", 6),
    "lambda_function": ("arn:aws:lambda:{region}:{acct}:function:{name}", "Lambda functions", 12),
    "ebs_volume": ("arn:aws:ec2:{region}:{acct}:volume/vol-{hex}", "EBS volumes", 16),
    "dynamodb_table": ("arn:aws:dynamodb:{region}:{acct}:table/{name}", "DynamoDB tables", 5),
}


def _resources(rng: random.Random) -> list[dict]:
    out = []
    for rtype, (arn_t, _plural, n) in RESOURCE_TYPES.items():
        for i in range(n):
            team = rng.choice(TEAMS)
            region = rng.choice(REGIONS)
            name = f"{team}-{rtype.split('_')[0]}-{i:02d}"
            tags: dict[str, str] = {}
            roll = rng.random()
            if roll > 0.12:  # ~12% untagged
                tags = {"Environment": rng.choice(ENVS), "Service Owner": f"{team}@example.com",
                        "CostCentre": f"CC-{100 + TEAMS.index(team)}"}
                if rng.random() < 0.25:
                    tags.pop(rng.choice(MANDATORY))
                if rng.random() < 0.08:
                    tags["Environment"] = rng.choice(["Production", "prd", "qa"])
                if rng.random() < 0.04:
                    tags["Password"] = "redacted"
            out.append({"rtype": rtype, "region": region, "team": team, "tags": tags,
                        "arn": arn_t.format(name=name, region=region, acct=ACCOUNT, hex=f"{rng.getrandbits(64):016x}"),
                        "title": name})
    return out


def _tag_findings(res: list[dict]) -> list[Finding]:
    fs = []
    for r in res:
        t, title = r["tags"], r["title"]
        base = {"resource_type": r["rtype"], "resource": r["arn"], "account_id": ACCOUNT, "region": r["region"],
                "tags": t}

        def add(check: str, status: Status, reason: str, _base=base, _r=r) -> None:
            spec = TAG_CHECKS[check]
            fs.append(Finding(control=f"tags_{check}_{_r['rtype']}", check=f"tags_{check}",
                              title=f"{RESOURCE_TYPES[_r['rtype']][1]} {spec.title_suffix}",
                              severity=Severity(spec.severity), status=status, reason=reason, **_base))

        missing = [k for k in MANDATORY if k not in t]
        add("mandatory", Status.ALARM if missing else Status.OK,
            f"{title} is missing tags: {', '.join(missing)}." if missing else f"{title} has all mandatory tags.")
        add("untagged", Status.ALARM if not t else Status.OK, f"{title} has no tags." if not t else f"{title} has tags.")
        add("prohibited", Status.ALARM if "Password" in t else Status.OK,
            f"{title} has prohibited tags: Password." if "Password" in t else f"{title} has no prohibited tags.")
        add("limit", Status.OK, f"{title} has {len(t)} tag(s).")
        if not t:
            add("expected_values", Status.SKIP, f"{title} has no tags.")
        elif "Environment" not in t:
            add("expected_values", Status.SKIP, f"{title} has none of the governed tag keys.")
        elif t["Environment"] in ALLOWED_ENV:
            add("expected_values", Status.OK, f"{title} has expected values for tags: Environment.")
        else:
            add("expected_values", Status.ALARM, f"{title} has unexpected values for tags: Environment.")
    return fs


def demo_tags(store: HistoryStore, rng: random.Random) -> None:
    res = _resources(rng)
    params = {"mandatory_tags": MANDATORY, "expected_tag_values": {"Environment": ALLOWED_ENV},
              "prohibited_tags": ["Password", "Secret"], "tag_limit": 45}
    store.save_run("tags", _tag_findings(res), account_id=ACCOUNT, caller_arn=CALLER, params=params,
                   meta={"CI_PIPELINE_ID": "1001", "demo": True}, started_at=datetime(2026, 11, 2, 6, 0, tzinfo=UTC))
    # A week later: teams fixed some tags, a few new untagged resources appeared, one bucket deleted.
    for r in res:
        if not r["tags"] and rng.random() < 0.5:
            r["tags"] = {"Environment": rng.choice(ENVS)}
        if r["tags"] and rng.random() < 0.7:
            r["tags"].update({k: v for k, v in (("Environment", "prod"), ("Service Owner", f"{r['team']}@example.com"),
                                                ("CostCentre", f"CC-{100 + TEAMS.index(r['team'])}")) if k not in r["tags"]})
            r["tags"].pop("Password", None)
    res = [r for r in res if r["title"] != "payments-s3-00"]
    extra = _resources(random.Random(7))[:4]
    for i, r in enumerate(extra):
        r["tags"], r["title"] = {}, f"adhoc-{r['rtype'].split('_')[0]}-{i}"
        r["arn"] = r["arn"].rsplit(":", 1)[0] + f":adhoc-{i}" if r["rtype"] == "s3_bucket" else r["arn"] + f"-new{i}"
    store.save_run("tags", _tag_findings(res + extra), account_id=ACCOUNT, caller_arn=CALLER, params=params,
                   meta={"CI_PIPELINE_ID": "1002", "demo": True}, started_at=datetime(2026, 11, 9, 6, 0, tzinfo=UTC))


GENERATORS = [demo_tags]


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.unlink(missing_ok=True)
    with HistoryStore(OUT) as store:
        for gen in GENERATORS:
            gen(store, random.Random(42))
        store.conn.execute("vacuum")
        runs = store.runs(limit=100)
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(runs)} run(s), {OUT.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
