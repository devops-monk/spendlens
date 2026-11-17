"""Live progress on stderr while long steps run: a spinner, what is happening and the elapsed time, then a ✓ line.

Shown only on an interactive terminal: CI logs, pipes and redirected stderr stay clean. Set SPENDLENS_NO_PROGRESS=1
to switch it off. stdout (tables, CSV, JSON) is never touched, so output can still be piped.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console(stderr=True, highlight=False)
_live: Progress | None = None


def enabled() -> bool:
    return console.is_terminal and not os.environ.get("SPENDLENS_NO_PROGRESS") and not os.environ.get("CI")


def elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


class Activity:
    """Handle for a running step: update(text) changes the message; done(text) sets the ✓ line printed at the end."""

    def __init__(self, progress: Progress | None, task: Any, done: str | None):
        self._progress, self._task, self.done_text, self.failed = progress, task, done, False

    def update(self, text: str) -> None:
        if self._progress is not None:
            self._progress.update(self._task, description=text)

    def done(self, text: str, *, failed: bool = False) -> None:
        self.done_text, self.failed = text, failed


@contextmanager
def activity(text: str, *, done: str | None = None) -> Iterator[Activity]:
    """Show `text` with a spinner and elapsed time while the block runs. Nested activities share one display."""
    global _live
    if not enabled():
        yield Activity(None, None, done)
        return
    owner = _live is None
    if owner:
        _live = Progress(SpinnerColumn(style="magenta"), TextColumn("{task.description}"),
                         TimeElapsedColumn(), console=console, transient=True)
        _live.start()
    live = _live
    task = live.add_task(text)
    act = Activity(live, task, done)
    start = time.monotonic()
    ok = False
    try:
        yield act
        ok = True
    finally:
        live.remove_task(task)
        if owner:
            live.stop()
            _live = None
        if ok and act.done_text:
            mark = "[red]✗[/]" if act.failed else "[green]✓[/]"
            console.print(f"{mark} {act.done_text} [dim]· {elapsed(time.monotonic() - start)}[/]")
