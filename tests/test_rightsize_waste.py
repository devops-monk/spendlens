import json
import sqlite3

import pytest
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.config import PricingConfig
from spendlens.features import controls as controls_feature
from spendlens.features.rightsize import FEATURE as RIGHTSIZE
from spendlens.features.savings import apply_estimates, total_saving
from spendlens.history import HistoryStore
from spendlens.models import Finding, Status

runner = CliRunner()
P = PricingConfig()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPENDLENS_CONFIG", raising=False)
    monkeypatch.setenv("SPENDLENS__HISTORY__PATH", str(tmp_path / "h.db"))
    monkeypatch.setattr("spendlens.common.caller_identity", lambda: {"Account": "999", "Arn": "arn:test"})


def F(control, status="alarm", **dims):
    return Finding(control=control, resource=f"arn:{control}", status=Status(status),
                   dimensions={k: str(v) for k, v in dims.items()})


def est(f, extra=None, pricing=P):
    return apply_estimates([f], pricing, extra)[0].est_monthly_saving


# ------------------------------------------------------------------ estimators


def test_ebs_estimates():
    assert est(F("ebs_volume_gp2", size_gb=100, volume_type="gp2")) == 2.0  # 100 * (0.10 - 0.08)
    assert est(F("ebs_volume_unattached", size_gb=100, volume_type="gp3")) == 8.0
    # io1 200 GB @ 10k IOPS: 25 + 650 = 675; gp3: 16 + 7000 * 0.005 = 51
    assert est(F("ebs_volume_piops_to_gp3", size_gb=200, volume_type="io1", iops=10000)) == 624.0
    assert est(F("ebs_snapshot_old", size_gb=100)) == 5.0


def test_compute_estimates():
    assert est(F("ec2_instance_low_utilization", instance_type="m5.xlarge")) == round(0.192 * 730 * 0.5, 2)
    assert est(F("ec2_instance_low_utilization", instance_type="x9.unknown")) is None
    assert est(F("rds_instance_low_connections", db_class="db.m5.large", multi_az="true")) == round(0.171 * 730 * 2, 2)
    # Multi-AZ policy only saves money when Multi-AZ is currently on
    assert est(F("rds_instance_multi_az_policy", db_class="db.m5.large", multi_az="false")) is None
    assert est(F("rds_instance_multi_az_policy", db_class="db.m5.large", multi_az="true")) == round(0.171 * 730, 2)


def test_fixed_price_estimates_and_non_alarms():
    assert est(F("vpc_eip_unattached")) == 3.65
    assert est(F("vpc_nat_gateway_unused")) == 32.85
    assert est(F("ec2_application_load_balancer_unused", lb_type="application")) == 16.43
    assert est(F("vpc_eip_unattached", status="ok")) is None
    assert est(F("no_estimator_control")) is None


def test_plugin_estimator_and_pricing_override():
    assert est(F("custom_ctl"), {"custom_ctl": lambda f, p: 12.5}) == 12.5
    assert est(F("custom_ctl"), {"custom_ctl": lambda f, p: 1 / 0}) is None  # broken estimator is ignored
    assert est(F("vpc_eip_unattached"), pricing=PricingConfig(eip_hour=0.01)) == 7.3
    assert total_saving([]) == 0


# ------------------------------------------------------------------ commands


def test_waste_demo_summary_has_savings():
    r = runner.invoke(app, ["waste", "--source", "demo", "--summary", "--by", "service", "-f", "json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    assert doc["meta"]["extra"]["est_monthly_saving_total"] > 0
    assert any(row["service"] == "ebs" and row["est_monthly_saving"] for row in doc["rows"])


def test_rightsize_demo_filters():
    r = runner.invoke(app, ["rightsize", "--source", "demo", "-S", "ebs", "--min-saving", "100", "-f", "json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)["rows"]
    assert rows and all(x["service"] == "ebs" and x["est_monthly_saving"] >= 100 for x in rows)
    savings = [x["est_monthly_saving"] for x in rows]
    assert savings == sorted(savings, reverse=True)


def test_rightsize_gate_and_bad_filters():
    assert runner.invoke(app, ["rightsize", "--source", "demo", "--fail-on-alarm"]).exit_code == 3
    r = runner.invoke(app, ["rightsize", "--source", "demo", "-S", "nope"])
    assert r.exit_code == 2 and "Unknown service" in r.output
    r = runner.invoke(app, ["waste", "--source", "demo", "-C", "ebs_volume_gp2"])  # a rightsize control
    assert r.exit_code == 2 and "Unknown control" in r.output
    assert runner.invoke(app, ["waste", "--source", "demo", "--summary", "--by", "colour"]).exit_code == 2


def test_resolve_filters_where_clause():
    assert controls_feature.resolve_filters(RIGHTSIZE, [], []) is None
    assert controls_feature.resolve_filters(RIGHTSIZE, ["rds"], ["ebs_volume_gp2"]) == "tags->>'service' in ('ebs', 'rds')"


def test_live_run_applies_estimates_and_saves(monkeypatch):
    seen = {}

    def fake(feat, cfg, where):
        seen["where"], seen["feature"] = where, feat.name
        f = F("ebs_volume_gp2", size_gb=500, volume_type="gp2")
        return [f.model_copy(update={"service": "ebs", "check": "rightsize"})]

    monkeypatch.setattr(controls_feature, "run_live", fake)
    r = runner.invoke(app, ["rightsize", "-S", "ebs", "-f", "json", "--set", "pricing.ebs_gb_month={gp2: 0.12, gp3: 0.08}"])
    assert r.exit_code == 0, r.output
    assert seen == {"where": "tags->>'service' in ('ebs')", "feature": "rightsize"}
    assert json.loads(r.stdout)["rows"][0]["est_monthly_saving"] == 20.0  # 500 * (0.12 - 0.08)
    r = runner.invoke(app, ["rightsize", "--source", "last", "-f", "json"])
    assert json.loads(r.stdout)["rows"][0]["est_monthly_saving"] == 20.0


def test_controls_subcommand_lists_controls():
    r = runner.invoke(app, ["waste", "controls", "-f", "json"])
    names = {x["control"] for x in json.loads(r.stdout)["rows"]}
    assert {"ebs_volume_unattached", "vpc_nat_gateway_unused"} <= names and "ebs_volume_gp2" not in names


# ------------------------------------------------------------------ history migration


def test_history_v1_db_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
      create table runs (id integer primary key autoincrement, feature text not null, started_at text not null,
        account_id text, caller_arn text, params text not null default '{}', meta text not null default '{}');
      create table findings (run_id integer not null, control text not null, title text, "check" text,
        resource_type text, resource text not null, account_id text, region text, status text not null,
        reason text, severity text, est_monthly_saving real, tags text, dimensions text);
      create table cost_rows (run_id integer not null, kind text not null, period text not null,
        dimension text not null, grp text not null, amount real not null, currency text not null default 'USD');
      insert into runs(feature, started_at) values ('tags', '2026-01-01T00:00:00+00:00');
      insert into findings(run_id, control, resource, status) values (1, 'c', 'r', 'ok');
      pragma user_version = 1;
    """)
    con.close()
    with HistoryStore(db) as s:
        assert s.findings(1)[0].service is None
        rid = s.save_run("rightsize", [Finding(control="c", resource="r", status=Status.OK, service="ec2")])
        assert s.findings(rid)[0].service == "ec2"
        assert s.conn.execute("pragma user_version").fetchone()[0] == 2
