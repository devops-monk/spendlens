// Benchmarks for `spendlens rightsize` and `spendlens waste`.
// To add a control: define it in a service file with tags {spendlens_check, service, resource_type}
// and list it here. tests/test_finops_mod.py fails if a control is not in exactly one of these.

benchmark "rightsize" {
  title       = "Rightsizing and company standards"
  description = "Over-provisioned resources and resources not provisioned to company standards."
  children = [
    control.ec2_instance_type_not_allowed,
    control.ec2_instance_older_generation,
    control.ec2_instance_low_utilization,
    control.ec2_instance_without_graviton,
    control.ec2_instance_reservation_candidate,
    control.ec2_reserved_instance_expiring,
    control.ebs_volume_gp2,
    control.ebs_volume_io1,
    control.ebs_volume_oversized,
    control.ebs_volume_high_iops,
    control.ebs_volume_low_usage,
    control.rds_instance_class_not_allowed,
    control.rds_instance_low_utilization,
    control.rds_instance_reservation_candidate,
    control.rds_instance_without_graviton,
    control.rds_instance_multi_az_policy,
    control.rds_instance_performance_insights_policy,
    control.rds_instance_backup_retention_policy,
    control.ecs_service_task_count_policy,
    control.ecs_task_cpu_over_max,
    control.ecs_task_memory_over_max,
    control.ecs_service_without_autoscaling,
    control.ecs_cluster_low_utilization,
    control.lambda_function_high_error_rate,
    control.lambda_function_excessive_timeout,
    control.lambda_function_without_graviton,
    control.cloudwatch_log_group_retention_policy,
    control.cloudwatch_log_group_kms_policy,
    control.elasticache_cluster_reservation_candidate,
    control.ebs_volume_piops_to_gp3,
    control.ec2_instance_ebs_delete_on_termination,
    control.dynamodb_table_without_autoscaling,
    control.rds_instance_extended_support_version,
    control.ecs_container_instance_without_graviton,
    control.eks_node_group_without_graviton,
    control.emr_instance_group_older_generation,
    control.redshift_cluster_low_utilization,
    control.redshift_cluster_reservation_candidate,
    control.redshift_cluster_without_pause_schedule,
    control.cloudfront_distribution_price_class_all,
    control.route53_record_low_ttl,
  ]
}

benchmark "waste" {
  title       = "Idle and orphaned resources"
  description = "Resources that are billed but unused: candidates for deletion after human review."
  children = [
    control.ec2_instance_stopped_long,
    control.ec2_application_load_balancer_unused,
    control.ec2_network_load_balancer_unused,
    control.ec2_gateway_load_balancer_unused,
    control.ec2_classic_load_balancer_unused,
    control.ebs_volume_unattached,
    control.ebs_volume_on_stopped_instance,
    control.ebs_snapshot_old,
    control.ebs_snapshot_orphaned,
    control.rds_instance_low_connections,
    control.rds_snapshot_orphaned,
    control.vpc_eip_unattached,
    control.vpc_nat_gateway_unused,
    control.secretsmanager_secret_unused,
    control.ecr_repository_unused,
    control.s3_bucket_without_lifecycle,
    control.dynamodb_table_stale,
    control.ec2_ami_old,
    control.s3_bucket_versioned_without_lifecycle,
    control.ecr_repository_without_lifecycle,
    control.emr_cluster_idle,
    control.route53_health_check_unused,
    control.cloudtrail_trail_redundant,
    control.kms_key_disabled,
  ]
}
