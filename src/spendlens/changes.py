"""What changed in the AWS account, and who changed it: CloudTrail write events, read-only.

`spendlens changes` is the short form of `spendlens cloudtrail events` for write events (see cloudtrail.py for the
sources, filters and limits): routine calls are left out, and the result is grouped by who and by API call.
"""

from __future__ import annotations

import time
from typing import Any

from . import cloudtrail as ct

MAX_ROWS = ct.MAX_ROWS
MAX_WAIT_S = ct.MAX_WAIT_S
NOISE = ct.NOISE
_SOURCE = {"auto": "auto", "tailpipe": "tailpipe", "cloudwatch": "cloudwatch", "lookup_events": "lookup",
           "lookup": "lookup"}


def find_changes(cfg: Any, hours: int = 24, *, user: str | None = None, resource: str | None = None,
                 service: str | None = None, event: str | None = None, source: str = "auto",
                 tailpipe_run: Any = None, steampipe: Any = None, max_wait_s: float = MAX_WAIT_S,
                 clock: Any = time.monotonic) -> dict[str, Any]:
    """{source, count, by_who, by_event, rows, notes, partial, covered_hours}. source: auto | tailpipe | cloudwatch |
    lookup_events."""
    hours = max(1, min(int(hours), 24 * 400))
    f = ct.Filters.build(hours=hours, user=user, resource=resource, service=service, events=[event] if event else None)
    res = ct.search(cfg, f, source=_SOURCE.get(source, source), tailpipe_run=tailpipe_run, steampipe=steampipe,
                    max_wait_s=max_wait_s, clock=clock)
    d = res.to_dict()
    src = "lookup_events" if res.source == "lookup" else res.source
    return {**d, "source": src, "hours": hours,
            "by_who": [{"who": x["name"], "changes": x["count"]} for x in d["by_who"]],
            "by_event": [{"event": x["name"], "count": x["count"]} for x in d["by_event"]]}
