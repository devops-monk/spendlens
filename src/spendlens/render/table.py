from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from ..models import ReportMeta

STATUS_STYLE = {"ok": "green", "alarm": "bold red", "info": "cyan", "skip": "dim", "error": "magenta"}


def _cell(col: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str) if value else ""
    if col == "status" and str(value) in STATUS_STYLE:
        return f"[{STATUS_STYLE[str(value)]}]{value}[/]"
    if col in ("change", "change_pct") and isinstance(value, (int, float)):
        style = "red" if value > 0 else "green" if value < 0 else ""
        txt = f"{value:+,.2f}" + ("%" if col == "change_pct" else "")
        return f"[{style}]{txt}[/]" if style else txt
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def render_table(records: list[dict[str, Any]], cols: list[str], meta: ReportMeta, *, out: Path | None = None) -> None:
    table = Table(title=meta.title, header_style="bold #E6007E", show_lines=False)
    for c in cols:
        table.add_column(c, overflow="fold", justify="right" if records and isinstance(records[0].get(c), (int, float)) else "left")
    for r in records:
        table.add_row(*(_cell(c, r.get(c)) for c in cols))
    console = Console(record=out is not None, width=200 if out else None)
    console.print(table if records else f"[dim]{meta.title}: no rows[/]")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(console.export_text())
