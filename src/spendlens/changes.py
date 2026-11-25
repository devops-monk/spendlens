"""What changed in the AWS account, and who changed it: CloudTrail write events, read-only.

Two sources, best first:
  tailpipe       the trail's own logs collected by Tailpipe (aws_cloudtrail_log): any age, every region the trail
                 records, data events when the trail has them
  lookup_events  CloudTrail LookupEvents through Steampipe (aws_cloudtrail_lookup_event): the last 90 days of
                 management events, in the regions the Steampipe connection covers (IAM and other global services
                 are recorded in us-east-1)

Only write events (read_only = false) are listed, minus routine calls that change nothing people manage
(role assumptions, KMS data calls, log delivery). Filters: user, resource, service (e.g. ec2) and event name.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

NOISE = ("AssumeRole", "AssumeRoleWithSAML", "AssumeRoleWithWebIdentity", "GetSessionToken", "GetFederationToken",
         "Decrypt", "Encrypt", "GenerateDataKey", "GenerateDataKeyWithoutPlaintext", "ReEncrypt", "CreateLogStream",
         "PutLogEvents", "StartQuery", "SetTaskStatus", "ReportTaskProgress", "PollForTask", "SendCommand",
         "UpdateInstanceInformation", "RefreshCredentials", "CreateToken", "Federate")
MAX_ROWS = 500


def _quote(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _service(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().lower()
    return s if "." in s else f"{s}.amazonaws.com"


def tailpipe_sql(hours: int, *, user: str | None = None, resource: str | None = None, service: str | None = None,
                 event: str | None = None) -> str:
    days = max(1, (int(hours) + 23) // 24 + 1)
    where = [f"tp_date >= current_date - interval {days} day",
             f"event_time >= now() - interval {int(hours)} hour",
             "coalesce(read_only, false) = false",
             f"event_name not in ({', '.join(_quote(n) for n in NOISE)})"]
    if user:
        where.append(f"(user_identity.arn ilike {_quote('%' + user + '%')} or "
                     f"user_identity.user_name ilike {_quote('%' + user + '%')})")
    if svc := _service(service):
        where.append(f"event_source = {_quote(svc)}")
    if event:
        where.append(f"event_name = {_quote(event)}")
    if resource:
        where.append(f"(cast(resources as varchar) ilike {_quote('%' + resource + '%')} or "
                     f"cast(request_parameters as varchar) ilike {_quote('%' + resource + '%')})")
    return ("select event_time, user_identity.arn as who, event_name, event_source, aws_region as region, "
            "source_ip_address as source_ip, error_code, cast(request_parameters as varchar) as details "
            f"from aws_cloudtrail_log where {' and '.join(where)} order by event_time desc limit {MAX_ROWS}")


def lookup_sql(hours: int, *, user: str | None = None, resource: str | None = None, service: str | None = None,
               event: str | None = None) -> tuple[str, dict[str, Any]]:
    """For Steampipe: start_time / end_time and the '=' filters are sent to the LookupEvents API."""
    where = [f"start_time = now() - interval '{int(hours)} hours'", "end_time = now()", "read_only = 'false'"]
    params: dict[str, Any] = {}
    # LookupEvents takes one lookup attribute: send the most selective one, filter the rest in SQL
    if resource:
        where.append("resource_name = %(resource)s")
        params["resource"] = resource
    elif event:
        where.append("event_name = %(event)s")
        params["event"] = event
    elif user:
        where.append("username = %(user)s")
        params["user"] = user
    elif service:
        where.append("event_source = %(service)s")
        params["service"] = _service(service)
    noise = ", ".join(_quote(n) for n in NOISE)
    sql = ("select event_time, coalesce(cloud_trail_event -> 'userIdentity' ->> 'arn', username) as who, event_name, "
           "event_source, region, cloud_trail_event ->> 'sourceIPAddress' as source_ip, "
           "cloud_trail_event ->> 'errorCode' as error_code, resource_name as details "
           f"from aws_cloudtrail_lookup_event where {' and '.join(where)} and event_name not in ({noise}) "
           f"order by event_time desc limit {MAX_ROWS}")
    return sql, params


def _match(r: dict[str, Any], user: str | None, service: str | None, event: str | None) -> bool:
    """Filters LookupEvents could not take (it accepts one attribute)."""
    if user and user.lower() not in str(r.get("who") or "").lower():
        return False
    if (svc := _service(service)) and r.get("event_source") != svc:
        return False
    return not (event and r.get("event_name") != event)


def summarise(rows: list[dict[str, Any]], source: str, hours: int) -> dict[str, Any]:
    who = Counter(str(r.get("who") or "?") for r in rows)
    what = Counter(f"{str(r.get('event_source') or '').split('.')[0]}:{r.get('event_name')}" for r in rows)
    return {"source": source, "hours": hours, "count": len(rows), "truncated": len(rows) >= MAX_ROWS,
            "by_who": [{"who": k, "changes": v} for k, v in who.most_common(15)],
            "by_event": [{"event": k, "count": v} for k, v in what.most_common(15)],
            "failed": sum(1 for r in rows if r.get("error_code")), "rows": rows}


def find_changes(cfg: Any, hours: int = 24, *, user: str | None = None, resource: str | None = None,
                 service: str | None = None, event: str | None = None, source: str = "auto",
                 tailpipe_run: Any = None, steampipe: Any = None) -> dict[str, Any]:
    """{source, count, by_who, by_event, rows, notes}. source: auto | tailpipe | lookup_events."""
    hours = max(1, min(int(hours), 24 * 400))
    notes: list[str] = []
    if source in ("auto", "tailpipe"):
        from .graph.costs import CostSourceError, tailpipe_runner

        try:
            run = tailpipe_run or tailpipe_runner(cfg.graph.tailpipe_path, timeout=300)
            rows = run(tailpipe_sql(hours, user=user, resource=resource, service=service, event=event))
            out = summarise(rows, "tailpipe", hours)
            if rows or source == "tailpipe":
                return {**out, "notes": notes}
            notes.append("No matching events in Tailpipe's CloudTrail logs; checked LookupEvents too.")
        except CostSourceError as e:
            notes.append(f"Tailpipe CloudTrail logs unavailable ({str(e)[:120]}); used LookupEvents.")
            if source == "tailpipe":
                return {**summarise([], "tailpipe", hours), "notes": notes}
    if hours > 90 * 24:
        notes.append("LookupEvents keeps 90 days: the window was cut to 90 days.")
        hours = 90 * 24
    from .steampipe import SteampipeClient

    sql, params = lookup_sql(hours, user=user, resource=resource, service=service, event=event)
    c = steampipe or SteampipeClient(cfg.steampipe, timeout_s=300)
    try:
        rows = [r for r in c.query(sql, params or None) if _match(r, user, service, event)]
    finally:
        if steampipe is None:
            c.close()
    notes.append("From LookupEvents: management events only, in the connection's regions (global services such as "
                 "IAM are recorded in us-east-1).")
    return {**summarise(rows, "lookup_events", hours), "notes": notes}
