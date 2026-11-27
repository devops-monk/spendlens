"""`spendlens session`: the saved `spendlens ask` conversations (list, show, open, delete, clear).

Every ask conversation is saved (llm.sessions_auto). `session open ID` shows the earlier chat and continues it: the
assistant gets the earlier questions, answers and the data behind them, so it can answer follow-ups."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markdown import Markdown
from rich.table import Table

from ..ai.session import SessionError, Sessions, sessions_dir
from ..common import BRAND, ConfigOpt, SetOpt, console, err, history_path, load
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
        console.print('[dim]No sessions yet. Every question starts one: spendlens ask "your question"[/]')
        return
    t = Table(header_style=f"bold {BRAND}", title=f"Sessions · {s.root}")
    for c in ("", "id", "questions", "last used (UTC)", "first question", "last question"):
        t.add_column(c, overflow="fold", no_wrap=c in ("id", "last used (UTC)"))
    for x in items:
        first = x.turns[0]["question"] if x.turns else ""
        last = x.turns[-1]["question"] if len(x.turns) > 1 else ""
        t.add_row("*" if x.name == cur else "", x.name, str(len(x.turns)), x.updated[:16].replace("T", " "),
                  first[:60] + ("…" if len(first) > 60 else ""), last[:60] + ("…" if len(last) > 60 else ""))
    console.print(t)
    console.print("[dim]Open one with its earlier chat: spendlens session open <id>   ·   "
                  "one question: spendlens session open <id> \"...\"[/]")


def _transcript(x, last: int | None = None) -> None:
    turns = x.turns[-last:] if last else x.turns
    console.print(f"[bold {BRAND}]Session {x.name}[/] [dim]· {len(x.turns)} question(s) · started "
                  f"{x.created[:16].replace('T', ' ')} UTC[/]")
    if last and len(x.turns) > last:
        console.print(f"[dim]… {len(x.turns) - last} earlier question(s) not shown (they are still in the "
                      "assistant's context, up to llm.session_turns)[/]")
    start = len(x.turns) - len(turns)
    for i, t in enumerate(turns, start + 1):
        console.rule(f"[bold]{i}. {t['question'][:120]}[/]", align="left", style="dim")
        console.print(Markdown(t["answer"]))
        data = [d for d in t.get("data") or [] if d.get("ok")]
        used = ", ".join(sorted({d["tool"] for d in data}))
        console.print(f"[dim]{t['at'][:16].replace('T', ' ')} UTC · {t.get('model', '')} · "
                      f"{t.get('tool_calls', 0)} tool call(s){' · live AWS' if t.get('live') else ''}"
                      f"{' · data kept: ' + used if used else ''}[/]")


@session_app.command("open")
def session_open(
    name: Annotated[str | None, typer.Argument(help="Session id (see `spendlens session list`; default: the last "
                                                    "one used).")] = None,
    question: Annotated[str | None, typer.Argument(help="Ask this follow-up, then stop (default: keep asking).")] = None,
    last: Annotated[int | None, typer.Option("--last", help="Show only the last N earlier questions.")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Don't show the earlier chat.")] = False,
    show_tools: Annotated[bool, typer.Option("--show-tools", help="Show every tool call and SQL used.")] = False,
    live: Annotated[bool | None, typer.Option("--live/--no-live", help="Allow read-only live AWS queries "
                                                                     "(default llm.live).")] = None,
    config: ConfigOpt = None,
    sets: SetOpt = None,
) -> None:
    """Open a saved session: show its earlier chat and continue it, with the earlier questions, answers and data in
    the assistant's context.

    Examples:
      spendlens session open s-20261127-141502
      spendlens session open s-20261127-141502 "And which of those are in eu-west-1?"
      spendlens session open            # the last session used
    """
    from .ask import Source, ask

    s = _sessions(config)
    try:
        x = s.get(name or s.current() or "")
    except SessionError as e:
        raise _fail(SessionError("No session yet. See: spendlens session list") if not name else e) from e
    if not quiet:
        _transcript(x, last)
        console.rule(style="dim")
    src = (x.turns[-1].get("source") if x.turns else None) or Source.last.value
    ask(question=question, interactive=question is None, session=x.name, show_tools=show_tools, live=live,
        source=Source(src) if src in {m.value for m in Source} else Source.last, config=config, sets=sets)


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
    _transcript(x)


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
