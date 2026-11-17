"""`spendlens schema` and `spendlens cache`: the tools the AI assistant uses, for people.

schema find / show / check work offline from the bundled AWS table catalog (no AWS calls, no Steampipe needed):
the same table search, table description and query checker that `spendlens ask --live` uses.
cache show / clear inspect the assistant's cache of live results, answers and brief summaries.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.syntax import Syntax
from rich.table import Table

from ..common import BRAND, ConfigOpt, console, err, history_path, load
from ..plugins import Registry

schema_app = typer.Typer(help="The Steampipe AWS tables the AI assistant can query: find, describe, check SQL "
                              "(offline).", no_args_is_help=True)
cache_app = typer.Typer(help="The AI assistant's cache of live results, answers and brief summaries.",
                        no_args_is_help=True)


def _catalog():
    try:
        from ..ai import catalog
    except ImportError as e:  # sqlglot lives in the ai extra
        err.print(f"[red]Needs the AI extras:[/] pip install 'spendlens[ai]' ({e.name} missing)")
        raise typer.Exit(2) from e
    return catalog.load()


@schema_app.command("find")
def find(
    question: Annotated[str, typer.Argument(help="A question or keywords, e.g. \"unattached volumes\".")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many tables.")] = 8,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Find the AWS tables that can answer a question.

    Examples:
      spendlens schema find "unattached EBS volumes"
      spendlens schema find "IAM users without MFA"
      spendlens schema find "monthly cost by service" --limit 3
    """
    hits = _catalog().find(question, limit)
    if json_out:
        typer.echo(json.dumps(hits, indent=2))
        return
    t = Table(header_style=f"bold {BRAND}", title=f"Tables for: {question}")
    for c in ("table", "service", "what it holds", "required filters"):
        t.add_column(c, overflow="fold")
    for h in hits:
        t.add_row(h["table"], h["service"], h["description"], h.get("required_filters", ""))
    console.print(t)
    if hits:
        err.print(f"[dim]Next: spendlens schema show {hits[0]['table']}[/]")


@schema_app.command("show")
def show(
    table: Annotated[str, typer.Argument(help="Table name, e.g. aws_ebs_volume.")],
    examples: Annotated[bool, typer.Option("--examples/--no-examples", help="Show example queries.")] = True,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Columns, required filters, fast filters and example queries for one table.

    Examples:
      spendlens schema show aws_ebs_volume
      spendlens schema show aws_cost_usage --no-examples
    """
    cat = _catalog()
    t = cat.get(table)
    if t is None:
        err.print(f"[red]No table {table}.[/] Did you mean: {', '.join(cat.similar(table)) or 'try spendlens schema find'}")
        raise typer.Exit(1)
    d = cat.describe(t.name, examples=examples)
    if json_out:
        typer.echo(json.dumps(d, indent=2))
        return
    console.print(f"[bold]{t.name}[/] · {t.service} · {d['description']}")
    for k in ("required_filters", "fast_filters", "cost_note", "time_note"):
        if k in d:
            console.print(f"[{'red' if k == 'required_filters' else 'yellow' if k.endswith('note') else 'dim'}]{d[k]}[/]")
    tb = Table(header_style=f"bold {BRAND}")
    for c in ("column", "type", "filter", "description"):
        tb.add_column(c, overflow="fold")
    for c in d["columns"]:
        flt = "required" if c.get("required_filter") else ("fast " + " ".join(c["fast_filter"])) if c.get("fast_filter") else ""
        tb.add_row(c["name"], c["type"], flt, c.get("description", ""))
    console.print(tb)
    for ex in d.get("examples", []):
        console.print(f"[bold]{ex['title']}[/]")
        console.print(Syntax(ex["sql"], "sql", theme="ansi_dark", background_color="default"))
    err.print(f"[dim]Catalog: Steampipe AWS plugin v{cat.version}[/]")


@schema_app.command("check")
def check_sql(
    sql: Annotated[str, typer.Argument(help="One SELECT on aws_* tables (quote it).")],
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Check a Steampipe query without running it: tables, columns, required filters, cost guards.

    Exit code 1 when the query would fail. The same check runs before every query the AI assistant makes.
    Examples:
      spendlens schema check "select volume_id, size from aws_ebs_volume where state = 'available'"
      spendlens schema check "select * from aws_cost_usage"
    """
    from ..ai.sql_check import check
    from ..ai.sql_guard import UnsafeSQL

    try:
        res = check(sql, _catalog())
    except UnsafeSQL as e:
        err.print(f"[red]Rejected:[/] {e}")
        raise typer.Exit(1) from e
    if json_out:
        typer.echo(json.dumps(res.as_dict(), indent=2))
    else:
        for p in res.errors:
            console.print(f"[red]✗ {p.problem}[/]")
            if p.fix:
                console.print(f"  fix: {p.fix}")
            if p.did_you_mean:
                console.print(f"  did you mean: {', '.join(p.did_you_mean)}")
        for p in res.warnings:
            console.print(f"[yellow]! {p.problem}[/]" + (f"\n  {p.fix}" if p.fix else ""))
        if res.ok:
            console.print(f"[green]✓ OK[/] · tables: {', '.join(res.tables) or '-'}")
    if not res.ok:
        raise typer.Exit(1)


def _cache(config):
    from ..ai.cache import AICache

    cfg = load(config)
    return AICache.for_config(cfg, history_path(cfg))


KINDS = {"query": "live query results", "answer": "answers", "brief": "brief AI summaries",
         "columns": "installed plugin columns"}


@cache_app.command("show")
def cache_show(
    config: ConfigOpt = None,
    entries: Annotated[bool, typer.Option("--entries", help="List the most recent entries of each kind.")] = False,
) -> None:
    """What the AI assistant has cached, how old it is and when it expires."""
    c = _cache(config)
    stats = c.stats()
    t = Table(header_style=f"bold {BRAND}", title=f"AI cache · {c.path}")
    for col in ("kind", "entries", "size (KiB)", "oldest (min)", "last expiry in (min)"):
        t.add_column(col)
    for s in stats:
        t.add_row(KINDS.get(s["kind"], s["kind"]), str(s["entries"]), str(s["kib"]), str(s["oldest_min"]),
                  str(s["last_expiry_min"]))
    console.print(t if stats else "[dim]The cache is empty.[/]")
    if entries:
        for kind in ("answer", "query", "brief"):
            for e in c.entries(kind, 10):
                console.print(f"[dim]{kind:6}[/] {e['age_min']:>6} min old · expires in {e['expires_in_min']} min · "
                              f"{e['label'][:110]}")
    c.close()


@cache_app.command("clear")
def cache_clear(
    config: ConfigOpt = None,
    answers: Annotated[bool, typer.Option("--answers", help="Only cached answers and brief summaries.")] = False,
    queries: Annotated[bool, typer.Option("--queries", help="Only cached live query results.")] = False,
) -> None:
    """Delete cached entries (everything by default)."""
    c = _cache(config)
    n = 0
    if answers:
        n += c.clear("answer") + c.clear("brief")
    if queries:
        n += c.clear("query")
    if not (answers or queries):
        n = c.clear()
    c.close()
    console.print(f"Removed {n} cached entr{'y' if n == 1 else 'ies'}.")


def register(reg: Registry) -> None:
    reg.add_typer(schema_app, "schema")
    reg.add_typer(cache_app, "cache")
