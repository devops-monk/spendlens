"""Named `spendlens ask` sessions: follow-up questions across separate commands remember the conversation.

A session is a small JSON file (sessions/<name>.json next to the history DB) holding each question and its final
answer. On the next question the recent exchanges are replayed to the model as plain user/assistant turns, so it
keeps the context without re-sending (or paying for) the earlier tool results. Works with every provider.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONTEXT_TURNS = 12  # exchanges replayed to the model
ANSWER_CHARS = 4000  # per replayed answer
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"name": self.name, "created": self.created, "updated": self.updated,
                                   "turns": self.turns}, indent=1, default=str))
        tmp.replace(self.path)  # atomic: a crash never leaves half a session

    def context(self) -> list[tuple[str, str]]:
        """The recent exchanges to replay to the model (question, answer)."""
        out = []
        for t in self.turns[-CONTEXT_TURNS:]:
            a = t["answer"]
            out.append((t["question"], a if len(a) <= ANSWER_CHARS else a[:ANSWER_CHARS] + "\n[answer shortened]"))
        return out

    def add(self, question: str, answer: str, *, model: str, live: bool, source: str, tool_calls: int,
            steps: list[dict[str, Any]] | None = None) -> None:
        self.turns.append({"at": _now(), "question": question, "answer": answer, "model": model, "live": live,
                           "source": source, "tool_calls": tool_calls, "steps": steps or []})
        self.updated = _now()
        self.save()


class Sessions:
    def __init__(self, root: Path):
        self.root = root

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
            name = datetime.now(UTC).strftime("s-%Y%m%d-%H%M%S")
        path = self.path(name)
        s = Session(path, name) if new or not path.exists() else Session.load(path)
        if new:
            s.save()
        self.set_current(name)
        return s

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
