"""Offline SQL checker shared by tests: Postgres grammar (pglast) + table/column names from the
steampipe-plugin-aws schema snapshot (tests/fixtures/aws_schema.json, from scripts/snapshot_aws_schema.py)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pglast import ast, parse_sql

SCHEMA = json.loads((Path(__file__).resolve().parent / "fixtures" / "aws_schema.json").read_text())["tables"]
BUILTIN_COLUMNS = {"*"}


def _walk(node):
    if isinstance(node, (list, tuple)):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, ast.Node):
        yield node
        for attr in node.__slots__:
            yield from _walk(getattr(node, attr, None))



def check_sql(sql: str) -> list[str]:
    """Return a list of problems (empty = ok). Accepts $n (Powerpipe) and %(name)s / %s (psycopg) params."""
    sql = re.sub(r"%\((\w+)\)s|%s", "$1", sql)
    stmts = parse_sql(sql)
    nodes = list(_walk(stmts))
    ctes = {n.ctename for n in nodes if isinstance(n, ast.CommonTableExpr)}
    alias_to_table: dict[str, str] = {}
    problems = []
    real_tables = set()
    for n in nodes:
        if isinstance(n, ast.RangeVar):
            if n.relname in ctes:
                if n.alias:
                    alias_to_table[n.alias.aliasname] = n.relname
                continue
            if n.relname not in SCHEMA:
                problems.append(f"unknown table {n.relname}")
                continue
            real_tables.add(n.relname)
            alias_to_table[n.relname] = n.relname
            if n.alias:
                alias_to_table[n.alias.aliasname] = n.relname
    derived = set(BUILTIN_COLUMNS)
    for n in nodes:
        if isinstance(n, ast.ResTarget) and n.name:
            derived.add(n.name)
        if isinstance(n, (ast.RangeFunction, ast.RangeSubselect)) and n.alias:
            derived.add(n.alias.aliasname)
            derived.update(c.sval for c in (n.alias.colnames or ()) if isinstance(c, ast.String))
    any_table_cols = set().union(*(SCHEMA[t] for t in real_tables)) if real_tables else set()
    for n in nodes:
        if not isinstance(n, ast.ColumnRef):
            continue
        parts = [f.sval for f in n.fields if isinstance(f, ast.String)]
        if not parts:
            continue
        col = parts[-1]
        if len(parts) >= 2:
            target = alias_to_table.get(parts[-2])
            if target in SCHEMA and col not in SCHEMA[target]:
                problems.append(f"{parts[-2]}.{col}: no column {col} in {target}")
            continue
        if col not in any_table_cols and col not in derived:
            problems.append(f"unknown column {col}")
    return sorted(set(problems))
