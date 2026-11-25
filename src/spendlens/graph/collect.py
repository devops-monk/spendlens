"""Collect the graph from Steampipe: one read-only query per resource type, edges resolved at the end."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Config
from .model import Graph, Node
from .sources import SOURCES, Ref, Source, resolve

_DENIED = ("accessdenied", "not authorized", "unauthorizedoperation", "access denied")


class LoginExpired(RuntimeError):
    """Every source would fail the same way: stop and say so once."""


def explain(source: Source, error: str) -> str:
    e = error.lower()
    if any(k in e for k in _DENIED):
        return f"{source.what}: no permission ({error.splitlines()[0][:160]})"
    if "does not exist" in e and source.table in e:
        return f"{source.what}: table {source.table} is not in the installed AWS plugin"
    return f"{source.what}: {error.splitlines()[0][:200]}"


def collect(cfg: Config, *, client: Any = None, sources: tuple[Source, ...] = SOURCES,
            progress: Callable[[str], None] | None = None) -> tuple[Graph, dict[str, Any]]:
    """Run every source. A source that fails (no permission, table missing) is skipped with a note."""
    from ..steampipe import SteampipeClient, SteampipeError

    own = client is None
    c = client or SteampipeClient(cfg.steampipe)
    nodes: list[Node] = []
    refs: list[Ref] = []
    counts: dict[str, int] = {}
    skipped: dict[str, str] = {}
    try:
        for i, s in enumerate(sources, 1):
            if progress:
                progress(f"{s.what} ({i}/{len(sources)})")
            try:
                rows = c.query(s.sql)
            except SteampipeError as e:
                from ..doctor import credential_fix

                fix = credential_fix(str(e), cfg.aws.profile, "")
                if fix:  # an expired or missing login: no point trying the other 23 sources
                    raise LoginExpired(fix) from e
                skipped[s.name] = explain(s, str(e))
                continue
            n0 = len(nodes)
            for r in rows:
                try:
                    ns, rs = s.build(r, cfg.pricing)
                except (KeyError, TypeError, ValueError) as e:  # one odd row never loses the rest
                    skipped.setdefault(s.name, f"{s.what}: skipped a row ({type(e).__name__}: {e})")
                    continue
                nodes += ns
                refs += rs
            counts[s.name] = len(nodes) - n0
    finally:
        if own:
            c.close()
    g = Graph(nodes)
    for e in resolve(list(g.nodes.values()), refs):
        g.add_edge(e)
    return g, {"counts": counts, "skipped": skipped}
