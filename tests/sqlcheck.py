"""Offline SQL checker shared by tests: Postgres grammar (pglast) + table/column names AND basic type rules from
the steampipe-plugin-aws schema snapshot (tests/fixtures/aws_schema.json, from scripts/snapshot_aws_schema.py).

Type rules (each one caught a real bug end-to-end before it existed here):
  * JSON operators (->, ->>, ?, ?&, ?|, @>, #>, #>>) need a JSON column      (budgets ->> on a STRING column)
  * arithmetic (+ - * /) on a STRING column needs an explicit cast           (lambda `timeout * 1000`)
  * coalesce(<ip address column>, 'text') needs host()/::text                (EIP private_ip_address)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pglast import ast, parse_sql

SCHEMA: dict[str, dict[str, str]] = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "aws_schema.json").read_text())["tables"]
BUILTIN_COLUMNS = {"*"}
JSON_OPS = {"->", "->>", "?", "?&", "?|", "@>", "#>", "#>>"}
ARITH_OPS = {"+", "-", "*", "/"}


def _walk(node):
    if isinstance(node, (list, tuple)):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, ast.Node):
        yield node
        for attr in node.__slots__:
            yield from _walk(getattr(node, attr, None))


def _op(n: ast.A_Expr) -> str | None:
    names = [x.sval for x in (n.name or ()) if isinstance(x, ast.String)]
    return names[-1] if names else None


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

    def col_type(ref: ast.Node) -> tuple[str, str] | None:
        """(display name, steampipe type) for a ColumnRef that resolves to a real table column."""
        if not isinstance(ref, ast.ColumnRef):
            return None
        parts = [f.sval for f in ref.fields if isinstance(f, ast.String)]
        if not parts:
            return None
        col = parts[-1]
        if len(parts) >= 2:
            t = alias_to_table.get(parts[-2])
            return (f"{parts[-2]}.{col}", SCHEMA[t][col]) if t in SCHEMA and col in SCHEMA[t] else None
        if col in derived:
            return None  # an alias defined in this query: type unknown here
        types = {SCHEMA[t][col] for t in real_tables if col in SCHEMA[t]}
        return (col, types.pop()) if len(types) == 1 else None

    for n in nodes:
        if isinstance(n, ast.ColumnRef):
            parts = [f.sval for f in n.fields if isinstance(f, ast.String)]
            if not parts:
                continue
            col = parts[-1]
            if len(parts) >= 2:
                target = alias_to_table.get(parts[-2])
                if target in SCHEMA and col not in SCHEMA[target]:
                    problems.append(f"{parts[-2]}.{col}: no column {col} in {target}")
            elif col not in any_table_cols and col not in derived:
                problems.append(f"unknown column {col}")
        elif isinstance(n, ast.A_Expr) and n.kind == 0:  # AEXPR_OP
            op = _op(n)
            if op in JSON_OPS:
                ct = col_type(n.lexpr)
                if ct and ct[1] != "JSON":
                    problems.append(f"{ct[0]} is {ct[1]}, not JSON: cannot use '{op}'")
            elif op in ARITH_OPS:
                for side in (n.lexpr, n.rexpr):
                    ct = col_type(side)
                    if ct and ct[1] == "STRING":
                        problems.append(f"{ct[0]} is STRING: cast before '{op}' (e.g. {ct[0]}::numeric)")
        elif isinstance(n, ast.CoalesceExpr):
            args = list(n.args or ())
            ip = [col_type(a) for a in args if col_type(a) and col_type(a)[1] in ("IPADDR", "CIDR")]
            if ip and any(isinstance(a, ast.A_Const) and isinstance(a.val, ast.String) for a in args):
                problems.append(f"coalesce({ip[0][0]} {ip[0][1]}, text): use host({ip[0][0]})")
    return sorted(set(problems))
