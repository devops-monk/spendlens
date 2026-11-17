"""cost diff -g service -b tag:Name: two-level breakdown via ce:GetCostAndUsage.

Fully offline: a real boto3 Cost Explorer client wrapped in botocore's Stubber (answers in memory, never opens a
connection), on top of conftest's dummy credentials and dead AWS endpoint.
"""

from __future__ import annotations

import json
import re

import boto3
import pytest
from botocore.stub import Stubber
from typer.testing import CliRunner

from spendlens import aws_ce
from spendlens.cli import app
from spendlens.features.cost import SEP, breakdown_rows

pytestmark = pytest.mark.integration
runner = CliRunner()


def _resp(groups, token=None):
    r = {"ResultsByTime": [{"TimePeriod": {"Start": "x", "End": "y"}, "Total": {}, "Estimated": False,
                            "Groups": [{"Keys": list(k), "Metrics": {"NetUnblendedCost": {"Amount": str(a), "Unit": "USD"}}}
                                       for k, a in groups]}],
         "DimensionValueAttributes": [], "GroupDefinitions": []}
    if token:
        r["NextPageToken"] = token
    return r


@pytest.fixture
def stubbed(monkeypatch):
    ce = boto3.client("ce", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    stub = Stubber(ce)
    monkeypatch.setattr(aws_ce, "client", lambda profile=None: ce)
    with stub:
        yield stub
    stub.assert_no_pending_responses()


def test_request_groups_by_service_and_tag_and_excludes_credits():
    p = aws_ce.request([("dimension", "SERVICE"), ("tag", "Name")], "2026-09-01", "2026-10-01", "net_unblended",
                       ["Credit", "Refund"])
    assert p["GroupBy"] == [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "TAG", "Key": "Name"}]
    assert p["Metrics"] == ["NetUnblendedCost"] and p["Granularity"] == "MONTHLY"
    assert p["Filter"] == {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}}
    assert "Filter" not in aws_ce.request([("dimension", "SERVICE")], "a", "b", "unblended", [])


def test_tag_keys_are_cleaned():
    assert aws_ce.clean_key("tag", "Name", "Name$web-1") == "web-1"
    assert aws_ce.clean_key("tag", "Name", "Name$") == aws_ce.UNTAGGED
    assert aws_ce.clean_key("dimension", "SERVICE", "Amazon EC2") == "Amazon EC2"


def test_pagination_is_followed(stubbed):
    groups = [("dimension", "SERVICE"), ("tag", "Name")]
    req = aws_ce.request(groups, "2026-09-01", "2026-10-01", "net_unblended", [])
    stubbed.add_response("get_cost_and_usage", _resp([(("EC2", "Name$a"), 10)], token="t2"), req)
    stubbed.add_response("get_cost_and_usage", _resp([(("EC2", "Name$b"), 5)]), {**req, "NextPageToken": "t2"})
    got = list(aws_ce.grouped_costs(aws_ce.client(), groups, "2026-09-01", "2026-10-01", "net_unblended", []))
    assert [r["keys"] for r in got] == [["EC2", "a"], ["EC2", "b"]]


def test_access_denied_is_explained(stubbed):
    stubbed.add_client_error("get_cost_and_usage", "AccessDeniedException", "not authorized")
    with pytest.raises(aws_ce.CostExplorerError, match="ce:GetCostAndUsage"):
        list(aws_ce.grouped_costs(aws_ce.client(), [("dimension", "SERVICE")], "a", "b", "unblended", []))


