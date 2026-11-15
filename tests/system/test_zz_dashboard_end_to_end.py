"""Runs last (file name): build the dashboard from the history written by the other end-to-end tests."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.system


def test_dashboard_from_live_history(system_env, spendlens):
    out = system_env["work"] / "report.html"
    code, _, err = spendlens("dashboard", "-o", str(out), expect_json=False)
    assert code == 0, err
    html = out.read_text()
    assert "AWS account 123456789012" in html and "DEMO DATA" not in html
    ids = system_env["ids"]
    assert ids["vol_gp2"] in html  # a seeded finding made it into the report
    assert "e2e-legacy-db" in html
    for tab in ("tags", "rightsize", "waste", "anomalies", "forecast", "cost"):
        assert f'id="tab-{tab}"' in html
