"""Every control in the internal legacy mods must have a spendlens equivalent.

Legacy mods: steampipe-mod-aws-tags (internal fork) and steampipe-mod-aws-cost-analysis.
If you retire or rename a spendlens control, this test tells you which legacy check would be lost.
Human-readable version: docs/control-mapping.md.
"""

from spendlens.features.tags_catalog import TAGGABLE_RESOURCES
from spendlens.powerpipe import bundled_mod_dir, mod_controls

# steampipe-mod-aws-tags: <slug>_mandatory and <slug>_untagged for these 54 resource types
LEGACY_TAG_SLUGS = [
    "accessanalyzer_analyzer", "api_gateway_stage", "cloudfront_distribution", "cloudtrail_trail",
    "cloudwatch_alarm", "cloudwatch_log_group", "codebuild_project", "codecommit_repository",
    "codepipeline_pipeline", "config_rule", "dynamodb_table", "ebs_snapshot", "ebs_volume",
    "ec2_application_load_balancer", "ec2_classic_load_balancer", "ec2_gateway_load_balancer", "ec2_instance",
    "ec2_network_load_balancer", "ec2_reserved_instance", "ecr_repository", "ecs_container_instance", "ecs_service",
    "efs_file_system", "eventbridge_rule", "guardduty_detector", "iam_role", "iam_server_certificate", "iam_user",
    "inspector_assessment_template", "kinesis_firehose_delivery_stream", "kms_key", "lambda_function",
    "rds_db_cluster", "rds_db_cluster_parameter_group", "rds_db_cluster_snapshot", "rds_db_instance",
    "rds_db_option_group", "rds_db_parameter_group", "rds_db_snapshot", "rds_db_subnet_group", "route53_domain",
    "route53_resolver_endpoint", "s3_bucket", "secretsmanager_secret", "ssm_parameter", "vpc", "vpc_eip",
    "vpc_nat_gateway", "vpc_network_acl", "vpc_security_group", "wafv2_ip_set", "wafv2_regex_pattern_set",
    "wafv2_rule_group", "wafv2_web_acl",
]

# steampipe-mod-aws-cost-analysis control -> spendlens control (or a planned feature)
LEGACY_COST_ANALYSIS = {
    "latest_rds_instance_types": "rds_instance_class_not_allowed",
    "long_running_rds_db_instances": "rds_instance_reservation_candidate",
    "rds_db_low_connection_count": "rds_instance_low_connections",
    "rds_db_low_utilization": "rds_instance_low_utilization",
    "rds_performance_insights_enabled": "rds_instance_performance_insights_policy",
    "rds_backup_retention_period": "rds_instance_backup_retention_policy",
    "rds_multi_az": "rds_instance_multi_az_policy",
    "ecs_service_correct_no_of_tasks": "ecs_service_task_count_policy",
    "ecs_tasks_cpu_usage": "ecs_task_cpu_over_max",
    "ecs_tasks_memory_usage": "ecs_task_memory_over_max",
    "log_group_encryption_at_rest_enabled": "cloudwatch_log_group_kms_policy",
    "cloudwatch_log_group_retention_period_in_days": "cloudwatch_log_group_retention_policy",
    "full_month_cost_changes": "feature:cost diff",  # phase 4
}
PLANNED_FEATURES = {"feature:cost diff"}




def test_all_legacy_tag_controls_covered():
    assert len(LEGACY_TAG_SLUGS) == 54
    controls = mod_controls(bundled_mod_dir())
    missing = [s for s in LEGACY_TAG_SLUGS if s not in TAGGABLE_RESOURCES]
    assert not missing, f"legacy tag resource types not in tags_catalog: {missing}"
    for slug in LEGACY_TAG_SLUGS:
        for check in ("mandatory", "untagged"):
            assert f"tags_{check}_{slug}" in controls



def test_all_legacy_cost_analysis_controls_covered():
    assert len(LEGACY_COST_ANALYSIS) == 13
    controls = mod_controls(bundled_mod_dir())
    for legacy, target in LEGACY_COST_ANALYSIS.items():
        if target in PLANNED_FEATURES:
            continue
        assert target in controls, f"{legacy} -> {target} does not exist"

