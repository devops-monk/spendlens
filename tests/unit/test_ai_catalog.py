"""The AWS table catalog and the query checker the AI assistant relies on (offline)."""

import json
from pathlib import Path

import pytest

from spendlens.ai import catalog as cat_mod
from spendlens.ai.sql_check import check, explain_error
from spendlens.ai.sql_guard import UnsafeSQL

CAT = cat_mod.load()

# Plugin doc examples that are genuinely wrong (verified against the plugin source at v1.35.0)
KNOWN_BAD_EXAMPLES = {
    ("aws_appautoscaling_target", "requires these filters"),  # top-level OR drops the required filter
    ("aws_bedrock_foundation_model", "model_lifecycle_status"),  # the column is model_lifecycle
    ("aws_bedrock_imported_model", "model_arn"),  # the column is arn
    ("aws_iam_action", "could not parse"),  # SQLite glob() in a Postgres example
    # filters on event_time, which is not sent to LookupEvents (only start_time/end_time '=' are): 90 days are paged
    ("aws_cloudtrail_lookup_event", "needs a time range"),
}


def test_catalog_shape():
    assert CAT.version == "1.35.0" and len(CAT.tables) == 589
    cu = CAT.get("aws_cost_usage")
    assert cu.required == ["dimension_type_1", "dimension_type_2", "granularity"] and cu.cost_explorer
    assert "DAILY" in cu.columns["granularity"].description
    ec2 = CAT.get("aws_ec2_instance")
    assert "instance_state" in ec2.filters and ec2.get_keys == ["instance_id"] and ec2.examples
    assert {"region", "account_id", "tags", "sp_connection_name"} <= set(ec2.columns)
    assert CAT.get("aws_ec2_instance_metric_cpu_utilization_daily").time_series
    assert CAT.get("AWS.aws_ebs_volume") is not None  # schema-qualified / any case


def test_catalog_matches_hand_checked_fixture():
    fx = json.loads((Path(__file__).parents[1] / "fixtures" / "aws_schema.json").read_text())["tables"]
    fixture_errors = {"aws_cost_usage": {"akas", "raw", "raw_quals", "tags", "title"},  # commented out in source
                      "aws_route53_domain": {"dns_sec"}}
    for table, cols in fx.items():
        missing = set(cols) - set(CAT.get(table).columns) - fixture_errors.get(table, set())
        assert not missing, (table, missing)


@pytest.mark.parametrize(("question", "expect", "top"), [
    ("Which EBS volumes are unattached?", "aws_ebs_volume", 1),
    ("s3 buckets without encryption", "aws_s3_bucket", 1),
    ("IAM users without MFA", "aws_iam_user", 3),
    ("security groups open to 0.0.0.0/0 on port 22", "aws_vpc_security_group_rule", 2),
    ("lambda functions on a deprecated runtime", "aws_lambda_function", 1),
    ("monthly cost by service", "aws_cost_by_service_monthly", 1),
    ("RDS instances that are not multi az", "aws_rds_db_instance", 1),
    ("ec2 cpu utilization last week", "aws_ec2_instance_metric_cpu_utilization", 3),
    ("NAT gateways", "aws_vpc_nat_gateway", 1),
    ("aws_eks_cluster versions", "aws_eks_cluster", 1),
    ("how many rds instances and their ACUs configuration", "aws_rds_db_cluster", 1),  # Aurora Serverless v2
])
def test_find_tables(question, expect, top):
    assert expect in [h["table"] for h in CAT.find(question, top)]


def test_every_plugin_doc_example_passes_the_checker():
    """No false positives: the plugin's own ~1,600 Postgres examples pass (except verified doc bugs)."""
    failures = set()
    for name, t in CAT.tables.items():
        for ex in t.examples:
            try:
                res = check(ex["sql"], CAT, max_rows=500)
                if not res.ok:
                    failures.add((name, res.errors[0].problem))
            except UnsafeSQL as e:
                failures.add((name, str(e)))
    unexpected = {f for f in failures if not any(f[0] == t and s in f[1] for t, s in KNOWN_BAD_EXAMPLES)}
    assert not unexpected, sorted(unexpected)[:10]
    assert sum(len(t.examples) for t in CAT.tables.values()) > 1500


def problems(sql):
    return check(sql, CAT, max_rows=500)


