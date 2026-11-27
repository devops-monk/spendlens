"""Named `spendlens ask` sessions: follow-up questions across separate commands remember the conversation.

A session is a small JSON file (sessions/<id>.json next to the history DB) holding each question, its final answer
and a shortened copy of the data the tools returned for it. Every `spendlens ask` conversation is saved as one
(llm.sessions_auto; ids like s-20261127-141502), and named ones with --session NAME. When a session continues
(`spendlens session open ID`, `ask -S ID`, `ask -C`), the recent exchanges are replayed to the model as plain
user/assistant turns, each answer followed by the data behind it, so follow-ups can reuse it without querying again.
Works with every provider.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONTEXT_TURNS = 20  # exchanges replayed to the model (llm.session_turns)
ANSWER_CHARS = 6000  # per replayed answer
DATA_CHARS = 6000  # tool data replayed per answer (llm.session_data_chars)
AUTO_RE = re.compile(r"^s-\d{8}-\d{6}(-\d+)?$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
CURRENT = ".current"


class SessionError(ValueError):
    pass


def sessions_dir(history_db: Path) -> Path:
    return history_db.parent / "sessions"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def check_name(name: str) -> str:
    if not NAME_RE.match(name):
        raise SessionError(f"Session names use letters, digits, '.', '_' and '-' (max 64): {name!r}")
    return name


@dataclass
class Session:
    path: Path
    name: str
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    turns: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Session:
        d = json.loads(path.read_text())
        return cls(path, d["name"], d.get("created", _now()), d.get("updated", _now()), d.get("turns", []))

    def save(self) -> None:
        from ..privacy import private

        self.path.parent.mkdir(parents=True, exist_ok=True)
        private(self.path.parent, folder=True)  # sessions hold answers and tool data: your user only
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"name": self.name, "created": self.created, "updated": self.updated,
                                   "turns": self.turns}, indent=1, default=str))
        private(tmp)
        tmp.replace(self.path)  # atomic: a crash never leaves half a session

    def context(self, turns: int = CONTEXT_TURNS, data_chars: int = DATA_CHARS) -> list[tuple[str, str]]:
        """The recent exchanges to replay to the model: (question, answer + the data behind it)."""
        out = []
        for t in self.turns[-turns:]:
            a = t["answer"]
            a = a if len(a) <= ANSWER_CHARS else a[:ANSWER_CHARS] + "\n[answer shortened]"
            out.append((t["question"], a + _data_block(t.get("data") or [], data_chars)))
        return out

    def add(self, question: str, answer: str, *, model: str, live: bool, source: str, tool_calls: int,
            steps: list[dict[str, Any]] | None = None, data: list[dict[str, Any]] | None = None) -> None:
        self.turns.append({"at": _now(), "question": question, "answer": answer, "model": model, "live": live,
                           "source": source, "tool_calls": tool_calls, "steps": steps or [], "data": data or []})
        self.updated = _now()
        self.save()


def _data_block(data: list[dict[str, Any]], chars: int) -> str:
    """The tool results behind an earlier answer, shortened to `chars` in all."""
    data = [d for d in data if d.get("ok") and d.get("result")]
    if not data or chars <= 0:
        return ""
    each = max(300, chars // len(data))
    lines = []
    for d in data:
        args = json.dumps(d.get("arguments") or {}, default=str)[:600]
        res = str(d["result"])
        lines.append(f"- {d['tool']} {args} -> {res[:each]}{' [shortened]' if len(res) > each else ''}")
    text = "\n".join(lines)[:chars + 500]
    return ("\n\n[Data behind this answer: the tools spendlens ran for it, results shortened. Reuse it for "
            "follow-ups; query again for anything it does not show, or when fresher data matters.]\n" + text)


def step_data(steps: list[Any], excerpts: list[str]) -> list[dict[str, Any]]:
    """Pair each audited tool call with the start of what it returned."""
    return [{"tool": s.tool, "arguments": s.arguments, "ok": s.ok, "rows": s.rows, "result": r}
            for s, r in zip(steps, excerpts, strict=False)]


class Sessions:
    def __init__(self, root: Path):
        self.root = root
        self._issued: set[str] = set()  # ids handed out but not saved yet

    def path(self, name: str) -> Path:
        return self.root / f"{check_name(name)}.json"

    def current(self) -> str | None:
        p = self.root / CURRENT
        name = p.read_text().strip() if p.exists() else ""
        return name if name and NAME_RE.match(name) and self.path(name).exists() else None

    def set_current(self, name: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / CURRENT).write_text(name)

    def open(self, name: str | None, *, new: bool = False, resume: bool = False) -> Session:
        """--session NAME (continue or create), --new (fresh; NAME optional), --continue (the last session used)."""
        if resume and not name:
            name = self.current()
            if not name:
                raise SessionError("No session to continue yet: start one with --new or --session NAME.")
        if not name:
            name = self.new_id()
        path = self.path(name)
        s = Session(path, name) if new or not path.exists() else Session.load(path)
        if new:
            s.save()
        self.set_current(name)
        return s

    def new_id(self) -> str:
        base = datetime.now(UTC).strftime("s-%Y%m%d-%H%M%S")
        name, i = base, 2
        while self.path(name).exists() or name in self._issued:
            name, i = f"{base}-{i}", i + 1
        self._issued.add(name)
        return name

    def auto(self) -> Session:
        """A new session for one conversation, saved only once it has an answer."""
        name = self.new_id()
        return Session(self.path(name), name)

    def prune(self, keep: int) -> int:
        """Delete automatic sessions beyond the newest `keep` (by last use); named sessions are never pruned."""
        old = [s for s in self.list() if AUTO_RE.match(s.name)][keep:]
        for s in old:
            s.path.unlink(missing_ok=True)
        cur = self.root / CURRENT
        if cur.exists() and cur.read_text().strip() in {s.name for s in old}:
            cur.unlink()
        return len(old)

    def list(self) -> list[Session]:
        if not self.root.exists():
            return []
        out = []
        for p in self.root.glob("*.json"):
            try:
                out.append(Session.load(p))
            except (ValueError, KeyError, OSError):
                continue
        return sorted(out, key=lambda s: s.updated, reverse=True)

    def get(self, name: str) -> Session:
        p = self.path(name)
        if not p.exists():
            raise SessionError(f"No session named {name!r}. See: spendlens session list")
        return Session.load(p)

    def delete(self, name: str) -> None:
        self.get(name).path.unlink()
        if (self.root / CURRENT).exists() and (self.root / CURRENT).read_text().strip() == name:
            (self.root / CURRENT).unlink()

    def clear(self) -> int:
        items = self.list()
        for s in items:
            s.path.unlink()
        (self.root / CURRENT).unlink(missing_ok=True)
        return len(items)
