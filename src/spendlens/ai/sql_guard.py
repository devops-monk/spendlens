"""SQL guard for model-written queries: exactly one read-only SELECT (incl. WITH / UNION).

Layer 1 of 3. The guard is a fast, explainable "no" for the model; it is NOT the only protection:
  2. Steampipe connections run with default_transaction_read_only=on, a statement_timeout and a row cap;
  3. the SQLite history DB is opened read-only (mode=ro), with PRAGMA query_only and an authorizer that
     denies everything except reads.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

# side effects, file/network access, session state, or denial of service
FORBIDDEN_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export", "lo_get",
    "dblink", "dblink_exec", "dblink_connect", "set_config", "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile", "query_to_xml",
    "load_extension", "readfile", "writefile", "edit", "fts3_tokenizer",
}
FORBIDDEN_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Merge,
                   exp.Into, exp.Lock, exp.Copy, exp.TruncateTable, exp.Set, exp.Pragma, exp.Attach, exp.Detach,
                   exp.Grant, exp.Transaction, exp.Commit, exp.Rollback, exp.Use)


class UnsafeSQL(ValueError):
    pass


def parseable(text: str, dialect: str = "postgres") -> str:
    """Postgres network operators (inet <<= cidr, >>=, <<, >>) are not in sqlglot's grammar. For structural checks
    only, read them as the same-shaped '=' comparison; the original text is always what runs."""
    return re.sub(r"(?<![-#])(?:<<=|>>=|<<|>>)", "=", text) if dialect == "postgres" else text


def check_select(sql: str, dialect: str = "postgres") -> str:
    """Return the statement (trimmed) if it is a single read-only query, else raise UnsafeSQL with a reason."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise UnsafeSQL("empty query")
    parse_text = parseable(text, dialect)
    try:
        statements = [s for s in sqlglot.parse(parse_text, read=dialect) if s is not None]
    except (ParseError, TokenError) as e:
        raise UnsafeSQL(f"could not parse SQL: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise UnsafeSQL(f"exactly one statement allowed, got {len(statements)}")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise UnsafeSQL(f"only SELECT queries are allowed, got {stmt.key.upper()}")
    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQL(f"{node.key.upper()} is not allowed in read-only queries")
        if isinstance(node, (exp.Func, exp.Anonymous)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise UnsafeSQL(f"function {name}() is not allowed")
    return text
