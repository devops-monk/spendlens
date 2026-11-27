"""Who actually uses a resource, from its access logs: unused S3 buckets and idle load balancers.

Inventory comes from Steampipe (read-only); activity from the access logs Tailpipe collected (local, no AWS calls):
  buckets         aws_s3_server_access_log: requests per bucket, reads and writes, last access, bytes sent
  load balancers  aws_alb_access_log, aws_nlb_access_log, aws_clb_access_log: requests per load balancer

A resource is "unused" only when its logs are collected and cover the whole window, and show no requests in it.
Without logs it is "unknown", never "unused": no log is not the same as no traffic. AWS's own housekeeping (S3
lifecycle expirations and transitions, S3 inventory and replication) does not count as use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

UNUSED, ACTIVE, UNKNOWN = "unused", "active", "unknown"
LB_KINDS = {"alb_access_logs": ("aws_alb_access_log", "aws_ec2_application_load_balancer", "app"),
            "nlb_access_logs": ("aws_nlb_access_log", "aws_ec2_network_load_balancer", "net"),
            "clb_access_logs": ("aws_clb_access_log", "aws_ec2_classic_load_balancer", "")}


@dataclass
class Usage:
    kind: str
    rows: list[dict[str, Any]]
    days: int
    covered: dict[str, tuple[str, str] | None] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {UNUSED: 0, ACTIVE: 0, UNKNOWN: 0}
        for r in self.rows:
            out[r["status"]] += 1
        return out


def bucket_sql(days: int) -> str:
    return ("select bucket, count(*) as requests, "
            "count(*) filter (where operation like 'REST.GET.%' or operation like 'REST.HEAD.%') as reads, "
            "count(*) filter (where operation like 'REST.PUT.%' or operation like 'REST.POST.%' or "
            "operation like 'REST.DELETE.%' or operation like 'REST.COPY.%') as writes, "
            "cast(max(timestamp) as varchar) as last_access, sum(coalesce(bytes_sent, 0)) as bytes_sent "
            "from aws_s3_server_access_log "
            f"where tp_date >= current_date - interval {int(days)} day "
            "and operation not like 'S3.%' and operation not like 'BATCH.%' group by bucket")


def lb_sql(table: str, days: int) -> str:
    return (f"select elb, count(*) as requests, cast(max(timestamp) as varchar) as last_access, "
            f"sum(coalesce(received_bytes, 0)) + sum(coalesce(sent_bytes, 0)) as bytes "
            f"from {table} where tp_date >= current_date - interval {int(days)} day group by elb")


def _covers(cov: tuple[str, str] | None, days: int, today: date) -> bool:
    """The collected logs reach back to the window's start and up to yesterday."""
    if not cov:
        return False
    return cov[0] <= (today - timedelta(days=days)).isoformat() and cov[1] >= (today - timedelta(days=1)).isoformat()


def _status(requests: int, logged: bool, covered: bool) -> str:
    if requests:
        return ACTIVE
    return UNUSED if logged and covered else UNKNOWN


