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


# ------------------------------------------------------------------ rightsize / waste

def _ctl_index() -> dict[str, dict[str, str]]:
    from spendlens.powerpipe import bundled_mod_dir, mod_controls

    return mod_controls(bundled_mod_dir())


def _finding(idx, control, resource, status, reason, region, **dims) -> Finding:
    meta = idx[control]
    return Finding(control=control, title=meta.get("title", ""), check=meta["spendlens_check"],
                   severity=Severity(meta.get("severity", "low")),
                   service=meta["service"], resource_type=meta["resource_type"], resource=resource,
                   account_id=ACCOUNT, region=region, status=Status(status), reason=reason,
                   dimensions={k: str(v) for k, v in dims.items()})


def _finops_findings(rng: random.Random, week: int) -> list[Finding]:
    """Synthetic EC2/EBS/RDS/ECS/network estate. week=1 has some issues fixed."""
    from spendlens.config import PricingConfig
    from spendlens.features.savings import apply_estimates

    idx = _ctl_index()
    fs: list[Finding] = []
    fixed = (lambda: week == 1 and rng.random() < 0.35)
    types = ["t3.medium", "m5.xlarge", "m5.2xlarge", "c5.xlarge", "r5.large", "t2.medium", "m4.large", "m6i.large"]
    for i in range(24):
        region, team = rng.choice(REGIONS), rng.choice(TEAMS)
        itype = rng.choice(types)
        arn = f"arn:aws:ec2:{region}:{ACCOUNT}:instance/i-{rng.getrandbits(64):016x}"
        name = f"{team}-app-{i:02d}"
        allowed = itype.split(".")[0] in ("t3", "m6i")
        fs.append(_finding(idx, "ec2_instance_type_not_allowed", arn, "ok" if allowed or fixed() else "alarm",
                           f"{name} is {itype} (running).", region, instance_type=itype))
        old = itype.split(".")[0] in ("t2", "m4")
        fs.append(_finding(idx, "ec2_instance_older_generation", arn, "alarm" if old and not fixed() else "ok",
                           f"{name} uses {itype}.", region, instance_type=itype))
        cpu = round(rng.choice([3, 7, 12, 18, 25, 41, 63, 78]) + rng.random(), 1)
        st = "alarm" if cpu < 20 else "info" if cpu < 35 else "ok"
        if st == "alarm" and fixed():
            st = "ok"
        fs.append(_finding(idx, "ec2_instance_low_utilization", arn, st,
                           f"{name} ({itype}) peaked at {cpu}% CPU over 30 days.", region, instance_type=itype))
        days = rng.choice([12, 45, 120, 300, 700])
        fs.append(_finding(idx, "ec2_instance_reservation_candidate", arn, "alarm" if days > 90 else "ok",
                           f"{name} ({itype}) has been running {days} days.", region, instance_type=itype))
    for i in range(30):
        region = rng.choice(REGIONS)
        vtype = rng.choice(["gp2", "gp2", "gp3", "gp3", "io1", "st1"])
        size = rng.choice([8, 20, 50, 100, 200, 500, 1000])
        iops = rng.choice([3000, 6000, 10000]) if vtype == "io1" else None
        arn = f"arn:aws:ec2:{region}:{ACCOUNT}:volume/vol-{rng.getrandbits(64):016x}"
        vid = arn.rsplit("/", 1)[1]
        dims = {"volume_type": vtype, "size_gb": size, **({"iops": iops} if iops else {})}
        if vtype in ("gp2", "gp3"):
            fs.append(_finding(idx, "ebs_volume_gp2", arn, "alarm" if vtype == "gp2" and not fixed() else "ok",
                               f"{vid} is {vtype} ({size} GB).", region, **dims))
        if vtype == "io1":
            fs.append(_finding(idx, "ebs_volume_piops_to_gp3", arn, "alarm",
                               f"{vid} (io1, {size} GB) has {iops} provisioned IOPS.", region, **dims))
        unattached = rng.random() < 0.2 and not fixed()
        fs.append(_finding(idx, "ebs_volume_unattached", arn, "alarm" if unattached else "ok",
                           f"{vid} ({size} GB {vtype}) is not attached to any instance." if unattached
                           else f"{vid} is attached.", region, **dims))
    for i in range(18):
        region = rng.choice(REGIONS)
        size = rng.choice([8, 30, 100, 250, 500])
        arn = f"arn:aws:ec2:{region}::snapshot/snap-{rng.getrandbits(64):016x}"
        old = rng.random() < 0.5 and not fixed()
        fs.append(_finding(idx, "ebs_snapshot_old", arn, "alarm" if old else "ok",
                           f"{arn.rsplit('/', 1)[1]} ({size} GB) was created 2025-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}.",
                           region, size_gb=size))
    for i, (cls, maz, env) in enumerate([("db.m5.xlarge", True, "dev"), ("db.r5.large", True, "prod"),
                                          ("db.t3.medium", False, "staging"), ("db.m4.large", True, "test"),
                                          ("db.t2.medium", False, "dev"), ("db.m5.large", False, "prod")]):
        region = rng.choice(REGIONS)
        arn = f"arn:aws:rds:{region}:{ACCOUNT}:db:{rng.choice(TEAMS)}-db-{i}"
        dims = {"db_class": cls, "multi_az": str(maz).lower()}
        expected = env == "prod"
        fs.append(_finding(idx, "rds_instance_multi_az_policy", arn,
                           "ok" if maz == expected or (week == 1 and i == 0) else "alarm",
                           f"{arn.rsplit(':', 1)[1]} Multi-AZ is {'enabled' if maz else 'disabled'}; policy for environment {env} is {'enabled' if expected else 'disabled'}.",
                           region, environment=env, **dims))
        fs.append(_finding(idx, "rds_instance_class_not_allowed", arn,
                           "alarm" if cls.split(".")[1] in ("m4", "t2") else "ok", f"{arn.rsplit(':', 1)[1]} uses {cls}.",
                           region, **dims))
        conns = 0 if i == 4 else rng.randint(5, 80)
        fs.append(_finding(idx, "rds_instance_low_connections", arn, "alarm" if conns == 0 else "ok",
                           f"{arn.rsplit(':', 1)[1]} has had no connections in 30 days." if conns == 0
                           else f"{arn.rsplit(':', 1)[1]} averages {conns} peak connections/day over 30 days.", region, **dims))
    for i in range(6):
        region = rng.choice(REGIONS)
        lbt = rng.choice(["application", "network", "classic"])
        unused = i < 2 and not fixed()
        ctl = {"application": "ec2_application_load_balancer_unused", "network": "ec2_network_load_balancer_unused",
               "classic": "ec2_classic_load_balancer_unused"}[lbt]
        fs.append(_finding(idx, ctl, f"arn:aws:elasticloadbalancing:{region}:{ACCOUNT}:loadbalancer/{lbt[:3]}/lb-{i}",
                           "alarm" if unused else "ok", f"lb-{i} has {0 if unused else rng.randint(1, 6)} registered target(s).",
                           region, lb_type=lbt))
    for i in range(5):
        region = rng.choice(REGIONS)
        fs.append(_finding(idx, "vpc_eip_unattached", f"arn:aws:ec2:{region}:{ACCOUNT}:eip/eipalloc-{i:04d}",
                           "alarm" if i < 3 and not fixed() else "ok",
                           f"52.{i}.10.{i} is not associated." if i < 3 else f"52.{i}.10.{i} is associated.", region))
    fs.append(_finding(idx, "vpc_nat_gateway_unused", f"arn:aws:ec2:eu-west-2:{ACCOUNT}:natgateway/nat-0legacy",
                       "alarm" if week == 0 else "ok", "nat-0legacy sent no traffic in the metric window.", "eu-west-2"))
    fs.append(_finding(idx, "rds_instance_extended_support_version", f"arn:aws:rds:eu-west-1:{ACCOUNT}:db:search-db-legacy",
                       "alarm", "search-db-legacy runs mysql 5.7.44.", "eu-west-1", db_class="db.m5.large",
                       engine="mysql", engine_version="5.7.44"))
    for i in range(8):
        fs.append(_finding(idx, "secretsmanager_secret_unused",
                           f"arn:aws:secretsmanager:eu-west-1:{ACCOUNT}:secret:svc-{i}", "alarm" if i < 3 else "ok",
                           f"svc-{i} has never been accessed (created 2025-03-01)." if i < 3 else f"svc-{i} was last accessed 2026-11-0{i}.",
                           "eu-west-1"))
    return apply_estimates(fs, PricingConfig())


