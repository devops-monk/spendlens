"""Files for the assistant: read the files people attach, write the files they ask for.

Reading: only files the person attached (`ask --file`, or a path written in the question that exists). Text-like
files (csv, json, yaml, logs, Terraform, ...) are read as text or rows; xlsx, pdf and docx are converted. Paged, so
big files never go to the model in one piece.

Writing: only inside the output directory (`ask --out-dir`, default llm.output_dir), never over an existing file
(a numbered name is used instead), plain names only (no absolute paths, no ".."). Tables are written from rows the
model sends or, without sending them again, from an earlier query result by its result_id.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 50 * 1024 * 1024
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".log", ".sql", ".tf",
            ".tfvars", ".hcl", ".py", ".sh", ".js", ".ts", ".html", ".htm", ".xml", ".ini", ".toml", ".conf", ".cfg",
            ".env.example", ".mmd", ".dot", ".svg", ".tpc", ".out"}
BINARY_EXT = {".xlsx", ".xlsm", ".pdf", ".docx"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm"}
WRITE_TEXT_EXT = {".txt", ".md", ".json", ".jsonl", ".yaml", ".yml", ".html", ".csv", ".tsv", ".sql", ".sh", ".tf",
                  ".tfvars", ".hcl", ".py", ".mmd", ".dot", ".svg", ".xml", ".ini", ".toml", ".log", ".tpc"}
WRITE_EXT = WRITE_TEXT_EXT | {".xlsx", ".docx"}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,120}$")


class FileError(Exception):
    pass


# ------------------------------------------------------------------ reading


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _ext(p: Path) -> str:
    return p.suffix.lower()


def is_readable(p: Path) -> bool:
    return _ext(p) in TEXT_EXT | BINARY_EXT or p.name.lower() in {"dockerfile", "makefile", "readme"}


def _rows_from_text(text: str, ext: str) -> tuple[list[str], list[list[str]]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|") if ext != ".tsv" else csv.excel_tab
    except csv.Error:
        dialect = csv.excel_tab if ext == ".tsv" else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    return (rows[0], rows[1:]) if rows else ([], [])


def _xlsx(path: Path) -> dict[str, tuple[list[str], list[list[Any]]]]:
    try:
        import openpyxl
    except ImportError as e:
        raise FileError("reading .xlsx needs openpyxl: pip install 'spendlens[ai]'") from e
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        rows = [["" if v is None else v for v in r] for r in ws.iter_rows(values_only=True)]
        while rows and not any(str(v).strip() for v in rows[-1]):
            rows.pop()
        out[ws.title] = ([str(v) for v in rows[0]], rows[1:]) if rows else ([], [])
    wb.close()
    return out


def _pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise FileError("reading .pdf needs pypdf: pip install 'spendlens[ai]'") from e
    reader = PdfReader(str(path))
    return "\n\n".join(f"--- page {i + 1} ---\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages))


def _docx(path: Path) -> str:
    try:
        import docx
    except ImportError as e:
        raise FileError("reading .docx needs python-docx: pip install 'spendlens[ai]'") from e
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        parts.append("\n".join(" | ".join(c.text.strip() for c in row.cells) for row in t.rows))
    return "\n".join(parts)


@dataclass
class InputFile:
    name: str
    path: Path
    text: str | None = None
    tables: dict[str, tuple[list[str], list[list[Any]]]] | None = None  # sheet -> (columns, rows)

    @classmethod
    def load(cls, path: Path, name: str | None = None) -> InputFile:
        p = path.expanduser()
        if not p.is_file():
            raise FileError(f"{path}: not a file")
        if p.stat().st_size > MAX_INPUT_BYTES:
            raise FileError(f"{path}: larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
        f = cls(name or p.name, p)
        ext = _ext(p)
        if ext in (".xlsx", ".xlsm"):
            f.tables = _xlsx(p)
        elif ext == ".pdf":
            f.text = _pdf(p)
        elif ext == ".docx":
            f.text = _docx(p)
        else:
            f.text = _decode(p.read_bytes())
            if ext in (".csv", ".tsv"):
                f.tables = {"": _rows_from_text(f.text, ext)}
        return f

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": _ext(self.path).lstrip(".") or "text",
                               "bytes": self.path.stat().st_size}
        if self.tables:
            out["tables"] = [{"sheet": s or None, "columns": c, "rows": len(r)} for s, (c, r) in self.tables.items()]
        if self.text is not None:
            out["chars"] = len(self.text)
        return out

    def read(self, *, offset: int = 0, limit: int = 15000, sheet: str | None = None, max_rows: int = 200
             ) -> dict[str, Any]:
        if self.tables and (sheet is not None or self.text is None or _ext(self.path) in (".csv", ".tsv")):
            key = sheet if sheet is not None else next(iter(self.tables))
            if key not in self.tables:
                raise FileError(f"no sheet {sheet!r}; sheets: {', '.join(self.tables)}")
            cols, rows = self.tables[key]
            part = rows[offset: offset + max_rows]
            return {"name": self.name, "sheet": key or None, "columns": cols, "rows": part, "offset": offset,
                    "total_rows": len(rows), "more": offset + len(part) < len(rows), "row_count": len(part)}
        text = self.text or ""
        part = text[offset: offset + limit]
        return {"name": self.name, "offset": offset, "text": part, "total_chars": len(text),
                "more": offset + len(part) < len(text), "next_offset": offset + len(part)}


def paths_in(question: str, base: Path | None = None) -> list[Path]:
    """Existing readable files named in a question ("summarise ./bill.csv")."""
    base = base or Path.cwd()
    found: list[Path] = []
    for tok in question.split():
        raw = tok.strip("\"'`()[]{}<>,;:!?").rstrip(".")
        if "." not in raw or len(raw) > 4096:
            continue
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else base / p
        if p.is_file() and is_readable(p) and p.resolve() not in {f.resolve() for f in found}:
            found.append(p)
    return found


# ------------------------------------------------------------------ writing


def _cell(v: Any) -> Any:
    if v is None or isinstance(v, int | float | bool):
        return v
    if isinstance(v, dict | list):
        return json.dumps(v, default=str)
    return str(v)


def _md_table(cols: list[str], rows: list[list[Any]]) -> str:
    def esc(v: Any) -> str:
        return "" if v is None else str(_cell(v)).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(esc(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _html_table(title: str, cols: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape('' if v is None else str(_cell(v)))}</td>" for v in r)
                   + "</tr>" for r in rows)
    return ("<!doctype html><meta charset=utf-8><title>" + html.escape(title) + "</title><style>body{font:14px "
            "system-ui,sans-serif;margin:24px}table{border-collapse:collapse}th,td{border:1px solid #ccc;"
            "padding:4px 8px;text-align:left}th{background:#f3f3f3}</style><h1>" + html.escape(title) + "</h1>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>\n")


def _docx_from_markdown(text: str, tables: list[tuple[str, list[str], list[list[Any]]]]) -> bytes:
    import docx

    d = docx.Document()
    for line in text.splitlines():
        s = line.rstrip()
        if m := re.match(r"^(#{1,4})\s+(.*)", s):
            d.add_heading(m.group(2), level=len(m.group(1)))
        elif m := re.match(r"^\s*[-*]\s+(.*)", s):
            d.add_paragraph(m.group(1), style="List Bullet")
        elif m := re.match(r"^\s*\d+[.)]\s+(.*)", s):
            d.add_paragraph(m.group(1), style="List Number")
        elif s:
            d.add_paragraph(s)
    for title, cols, rows in tables:
        if title:
            d.add_heading(title, level=2)
        t = d.add_table(rows=1, cols=max(len(cols), 1))
        t.style = "Table Grid"
        for i, c in enumerate(cols):
            t.rows[0].cells[i].text = str(c)
        for r in rows:
            cells = t.add_row().cells
            for i, v in enumerate(r[: len(cols)]):
                cells[i].text = "" if v is None else str(_cell(v))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _xlsx_bytes(sheets: list[tuple[str, list[str], list[list[Any]]]]) -> bytes:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for i, (title, cols, rows) in enumerate(sheets):
        name = re.sub(r"[\[\]:*?/\\]", " ", title or f"Sheet{i + 1}")[:31] or f"Sheet{i + 1}"
        while name in used:
            name = f"{name[:28]}_{i + 1}"
        used.add(name)
        ws = wb.create_sheet(name)
        ws.append([str(c) for c in cols])
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append([_cell(v) for v in r])
        for j, c in enumerate(cols, start=1):
            width = max([len(str(c))] + [len(str(r[j - 1])) for r in rows[:200] if j - 1 < len(r)])
            ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = min(max(width + 2, 8), 60)
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


TABLE_EXT = (".csv", ".tsv", ".json", ".jsonl", ".md", ".html", ".yaml", ".yml", ".xlsx")


def table_data(ext: str, title: str, tables: list[tuple[str, list[str], list[list[Any]]]]) -> bytes | str:
    """Tables as a file of type ext (.csv .tsv .json .jsonl .md .html .yaml .xlsx). Only .xlsx and .md keep every
    table; the others write the first."""
    if ext == ".xlsx":
        return _xlsx_bytes(tables)
    _, cols, rows = tables[0]
    if ext in (".csv", ".tsv"):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter="\t" if ext == ".tsv" else ",", lineterminator="\n")
        w.writerow(cols)
        w.writerows([[_cell(v) for v in r] for r in rows])
        return buf.getvalue()
    if ext == ".json":
        return json.dumps([dict(zip(cols, r, strict=False)) for r in rows], default=str, indent=2) + "\n"
    if ext == ".jsonl":
        return "".join(json.dumps(dict(zip(cols, r, strict=False)), default=str) + "\n" for r in rows)
    if ext == ".md":
        return "".join((f"## {t}\n\n" if t else "") + _md_table(c, r) + "\n" for t, c, r in tables)
    if ext == ".html":
        return _html_table(title, cols, rows)
    if ext in (".yaml", ".yml"):
        import yaml

        return yaml.safe_dump([dict(zip(cols, [_cell(v) for v in r], strict=False)) for r in rows], sort_keys=False)
    raise FileError(f"table data cannot be written as {ext}; use csv, tsv, xlsx, json, jsonl, md, html or yaml, "
                    "or send content")


@dataclass
class OutputDir:
    root: Path
    written: list[Path] = field(default_factory=list)

    def _target(self, name: str) -> Path:
        name = (name or "").strip()
        parts = [p for p in name.replace("\\", "/").split("/") if p]
        if not parts or name.startswith(("/", "~")) or any(p in (".", "..") or not _NAME.match(p) for p in parts) \
                or len(parts) > 3:
            raise FileError("give a plain file name such as 'idle-volumes.csv' (letters, digits, - _ . and spaces; "
                            "at most two folders; no absolute paths or ..)")
        p = self.root.joinpath(*parts)
        if _ext(p) not in WRITE_EXT:
            raise FileError(f"unsupported type {_ext(p) or '(none)'}; use one of {', '.join(sorted(WRITE_EXT))}")
        stem, ext, i = p.stem, p.suffix, 1
        while p.exists():
            p = p.with_name(f"{stem}-{i}{ext}")
            i += 1
        return p

    def write(self, name: str, *, content: str | None = None,
              tables: list[tuple[str, list[str], list[list[Any]]]] | None = None) -> dict[str, Any]:
        p = self._target(name)
        ext = _ext(p)
        tables = tables or []
        title = p.stem.replace("-", " ").replace("_", " ")
        if ext == ".xlsx":
            if not tables:
                raise FileError("an .xlsx needs table data: columns + rows, or result_id")
            data: bytes | str = _xlsx_bytes(tables)
        elif ext == ".docx":
            if content is None and not tables:
                raise FileError("a .docx needs content (Markdown headings, lists, paragraphs) and/or table data")
            data = _docx_from_markdown(content or "", tables)
        elif tables and content is None:
            data = table_data(ext, title, tables)
        else:
            if content is None:
                raise FileError("give content (text) or table data (columns + rows, or result_id)")
            if ext == ".json":
                try:
                    json.loads(content)
                except ValueError as e:
                    raise FileError(f"content is not valid JSON: {e}") from e
            data = content if content.endswith("\n") else content + "\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data, encoding="utf-8")
        self.written.append(p)
        rows = sum(len(r) for _, _, r in tables) if tables else None
        return {"written": str(p), "bytes": p.stat().st_size, **({"rows": rows} if rows is not None else {})}
