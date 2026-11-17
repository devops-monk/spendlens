"""`spendlens ask`: ask the FinOps assistant about your AWS spend, in plain English.

Read-only. Answers from the saved history first (offline, free); live AWS via Steampipe only with --live.
Any provider: Claude via the Anthropic API or Amazon Bedrock (official Anthropic SDK), OpenAI, Azure OpenAI or any
OpenAI-compatible server (official OpenAI SDK) -- set llm.* in finops.yaml.
"""

from __future__ import annotations

import json
import time
from typing import Annotated

import typer
from rich.markdown import Markdown
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


def _print(ans: Answer, show_tools: bool) -> None:
    console.print(Markdown(ans.text))
    if show_tools and ans.steps:
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
    live: Annotated[bool, typer.Option("--live", help="Also allow read-only live AWS queries via Steampipe.")] = False,
    show_tools: Annotated[bool, typer.Option("--show-tools", help="Show every tool call and SQL used.")] = False,
    max_steps: Annotated[int | None, typer.Option("--max-steps", help="Tool-call limit per question (llm.max_steps).")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable answer + audit trail.")] = False,
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
    """
    if source is Source.live:
        raise typer.BadParameter("Use --live to allow live AWS queries; --source picks the history (last or demo).",
                                 param_hint="--source")
    if not question and not interactive:
        raise typer.BadParameter("Ask a question, or use -i for an interactive session.", param_hint="QUESTION")
    cfg = load(config, profile, sets)
    db = demo_db_path() if source is Source.demo else history_path(cfg)
    from ..ai.cache import AICache

    cache = AICache.for_config(cfg, history_path(cfg))
    toolbox = Toolbox(cfg, db, live=live, extra=_plugin_tools(), fresh=fresh, cache=cache)
    try:
        provider = build_provider(cfg)
    except AIError as e:
        err.print(f"[red]AI setup:[/] {e}")
        raise typer.Exit(2) from e
    agent = Agent(provider, toolbox, system_prompt(cfg, live), max_steps or cfg.llm.max_steps)
    err.print(f"[dim]spendlens ask · {provider.label} · {cfg.llm.model} · history: {source.value}"
              f"{' + live AWS' if live else ''}[/]")
    try:
        questions = [question] if question else []
        first = True  # only a conversation's first question is standalone, so only it is cached
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
            key = _answer_key(cfg, provider.label, source, live, db, q) if first else None
            first = False
            if key and not fresh and (hit := cache.get("answer", key)):
                _print_cached(hit, q, json_out)
                continue
            ttls_before = len(toolbox.live_ttls)
            try:
                ans = agent.ask(q)
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
                _print(ans, show_tools)
            if key and not (ans.refused or ans.hit_step_limit) and ans.text.strip():
                live_left = toolbox.live_ttls[ttls_before:]
                ttl = min([cfg.llm.cache.answer_ttl_s, *live_left]) if live_left else cfg.llm.cache.answer_ttl_s
                cache.put("answer", key, {"answer": ans.text, "model": ans.model, "notes": ans.notes,
                                          "steps": [s.__dict__ for s in ans.steps]}, int(ttl), label=q)
            if ans.refused and not interactive:
                raise typer.Exit(4)
    finally:
        toolbox.close()
        cache.close()


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
