"""What leaves your machine, and what stays on it.

Redactor: before tool results go to the AI provider, account ids, email addresses and IP addresses can be replaced
by stable placeholders (ACCOUNT_1, EMAIL_1, IP_1; llm.redact). The model works with the placeholders, its tool
arguments are mapped back before anything runs, and the final answer is mapped back before you see it, so answers
read normally while the provider never receives the real values.

private(): spendlens's local stores (history, AI cache, sessions) are readable by your user only (0600 files,
0700 folders), because they hold query results, CloudTrail rows and files you attached.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

KINDS = ("account_ids", "emails", "ips")
PATTERNS = {
    "emails": (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "EMAIL"),
    "ips": (re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])"), "IP"),
    "account_ids": (re.compile(r"(?<!\d)\d{12}(?!\d)"), "ACCOUNT"),
}
TOKEN = re.compile(r"\b(?:ACCOUNT|EMAIL|IP)_\d+\b")


class Redactor:
    """Stable placeholders for sensitive values, for one conversation."""

    def __init__(self, kinds: list[str] | tuple[str, ...] = ()) -> None:
        self.kinds = [k for k in ("emails", "ips", "account_ids") if k in kinds]  # emails first: they hold digits
        self.forward: dict[str, str] = {}
        self.back: dict[str, str] = {}

    @property
    def on(self) -> bool:
        return bool(self.kinds)

    def _token(self, value: str, prefix: str) -> str:
        if value not in self.forward:
            n = sum(1 for t in self.back if t.startswith(prefix + "_")) + 1
            tok = f"{prefix}_{n}"
            self.forward[value], self.back[tok] = tok, value
        return self.forward[value]

    def redact(self, text: str) -> str:
        if not self.on or not text:
            return text
        for k in self.kinds:
            rx, prefix = PATTERNS[k]
            text = rx.sub(lambda m, p=prefix: self._token(m.group(0), p), text)
        return text

    def restore(self, value: Any) -> Any:
        """Placeholders back to the real values: in a string, or every string inside a dict / list."""
        if not self.back:
            return value
        if isinstance(value, str):
            return TOKEN.sub(lambda m: self.back.get(m.group(0), m.group(0)), value)
        if isinstance(value, dict):
            return {k: self.restore(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.restore(v) for v in value]
        return value


def private(path: Path | str, *, folder: bool = False) -> None:
    """Owner-only permissions (0600 files, 0700 folders). Best-effort: never fails a command."""
    try:
        os.chmod(path, 0o700 if folder else 0o600)
    except OSError:
        pass


def leaves_account(provider_label: str) -> bool:
    """Whether the AI provider is outside your AWS account (everything but Bedrock)."""
    return not provider_label.lower().startswith("bedrock")
