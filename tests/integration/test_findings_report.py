"""The `--format html` findings report for control-based commands (tags / rightsize / waste)."""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.render.html import _short, findings_model, is_findings

pytestmark = pytest.mark.integration
runner = CliRunner()

INDEX = {"c1": {"title": "Check one", "description": "Peak CPU at or below sizing.x_low.", "variables": ["x_low"]}}
SETTINGS = {"x_low": "sizing.x_low"}


def _rows():
    return [
        {"control": "c1", "title": "Check one", "service": "ec2", "resource": "arn:aws:ec2:r:1:instance/i-1",
         "status": "ok", "reason": "fine", "region": "eu-west-1"},
        {"control": "c1", "title": "Check one", "service": "ec2", "resource": "arn:aws:ec2:r:1:instance/i-2",
         "status": "alarm", "reason": "big", "est_monthly_saving": 40.0, "region": "eu-west-2"},
        {"control": "c2", "title": "Check two", "service": "rds", "resource": "arn:aws:rds:r:1:db:d-1",
         "status": "alarm", "reason": "idle", "est_monthly_saving": 120.0, "region": "eu-west-1"},
        {"control": "c3", "title": "Check three", "service": "rds", "resource": "arn:aws:rds:r:1:db:d-2",
         "status": "ok", "reason": "fine", "region": "eu-west-1"},
    ]


def _model(rows=None, **kw):
    return findings_model(rows or _rows(), index=INDEX, settings=SETTINGS, values={"x_low": 25}, **kw)


def test_model_orders_worst_first_and_totals():
    m = _model()
    assert [c["id"] for c in m["controls"]] == ["c2", "c1", "c3"]  # failing first, larger saving first
    assert m["totals"]["alarm"] == 2 and m["totals"]["ok"] == 2
    assert m["compliance"] == 50.0 and m["checks_failing"] == 2 and m["failing_resources"] == 2
    assert m["saving"] == 160.0 and m["saving_text"] == "$160"
    assert m["top"][0]["name"] == "d-1"
    assert m["regions"] == ["eu-west-1", "eu-west-2"]


def test_each_check_states_threshold_and_fail_ratio():
    c1 = next(c for c in _model()["controls"] if c["id"] == "c1")
    assert c1["thresholds"] == [{"setting": "sizing.x_low", "value": "25"}]  # the value this run used
    assert c1["description"].startswith("Peak CPU")
    assert (c1["fail"], c1["total"], c1["fail_pct"]) == (1, 2, 50.0)
    assert [r["status"] for r in c1["preview"]] == ["alarm", "ok"]  # failures first within a check


def test_payload_is_compact_and_preview_is_capped():
    rows = [{"control": "big", "title": "Big", "service": "logs", "status": "alarm", "reason": f"r{i}",
             "resource": f"arn:aws:logs:eu-west-1:1:log-group:/aws/g{i}", "tags": {"k": "v" * 50}} for i in range(500)]
    m = _model(rows, preview=50)
    c = m["controls"][0]
    assert len(c["preview"]) == 50 and c["total"] == 500                 # only 50 rows go into the HTML
    p = m["payload"]
    assert len(p["rows"]) == 500 and len(p["rows"][0]) == len(p["row_fields"])  # one compact array per result
    assert "tags" not in json.dumps(p)                                   # no tags/dimensions bloat
    assert c["preview"][0]["name"].startswith("/aws/g")


def test_short_names_keep_the_useful_part_of_the_arn():
    assert _short("arn:aws:logs:eu-west-1:1:log-group:/aws/ecs/app") == "/aws/ecs/app"
    assert _short("arn:aws:ecs:eu-west-1:1:service/cluster/svc") == "cluster/svc"
    assert _short("arn:aws:s3:::bucket") == "bucket"
    assert _short("arn:aws:rds:eu-west-1:1:db:mydb") == "mydb"
    assert _short("not-an-arn") == "not-an-arn"


def test_only_findings_get_the_report():
    assert is_findings(_rows())
    assert not is_findings([{"service": "ec2", "total": 3}])          # --summary rows keep the plain table
    assert not is_findings([])


def test_cli_html_is_findings_report_and_self_contained(tmp_path):
    out = tmp_path / "r.html"
    res = runner.invoke(app, ["rightsize", "--source", "demo", "--status", "all", "-f", "html", "-o", str(out)])
    assert res.exit_code == 0, res.output
    html = out.read_text()
    assert 'class="fr"' in html and "Download CSV" in html and 'class="fr-overview"' in html
    assert not re.search(r"(src|href)=\"https?://", html)               # works offline / as a CI artifact
    data = json.loads(re.search(r'<script type="application/json" id="spendlens-data">(.*?)</script>', html,
                                re.DOTALL).group(1))
    assert data["columns"][:3] == ["status", "est_monthly_saving", "service"]  # Download CSV = --format csv columns
    sections = html.count('<details class="ctl ')
    assert sections == len(data["controls"]) == html.count('class="ov-row ')  # overview lists every check
    assert sum(1 for r in data["rows"]) >= sections
    assert "Threshold" in html and "sizing." in html                     # thresholds from finops.yaml are shown


def test_summary_html_falls_back_to_table(tmp_path):
    out = tmp_path / "s.html"
    res = runner.invoke(app, ["waste", "--source", "demo", "--summary", "-f", "html", "-o", str(out)])
    assert res.exit_code == 0, res.output
    html = out.read_text()
    assert 'class="fr"' not in html and "sortable" in html
