import json
import re

from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.history import HistoryStore
from spendlens.models import Finding, Status

runner = CliRunner()
TABS = ["overview", "cost", "showback", "anomalies", "forecast", "tags", "rightsize", "waste", "savings"]


def test_demo_dashboard_complete_and_self_contained(tmp_path):
    out = tmp_path / "r.html"
    r = runner.invoke(app, ["dashboard", "--source", "demo", "-o", str(out)])
    assert r.exit_code == 0, r.output
    html = out.read_text()
    for t in TABS:
        assert f'id="tab-{t}"' in html and f'data-tab="{t}"' in html
    assert "No saved run yet" not in html  # every section populated from the demo DB
    assert not re.search(r"(src|href)=\"https?://", html)  # no network: works offline / as a CI artifact
    assert "<script>" in html and "DEMO DATA" in html
    assert "$55.4K" in html  # hero: last full month spend
    assert html.count("<svg") >= 6


def test_dashboard_branding_from_config(tmp_path):
    (tmp_path / "finops.yaml").write_text(
        'dashboard: {title: "Very FinOps", organisation: "Very Group", brand_primary: "#123456"}\n')
    out = tmp_path / "r.html"
    r = runner.invoke(app, ["dashboard", "--source", "demo", "-o", str(out), "-c", str(tmp_path / "finops.yaml")])
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert "<title>Very FinOps</title>" in html and "Very Group" in html and "--brand:#123456" in html
    # brand colour is chrome only: chart data colours stay on the validated palette
    assert "--series-1:#2a78d6" in html


def test_dashboard_bad_brand_colour_rejected(tmp_path):
    r = runner.invoke(app, ["dashboard", "--source", "demo", "--set", "dashboard.brand_primary=pink"])
    assert r.exit_code == 2


def test_dashboard_from_partial_history_shows_hints(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    with HistoryStore(db) as s:
        s.save_run("tags", [Finding(control="tags_mandatory_s3_bucket", check="tags_mandatory",
                                    resource_type="s3_bucket", resource="arn:aws:s3:::<evil>",
                                    status=Status.ALARM, reason="missing <Team>")])
    out = tmp_path / "r.html"
    r = runner.invoke(app, ["dashboard", "-o", str(out)])
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert "Run <code>spendlens cost diff</code>" in html  # sections without data say how to populate them
    assert "arn:aws:s3:::&lt;evil&gt;" in html and "<evil>" not in html


def test_dashboard_requires_saved_runs(tmp_path):
    r = runner.invoke(app, ["dashboard", "-o", str(tmp_path / "x.html")])
    assert r.exit_code == 1
    assert runner.invoke(app, ["dashboard", "--source", "live"]).exit_code == 2


def test_every_command_supports_html(tmp_path):
    for cmd in (["tags", "--source", "demo"], ["waste", "--source", "demo"], ["cost", "diff", "--source", "demo"],
                ["anomalies", "--source", "demo"], ["savings", "summary", "--source", "demo"]):
        out = tmp_path / f"{cmd[0]}.html"
        r = runner.invoke(app, [*cmd, "-f", "html", "-o", str(out)])
        assert r.exit_code in (0, 3), (cmd, r.output)
        html = out.read_text()
        assert html.startswith("<!doctype html>") and "<table" in html, cmd
    # json still carries machine-readable data
    doc = json.loads(runner.invoke(app, ["cost", "diff", "--source", "demo", "-f", "json"]).stdout)
    assert doc["rows"]


def _embedded(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="spendlens-data">(.*?)</script>', html, re.DOTALL)
    assert m, "view model not embedded"
    return json.loads(m.group(1))


def test_view_model_embedded_as_json(tmp_path):
    out = tmp_path / "r.html"
    runner.invoke(app, ["dashboard", "--source", "demo", "-o", str(out)])
    vm = _embedded(out.read_text())
    assert vm["account"] == "123456789012" and vm["source"] == "demo"
    assert vm["cost_diff"]["p1"] == "2026-10" and vm["savings"]["amount"] > 0
    assert vm["hero"]["value"] == "$55.4K"


def test_plugin_dashboard_section(tmp_path, monkeypatch):
    from spendlens.plugins import registry

    monkeypatch.setattr(registry, "dashboard_sections", [
        ("gpu", "GPU fleet", lambda data, cfg: f'<div class="card"><h2>GPU</h2><p>{len(data.runs)} runs</p></div>'),
        ("broken", "Broken", lambda data, cfg: 1 / 0),
    ])
    out = tmp_path / "r.html"
    r = runner.invoke(app, ["dashboard", "--source", "demo", "-o", str(out)])
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert 'data-tab="gpu"' in html and "<p>10 runs</p>" in html  # 10 feature runs in the demo DB
    assert 'id="tab-broken"' in html and "Section failed" in html  # a broken plugin never breaks the report


def test_templates_are_package_files():
    from importlib import resources

    d = resources.files("spendlens.templates").joinpath("report")
    names = {p.name for p in d.iterdir()}
    assert {"base.html.j2", "macros.html.j2", "dashboard.html.j2", "table.html.j2", "style.css", "app.js"} <= names


def test_css_values_not_html_escaped_and_injection_rejected(tmp_path):
    out = tmp_path / "r.html"
    runner.invoke(app, ["dashboard", "--source", "demo", "-o", str(out)])
    assert '--font:system-ui, -apple-system, "Segoe UI"' in out.read_text()  # real quotes inside <style>
    for bad in ['dashboard.font_family=x}</style><script>alert(1)</script>', "dashboard.font_family=a;color:red"]:
        assert runner.invoke(app, ["dashboard", "--source", "demo", "--set", bad]).exit_code == 2
