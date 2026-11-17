"""spendlens brief: the executive report, with the rule-based summary and with the AI analyst (real OpenAI SDK
against the local stub LLM; no network, no AWS)."""

import json
import re

import pytest
from stubllm import StubLLM
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.features import brief as br

runner = CliRunner()

AI_JSON = {
    "headline": "Spend rose 1.8% to $55.4K, led by EC2; the month is forecast over budget.",
    "summary": "EC2 compute grew by $3,698 while Redshift was retired.",
    "insights": [{"title": "EC2 drove the increase", "detail": "+$3,698 vs September.", "impact": "HIGH",
                  "area": "cost"},
                 {"title": "Budget at risk", "detail": "Forecast $59.1K vs $55.0K.", "impact": "urgent",
                  "area": "Forecast / budget"}],
    "actions": [{"action": "Buy a 1-year Compute Savings Plan", "why": "AWS recommends it",
                 "est_monthly_saving": "$1,240", "effort": "low", "owner": "platform"},
                {"action": "Impossible saving", "est_monthly_saving": 999999}],
    "risks": ["Over budget by month end"],
    "anomaly_notes": [{"date": "2026-11-07", "group": "Amazon Elastic Compute Cloud - Compute",
                       "explanation": "A batch fleet scaled out"}],
}


def run(*args, env=None):
    return runner.invoke(app, ["brief", *args], env={"SPENDLENS_NO_PROGRESS": "1", **(env or {})})


def stub_sets(stub):
    return ["--set", "llm.provider=openai_compatible", "--set", "llm.model=stub-model",
            "--set", f"llm.base_url={stub.url}/v1"]


def test_rule_based_brief_has_every_section(tmp_path):
    out = tmp_path / "b.html"
    r = run("--source", "demo", "--no-ai", "-o", str(out))
    assert r.exit_code == 0, r.output
    html = out.read_text()
    for text in ("Automatic summary", "Key insights", "Monthly spend vs budget", "What changed in 2026-10",
                 "Trend of the top services", "Spend by Service Owner", "Unusual daily spend",
                 "Recommended next steps", "Review checklist", "Month-end forecast vs budgets", "Tag compliance",
                 "Risks to watch", "About this report"):
        assert text in html, text
    assert "Spend was $55.4K in 2026-10" in html and "$4,121 over the $55.0K budget" in html
    assert 'data-review="' in html and "url(#hatch)" in html and 'class="budget"' in html
    assert "Purchase savings plans" in html and "EBS volume provisioned IOPS to gp3" in html  # human names
    assert "rule-based summary" in html and "AI analysis by" not in html


def test_ai_brief_uses_model_json_and_sanitises(tmp_path):
    out = tmp_path / "b.html"
    script = [("tool", "history_overview", {}), ("final", "```json\n" + json.dumps(AI_JSON) + "\n```")]
    with StubLLM(script) as stub:
        r = run("--source", "demo", "-o", str(out), *stub_sets(stub))
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert "FinOps AI analyst" in html and AI_JSON["headline"].replace("'", "&#39;") in html
    assert "A batch fleet scaled out" in html and "Likely cause (AI)" in html
    assert "$1,240/mo" in html and "Impossible saving" in html and "$999" not in html  # capped figure dropped
    assert "imp-high" in html  # "HIGH" / "urgent" normalised
    data = json.loads(re.search(r'id="spendlens-data">(.*?)</script>', html, re.DOTALL).group(1))
    assert data["analysis"]["by"] == "ai" and data["facts"]["savings"]["monthly_total"] == pytest.approx(4613, abs=1)
    body = stub.requests[0]["body"]
    assert "read-only" in body["messages"][0]["content"]  # FinOps analyst profile
    facts = body["messages"][1]["content"].split("Facts (JSON):\n", 1)[1]
    assert json.loads(facts)["cost_change"]["to"] == "2026-10"
    assert {t["function"]["name"] for t in body["tools"]} == {"history_overview", "history_query"}  # no live AWS


def test_ai_failure_falls_back_to_rules(tmp_path):
    out = tmp_path / "b.html"
    with StubLLM([("final", "Sorry, here is prose instead of JSON.")]) as stub:
        r = run("--source", "demo", "-o", str(out), *stub_sets(stub))
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert "Automatic summary" in html and "AI summary unavailable" in html and "no JSON object" in html


def test_ai_setup_error_still_writes_report(tmp_path):
    out = tmp_path / "b.html"
    r = run("--source", "demo", "-o", str(out), "--set", "llm.provider=openai_compatible")  # no base_url
    assert r.exit_code == 0, r.output
    assert "AI summary unavailable" in out.read_text()


def test_refresh_needs_live():
    r = run("--source", "last", "--refresh")
    assert r.exit_code == 2  # BadParameter (the message box is terminal-width dependent)


def test_live_refreshes_every_section(tmp_path, monkeypatch):
    from spendlens import refresh as rf
    from spendlens.history import demo_db_path

    seen = {}

    def fake(path, features, **kw):  # no AWS: record what would be refreshed
        seen.update(features=features, **kw)
        return [], []

    monkeypatch.setattr(rf, "ensure_fresh", fake)
    monkeypatch.setattr(br, "history_path", lambda cfg: demo_db_path())
    out = tmp_path / "b.html"
    r = run("--no-ai", "--refresh", "-o", str(out))
    assert r.exit_code == 0, r.output
    assert seen["features"] == br.FEATURES and seen["refresh"] is True and out.exists()


def test_human_names():
    assert br._human("ebs_volume_unattached") == "EBS volume unattached"
    assert br._human("coh_rightsize", "Rightsize: m5.2xlarge -> m7g.xlarge") == "Rightsize"
    assert br._human("coh_migratetograviton", "MigrateToGraviton: db.r5 -> db.r7g") == "Migrate to graviton"
    assert br._short("arn:aws:ec2:us-east-1:1:volume/vol-1") == "vol-1" and br._short("i-0a11") == "i-0a11"


def test_ai_summary_reused_for_the_same_data(tmp_path):
    with StubLLM([("final", json.dumps(AI_JSON))]) as stub:
        assert run("--source", "demo", "-o", str(tmp_path / "1.html"), *stub_sets(stub)).exit_code == 0
    with StubLLM([("final", "unused")]) as stub:  # same facts + model: no tokens
        r = run("--source", "demo", "-o", str(tmp_path / "2.html"), *stub_sets(stub))
        assert r.exit_code == 0 and stub.requests == []
    html = (tmp_path / "2.html").read_text()
    assert AI_JSON["summary"] in html and "AI summary reused from" in html
    with StubLLM([("final", json.dumps({**AI_JSON, "summary": "Regenerated."}))]) as stub:
        run("--source", "demo", "--fresh", "-o", str(tmp_path / "3.html"), *stub_sets(stub))
        assert stub.requests
    assert "Regenerated." in (tmp_path / "3.html").read_text()
