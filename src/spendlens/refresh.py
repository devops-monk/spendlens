"""Keep summary commands consistent: reuse what was checked today, run the rest live first.

`savings summary`, `dashboard` and `notify` read the latest saved run of each feature. With the default
`--source live`, any feature without a run from today (UTC) is run live first -- in a separate process, exactly
like `spendlens report` -- so every summary covers every source and is at most a day old. `--refresh` re-runs
them all; `--source last` never calls AWS.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .common import err

# the live command that refreshes each feature (same arguments as `spendlens report`)
REFRESH_ARGS: dict[str, list[str]] = {
    "tags": ["tags", "--status", "all"],
    "rightsize": ["rightsize", "--status", "all"],
    "waste": ["waste", "--status", "all"],
    "cost_diff": ["cost", "diff", "--show", "all"],
    "cost_trend": ["cost", "trend"],
    "cost_showback": ["cost", "showback"],
    "anomalies": ["anomalies"],
    "forecast": ["forecast"],
    "savings_hub": ["savings", "hub"],
    "savings_commitments": ["savings", "commitments"],
    "graph": ["graph", "orphans"],
}


def stale(path: Path, features: tuple[str, ...] | list[str], refresh: bool = False) -> list[str]:
    """Features without a saved run from today (UTC), or all of them with refresh=True."""
    if refresh:
        return list(features)
    from .history import HistoryStore

    today = datetime.now(UTC).date().isoformat()
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError:
        return list(features)
    with store:
        return [f for f in features if not ((r := store.latest_run(f)) and r.started_at[:10] == today)]


def run_live(feat: str, config: Any, profile: str | None, step: str) -> dict[str, Any]:
    """Run one feature live in a separate process (it saves its run to the history)."""
    from .features.report import _run

    cmd = [sys.executable, "-m", "spendlens", *REFRESH_ARGS[feat], "--source", "live", "-f", "json", "-o", os.devnull]
    if config:
        cmd += ["--config", str(config)]
    if profile:
        cmd += ["--profile", profile]
    return _run(feat, cmd, timeout=1800, step=step)


def ensure_fresh(path: Path, features: tuple[str, ...] | list[str], *, config: Any = None, profile: str | None = None,
                 refresh: bool = False) -> tuple[list[str], dict[str, str]]:
    """Run every stale feature live. Returns (run now, {failed feature: reason}); failures are printed."""
    todo = stale(path, features, refresh)
    if todo:
        err.print(f"[dim]Checking {len(todo)} source(s) not run today: {', '.join(todo)}. "
                  "Use --source last to skip.[/]")
    fetched: list[str] = []
    failed: dict[str, str] = {}
    for i, feat in enumerate(todo, 1):
        r = run_live(feat, config, profile, step=f"{i}/{len(todo)}")
        if r["result"] == "failed":
            failed[feat] = r["detail"]
            err.print(f"[red]{feat} could not run[/] [dim]({r['detail'] or 'run it on its own to see why'})[/]")
        else:
            fetched.append(feat)
    if failed:
        err.print(f"[yellow]Using older saved runs (if any) for {', '.join(failed)}.[/]")
    return fetched, failed
