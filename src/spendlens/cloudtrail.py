"""CloudTrail events, read-only: search, filter, summarise and export who did what, when.

Three sources, best first (`source="auto"` tries them in this order):
  tailpipe     the trail's own logs collected by Tailpipe (aws_cloudtrail_log, local DuckDB): any age, data events,
               every filter applied locally, no AWS calls
  cloudwatch   the trail's CloudWatch Logs log group (aws_cloudtrail_trail_event, FilterLogEvents): as far back as
               the log group keeps, data events if the trail records them; most filters are sent to AWS together
  lookup       CloudTrail LookupEvents (aws_cloudtrail_lookup_event): no setup, the last 90 days of management events;
               AWS takes ONE filter and returns 50 events a page, so broad searches go newest first in widening
               windows and stop at a time budget with what they found

Filters: a time range (from/to, or the last N hours), user or role (part of the name or ARN), event names (exact, or
with * wildcards), service, resource (id or name), resource type, access key, source IP (exact or CIDR), error code,
errors only, and writes / reads / all. Presets bundle common questions (console logins, deletes, deployments ...).
"""

from __future__ import annotations

import ipaddress
import re
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

NOISE = ("AssumeRole", "AssumeRoleWithSAML", "AssumeRoleWithWebIdentity", "GetSessionToken", "GetFederationToken",
         "Decrypt", "Encrypt", "GenerateDataKey", "GenerateDataKeyWithoutPlaintext", "ReEncrypt", "CreateLogStream",
         "PutLogEvents", "StartQuery", "SetTaskStatus", "ReportTaskProgress", "PollForTask", "SendCommand",
         "UpdateInstanceInformation", "RefreshCredentials", "CreateToken", "Federate")
MAX_ROWS = 500
MAX_WAIT_S = 60.0
LOOKUP_DAYS = 90
SOURCES = ("auto", "tailpipe", "cloudwatch", "lookup", "aws")  # aws = CloudWatch Logs, then LookupEvents
READS = ("writes", "reads", "all")
COLUMNS = ["event_time", "who", "event_name", "event_source", "region", "source_ip", "error_code", "error_message",
           "read_only", "resources", "request", "user_agent", "access_key", "event_id"]

PRESETS: dict[str, dict[str, Any]] = {
    "console-logins": {"events": ["ConsoleLogin"], "reads": "all", "help": "Console sign-ins, failed ones included"},
    "deletes": {"events": ["Delete*", "Terminate*", "Remove*", "Deregister*"], "help": "Anything deleted or terminated"},
    "deployments": {"events": ["UpdateService", "CreateService", "UpdateFunctionCode", "UpdateFunctionConfiguration",
                               "PublishVersion", "CreateDeployment", "CreateStack", "UpdateStack", "ExecuteChangeSet"],
                    "help": "ECS, Lambda, CodeDeploy and CloudFormation deployments"},
    "security-groups": {"events": ["AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress",
                                   "RevokeSecurityGroupIngress", "RevokeSecurityGroupEgress", "CreateSecurityGroup",
                                   "DeleteSecurityGroup", "ModifySecurityGroupRules"],
                        "help": "Security group rules opened, closed or changed"},
    "iam": {"service": "iam", "help": "IAM changes: users, roles, policies, keys (recorded in us-east-1)"},
    "access-denied": {"errors": ["AccessDenied*", "*UnauthorizedOperation", "*Unauthorized*"], "reads": "all",
                      "help": "Calls refused for missing permissions"},
    "errors": {"errors_only": True, "reads": "all", "help": "Every failed call"},
    "root": {"user": ":root", "reads": "all", "help": "Anything done as the root user"},
}


