"""Cost reports explain themselves: the flagged rule everywhere, "How to read this" guides, charts (demo data)."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.config import Config
from spendlens.features.guides import flag_rule

pytestmark = pytest.mark.integration
runner = CliRunner()


def _html(tmp_path, *args):
    out = tmp_path / "r.html"
    r = runner.invoke(app, [*args, "--source", "demo", "-f", "html", "-o", str(out)])
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert not re.search(r"(src|href)=\"https?://", html)  # still self-contained
    return html


def test_flag_rule_states_the_configured_thresholds():
    assert "at least $10.00 AND at least 20%" in flag_rule(Config())
    only_abs = flag_rule(Config.model_validate({"cost": {"change_alarm_pct": 0}}))
    assert "at least $10.00, whatever its percentage" in only_abs
    only_pct = flag_rule(Config.model_validate({"cost": {"change_alarm_abs": 0}}))
    assert "at least 20%, whatever its size" in only_pct


def test_cost_diff_terminal_states_the_rule():
    r = runner.invoke(app, ["cost", "diff", "--source", "demo"])
    assert r.exit_code == 0 and "Flagged = a change is flagged when" in r.output


def test_cost_diff_html_is_a_full_report(tmp_path):
    html = _html(tmp_path, "cost", "diff")
    for part in ("How to read this", "<b>Flagged</b> =", "Flagged changes", "Biggest increase", "Biggest decrease",
                 "Where the money goes", "<svg"):
        assert part in html, part
    assert "Before" in html and "After" in html and "⚑ flagged" in html


@pytest.mark.parametrize("cmd", [["cost", "trend"], ["cost", "showback"], ["anomalies"], ["forecast"]])
def test_other_cost_reports_have_chart_and_guide(tmp_path, cmd):
    html = _html(tmp_path, *cmd)
    assert "How to read this" in html
    assert "<svg" in html or 'class="meter' in html


def test_dashboard_explains_every_tab(tmp_path):
    out = tmp_path / "d.html"
    r = runner.invoke(app, ["dashboard", "--source", "demo", "-o", str(out)])
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert html.count("How to read this") >= 9          # overview, cost, trend, showback x2, anomalies, forecast, …
    assert "≥ $10.00 and ≥ 20% vs" in html               # the flagged tile states its rule
