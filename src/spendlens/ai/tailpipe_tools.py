"""The assistant's `tailpipe_query` tool: data already collected by Tailpipe (DuckDB SQL, local): CloudTrail logs,
VPC flow logs and Cost and Usage Reports.

Beyond what LookupEvents gives (`aws_cloudtrail_lookup_event`, 90 days of management events): full history and data
events (S3 object reads and writes), from the trail's own log files that Tailpipe collected. Offered to the model
only when the tailpipe CLI is installed. Nothing here calls AWS.

Guard, on top of the general one (single read-only SELECT):
  * only the tables aws_cloudtrail_log, aws_vpc_flow_log, aws_cost_and_usage_report (and the query's own CTEs):
    no table functions, no read_csv / read_parquet /
    glob / getenv or other file and environment access DuckDB offers;
  * a time range (tp_date, tp_timestamp, event_time, start_time or the CUR's usage date), so Tailpipe reads only
    the partitions it needs;
  * a row cap (llm.max_rows).
"""

from __future__ import annotations

import shutil
from typing import Any

import sqlglot
from sqlglot import exp

from .sql_guard import UnsafeSQL, check_select

TABLE = "aws_cloudtrail_log"
TABLES = ("aws_cloudtrail_log", "aws_vpc_flow_log", "aws_cost_and_usage_report")
TIME_COLUMNS = {"tp_date", "tp_timestamp", "event_time", "start_time", "line_item_usage_start_date",
                "bill_billing_period_start_date"}
DUCKDB_FORBIDDEN = {
    "read_csv", "read_csv_auto", "read_parquet", "parquet_scan", "read_json", "read_json_auto", "read_ndjson",
    "read_ndjson_auto", "read_json_objects", "read_text", "read_blob", "glob", "getenv", "sniff_csv", "iceberg_scan",
    "delta_scan", "query", "query_table", "install", "load", "attach", "copy", "export_database", "import_database",
    "duckdb_secrets", "duckdb_settings", "current_setting", "sqlite_scan", "postgres_scan", "mysql_scan", "httpfs",
}


def columns_sql(table: str) -> str:
    if table not in TABLES:
        raise UnsafeSQL(f"table must be one of {', '.join(TABLES)}")
    return (f"select column_name, data_type from information_schema.columns where table_name = '{table}' "
            "order by ordinal_position")


COLUMNS_SQL = columns_sql(TABLE)


def available(path: str) -> bool:
    return shutil.which(path) is not None


def check(sql: str, max_rows: int) -> str:
    """The query to run (capped), or UnsafeSQL with what to change."""
    text = check_select(sql, "duckdb")
    try:
        tree = sqlglot.parse_one(text, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"could not parse SQL: {str(e).splitlines()[0]}") from e
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise UnsafeSQL("table functions are not allowed: query the Tailpipe tables directly")
        name = t.name.lower()
        if name not in ctes and name not in TABLES:
            raise UnsafeSQL(f"only {', '.join(TABLES)} can be queried here (got {t.name})")
    for f in tree.find_all(exp.Func):
        name = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if name in DUCKDB_FORBIDDEN:
            raise UnsafeSQL(f"function {name}() is not allowed")
    bounded = False
    for where in tree.find_all(exp.Where):
        for cmp in where.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.Between):
            if any(c.name.lower() in TIME_COLUMNS for c in cmp.find_all(exp.Column)):
                bounded = True
    if not bounded:
        raise UnsafeSQL("add a time range so Tailpipe reads only the partitions it needs, e.g. "
                        "where tp_date >= current_date - interval 30 day")
    return f"select * from ({text}) as q limit {int(max_rows) + 1}"


def run(sql: str, *, runner: Any, max_rows: int) -> dict[str, Any]:
    rows = runner(check(sql, max_rows))
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    cols = list(rows[0]) if rows else []
    return {"columns": cols, "rows": [[r.get(c) for c in cols] for r in rows], "row_count": len(rows),
            "truncated": truncated, "source": "Tailpipe (local)"}