class CloudTrailError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def parse_time(text: str, *, end: bool = False) -> datetime:
    """'2026-11-01', '2026-11-01 14:30', ISO 8601, or relative: '6h', '2d', '1w' ago. Naive times are UTC."""
    s = str(text).strip()
    if m := re.fullmatch(r"(\d+)\s*([hdw])", s.lower()):
        n, unit = int(m.group(1)), m.group(2)
        return _now() - timedelta(hours=n * {"h": 1, "d": 24, "w": 168}[unit])
    if s.lower() == "now":
        return _now()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise CloudTrailError(f"not a time: {text!r} (use 2026-11-01, '2026-11-01 14:30', or 6h / 2d / 1w)") from e
    if end and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        dt += timedelta(days=1)  # --to 2026-11-20 means the whole of that day
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass
class Filters:
    start: datetime
    end: datetime
    user: str | None = None
    events: list[str] = field(default_factory=list)
    service: str | None = None
    resource: str | None = None
    resource_type: str | None = None
    access_key: str | None = None
    source_ip: str | None = None
    errors: list[str] = field(default_factory=list)
    errors_only: bool = False
    reads: str = "writes"
    include_noise: bool = False
    region: str | None = None

    @classmethod
    def build(cls, *, hours: float | None = None, start: str | datetime | None = None,
              end: str | datetime | None = None, preset: str | None = None, **kw: Any) -> Filters:
        e = end if isinstance(end, datetime) else parse_time(end, end=True) if end else _now()
        if isinstance(start, datetime):
            s = start
        elif start:
            s = parse_time(start)
        else:
            s = e - timedelta(hours=float(hours or 24))
        if s >= e:
            raise CloudTrailError("the start of the time range must be before its end")
        base: dict[str, Any] = {}
        if preset:
            if preset not in PRESETS:
                raise CloudTrailError(f"unknown preset {preset!r}: one of {', '.join(PRESETS)}")
            base = {k: v for k, v in PRESETS[preset].items() if k != "help"}
        for k, v in kw.items():
            if v in (None, [], ()):
                continue
            if k in ("events", "errors"):
                v = [x for item in (v if isinstance(v, list | tuple) else [v]) for x in str(item).split(",") if x.strip()]
                base[k] = [x.strip() for x in v]
            else:
                base[k] = v
        f = cls(start=s.astimezone(UTC), end=e.astimezone(UTC), **base)
        if f.reads not in READS:
            raise CloudTrailError(f"reads must be one of {', '.join(READS)}")
        if f.service:
            f.service = service_host(f.service)
        if f.source_ip and "/" in f.source_ip:
            try:
                ipaddress.ip_network(f.source_ip, strict=False)
            except ValueError as ex:
                raise CloudTrailError(f"not an IP address or CIDR range: {f.source_ip}") from ex
        return f

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    def describe(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, [], False) and k not in ("start", "end")}
        return {"from": self.start.isoformat(), "to": self.end.isoformat(), **d}


def service_host(s: str) -> str:
    s = s.strip().lower()
    return s if "." in s else f"{s}.amazonaws.com"


