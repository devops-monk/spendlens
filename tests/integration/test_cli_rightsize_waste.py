import json

from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.config import PricingConfig
from spendlens.features import controls as controls_feature
from spendlens.features.rightsize import FEATURE as RIGHTSIZE
from spendlens.models import Finding, Status

runner = CliRunner()
P = PricingConfig()




def F(control, status="alarm", **dims):
    return Finding(control=control, resource=f"arn:{control}", status=Status(status),
                   dimensions={k: str(v) for k, v in dims.items()})


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

