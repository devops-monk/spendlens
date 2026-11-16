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
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote, unquote, urlsplit

from .config import SteampipeConfig


class SteampipeError(RuntimeError):
    pass


# ------------------------------------------------------------------ managed connection (aws.regions / aws.profile)

MANAGED_CONNECTION = "spendlens"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def install_dir(cfg: SteampipeConfig) -> Path:
    return Path(cfg.install_dir or os.environ.get("STEAMPIPE_INSTALL_DIR") or Path.home() / ".steampipe").expanduser()


def managed_spc_path(cfg: SteampipeConfig) -> Path:
    return install_dir(cfg) / "config" / f"{MANAGED_CONNECTION}.spc"


def managed_spc(regions: list[str], profile: str | None) -> str:
    """The Steampipe connection spendlens manages. Values are validated in config.AWSConfig (no quotes possible)."""
    lines = ["# Managed by spendlens from finops.yaml (aws.regions, aws.profile). Edits here are overwritten;",
             "# change the settings in finops.yaml instead, or set steampipe.connection to use your own connection.",
             f'connection "{MANAGED_CONNECTION}" {{', '  plugin  = "aws"',
             "  regions = [" + ", ".join(f'"{r}"' for r in regions) + "]"]
    if profile:
        lines.append(f'  profile = "{profile}"')
    return "\n".join([*lines, "}", ""])


def is_managed(cfg: SteampipeConfig) -> bool:
    """spendlens only manages its own connection, on a local Steampipe it can write config for."""
    return cfg.connection == MANAGED_CONNECTION and not cfg.dsn and cfg.host in _LOCAL_HOSTS


def write_managed_connection(cfg: SteampipeConfig) -> bool:
    """Write <install_dir>/config/spendlens.spc from aws.regions/aws.profile. Returns True if it changed."""
    aws = cfg._aws
    regions = list(aws.regions) if aws is not None else ["eu-west-1"]
    profile = aws.profile if aws is not None else None
    path = managed_spc_path(cfg)
    text = managed_spc(regions, profile)
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".spc.tmp")
    tmp.write_text(text)
    tmp.replace(path)  # atomic: Steampipe's file watcher never sees a half-written file
    return True


def wait_for_connection(conn: Any, name: str, *, since: float | None, timeout_s: float = 90,
                        sleep: Any = None) -> None:
    """Poll steampipe_internal.steampipe_connection until `name` is ready (and, after an edit, reloaded)."""
    import time

    sleep = sleep or time.sleep
    deadline = time.monotonic() + timeout_s
    last = "not loaded yet"
    while True:
        with conn.cursor() as cur:
            cur.execute("select state, error, extract(epoch from connection_mod_time) as mod "
                        "from steampipe_internal.steampipe_connection where name = %s", (name,))
            row = cur.fetchone()
        if row:
            state, error, mod = row["state"], row["error"], row["mod"]
            if state == "error":
                raise SteampipeError(f"Steampipe could not load the '{name}' connection: {error or 'unknown error'}.\n"
                                     f"Check aws.regions / aws.profile in finops.yaml.")
            if state == "ready" and (since is None or (mod or 0) >= since - 2):
                return
            last = state
        if time.monotonic() > deadline:
            raise SteampipeError(
                f"Steampipe has not loaded the '{name}' connection after {int(timeout_s)}s (state: {last}). "
                "Try `steampipe service restart`.")
        sleep(1)


def ensure_connection(cfg: SteampipeConfig, conn: Any) -> None:
    """Make sure the managed connection exists with the configured regions before querying through it."""
    if not is_managed(cfg):
        return
    import time

    changed = write_managed_connection(cfg)
    wait_for_connection(conn, MANAGED_CONNECTION, since=time.time() if changed else None)


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


def connection_params(cfg: SteampipeConfig, password: str | None = None) -> dict[str, Any]:
    """Host/port/db/user/password for tools that need discrete values (Powerpipe connection blocks)."""
    if cfg.dsn:
        u = urlsplit(cfg.dsn)
        return {
            "host": u.hostname or "localhost",
            "port": u.port or 9193,
            "db": (u.path or "/steampipe").lstrip("/") or "steampipe",
            "username": unquote(u.username or "steampipe"),
            "password": unquote(u.password) if u.password else None,
        }
    return {
        "host": cfg.host,
        "port": cfg.port,
        "db": cfg.database,
        "username": cfg.user,
        "password": password or os.environ.get(cfg.password_env) or discover_password(),
    }


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
            ensure_connection(self.cfg, self._conn)
            with self._conn.cursor() as cur:
                cur.execute(f"set statement_timeout = {int(self.timeout_s) * 1000}")
                if self.cfg.connection:
                    from psycopg import sql as _sql

                    cur.execute(_sql.SQL("set search_path to {}, public").format(_sql.Identifier(self.cfg.connection)))
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
