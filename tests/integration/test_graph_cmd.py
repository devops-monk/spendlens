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


def test_owners_demo_table_script_json(tmp_path):
    r = run("graph", "owners", "--source", "demo")
    assert "vol-0payapi1" in r.output and "payments" in r.output and "mounted by EC2 instance payments-api-1" in r.output
    assert "high" in r.output and "medium" in r.output and "low" in r.output
    sh = tmp_path / "tag.sh"
    run("graph", "owners", "--source", "demo", "--min-confidence", "high", "-k", "Team", "--script", str(sh),
        "-f", "json", "-o", str(tmp_path / "o.json"))
    doc = json.loads((tmp_path / "o.json").read_text())
    items = doc["rows"] if isinstance(doc, dict) else doc
    assert items and all(x["confidence"] == "high" and x["key"] == "Team" for x in items)
    text = sh.read_text()
    assert "REVIEW BEFORE RUNNING" in text and "aws resourcegroupstaggingapi tag-resources" in text
    assert "report-export" not in text  # low confidence filtered out


def test_tags_shows_suggestions_from_the_saved_graph(fake_steampipe):
    from spendlens.common import history_path
    from spendlens.config import load_config
    from spendlens.history import HistoryStore
    from spendlens.models import Finding, Status

    run("graph", "collect")
    vol = "arn:aws:ec2:eu-west-1:123456789012:volume/vol-0payapi1"
    with HistoryStore(history_path(load_config())) as s:
        s.save_run("tags", [Finding(control="tags_mandatory_ebs_volume", check="tags_mandatory", resource_type="ebs_volume",
                                    resource=vol, status=Status.ALARM, reason="vol-0payapi1 is missing tags: Team.")])
    r = run("tags", "--source", "last")
    assert "suggested" in r.output and "Team=payments" in r.output and "spendlens graph owners" in r.output
    r = run("tags", "--source", "last", "-f", "json")
    assert "Team=payments" in r.stdout


def test_review_has_suggested_tags_card(tmp_path):
    out = tmp_path / "review.html"
    run("review", "--source", "demo", "--no-ai", "-o", str(out))
    html = out.read_text()
    assert "Suggested tags" in html and "mounted by EC2 instance payments-api-1" in html
    assert "spendlens graph owners --script tag-review.sh" in html