def demo_finops(store: HistoryStore, rng: random.Random) -> None:
    for week, day in ((0, 2), (1, 9)):
        fs = _finops_findings(random.Random(100 + week), week)
        for feature in ("rightsize", "waste"):
            store.save_run(feature, [f for f in fs if f.check == feature], account_id=ACCOUNT, caller_arn=CALLER,
                           meta={"CI_PIPELINE_ID": str(1001 + week), "demo": True},
                           started_at=datetime(2026, 11, day, 6, 5, tzinfo=UTC))


# ------------------------------------------------------------------ cost

DEMO_TODAY = "2026-11-09"
SERVICE_BASE = {  # monthly USD, roughly a mid-size retail estate
    "Amazon Elastic Compute Cloud - Compute": 18400, "Amazon Relational Database Service": 9200,
    "Amazon Simple Storage Service": 4100, "Amazon CloudFront": 2600, "EC2 - Other": 3300,
    "Amazon Elastic Container Service": 2900, "AWS Lambda": 640, "Amazon DynamoDB": 1150,
    "Amazon CloudWatch": 980, "Amazon OpenSearch Service": 2100, "AWS Key Management Service": 85,
    "Amazon Route 53": 120, "Amazon ElastiCache": 1450, "Amazon Redshift": 3800,
}


