"""`spendlens ask`: ask the FinOps assistant about your AWS spend, in plain English.

Read-only. Answers from the saved history first (offline, free); live AWS via Steampipe only with --live.
Any provider: Claude via the Anthropic API or Amazon Bedrock (official Anthropic SDK), OpenAI, Azure OpenAI or any
OpenAI-compatible server (official OpenAI SDK) -- set llm.* in finops.yaml.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markdown import Markdown
from rich.padding import Padding
from rich.syntax import Syntax
from rich.table import Table

from ..ai.agent import Agent, Answer
from ..ai.prompts import system_prompt
from ..ai.provider import AIError, build_provider
from ..ai.tools import Toolbox, ToolSpec
from ..common import BRAND, ConfigOpt, ProfileOpt, SetOpt, Source, console, err, history_path, load
from ..history import demo_db_path
from ..plugins import Registry, registry


def _plugin_tools() -> list[ToolSpec]:
    return [ToolSpec(t.name, t.description, t.parameters, lambda args, _h=t.handler: _h(**args))
            for t in registry.ai_tools.values()]


PREVIEW_ROWS = 5


def _cell(v: Any) -> str:
    s = json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v).replace("\x1f", " > ")
    return s if len(s) <= 48 else s[:47] + "…"


def _preview_table(columns: list[str], rows: list[list[Any]], title: str = "") -> Table:
    t = Table(title=title or None, title_justify="left", header_style="dim", box=None, padding=(0, 1))
    for c in columns[:8]:
        t.add_column(str(c), overflow="ellipsis", no_wrap=True)
    for r in rows[:PREVIEW_ROWS]:
        t.add_row(*[_cell(v) for v in r[:8]])
    if len(rows) > PREVIEW_ROWS:
        t.add_row(f"[dim]… {len(rows) - PREVIEW_ROWS} more[/]", *[""] * (min(len(columns), 8) - 1))
    return t


def _preview(content: str) -> list[Any]:
    """A short view of a tool's output: the first rows as a table, or the start of the text."""
    try:
        out = json.loads(content)
    except ValueError:
        return [f"[dim]{content[:300]}{'…' if len(content) > 300 else ''}[/]"]
    if isinstance(out, dict) and isinstance(out.get("queries"), list):  # cloudtrail_events: the SQL it ran, the rows
        parts: list[Any] = []
        for q in out["queries"][:4]:
            parts.append(f"[dim]{q.get('source')}:[/]")
            parts.append(Syntax(str(q.get("sql") or ""), "sql", word_wrap=True, background_color="default"))
        if len(out["queries"]) > 4:
            parts.append(f"[dim]… {len(out['queries']) - 4} more queries[/]")
        rows = out.get("rows") or []
        if rows and isinstance(rows[0], dict):
            cols = ["event_time", "who", "event_name", "event_source", "source_ip", "error_code"]
            parts.append(_preview_table(cols, [[r.get(c) for c in cols] for r in rows]))
        span = f"{out.get('source')} · {out.get('count')} event(s) · from {str(out.get('covered_from'))[:16]}"
        parts.append(f"[dim]{span}{' · partial' if out.get('partial') else ''}[/]")
        parts += [f"[dim]{n}[/]" for n in out.get("notes") or []]
        return parts
    if isinstance(out, dict) and isinstance(out.get("rows"), list) and out.get("columns"):
        return [_preview_table(out["columns"], out["rows"])]
    if isinstance(out, dict) and isinstance(out.get("features"), list):  # history_overview
        return [_preview_table(["feature", "run", "saved", "alarms", "cost rows"],
                               [[f["feature"], f["latest_run_id"], str(f["started_at"])[:16], f["alarms"], f["cost_rows"]]
                                for f in out["features"]])]
    if isinstance(out, dict) and isinstance(out.get("cost"), dict):  # history_cost
        parts: list[Any] = [_preview_table(v["columns"], v["rows"], f"{k} · run #{v['run_id']}")
                            for k, v in out["cost"].items()]
        parts += [_preview_table(v["columns"], v["rows"], f"{k} findings · run #{v['run_id']}")
                  for k, v in out.get("findings", {}).items()]
        if out.get("note"):
            parts.append(f"[dim]{out['note']}[/]")
        return parts
    text = json.dumps(out, default=str)
    return [f"[dim]{text[:300]}{'…' if len(text) > 300 else ''}[/]"]


