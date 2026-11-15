# Control mapping and upstream fixes

spendlens replaces the internal Steampipe mods. Every legacy control has an equivalent. This is enforced by `tests/test_legacy_coverage.py`.

Full catalog: [controls.md](controls.md).

## steampipe-mod-aws-tags (internal fork, 108 controls)

| Legacy | spendlens |
|---|---|
| `<type>_mandatory` (54 resource types) | `tags_mandatory_<type>` (`spendlens tags -k mandatory`) |
| `<type>_untagged` (54 resource types) | `tags_untagged_<type>` (`spendlens tags -k untagged`) |
| `mandatory_tags` variable | `tagging.mandatory` in finops.yaml. Duplicates are removed: the legacy default listed `Environment` twice. |

spendlens adds the following:
- 16 more resource types, for 70 in total.
- Three more checks: `prohibited`, `limit` and `expected_values` (from turbot/steampipe-mod-aws-tags).

## steampipe-mod-aws-cost-analysis (13 controls)

| Legacy control | spendlens control | Change |
|---|---|---|
| `latest_rds_instance_types` | `rds_instance_class_not_allowed` | The hardcoded t2/m3/m4 vs m5/t3 check is now the `sizing.rds_allowed_instance_classes` allow-list. |
| `long_running_rds_db_instances` | `rds_instance_reservation_candidate` | Same thresholds (`rds_running_age_max_days` / `_warning_days`). |
| `rds_db_low_connection_count` | `rds_instance_low_connections` | Moved to `waste`: zero connections means the instance is idle. Adds a $ estimate. |
| `rds_db_low_utilization` | `rds_instance_low_utilization` | Adds a $ estimate. |
| `rds_performance_insights_enabled` | `rds_instance_performance_insights_policy` | Environment-aware (`rds_performance_insights_by_env`). Legacy used one global boolean, although its description says prod and non-prod need different settings. |
| `rds_backup_retention_period` | `rds_instance_backup_retention_policy` | Environment-aware (`rds_backup_retention_max_days_by_env`). |
| `rds_multi_az` | `rds_instance_multi_az_policy` | Environment-aware (`rds_multi_az_by_env`). Adds a $ estimate. |
| `ecs_service_correct_no_of_tasks` | `ecs_service_task_count_policy` | Environment-aware (`ecs_desired_tasks_by_env`, e.g. prod 3 / other 1), as the legacy description intended. |
| `ecs_tasks_cpu_usage` | `ecs_task_cpu_over_max` | **Bug fix:** `cpu / 1024` was integer division, so tasks under 1 vCPU never alarmed. Also adds account/region dimensions. |
| `ecs_tasks_memory_usage` | `ecs_task_memory_over_max` | **Bug fix:** same integer division. The reason text also printed the CPU value instead of memory. |
| `log_group_encryption_at_rest_enabled` | `cloudwatch_log_group_kms_policy` | Can be switched off with `cloudwatch_log_group_kms_required`. |
| `cloudwatch_log_group_retention_period_in_days` | `cloudwatch_log_group_retention_policy` | The reason now shows stored GB. |
| `full_month_cost_changes` | `spendlens cost diff` | **Bug fix:** `where prev.cost != base.cost` dropped new and removed services, because comparing against null removes the row. They now appear as `new` / `gone`. The threshold (`cost_explorer_service_cost_max_cost_units = 10`) is `cost.change_alarm_abs`, plus `cost.change_alarm_pct`. Any month, month range or month-to-date can be compared, grouped by service, account, region, usage type or tag. |

## Upstream fixes in controls taken from turbot/steampipe-mod-aws-thrifty

- **NLB/GWLB unused:** a load balancer with no target group was reported as `ok`. ALB/NLB also produced duplicate rows when a load balancer had several target groups.
- **NAT gateway unused:** a gateway with no metric datapoints was reported as in use.
- **Lambda error rate:** errors were joined to every day of invocations, which inflated the rate.
- **Lambda excessive timeout:** it alarmed whenever the timeout was over 1.1× the average, which is nearly every function. The multiple is now a parameter, defaulting to 3×.
- **ECS cluster, Redshift and CloudFront:** these reported names or IDs instead of ARNs.
- **EKS Graviton:** `ami_type = 'CUSTOM%'` (equality with a wildcard) never matched, and it used `arm_64` where AWS uses `arm64`.
- **EMR previous generation:** the pattern `like 'i2,m1.%'` was malformed, and only master groups were checked.
- **RDS unsupported versions:** hardcoded to MySQL 5.7 and PostgreSQL 11. Now the `sizing.rds_extended_support_versions` map, which you should review against AWS's Extended Support calendar.
- **Tags expected values (turbot/steampipe-mod-aws-tags):** a missing pair of parentheses in `a and b or c` added a spurious `skip` row for every tagged resource.

## Other repos reviewed

| Repo | Used |
|---|---|
| steampipe-mod-aws-compliance | Five cost-relevant controls: old AMIs, versioned S3 without noncurrent expiration, ECR lifecycle, disabled KMS keys, EBS delete-on-termination. |
| steampipe-mod-aws-well-architected, -top-10, -perimeter, -insights, terraform-aws-compliance, gitlab-insights | No cost controls (security/reliability/inventory). Not used. |
| tailpipe-mod-aws-cost-and-usage-insights | CUR-based cost queries. Input for phase 4 (cost diff / showback). |
| tailpipe cloudtrail / vpc-flow detections | Security detections. Not used. |

Deliberately skipped:
- **Unused CloudWatch log streams:** there can be millions per account, so scanning them is too slow.
- **API Gateway caching:** it can raise or lower cost, so it doesn't make a clear rule.
- **IAM, security group and ENI hygiene:** these resources cost nothing.