def _q(v: Any) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _ts(dt: datetime) -> str:
    return _q(dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00"))


def _tsn(dt: datetime) -> str:
    """A naive UTC timestamp literal (Tailpipe stores event_time without a zone)."""
    return _q(dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"))


def _wild(v: str) -> bool:
    return "*" in v or "?" in v


def _like(v: str) -> str:
    return v.replace("%", r"\%").replace("_", r"\_").replace("*", "%").replace("?", "_")


def _glob(pattern: str, value: str) -> bool:
    rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, value or "", re.IGNORECASE) is not None


def matches(r: dict[str, Any], f: Filters) -> bool:
    """The filters every source applies again in Python, so all three agree."""
    if f.user and f.user.lower() not in str(r.get("who") or "").lower():
        return False
    if f.events and not any(_glob(p, str(r.get("event_name") or "")) for p in f.events):
        return False
    if f.service and str(r.get("event_source") or "").lower() != f.service:
        return False
    if f.errors and not any(_glob(p, str(r.get("error_code") or "")) for p in f.errors):
        return False
    if f.errors_only and not r.get("error_code"):
        return False
    ro = str(r.get("read_only")).lower() in ("true", "1")
    if (f.reads == "writes" and ro) or (f.reads == "reads" and not ro):
        return False
    if not f.include_noise and r.get("event_name") in NOISE:
        return False
    if f.source_ip:
        ip = str(r.get("source_ip") or "")
        if "/" in f.source_ip:
            try:
                if ipaddress.ip_address(ip) not in ipaddress.ip_network(f.source_ip, strict=False):
                    return False
            except ValueError:
                return False
        elif ip != f.source_ip:
            return False
    if f.access_key and str(r.get("access_key") or "") != f.access_key:
        return False
    return not (f.region and str(r.get("region") or "") != f.region)


# ------------------------------------------------------------------ SQL per source


def _common_sql(f: Filters, *, col: dict[str, str], ilike: str = "ilike") -> list[str]:
    """SQL conditions every source can evaluate itself (none of them is sent to an API)."""
    w: list[str] = []
    if f.user:
        w.append(f"{col['who']} {ilike} {_q('%' + _like(f.user) + '%')}")
    wild = [e for e in f.events if _wild(e)]
    if wild:
        w.append("(" + " or ".join(f"{col['event_name']} {ilike} {_q(_like(e))}" for e in wild) + ")")
    if f.errors:
        w.append("(" + " or ".join(f"coalesce({col['error_code']}, '') {ilike} {_q(_like(e))}" for e in f.errors) + ")")
    if f.errors_only:
        w.append(f"{col['error_code']} is not null")
    if f.resource:
        w.append(f"({col['resources']} {ilike} {_q('%' + _like(f.resource) + '%')} or "
                 f"{col['request']} {ilike} {_q('%' + _like(f.resource) + '%')})")
    if f.resource_type:
        w.append(f"{col['resources']} {ilike} {_q('%' + _like(f.resource_type) + '%')}")
    if f.source_ip and "/" not in f.source_ip:
        w.append(f"{col['source_ip']} = {_q(f.source_ip)}")
    if not f.include_noise:
        w.append(f"{col['event_name']} not in ({', '.join(_q(n) for n in NOISE)})")
    return w


def tailpipe_sql(f: Filters, limit: int = MAX_ROWS) -> str:
    col = {"who": "coalesce(user_identity.arn, user_identity.user_name, '')", "event_name": "event_name",
           "error_code": "error_code", "resources": "coalesce(cast(resources as varchar), '')",
           "request": "coalesce(cast(request_parameters as varchar), '')", "source_ip": "source_ip_address"}
    w = [f"tp_date >= date {_q(f.start.date().isoformat())}", f"tp_date <= date {_q(f.end.date().isoformat())}",
         f"event_time >= timestamp {_tsn(f.start)}", f"event_time <= timestamp {_tsn(f.end)}"]
    exact = [e for e in f.events if not _wild(e)]
    if exact:
        w.append(f"event_name in ({', '.join(_q(e) for e in exact)})")
    if f.service:
        w.append(f"event_source = {_q(f.service)}")
    if f.reads != "all":
        w.append(f"coalesce(read_only, false) = {'true' if f.reads == 'reads' else 'false'}")
    if f.access_key:
        w.append(f"user_identity.access_key_id = {_q(f.access_key)}")
    if f.region:
        w.append(f"aws_region = {_q(f.region)}")
    w += _common_sql(f, col=col)
    return ("select event_time, coalesce(user_identity.arn, user_identity.user_name) as who, event_name, event_source, "
            "aws_region as region, source_ip_address as source_ip, error_code, error_message, "
            "cast(read_only as varchar) as read_only, cast(resources as varchar) as resources, "
            "cast(request_parameters as varchar) as request, user_agent, user_identity.access_key_id as access_key, "
            f"event_id from aws_cloudtrail_log where {' and '.join(w)} order by event_time desc limit {int(limit)}")


def cloudwatch_sql(f: Filters, log_group: str, region: str | None, start: datetime, end: datetime,
                   event: str | None = None, limit: int = MAX_ROWS) -> str:
    """FilterLogEvents: the '=' filters below are combined into one CloudWatch filter pattern, sent to AWS."""
    col = {"who": "coalesce(user_identity ->> 'arn', user_identifier, username, '')", "event_name": "event_name",
           "error_code": "error_code", "resources": "coalesce(resources::text, '')",
           "request": "coalesce(request_parameters::text, '')", "source_ip": "source_ip_address"}
    w = [f"log_group_name = {_q(log_group)}", f"timestamp >= {_ts(start)}", f"timestamp <= {_ts(end)}"]
    if region:
        w.append(f"region = {_q(region)}")
    if event:
        w.append(f"event_name = {_q(event)}")
    if f.service:
        w.append(f"event_source = {_q(f.service)}")
    if f.reads != "all":
        w.append(f"read_only = {'true' if f.reads == 'reads' else 'false'}")
    if f.access_key:
        w.append(f"access_key_id = {_q(f.access_key)}")
    if f.region:
        w.append(f"aws_region = {_q(f.region)}")
    w += _common_sql(f, col=col)
    return ("select event_time, coalesce(user_identity ->> 'arn', user_identifier, username) as who, event_name, "
            "event_source, aws_region as region, source_ip_address as source_ip, error_code, error_message, "
            "read_only::text as read_only, resources::text as resources, request_parameters::text as request, "
            "user_agent, access_key_id as access_key, event_id "
            f"from aws_cloudtrail_trail_event where {' and '.join(w)} limit {int(limit)}")


def lookup_attribute(f: Filters, event: str | None) -> tuple[str, str] | None:
    """The ONE filter LookupEvents gets: the most selective one there is. None = no attribute at all."""
    if f.resource:
        return "resource_name", f.resource
    if event:
        return "event_name", event
    if f.access_key:
        return "access_key_id", f.access_key
    if f.user and not f.user.startswith(":") and "/" not in f.user and ":" not in f.user:
        return "username", f.user
    if f.resource_type:
        return "resource_type", f.resource_type
    if f.service:
        return "event_source", f.service
    if f.reads != "all":
        return "read_only", "true" if f.reads == "reads" else "false"
    return None


def lookup_sql(f: Filters, start: datetime, end: datetime, event: str | None = None,
               limit: int = MAX_ROWS) -> tuple[str, dict[str, Any]]:
    """LookupEvents through Steampipe: start_time / end_time and the attribute go to the API ('='); everything else
    is written so Steampipe evaluates it (ilike, functions, <>), because a second attribute makes the request
    invalid. No ORDER BY: events come newest first, and a sort would make Steampipe read every page."""
    attr = lookup_attribute(f, event)
    ev = "cloud_trail_event"
    col = {"who": f"coalesce({ev} -> 'userIdentity' ->> 'arn', username, '')", "event_name": "event_name",
           "error_code": f"{ev} ->> 'errorCode'", "resources": "coalesce(resources::text, '')",
           "request": f"coalesce({ev} ->> 'requestParameters', '')", "source_ip": f"{ev} ->> 'sourceIPAddress'"}
    w = [f"start_time = {_ts(start)}", f"end_time = {_ts(end)}"]
    params: dict[str, Any] = {}
    if attr:
        w.append(f"{attr[0]} = %(attr)s")
        params["attr"] = attr[1]
    name = attr[0] if attr else None
    if f.reads != "all" and name != "read_only":  # functions and <> are evaluated by Steampipe, not sent to AWS
        w.append("coalesce(read_only, 'false') <> 'true'" if f.reads == "writes" else "lower(read_only) = 'true'")
    if event and name != "event_name":
        w.append(f"lower(event_name) = lower({_q(event)})")
    if f.service and name != "event_source":
        w.append(f"lower(event_source) = {_q(f.service)}")
    if f.access_key and name != "access_key_id":
        w.append(f"coalesce(access_key_id, '') = {_q(f.access_key)}")
    if f.region:
        w.append(f"region = {_q(f.region)}")
    if f.resource and name == "resource_name":
        f = replace(f, resource=None)  # already exact at the API
    if f.resource_type and name == "resource_type":
        f = replace(f, resource_type=None)
    w += _common_sql(f, col=col)
    sql = (f"select event_time, coalesce({ev} -> 'userIdentity' ->> 'arn', username) as who, event_name, event_source, "
           f"region, {ev} ->> 'sourceIPAddress' as source_ip, {ev} ->> 'errorCode' as error_code, "
           f"{ev} ->> 'errorMessage' as error_message, read_only, resources::text as resources, "
           f"{ev} ->> 'requestParameters' as request, {ev} ->> 'userAgent' as user_agent, access_key_id as access_key, "
           f"event_id from aws_cloudtrail_lookup_event where {' and '.join(w)} limit {int(limit)}")
    return sql, params


def windows(start: datetime, end: datetime, broad: bool) -> list[tuple[datetime, datetime]]:
    """Newest first. A broad search starts with the last hour and widens (6 h, a day, 3 days, a week, then a week at a
    time), so recent events come back fast and a time budget can stop it with what it has. Selective: one window."""
    if not broad:
        return [(start, end)]
    out, prev = [], end
    for h in [1, 6, 24, 72, 168] + list(range(336, 24 * 400 + 1, 168)):
        lo = max(start, end - timedelta(hours=h))
        if lo < prev:
            out.append((lo, prev))
            prev = lo
        if lo <= start:
            break
    return out


# ------------------------------------------------------------------ result


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = {c: r.get(c) for c in COLUMNS}
    out["event_time"] = str(out["event_time"] or "")
    for c in ("resources", "request", "error_message", "user_agent"):
        if out[c] is not None:
            out[c] = str(out[c])[:2000]
    return out


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def top(key: Any, n: int = 15) -> list[dict[str, Any]]:
        c = Counter(k for k in (key(r) for r in rows) if k)
        return [{"name": k, "count": v} for k, v in c.most_common(n)]
    days = Counter(str(r.get("event_time") or "")[:10] for r in rows)
    return {"by_who": top(lambda r: str(r.get("who") or "?")),
            "by_event": top(lambda r: f"{str(r.get('event_source') or '').split('.')[0]}:{r.get('event_name')}"),
            "by_service": top(lambda r: str(r.get("event_source") or "").split(".")[0]),
            "by_source_ip": top(lambda r: r.get("source_ip")),
            "by_error": top(lambda r: r.get("error_code")),
            "by_day": [{"name": d, "count": n} for d, n in sorted(days.items(), reverse=True) if d],
            "failed": sum(1 for r in rows if r.get("error_code"))}


def _clean_error(e: Exception) -> str:
    text = str(e)
    low = text.lower()
    if "aws_cloudtrail_log" in low and ("does not exist" in low or "catalog error" in low or "no such" in low):
        return "Tailpipe has no CloudTrail logs collected yet (spendlens tailpipe collect cloudtrail)"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not set(ln.strip()) <= set("^~ ")]
    return (lines[0] if lines else type(e).__name__)[:240]


