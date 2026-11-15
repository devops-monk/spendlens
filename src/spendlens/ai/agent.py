"""The tool loop: question -> (tool calls -> results)* -> answer. One Agent = one conversation."""

from __future__ import annotations

from dataclasses import dataclass, field

from .provider import Provider, Turn
from .tools import AuditEntry, Toolbox, ToolResult


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
        provider.start(system, toolbox.specs())

    def ask(self, question: str) -> Answer:
        audit_start = len(self.toolbox.audit)
        turn = self.provider.send_user(question)
        totals = [turn]
        steps = 0
        while turn.calls and not turn.refused:
            if steps >= self.max_steps:
                return self._answer(turn, totals, audit_start, hit_limit=True)
            steps += 1
            results = []
            for call in turn.calls:
                if call.arguments is None:
                    results.append(ToolResult(call.id, f"Error: arguments were not valid JSON: {call.raw_arguments[:200]}",
                                              True))
                    continue
                results.append(self.toolbox.execute(call.id, call.name, call.arguments))
            turn = self.provider.send_tool_results(results)
            totals.append(turn)
        return self._answer(turn, totals, audit_start)

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
