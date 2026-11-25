"""Tools the assistant may call. All read-only; every call is recorded in an audit trail.

History tools (default, offline, free) read the local SQLite history/demo DB. Live AWS tools are only offered with
`spendlens ask --live`; they work from the AWS table catalog (ai/catalog.py) so the model finds the right table and
real columns instead of guessing, every query is checked before it runs (ai/sql_check.py), slow queries run as
background jobs the model waits for (ai/jobs.py), and fresh results are reused from the cache (ai/cache.py).
Plugins add tools via registry.add_ai_tool(AITool(...)).
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

LIVE_COLUMNS_TTL_S = 86400  # the installed plugin's column list changes only on a plugin upgrade
WAIT_TOOLS = {"aws_query_result"}  # waiting for a running query does not use up llm.max_steps

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
  cost_diff, cost_trend, cost_showback, cost_resources, graph.
- findings(run_id, control, title, "check", service, resource_type, resource, account_id, region,
  status ['ok','alarm','info','skip','error'], reason, severity, est_monthly_saving REAL, tags JSON, dimensions JSON)
  dimensions holds per-control facts, e.g. json_extract(dimensions,'$.instance_type'), '$.size_gb', '$.delta'.
- cost_rows(run_id, kind ['p0','p1',...: period index from runs.meta.periods], period, dimension, grp, amount, currency)
- graph_nodes(run_id, id, type, name, account_id, region, state, created, monthly_cost, cost_basis, tags JSON,
  attrs JSON) and graph_edges(run_id, from_id, to_id, label, kind ['uses','contains','ref']) for feature 'graph':
  how resources connect (labels: mounts, eni, in subnet, routes via, snapshot, backed by, launched from, public ip,
  forwards to, targets, manages, member, runs). Prefer resource_context / cleanup_groups / suggested_owners.
  Feature 'graph_traffic' keeps its data transfer analysis in runs.meta: use traffic_paths.
The latest run of a feature: select max(id) from runs where feature = '...'.
Always filter findings/cost_rows to one run_id (usually the latest), or you will double count across runs."""


# short names people use -> words in Cost Explorer's service names (which are long: "Amazon Relational Database Service")
SERVICE_ALIASES = {
    "rds": ["Relational Database"], "aurora": ["Relational Database"], "ec2": ["Elastic Compute Cloud", "EC2"],
    "ebs": ["EC2 - Other", "Elastic Block Store"], "nat": ["EC2 - Other", "VPC"], "s3": ["Simple Storage Service"],
    "lambda": ["Lambda"], "elb": ["Elastic Load Balancing"], "alb": ["Elastic Load Balancing"],
    "nlb": ["Elastic Load Balancing"], "dynamodb": ["DynamoDB"], "efs": ["Elastic File System"],
    "eks": ["Elastic Container Service for Kubernetes", "Elastic Kubernetes"], "ecs": ["Elastic Container Service"],
    "fargate": ["Elastic Container Service"], "cloudwatch": ["CloudWatch"], "cloudfront": ["CloudFront"],
    "elasticache": ["ElastiCache"], "redshift": ["Redshift"], "opensearch": ["OpenSearch"],
    "kms": ["Key Management Service"], "sqs": ["Simple Queue Service"], "sns": ["Simple Notification Service"],
    "vpc": ["Virtual Private Cloud", "VPC"], "route53": ["Route 53"], "glue": ["Glue"], "athena": ["Athena"],
}
COST_FEATURES = ("cost_diff", "cost_trend", "cost_showback", "cost_breakdown", "forecast", "anomalies")


def _patterns(text: str) -> list[str]:
    t = text.strip()
    key = t.lower().replace("amazon ", "").replace("aws ", "").replace(" ", "")
    return sorted({t, *SERVICE_ALIASES.get(key, [])}, key=len)


# ------------------------------------------------------------------ toolbox