# ------------------------------------------------------------------ search


@dataclass
class Result:
    source: str
    filters: Filters
    rows: list[dict[str, Any]]
    covered_from: datetime
    queries: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def partial(self) -> bool:
        return self.covered_from > self.filters.start + timedelta(minutes=1)

    def to_dict(self, *, rows: int | None = None) -> dict[str, Any]:
        f = self.filters
        return {"source": self.source, "filters": f.describe(), "count": len(self.rows), "truncated": self.truncated,
                "partial": self.partial, "covered_from": self.covered_from.isoformat(),
                "covered_hours": round((f.end - self.covered_from).total_seconds() / 3600, 1),
                **summarise(self.rows), "rows": self.rows[: rows if rows is not None else len(self.rows)],
                "queries": self.queries, "notes": self.notes}


def log_group_of(cfg: Any, steampipe: Any) -> tuple[str, str | None] | None:
    """(log group name, its region) from finops.yaml cloudtrail.log_group, else the trail's CloudWatch Logs group."""
    ct = getattr(cfg, "cloudtrail", None)
    if ct is not None and ct.log_group:
        return ct.log_group, ct.log_group_region
    if ct is not None and not ct.discover_log_group:
        return None
    try:
        rows = steampipe.query("select distinct log_group_arn from aws_cloudtrail_trail where log_group_arn is not null")
    except Exception:  # noqa: BLE001 - discovery is best-effort; LookupEvents still answers
        return None
    for r in rows:
        if m := re.match(r"^arn:[^:]+:logs:([a-z0-9-]+):\d+:log-group:([^:*]+)", str(r.get("log_group_arn") or "")):
            return m.group(2), m.group(1)
    return None