def test_catches_unknown_table_and_column():
    r = problems("select * from aws_ec2_instances")
    assert not r.ok and r.errors[0].did_you_mean[0] == "aws_ec2_instance"
    r = problems("select volume_id, size_gb from aws_ebs_volume where state = 'available'")
    assert not r.ok and r.errors[0].did_you_mean == ["size"] and "size" in r.errors[0].columns
    r = problems("select v.volume_id, v.sizee from aws_ebs_volume v")
    assert "sizee" in r.errors[0].problem


def test_required_filters_have_the_exact_fix():
    r = problems("select dimension_1, sum(unblended_cost_amount) from aws_cost_usage "
                 "where period_start > now() - interval '30 days' group by 1")
    assert not r.ok
    e = r.errors[0]
    assert "granularity" in e.problem and "dimension_type_1" in e.problem and "MONTHLY" in e.fix
    ok = problems("select dimension_1 from aws_cost_usage where granularity = 'MONTHLY' and dimension_type_1 = "
                  "'SERVICE' and dimension_type_2 = 'RECORD_TYPE' and period_start >= '2026-09-01'")
    assert ok.ok and not ok.warnings
    # unqualified, unambiguous required filters in a join count
    assert problems("select u.name, decision from aws_iam_policy_simulator p, aws_iam_user u where action = "
                    "'sts:AssumeRole' and resource_arn = '*' and p.principal_arn = u.arn").ok


def test_json_operator_on_text_column():
    r = problems("select instance_id from aws_ec2_instance where instance_state ->> 'Name' = 'running'")
    assert not r.ok and "not JSON" in r.errors[0].problem
    assert problems("select instance_id from aws_ec2_instance where tags ->> 'Owner' is null").ok


def test_warnings_and_limit():
    r = problems("select dimension_1 from aws_cost_usage where granularity='MONTHLY' and dimension_type_1='SERVICE' "
                 "and dimension_type_2='RECORD_TYPE'")
    assert r.ok and "Cost Explorer" in r.warnings[0].problem
    r = problems("select instance_id, average from aws_ec2_instance_metric_cpu_utilization_daily")
    assert r.ok and "time range" in r.warnings[0].problem
    r = problems("select * from aws_ec2_instance")
    assert r.ok and "select *" in r.warnings[0].problem and r.sql.endswith("limit 501")
    r = problems("select vpc_id from aws_vpc where not cidr_block <<= '10.0.0.0/8'")
    assert r.ok and "<<=" in r.sql  # inet operators run exactly as written
    assert problems("select count(*) n from aws_ebs_volume order by n").ok
    assert problems("with x as (select volume_id v from aws_ebs_volume) select v from x").ok
    assert not problems("with x as (select volume_id v from aws_ebs_volume) select x.nope from x").ok


def test_writes_rejected():
    with pytest.raises(UnsafeSQL):
        check("delete from aws_ebs_volume", CAT)
    with pytest.raises(UnsafeSQL):
        check("select 1; drop table x", CAT)


def test_explain_runtime_errors():
    assert "size" in explain_error('column "size_gb" does not exist', ["aws_ebs_volume"], CAT)
    assert "granularity" in explain_error("missing 3 required quals", ["aws_cost_usage"], CAT)
    assert "ec2:DescribeVolumes" in explain_error(
        "User is not authorized to perform: ec2:DescribeVolumes", ["aws_ebs_volume"], CAT)
    assert "Narrow" in explain_error("canceling statement due to statement timeout", [], CAT)


def test_with_live_columns_wins():
    live = CAT.with_live({"aws_ebs_volume": [("volume_id", "text"), ("brand_new", "jsonb")],
                          "aws_future_table": [("id", "text")]})
    t = live.get("aws_ebs_volume")
    assert set(t.columns) == {"volume_id", "brand_new"} and t.live and "size" in t.missing_live
    assert t.columns["volume_id"].description  # catalog description kept
    assert t.columns["brand_new"].type == "JSON" and live.get("aws_future_table") is not None
    assert not check("select size from aws_ebs_volume", live).ok  # the installed plugin decides


def test_acu_query_passes_the_checker():
    sql = ("select db_cluster_identifier, serverless_v2_scaling_configuration ->> 'MinCapacity' as min_acu, "
           "serverless_v2_scaling_configuration ->> 'MaxCapacity' as max_acu from aws_rds_db_cluster")
    assert check(sql, CAT).ok