def test_breakdown_rows_order_and_per_group_top():
    def row(g, i, b, c):
        return {"group": f"{g}{SEP}{i}", "base": b, "compare": c, "change": round(c - b, 2), "change_pct": None,
                "status": "new" if not b else "gone" if not c else "increase" if c > b else "decrease",
                "flagged": abs(c - b) >= 10, "currency": "USD"}
    result = [row("EC2", "web", 100, 300), row("EC2", "db", 50, 40), row("EC2", "old", 20, 0),
              row("S3", "logs", 10, 12)]
    ordered, groups = breakdown_rows(result, "changed", top=2)
    assert [g["group"] for g in groups] == ["EC2", "S3"]              # largest total change first
    ec2 = groups[0]
    assert (ec2["base"], ec2["compare"], ec2["change"], ec2["up"], ec2["down"], ec2["gone"]) == (170, 340, 170, 1, 2, 1)
    assert [r["item"] for r in ordered if r["group"] == "EC2"] == ["web", "old"]   # top 2 by |change| in EC2
    inc, _ = breakdown_rows(result, "decreases", top=None)
    assert {r["item"] for r in inc} == {"db", "old"}


def _stub_two_periods(stub, base_groups, cmp_groups):
    groups = [("dimension", "SERVICE"), ("tag", "Name")]
    excluded = ["Credit", "Refund"]
    stub.add_response("get_cost_and_usage", _resp(base_groups),
                      aws_ce.request(groups, "2026-09-01", "2026-10-01", "net_unblended", excluded))
    stub.add_response("get_cost_and_usage", _resp(cmp_groups),
                      aws_ce.request(groups, "2026-10-01", "2026-11-01", "net_unblended", excluded))


SETS = ["--set", "cost.exclude_record_types=[Credit, Refund]", "--set", "cost.metric=net_unblended"]


def test_cli_breakdown_json_csv_and_replay(stubbed):
    _stub_two_periods(stubbed,
                      [(("Amazon EC2", "Name$web"), 100), (("Amazon EC2", "Name$db"), 50), (("Amazon S3", "Name$"), 30)],
                      [(("Amazon EC2", "Name$web"), 300), (("Amazon EC2", "Name$db"), 20), (("Amazon S3", "Name$"), 31),
                       (("Amazon EC2", "Name$new-api"), 25)])
    r = runner.invoke(app, ["cost", "diff", "-g", "service", "-b", "tag:Name", "--today", "2026-11-15",
                            "--show", "all", "-f", "json", *SETS])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)["rows"]
    by = {(x["service"], x["tag:Name"]): x for x in rows}
    assert by[("Amazon EC2", "web")]["change"] == 200 and by[("Amazon EC2", "new-api")]["status"] == "new"
    assert by[("Amazon S3", "(untagged)")]["change"] == 1                 # untagged spend is its own row
    assert rows[0]["service"] == "Amazon EC2"                            # largest service change first
    assert rows[-1]["service"] == "TOTAL" and rows[-1]["change"] == 196
    # replay from history: no Cost Explorer calls (the Stubber would fail on an unexpected request)
    r = runner.invoke(app, ["cost", "diff", "-g", "service", "-b", "tag:Name", "--source", "last", "--show", "all",
                            "-f", "csv", *SETS])
    assert r.exit_code == 0, r.output
    assert r.stdout.splitlines()[0].startswith("service,tag:Name,base,compare,change")


def test_cli_breakdown_html_report(stubbed, tmp_path):
    _stub_two_periods(stubbed, [(("Amazon EC2", "Name$web"), 100), (("Amazon RDS", "Name$db"), 80)],
                      [(("Amazon EC2", "Name$web"), 160), (("Amazon RDS", "Name$db"), 40)])
    out = tmp_path / "b.html"
    r = runner.invoke(app, ["cost", "diff", "-g", "service", "-b", "tag:Name", "--today", "2026-11-15",
                            "-f", "html", "-o", str(out), *SETS])
    assert r.exit_code == 0, r.output
    html = out.read_text()
    assert 'class="bd"' in html and html.count('class="ctl bd-grp') == 2
    assert "Biggest increase" in html and "+$60.00" in html and "−$40.00" in html
    assert not re.search(r"(src|href)=\"https?://", html)


def test_breakdown_must_differ_from_group(stubbed):
    r = runner.invoke(app, ["cost", "diff", "-g", "service", "-b", "service", "--today", "2026-11-15"])
    assert r.exit_code == 2 and "must differ" in r.output
