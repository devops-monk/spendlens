"""Tools the assistant may call. All read-only; every call is recorded in an audit trail.

History tools (default, offline, free) read the local SQLite history/demo DB. Steampipe tools (live AWS) are only
offered with `spendlens ask --live`. Plugins add tools via registry.add_ai_tool(AITool(...)).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from .sql_guard import UnsafeSQL, check_select

logging.getLogger("sqlglot").setLevel(logging.ERROR)


class ToolError(Exception):
    """A tool failed in a way the model should see (returned as an error result, not raised to the user)."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema: type object, additionalProperties false, required
    handler: Callable[[dict[str, Any]], Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class AuditEntry:
    tool: str
    arguments: dict[str, Any]
    ok: bool
    rows: int | None
    ms: int
    error: str | None = None


def _schema(props: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


# ------------------------------------------------------------------ read-only SQLite (history / demo DB)

_ALLOWED_SQLITE_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
_DENIED_SQLITE_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def _authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
    if action not in _ALLOWED_SQLITE_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_SQLITE_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def open_readonly_sqlite(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ToolError(f"history database not found: {path}. Run spendlens checks first (or use --source demo).")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only = on")
    con.set_authorizer(_authorizer)  # set after the pragma: from here on only reads are possible
    return con


HISTORY_SCHEMA_HINT = """\
Tables (SQLite):
- runs(id, feature, started_at ISO-UTC, account_id, caller_arn, params JSON, meta JSON)
  feature is one of: tags, rightsize, waste, anomalies, forecast, savings_hub, savings_commitments,
  cost_diff, cost_trend, cost_showback, cost_resources.
- findings(run_id, control, title, "check", service, resource_type, resource, account_id, region,
  status ['ok','alarm','info','skip','error'], reason, severity, est_monthly_saving REAL, tags JSON, dimensions JSON)
  dimensions holds per-control facts, e.g. json_extract(dimensions,'$.instance_type'), '$.size_gb', '$.delta'.
- cost_rows(run_id, kind ['p0','p1',...: period index from runs.meta.periods], period, dimension, grp, amount, currency)
The latest run of a feature: select max(id) from runs where feature = '...'.
Always filter findings/cost_rows to one run_id (usually the latest), or you will double count across runs."""


# ------------------------------------------------------------------ toolbox


@dataclass
class Toolbox:
    cfg: Config
    history_db: Path
    live: bool = False
    extra: list[ToolSpec] = field(default_factory=list)
    audit: list[AuditEntry] = field(default_factory=list)
    _sqlite: sqlite3.Connection | None = None
    _steampipe: Any = None

    # ----- plumbing

    def specs(self) -> list[ToolSpec]:
        specs = [
            ToolSpec("history_overview",
                     "Summary of the saved spendlens runs: for each feature its latest run (id, time, account) and "
                     "finding/alarm/saving totals or cost-row counts, plus the history database schema. Call this "
                     "first to see what data exists.", _schema(), lambda a: self.history_overview()),
            ToolSpec("history_query",
                     "Run ONE read-only SQLite SELECT against the spendlens history database (saved findings and "
                     "cost data; no AWS calls, free).\n" + HISTORY_SCHEMA_HINT,
                     _schema({"sql": {"type": "string", "description": "A single SELECT statement."}}, ["sql"]),
                     lambda a: self.history_query(a["sql"])),
        ]
        if self.live:
            specs += [
                ToolSpec("steampipe_tables",
                         "List Steampipe AWS tables (live). Optional prefix filter, e.g. 'aws_ec2', 'aws_cost'.",
                         _schema({"prefix": {"type": "string", "description": "Table name prefix, e.g. aws_rds."}}),
                         lambda a: self.steampipe_tables(a.get("prefix") or "aws_")),
                ToolSpec("steampipe_columns", "Columns and types of one Steampipe AWS table (live).",
                         _schema({"table": {"type": "string"}}, ["table"]), lambda a: self.steampipe_columns(a["table"])),
                ToolSpec("steampipe_query",
                         "Run ONE read-only PostgreSQL SELECT against live AWS through Steampipe, using the AWS role "
                         "assumed in the user's terminal. Prefer history tools; use this only for facts not in "
                         "history. Cost Explorer tables (aws_cost_*) cost $0.01 per API call: always constrain "
                         "period_start/period_end. aws_cost_usage needs granularity, dimension_type_1 and "
                         "dimension_type_2 in the WHERE clause.",
                         _schema({"sql": {"type": "string", "description": "A single SELECT statement."}}, ["sql"]),
                         lambda a: self.steampipe_query(a["sql"])),
            ]
        return specs + self.extra

    def execute(self, call_id: str, name: str, arguments: dict[str, Any] | None) -> ToolResult:
        spec = next((s for s in self.specs() if s.name == name), None)
        args = arguments if isinstance(arguments, dict) else {}
        t0 = time.monotonic()
        if spec is None:
            self.audit.append(AuditEntry(name, args, False, None, 0, "unknown tool"))
            return ToolResult(call_id, f"Error: unknown tool '{name}'.", True)
        missing = [k for k in spec.parameters.get("required", []) if k not in args]
        if missing:
            self.audit.append(AuditEntry(name, args, False, None, 0, f"missing {missing}"))
            return ToolResult(call_id, f"Error: missing required argument(s): {', '.join(missing)}.", True)
        try:
            out = spec.handler(args)
        except (ToolError, UnsafeSQL) as e:
            self.audit.append(AuditEntry(name, args, False, None, int((time.monotonic() - t0) * 1000), str(e)))
            return ToolResult(call_id, f"Error: {e}", True)
        except Exception as e:  # noqa: BLE001 - surface any tool failure to the model, never crash the session
            self.audit.append(AuditEntry(name, args, False, None, int((time.monotonic() - t0) * 1000), repr(e)))
            return ToolResult(call_id, f"Error: {type(e).__name__}: {e}", True)
        rows = out.get("row_count") if isinstance(out, dict) else None
        self.audit.append(AuditEntry(name, args, True, rows, int((time.monotonic() - t0) * 1000)))
        return ToolResult(call_id, self._serialise(out))

    def _serialise(self, out: Any) -> str:
        text = out if isinstance(out, str) else json.dumps(out, default=str, separators=(",", ":"))
        limit = self.cfg.llm.max_result_chars
        if len(text) > limit:
            text = text[:limit] + f'... [truncated at {limit} characters; narrow the query or aggregate]'
        return text

    def close(self) -> None:
        if self._sqlite is not None:
            self._sqlite.close()
        if self._steampipe is not None:
            self._steampipe.close()

    # ----- history

    def _history(self) -> sqlite3.Connection:
        if self._sqlite is None:
            self._sqlite = open_readonly_sqlite(self.history_db)
        return self._sqlite

    def history_query(self, sql: str) -> dict[str, Any]:
        stmt = check_select(sql, "sqlite")
        try:
            cur = self._history().execute(stmt)
        except sqlite3.DatabaseError as e:
            raise ToolError(f"SQLite error: {e}") from e
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(self.cfg.llm.max_rows + 1)
        truncated = len(rows) > self.cfg.llm.max_rows
        rows = rows[: self.cfg.llm.max_rows]
        return {"columns": cols, "rows": [list(r) for r in rows], "row_count": len(rows), "truncated": truncated}

    def history_overview(self) -> dict[str, Any]:
        con = self._history()
        features = []
        for r in con.execute("select feature, max(id) as id from runs group by feature order by feature"):
            run = con.execute("select id, started_at, account_id from runs where id = ?", (r["id"],)).fetchone()
            f = con.execute("select count(*) n, sum(status = 'alarm') alarms, round(sum(case when status = 'alarm' "
                            "then coalesce(est_monthly_saving, 0) else 0 end), 2) saving from findings where run_id = ?",
                            (r["id"],)).fetchone()
            c = con.execute("select count(*) n, round(sum(amount), 2) total from cost_rows where run_id = ?",
                            (r["id"],)).fetchone()
            features.append({"feature": r["feature"], "latest_run_id": run["id"], "started_at": run["started_at"],
                             "account_id": run["account_id"], "findings": f["n"], "alarms": f["alarms"] or 0,
                             "est_monthly_saving_on_alarms": f["saving"] or 0, "cost_rows": c["n"],
                             "cost_rows_total": c["total"]})
        return {"database": str(self.history_db), "features": features, "schema": HISTORY_SCHEMA_HINT,
                "row_count": len(features)}

    # ----- steampipe (live)

    def _sp(self) -> Any:
        if not self.live:
            raise ToolError("live AWS queries are disabled; re-run with --live")
        if self._steampipe is None:
            from ..steampipe import SteampipeClient

            self._steampipe = SteampipeClient(self.cfg.steampipe, read_only=True,
                                              timeout_s=self.cfg.llm.query_timeout_s)
        return self._steampipe

    def _sp_rows(self, sql: str, params: Any = None) -> dict[str, Any]:
        from ..steampipe import SteampipeError

        try:
            rows = self._sp().query(sql, params, limit=self.cfg.llm.max_rows + 1)
        except SteampipeError as e:
            raise ToolError(f"Steampipe error: {e}") from e
        truncated = len(rows) > self.cfg.llm.max_rows
        rows = rows[: self.cfg.llm.max_rows]
        cols = list(rows[0].keys()) if rows else []
        return {"columns": cols, "rows": [[r[c] for c in cols] for r in rows], "row_count": len(rows),
                "truncated": truncated}

    def steampipe_tables(self, prefix: str) -> dict[str, Any]:
        return self._sp_rows("select table_name from information_schema.tables where table_schema = %(s)s "
                             "and table_name like %(p)s order by 1",
                             {"s": self.cfg.steampipe.connection, "p": prefix.replace("%", "") + "%"})

    def steampipe_columns(self, table: str) -> dict[str, Any]:
        return self._sp_rows("select column_name, data_type from information_schema.columns where table_schema = %(s)s "
                             "and table_name = %(t)s order by ordinal_position",
                             {"s": self.cfg.steampipe.connection, "t": table})

    def steampipe_query(self, sql: str) -> dict[str, Any]:
        stmt = check_select(sql, "postgres")
        return self._sp_rows(stmt)  # no params -> psycopg sends the text as-is (a LIKE 'x%' stays intact)