def test_collect_prices_from_the_cur_via_tailpipe(fake_steampipe, tmp_path):
    import stat

    rows_ = [{"resource_id": "vol-0rptdata", "cost": 30.0, "first_seen": "2026-10-01", "last_seen": "2026-10-30"},
             {"resource_id": "i-0payapi1", "cost": 50.0, "first_seen": "2026-10-01", "last_seen": "2026-10-30"}]
    (tmp_path / "out.json").write_text(json.dumps({"columns": [], "rows": rows_}))
    exe = tmp_path / "tailpipe"
    exe.write_text(f'#!/bin/sh\ncat "{tmp_path}/out.json"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    r = run("graph", "collect", "--set", f"graph.tailpipe_path={exe}")
    assert "2 resources priced from the CUR (Tailpipe)" in r.output
    assert "2 from the CUR" in run("graph", "stats").output
    r = run("graph", "show", "legacy-reporting")
    assert "$30.44/mo (CUR)" in r.output and "$144.09/mo" in r.output  # 30 over 30 days, as a monthly rate
    r = run("graph", "orphans", "--source", "last")
    assert "legacy-reporting" in r.output
    # without Tailpipe or Cost Explorer data, the price book, said plainly
    r = run("graph", "collect", "--set", "graph.tailpipe_path=/nonexistent/tailpipe")
    assert "price-book estimates" in r.output and "spendlens cost resources" in r.output


def test_traffic_demo_table_html_json(tmp_path):
    r = run("graph", "traffic", "--source", "demo")
    assert "payments-api-2" in r.output and "Through a NAT gateway" in r.output and "DynamoDB" in r.output
    assert "Proposal: Add a DynamoDB gateway endpoint to vpc-0prod" in r.output
    r = run("graph", "traffic", "--source", "demo", "--path", "cross_az", "-f", "csv", "-o", str(tmp_path / "t.csv"))
    lines = (tmp_path / "t.csv").read_text().splitlines()
    assert len(lines) == 3 and all("Cross-AZ" in ln for ln in lines[1:])
    run("graph", "traffic", "--source", "demo", "-f", "html", "-o", str(tmp_path / "t.html"))
    html = (tmp_path / "t.html").read_text()
    assert "Costliest paths" in html and '<svg class="slg in-page"' in html and "Add a DynamoDB gateway endpoint" in html
    assert runner.invoke(app, ["graph", "traffic", "--source", "demo", "--path", "nope"]).exit_code == 2


def test_traffic_live_reads_flow_logs_with_tailpipe(fake_steampipe, tmp_path):
    import stat

    from spendlens.graph.demo import flows

    run("graph", "collect", "--set", "graph.tailpipe_path=/nonexistent/tailpipe")  # the graph to match flows against
    (tmp_path / "out.json").write_text(json.dumps({"columns": [], "rows": flows()}))
    exe = tmp_path / "tailpipe"
    exe.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\ncat "{tmp_path}/out.json"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    r = run("graph", "traffic", "--set", f"graph.tailpipe_path={exe}")
    assert "DynamoDB gateway endpoint" in r.output
    assert "from aws_vpc_flow_log" in (tmp_path / "args").read_text()
    r = run("graph", "traffic", "--source", "last")  # saved, replayed offline
    assert "payments-api-2" in r.output
    r = runner.invoke(app, ["graph", "traffic", "--set", "graph.tailpipe_path=/nonexistent/tailpipe"])
    assert r.exit_code == 1 and "Tailpipe" in " ".join(r.output.split())


def test_traffic_ai_tool_and_review_section(tmp_path):
    from spendlens.ai.tools import Toolbox
    from spendlens.config import load_config
    from spendlens.history import demo_db_path

    tb = Toolbox(load_config(environ={}), demo_db_path())
    d = json.loads(tb.execute("1", "traffic_paths", {"path": "nat"}).content)
    assert {r["peer_name"] for r in d["rows"]} == {"DynamoDB", "Internet", "S3"} and d["proposals"][0]["service"] == "DynamoDB"
    out = tmp_path / "r.html"
    run("review", "--source", "demo", "--no-ai", "-o", str(out))
    html = out.read_text()
    assert 'id="transfer"' in html and "endpoint|vpc-0prod|DynamoDB" in html


def test_cloudtrail_creators_are_opt_in(monkeypatch):
    from spendlens.graph.demo import FakeClient, rows

    tables = rows()
    tables["aws_cloudtrail_lookup_event"] = [
        {"event_name": "CreateFunction20150331", "event_time": "2026-11-01T09:00:00Z", "username": "bob@example.com",
         "resources": [{"ResourceName": "report-export"}]}]
    clients = []

    class Ctx(FakeClient):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr("spendlens.steampipe.SteampipeClient", lambda *a, **k: clients.append(Ctx(tables)) or clients[-1])
    monkeypatch.setattr("spendlens.features.graph_cmd.caller_identity", lambda: {"Account": "123456789012"})
    run("graph", "collect", "--set", "graph.tailpipe_path=/nonexistent/tailpipe")  # off by default
    assert not any("aws_cloudtrail_lookup_event" in q for c in clients for q in c.sql)
    clients.clear()
    r = run("graph", "collect", "--set", "graph.tailpipe_path=/nonexistent/tailpipe",
            "--set", "graph.owner_from_cloudtrail=true", "--set", "graph.cloudtrail_days=30")
    assert "creators found for 1 resource(s) over the last 30 days" in r.output
    ct = [q for c in clients for q in c.sql if "aws_cloudtrail_lookup_event" in q]
    assert len(ct) == 20 and all("interval '30 days'" in q for q in ct)
    r = run("graph", "owners", "-k", "Owner")
    assert "bob@example.com" in r.output and "created by bob@example.com" in r.output
