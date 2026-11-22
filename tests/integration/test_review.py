"""spendlens review: the full FinOps review page, with the rule-based summary and with the AI analyst (real OpenAI
SDK against the local stub LLM; no network, no AWS)."""

import json
import re

from stubllm import StubLLM
from test_brief import AI_JSON, stub_sets
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.features import review as rv
from spendlens.history import HistoryStore, demo_db_path
from spendlens.render import charts as ch

runner = CliRunner()


def run(*args):
    return runner.invoke(app, ["review", *args], env={"SPENDLENS_NO_PROGRESS": "1"})


def payload(html):
    return json.loads(re.search(r'id="spendlens-data">(.*?)</script>', html, re.DOTALL).group(1))


def test_rule_based_review_has_every_section(tmp_path):
    out = tmp_path / "r.html"
    r = run("--source", "demo", "--no-ai", "-o", str(out))
    assert r.exit_code == 0, r.output
    html = out.read_text()
    for text in ("Automatic summary", "Key insights", "Monthly spend vs budget", "what moved the total",
                 "Trend of the top services", "Service → Name", "Biggest movers by Name", "Spend by Service Owner",
                 "Spend by Environment", "Every service: 2026-09 → 2026-10", "Usual vs on the day",
                 "Effort vs value", "Where the savings come from", "Recommended next steps", "Review queue",
                 "47 proposals to decide", "Export decisions (CSV)", "Month-end forecast vs budgets",
                 "Where tags are missing", "Data and method", "You decide"):
        assert text in html, text
    assert html.count('<tr data-id="') == 47 and 'data-d="approve"' in html and 'class="rv-note"' in html
    assert "Total 2026-09" in html and "Total 2026-10" in html  # the spend bridge
    assert 'class="chart flow"' in html and 'class="mx"' in html and "Savings Plans" in html  # readable services
    data = payload(html)
    assert len(data["review"]) == 47 and data["key"] == "123456789012"
    # the queue = every saving, plus what only cleanup groups find (their included waste alarms are not counted twice)
    groups_new = 258.15  # demo: snapshots of a stopped instance's volumes, deleted volumes and databases, unused AMI
    groups_new += 78.27 + 29.35  # and two gateway-endpoint proposals (graph traffic): add DynamoDB, associate S3
    assert round(sum(x["amount"] for x in data["review"]), 2) == round(
        data["facts"]["savings"]["monthly_total"] + groups_new, 2)
    assert "Cleanup groups</a> add <b>$258" in html
    assert data["analysis"]["by"] == "rules" and "AI analysis by" not in html
    assert "spendlens never changes resources" in html


def test_ai_review_uses_the_analyst_and_reuses_the_briefs_analysis(tmp_path):
    with StubLLM([("final", json.dumps(AI_JSON))]) as stub:
        r = run("--source", "demo", "-o", str(tmp_path / "r.html"), *stub_sets(stub))
        assert r.exit_code == 0, r.output
        assert len(stub.requests) == 1
    html = (tmp_path / "r.html").read_text()
    assert "FinOps AI analyst" in html and "A batch fleet scaled out" in html and "Likely cause (AI)" in html
    with StubLLM([("final", "unused")]) as stub:  # the brief on the same data: cached, no tokens
        b = runner.invoke(app, ["brief", "--source", "demo", "-o", str(tmp_path / "b.html"), *stub_sets(stub)],
                          env={"SPENDLENS_NO_PROGRESS": "1"})
        assert b.exit_code == 0 and stub.requests == []


def test_skip_leaves_a_check_out(tmp_path):
    out = tmp_path / "r.html"
    r = run("--source", "demo", "--no-ai", "--skip", "tags", "-o", str(out))
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert "Tag compliance" not in html and "Where tags are missing" not in html and "Budget &amp; tags" not in html
    assert "left out (--skip)" in html and "Month-end forecast vs budgets" in html
    data = payload(html)
    assert "tag_compliance_pct" not in data["facts"] and "mandatory_tags" not in data["facts"]
    assert len(data["review"]) == 47  # the savings are untouched
    assert run("--source", "demo", "--skip", "nope").exit_code == 2


def test_refresh_needs_live():
    assert run("--source", "last", "--refresh").exit_code == 2


def test_kinds_effort_and_names():
    assert rv._kind("ebs_volume_unattached", "") == "removal"
    assert rv._kind("coh_purchasesavingsplans", "PurchaseSavingsPlans: ...") == "commitment"
    assert rv._kind("ebs_volume_gp2", "gp2 to gp3") == "change"
    assert rv.service_name("savingsplans") == "Savings Plans" and rv.service_name("Amazon EC2") == "Amazon EC2"
    assert rv._band(49.9) == "< $50" and rv._band(1000) == "≥ $1K"
    assert rv._region("arn:aws:ec2:eu-west-1:1:volume/vol-1") == "eu-west-1" and rv._region("i-1") == ""


def test_breakdown_flow_and_movers():
    with HistoryStore(demo_db_path(), read_only=True) as s:
        b = rv.breakdown(s, "USD")
    assert b["sub"] == "Name" and b["period"] == "2026-10" and b["links"]
    assert all(m["service"] and m["item"] for m in b["movers"])
    assert abs(b["movers"][0]["change"]) >= abs(b["movers"][-1]["change"])  # largest first


def test_charts():
    w = ch.waterfall({"label": "Sep", "value": 100}, [{"label": "EC2", "value": 30}, {"label": "S3", "value": -10}],
                     {"label": "Oct", "value": 120}, label="bridge")
    assert "+$30.00" in w and "-$10.00" in w and "$120" in w and "var(--div-neg)" in w
    links = [{"source": "A", "target": f"t{i}", "value": i + 1} for i in range(12)]
    f = ch.flow(links, label="f", max_nodes=5)
    assert "Other" in f and f.count('class="band"') == 5 and "A → t11" in f
    assert ch.flow([], label="f") == '<p class="empty">No data.</p>'
    m = ch.matrix(["low"], ["a", "b"], {("low", "a"): {"count": 2, "total": 40}}, label="m")
    assert "2 items" in m and "empty-cell" in m
    d = ch.dumbbell([{"label": "x", "base": 10, "value": 30, "severity": "high"}], label="d")
    assert "+$20.00 (+200%)" in d