def _monthly_by_service(month_index: int, rng: random.Random) -> dict[str, float]:
    """month_index 0..6 (oldest -> newest full month). EC2 and CloudWatch grow, Redshift is decommissioned,
    Bedrock appears in the last two months."""
    out = {}
    for svc, base in SERVICE_BASE.items():
        v = base * (1 + rng.uniform(-0.04, 0.04))
        if svc.startswith("Amazon Elastic Compute"):
            v *= 1 + 0.03 * month_index + (0.22 if month_index == 6 else 0)
        if svc == "Amazon CloudWatch":
            v *= 1 + 0.12 * month_index
        if svc == "Amazon Redshift" and month_index == 6:
            continue
        if svc == "Amazon Simple Storage Service" and month_index == 6:
            v *= 0.82  # lifecycle rules added
        out[svc] = round(v, 2)
    if month_index >= 5:
        out["Amazon Bedrock"] = 1900 if month_index == 6 else 240
    return out


def demo_costs(store: HistoryStore, rng: random.Random) -> None:
    from datetime import date

    from spendlens.engines import cost as ce

    today = date.fromisoformat(DEMO_TODAY)
    meta_base = {"demo": True, "CI_PIPELINE_ID": "1002"}
    started = datetime(2026, 11, 9, 6, 10, tzinfo=UTC)
    months = ce.trailing_months(today, 7)
    per_month = [_monthly_by_service(i, random.Random(200 + i)) for i in range(7)]

    def rows_for(periods_values, dimension):
        rows = []
        for i, (p, values) in enumerate(periods_values):
            rows += [{"kind": f"p{i}", "period": p.label, "dimension": dimension, "grp": g, "amount": a,
                      "currency": "USD"} for g, a in values.items()]
        return rows

    # cost diff: last full month vs month before (by service)
    diff_periods = list(ce.last_full_months(today))
    store.save_run("cost_diff", cost_rows=rows_for(zip(diff_periods, per_month[-2:], strict=True), "service"),
                   account_id=ACCOUNT, caller_arn=CALLER, started_at=started,
                   meta={**meta_base, "periods": [p.as_dict() for p in diff_periods], "group_by": "service",
                         "metric": "net_unblended", "include_credits": False})
    # cost diff with a breakdown: service -> tag:Name. Each service's demo spend is split across a few Name values
    # (+ an untagged share), so the service totals match the cost_diff run above.
    from spendlens.features.cost import SEP

    apps = ["checkout-api", "search-api", "payments-api", "data-pipeline", "platform-core", "batch-reports"]
    names_for = {svc: random.Random(f"names:{svc}").sample(apps, k=3) for svc in {*per_month[-2], *per_month[-1]}}

    def split(values, month):
        out = {}
        for svc, amount in values.items():
            r = random.Random(f"split:{svc}:{month}")
            names = list(names_for[svc]) + ["(untagged)"]
            if month == 1 and r.random() < 0.3:
                names[0] = "new-" + names[0]                     # some items appear only in the newer month
            base = random.Random(f"weights:{svc}")                # stable shares, small monthly jitter
            weights = [base.uniform(0.4, 2.0) * r.uniform(0.85, 1.15) for _ in names[:-1]]
            weights.append(base.uniform(0.05, 0.25) * r.uniform(0.85, 1.15))
            total = sum(weights)
            for n, w in zip(names, weights, strict=True):
                out[f"{svc}{SEP}{n}"] = round(amount * w / total, 2)
        return out

    store.save_run("cost_breakdown",
                   cost_rows=rows_for(zip(diff_periods, [split(per_month[-2], 0), split(per_month[-1], 1)],
                                          strict=True), "service>tag:Name"),
                   account_id=ACCOUNT, caller_arn=CALLER, started_at=started,
                   meta={**meta_base, "periods": [p.as_dict() for p in diff_periods], "group_by": "service",
                         "breakdown": "tag:Name", "metric": "net_unblended", "include_credits": False})
    # trend: 6 months by service
    trend_periods = months[-6:]
    store.save_run("cost_trend", cost_rows=rows_for(zip(trend_periods, per_month[-6:], strict=True), "service"),
                   account_id=ACCOUNT, caller_arn=CALLER, started_at=started,
                   meta={**meta_base, "periods": [p.as_dict() for p in trend_periods], "group_by": "service",
                         "metric": "net_unblended"})
    # showback: last month by Service Owner + Environment
    last = diff_periods[1]
    total = sum(per_month[-1].values())
    owners = {"payments@example.com": 0.27, "search@example.com": 0.19, "checkout@example.com": 0.16,
              "platform@example.com": 0.21, "data@example.com": 0.09, "(untagged)": 0.08}
    envs = {"prod": 0.68, "staging": 0.11, "dev": 0.13, "(untagged)": 0.08}
    rows = []
    for key, split in (("Service Owner", owners), ("Environment", envs)):
        rows += [{"kind": "p0", "period": last.label, "dimension": f"tag:{key}", "grp": v,
                  "amount": round(total * share, 2), "currency": "USD"} for v, share in split.items()]
    store.save_run("cost_showback", cost_rows=rows, account_id=ACCOUNT, caller_arn=CALLER, started_at=started,
                   meta={**meta_base, "periods": [last.as_dict()], "tag_keys": ["Service Owner", "Environment"],
                         "metric": "net_unblended"})
    # resources: last 7 days vs previous 7 days
    windows = list(ce.resource_windows(today, 7))
    res = {}
    for i in range(25):
        rid = f"arn:aws:ec2:eu-west-1:{ACCOUNT}:instance/i-{rng.getrandbits(64):016x}"
        a = round(rng.uniform(20, 400), 2)
        b = round(a * rng.choice([0.6, 0.95, 1.0, 1.05, 1.4, 2.2]), 2)
        res[rid] = (a, b)
    res[f"arn:aws:bedrock:us-east-1:{ACCOUNT}:provisioned-model/claims-assistant"] = (0.0, 410.0)
    rows = []
    for i, w in enumerate(windows):
        rows += [{"kind": f"p{i}", "period": w.label, "dimension": "resource", "grp": rid, "amount": v[i],
                  "currency": "USD"} for rid, v in res.items()]
    store.save_run("cost_resources", cost_rows=rows, account_id=ACCOUNT, caller_arn=CALLER, started_at=started,
                   meta={**meta_base, "periods": [w.as_dict() for w in windows], "metric": "net_unblended"})


