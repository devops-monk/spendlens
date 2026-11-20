"""Who created each resource, from CloudTrail: an opt-in owner hint (graph.owner_from_cloudtrail).

When it is on, `graph collect` runs one LookupEvents query per creation event (RunInstances, CreateVolume, ...),
each bounded to the last graph.cloudtrail_days days (90 at most: what LookupEvents keeps), and records on each
resource who created it and when. `graph owners` then suggests the creator as the owner tag
(graph.cloudtrail_owner_key, default Owner) for resources that lack it and that nothing attached already names.

It is off by default: LookupEvents allows about 2 calls a second per account and region, so with many resources
in a busy account it adds minutes to a collection, and it needs cloudtrail:LookupEvents. Read-only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .model import Graph
from .sources import aliases

# creation events for the resource types in the graph
CREATE_EVENTS = (
    "RunInstances", "CreateVolume", "CreateSnapshot", "CopySnapshot", "CreateImage", "CopyImage", "RegisterImage",
    "AllocateAddress", "CreateNatGateway", "CreateNetworkInterface", "CreateLoadBalancer", "CreateTargetGroup",
    "CreateAutoScalingGroup", "CreateDBInstance", "CreateDBCluster", "CreateDBSnapshot", "CreateDBClusterSnapshot",
    "CreateCluster", "CreateService", "CreateFunction20150331",
)


def event_sql(event: str, days: int) -> str:
    days = int(days)
    if not 1 <= days <= 90:
        raise ValueError("graph.cloudtrail_days must be between 1 and 90")
    if event not in CREATE_EVENTS:
        raise ValueError(f"unknown event {event!r}")
    # start_time / end_time / event_name are sent to the LookupEvents API ('=' filters)
    return ("select event_name, event_time, username, resources from aws_cloudtrail_lookup_event "
            f"where start_time = now() - interval '{days} days' and end_time = now() and event_name = '{event}'")


def _resources(v: Any) -> list[str]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return [str(r.get("ResourceName")) for r in v or [] if isinstance(r, dict) and r.get("ResourceName")]


def _when(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    return str(v or "")


def creators(query: Callable[[str], list[dict[str, Any]]], days: int,
             progress: Callable[[str], None] | None = None) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """({resource name or id: {by, at, event}}, {event: why it was skipped})."""
    from ..steampipe import SteampipeError

    out: dict[str, dict[str, str]] = {}
    skipped: dict[str, str] = {}
    for i, ev in enumerate(CREATE_EVENTS, 1):
        if progress:
            progress(f"{ev} ({i}/{len(CREATE_EVENTS)})")
        try:
            rows = query(event_sql(ev, days))
        except SteampipeError as e:
            skipped[ev] = str(e).splitlines()[0][:200]
            if "accessdenied" in str(e).lower() or "not authorized" in str(e).lower():
                break  # no permission: every other event would fail the same way
            continue
        for r in rows:
            if r.get("event_name") not in (None, ev) or not r.get("username"):
                continue
            for name in _resources(r.get("resources")):
                prev = out.get(name)
                at = _when(r.get("event_time"))
                if prev is None or at < prev["at"]:  # the earliest event created it
                    out[name] = {"by": str(r["username"]), "at": at, "event": ev}
    return out, skipped


def apply(g: Graph, found: dict[str, dict[str, str]]) -> int:
    """Record the creator on each matching resource. Returns how many matched."""
    if not found:
        return 0
    index: dict[str, dict[str, str]] = {}
    for k, v in found.items():
        index.setdefault(k, v)
        if k.startswith("arn:"):
            index.setdefault(k.rsplit("/", 1)[-1].rsplit(":", 1)[-1], v)
    n_matched = 0
    for n in g.nodes.values():
        hit = next((index[k] for k in [*aliases(n), n.name] if k and k in index), None)
        if hit:
            n.attrs.update({"created_by": hit["by"], "created_at": hit["at"], "created_event": hit["event"]})
            n_matched += 1
    return n_matched
