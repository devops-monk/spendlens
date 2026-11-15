import csv
import io
import json

import pytest

from spendlens.models import CostDelta, Finding, ReportMeta, Status
from spendlens.render import Format, render

META = ReportMeta(title="t")


def _findings():
    return [
        Finding(control="mandatory_tags", resource="arn:aws:s3:::b1", status=Status.ALARM,
                reason="missing Environment", tags={"Team": "x"}),
        Finding(control="mandatory_tags", resource="arn:aws:s3:::b2", status=Status.OK),
    ]


def test_csv_findings():
    text = render(_findings(), Format.csv, meta=META)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 2
    assert rows[0]["status"] == "alarm"
    assert json.loads(rows[0]["tags"]) == {"Team": "x"}
    assert rows[1]["tags"] == ""


def test_json_envelope():
    doc = json.loads(render(_findings(), Format.json, meta=META))
    assert doc["count"] == 2 and doc["meta"]["title"] == "t"
    assert doc["rows"][1]["status"] == "ok"


def test_cost_delta_flattened():
    d = CostDelta(group="Amazon EC2", previous=100, current=150)
    doc = json.loads(render([d], Format.json, meta=META))
    assert doc["rows"][0] == {"group": "Amazon EC2", "previous": 100.0, "current": 150.0,
                              "change": 50.0, "change_pct": 50.0, "currency": "USD"}
    assert CostDelta(group="x", previous=0, current=5).change_pct is None


def test_table_writes_file(tmp_path):
    out = tmp_path / "t.txt"
    render(_findings() + [{"extra": 1}], Format.table, meta=META, out=out)
    assert "arn:aws:s3:::b1" in out.read_text()


def test_html_not_yet():
    with pytest.raises(NotImplementedError):
        render([], Format.html, meta=META)
