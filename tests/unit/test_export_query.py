"""export_query: every row of a query written to a file, none of them sent to the model (fakes only)."""

import json
from typing import Any

import openpyxl
import pytest

from spendlens.ai.files import OutputDir
from spendlens.ai.tools import Toolbox
from spendlens.config import load_config
from spendlens.history import HistoryStore
from spendlens.models import Finding, Status


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "h.db"
    with HistoryStore(p) as s:
        s.save_run("waste", [Finding(control="ebs_unattached", resource=f"vol-{i}", status=Status.ALARM,
                                     est_monthly_saving=float(i), service="ebs") for i in range(1200)])
    return p


class Client:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows, self.sql = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def query(self, sql: str, params: Any = None, *, limit: int | None = None, **kw: Any) -> list[dict[str, Any]]:
        self.sql.append(sql)
        return self.rows[: limit or len(self.rows)]


def test_history_export_writes_every_row_and_sends_only_a_preview(db, tmp_path) -> None:
    cfg = load_config(environ={}, sets=["llm.max_rows=10"])
    tb = Toolbox(cfg, db, outdir=OutputDir(tmp_path / "out"))
    assert "export_query" in {s.name for s in tb.specs()}
    r = tb.execute("1", "export_query", {"sql": "select resource, est_monthly_saving from findings",
                                         "name": "volumes.xlsx", "engine": "history"})
    out = json.loads(r.content)
    assert not r.is_error and out["rows_written"] == 1200 and len(out["first_rows"]) == 5
    assert len(r.content) < 2000  # the rows never reach the model
    ws = openpyxl.load_workbook(out["written"]).active
    assert ws.max_row == 1201 and ws["A1"].value == "resource"


def test_aws_export_is_checked_capped_and_needs_live(db, tmp_path) -> None:
    cfg = load_config(environ={}, sets=["llm.export_max_rows=3"])
    rows = [{"instance_id": f"i-{i}", "instance_type": "t3.micro"} for i in range(5)]
    tb = Toolbox(cfg, db, live=True, outdir=OutputDir(tmp_path / "out"))
    tb._live_columns = lambda: None  # type: ignore[method-assign]
    client = Client(rows)
    tb._client = lambda: client  # type: ignore[method-assign]
    out = json.loads(tb.execute("1", "export_query", {"sql": "select instance_id, instance_type from aws_ec2_instance",
                                                      "name": "i.csv", "engine": "aws"}).content)
    assert out["rows_written"] == 3 and out["truncated"] and "export_max_rows" in out["note"]
    assert "limit 4" in client.sql[0]
    bad = tb.execute("2", "export_query", {"sql": "select nope from aws_ec2_instance", "name": "x.csv", "engine": "aws"})
    assert bad.is_error and "not run" in bad.content
    off = Toolbox(cfg, db, outdir=OutputDir(tmp_path / "o2"))
    spec = next(s for s in off.specs() if s.name == "export_query")
    assert spec.parameters["properties"]["engine"]["enum"] == ["history"]  # no live AWS: history only
    assert off.execute("3", "export_query", {"sql": "select 1 from aws_ec2_instance", "name": "x.csv",
                                             "engine": "aws"}).is_error


def test_no_rows_no_file(db, tmp_path) -> None:
    tb = Toolbox(load_config(environ={}), db, outdir=OutputDir(tmp_path / "out"))
    out = json.loads(tb.execute("1", "export_query", {"sql": "select * from findings where 1 = 0", "name": "e.csv",
                                                      "engine": "history"}).content)
    assert "no file" in out["note"] and not (tmp_path / "out").exists()