# ------------------------------------------------------------------ anomalies / forecast / savings

def demo_phase5(store: HistoryStore, rng: random.Random) -> None:
    from spendlens.models import Finding, Severity, Status

    started = datetime(2026, 11, 9, 6, 15, tzinfo=UTC)
    meta = {"demo": True, "CI_PIPELINE_ID": "1002"}
    anomalies = [
        ("2026-11-07", "Amazon Elastic Compute Cloud - Compute", 1468.2, 802.4, 9.1, "high"),
        ("2026-11-08", "Amazon CloudWatch", 142.6, 38.9, 7.4, "high"),
        ("2026-11-08", "AWS Data Transfer", 96.3, 41.2, 4.2, "medium"),
    ]
    store.save_run("anomalies", [
        Finding(control="cost_anomaly_spike", title="Daily cost spike", check="anomaly", resource=f"service:{g}",
                status=Status.ALARM, severity=Severity(sev),
                reason=f"{g} cost {c:,.2f} on {d} vs baseline {b:,.2f}: +{c - b:,.2f} (+{(c - b) / b * 100:.1f}%), z={z}.",
                dimensions={"date": d, "group": g, "direction": "spike", "cost": c, "baseline": b,
                            "delta": round(c - b, 2), "delta_pct": round((c - b) / b * 100, 1), "z": z})
        for d, g, c, b, z, sev in anomalies
    ], account_id=ACCOUNT, caller_arn=CALLER, started_at=started, meta={**meta, "group_by": "service"})

    def fc(scope, status, label, limit, actual, forecast, method):
        pct = round(forecast / limit * 100, 1) if limit else None
        return Finding(control="budget_forecast", title="Month-end forecast vs budget", check="forecast",
                       resource=scope, status=Status(status),
                       severity=Severity.HIGH if status == "alarm" else Severity.MEDIUM if status == "info" else Severity.LOW,
                       reason=f"{scope}: actual {actual}, forecast {forecast} vs limit {limit} ({pct}%) -> {label}.",
                       dimensions={"scope": scope, "limit": limit, "actual": actual, "forecast": forecast,
                                   "forecast_pct_of_limit": pct, "label": label, "method": method})
    store.save_run("forecast", [
        fc("account", "alarm", "forecast over budget", 55000.0, 15890.4, 59120.8, "cost_explorer_forecast"),
        fc("budget:platform-monthly", "info", "at risk", 14000.0, 3920.1, 12110.0, "aws_budgets"),
        fc("budget:data-monthly", "ok", "on track", 6000.0, 1010.7, 3870.2, "aws_budgets"),
    ], account_id=ACCOUNT, caller_arn=CALLER, started_at=started, meta=meta)

    hub = [
        ("Rightsize", "arn:aws:ec2:eu-west-1:123456789012:instance/i-0a11", "m5.2xlarge", "m7g.xlarge", 182.4, "Medium", True),
        ("MigrateToGraviton", "arn:aws:rds:eu-west-1:123456789012:db:checkout-db", "db.r5.xlarge", "db.r7g.xlarge", 96.1, "Low", True),
        ("Stop", "arn:aws:ec2:eu-west-2:123456789012:instance/i-0b22", "t3.large", "", 60.7, "Low", False),
        ("Delete", "arn:aws:ec2:eu-west-1:123456789012:volume/vol-0c33", "gp2 500 GB", "", 50.0, "VeryLow", False),
        ("PurchaseSavingsPlans", "arn:aws:savingsplans::123456789012:compute", "On-Demand", "Compute SP 1yr", 1240.0, "Low", False),
    ]
    from spendlens.engines.savings import hub_reason, service_from_arn

    store.save_run("savings_hub", [
        Finding(control=f"coh_{a.lower()}", title="Cost Optimization Hub recommendation", check="savings",
                service=service_from_arn(arn), resource=arn, account_id=ACCOUNT, region=arn.split(":")[3] or None,
                status=Status.ALARM, severity=Severity.HIGH if s >= 100 else Severity.MEDIUM,
                reason=hub_reason({"action_type": a, "current_resource_type": cur, "recommended_resource_type": rec,
                                   "implementation_effort": eff, "restart_needed": rs}),
                est_monthly_saving=s, dimensions={"action": a, "effort": eff, "current": cur, "recommended": rec,
                                                  "source": "cost_optimization_hub"})
        for a, arn, cur, rec, s, eff, rs in hub
    ], account_id=ACCOUNT, caller_arn=CALLER, started_at=started, meta=meta)

    from spendlens.engines.savings import coverage

    rows = [
        {"purchase_type": "On Demand Instances", "service": "Amazon Elastic Compute Cloud - Compute", "amount": 13900},
        {"purchase_type": "Savings Plans", "service": "Amazon Elastic Compute Cloud - Compute", "amount": 9800},
        {"purchase_type": "Spot Instances", "service": "Amazon Elastic Compute Cloud - Compute", "amount": 1050},
        {"purchase_type": "On Demand Instances", "service": "Amazon Relational Database Service", "amount": 4100},
        {"purchase_type": "Reserved Instances", "service": "Amazon Relational Database Service", "amount": 4950},
        {"purchase_type": "On Demand Instances", "service": "Amazon ElastiCache", "amount": 1450},
    ]
    findings = []
    for c in coverage(rows, [], 70, 100, 0.3):
        findings.append(Finding(control="commitment_coverage", title="Savings Plan / RI coverage", check="savings",
                                resource=f"service:{c['service']}", status=Status(c["status"]),
                                reason=f"{c['service']}: {c['coverage_pct']}% committed (target 70%).",
                                est_monthly_saving=c["est_monthly_saving"], dimensions={**c, "period": "2026-10"}))
    findings.append(Finding(control="savings_plan_expiring", title="Savings Plan expiry", check="savings",
                            resource="arn:aws:savingsplans::123456789012:savingsplan/sp-0d44", status=Status.ALARM,
                            severity=Severity.HIGH, reason="Compute plan (4.2/h) ends 2026-12-01.",
                            dimensions={"type": "Compute", "commitment_per_hour": "4.2", "end_time": "2026-12-01T00:00:00+00:00"}))
    store.save_run("savings_commitments", findings, account_id=ACCOUNT, caller_arn=CALLER, started_at=started, meta=meta)


