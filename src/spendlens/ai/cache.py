"""Caches for the AI assistant: live query results, whole answers and brief summaries (SQLite, ai-cache.db).

Freshness rules (config llm.cache.*):
  * a live query result is reused only while younger than the TTL of its data class: resources 15 min,
    CloudWatch metrics / CloudTrail / logs 5 min, Cost Explorer 6 h. The key includes the scope (Steampipe
    connection, regions, AWS profile / identity), so another account or region never sees it;
  * an answer is keyed by the question, the model and the data fingerprint (latest saved run per feature): a new
    saved run changes the fingerprint, so an answer never outlives the data it came from. With --live it also
    expires with the shortest-lived query it used;
  * --fresh skips reads (results are still written for next time).
Stored separately from the history DB, which the assistant may only read.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..config import Config
from .catalog import Catalog

SCHEMA = """create table if not exists kv (
  ns text not null, key text not null, created real not null, expires real not null, size integer not null,
  label text, payload text not null, primary key (ns, key));
create index if not exists kv_expires on kv(expires);"""


def digest(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(json.dumps(p, sort_keys=True, default=str) for p in parts).encode()).hexdigest()


def normalise_sql(sql: str) -> str:
    with contextlib.suppress(Exception):  # unparseable for sqlglot: fall back to whitespace normalisation
        import sqlglot

        from .sql_guard import parseable

        if parseable(sql) == sql:  # canonical form only when the text round-trips exactly
            return sqlglot.transpile(sql, read="postgres", write="postgres")[0].lower()
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def normalise_question(q: str) -> str:
    return re.sub(r"[\s?.!]+", " ", q.strip().lower()).strip()


def scope(cfg: Config) -> str:
    """Who and where a live result is valid for (no secrets stored: identity values are hashed)."""
    ident = [os.environ.get(k, "") for k in ("AWS_PROFILE", "AWS_ROLE_ARN", "AWS_ACCESS_KEY_ID")]
    return digest(cfg.steampipe.connection, sorted(cfg.aws.regions or []), cfg.aws.profile or "", ident)


def ttl_for(tables: list[str], cat: Catalog, cfg: Config) -> int:
    """The strictest freshness among the tables a query reads."""
    c = cfg.llm.cache

    def one(name: str) -> int:
        t = cat.get(name)
        return c.cost_ttl_s if t and t.cost_explorer else c.metrics_ttl_s if t and t.time_series else c.inventory_ttl_s

    return min((one(n) for n in tables), default=c.inventory_ttl_s)


class AICache:
    def __init__(self, path: Path, *, enabled: bool = True, max_mb: int = 100):
        self.path, self.enabled, self.max_bytes = path, enabled, max_mb * 1024 * 1024
        self._db: sqlite3.Connection | None = None

    @classmethod
    def for_config(cls, cfg: Config, history_db: Path) -> AICache:
        c = cfg.llm.cache
        path = Path(c.path).expanduser() if c.path else history_db.expanduser().parent / "ai-cache.db"
        return cls(path, enabled=c.enabled, max_mb=c.max_mb)

    def _conn(self) -> sqlite3.Connection | None:
        if not self.enabled:
            return None
        if self._db is None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
                self._db.executescript(SCHEMA)
                self._db.execute("delete from kv where expires < ?", (time.time(),))
                self._db.commit()
            except (sqlite3.Error, OSError):
                self.enabled = False  # a cache must never break the assistant
                return None
        return self._db

    def get(self, ns: str, key: str) -> tuple[Any, float] | None:
        """(payload, created_at) if present and fresh."""
        db = self._conn()
        if db is None:
            return None
        try:
            row = db.execute("select payload, created from kv where ns = ? and key = ? and expires >= ?",
                             (ns, key, time.time())).fetchone()
        except sqlite3.Error:
            return None
        return (json.loads(row[0]), row[1]) if row else None

    def put(self, ns: str, key: str, payload: Any, ttl_s: int, label: str = "") -> None:
        db = self._conn()
        if db is None or ttl_s <= 0:
            return
        text = json.dumps(payload, default=str, separators=(",", ":"))
        now = time.time()
        try:
            db.execute("insert or replace into kv values (?, ?, ?, ?, ?, ?, ?)",
                       (ns, key, now, now + ttl_s, len(text), label[:300], text))
            total = db.execute("select coalesce(sum(size), 0) from kv").fetchone()[0]
            if total > self.max_bytes:  # drop the oldest entries until under the cap
                db.execute("delete from kv where rowid in (select rowid from kv order by created limit "
                           "(select count(*) / 4 + 1 from kv))")
            db.commit()
        except sqlite3.Error:
            pass

    def stats(self) -> list[dict[str, Any]]:
        db = self._conn()
        if db is None:
            return []
        now = time.time()
        rows = db.execute("select ns, count(*), sum(size), min(created), max(expires) from kv where expires >= ? "
                          "group by ns order by ns", (now,)).fetchall()
        return [{"kind": r[0], "entries": r[1], "kib": round((r[2] or 0) / 1024, 1),
                 "oldest_min": round((now - r[3]) / 60, 1), "last_expiry_min": round((r[4] - now) / 60, 1)}
                for r in rows]

    def entries(self, ns: str, limit: int = 20) -> list[dict[str, Any]]:
        db = self._conn()
        if db is None:
            return []
        now = time.time()
        return [{"label": r[0], "age_min": round((now - r[1]) / 60, 1), "expires_in_min": round((r[2] - now) / 60, 1)}
                for r in db.execute("select label, created, expires from kv where ns = ? and expires >= ? "
                                    "order by created desc limit ?", (ns, now, limit))]

    def clear(self, ns: str | None = None) -> int:
        db = self._conn()
        if db is None:
            return 0
        cur = db.execute("delete from kv" + (" where ns = ?" if ns else ""), (ns,) if ns else ())
        db.commit()
        return cur.rowcount

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


def history_fingerprint(history_db: Path) -> str:
    """Changes whenever a run is saved: the latest run id per feature (read-only)."""
    try:
        con = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True)
        try:
            rows = con.execute("select feature, max(id) from runs group by feature order by feature").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return "none"
    return digest(str(history_db.resolve()), rows)
