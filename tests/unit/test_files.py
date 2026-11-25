"""Attached files the assistant reads, and the files it writes (all local; no AWS, no LLM)."""

import json
from pathlib import Path

import pytest

from spendlens.ai.files import FileError, InputFile, OutputDir, paths_in


def test_csv_reads_as_rows_with_paging(tmp_path: Path) -> None:
    p = tmp_path / "inventory.csv"
    p.write_text("id,team\n" + "".join(f"i-{n},t{n % 3}\n" for n in range(12)))
    f = InputFile.load(p)
    assert f.summary()["tables"] == [{"sheet": None, "columns": ["id", "team"], "rows": 12}]
    page = f.read(offset=10, max_rows=5)
    assert page["rows"] == [["i-10", "t1"], ["i-11", "t2"]] and page["more"] is False and page["total_rows"] == 12


def test_text_pages_by_characters(tmp_path: Path) -> None:
    p = tmp_path / "notes.md"
    p.write_text("a" * 30)
    f = InputFile.load(p)
    first = f.read(limit=20)
    assert first["more"] and first["next_offset"] == 20 and f.read(offset=20, limit=20)["text"] == "a" * 10


def test_xlsx_and_docx_round_trip(tmp_path: Path) -> None:
    out = OutputDir(tmp_path / "out")
    x = out.write("budget.xlsx", tables=[("Budget", ["service", "amount"], [["EC2", 120.5], ["S3", 30]]),
                                         ("Notes", ["n"], [["ok"]])])
    f = InputFile.load(Path(x["written"]))
    assert f.tables is not None and list(f.tables) == ["Budget", "Notes"]
    assert f.read(sheet="Budget")["rows"] == [["EC2", 120.5], ["S3", 30]]
    d = out.write("summary.docx", content="# Idle volumes\n\n- vol-1 costs 8.00\n\nPlain text.",
                  tables=[("Detail", ["id", "cost"], [["vol-1", 8.0]])])
    text = InputFile.load(Path(d["written"])).text or ""
    assert "Idle volumes" in text and "vol-1 costs 8.00" in text and "vol-1 | 8.0" in text


@pytest.mark.parametrize(("name", "check"), [
    ("t.csv", lambda s: s.splitlines() == ["a,b", "1,x"]),
    ("t.json", lambda s: json.loads(s) == [{"a": 1, "b": "x"}]),
    ("t.md", lambda s: "| a | b |" in s and "| 1 | x |" in s),
    ("t.html", lambda s: "<th>a</th>" in s and "<td>x</td>" in s),
    ("t.yaml", lambda s: "a: 1" in s),
])
def test_table_formats(tmp_path: Path, name: str, check) -> None:
    w = OutputDir(tmp_path).write(name, tables=[("", ["a", "b"], [[1, "x"]])])
    assert check(Path(w["written"]).read_text())


def test_never_overwrites_and_stays_inside(tmp_path: Path) -> None:
    out = OutputDir(tmp_path)
    (tmp_path / "plan.md").write_text("mine")
    w = out.write("plan.md", content="new")
    assert w["written"].endswith("plan-1.md") and (tmp_path / "plan.md").read_text() == "mine"
    assert out.write("scripts/cleanup.sh", content="echo hi")["written"].endswith("scripts/cleanup.sh")
    for bad in ("../escape.md", "/etc/x.md", "~/x.md", "a/../../b.md", "run.exe", "noext"):
        with pytest.raises(FileError):
            out.write(bad, content="x")
    with pytest.raises(FileError, match="not valid JSON"):
        out.write("bad.json", content="{nope")
    with pytest.raises(FileError, match="needs table data"):
        out.write("empty.xlsx", content="x")


def test_paths_in_question(tmp_path: Path) -> None:
    (tmp_path / "bill.csv").write_text("a\n1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "plan.yaml").write_text("x: 1\n")
    got = paths_in("compare ./bill.csv with sub/plan.yaml, and missing.csv please", tmp_path)
    assert [p.name for p in got] == ["bill.csv", "plan.yaml"]
    assert paths_in("what does vol-0abc.123 cost?", tmp_path) == []
