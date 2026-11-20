"""`spendlens session`: the saved `spendlens ask` conversations (list, show, delete, clear). Offline."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markdown import Markdown
from rich.table import Table

from ..ai.session import SessionError, Sessions, sessions_dir
from ..common import BRAND, ConfigOpt, console, err, history_path, load
from ..plugins import Registry

session_app = typer.Typer(help="Saved `spendlens ask` sessions: follow-up questions that remember the conversation.",
                          no_args_is_help=True)


def _sessions(config) -> Sessions:
    return Sessions(sessions_dir(history_path(load(config))))


def _fail(e: SessionError) -> typer.Exit:
    err.print(f"[red]{e}[/]")
    return typer.Exit(1)


@session_app.command("list")
def session_list(config: ConfigOpt = None) -> None:
    """Every saved session, most recent first (* = the one `spendlens ask -C` continues)."""
    s = _sessions(config)
    items, cur = s.list(), s.current()
    if not items:
        console.print('[dim]No sessions yet. Start one: spendlens ask --new "your question"[/]')
        return
    t = Table(header_style=f"bold {BRAND}", title=f"Sessions · {s.root}")
    for c in ("", "session", "questions", "last used (UTC)", "last question"):
        t.add_column(c, overflow="fold")
    for x in items:
        last = x.turns[-1]["question"] if x.turns else ""
        t.add_row("*" if x.name == cur else "", x.name, str(len(x.turns)), x.updated[:16].replace("T", " "),
                  last[:90] + ("…" if len(last) > 90 else ""))
    console.print(t)


@session_app.command("show")
def session_show(
    name: Annotated[str | None, typer.Argument(help="Session name (default: the current one).")] = None,
    config: ConfigOpt = None,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Save the whole session as a page (.html) or .md.")] = None,
) -> None:
    """The questions and answers of a session (-o: save them as one HTML page or Markdown file)."""
    s = _sessions(config)
    try:
        x = s.get(name or s.current() or "")
    except SessionError as e:
        raise _fail(SessionError("No current session. See: spendlens session list") if not name else e) from e
    if out:
        from ..render import answer as page

        cfg = load(config)
        page.write(out, x.turns, draw=page.graph_drawer(history_path(cfg), cfg.pricing.currency, cfg.graph.max_nodes),
                   title=f"Session {x.name}",
                   subtitle=f"{len(x.turns)} question(s) · started {x.created[:16].replace('T', ' ')} UTC")
        err.print(f"[green]Wrote[/] {out}")
        return
    console.print(f"[bold {BRAND}]Session {x.name}[/] [dim]· {len(x.turns)} question(s) · started "
                  f"{x.created[:16].replace('T', ' ')} UTC[/]")
    for i, t in enumerate(x.turns, 1):
        console.rule(f"[bold]{i}. {t['question'][:120]}[/]", align="left", style="dim")
        console.print(Markdown(t["answer"]))
        console.print(f"[dim]{t['at'][:16].replace('T', ' ')} UTC · {t.get('model', '')} · "
                      f"{t.get('tool_calls', 0)} tool call(s){' · live AWS' if t.get('live') else ''}[/]")


@session_app.command("delete")
def session_delete(name: Annotated[str, typer.Argument(help="Session name.")], config: ConfigOpt = None) -> None:
    """Delete one session."""
    try:
        _sessions(config).delete(name)
    except SessionError as e:
        raise _fail(e) from e
    console.print(f"Deleted session {name}.")


@session_app.command("clear")
def session_clear(config: ConfigOpt = None,
                  yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False) -> None:
    """Delete every session."""
    s = _sessions(config)
    n = len(s.list())
    if n and not yes and not typer.confirm(f"Delete all {n} session(s)?"):
        raise typer.Exit(1)
    console.print(f"Deleted {s.clear()} session(s).")


def register(reg: Registry) -> None:
    reg.add_typer(session_app, "session")
