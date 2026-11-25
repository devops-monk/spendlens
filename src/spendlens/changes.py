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

import time
from collections import Counter
from typing import Any

NOISE = ("AssumeRole", "AssumeRoleWithSAML", "AssumeRoleWithWebIdentity", "GetSessionToken", "GetFederationToken",
         "Decrypt", "Encrypt", "GenerateDataKey", "GenerateDataKeyWithoutPlaintext", "ReEncrypt", "CreateLogStream",
         "PutLogEvents", "StartQuery", "SetTaskStatus", "ReportTaskProgress", "PollForTask", "SendCommand",
         "UpdateInstanceInformation", "RefreshCredentials", "CreateToken", "Federate")
MAX_ROWS = 500
MAX_WAIT_S = 60.0


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
               event: str | None = None, since_hours: int = 0) -> tuple[str, dict[str, Any]]:
    """For Steampipe: the window [now - hours, now - since_hours] and ONE '=' filter go to the LookupEvents API.

    LookupEvents accepts a single lookup attribute (the plugin silently drops a request with two), so the most
    selective filter is sent as '=' and the others are written so Steampipe filters them itself (ilike, functions,
    <>): read-only events are only sent as the attribute when nothing else is. There is no ORDER BY: LookupEvents
    already returns newest first, and sorting would make Steampipe read every page before returning a row; with a
    plain LIMIT it stops as soon as it has enough.
    """
    hours, since_hours = int(hours), max(0, int(since_hours))
    end = f"now() - interval '{since_hours} hours'" if since_hours else "now()"
    where = [f"start_time = now() - interval '{hours} hours'", f"end_time = {end}"]
    params: dict[str, Any] = {}
    svc = _service(service)
    if resource:
        where.append("resource_name = %(resource)s")
        params["resource"] = resource
    elif event:
        where.append("event_name = %(event)s")
        params["event"] = event
    elif user:
        where.append("username = %(user)s")
        params["user"] = user
    elif svc:
        where.append("event_source = %(service)s")
        params["service"] = svc
    else:
        where.append("read_only = 'false'")
    if params:  # something else went to the API: Steampipe drops read events itself
        where.append("coalesce(read_only, 'false') <> 'true'")
    if event and "event" not in params:
        where.append("lower(event_name) = lower(%(event)s)")
        params["event"] = event
    if svc and "service" not in params:
        where.append("lower(event_source) = %(service)s")
        params["service"] = svc
    if user and "user" not in params:
        where.append("(coalesce(cloud_trail_event -> 'userIdentity' ->> 'arn', '') ilike %(user_like)s "
                     "or coalesce(username, '') ilike %(user_like)s)")
        params["user_like"] = f"%{user}%"
    noise = ", ".join(_quote(n) for n in NOISE)
    sql = ("select event_time, coalesce(cloud_trail_event -> 'userIdentity' ->> 'arn', username) as who, event_name, "
           "event_source, region, cloud_trail_event ->> 'sourceIPAddress' as source_ip, "
           "cloud_trail_event ->> 'errorCode' as error_code, resource_name as details "
           f"from aws_cloudtrail_lookup_event where {' and '.join(where)} and event_name not in ({noise}) "
           f"limit {MAX_ROWS}")
    return sql, params


def windows(hours: int, broad: bool) -> list[tuple[int, int]]:
    """(since_hours, hours) pairs, newest first. A broad search (service only, or nothing) starts small and widens,
    so recent changes come back fast and a time budget can stop it with what it has; a selective one is one call."""
    if not broad:
        return [(0, hours)]
    edges = [e for e in (1, 6, 24, 72, 168) if e < hours]
    edges += list(range(336, hours, 168)) + [hours]
    out, prev = [], 0
    for e in edges:
        out.append((prev, e))
        prev = e
    return out


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
                 tailpipe_run: Any = None, steampipe: Any = None, max_wait_s: float = MAX_WAIT_S,
                 clock: Any = time.monotonic) -> dict[str, Any]:
    """{source, count, by_who, by_event, rows, notes}. source: auto | tailpipe | lookup_events.

    LookupEvents gets at most max_wait_s: what it found by then is returned, with covered_hours and a note."""
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
    from .steampipe import SteampipeClient, SteampipeError

    broad = not (resource or event or user)
    c = steampipe or SteampipeClient(cfg.steampipe, timeout_s=max(10, int(max_wait_s)))
    rows: list[dict[str, Any]] = []
    covered = 0
    deadline = clock() + max_wait_s
    try:
        for since, upto in windows(hours, broad):
            left = deadline - clock()
            if left <= 1:
                break
            sql, params = lookup_sql(upto, user=user, resource=resource, service=service, event=event,
                                     since_hours=since)
            try:
                got = c.query(sql, params or None, timeout_s=max(1, int(left)))
            except SteampipeError as e:
                if "statement timeout" not in str(e).lower() and "canceling statement" not in str(e).lower():
                    raise
                break
            rows += [r for r in got if _match(r, user, service, event)]
            covered = upto
            if len(rows) >= MAX_ROWS:
                rows = rows[:MAX_ROWS]
                break
    finally:
        if steampipe is None:
            c.close()
    if covered < hours:
        notes.append(f"LookupEvents is slow (50 events a page, rate-limited): stopped after {int(max_wait_s)}s with "
                     f"the last {covered} of {hours} hours covered. Narrow it with an event (e.g. UpdateService, "
                     "TerminateInstances), a resource or a user, or collect CloudTrail logs with Tailpipe "
                     "(`spendlens tailpipe init`, `spendlens tailpipe collect`).")
    notes.append("From LookupEvents: management events only, in the connection's regions (global services such as "
                 "IAM are recorded in us-east-1).")
    rows.sort(key=lambda r: str(r.get("event_time") or ""), reverse=True)
    return {**summarise(rows, "lookup_events", hours), "covered_hours": covered, "partial": covered < hours,
            "notes": notes}
