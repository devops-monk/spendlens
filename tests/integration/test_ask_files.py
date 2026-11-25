"""ask reads attached files and writes the files it is asked for (stub LLM, demo history; no AWS)."""

import json
from pathlib import Path

from stubllm import StubLLM
from test_ask import WASTE_ALARMS, ask


def test_writes_an_xlsx_from_a_result_id(tmp_path: Path) -> None:
    out = tmp_path / "exports"
    script = [("tool", "history_query", {"sql": WASTE_ALARMS}),
              ("tool", "write_file", {"name": "alarms.xlsx", "result_id": "r1", "title": "Alarms"}),
              ("final", "Saved: {last}")]
    with StubLLM(script) as stub:
        r = ask(stub, "Export the waste alarm count to Excel", "--out-dir", str(out), "--json")
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout[r.stdout.index("{"):])
    assert [s["tool"] for s in doc["steps"]] == ["history_query", "write_file"] and all(s["ok"] for s in doc["steps"])
    import openpyxl

    ws = openpyxl.load_workbook(out / "alarms.xlsx")["Alarms"]
    assert [c.value for c in ws[1]] == ["alarms"] and ws.cell(2, 1).value == 14
    first = stub.requests[0]["body"]
    assert "write_file" in {t["function"]["name"] for t in first["tools"]}
    second = stub.requests[1]["body"]["messages"][-1]["content"]
    assert '"result_id":"r1"' in second  # the model is told which id to export


def test_reads_an_attached_file_and_a_path_in_the_question(tmp_path: Path) -> None:
    budget = tmp_path / "budget.csv"
    budget.write_text("service,budget\nEC2,100\nS3,20\n")
    notes = tmp_path / "notes.md"
    notes.write_text("Team alpha owns the EC2 fleet.\n")
    script = [("tool", "read_file", {"name": "budget.csv"}), ("final", "Read: {last}")]
    with StubLLM(script) as stub:
        r = ask(stub, f"Compare my budget with {notes} please", "-a", str(budget), "--json")
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout[r.stdout.index("{"):])
    assert '"EC2","100"' in doc["answer"].replace(" ", "")
    body = stub.requests[0]["body"]
    user = body["messages"][-1]["content"]
    assert "[Attached files" in user and "budget.csv" in user and "notes.md" in user and "Team alpha" in user
    assert "read_file" in {t["function"]["name"] for t in body["tools"]}


def test_missing_attachment_is_a_usage_error(tmp_path: Path) -> None:
    with StubLLM([("final", "x")]) as stub:
        r = ask(stub, "hi", "-a", str(tmp_path / "nope.csv"))
    import re

    flat = re.sub(r"\x1b\[[0-9;]*m|[\s│]", "", r.output)  # the error box wraps long temp paths
    assert r.exit_code == 2 and "notafile" in flat