def demo_graph(store: HistoryStore, rng: random.Random) -> None:
    """The resource graph, through the real collect() with Steampipe-shaped rows (spendlens.graph.demo). It reuses
    some demo waste alarms' resources, so cleanup groups show which waste findings they include."""
    from spendlens.config import Config
    from spendlens.graph.collect import collect
    from spendlens.graph.demo import FakeClient, rows

    waste = store.findings(store.latest_run("waste").id)
    alarms = [f for f in waste if f.status is Status.ALARM]

    def pick(control: str, n: int) -> list[dict]:
        return [{"arn": f.resource, "region": f.region, **f.dimensions} for f in alarms if f.control == control][:n]

    link = {"volume": pick("ebs_volume_unattached", 2), "eip": pick("vpc_eip_unattached", 2),
            "lb": pick("ec2_application_load_balancer_unused", 1)}
    from spendlens.graph.cloudtrail import apply as apply_creators
    from spendlens.graph.demo import created_by

    g, meta = collect(Config(), client=FakeClient(rows(link)))
    meta["cloudtrail"] = {"matched": apply_creators(g, created_by()), "days": 90}  # as with graph.owner_from_cloudtrail
    run_id = store.save_run("graph", account_id=ACCOUNT, caller_arn=CALLER, params=Config().graph.model_dump(),
                            meta={**meta, "demo": True}, started_at=datetime(2026, 11, 9, 6, 20, tzinfo=UTC))
    store.save_graph(run_id, *g.to_rows())
    # a week of VPC flow logs, as `spendlens graph traffic` saves it
    from spendlens.graph.demo import flows
    from spendlens.graph.traffic import analyse

    cfg = Config()
    store.save_run("graph_traffic", account_id=ACCOUNT, caller_arn=CALLER, params=cfg.graph.model_dump(),
                   meta={"traffic": analyse(g, flows(), cfg.pricing, 7), "graph_run": run_id, "demo": True},
                   started_at=datetime(2026, 11, 9, 6, 25, tzinfo=UTC))


GENERATORS = [demo_tags, demo_finops, demo_costs, demo_phase5, demo_graph]


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
