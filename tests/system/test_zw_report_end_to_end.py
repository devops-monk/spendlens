"""spendlens report against Moto: every step runs live; steps the emulator can't serve fail without stopping the rest."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.system

# Moto has no Cost Optimization Hub API, so that step is expected to fail; everything else must succeed.
EMULATOR_GAPS = {"savings_hub"}


def test_report_runs_everything_and_survives_failures(system_env, spendlens):
    out = system_env["work"] / "reports"
    code, _, err = spendlens("report", "--out", str(out), "--set", "budgets.monthly_total=2500", expect_json=False)
    steps = {s["step"]: s for s in json.loads((out / "report-steps.json").read_text())}
    failed = {name for name, s in steps.items() if s["result"] == "failed"}
    assert failed <= EMULATOR_GAPS, {n: steps[n]["detail"] for n in failed}
    assert code == (1 if failed else 0), err
    for name in ("tags", "rightsize", "waste", "cost_diff", "anomalies", "forecast", "savings_commitments"):
        assert steps[name]["result"] in ("ok", "alarms"), (name, steps[name]["detail"])
        assert json.loads((out / f"{name}.json").read_text())["meta"]["extra"]["source"] == "live"
    assert steps["dashboard"]["result"] == "ok" and (out / "report.html").stat().st_size > 10_000
    waste = json.loads((out / "waste.json").read_text())
    assert any(system_env["ids"]["vol_gp2"] in r["resource"] for r in waste["rows"])
