"""Check a Steampipe query against the AWS catalog BEFORE it runs, and explain failures AFTER.

`check()` (offline, no AWS calls) returns problems the model can fix in one step:
  * not a single read-only SELECT (the existing guard, sql_guard.check_select);
  * unknown table -> "did you mean";
  * unknown column -> close matches and the table's columns;
  * a required filter missing (e.g. aws_cost_usage needs granularity, dimension_type_1, dimension_type_2) -> the
    exact WHERE clause to add, with valid values from the column description;
  * JSON operators (->, ->>, ?, @>) on a column that is not JSON.
Warnings (the query still runs): Cost Explorer without a date range, time series without a time range,
`select *` on a wide table. A LIMIT is added when the outer query has none.

`explain_error()` turns a Postgres / Steampipe / AWS error into the same kind of actionable advice.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.scope import Scope, traverse_scope

from .catalog import Catalog, Table
from .sql_guard import UnsafeSQL, check_select, parseable

JSON_NODES = tuple(getattr(exp, n) for n in ("JSONExtract", "JSONExtractScalar", "JSONBExtract", "JSONBExtractScalar",
                                             "JSONBContains", "JSONBContainsTopKey", "JSONBContainsAnyTopKeys",
                                             "JSONBContainsAllTopKeys") if hasattr(exp, n))
TIME_COLUMNS = ("timestamp", "period_start", "period_end", "event_time", "start_time", "end_time", "timestamp_ms",
                "search_start_time", "search_end_time")
WIDE_TABLE = 30
# CloudTrail event tables must be bounded in time, with a filter the API receives (error, not warning: an unbounded
# LookupEvents query pages through 90 days at about 2 calls a second)
TIME_REQUIRED = {
    "aws_cloudtrail_lookup_event": ({"start_time": {"="}}, "start_time = now() - interval '24 hours' AND end_time = now()",
                                    ("start_time and end_time are sent to the LookupEvents API only as '=' filters; a "
                                    "filter on event_time is applied after AWS has returned up to 90 days of events")),
    "aws_cloudtrail_trail_event": ({"timestamp": set(), "timestamp_ms": set()},
                                   "timestamp >= now() - interval '24 hours'",
                                   "timestamp is sent to CloudWatch Logs; without it every log stream is read"),
}


@dataclass
class Problem:
    problem: str
    fix: str = ""
    did_you_mean: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass
class CheckResult:
    sql: str
    tables: list[str] = field(default_factory=list)
    errors: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "tables": self.tables}
        if self.errors:
            out["errors"] = [p.as_dict() for p in self.errors]
        if self.warnings:
            out["warnings"] = [p.as_dict() for p in self.warnings]
        if self.ok:
            out["sql"] = self.sql
        return out


def _close(name: str, options: list[str], n: int = 5) -> list[str]:
    hits = difflib.get_close_matches(name, options, n=n, cutoff=0.55)
    sub = [o for o in options if name and (name in o or o in name) and o not in hits]
    return (hits + sub)[:n]


def _table_name(t: exp.Table) -> str:
    return (t.name or "").lower()


def _is_function_source(t: exp.Expression) -> bool:
    return isinstance(t, exp.Table) and not isinstance(t.this, exp.Identifier)


def check(sql: str, cat: Catalog, *, max_rows: int | None = None) -> CheckResult:
    """Validate a model-written Steampipe query against the catalog. Raises UnsafeSQL for non-read-only SQL."""
    text = check_select(sql, "postgres")
    try:
        tree = sqlglot.parse_one(parseable(text), read="postgres")
    except (ParseError, TokenError) as e:  # check_select parsed it, so this is unlikely
        raise UnsafeSQL(f"could not parse SQL: {str(e).splitlines()[0]}") from e
    res = CheckResult(sql=text)
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    seen_tables: set[str] = set()
    for t in tree.find_all(exp.Table):
        name = _table_name(t)
        if _is_function_source(t) or not name or name in cte_names:
            continue
        if name.startswith(("information_schema", "pg_")) or (t.db or "").lower() in ("information_schema", "pg_catalog"):
            continue
        if cat.get(name) is None:
            if name not in seen_tables:
                res.errors.append(Problem(
                    f"table {name} does not exist in the Steampipe AWS plugin",
                    "use aws_find_tables to find the right table",
                    did_you_mean=cat.similar(name) or [x["table"] for x in cat.find(name.replace("_", " "), 5)]))
        elif name not in res.tables:
            res.tables.append(name)
        seen_tables.add(name)
    if res.errors:
        return res
    try:
        scopes = traverse_scope(tree)
    except (OptimizeError, Exception):  # noqa: BLE001 - exotic SQL: skip column checks, Steampipe will tell
        scopes = []
    for scope in scopes:
        _check_scope(scope, cat, res)
    _check_star(tree, cat, res)
    _check_lookup_events(tree, res)
    if max_rows and isinstance(tree, (exp.Select, exp.Union)) and not tree.args.get("limit"):
        res.sql = f"{text}\nlimit {max_rows + 1}"  # textual: the query runs exactly as written, plus a row cap
    return res


LOOKUP_ATTRS = ("event_id", "event_name", "event_source", "read_only", "username", "resource_name", "resource_type",
                "access_key_id")


def _check_lookup_events(tree: exp.Expression, res: CheckResult) -> None:
    """LookupEvents takes ONE lookup attribute: two '=' filters make the API reject the call, and the plugin hides
    the error (no rows). A sort makes Steampipe read every page before returning a row."""
    if "aws_cloudtrail_lookup_event" not in res.tables:
        return
    used = []
    for eq in tree.find_all(exp.EQ):
        col, other = (eq.left, eq.right) if isinstance(eq.left, exp.Column) else (eq.right, eq.left)
        if isinstance(col, exp.Column) and col.name.lower() in LOOKUP_ATTRS and isinstance(
                other, exp.Literal | exp.Boolean | exp.Placeholder | exp.Cast):
            used.append(col.name.lower())
    if len(set(used)) > 1:
        keep, *rest = sorted(set(used), key=LOOKUP_ATTRS.index)
        res.errors.append(Problem(
            f"aws_cloudtrail_lookup_event: LookupEvents takes ONE lookup attribute, but {', '.join(sorted(set(used)))} "
            "are all '=' filters (AWS rejects the call and no rows come back)",
            f"keep {keep} = '...' and write the others so Steampipe applies them, e.g. "
            + ", ".join(f"lower({c}) = lower('...')" for c in rest)
            + "; or use the cloudtrail_events tool, which does this for you"))
    if tree.args.get("order"):
        res.warnings.append(Problem("aws_cloudtrail_lookup_event: ORDER BY makes Steampipe read every page before the "
                                    "first row (minutes for a busy account)",
                                    "drop ORDER BY: LookupEvents returns events newest first; use a LIMIT"))


def _aws_sources(scope: Scope, cat: Catalog) -> dict[str, Table]:
    out = {}
    for alias, src in scope.sources.items():
        if isinstance(src, exp.Table) and not _is_function_source(src) and (t := cat.get(_table_name(src))):
            out[alias.lower()] = t
    return out


def _scope_outputs(src: Any) -> set[str] | None:
    """Column names a derived source (CTE / subquery) exposes; None = anything (select *)."""
    if isinstance(src, Scope):
        e = src.expression
        if any(isinstance(s, exp.Star) or (isinstance(s, exp.Column) and isinstance(s.this, exp.Star))
               for s in getattr(e, "expressions", [])):
            return None
        return {n.lower() for n in getattr(e, "named_selects", [])}
    return None


def _check_scope(scope: Scope, cat: Catalog, res: CheckResult) -> None:
    aws = _aws_sources(scope, cat)
    others = {a.lower(): s for a, s in scope.sources.items() if a.lower() not in aws}
    select_aliases: set[str] = set()  # explicit AS names, and the implicit names of unaliased function calls
    for e in getattr(scope.expression, "expressions", []):
        if isinstance(e, exp.Alias):
            select_aliases.add(e.alias.lower())
        elif isinstance(e, exp.Func):
            select_aliases.add((e.name if isinstance(e, exp.Anonymous) else e.sql_name()).lower())
    reported: set[tuple[str, str]] = set()
    for col in scope.columns:
        if col.find_ancestor(exp.Select) is not scope.expression:
            continue  # inside a nested subquery: checked in that scope
        name = col.name.lower()
        qual = (col.table or "").lower()
        if qual:
            t = aws.get(qual)
            if t is None:
                outs = _scope_outputs(others.get(qual))
                if outs is not None and name not in outs and (qual, name) not in reported:
                    reported.add((qual, name))
                    res.errors.append(Problem(f"column {qual}.{name} is not an output of {qual}",
                                              did_you_mean=_close(name, sorted(outs)), columns=sorted(outs)[:40]))
                continue
            if name not in t.columns and (t.name, name) not in reported:
                reported.add((t.name, name))
                res.errors.append(_unknown_column(t, name))
            continue
        if name in t_names(aws) or name in select_aliases or name in others or name in aws:
            continue
        if any(_scope_outputs(s) is None or name in (_scope_outputs(s) or set()) for s in others.values()):
            continue  # may come from a derived source / function
        if aws and (("*", name) not in reported):
            reported.add(("*", name))
            t = next(iter(aws.values())) if len(aws) == 1 else None
            if t:
                res.errors.append(_unknown_column(t, name))
            else:
                res.errors.append(Problem(f"column {name} is not in any of {', '.join(x.name for x in aws.values())}",
                                          did_you_mean=_close(name, sorted({c for x in aws.values() for c in x.columns}))))
    owners: dict[str, int] = {}
    for t in aws.values():
        for c in t.columns:
            owners[c] = owners.get(c, 0) + 1
    for alias, t in aws.items():
        _check_required(scope, alias, t, len(aws) == 1, res, owners)
        _check_bounds(scope, alias, t, len(aws) == 1, res, owners)
    _check_json(scope, aws, res)


def t_names(aws: dict[str, Table]) -> set[str]:
    return {c for t in aws.values() for c in t.columns}


def _unknown_column(t: Table, name: str) -> Problem:
    cols = list(t.columns)
    return Problem(f"column {name} does not exist in {t.name}",
                   "use one of the listed columns (aws_describe_table shows their meaning)",
                   did_you_mean=_close(name, cols), columns=[c for c in cols if c not in ("_ctx", "sp_ctx")][:60])


def _predicates(scope: Scope) -> list[exp.Expression]:
    out = []
    sel = scope.expression
    if isinstance(sel, exp.Select):
        if w := sel.args.get("where"):
            out.append(w.this)
        for j in sel.args.get("joins") or []:
            if on := j.args.get("on"):
                out.append(on)
    return out


def _filtered_columns(scope: Scope, alias: str, single: bool, owners: dict[str, int] | None = None,
                      table: Table | None = None) -> dict[str, set[str]]:
    """{column: {operators}} constrained in WHERE / JOIN ON for this table alias (AND-ed terms only)."""
    ops = {exp.EQ: "=", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<=", exp.In: "=", exp.Like: "like",
           exp.ILike: "ilike", exp.NEQ: "<>", exp.Is: "is", exp.Between: "between"}
    found: dict[str, set[str]] = {}

    def terms(e: exp.Expression) -> list[exp.Expression]:
        e = e.unnest() if isinstance(e, exp.Paren) else e
        return [x for p in (e.left, e.right) for x in terms(p)] if isinstance(e, exp.And) else [e]

    for root in _predicates(scope):
        for term in terms(root):
            op = next((v for k, v in ops.items() if isinstance(term, k)), None)
            if op is None:
                continue
            sides = [term.this] + ([term.expression] if term.args.get("expression") is not None else [])
            for side in sides:
                if not isinstance(side, exp.Column):
                    continue
                unqualified_ok = not side.table and (single or (
                    table is not None and owners is not None and side.name.lower() in table.columns
                    and owners.get(side.name.lower(), 0) == 1))
                if (side.table or "").lower() == alias or unqualified_ok:
                    found.setdefault(side.name.lower(), set()).add(op)
    return found


def _check_required(scope: Scope, alias: str, t: Table, single: bool, res: CheckResult,
                    owners: dict[str, int]) -> None:
    if not (t.required or t.any_of):
        return
    have = _filtered_columns(scope, alias, single, owners, t)
    missing = [c for c in t.required if "=" not in have.get(c, set())]
    any_missing = bool(t.any_of) and not any(c in have for c in t.any_of)
    if not (missing or any_missing):
        return
    hints = []
    for c in missing + (t.any_of if any_missing else []):
        col = t.columns.get(c)
        if col and col.description:
            hints.append(f"{c}: {col.description[:300]}")
    need = " AND ".join(f"{c} = '<value>'" for c in missing)
    if any_missing:
        need = (need + " AND " if need else "") + "one of (" + " / ".join(f"{c} = '<value>'" for c in t.any_of) + ")"
    res.errors.append(Problem(f"{t.name} requires these filters in WHERE: {need}",
                              "add them; AWS cannot list this table without them. " + " | ".join(hints)))


def _check_bounds(scope: Scope, alias: str, t: Table, single: bool, res: CheckResult,
                  owners: dict[str, int]) -> None:
    if t.name in TIME_REQUIRED:
        cols, example, why = TIME_REQUIRED[t.name]
        have = _filtered_columns(scope, alias, single, owners, t)
        if not any(c in have and (not ops or ops & have[c]) for c, ops in cols.items()):
            res.errors.append(Problem(f"{t.name} needs a time range the AWS API receives", f"add {example} ({why})"))
        return
    if not (t.cost_explorer or t.time_series):
        return
    have = _filtered_columns(scope, alias, single, owners, t)
    if any(c in have for c in TIME_COLUMNS if c in t.columns):
        return
    if t.cost_explorer:
        res.warnings.append(Problem(f"{t.name} has no date range: Cost Explorer bills about $0.01 per request",
                                    "add e.g. period_start >= date_trunc('month', now()) - interval '3 months'"))
    else:
        col = next((c for c in ("timestamp", "event_time", "period_start", "start_time") if c in t.columns), "timestamp")
        res.warnings.append(Problem(f"{t.name} is a time series and the query has no time range",
                                    f"add e.g. {col} >= now() - interval '7 days'"))


def _check_json(scope: Scope, aws: dict[str, Table], res: CheckResult) -> None:
    for node in scope.expression.find_all(*JSON_NODES):
        target = node.this
        if not isinstance(target, exp.Column):
            continue
        qual, name = (target.table or "").lower(), target.name.lower()
        t = aws.get(qual) if qual else (next(iter(aws.values())) if len(aws) == 1 else None)
        col = t.columns.get(name) if t else None
        if col and col.type not in ("JSON", "UNKNOWN"):
            res.errors.append(Problem(f"{t.name}.{name} is {col.pg_type}, not JSON: JSON operators (->, ->>, ?, @>) "
                                      "do not apply", f"compare {name} directly, e.g. {name} = '...'"))


def _check_star(tree: exp.Expression, cat: Catalog, res: CheckResult) -> None:
    for sel in tree.find_all(exp.Select):
        if any(isinstance(e, exp.Star) for e in sel.expressions):
            for t in sel.find_all(exp.Table):
                tb = cat.get(_table_name(t))
                if tb and len(tb.columns) > WIDE_TABLE:
                    res.warnings.append(Problem(f"select * on {tb.name} returns {len(tb.columns)} columns and may call "
                                                "extra AWS APIs per row", "select only the columns you need"))


# ------------------------------------------------------------------ runtime errors -> advice

def explain_error(message: str, tables: list[str], cat: Catalog) -> str:
    """Actionable advice for a failed query (added to the error the model sees)."""
    m = message or ""
    if mm := re.search(r'column "?([\w.]+)"? does not exist', m):
        name = mm.group(1).split(".")[-1]
        cols = sorted({c for t in tables if (tb := cat.get(t)) for c in tb.columns})
        return f"Column {name} does not exist. Close matches: {', '.join(_close(name, cols)) or 'none'}. " \
               "Call aws_describe_table for the real columns."
    if mm := re.search(r'relation "?([\w.]+)"? does not exist', m):
        name = mm.group(1).split(".")[-1]
        return f"Table {name} does not exist. Did you mean: {', '.join(cat.similar(name)) or 'see aws_find_tables'}?"
    if re.search(r"required (key column|qual)|missing \d+ required qual|please provide", m, re.IGNORECASE):
        req = [f"{t}: WHERE {tb.required_text()}" for t in tables if (tb := cat.get(t)) and (tb.required or tb.any_of)]
        return "A required filter is missing. " + ("; ".join(req) or "Call aws_describe_table for its required filters.")
    if mm := re.search(r"not authori[sz]ed to perform:?\s*([\w:*-]+)|AccessDenied\w*|UnauthorizedOperation", m):
        action = mm.group(1) if mm.lastindex else ""
        return (f"AWS denied the call{f' ({action})' if action else ''}: the role in use lacks this read permission. "
                "Tell the user which permission is missing; try another table or answer from history.")
    if re.search(r"Throttl|Rate exceeded|TooManyRequests", m, re.IGNORECASE):
        return "AWS throttled the request. Narrow the query (fewer regions, a filter on a fast_filter column)."
    if re.search(r"statement timeout|canceling statement", m, re.IGNORECASE):
        return "The query ran out of time. Narrow it: filter on fast_filter columns, a shorter time range, or fewer rows."
    return ""