def tailpipe_configured(cfg: Any) -> bool:
    """Tailpipe may be used for CloudTrail: enabled, installed, CloudTrail sources configured (sources.py)."""
    from .sources import tailpipe_ready

    return tailpipe_ready(cfg, "cloudtrail")


def search(cfg: Any, f: Filters, *, source: str = "auto", limit: int = MAX_ROWS, max_wait_s: float = MAX_WAIT_S,
           tailpipe_run: Any = None, steampipe: Any = None, clock: Any = time.monotonic,
           allow_aws: bool = True) -> Result:
    """source auto: Tailpipe when CloudTrail sources are configured for it and its collected logs cover the window;
    otherwise straight from AWS through Steampipe (the trail's CloudWatch Logs, then LookupEvents), as usual.
    allow_aws=False (the assistant with --no-live) never calls AWS: Tailpipe only."""
    if source not in SOURCES:
        raise CloudTrailError(f"source must be one of {', '.join(SOURCES)}")
    notes: list[str] = []
    use_tailpipe = source == "tailpipe"
    if source == "auto":
        source = "aws"
        if cfg.tailpipe.enabled and (tailpipe_run is not None or tailpipe_configured(cfg)):
            from .sources import tailpipe_coverage

            cov = tailpipe_coverage(cfg, "cloudtrail", tailpipe_run)
            if cov is None:
                notes.append("Tailpipe has no CloudTrail logs collected yet (spendlens tailpipe collect cloudtrail).")
            elif cov[0] <= f.start.date().isoformat() and cov[1] >= f.end.date().isoformat():
                use_tailpipe = True
            elif not allow_aws or f.start < _now() - timedelta(days=LOOKUP_DAYS):
                use_tailpipe = True  # older than LookupEvents keeps (or AWS not allowed): the logs are the best source
                notes.append(f"Tailpipe's CloudTrail logs cover {cov[0]} to {cov[1]}, not the whole window: events "
                             "outside it are not included (spendlens tailpipe collect cloudtrail --days N).")
            else:
                notes.append(f"Tailpipe's CloudTrail logs cover {cov[0]} to {cov[1]}, not the whole window, so this "
                             "was read from AWS through Steampipe (--source tailpipe uses the logs anyway; "
                             "spendlens tailpipe collect brings them up to date).")
    if use_tailpipe:
        from .graph.costs import CostSourceError, tailpipe_runner

        sql = tailpipe_sql(f, limit)
        try:
            run = tailpipe_run or tailpipe_runner(cfg.graph.tailpipe_path, timeout=300)
            rows = [r for r in (_row(x) for x in run(sql)) if matches(r, f)]
        except CostSourceError as e:
            raise CloudTrailError(_clean_error(e)) from e
        return Result("tailpipe", f, rows, f.start, [{"source": "tailpipe", "sql": sql}], notes, len(rows) >= limit)
    if not allow_aws:
        raise CloudTrailError("live AWS is off (--no-live) and Tailpipe has no CloudTrail logs for this: ask without "
                              "--no-live, or collect them (spendlens tailpipe init, spendlens tailpipe collect)")

    from .steampipe import SteampipeClient

    own = steampipe is None
    c = steampipe or SteampipeClient(cfg.steampipe, timeout_s=max(10, int(max_wait_s)))
    try:
        if source in ("aws", "cloudwatch"):
            lg = log_group_of(cfg, c)
            if lg:
                try:
                    return _paged(c, f, "cloudwatch", limit, max_wait_s, clock, notes, log_group=lg)
                except CloudTrailError:
                    raise
                except Exception as e:  # e.g. a log group in another account: fall back
                    if source == "cloudwatch":
                        raise CloudTrailError(f"CloudWatch Logs: {_clean_error(e)}") from e
                    notes.append(f"The trail's CloudWatch Logs group {lg[0]} could not be read ({_clean_error(e)}); "
                                 "used LookupEvents.")
            elif source == "cloudwatch":
                raise CloudTrailError("no CloudWatch Logs group: the trail does not send to CloudWatch Logs (set "
                                      "cloudtrail.log_group in finops.yaml if it does)")
        cut = f
        if f.end - f.start > timedelta(days=LOOKUP_DAYS):
            notes.append(f"LookupEvents keeps {LOOKUP_DAYS} days: the window was cut to the last {LOOKUP_DAYS} days.")
            cut = replace(f, start=max(f.start, _now() - timedelta(days=LOOKUP_DAYS)))
        res = _paged(c, cut, "lookup", limit, max_wait_s, clock, notes)
        res.notes.append("From LookupEvents: management events only, in the connection's regions (IAM and other "
                         "global services are recorded in us-east-1).")
        return res
    finally:
        if own:
            c.close()


