"""The `--format html` findings report for control-based commands (tags / rightsize / waste)."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.render.html import findings_model, is_findings

pytestmark = pytest.mark.integration
runner = CliRunner()


def _rows():
    return [
        {"control": "c1", "title": "Check one", "service": "ec2", "resource": "arn:aws:ec2:r:1:instance/i-1",
         "status": "ok", "reason": "fine"},
        {"control": "c1", "title": "Check one", "service": "ec2", "resource": "arn:aws:ec2:r:1:instance/i-2",
         "status": "alarm", "reason": "big", "est_monthly_saving": 40.0},
        {"control": "c2", "title": "Check two", "service": "rds", "resource": "arn:aws:rds:r:1:db:d-1",
         "status": "alarm", "reason": "idle", "est_monthly_saving": 120.0},
        {"control": "c3", "title": "Check three", "service": "rds", "resource": "arn:aws:rds:r:1:db:d-2",
         "status": "ok", "reason": "fine"},
    ]


def test_model_orders_worst_first_and_totals():
    m = findings_model(_rows())
    assert [c["id"] for c in m["controls"]] == ["c2", "c1", "c3"]  # alarms first, larger saving first
    assert m["totals"]["alarm"] == 2 and m["totals"]["ok"] == 2
    assert m["compliance"] == 50.0
    assert m["saving"] == 160.0 and m["saving_text"] == "$160"
    assert m["top"][0]["name"] == "d-1"                              # short name from the ARN
    c1 = next(c for c in m["controls"] if c["id"] == "c1")
    assert [r["status"] for r in c1["rows"]] == ["alarm", "ok"]      # alarms first within a check
    assert {r["i"] for c in m["controls"] for r in c["rows"]} == {0, 1, 2, 3}  # row -> embedded record index


def test_only_findings_get_the_report():
    assert is_findings(_rows())
    assert not is_findings([{"service": "ec2", "total": 3}])          # --summary rows keep the plain table
    assert not is_findings([])


def test_cli_html_is_findings_report_and_self_contained(tmp_path):
    out = tmp_path / "r.html"
    res = runner.invoke(app, ["rightsize", "--source", "demo", "--status", "all", "-f", "html", "-o", str(out)])
    assert res.exit_code == 0, res.output
    html = out.read_text()
    assert 'class="fr"' in html and "Download CSV" in html and "Largest savings" in html
    assert not re.search(r"(src|href)=\"https?://", html)               # works offline / as a CI artifact
    data = json.loads(re.search(r'<script type="application/json" id="spendlens-data">(.*?)</script>', html, re.DOTALL)
                      .group(1))
    cols = json.loads(re.search(r'id="spendlens-csv-columns">(.*?)</script>', html, re.DOTALL).group(1))
    assert cols[:3] == ["status", "est_monthly_saving", "service"]     # Download CSV uses the --format csv columns
    sections = html.count('<details class="ctl ')
    assert sections == len({r["control"] for r in data["rows"]})
    assert html.count("data-i=") == len(data["rows"])                    # every result is listed exactly once


def test_summary_html_falls_back_to_table(tmp_path):
    out = tmp_path / "s.html"
    res = runner.invoke(app, ["waste", "--source", "demo", "--summary", "-f", "html", "-o", str(out)])
    assert res.exit_code == 0, res.output
    html = out.read_text()
    assert 'class="fr"' not in html and "sortable" in html