class LiveSteps:
    """--show-tools: print each tool call as it starts, then its result and the first rows, as they happen."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, kind: str, name: str, args: dict[str, Any], entry: Any = None, content: str | None = None) -> None:
        if kind == "start":
            self.n += 1
            sql = args.get("sql")
            other = {k: v for k, v in args.items() if k != "sql"}
            console.print(f"[bold {BRAND}]▸ {self.n}[/] [bold]{name}[/]"
                          + (f" [dim]{json.dumps(other, default=str)}[/]" if other else ""))
            if sql:
                console.print(Padding(Syntax(sql.strip(), "sql", word_wrap=True, background_color="default"),
                                      (0, 0, 0, 4)))
            return
        if not entry.ok:
            console.print(f"    [red]✗ {entry.error}[/] [dim]· {entry.ms} ms[/]")
            return
        res = f"{entry.rows} row(s)" if entry.rows is not None else "ok"
        console.print(f"    [green]✓[/] {res} [dim]· {entry.ms} ms[/]")
        for part in _preview(content or ""):
            console.print(Padding(part, (0, 0, 0, 4)))


def _print(ans: Answer, show_tools: bool, streamed: bool = False) -> None:
    if streamed and ans.steps:
        console.rule(style="dim")
    console.print(Markdown(ans.text))
    if show_tools and ans.steps and not streamed:
        t = Table(title="Tool calls (audit trail)", header_style=f"bold {BRAND}", show_lines=False)
        for c in ("#", "tool", "arguments", "result"):
            t.add_column(c, overflow="fold")
        for i, s in enumerate(ans.steps, 1):
            args = s.arguments.get("sql") or json.dumps(s.arguments) if s.arguments else ""
            res = (f"{s.rows} row(s)" if s.rows is not None else "ok") if s.ok else f"[red]{s.error}[/]"
            t.add_row(str(i), s.tool, args, f"{res} · {s.ms} ms")
        console.print(t)
    for n in ans.notes:
        err.print(f"[yellow]{n}[/]")
    err.print(f"[dim]{ans.model} · {len(ans.steps)} tool call(s) · {ans.input_tokens:,} in / {ans.output_tokens:,} out"
              f" tokens{f' ({ans.cache_read_tokens:,} cached)' if ans.cache_read_tokens else ''} · read-only; "
              "savings are estimates for human review[/]")


def ask(
    question: Annotated[str | None, typer.Argument(help="Your question (omit with -i for an interactive session).")] = None,
    interactive: Annotated[bool, typer.Option("--interactive", "-i", help="Keep asking follow-up questions.")] = False,
    source: Annotated[Source, typer.Option("--source", help="History to answer from: last (yours) or demo.")] = Source.last,
    live: Annotated[bool | None, typer.Option("--live/--no-live", help="Allow read-only live AWS queries via Steampipe "
                                                                     "(default llm.live: on).")] = None,
    show_tools: Annotated[bool, typer.Option("--show-tools", help="Show every tool call and SQL used.")] = False,
    max_steps: Annotated[int | None, typer.Option("--max-steps", help="Tool-call limit per question (llm.max_steps).")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable answer + audit trail.")] = False,
    session: Annotated[str | None, typer.Option(
        "--session", "-S", help="Continue (or start) a named session: follow-up questions remember the earlier ones, "
                                "across commands. See `spendlens session list`.")] = None,
    new: Annotated[bool, typer.Option("--new", help="Start a new session (with --session NAME: start it over).")] = False,
    resume: Annotated[bool, typer.Option("--continue", "-C", help="Continue the last session used.")] = False,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Also save the answer as a page: .html (styled, "
                                                                 "with the tool calls) or .md.")] = None,
    files: Annotated[list[Path] | None, typer.Option("--file", "-a", help="Attach a file for the assistant to read "
                                                         "(csv, xlsx, json, yaml, pdf, docx, logs, Terraform, ...). "
                                                         "Repeatable. Paths written in the question are attached too.")
                     ] = None,
    out_dir: Annotated[Path | None, typer.Option("--out-dir", help="Where the assistant writes files you ask for "
                                                                   "(default llm.output_dir: ./spendlens-output).")
                       ] = None,
    tailpipe: Annotated[bool | None, typer.Option("--tailpipe/--no-tailpipe", help="Use Tailpipe's collected "
                                                  "CloudTrail logs, flow logs and CUR (default tailpipe.enabled). "
                                                  "--no-tailpipe: Steampipe tables only.")] = None,
    fresh: Annotated[bool, typer.Option("--fresh", help="Ignore cached answers and live results; ask and query "
                                                        "again (results are still cached for next time).")] = False,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
) -> None:
    """Ask the FinOps assistant, e.g. spendlens ask "Why did EC2 cost go up last month?"

    Examples:
      spendlens ask "What are my top 5 savings opportunities?"
      spendlens ask "Which teams own untagged resources?" --show-tools
      spendlens ask --source demo "Summarise the cost changes between September and October"
      spendlens ask --live "Which running instances are not in an allowed instance family?"
      spendlens ask -i
      spendlens ask --live --fresh "How many instances are running right now?"
      spendlens ask --new "Which ECS services are over-provisioned?"     # start a session
      spendlens ask -C "List them by cluster"                            # follow-up in the same session
      spendlens ask -S ecs "Which of those have no autoscaling?"          # a named session
      spendlens ask "Which ECS services should be right-sized?" -o ecs.html   # save the answer as a page
      spendlens ask "Export unattached EBS volumes with their cost to an Excel file"   # writes spendlens-output/
      spendlens ask -a budget.xlsx "Compare this budget with last month's actual cost per service"
      spendlens ask "Which of the instances in ./inventory.csv no longer exist?"
    """
    if source is Source.live:
        raise typer.BadParameter("Use --live to allow live AWS queries; --source picks the history (last or demo).",
                                 param_hint="--source")
    if not question and not interactive:
        raise typer.BadParameter("Ask a question, or use -i for an interactive session.", param_hint="QUESTION")
    cfg = load(config, profile, sets)
    live = cfg.llm.live if live is None else live
    if tailpipe is not None:
        cfg.tailpipe.enabled = tailpipe
    db = demo_db_path() if source is Source.demo else history_path(cfg)
    from ..ai.cache import AICache

    cache = AICache.for_config(cfg, history_path(cfg))
    from ..ai.session import SessionError, Sessions, sessions_dir, step_data

    sessions = Sessions(sessions_dir(history_path(cfg)))
    sess = None
    if session or new or resume:
        try:
            sess = sessions.open(session, new=new, resume=resume)
        except SessionError as e:
            raise typer.BadParameter(str(e), param_hint="--session") from e
    elif cfg.llm.sessions_auto:
        sess = sessions.auto()  # saved once it has an answer: spendlens session list / open
    from ..ai.files import FileError, InputFile, OutputDir

    toolbox = Toolbox(cfg, db, live=live, extra=_plugin_tools(), fresh=fresh, cache=cache,
                      outdir=OutputDir((out_dir or Path(cfg.llm.output_dir)).expanduser()))
    for f in files or []:
        try:
            _attach(toolbox, InputFile.load(f))
        except FileError as e:
            raise typer.BadParameter(str(e), param_hint="--file") from e
    try:
        provider = build_provider(cfg)
    except AIError as e:
        err.print(f"[red]AI setup:[/] {e}")
        raise typer.Exit(2) from e
    from ..privacy import Redactor, leaves_account

    red = toolbox.redactor = Redactor(cfg.llm.redact)
    agent = Agent(provider, toolbox, system_prompt(cfg, live), max_steps or cfg.llm.max_steps)
    if sess is not None and sess.turns:
        agent.preload([(red.redact(q), red.redact(a))
                       for q, a in sess.context(cfg.llm.session_turns, cfg.llm.session_data_chars)])
    if cfg.llm.egress_notice and leaves_account(provider.label) and (live or toolbox.inputs) and not json_out:
        hidden = f"; hidden from it: {', '.join(cfg.llm.redact)}" if cfg.llm.redact else (
            "; llm.redact can hide account ids, emails and IPs")
        err.print(f"[dim]data note: account data the tools read is sent to {provider.label}, outside your AWS "
                  f"account (Bedrock keeps it inside){hidden}. llm.egress_notice: false hides this note.[/]")
    streamed = show_tools and not json_out
    if streamed:  # show every step as it happens, not all at the end
        from ..ui import activity

        toolbox.on_event = LiveSteps()
        agent.waiting = lambda: activity(f"[dim]{cfg.llm.model} is thinking[/]")
    where = (f" · session {sess.name} ({len(sess.turns)} earlier question{'' if len(sess.turns) == 1 else 's'})"
             if sess is not None and (sess.turns or session or new or resume) else "")
    err.print(f"[dim]spendlens ask · {provider.label} · {cfg.llm.model} · history: {source.value}"
              f"{' + live AWS' if live else ''}{where}[/]")
    try:
        questions = [question] if question else []
        first = sess is None or not sess.turns  # only a conversation's first question is standalone (cacheable)
        while True:
            if not questions:
                if not interactive:
                    break
                try:
                    q = console.input(f"[bold {BRAND}]ask›[/] ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if q.lower() in ("", "exit", "quit", ":q"):
                    break
                questions.append(q)
            q = questions.pop(0)
            q = _with_files(toolbox, q)
            key = _answer_key(cfg, provider.label, source, live, db, q) if first and not toolbox.inputs else None
            first = False
            if key and not fresh and (hit := cache.get("answer", key)):
                _print_cached(hit, q, json_out)
                if out:
                    _save(out, q, hit[0]["answer"], hit[0].get("model") or "", live, hit[0].get("steps") or [], db, cfg)
                if sess is not None:
                    sess.add(q, hit[0]["answer"], model=hit[0].get("model") or "", live=live, source=source.value,
                             tool_calls=0)
                continue
            ttls_before = len(toolbox.live_ttls)
            try:
                ans = agent.ask(red.redact(q))
                ans.text = red.restore(ans.text)  # placeholders back to the real values, for you
            except Exception as e:
                err.print(f"[red]{type(e).__name__}:[/] {str(e)[:500]}")
                if not interactive:
                    raise typer.Exit(1) from e
                continue
            if json_out:
                typer.echo(json.dumps({
                    "question": q, "answer": ans.text, "model": ans.model, "refused": ans.refused,
                    "hit_step_limit": ans.hit_step_limit, "notes": ans.notes,
                    "usage": {"input_tokens": ans.input_tokens, "output_tokens": ans.output_tokens,
                              "cache_read_tokens": ans.cache_read_tokens},
                    "steps": [s.__dict__ for s in ans.steps],
                }, default=str, indent=2))
            else:
                _print(ans, show_tools, streamed)
            wrote = [s for s in ans.steps if s.tool in ("write_file", "export_query") and s.ok]
            if wrote and not json_out:
                for p in toolbox.outdir.written[-len(wrote):]:
                    err.print(f"[green]wrote[/] {p}")
            if key and not wrote and not (ans.refused or ans.hit_step_limit) and ans.text.strip():
                live_left = toolbox.live_ttls[ttls_before:]
                ttl = min([cfg.llm.cache.answer_ttl_s, *live_left]) if live_left else cfg.llm.cache.answer_ttl_s
                cache.put("answer", key, {"answer": ans.text, "model": ans.model, "notes": ans.notes,
                                          "steps": [s.__dict__ for s in ans.steps]}, int(ttl), label=q)
            if out and ans.text.strip() and not ans.refused:
                _save(out, q, ans.text, ans.model, live, [s.__dict__ for s in ans.steps], db, cfg)
            if sess is not None and ans.text.strip() and not ans.refused:
                sess.add(q, ans.text, model=ans.model, live=live, source=source.value, tool_calls=len(ans.steps),
                         steps=[s.__dict__ for s in ans.steps],
                         data=step_data(ans.steps, toolbox.excerpts[-len(ans.steps):] if ans.steps else []))
            if ans.refused and not interactive:
                raise typer.Exit(4)
    finally:
        toolbox.close()
        cache.close()
        if sess is not None and sess.turns:
            sessions.set_current(sess.name)
            sessions.prune(cfg.llm.sessions_keep)
            err.print(f"[dim]session {sess.name} · {len(sess.turns)} question(s) · continue it: "
                      f"spendlens session open {sess.name} (or ask -C \"...\")[/]")


def _attach(toolbox: Toolbox, f: Any) -> None:
    names = {x.name for x in toolbox.inputs}
    if any(x.path.resolve() == f.path.resolve() for x in toolbox.inputs):
        return
    base, i = f.name, 2
    while f.name in names:
        f.name = f"{Path(base).stem}-{i}{Path(base).suffix}"
        i += 1
    toolbox.inputs.append(f)
    err.print(f"[dim]attached {f.path} ({f.summary().get('bytes', 0):,} bytes)[/]")


def _with_files(toolbox: Toolbox, q: str) -> str:
    """Attach files named in the question, and tell the model what is attached (with a short preview)."""
    from ..ai.files import FileError, InputFile, paths_in

    for p in paths_in(q):
        try:
            _attach(toolbox, InputFile.load(p))
        except FileError as e:
            err.print(f"[yellow]not attached:[/] {e}")
    new = [f for f in toolbox.inputs if not getattr(f, "announced", False)]
    if not new:
        return q
    lines = []
    for f in new:
        f.announced = True
        s = f.summary()
        head = f.read(max_rows=5, limit=1500)
        preview = (json.dumps({"columns": head.get("columns"), "first_rows": head.get("rows")}, default=str)
                   if "rows" in head else head.get("text", ""))
        lines.append(f"- {json.dumps(s, default=str)}\n  preview: {preview[:1500]}")
    return (q + "\n\n[Attached files: read them with read_file(name); the preview is only the start.]\n"
            + "\n".join(lines))


def _save(out: Path, q: str, answer: str, model: str, live: bool, steps: list, db: Path, cfg: Any) -> None:
    from datetime import UTC, datetime

    from ..render import answer as page

    page.write(out, [{"question": q, "answer": answer, "model": model, "live": live, "steps": steps,
                      "at": datetime.now(UTC).isoformat(timespec="seconds")}],
               draw=page.graph_drawer(db, cfg.pricing.currency, cfg.graph.max_nodes))
    err.print(f"[green]Wrote[/] {out}")


def _answer_key(cfg, label: str, source: Source, live: bool, db, q: str) -> str:
    from ..ai.cache import digest, history_fingerprint, normalise_question, scope

    return digest("answer", normalise_question(q), label, cfg.llm.model, source.value, live,
                  history_fingerprint(db), scope(cfg) if live else "")


def _print_cached(hit: tuple, q: str, json_out: bool) -> None:
    payload, created = hit
    age = (time.time() - created) / 60
    when = time.strftime("%H:%M UTC", time.gmtime(created))
    if json_out:
        typer.echo(json.dumps({"question": q, "answer": payload["answer"], "model": payload.get("model"),
                               "cached": True, "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created)),
                               "notes": payload.get("notes", []), "steps": payload.get("steps", [])},
                              default=str, indent=2))
        return
    console.print(Markdown(payload["answer"]))
    err.print(f"[dim]cached answer from {when} ({age:.0f} min ago, same question and data) · no tokens used · "
              "--fresh to ask again[/]")


def register(reg: Registry) -> None:
    reg.add_command(ask, "ask")