@dataclass
class Toolbox:
    cfg: Config
    history_db: Path
    live: bool = False
    extra: list[ToolSpec] = field(default_factory=list)
    audit: list[AuditEntry] = field(default_factory=list)
    fresh: bool = False  # skip cache reads (still write)
    cache: Any = None  # AICache | None
    live_ttls: list[float] = field(default_factory=list)  # remaining freshness (s) of each live result used
    on_event: Any = None  # callable(kind: "start" | "done", name, args, entry=None, content=None): live display
    inputs: list[Any] = field(default_factory=list)  # files.InputFile the person attached
    outdir: Any = None  # files.OutputDir: where write_file puts files (None: no write_file tool)
    _results: dict[str, Any] = field(default_factory=dict)  # result_id -> (columns, rows) for write_file
    _sqlite: sqlite3.Connection | None = None
    _steampipe: Any = None
    _catalog: Any = None
    _jobs: Any = None

    # ----- plumbing

    def specs(self) -> list[ToolSpec]:
        specs = [
            ToolSpec("history_overview",
                     "Summary of the saved spendlens runs: for each feature its latest run (id, time, account) and "
                     "finding/alarm/saving totals or cost-row counts, plus the history database schema. Call this "
                     "first to see what data exists.", _schema(), lambda a: self.history_overview()),
            ToolSpec("history_cost",
                     "Everything saved about one service's (or tag value's) cost, in one call: for the latest run of "
                     "each cost feature (cost_diff, cost_trend, cost_breakdown, cost_showback, anomalies, forecast) "
                     "its rows by period, plus the latest findings for that service. Use it first for 'why did X "
                     "cost change' questions. Short names work (rds, ec2, s3, ebs, nat, lambda ...).",
                     _schema({"service": {"type": "string",
                                          "description": "Service or group to match, e.g. 'rds' or 'Amazon Relational "
                                                         "Database Service' or a tag value."}}, ["service"]),
                     lambda a: self.history_cost(a["service"])),
            ToolSpec("resource_context",
                     "One AWS resource from the saved resource graph: what it is attached to and what uses it (with "
                     "each neighbour's cost), what the whole group costs, its cleanup group if any, suggested owner "
                     "tags, failing checks on it, what deleting it would affect, and shared costs (a NAT gateway or "
                     "load balancer split across its users). Offline and free. Use it for 'what is X attached to', "
                     "'who owns X', 'can I delete X', 'why does X cost so much'.",
                     _schema({"resource": {"type": "string", "description": "ARN, AWS id (i-..., vol-..., snap-..., "
                                                                            "ami-..., nat-...) or Name tag."},
                              "depth": {"type": "integer", "description": "Links to follow, 1 or 2 (default 1)."}},
                             ["resource"]),
                     lambda a: self.resource_context(a["resource"], a.get("depth") or 1)),
            ToolSpec("cleanup_groups",
                     "Cleanup groups from the saved resource graph: connected resources that can be removed "
                     "together (a stopped instance with its volumes, IP and snapshots; snapshots of deleted volumes; "
                     "unused AMIs; load balancers without targets; unattached volumes and IPs; snapshots of deleted "
                     "databases), each with one total monthly saving, counted once. Waste alarms they include are "
                     "listed, not added. Offline and free.",
                     _schema({"kind": {"type": "string", "description": "Optional: stopped_instance, idle_load_balancer, "
                                                                        "unused_ami, orphan_snapshots, unattached_volume, "
                                                                        "unassociated_eip or rds_orphan_snapshots."},
                              "min_saving": {"type": "number", "description": "Optional minimum saving per month."},
                              "top": {"type": "integer", "description": "Optional: only the largest N (default 15)."}}),
                     lambda a: self.cleanup_groups(a.get("kind"), a.get("min_saving") or 0, a.get("top") or 15)),
            ToolSpec("suggested_owners",
                     "Suggested owner tags for untagged resources, from what they are attached to (a volume takes the "
                     "Team of the instance that mounts it), each with its confidence (high, medium, low) and reason. "
                     "Offline and free. Use it for 'who owns these untagged resources'.",
                     _schema({"resource": {"type": "string", "description": "Optional: one resource (ARN, id or name)."},
                              "key": {"type": "string", "description": "Optional: one tag key, e.g. Team."},
                              "min_confidence": {"type": "string", "description": "Optional: high, medium or low."}}),
                     lambda a: self.suggested_owners(a.get("resource"), a.get("key"), a.get("min_confidence") or "low")),
            ToolSpec("traffic_paths",
                     "Data transfer from the saved VPC flow log analysis (spendlens graph traffic): per resource and "
                     "path (cross_az, nat, internet, endpoint, same_az, vpc_other) the GB per month and monthly cost, "
                     "the totals per path, and gateway-endpoint proposals. Offline and free. Use it for 'why is NAT "
                     "so expensive', 'which services talk across AZs', 'should we add an S3 or DynamoDB endpoint'.",
                     _schema({"path": {"type": "string", "description": "Optional: one path, e.g. nat or cross_az."},
                              "resource": {"type": "string", "description": "Optional: one resource name or id."},
                              "top": {"type": "integer", "description": "Optional: rows (default 20)."}}),
                     lambda a: self.traffic_paths(a.get("path"), a.get("resource"), a.get("top") or 20)),
            ToolSpec("history_query",
                     "Run ONE read-only SQLite SELECT against the spendlens history database (saved findings and "
                     "cost data; no AWS calls, free).\n" + HISTORY_SCHEMA_HINT,
                     _schema({"sql": {"type": "string", "description": "A single SELECT statement."}}, ["sql"]),
                     lambda a: self.history_query(a["sql"])),
        ]
        if self.live or self._tailpipe_available():  # CloudTrail needs one of the two
            specs.append(ToolSpec(
                "aws_changes",
                "What changed in the AWS account and who changed it: CloudTrail write events, newest first, with counts "
                "by who and by API call. Uses the trail's logs in Tailpipe when collected (any age, data events), else "
                "CloudTrail LookupEvents (last 90 days, management events). Routine calls (role assumptions, KMS, log "
                "delivery) are left out. Use it first for 'what changed', 'who changed X', 'who deleted ...'.",
                _schema({"hours": {"type": "integer", "description": "How far back (default 24; 168 = a week)."},
                         "user": {"type": "string", "description": "Optional: part of a user or role name / ARN."},
                         "resource": {"type": "string", "description": "Optional: a resource id or name."},
                         "service": {"type": "string", "description": "Optional: a service, e.g. ec2, iam, s3."},
                         "event": {"type": "string", "description": "Optional: an API call, e.g. DeleteBucket."}}),
                lambda a: self.aws_changes(a.get("hours") or 24, a.get("user"), a.get("resource"), a.get("service"),
                                           a.get("event"))))
        if self._tailpipe_available():
            specs.append(ToolSpec(
                "tailpipe_query",
                "Data collected by Tailpipe (local; no AWS calls). ONE DuckDB SELECT on aws_cloudtrail_log (CloudTrail: "
                "full history and data events), aws_vpc_flow_log (VPC flow logs) or aws_cost_and_usage_report (the "
                "CUR: billed cost per resource and line item), with a time range: tp_date (partition date; e.g. "
                "tp_date >= current_date - interval 30 day), tp_timestamp, event_time, start_time or "
                "line_item_usage_start_date. Call with describe=<table> first to list its exact columns. CloudTrail "
                "hints: user_identity.arn, event_name, event_source, request_parameters ->> 'bucketName', read_only.",
                _schema({"sql": {"type": "string", "description": "One DuckDB SELECT on one of the three tables."},
                         "describe": {"type": "string", "description": "A table name: return its columns instead."}}),
                lambda a: self.tailpipe_query(a.get("sql") or "", a.get("describe"))))
        if self.live:
            q = {"sql": {"type": "string", "description": "One read-only PostgreSQL SELECT on aws_* tables."}}
            specs += [
                ToolSpec("aws_find_tables",
                         "Find the Steampipe AWS tables that can answer a question (searches ~590 tables by name, "
                         "service, description and columns). Call this first for any live AWS question.",
                         _schema({"question": {"type": "string", "description": "The question or keywords."}},
                                 ["question"]),
                         lambda a: self.aws_find_tables(a["question"])),
                ToolSpec("aws_describe_table",
                         "Columns (type and meaning), REQUIRED filters with valid values, fast filter columns and "
                         "example queries for one AWS table. Call before writing SQL; never guess column names.",
                         _schema({"table": {"type": "string"}}, ["table"]),
                         lambda a: self.aws_describe_table(a["table"])),
                ToolSpec("aws_check_query",
                         "Check a query without running it (no AWS calls): tables, columns, required filters, JSON "
                         "operators, cost and time-range guards. aws_query runs the same check automatically.",
                         _schema(q, ["sql"]), lambda a: self.aws_check_query(a["sql"])),
                ToolSpec("aws_query",
                         "Run ONE read-only SELECT against live AWS through Steampipe with the user's AWS role. The "
                         "query is checked first; a fresh cached result is returned when one exists. If it takes "
                         "longer than a few seconds the result is {status: running, job_id}: call aws_query_result "
                         "to wait for it. Prefer history tools when they can answer.",
                         _schema(q, ["sql"]), lambda a: self.aws_query(a["sql"])),
                ToolSpec("aws_query_result",
                         "Wait for a running aws_query job and return its rows (or that it is still running).",
                         _schema({"job_id": {"type": "string"}}, ["job_id"]),
                         lambda a: self.aws_query_result(a["job_id"])),
            ]
        specs += self._file_specs()
        return specs + self.extra

    # ----- files: read what was attached, write what was asked for

    def _file_specs(self) -> list[ToolSpec]:
        out = []
        if self.inputs:
            names = ", ".join(f.name for f in self.inputs)
            out.append(ToolSpec(
                "read_file",
                f"Read a file the person attached ({names}). Text comes in pages of characters (use next_offset "
                "for more); CSV and spreadsheets come as columns + rows (offset = first row; sheet picks a sheet). "
                "PDF and Word files come as text.",
                _schema({"name": {"type": "string", "description": "The attached file's name."},
                         "offset": {"type": "integer", "description": "Character (text) or row (tables) to start at."},
                         "sheet": {"type": "string", "description": "Spreadsheet sheet name (default: the first)."}},
                        ["name"]),
                lambda a: self.read_file(a["name"], int(a.get("offset") or 0), a.get("sheet"))))
        if self.outdir is not None:
            out.append(ToolSpec(
                "write_file",
                f"Write a file the person asked for into {self.outdir.root} (never overwrites: a numbered name is used "
                "instead). Only when they ask for a file, export, report, script or document. Types by extension: "
                "tables as .csv .tsv .xlsx .json .jsonl .md .html .yaml; documents as .docx (from Markdown content, "
                "plus optional tables) or .md/.html/.txt; code and config as .sql .sh .tf .py .yaml .json .mmd .dot "
                ".svg. For table data from an earlier tool result, pass its result_id (or a list of them: one "
                "sheet each in .xlsx) instead of copying rows; otherwise pass columns + rows. Scripts are written, "
                "never run: say that the person should review them.",
                _schema({"name": {"type": "string", "description": "File name with extension, e.g. idle-volumes.xlsx"},
                         "content": {"type": "string", "description": "Text content (Markdown for .docx)."},
                         "result_id": {"description": "An earlier result's result_id, or a list of them.",
                                       "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                         "columns": {"type": "array", "items": {"type": "string"}},
                         "rows": {"type": "array", "items": {"type": "array"}},
                         "title": {"type": "string", "description": "Sheet / table title (optional)."}},
                        ["name"]),
                lambda a: self.write_file(a)))
        return out

    def read_file(self, name: str, offset: int = 0, sheet: str | None = None) -> dict[str, Any]:
        from .files import FileError

        f = next((f for f in self.inputs if f.name == name), None) or next(
            (f for f in self.inputs if f.name.lower() == str(name).lower() or str(f.path) == name), None)
        if f is None:
            raise ToolError(f"no attached file {name!r}; attached: {', '.join(x.name for x in self.inputs)}")
        try:
            return f.read(offset=max(offset, 0), sheet=sheet, limit=max(self.cfg.llm.max_result_chars - 2000, 1000),
                          max_rows=self.cfg.llm.max_rows)
        except FileError as e:
            raise ToolError(str(e)) from e

    def write_file(self, a: dict[str, Any]) -> dict[str, Any]:
        from .files import FileError

        tables = []
        ids = a.get("result_id")
        ids = [ids] if isinstance(ids, str) else list(ids or [])
        for rid in ids:
            if rid not in self._results:
                raise ToolError(f"unknown result_id {rid!r}; known: {', '.join(self._results) or 'none yet'}")
            title, cols, rows = self._results[rid]
            tables.append(((a.get("title") if len(ids) == 1 else None) or title, cols, rows))
        if a.get("columns") is not None or a.get("rows") is not None:
            cols = [str(c) for c in a.get("columns") or []]
            rows = [r if isinstance(r, list) else [r] for r in a.get("rows") or []]
            if not cols and rows:
                cols = [f"column_{i + 1}" for i in range(max(len(r) for r in rows))]
            tables.append((a.get("title") or "", cols, rows))
        try:
            return self.outdir.write(a["name"], content=a.get("content"), tables=tables or None)
        except FileError as e:
            raise ToolError(str(e)) from e

    def _remember(self, name: str, args: dict[str, Any], out: Any) -> Any:
        """Tabular results get a result_id the model can hand to write_file."""
        if self.outdir is None or not isinstance(out, dict):
            return out
        rows = out.get("rows")
        if not isinstance(rows, list) or not rows:
            return out
        cols = out.get("columns")
        if not cols and isinstance(rows[0], dict):
            cols = list(rows[0])
            rows = [[r.get(c) for c in cols] for r in rows]
        if not cols or not isinstance(rows[0], list):
            return out
        rid = f"r{len(self._results) + 1}"
        self._results[rid] = (name, list(cols), rows)
        return {"result_id": rid, **out}

    def _emit(self, kind: str, name: str, args: dict[str, Any], entry: AuditEntry | None = None,
              content: str | None = None) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, name, args, entry, content)
            except Exception:  # noqa: BLE001, S110 - the display must never break a question
                pass

    def _log(self, call_id: str, entry: AuditEntry, content: str, is_error: bool = False) -> ToolResult:
        self.audit.append(entry)
        self._emit("done", entry.tool, entry.arguments, entry, content)
        return ToolResult(call_id, content, is_error)

    def execute(self, call_id: str, name: str, arguments: dict[str, Any] | None) -> ToolResult:
        spec = next((s for s in self.specs() if s.name == name), None)
        args = arguments if isinstance(arguments, dict) else {}
        self._emit("start", name, args)
        t0 = time.monotonic()
        if spec is None:
            return self._log(call_id, AuditEntry(name, args, False, None, 0, "unknown tool"),
                             f"Error: unknown tool '{name}'.", True)
        missing = [k for k in spec.parameters.get("required", []) if k not in args]
        if missing:
            return self._log(call_id, AuditEntry(name, args, False, None, 0, f"missing {missing}"),
                             f"Error: missing required argument(s): {', '.join(missing)}.", True)
        try:
            out = spec.handler(args)
        except (ToolError, UnsafeSQL) as e:
            return self._log(call_id, AuditEntry(name, args, False, None, int((time.monotonic() - t0) * 1000), str(e)),
                             f"Error: {e}", True)
        except Exception as e:  # noqa: BLE001 - surface any tool failure to the model, never crash the session
            return self._log(call_id, AuditEntry(name, args, False, None, int((time.monotonic() - t0) * 1000), repr(e)),
                             f"Error: {type(e).__name__}: {e}", True)
        out = self._remember(name, args, out)
        rows = out.get("row_count") if isinstance(out, dict) else None
        return self._log(call_id, AuditEntry(name, args, True, rows, int((time.monotonic() - t0) * 1000)),
                         self._serialise(out))

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
        if self._jobs is not None:
            self._jobs.close()

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
            item = {"feature": r["feature"], "latest_run_id": run["id"], "started_at": run["started_at"],
                    "account_id": run["account_id"], "findings": f["n"], "alarms": f["alarms"] or 0,
                    "est_monthly_saving_on_alarms": f["saving"] or 0, "cost_rows": c["n"], "cost_rows_total": c["total"]}
            if c["n"]:  # what the cost rows hold, so no queries are spent discovering it
                item["periods"] = {k: p for k, p in con.execute(
                    "select distinct kind, period from cost_rows where run_id = ? order by kind", (run["id"],))}
                item["dimensions"] = [d for (d,) in con.execute(
                    "select distinct dimension from cost_rows where run_id = ?", (run["id"],))]
            features.append(item)
        latest = [f["latest_run_id"] for f in features if f["feature"] in ("cost_diff", "cost_trend") and f["cost_rows"]]
        names = [g for (g,) in con.execute(
            f"select grp from cost_rows where run_id in ({','.join('?' * len(latest))}) and grp not like '%' || "
            "char(31) || '%' group by grp order by sum(amount) desc limit 40", latest)] if latest else []
        return {"database": str(self.history_db), "features": features, "schema": HISTORY_SCHEMA_HINT,
                "cost_groups_by_spend": names,
                "tip": "For one service's cost change use history_cost('<service>'); grp values are the exact names in "
                       "cost_groups_by_spend.", "row_count": len(features)}

    # ----- CloudTrail logs collected by Tailpipe

    _tailpipe_ok: Any = None

    def _tailpipe_available(self) -> bool:
        if self._tailpipe_ok is None:
            from .tailpipe_tools import available

            self._tailpipe_ok = available(self.cfg.graph.tailpipe_path)
        return bool(self._tailpipe_ok)

    def aws_changes(self, hours: int, user: str | None, resource: str | None, service: str | None,
                    event: str | None) -> dict[str, Any]:
        from ..changes import find_changes
        from ..steampipe import SteampipeError

        tailpipe = self._tailpipe_available()
        if not tailpipe and not self.live:
            return {"note": "CloudTrail needs live AWS (ask without --no-live) or CloudTrail logs collected by "
                            "Tailpipe (`spendlens tailpipe init` then `spendlens tailpipe collect`).", "row_count": 0}
        try:
            res = find_changes(self.cfg, int(hours), user=user, resource=resource, service=service, event=event,
                               source="auto" if tailpipe else "lookup_events",
                               steampipe=self._client() if self.live else None)
        except SteampipeError as e:
            raise ToolError(str(e).splitlines()[0][:300]) from e
        rows = [{k: str(v)[:300] if v is not None else None for k, v in r.items()}
                for r in res["rows"][: self.cfg.llm.max_rows]]
        return {**res, "rows": rows, "row_count": len(rows)}

    def tailpipe_query(self, sql: str, describe: str | None = None) -> dict[str, Any]:
        from ..graph.costs import CostSourceError, tailpipe_runner
        from .tailpipe_tools import columns_sql, run

        try:
            runner = tailpipe_runner(self.cfg.graph.tailpipe_path, timeout=int(self.cfg.llm.query_timeout_s))
            if describe:
                rows = runner(columns_sql(describe))
                if not rows:
                    return {"note": f"Tailpipe has no {describe} data yet: `spendlens tailpipe init`, then "
                                    "`spendlens tailpipe collect`.", "row_count": 0}
                return {"columns": [[r.get("column_name"), r.get("data_type")] for r in rows], "row_count": len(rows)}
            if not sql.strip():
                raise ToolError("give sql, or describe=true")
            return run(sql, runner=runner, max_rows=self.cfg.llm.max_rows)
        except CostSourceError as e:
            raise ToolError(f"tailpipe: {e}") from e

    # ----- resource graph (feature 'graph' in the history)

    _graph_cache: Any = None

    def _graph(self) -> Any:
        """(Graph, run row, waste findings, collected sources) from the latest saved graph, or None."""
        if self._graph_cache is None:
            from ..graph.model import Graph
            from ..history import read_findings, read_graph

            con = self._history()
            run = con.execute("select id, started_at, account_id, meta from runs where feature = 'graph' "
                              "order by id desc limit 1").fetchone()
            if run is None:
                self._graph_cache = False
                return None
            g = Graph.from_rows(*read_graph(con, run["id"]))
            findings: dict[str, list[Any]] = {}
            for feat in ("tags", "rightsize", "waste"):
                r = con.execute("select id from runs where feature = ? order by id desc limit 1", (feat,)).fetchone()
                findings[feat] = read_findings(con, r["id"]) if r else []
            meta = json.loads(run["meta"] or "{}")
            counts = meta.get("counts")
            self._graph_cache = (g, dict(run), findings, set(counts) if isinstance(counts, dict) else None)
        return self._graph_cache or None

    def _no_graph(self) -> dict[str, Any]:
        return {"note": "No saved resource graph in this history. `spendlens graph collect` (read-only) or "
                        "`spendlens graph orphans` saves one; `--source demo` has a sample.", "row_count": 0}

    def _groups(self, loaded: Any) -> list[Any]:
        from datetime import datetime

        from ..features.graph_cmd import owner_keys
        from ..graph.analyze import cleanup_groups

        g, run, findings, collected = loaded
        return cleanup_groups(g, self.cfg.graph, now=datetime.fromisoformat(run["started_at"]), collected=collected,
                              findings=findings["waste"], owner_keys=owner_keys(self.cfg))

    def resource_context(self, resource: str, depth: int = 1) -> dict[str, Any]:
        from ..features.graph_cmd import suggestions_for
        from ..graph.analyze import impact, shared_costs
        from ..graph.model import type_label

        loaded = self._graph()
        if not loaded:
            return self._no_graph()
        g, run, findings, _ = loaded
        found = g.find(resource)
        if not found:
            return {"note": f"No resource matching {resource!r} in graph run #{run['id']}. Use an ARN, an AWS id or a "
                            "Name tag; history_query on graph_nodes can search by name.", "row_count": 0}
        if len(found) > 1:
            return {"note": f"{len(found)} resources match; call again with one id.", "row_count": len(found),
                    "matches": [{"id": n.id, "type": n.type, "name": n.label} for n in found[:15]]}
        n = found[0]
        sub = g.around(n.id, max(1, min(int(depth), 2)))

        def link(other: str) -> str:
            for e in g.out(n.id):
                if e.to_id == other:
                    return f"{e.label} (this -> it)"
            for e in g.into(n.id):
                if e.from_id == other:
                    return f"{e.label} (it -> this)"
            return "2 links away"

        neighbours = [{"id": m.short_id, "type": type_label(m.type), "name": m.label, "state": m.state,
                       "monthly_cost": m.monthly_cost, "link": link(m.id)}
                      for m in sorted(sub.nodes.values(), key=lambda m: -(m.monthly_cost or 0)) if m.id != n.id][:40]
        short = n.short_id
        alarms = [{"check": f.check, "control": f.control, "reason": f.reason, "est_monthly_saving": f.est_monthly_saving}
                  for fs in findings.values() for f in fs if f.status.value == "alarm" and f.resource in (n.id, short)]
        group = next((grp for grp in self._groups(loaded) if n.id in grp.members), None)
        imp = impact(g, n.id)
        return {
            "graph_run": {"id": run["id"], "started_at": run["started_at"]},
            "resource": {"id": n.id, "type": type_label(n.type), "name": n.label, "state": n.state,
                         "region": n.region, "monthly_cost": n.monthly_cost, "cost_basis": n.cost_basis,
                         "tags": n.tags, "facts": {k: v for k, v in n.attrs.items() if v not in (None, "", [])}},
            "neighbours": neighbours, "group_monthly_cost": sub.total_cost(), "group_size": len(sub.nodes),
            "cleanup_group": group.to_dict() | {"members": [g.nodes[m].short_id for m in group.members]} if group else None,
            "suggested_tags": [x.to_dict() for x in suggestions_for(g, self.cfg) if x.resource == n.id],
            "failing_checks": alarms,
            "if_deleted": {"breaks": [x["why"] for x in imp["breaks"]],
                           "left_behind": [x["why"] for x in imp["left_behind"]],
                           "left_behind_monthly_cost": imp["left_behind_cost"], "stays": [x["why"] for x in imp["kept"]]},
            "shared_costs": [x for x in shared_costs(g) if n.id in (x["source"], x["target"])],
            "note": "Costs are monthly estimates from the finops.yaml price book (snapshots: upper bound). "
                    "Read-only: nothing can be changed from here.",
            "row_count": len(neighbours) + 1,
        }

    def cleanup_groups(self, kind: str | None, min_saving: float, top: int) -> dict[str, Any]:
        loaded = self._graph()
        if not loaded:
            return self._no_graph()
        g, run, _, _ = loaded
        groups = [grp for grp in self._groups(loaded) if (not kind or grp.kind == kind)
                  and grp.monthly_saving >= float(min_saving)]
        rows = [{"kind": grp.kind, "title": grp.title, "monthly_saving": grp.monthly_saving,
                 "resources": [g.nodes[m].short_id for m in grp.members], "region": grp.region, "owner": grp.owner,
                 "waste_alarms_included": len(grp.findings), "waste_alarms_saving": grp.findings_saving,
                 "evidence": grp.evidence, "action": grp.action, "check_first": grp.risk}
                for grp in groups[: max(1, int(top))]]
        return {"graph_run": {"id": run["id"], "started_at": run["started_at"]}, "groups": rows,
                "total_monthly_saving": round(sum(grp.monthly_saving for grp in groups), 2),
                "new_beyond_waste_alarms": round(sum(max(grp.monthly_saving - grp.findings_saving, 0)
                                                     for grp in groups), 2),
                "group_count": len(groups), "row_count": len(rows),
                "note": "Estimates for human review; each resource is in at most one group."}

    def suggested_owners(self, resource: str | None, key: str | None, min_confidence: str) -> dict[str, Any]:
        from ..features.graph_cmd import suggestions_for

        loaded = self._graph()
        if not loaded:
            return self._no_graph()
        g, run, _, _ = loaded
        if min_confidence not in ("high", "medium", "low"):
            raise ToolError("min_confidence must be high, medium or low")
        sugg = suggestions_for(g, self.cfg, min_confidence)
        if resource:
            ids = {n.id for n in g.find(resource)}
            sugg = [x for x in sugg if x.resource in ids]
        if key:
            sugg = [x for x in sugg if x.key.lower() == key.lower()]
        rows = [{"resource": g.nodes[x.resource].short_id, "type": x.type, "name": x.name, "tag": x.key,
                 "suggested": x.value, "confidence": x.confidence, "because": x.reason}
                for x in sugg[: self.cfg.llm.max_rows]]
        return {"graph_run": {"id": run["id"], "started_at": run["started_at"]}, "suggestions": rows,
                "resources": len({x.resource for x in sugg}), "row_count": len(rows),
                "note": "Suggestions only; spendlens never applies tags. `spendlens graph owners --script "
                        "tag-review.sh` writes the commands for a person to review."}

    def traffic_paths(self, path: str | None, resource: str | None, top: int) -> dict[str, Any]:
        con = self._history()
        run = con.execute("select id, started_at, meta from runs where feature = 'graph_traffic' order by id desc "
                          "limit 1").fetchone()
        if run is None:
            return {"note": "No saved data transfer analysis. `spendlens graph traffic` makes one from VPC flow logs "
                            "collected with Tailpipe; `--source demo` has a sample.", "row_count": 0}
        t = json.loads(run["meta"] or "{}").get("traffic") or {}
        rows = t.get("rows") or []
        if path:
            rows = [r for r in rows if r["path"] == path]
        if resource:
            low = resource.lower()
            rows = [r for r in rows if low in (r["resource_name"] or "").lower() or low in r["resource"].lower()]
        keep = ("resource_name", "resource_type", "path_label", "peer_name", "via_name", "gb_in_month", "gb_out_month",
                "monthly_cost", "region")
        out = [{k: r.get(k) for k in keep} for r in rows[: max(1, int(top))]]
        return {"run": {"id": run["id"], "started_at": run["started_at"], "window_days": t.get("window_days")},
                "by_path": t.get("by_path"), "monthly_cost": t.get("monthly_cost"), "proposals": t.get("proposals"),
                "rows": out, "row_count": len(out),
                "note": "Monthly rates from the flow log window, priced with finops.yaml pricing.*_gb; traffic "
                        "between two collected resources is counted once, by the sender. Estimates for review."}

    def history_cost(self, service: str) -> dict[str, Any]:
        con = self._history()
        pats = _patterns(service)
        like = " or ".join("lower(grp) like ?" for _ in pats)
        args = [f"%{p.lower()}%" for p in pats]
        out: dict[str, Any] = {"matched": pats, "cost": {}, "findings": {}}
        for feat in COST_FEATURES:
            run = con.execute("select id, started_at, meta from runs where feature = ? order by id desc limit 1",
                              (feat,)).fetchone()
            if not run:
                continue
            rows = con.execute(f"select kind, period, dimension, replace(grp, char(31), ' > ') grp, round(sum(amount), 2) "
                               f"amount from cost_rows where run_id = ? and ({like}) group by kind, period, dimension, grp "
                               "order by dimension, grp, kind", (run["id"], *args)).fetchall()
            if rows:
                out["cost"][feat] = {"run_id": run["id"], "started_at": run["started_at"],
                                     "rows": [list(r) for r in rows[: self.cfg.llm.max_rows]],
                                     "columns": ["kind", "period", "dimension", "grp", "amount"],
                                     "truncated": len(rows) > self.cfg.llm.max_rows}
        short = [p.lower() for p in pats] + [k for k, v in SERVICE_ALIASES.items() if set(v) & set(pats)]
        flike = " or ".join("lower(service) = ? or lower(resource_type) like ?" for _ in short)
        fargs = [a for s in short for a in (s, f"%{s}%")]
        for feat in ("rightsize", "waste", "savings_hub", "tags"):
            run = con.execute("select id from runs where feature = ? order by id desc limit 1", (feat,)).fetchone()
            if not run:
                continue
            rows = con.execute(f"select control, status, count(*) n, round(sum(coalesce(est_monthly_saving, 0)), 2) "
                               f"saving from findings where run_id = ? and ({flike}) group by control, status "
                               "order by saving desc limit 20", (run["id"], *fargs)).fetchall()
            if rows:
                out["findings"][feat] = {"run_id": run["id"], "columns": ["control", "status", "n", "est_monthly_saving"],
                                         "rows": [list(r) for r in rows]}
        out["row_count"] = sum(len(v["rows"]) for part in ("cost", "findings") for v in out[part].values())
        if not out["cost"]:
            out["note"] = ("No saved cost rows match. Pick an exact name from history_overview's cost_groups_by_spend, "
                           "or run `spendlens cost diff` first.")
        elif "cost_breakdown" not in out["cost"]:
            out["note"] = ("No usage-type breakdown is saved, so the cause inside the service is unknown here. Say so, and "
                           "suggest `spendlens cost diff -g service -b usage-type` (or -b tag:Name) to see it.")
        return out

    # ----- live AWS (Steampipe)

    def _client(self) -> Any:
        from ..steampipe import SteampipeClient

        return SteampipeClient(self.cfg.steampipe, read_only=True, timeout_s=self.cfg.llm.query_timeout_s)

    def _scope(self) -> str:
        from .cache import scope

        return scope(self.cfg)

    def catalog(self) -> Any:
        """The AWS catalog, with the installed plugin's real columns merged in when Steampipe is reachable."""
        if self._catalog is None:
            from . import catalog as cat_mod

            base = cat_mod.load()
            live = self._live_columns()
            self._catalog = base.with_live(live) if live else base
        return self._catalog

    def _live_columns(self) -> dict[str, list[tuple[str, str]]] | None:
        from ..steampipe import SteampipeError
        from .cache import digest

        key = digest("columns", self._scope())
        if self.cache and (hit := self.cache.get("columns", key)):
            return {t: [tuple(c) for c in cols] for t, cols in hit[0].items()}
        try:
            with self._client() as c:
                rows = c.query("select table_name, column_name, data_type from information_schema.columns "
                               "where table_schema = %(s)s order by table_name, ordinal_position",
                               {"s": self.cfg.steampipe.connection})
        except (SteampipeError, OSError):
            return None  # Steampipe unreachable: the bundled catalog alone still guides the model
        live: dict[str, list[tuple[str, str]]] = {}
        for r in rows:
            live.setdefault(r["table_name"], []).append((r["column_name"], r["data_type"]))
        if live and self.cache:
            self.cache.put("columns", key, live, LIVE_COLUMNS_TTL_S, label=f"{len(live)} tables")
        return live or None

    def _jobs_runner(self) -> Any:
        if self._jobs is None:
            from .jobs import QueryJobs

            self._jobs = QueryJobs(self._client, max_rows=self.cfg.llm.max_rows,
                                   timeout_s=self.cfg.llm.query_timeout_s)
        return self._jobs

    def _require_live(self) -> None:
        if not self.live:
            raise ToolError("live AWS queries are disabled; re-run with --live")

    def aws_find_tables(self, question: str) -> dict[str, Any]:
        self._require_live()
        hits = self.catalog().find(question, 8)
        return {"tables": hits, "row_count": len(hits),
                "next": "call aws_describe_table on the best match before writing SQL"}

    def aws_describe_table(self, table: str) -> dict[str, Any]:
        self._require_live()
        cat = self.catalog()
        t = cat.get(table)
        if t is None:
            raise ToolError(f"table {table} does not exist. Did you mean: {', '.join(cat.similar(table)) or 'none'}? "
                            "Use aws_find_tables.")
        out = cat.describe(t.name)
        out["row_count"] = len(out["columns"])
        return out

    def aws_check_query(self, sql: str) -> dict[str, Any]:
        self._require_live()
        from .sql_check import check

        return check(sql, self.catalog(), max_rows=self.cfg.llm.max_rows).as_dict()

    def aws_query(self, sql: str) -> dict[str, Any]:
        self._require_live()
        from .cache import digest, normalise_sql, ttl_for
        from .sql_check import check

        res = check(sql, self.catalog(), max_rows=self.cfg.llm.max_rows)
        if not res.ok:
            raise ToolError("the query was not run; fix it first: "
                            + json.dumps([p.as_dict() for p in res.errors], separators=(",", ":")))
        ttl = ttl_for(res.tables, self.catalog(), self.cfg)
        key = digest("query", self._scope(), normalise_sql(res.sql))
        if self.cache and not self.fresh and (hit := self.cache.get("query", key)):
            payload, created = hit
            age = time.time() - created
            self.live_ttls.append(max(ttl - age, 0))
            return {**payload, "cached": True, "age_s": round(age),
                    "as_of": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(created)),
                    **({"warnings": [w.as_dict() for w in res.warnings]} if res.warnings else {})}
        job = self._jobs_runner().submit(res.sql, tables=res.tables, key=key, ttl=ttl,
                                         warnings=[w.as_dict() for w in res.warnings])
        return self._await(job.id)

    def aws_query_result(self, job_id: str) -> dict[str, Any]:
        self._require_live()
        return self._await(job_id)

    def _await(self, job_id: str) -> dict[str, Any]:
        from ..ui import activity
        from .sql_check import explain_error

        runner = self._jobs_runner()
        job = runner.jobs.get(job_id)
        if job is None:
            raise ToolError(f"unknown job_id {job_id}")
        label = ", ".join(job.meta.get("tables", [])) or "AWS"
        with activity(f"[bold]live query[/] {job_id} · {label}"):
            out = runner.wait(job_id, self.cfg.llm.query_wait_s)
        if out["status"] == "running":
            return {**out, "row_count": None,
                    "next": f"still running after {out['elapsed_s']}s: call aws_query_result with job_id {job_id}"}
        if out["status"] == "error":
            hint = explain_error(out["error"], job.meta.get("tables", []), self.catalog())
            raise ToolError(f"Steampipe error: {out['error']}" + (f"\nHow to fix: {hint}" if hint else ""))
        rows = out["rows"]
        truncated = len(rows) > self.cfg.llm.max_rows
        rows = rows[: self.cfg.llm.max_rows]
        cols = list(rows[0].keys()) if rows else []
        payload = {"columns": cols, "rows": [[r[c] for c in cols] for r in rows], "row_count": len(rows),
                   "truncated": truncated, "tables": job.meta.get("tables", [])}
        payload = json.loads(json.dumps(payload, default=str))  # cache-safe (dates, decimals, inet ...)
        ttl = job.meta.get("ttl", 0)
        if self.cache:
            self.cache.put("query", job.meta["key"], payload, ttl, label=job.sql[:300])
        self.live_ttls.append(ttl)
        extra = {"warnings": job.meta["warnings"]} if job.meta.get("warnings") else {}
        return {**payload, "cached": False, "elapsed_s": out["elapsed_s"],
                "as_of": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), **extra}
