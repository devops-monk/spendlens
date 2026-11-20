"""The tool loop: question -> (tool calls -> results)* -> answer. One Agent = one conversation."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any

from .provider import Provider, Turn
from .tools import WAIT_TOOLS, AuditEntry, Toolbox, ToolResult

MAX_WAITS = 60


@dataclass
class Answer:
    text: str
    model: str
    steps: list[AuditEntry]
    refused: bool = False
    hit_step_limit: bool = False
    notes: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class Agent:
    def __init__(self, provider: Provider, toolbox: Toolbox, system: str, max_steps: int):
        self.provider, self.toolbox, self.max_steps = provider, toolbox, max_steps
        self.waiting: Any = nullcontext  # () -> context manager shown while the model works (e.g. a spinner)
        provider.start(system, toolbox.specs())

    def _wait(self) -> AbstractContextManager[Any]:
        return self.waiting()

    def ask(self, question: str) -> Answer:
        audit_start = len(self.toolbox.audit)
        with self._wait():
            turn = self.provider.send_user(question)
        totals = [turn]
        steps = 0
        waits = 0
        while turn.calls and not turn.refused:
            if all(c.name in WAIT_TOOLS for c in turn.calls) and waits < MAX_WAITS:
                waits += 1  # waiting for a running live query is bounded by llm.query_timeout_s, not max_steps
            else:
                if steps >= self.max_steps:
                    return self._final(turn, totals, audit_start)
                steps += 1
            results = []
            for call in turn.calls:
                if call.arguments is None:
                    results.append(ToolResult(call.id, f"Error: arguments were not valid JSON: {call.raw_arguments[:200]}",
                                              True))
                    continue
                results.append(self.toolbox.execute(call.id, call.name, call.arguments))
            with self._wait():
                turn = self.provider.send_tool_results(results)
            totals.append(turn)
        return self._answer(turn, totals, audit_start)

    def _final(self, turn: Turn, totals: list[Turn], audit_start: int) -> Answer:
        """Out of tool steps: decline the pending calls and ask for the best answer from what was found so far."""
        msg = (f"Not run: the tool-step limit ({self.max_steps}) is reached. Do not call any more tools. Answer now "
               "from the tool results you already have, and say briefly what you could not check.")
        try:
            with self._wait():
                last = self.provider.send_tool_results([ToolResult(c.id, msg, True) for c in turn.calls], final=True)
        except Exception:  # noqa: BLE001 - keep the original outcome if the provider rejects the final request
            return self._answer(turn, totals, audit_start, hit_limit=True)
        totals.append(last)
        if last.calls or not last.text.strip() or last.refused:
            return self._answer(last, totals, audit_start, hit_limit=True)
        ans = self._answer(last, totals, audit_start)
        ans.hit_step_limit = True
        ans.notes.append(f"answered at the tool-step limit ({self.max_steps}, llm.max_steps); it may be incomplete. "
                         "Raise it with --max-steps")
        return ans

    def _answer(self, turn: Turn, totals: list[Turn], audit_start: int, hit_limit: bool = False) -> Answer:
        notes = [n for t in totals for n in t.notes]
        text = turn.text.strip()
        if hit_limit:
            notes.append(f"stopped after {self.max_steps} tool steps (llm.max_steps); ask a narrower question")
            text = text or "I could not finish within the tool-step limit. Try a narrower question."
        return Answer(text=text or "(no answer)", model=turn.model, steps=self.toolbox.audit[audit_start:],
                      refused=turn.refused, hit_step_limit=hit_limit, notes=notes,
                      input_tokens=sum(t.input_tokens for t in totals),
                      output_tokens=sum(t.output_tokens for t in totals),
                      cache_read_tokens=sum(t.cache_read_tokens for t in totals))
