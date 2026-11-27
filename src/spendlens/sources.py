"""Where each kind of data comes from: one place that every feature, the assistant and `spendlens sources` ask.

  cloudtrail   Tailpipe's collected trail logs (when configured and covering the window), the trail's CloudWatch
               Logs group, CloudTrail LookupEvents (always; 90 days)
  costs        the CUR through Tailpipe (when configured), Cost Explorer resource data saved by `cost resources`,
               the price book (always; estimates)
  flow_logs    Tailpipe's collected VPC flow logs (when configured); no Steampipe equivalent
  live AWS     the Steampipe AWS plugin (ask --live, every check)

Tailpipe is used automatically only when it is enabled (tailpipe.enabled), installed, and has sources configured
(spendlens tailpipe init or your own .tpc files); otherwise spendlens uses the Steampipe tables, as usual.
"""

from __future__ import annotations

import shutil
from typing import Any

from .tailpipe import KINDS as KIND_TABLES  # every kind of data Tailpipe can hold


def tailpipe_ready(cfg: Any, kind: str | None = None) -> bool:
    """Tailpipe may be used automatically: enabled, installed, and (for `kind`, or any kind) configured."""
    if not cfg.tailpipe.enabled or shutil.which(cfg.graph.tailpipe_path) is None:
        return False
    from .tailpipe import configured

    have = configured()
    return bool(have.get(kind)) if kind else any(have.values())


def tailpipe_coverage(cfg: Any, kind: str, run: Any = None) -> tuple[str, str] | None:
    """(first, last) day Tailpipe has collected for `kind`, or None (nothing collected, or Tailpipe unusable)."""
    from .graph.costs import CostSourceError, tailpipe_runner

    try:
        r = (run or tailpipe_runner(cfg.graph.tailpipe_path, timeout=120))(
            f"select cast(min(tp_date) as varchar) as lo, cast(max(tp_date) as varchar) as hi from {KIND_TABLES[kind]}")
    except CostSourceError:
        return None
    row = r[0] if r else {}
    return (str(row["lo"])[:10], str(row["hi"])[:10]) if row.get("lo") and row.get("hi") else None


def report(cfg: Any, *, steampipe: Any = None, history_db: Any = None) -> list[dict[str, Any]]:
    """One row per kind of data: the sources in order, which one is used now, and why. Local unless `steampipe`
    is given (then the trail's CloudWatch Logs group is looked up, read-only)."""
    from .tailpipe import configured

    installed = shutil.which(cfg.graph.tailpipe_path) is not None
    have = configured() if installed else {}
    rows = []

    def tp(kind: str) -> dict[str, Any]:
        if not cfg.tailpipe.enabled:
            return {"source": "Tailpipe", "ready": False, "detail": "turned off (tailpipe.enabled: false)"}
        if not installed:
            return {"source": "Tailpipe", "ready": False, "detail": "not installed"}
        if not have.get(kind):
            return {"source": "Tailpipe", "ready": False, "detail": "no sources configured (spendlens tailpipe init)"}
        cov = tailpipe_coverage(cfg, kind)
        return {"source": "Tailpipe", "ready": bool(cov),
                "detail": f"collected {cov[0]} to {cov[1]}" if cov else "configured, nothing collected yet "
                "(spendlens tailpipe collect)"}

    lg = None
    if steampipe is not None:
        from .cloudtrail import log_group_of

        lg = log_group_of(cfg, steampipe)
    elif cfg.cloudtrail.log_group:
        lg = (cfg.cloudtrail.log_group, cfg.cloudtrail.log_group_region)
    ct = [tp("cloudtrail"),
          {"source": "CloudWatch Logs", "ready": bool(lg),
           "detail": f"log group {lg[0]}" if lg else ("the trail does not send to CloudWatch Logs" if steampipe
                                                      is not None else "checked with --aws")},
          {"source": "LookupEvents", "ready": True, "detail": "always: 90 days of management events"}]
    rows.append({"data": "CloudTrail events", "sources": ct,
                 "used": (("Tailpipe when its logs cover the window, else " if ct[0]["ready"] else "")
                          + ("CloudWatch Logs, then LookupEvents" if lg else "LookupEvents")),
                 "commands": "cloudtrail, changes, ask"})

    ce = None
    if history_db is not None:
        try:
            from .history import HistoryStore

            with HistoryStore(history_db, read_only=True) as s:
                r = s.conn.execute("select max(started_at) as at from runs where feature = 'cost_resources'").fetchone()
                ce = r["at"] if r and r["at"] else None
        except (OSError, FileNotFoundError, Exception):  # noqa: BLE001 - no history yet
            ce = None
    mode = cfg.graph.cost_source
    costs = [tp("cur") if mode in ("auto", "cur") else {"source": "Tailpipe", "ready": False,
                                                       "detail": f"graph.cost_source: {mode}"},
             {"source": "Cost Explorer resource data", "ready": bool(ce) and mode == "auto",
              "detail": f"saved {str(ce)[:16]} (spendlens cost resources)" if ce else "none saved (spendlens cost "
              "resources)"},
             {"source": "Price book", "ready": True, "detail": "always: estimates"}]
    used = next((c["source"] for c in costs if c["ready"]), "Price book")
    rows.append({"data": "Resource costs (graph)", "sources": costs, "used": used,
                 "commands": "graph collect, graph orphans, cleanup savings"})
    fl = [tp("flow_logs")]
    rows.append({"data": "VPC flow logs", "sources": fl,
                 "used": "Tailpipe" if fl[0]["ready"] else "none (traffic costs need Tailpipe)",
                 "commands": "graph traffic"})
    rows.append({"data": "Everything else", "sources": [{"source": "Steampipe AWS plugin", "ready": True,
                                                         "detail": f"connection {cfg.steampipe.connection or 'aws'}, "
                                                                   f"regions {', '.join(cfg.aws.regions)}"}],
                 "used": "Steampipe AWS plugin", "commands": "every check, ask (live)"})
    return rows
