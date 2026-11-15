"""End-to-end: real spendlens CLI -> Powerpipe -> Steampipe -> aws plugin -> Moto, with seeded resources."""

from __future__ import annotations

import boto3
import pytest
from seed import EXPECTATIONS

pytestmark = pytest.mark.system


@pytest.mark.parametrize(("feature", "control", "resource_key", "want"), EXPECTATIONS,
                         ids=[f"{f}:{c}:{r}" for f, c, r, _ in EXPECTATIONS])
def test_seeded_resource_gets_expected_status(system_env, live_results, feature, control, resource_key, want):
    needle = system_env["ids"].get(resource_key, resource_key)
    rows = [r for r in live_results[feature] if r["control"] == control and needle in r["resource"]]
    errors = [r["reason"] for r in live_results[feature] if r["control"] == control and r["status"] == "error"]
    assert rows, f"no {control} row for {needle}; control errors: {errors}"
    assert rows[0]["status"] == want, rows[0]["reason"]


def test_no_unexpected_sql_errors(live_results):
    """Controls may error only where the emulator lacks the API (FDW error SQLSTATE HV000), never from our SQL
    (e.g. 22P02 invalid input, 42703 undefined column, 42883 undefined function)."""
    sql_errors = [(f, r["control"], r["reason"]) for f, rows in live_results.items() for r in rows
                  if r["status"] == "error" and "SQLSTATE" in r["reason"] and "SQLSTATE HV" not in r["reason"]]
    assert not sql_errors, sql_errors


def test_savings_are_estimated(live_results):
    by = {(r["control"]): r for r in live_results["waste"] if r["status"] == "alarm"}
    assert by["vpc_eip_unattached"]["est_monthly_saving"] == 3.65
    assert by["ebs_volume_unattached"]["est_monthly_saving"] > 0


def test_doctor_sees_emulated_account(spendlens):
    _, out, _ = spendlens("doctor", expect_json=False)
    assert "123456789012" in out, out  # Moto's account: proves the aws CLI used the emulator, not real AWS
    assert "steampipe service" in out and "aws tables" in out


def test_formats_and_replay_from_history(spendlens, system_env):
    code, csv_text, _ = spendlens("waste", "-S", "ebs", "-f", "csv", expect_json=False)
    assert code == 0 and csv_text.splitlines()[0].startswith("status,est_monthly_saving,service")
    out = system_env["work"] / "rightsize.json"
    code, _, _ = spendlens("rightsize", "--summary", "-f", "json", "-o", str(out), expect_json=False)
    assert code == 0 and out.exists()
    code, doc, _ = spendlens("rightsize", "--source", "last", "-f", "json")
    assert code == 0 and doc["meta"]["extra"]["source"] == "last"


def test_ci_gate_exit_code(spendlens):
    code, _, err = spendlens("tags", "-k", "mandatory", "--fail-on-alarm", "-f", "json")
    assert code == 3, err


def test_fixing_tags_shows_as_resolved_in_history_diff(spendlens, system_env):
    """Scenario: a team tags its instance; the next run + history diff shows the alarm as resolved."""
    spendlens("tags", "-k", "mandatory", "-r", "ec2_instance", "-f", "json")
    ec2 = boto3.client("ec2", region_name="us-east-1", endpoint_url=system_env["endpoint"],
                       aws_access_key_id="testing", aws_secret_access_key="testing")
    ec2.create_tags(Resources=[system_env["ids"]["ec2_bad"]],
                    Tags=[{"Key": "Environment", "Value": "dev"}, {"Key": "Service Owner", "Value": "x@example.com"}])
    code, doc, _ = spendlens("tags", "-k", "mandatory", "-r", "ec2_instance", "--status", "all", "-f", "json")
    assert code == 0
    row = next(r for r in doc["rows"] if system_env["ids"]["ec2_bad"] in r["resource"])
    assert row["status"] == "ok"
    code, diff, _ = spendlens("history", "diff", "--feature", "tags", "-f", "json")
    assert code == 0
    assert any(r["change"] == "resolved" and system_env["ids"]["ec2_bad"] in r["resource"] for r in diff["rows"])
