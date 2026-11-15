"""Renderers: every command produces rows (pydantic models or dicts) and picks a format."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..models import ReportMeta


class Format(str, Enum):
    table = "table"
    csv = "csv"
    json = "json"
    html = "html"


def to_records(rows: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if hasattr(r, "flat"):
            out.append(r.flat())
        elif isinstance(r, BaseModel):
            out.append(r.model_dump(mode="json"))
        elif isinstance(r, dict):
            out.append(dict(r))
        else:
            raise TypeError(f"Cannot render row of type {type(r).__name__}")
    return out


def columns_of(records: list[dict[str, Any]], columns: list[str] | None = None) -> list[str]:
    if columns:
        return columns
    cols: list[str] = []
    for r in records:
        cols.extend(k for k in r if k not in cols)
    return cols


def render(
    rows: Iterable[Any],
    fmt: Format,
    *,
    meta: ReportMeta,
    out: Path | None = None,
    columns: list[str] | None = None,
) -> str | None:
    """Render rows. Returns the rendered text for file formats (also written to `out` if given)."""
    records = to_records(rows)
    cols = columns_of(records, columns)
    if fmt is Format.table:
        from .table import render_table

        render_table(records, cols, meta, out=out)
        return None
    if fmt is Format.csv:
        from .csv import render_csv

        text = render_csv(records, cols)
    elif fmt is Format.json:
        from .json import render_json

        text = render_json(records, meta)
    else:
        raise NotImplementedError("HTML output arrives in phase 6 (`spendlens dashboard`).")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return text
