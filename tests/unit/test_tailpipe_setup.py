"""spendlens tailpipe init/collect/status: discovery from fakes, the managed .tpc file, collect commands. No AWS."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from spendlens import tailpipe as tp
from spendlens.cli import app
from spendlens.config import load_config
from spendlens.steampipe import SteampipeError


class Client:
    def __init__(self, fail_flow: bool = False) -> None:
        self.fail_flow = fail_flow

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        if "aws_cloudtrail_trail" in sql:
            return [{"name": "org", "s3_bucket_name": "ct-logs", "s3_key_prefix": "org/"},
                    {"name": "dup", "s3_bucket_name": "ct-logs", "s3_key_prefix": "org"}]
        if "aws_vpc_flow_log" in sql:
            if self.fail_flow:
                raise SteampipeError("AccessDenied: ec2:DescribeFlowLogs")
            return [{"bucket_name": None, "log_destination": "arn:aws:s3:::flows/vpc/", "log_destination_type": "s3"},
                    {"bucket_name": "Bad Bucket!", "log_destination": "", "log_destination_type": "s3"}]
        return []


def aws_cli(*args: str) -> dict[str, Any]:
    if args[:2] == ("bcm-data-exports", "list-exports"):
        return {"Exports": [{"ExportArn": "arn:1"}, {"ExportArn": "arn:2"}]}
    if args[:2] == ("bcm-data-exports", "get-export"):
        if "arn:1" in args:
            return {"Export": {"Name": "cur2", "DataQuery": {"QueryStatement": "SELECT * FROM COST_AND_USAGE_REPORT"},
                               "DestinationConfigurations": {"S3Destination": {"S3Bucket": "billing",
                                                                               "S3Prefix": "cur2"}}}}
        return {"Export": {"Name": "focus", "DataQuery": {"QueryStatement": "SELECT * FROM FOCUS_1_0"}}}
    if args[:2] == ("cur", "describe-report-definitions"):
        raise RuntimeError("AccessDenied: cur:DescribeReportDefinitions")
    return {}


def test_discover_merges_config_steampipe_and_cli() -> None:
    c = load_config(environ={}, sets=["aws.profile=prod", "tailpipe.cur=[{bucket: manual-cur, prefix: x}]"])
    found = tp.discover(c, steampipe=Client(fail_flow=False), aws_cli=aws_cli)
    s = found.sources
    assert s["cloudtrail"] == [{"bucket": "ct-logs", "prefix": "org/", "origin": "trail org"}]  # de-duplicated
    assert s["flow_logs"] == [{"bucket": "flows", "prefix": "vpc/", "origin": "flow log"}]
    assert [x["bucket"] for x in s["cur"]] == ["manual-cur", "billing"]  # config first; FOCUS export skipped
    assert any("Bad Bucket!" in n for n in found.notes) and any("Legacy CUR" in n for n in found.notes)
    text = tp.render(c, found)
    assert 'connection "aws" "spendlens" {\n  profile = "prod"' in text
    assert 'partition "aws_cloudtrail_log" "spendlens_cloudtrail_1_ct_logs"' in text
    assert 'bucket     = "billing"' in text and 'prefix     = "cur2/"' in text


def test_discovery_errors_become_notes() -> None:
    found = tp.discover(load_config(environ={}), steampipe=Client(fail_flow=True), aws_cli=aws_cli)
    assert found.sources["cloudtrail"] and not found.sources["flow_logs"]
    assert any("DescribeFlowLogs" in n for n in found.notes)


def test_no_discover_uses_only_config() -> None:
    c = load_config(environ={}, sets=["tailpipe.discover=false", "tailpipe.cloudtrail=[{bucket: my-trail}]"])
    found = tp.discover(c, steampipe=None, aws_cli=None)
    assert found.sources["cloudtrail"][0]["bucket"] == "my-trail" and not found.sources["cur"]


def test_write_status_and_collect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAILPIPE_INSTALL_DIR", str(tmp_path / "tp"))
    c = load_config(environ={}, sets=["tailpipe.cloudtrail=[{bucket: my-trail, prefix: a/b}]"])
    found = tp.discover(c, discover=False)
    path = tp.write(c, found)
    assert path == tmp_path / "tp" / "config" / "spendlens.tpc"
    assert tp.configured() == {k: int(k == "cloudtrail") for k in tp.KINDS}
    assert tp.collect_cmd("/bin/tp", "cloudtrail", 30, today=date(2026, 11, 25)) == [
        "/bin/tp", "collect", "aws_cloudtrail_log", "--from", "2026-10-26", "--progress=false"]


def test_cli_init_dry_run_and_collect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAILPIPE_INSTALL_DIR", str(tmp_path / "tp"))
    runner = CliRunner()
    r = runner.invoke(app, ["tailpipe", "init", "--no-discover", "--dry-run",
                            "--set", "tailpipe.cur=[{bucket: my-cur, prefix: cur}]"])
    assert r.exit_code == 0, r.output
    assert 'partition "aws_cost_and_usage_report"' in r.stdout and not (tmp_path / "tp").exists()
    r = runner.invoke(app, ["tailpipe", "init", "--no-discover"])
    assert r.exit_code == 1 and "Nothing to configure" in r.output
    r = runner.invoke(app, ["tailpipe", "init", "--no-discover", "--set", "tailpipe.cur=[{bucket: my-cur}]"])
    assert r.exit_code == 0 and (tmp_path / "tp" / "config" / "spendlens.tpc").is_file()
    # a fake tailpipe records what collect runs
    log = tmp_path / "calls"
    fake = tmp_path / "tailpipe"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n")
    fake.chmod(0o755)
    r = runner.invoke(app, ["tailpipe", "collect", "--days", "3", "--set", f"graph.tailpipe_path={fake}"])
    assert r.exit_code == 0, r.output
    assert log.read_text().startswith("collect aws_cost_and_usage_report --from ")
    r = runner.invoke(app, ["tailpipe", "collect", "cloudtrail", "--set", f"graph.tailpipe_path={fake}"])
    assert r.exit_code == 1 and "Nothing configured" in r.output
    r = runner.invoke(app, ["tailpipe", "collect", "bogus", "--set", f"graph.tailpipe_path={fake}"])
    assert r.exit_code == 2


def test_discovery_errors_name_the_fix():
    from spendlens.tailpipe import _why

    sso = ("operation error STS: GetCallerIdentity, get identity: get credentials: failed to refresh cached "
           "credentials, the SSO session has expired or is invalid")
    assert "aws sso login" in _why(sso)
    old_cli = "\nusage: aws [options] <command> <subcommand>\nTo see help text, you can run:\n\n  aws help\n"
    assert "upgrade the AWS CLI" in _why(old_cli)
    assert _why("line one\nAccessDenied: not allowed\n") == "AccessDenied: not allowed"


class AllSources:
    """Every discovery query answered, as Steampipe would."""

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        if "aws_cloudtrail_trail" in sql:
            return [{"name": "org", "s3_bucket_name": None,
                     "log_group_arn": "arn:aws:logs:eu-west-1:123456789012:log-group:aws-cloudtrail-logs:*"}]
        if "aws_vpc_flow_log" in sql:
            return [{"log_destination_type": "cloud-watch-logs", "log_group_name": "vpc-flows", "region": "eu-west-2"}]
        if "aws_s3_bucket" in sql:
            return [{"name": "a", "logging": {"TargetBucket": "logs", "TargetPrefix": "s3/a/"}},
                    {"name": "b", "logging": {"TargetBucket": "logs", "TargetPrefix": "s3/b/"}}]
        if "aws_ec2_application_load_balancer" in sql:
            return [{"name": "web", "load_balancer_attributes": [
                {"Key": "access_logs.s3.enabled", "Value": "true"}, {"Key": "access_logs.s3.bucket", "Value": "lb-logs"},
                {"Key": "access_logs.s3.prefix", "Value": "alb"}]},
                {"name": "off", "load_balancer_attributes": [{"Key": "access_logs.s3.enabled", "Value": "false"}]}]
        if "aws_ec2_network_load_balancer" in sql:
            return []
        if "aws_ec2_classic_load_balancer" in sql:
            return [{"name": "old", "access_log_s3_bucket_name": "lb-logs", "access_log_s3_bucket_prefix": "clb"}]
        if "aws_guardduty_publishing_destination" in sql:
            return [{"destination_arn": "arn:aws:s3:::sec-findings/guardduty"}]
        if "aws_wafv2_web_acl" in sql:
            return [{"name": "edge", "logging_configuration": {"LogDestinationConfigs": [
                "arn:aws:logs:us-east-1:123456789012:log-group:aws-waf-logs-edge"]}}]
        return []


def exports(*args: str) -> dict[str, Any]:
    if args[:2] == ("bcm-data-exports", "list-exports"):
        return {"Exports": [{"ExportArn": "f"}, {"ExportArn": "r"}]}
    if args[:2] == ("bcm-data-exports", "get-export"):
        q = "SELECT * FROM FOCUS_1_0_AWS" if "f" in args else "SELECT * FROM COST_OPTIMIZATION_RECOMMENDATIONS"
        return {"Export": {"Name": args[3], "DataQuery": {"QueryStatement": q},
                           "DestinationConfigurations": {"S3Destination": {"S3Bucket": "billing", "S3Prefix": args[3]}}}}
    return {}


def test_discovers_every_kind_and_cloudwatch_log_groups() -> None:
    c = load_config(environ={}, sets=["tailpipe.securityhub=[{bucket: sh-export, prefix: findings}]",
                                      "tailpipe.flow_log_groups=[{name: manual-flows, region: eu-west-1}]"])
    found = tp.discover(c, steampipe=AllSources(), aws_cli=exports)
    s = found.sources
    assert s["cloudtrail"] == [{"log_group": "aws-cloudtrail-logs", "region": "eu-west-1", "origin": "trail org"}]
    assert [g["log_group"] for g in s["flow_logs"]] == ["manual-flows", "vpc-flows"]
    assert s["s3_access_logs"] == [{"bucket": "logs", "prefix": "s3/", "origin": "access logs of 2 bucket(s)"}]
    assert [x["prefix"] for x in s["alb_access_logs"]] == ["alb/"] and s["clb_access_logs"][0]["prefix"] == "clb/"
    assert s["guardduty"][0]["bucket"] == "sec-findings" and s["securityhub"][0]["bucket"] == "sh-export"
    assert s["waf"] == [{"log_group": "aws-waf-logs-edge", "region": "us-east-1", "origin": "web ACL edge"}]
    assert s["focus"][0]["prefix"] == "f/" and s["cost_recommendations"][0]["prefix"] == "r/"
    text = tp.render(c, found)
    assert 'source "aws_cloudwatch_log_group" {' in text and 'log_group_name = "aws-cloudtrail-logs"' in text
    assert 'partition "aws_cost_and_usage_focus"' in text and 'partition "aws_waf_traffic_log"' in text
    counts = tp.configured(Path("/nonexistent"))
    assert set(counts) == set(tp.KINDS)


def test_bad_log_groups_are_skipped() -> None:
    f = tp.Found()
    f.add_group("cloudtrail", 'bad"name', "eu-west-1", "x")
    f.add_group("cur", "ok", "eu-west-1", "x")  # the CUR has no CloudWatch Logs source
    assert f.sources["cloudtrail"] == [] and len(f.notes) == 2
