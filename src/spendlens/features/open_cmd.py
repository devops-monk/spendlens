"""`spendlens open [page]`: build a report page from the saved runs and open it in the browser.

Offline and quick: pages are built from the history (--source last) or the sample data (--source demo), never from
AWS, and without an AI provider unless --ai. They are written next to the history (reports/<page>.html), so the
latest one is always in the same place; --out writes elsewhere and --no-browser just prints the path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from ..common import ConfigOpt, SetOpt, Source, err, history_path, load
from ..plugins import Registry


class Page(str, Enum):
    review = "review"
    dashboard = "dashboard"
    brief = "brief"
    cleanup = "cleanup"
    traffic = "traffic"
    age = "age"


ARGS = {
    Page.review: ["review"], Page.dashboard: ["dashboard"], Page.brief: ["brief"],
    Page.cleanup: ["graph", "orphans", "-f", "html"], Page.traffic: ["graph", "traffic", "-f", "html"],
    Page.age: ["graph", "age", "-f", "html"],
}
AI_PAGES = (Page.review, Page.brief)


def open_page(
    page: Annotated[Page, typer.Argument(help="Which page.")] = Page.review,
    source: Annotated[Source, typer.Option("--source", help="last = your saved runs (default); demo = sample data.")] = Source.last,
    ai: Annotated[bool, typer.Option("--ai", help="review / brief: write the analysis with your AI provider.")] = False,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Write the page here instead.")] = None,
    browser: Annotated[bool, typer.Option("--browser/--no-browser", help="Open it, or only print the path.")] = True,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """Build a page from the saved runs and open it: review (default), dashboard, brief, cleanup, traffic or age.

    No AWS calls: it uses what the checks already saved (run `spendlens report` or the checks first).
    Examples:
      spendlens open
      spendlens open cleanup
      spendlens open dashboard --source demo
      spendlens open review --ai
    """
    if source is Source.live:
        raise typer.BadParameter("open builds from saved runs: use --source last (default) or demo, or run the checks "
                                 "first (spendlens report).", param_hint="--source")
    cfg = load(config, None, sets)
    path = out or history_path(cfg).parent / "reports" / f"{'demo-' if source is Source.demo else ''}{page.value}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "spendlens", *ARGS[page], "--source", source.value, "-o", str(path)]
    if page in AI_PAGES:
        cmd.append("--ai" if ai else "--no-ai")
    if config:
        cmd += ["--config", str(config)]
    for s in sets or []:
        cmd += ["--set", s]
    from ..ui import activity

    with activity(f"Building the {page.value} page [dim](saved runs, no AWS calls)[/]"):
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if p.returncode != 0 or not path.is_file():
        lines = [ln for ln in (p.stderr or p.stdout).splitlines() if ln.strip()]
        err.print(f"[red]Could not build the {page.value} page:[/] {lines[-1] if lines else 'unknown error'}")
        raise typer.Exit(1)
    err.print(f"[green]Built[/] {path}")
    if browser and not os.environ.get("SPENDLENS_NO_BROWSER"):
        import webbrowser

        if not webbrowser.open(path.resolve().as_uri()):
            err.print("[dim]No browser available here: open the file above.[/]")


def register(reg: Registry) -> None:
    reg.add_command(open_page, "open")