def _paged(c: Any, f: Filters, source: str, limit: int, max_wait_s: float, clock: Any, notes: list[str], *,
           log_group: tuple[str, str | None] | None = None) -> Result:
    from .steampipe import SteampipeError

    exact = [e for e in f.events if not _wild(e)] if not [e for e in f.events if _wild(e)] else []
    events: list[str | None] = list(exact) or [None]  # one call per exact event name: each one is selective
    def wide(ev: str | None) -> bool:
        a = lookup_attribute(f, ev)
        return a is None or a[0] in ("event_source", "read_only", "resource_type")

    # FilterLogEvents returns oldest first, so CloudWatch always goes in newest-first windows
    broad = source != "lookup" or any(wide(e) for e in events)
    deadline = clock() + max_wait_s
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    queries: list[dict[str, str]] = []
    covered = f.end
    stopped = False
    for lo, hi in windows(f.start, f.end, broad):
        for ev in events:
            left = deadline - clock()
            if left <= 1:
                stopped = True
                break
            if source == "lookup":
                sql, params = lookup_sql(f, lo, hi, ev, limit)
            else:
                assert log_group is not None
                sql, params = cloudwatch_sql(f, log_group[0], log_group[1], lo, hi, ev, limit), {}
            queries.append({"source": source, "sql": sql.replace("%(attr)s", _q(params["attr"])) if params else sql})
            try:
                got = c.query(sql, params or None, timeout_s=max(1, int(left)))
            except SteampipeError as e:
                low = str(e).lower()
                if "statement timeout" in low or "canceling statement" in low:
                    stopped = True
                    break
                raise
            for r in (_row(x) for x in got):
                key = r.get("event_id") or (r["event_time"], r.get("event_name"), r.get("who"))
                if key not in seen and matches(r, f):  # windows share their edges: never count an event twice
                    seen.add(key)
                    rows.append(r)
        if stopped:
            break
        covered = lo
        if len(rows) >= limit:
            break
    rows.sort(key=lambda r: r["event_time"], reverse=True)
    res = Result(source, f, rows[:limit], covered, queries, notes, len(rows) >= limit)
    if res.partial and not res.truncated:
        api = "LookupEvents (50 events a page, rate-limited)" if source == "lookup" else "CloudWatch Logs"
        notes.append(f"{api} was still reading after {int(max_wait_s)}s: covered {covered.isoformat()} to "
                     f"{f.end.isoformat()}. Narrow it with an event name, resource or user, use --max-wait, or "
                     "collect the trail's logs with Tailpipe.")
    return res
