"""`spendlens graph` end to end through the CLI: demo data, a saved graph, and a live collect against a fake
Steampipe (the real collect() code with Steampipe-shaped rows; never AWS)."""

import json

import pytest
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.graph.demo import FakeClient, rows

runner = CliRunner()


class Out:
    """CLI output with whitespace collapsed (rich wraps long words at the test terminal's width)."""

    def __init__(self, r):
        self.exit_code, self.stdout = r.exit_code, r.stdout
        self.output = " ".join(r.output.split())


def run(*args):
    r = runner.invoke(app, list(args), env={"COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    return Out(r)


def test_orphans_demo_table_json_csv(tmp_path):
    r = run("graph", "orphans", "--source", "demo")
    assert "legacy-reporting" in r.output and "163.65" in r.output and "already flagged by waste" in r.output
    run("graph", "orphans", "--source", "demo", "-f", "json", "-o", str(tmp_path / "g.json"))
    doc = json.loads((tmp_path / "g.json").read_text())
    rows_ = doc["rows"] if isinstance(doc, dict) else doc
    kinds = {g["kind"] for g in rows_}
    assert {"stopped_instance", "orphan_snapshots", "unused_ami", "idle_load_balancer"} <= kinds
    stopped = next(g for g in rows_ if g["kind"] == "stopped_instance")
    assert len(stopped["resources"]) == len(stopped["members"]) == 8
    run("graph", "orphans", "--source", "demo", "-f", "csv", "--min-saving", "50", "-o", str(tmp_path / "g.csv"))
    csv = (tmp_path / "g.csv").read_text().splitlines()
    assert csv[0].startswith("saving_per_month") and all(float(x.split(",")[0]) >= 50 for x in csv[1:])


def test_orphans_html_page(tmp_path):
    out = tmp_path / "cleanup.html"
    run("graph", "orphans", "--source", "demo", "-f", "html", "-o", str(out))
    html = out.read_text()
    assert "<svg class=\"slg in-page\"" in html and "&lt;svg" not in html  # drawn, not escaped
    assert "Stopped instance legacy-reporting" in html and "Before you do" in html


def test_show_tree_and_files(tmp_path):
    r = run("graph", "show", "legacy-reporting", "--source", "demo")
    assert "mounts" in r.output and "vol-0rptdata" in r.output and "Cleanup group:" in r.output
    assert "payments-db" not in r.output  # subnets are shown, not walked through
    for name, marker in (("g.html", "<svg class=\"slg in-page\""), ("g.svg", "<svg class=\"slg\""),
                         ("g.mmd", "flowchart LR"), ("g.dot", "digraph"), ("g.json", "\"nodes\"")):
        run("graph", "show", "i-0legacyrpt", "--source", "demo", "-o", str(tmp_path / name))
        assert marker in (tmp_path / name).read_text(), name
    doc = json.loads((tmp_path / "g.json").read_text())
    assert doc["total_monthly_cost"] > 160 and doc["cleanup_groups"][0]["kind"] == "stopped_instance"


def test_show_ambiguous_and_missing():
    r = runner.invoke(app, ["graph", "show", "i-0", "--source", "demo"])
    assert r.exit_code == 1 and "match" in r.output
    r = runner.invoke(app, ["graph", "show", "nothing-like-this", "--source", "demo"])
    assert r.exit_code == 1


def test_impact_and_stats():
    r = run("graph", "impact", "vol-0rptdata", "--source", "demo", "--json")
    d = json.loads(r.stdout[r.stdout.index("{"):])
    assert d["breaks"][0]["why"] == "EC2 instance legacy-reporting mounts it"
    r = run("graph", "stats", "--source", "demo")
    assert "EBS snapshot" in r.output and "Links:" in r.output


def test_no_saved_graph_says_how_to_collect():
    r = Out(runner.invoke(app, ["graph", "show", "x"]))
    assert r.exit_code == 1 and "spendlens graph collect" in r.output


@pytest.fixture
def fake_steampipe(monkeypatch):
    fail = {}
    monkeypatch.setattr("spendlens.steampipe.SteampipeClient", lambda *a, **k: FakeClient(rows(), fail))
    monkeypatch.setattr("spendlens.features.graph_cmd.caller_identity", lambda: {"Account": "123456789012"})
    return fail


def test_live_collect_saves_then_offline_commands_use_it(fake_steampipe):
    fake_steampipe["aws_lambda_function"] = "AccessDeniedException: lambda:ListFunctions"
    r = run("graph", "collect")
    assert "Lambda functions: no permission" in r.output and "EBS volume" in r.output
    r = run("graph", "orphans", "--source", "last")
    assert "legacy-reporting" in r.output
    r = run("graph", "show", "payments-api-1")  # default: the saved graph, no AWS
    assert "payments-alb" in r.output or "payments-tg" in r.output


def test_review_shows_cleanup_groups_and_queues_them_once(tmp_path):
    out = tmp_path / "review.html"
    r = run("review", "--source", "demo", "--no-ai", "-o", str(out))
    assert "cleanup groups" in r.output
    html = out.read_text()
    assert 'id="cleanup"' in html and "Clean up: Stopped instance legacy-reporting" in html
    data = json.loads(html.split('id="spendlens-data">', 1)[1].split("</script>", 1)[0])
    ids = [x["id"] for x in data["review"]]
    assert any(i.startswith("group|stopped_instance:") for i in ids)
    # the unattached-volume alarm a group includes is replaced by the group, never listed twice
    groups = [x for x in data["review"] if x["id"].startswith("group|unattached_volume:")]
    for grp in groups:
        assert not any(x["control"] == "ebs_volume_unattached" and x["full"] == grp["full"] for x in data["review"])
    r = run("review", "--source", "demo", "--no-ai", "--skip", "graph", "-o", str(out))
    assert 'id="cleanup"' not in out.read_text()
