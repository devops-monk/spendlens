// RDS controls. Migrated from the internal steampipe-mod-aws-cost-analysis (rds.sp) and
// turbot/steampipe-mod-aws-thrifty (Apache-2.0).
// *_by_env policies look up lower(tags ->> environment_tag) in the map, falling back to the "*" key.

control "rds_instance_class_not_allowed" {
  title       = "RDS instances should use an allowed instance class"
  description = "Company standard: classes must match sizing.rds_allowed_instance_classes (replaces the hardcoded t2/m3/m4 check)."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when class like any ($1::text[]) then 'ok'
        else 'alarm'
      end as status,
      title || ' uses ' || class || '.' as reason,
      class as db_class,
      multi_az
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_rds_db_instance;
  EOQ
  param "rds_allowed_instance_classes" {
    default = var.rds_allowed_instance_classes
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "rds_instance_low_utilization" {
  title       = "RDS instances with low CPU utilization should be downsized"
  description = "Average daily peak CPU over 30 days at or below sizing.rds_cpu_low (alarm) / rds_cpu_high (info)."
  severity    = "high"
  sql = <<-EOQ
    with usage as (
      select
        db_instance_identifier,
        round((sum(maximum) / count(maximum))::numeric, 1) as avg_max,
        count(maximum) as days
      from
        aws_rds_db_instance_metric_cpu_utilization_daily
      where
        timestamp >= now() - interval '30 days'
      group by
        db_instance_identifier
    )
    select
      i.arn as resource,
      case
        when u.avg_max is null then 'info'
        when u.avg_max <= $1 then 'alarm'
        when u.avg_max <= $2 then 'info'
        else 'ok'
      end as status,
      case
        when u.avg_max is null then 'CloudWatch metrics not available for ' || i.title || '.'
        else i.title || ' (' || i.class || ') averages ' || u.avg_max || '% peak CPU over ' || u.days || ' days.'
      end as reason,
      i.class as db_class,
      i.multi_az
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "i.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "i.")}
    from
      aws_rds_db_instance as i
      left join usage as u on u.db_instance_identifier = i.db_instance_identifier;
  EOQ
  param "rds_cpu_low" {
    default = var.rds_cpu_low
  }
  param "rds_cpu_high" {
    default = var.rds_cpu_high
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "rds_instance_low_connections" {
  title       = "RDS instances with few or no connections should be reviewed"
  description = "Zero connections for 30 days = idle (alarm); below sizing.rds_avg_connections_min = info."
  severity    = "high"
  sql = <<-EOQ
    with usage as (
      select
        db_instance_identifier,
        round(sum(maximum) / count(maximum)) as avg_max,
        count(maximum) as days
      from
        aws_rds_db_instance_metric_connections_daily
      where
        timestamp >= now() - interval '30 days'
      group by
        db_instance_identifier
    )
    select
      i.arn as resource,
      case
        when u.avg_max is null then 'info'
        when u.avg_max = 0 then 'alarm'
        when u.avg_max < $1 then 'info'
        else 'ok'
      end as status,
      case
        when u.avg_max is null then 'CloudWatch metrics not available for ' || i.title || '.'
        when u.avg_max = 0 then i.title || ' has had no connections in ' || u.days || ' days.'
        else i.title || ' averages ' || u.avg_max || ' peak connections/day over ' || u.days || ' days.'
      end as reason,
      i.class as db_class,
      i.multi_az
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "i.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "i.")}
    from
      aws_rds_db_instance as i
      left join usage as u on u.db_instance_identifier = i.db_instance_identifier;
  EOQ
  param "rds_avg_connections_min" {
    default = var.rds_avg_connections_min
  }
  tags = {
    spendlens_check = "waste"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "rds_instance_reservation_candidate" {
  title       = "Long-running RDS instances should be covered by reserved instances"
  description = "Older than sizing.rds_running_age_max_days = alarm; older than rds_running_age_warning_days = info."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when date_part('day', now() - create_time) > $1 then 'alarm'
        when date_part('day', now() - create_time) > $2 then 'info'
        else 'ok'
      end as status,
      title || ' (' || class || ') was created ' || date_part('day', now() - create_time) || ' days ago.' as reason,
      class as db_class,
      multi_az
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_rds_db_instance;
  EOQ
  param "rds_running_age_max_days" {
    default = var.rds_running_age_max_days
  }
  param "rds_running_age_warning_days" {
    default = var.rds_running_age_warning_days
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "rds_instance_without_graviton" {
  title       = "RDS instances should consider Graviton classes"
  description = "Graviton (db.*g.*) classes are typically ~10-20% cheaper."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when engine like 'sqlserver%' or engine like 'oracle%' then 'skip'
        when split_part(class, '.', 2) ~ '^[a-z]+[0-9]+g' then 'ok'
        else 'info'
      end as status,
      title || ' uses ' || class || '.' as reason,
      class as db_class,
      multi_az
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_rds_db_instance;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "rds_instance_multi_az_policy" {
  title       = "RDS Multi-AZ should match the environment policy"
  description = "sizing.rds_multi_az_by_env, e.g. {prod: true, '*': false}: Multi-AZ doubles instance cost in non-prod."
  severity    = "medium"
  sql = <<-EOQ
    with policy as (
      select
        arn, title, class, multi_az, performance_insights_enabled, backup_retention_period,
        tags, region, account_id, _ctx,
        coalesce($1::jsonb ->> lower(tags ->> $2), $1::jsonb ->> '*')::bool as expected,
        coalesce(tags ->> $2, '(none)') as environment
      from
        aws_rds_db_instance
    )
    select
      arn as resource,
      case
        when expected is null then 'skip'
        when multi_az = expected then 'ok'
        else 'alarm'
      end as status,
      title || ' Multi-AZ is ' || case when multi_az then 'enabled' else 'disabled' end
        || '; policy for environment ' || environment || ' is '
        || coalesce(case when expected then 'enabled' else 'disabled' end, 'unset') || '.' as reason,
      class as db_class,
      multi_az,
      environment
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      policy;
  EOQ
  param "rds_multi_az_by_env" {
    default = var.rds_multi_az_by_env
  }
  param "environment_tag" {
    default = var.environment_tag
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "rds_instance_performance_insights_policy" {
  title       = "RDS Performance Insights should match the environment policy"
  description = "sizing.rds_performance_insights_by_env: enabled for prod, disabled for non-prod by default."
  severity    = "low"
  sql = <<-EOQ
    with policy as (
      select
        arn, title, class, multi_az, performance_insights_enabled, backup_retention_period,
        tags, region, account_id, _ctx,
        coalesce($1::jsonb ->> lower(tags ->> $2), $1::jsonb ->> '*')::bool as expected,
        coalesce(tags ->> $2, '(none)') as environment
      from
        aws_rds_db_instance
    )
    select
      arn as resource,
      case
        when expected is null then 'skip'
        when coalesce(performance_insights_enabled, false) = expected then 'ok'
        else 'alarm'
      end as status,
      title || ' Performance Insights is ' || case when performance_insights_enabled then 'enabled' else 'disabled' end
        || '; policy for environment ' || environment || ' is '
        || coalesce(case when expected then 'enabled' else 'disabled' end, 'unset') || '.' as reason,
      class as db_class,
      environment
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      policy;
  EOQ
  param "rds_performance_insights_by_env" {
    default = var.rds_performance_insights_by_env
  }
  param "environment_tag" {
    default = var.environment_tag
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "rds_instance_backup_retention_policy" {
  title       = "RDS backup retention should not exceed the environment policy"
  description = "sizing.rds_backup_retention_max_days_by_env, e.g. {prod: 14, '*': 1}."
  severity    = "low"
  sql = <<-EOQ
    with policy as (
      select
        arn, title, class, multi_az, performance_insights_enabled, backup_retention_period,
        tags, region, account_id, _ctx,
        coalesce($1::jsonb ->> lower(tags ->> $2), $1::jsonb ->> '*')::int as max_days,
        coalesce(tags ->> $2, '(none)') as environment
      from
        aws_rds_db_instance
    )
    select
      arn as resource,
      case
        when max_days is null then 'skip'
        when backup_retention_period > max_days then 'alarm'
        else 'ok'
      end as status,
      title || ' keeps backups for ' || backup_retention_period || ' day(s); max for environment '
        || environment || ' is ' || coalesce(max_days::text, 'unset') || '.' as reason,
      class as db_class,
      environment
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      policy;
  EOQ
  param "rds_backup_retention_max_days_by_env" {
    default = var.rds_backup_retention_max_days_by_env
  }
  param "environment_tag" {
    default = var.environment_tag
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "rds_snapshot_orphaned" {
  title       = "Manual RDS snapshots whose source instance is gone should be reviewed"
  description = "Older than sizing.rds_snapshot_orphaned_max_days = alarm, younger = info."
  severity    = "medium"
  sql = <<-EOQ
    select
      s.arn as resource,
      case
        when s.status <> 'available' then 'skip'
        when s.type <> 'manual' then 'skip'
        when i.db_instance_identifier is not null then 'ok'
        when s.create_time < now() - ($1::int || ' days')::interval then 'alarm'
        else 'info'
      end as status,
      case
        when i.db_instance_identifier is not null then s.title || ' source instance still exists.'
        else s.title || ' (' || s.allocated_storage || ' GB) source instance ' || s.db_instance_identifier
          || ' no longer exists; created ' || to_char(s.create_time, 'YYYY-MM-DD') || '.'
      end as reason,
      s.allocated_storage as size_gb
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "s.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "s.")}
    from
      aws_rds_db_snapshot as s
      left join aws_rds_db_instance as i
        on i.db_instance_identifier = s.db_instance_identifier and i.account_id = s.account_id and i.region = s.region;
  EOQ
  param "rds_snapshot_orphaned_max_days" {
    default = var.rds_snapshot_orphaned_max_days
  }
  tags = {
    spendlens_check = "waste"
    service         = "rds"
    resource_type   = "rds_db_snapshot"
  }
}
