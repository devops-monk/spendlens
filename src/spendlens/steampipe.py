"""Steampipe service connection + query helper.

Steampipe's aws plugin uses the default AWS credential chain, so whatever role
or profile is assumed in the terminal (AWS_PROFILE, env vars, SSO) is what
spendlens queries with.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Self
from urllib.parse import quote

from .config import SteampipeConfig


class SteampipeError(RuntimeError):
    pass


def discover_password() -> str | None:
    """Ask the local steampipe service for its password (`steampipe service status --show-password`)."""
    exe = shutil.which("steampipe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "service", "status", "--show-password"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Password:\s*(\S+)", out)
    return m.group(1) if m else None


def build_dsn(cfg: SteampipeConfig, password: str | None = None) -> str:
    if cfg.dsn:
        return cfg.dsn
    pw = password or os.environ.get(cfg.password_env) or discover_password()
    auth = quote(cfg.user, safe="") + (f":{quote(pw, safe='')}" if pw else "")
    return f"postgresql://{auth}@{cfg.host}:{cfg.port}/{cfg.database}"


class SteampipeClient:
    def __init__(self, cfg: SteampipeConfig, *, read_only: bool = True, timeout_s: int = 300):
        self.cfg = cfg
        self.read_only = read_only
        self.timeout_s = timeout_s
        self._conn: Any = None

    def connect(self) -> Any:
        if self._conn is None:
            import psycopg
            from psycopg.rows import dict_row

            try:
                self._conn = psycopg.connect(build_dsn(self.cfg), row_factory=dict_row, connect_timeout=10)
            except psycopg.OperationalError as e:
                raise SteampipeError(
                    f"Cannot connect to Steampipe at {self.cfg.host}:{self.cfg.port}. "
                    "Is the service running? Try `steampipe service start`.\n"
                    f"Details: {e}"
                ) from e
            self._conn.autocommit = True
            with self._conn.cursor() as cur:
                cur.execute(f"set statement_timeout = {int(self.timeout_s) * 1000}")
                if self.read_only:
                    cur.execute("set default_transaction_read_only = on")
        return self._conn

    def query(self, sql: str, params: Any = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        import psycopg

        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None:
                    return []
                rows = cur.fetchmany(limit) if limit else cur.fetchall()
                return [dict(r) for r in rows]
        except psycopg.Error as e:
            raise SteampipeError(str(e).strip()) from e

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@contextmanager
def client(cfg: SteampipeConfig, **kw: Any) -> Iterator[SteampipeClient]:
    c = SteampipeClient(cfg, **kw)
    try:
        yield c
    finally:
        c.close()