def buckets(cfg: Any, days: int = 30, *, steampipe: Any = None, run: Any = None, today: date | None = None) -> Usage:
    from .sources import tailpipe_coverage, tailpipe_ready

    today = today or date.today()
    res = Usage("buckets", [], days)
    inventory = steampipe.query("select name, region, account_id, cast(creation_date as varchar) as created, "
                                "logging from aws_s3_bucket")
    activity: dict[str, dict[str, Any]] = {}
    covered = False
    if run is not None or tailpipe_ready(cfg, "s3_access_logs"):
        from .graph.costs import CostSourceError, tailpipe_runner

        try:
            run = run or tailpipe_runner(cfg.graph.tailpipe_path, timeout=600)
            cov = tailpipe_coverage(cfg, "s3_access_logs", run)
            res.covered["s3_access_logs"] = cov
            covered = _covers(cov, days, today)
            activity = {str(r["bucket"]): r for r in run(bucket_sql(days)) if r.get("bucket")}
        except CostSourceError as e:
            res.notes.append(f"S3 access logs unavailable: {str(e).splitlines()[0][:200]}")
    else:
        res.notes.append("No S3 server access logs in Tailpipe: every bucket is 'unknown'. Turn on server access "
                         "logging, then spendlens tailpipe init and collect.")
    if activity and not covered:
        res.notes.append(f"The collected access logs cover {res.covered.get('s3_access_logs')}, not the last {days} "
                         "days: buckets without requests are 'unknown', not 'unused' (spendlens tailpipe collect "
                         f"s3_access_logs --days {days}).")
    for b in inventory:
        name = str(b.get("name") or "")
        a = activity.get(name, {})
        logged = bool(b.get("logging"))
        req = int(a.get("requests") or 0)
        res.rows.append({"bucket": name, "region": b.get("region"), "account_id": b.get("account_id"),
                         "status": _status(req, logged, covered), "requests": req,
                         "reads": int(a.get("reads") or 0), "writes": int(a.get("writes") or 0),
                         "last_access": str(a.get("last_access") or "")[:19], "bytes_sent": int(a.get("bytes_sent") or 0),
                         "access_logging": logged, "created": str(b.get("created") or "")[:10]})
    res.rows.sort(key=lambda r: ({UNUSED: 0, UNKNOWN: 1, ACTIVE: 2}[r["status"]], -r["requests"], r["bucket"]))
    return res


def load_balancers(cfg: Any, days: int = 30, *, steampipe: Any = None, run: Any = None,
                   today: date | None = None) -> Usage:
    from .sources import tailpipe_coverage, tailpipe_ready

    today = today or date.today()
    res = Usage("load_balancers", [], days)
    for kind, (log_table, inv_table, prefix) in LB_KINDS.items():
        attrs = "load_balancer_attributes" if prefix else "access_log_enabled"
        inventory = steampipe.query(f"select name, arn, region, account_id, cast(created_time as varchar) as created, "
                                    f"{attrs} from {inv_table}")
        activity: dict[str, dict[str, Any]] = {}
        covered = False
        if inventory and (run is not None or tailpipe_ready(cfg, kind)):
            from .graph.costs import CostSourceError, tailpipe_runner

            try:
                r = run or tailpipe_runner(cfg.graph.tailpipe_path, timeout=600)
                cov = tailpipe_coverage(cfg, kind, r)
                res.covered[kind] = cov
                covered = _covers(cov, days, today)
                for x in r(lb_sql(log_table, days)):
                    elb = str(x.get("elb") or "")
                    # ALB/NLB logs name it "app/<name>/<id>" or "net/<name>/<id>"; CLB logs use the name
                    activity[elb.split("/")[1] if elb.count("/") >= 2 else elb] = x
            except CostSourceError as e:
                res.notes.append(f"{kind.replace('_', ' ')} unavailable: {str(e).splitlines()[0][:200]}")
        for lb in inventory:
            name = str(lb.get("name") or "")
            if prefix:
                a_map = {a.get("Key"): a.get("Value") for a in (lb.get("load_balancer_attributes") or [])
                         if isinstance(a, dict)}
                logged = str(a_map.get("access_logs.s3.enabled")).lower() == "true"
            else:
                logged = bool(lb.get("access_log_enabled"))
            a = activity.get(name, {})
            req = int(a.get("requests") or 0)
            res.rows.append({"load_balancer": name, "type": {"app": "application", "net": "network"}.get(prefix, "classic"),
                             "region": lb.get("region"), "account_id": lb.get("account_id"),
                             "status": _status(req, logged, covered), "requests": req,
                             "last_access": str(a.get("last_access") or "")[:19], "bytes": int(a.get("bytes") or 0),
                             "access_logging": logged, "created": str(lb.get("created") or "")[:10],
                             "arn": lb.get("arn")})
    if not any(res.covered.values()):
        res.notes.append("No load balancer access logs in Tailpipe: every load balancer is 'unknown'. Turn on access "
                         "logging, then spendlens tailpipe init and collect.")
    res.rows.sort(key=lambda r: ({UNUSED: 0, UNKNOWN: 1, ACTIVE: 2}[r["status"]], -r["requests"], r["load_balancer"]))
    return res
