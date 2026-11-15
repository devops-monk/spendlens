// EC2 rightsizing and waste controls.
// Derived from turbot/steampipe-mod-aws-thrifty (Apache-2.0) and the internal steampipe-mod-aws-cost-analysis.
// Extra output columns (instance_type, ...) become dimensions used by spendlens savings estimators.

control "ec2_instance_type_not_allowed" {
  title       = "EC2 instances should use an allowed instance type"
  description = "Company standard: running instances must match sizing.ec2_allowed_instance_types (SQL LIKE patterns)."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when instance_state not in ('running', 'pending', 'rebooting') then 'skip'
        when instance_type like any ($1::text[]) then 'ok'
        else 'alarm'
      end as status,
      title || ' is ' || instance_type || ' (' || instance_state || ').' as reason,
      instance_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ec2_instance;
  EOQ
  param "ec2_allowed_instance_types" {
    default = var.ec2_allowed_instance_types
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ec2"
    resource_type   = "ec2_instance"
  }
}

control "ec2_instance_older_generation" {
  title       = "EC2 instances should not use previous-generation instance families"
  description = "t2, m3, m4, c3, c4, r3, r4 are slower and more expensive than current generations."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when instance_state not in ('running', 'pending', 'rebooting') then 'skip'
        when split_part(instance_type, '.', 1) in ('t1', 't2', 'm1', 'm2', 'm3', 'm4', 'c1', 'c3', 'c4', 'r3', 'r4', 'i2', 'd2') then 'alarm'
        else 'ok'
      end as status,
      title || ' uses ' || instance_type || '.' as reason,
      instance_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ec2_instance;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "ec2"
    resource_type   = "ec2_instance"
  }
}

control "ec2_instance_low_utilization" {
  title       = "EC2 instances with low CPU utilization should be downsized"
  description = "Peak daily CPU over the last 30 days is below sizing.ec2_cpu_low (alarm) or sizing.ec2_cpu_high (info)."
  severity    = "high"
  sql = <<-EOQ
    with utilization as (
      select
        instance_id,
        round(max(maximum)::numeric, 1) as peak_cpu,
        count(*) as days
      from
        aws_ec2_instance_metric_cpu_utilization_daily
      where
        timestamp >= now() - interval '30 days'
      group by
        instance_id
    )
    select
      i.arn as resource,
      case
        when i.instance_state <> 'running' then 'skip'
        when u.peak_cpu is null then 'info'
        when u.peak_cpu < $1 then 'alarm'
        when u.peak_cpu < $2 then 'info'
        else 'ok'
      end as status,
      case
        when i.instance_state <> 'running' then i.title || ' is ' || i.instance_state || '.'
        when u.peak_cpu is null then 'CloudWatch metrics not available for ' || i.title || '.'
        else i.title || ' (' || i.instance_type || ') peaked at ' || u.peak_cpu || '% CPU over ' || u.days || ' days.'
      end as reason,
      i.instance_type
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "i.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "i.")}
    from
      aws_ec2_instance as i
      left join utilization as u on u.instance_id = i.instance_id;
  EOQ
  param "ec2_cpu_low" {
    default = var.ec2_cpu_low
  }
  param "ec2_cpu_high" {
    default = var.ec2_cpu_high
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ec2"
    resource_type   = "ec2_instance"
  }
}

control "ec2_instance_without_graviton" {
  title       = "EC2 instances should consider Graviton (arm64)"
  description = "Graviton instances are typically ~20% cheaper for the same performance."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when instance_state not in ('running', 'pending', 'rebooting') then 'skip'
        when platform = 'windows' then 'skip'
        when architecture = 'arm64' then 'ok'
        else 'info'
      end as status,
      case
        when platform = 'windows' then title || ' runs Windows.'
        when architecture = 'arm64' then title || ' uses Graviton.'
        else title || ' (' || instance_type || ') is ' || coalesce(architecture, 'unknown') || '; consider a Graviton equivalent.'
      end as reason,
      instance_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ec2_instance;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "ec2"
    resource_type   = "ec2_instance"
  }
}

control "ec2_instance_reservation_candidate" {
  title       = "Long-running EC2 instances should be covered by a reservation or Savings Plan"
  description = "Instances running longer than sizing.ec2_running_instance_age_max_days are steady-state workloads."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when instance_state <> 'running' then 'skip'
        when instance_lifecycle = 'spot' then 'skip'
        when date_part('day', now() - launch_time) > $1 then 'alarm'
        else 'ok'
      end as status,
      title || ' (' || instance_type || ') has been running ' || date_part('day', now() - launch_time) || ' days.' as reason,
      instance_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ec2_instance;
  EOQ
  param "ec2_running_instance_age_max_days" {
    default = var.ec2_running_instance_age_max_days
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ec2"
    resource_type   = "ec2_instance"
  }
}

control "ec2_reserved_instance_expiring" {
  title       = "EC2 reserved instances expiring soon should be reviewed"
  description = "Active reservations ending within sizing.ec2_reserved_instance_expiration_warning_days."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when instance_state <> 'active' then 'skip'
        when end_time < now() + ($1::int || ' days')::interval then 'alarm'
        else 'ok'
      end as status,
      title || ' (' || instance_count || ' x ' || instance_type || ') ends ' || to_char(end_time, 'YYYY-MM-DD') || '.' as reason,
      instance_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ec2_reserved_instance;
  EOQ
  param "ec2_reserved_instance_expiration_warning_days" {
    default = var.ec2_reserved_instance_expiration_warning_days
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ec2"
    resource_type   = "ec2_reserved_instance"
  }
}

control "ec2_instance_stopped_long" {
  title       = "EC2 instances stopped for a long time should be terminated or snapshotted"
  description = "Stopped instances still pay for attached EBS volumes and Elastic IPs."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when instance_state <> 'stopped' then 'skip'
        when state_transition_time < now() - ($1::int || ' days')::interval then 'alarm'
        else 'ok'
      end as status,
      case
        when instance_state <> 'stopped' then title || ' is ' || instance_state || '.'
        else title || ' has been stopped since ' || to_char(state_transition_time, 'YYYY-MM-DD') || '.'
      end as reason,
      instance_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ec2_instance;
  EOQ
  param "ec2_stopped_instance_max_days" {
    default = var.ec2_stopped_instance_max_days
  }
  tags = {
    spendlens_check = "waste"
    service         = "ec2"
    resource_type   = "ec2_instance"
  }
}

// ------------------------------------------------------------------ load balancers

control "ec2_application_load_balancer_unused" {
  title       = "Application load balancers without registered targets should be deleted"
  description = "An ALB is billed hourly even with no targets."
  severity    = "high"
  sql = <<-EOQ
    with targets as (
      select
        lb_arn,
        sum(jsonb_array_length(coalesce(tg.target_health_descriptions, '[]'::jsonb))) as target_count
      from
        aws_ec2_target_group as tg,
        jsonb_array_elements_text(tg.load_balancer_arns) as lb_arn
      group by
        lb_arn
    )
    select
      lb.arn as resource,
      case
        when coalesce(t.target_count, 0) = 0 then 'alarm'
        else 'ok'
      end as status,
      lb.title || ' has ' || coalesce(t.target_count, 0) || ' registered target(s).' as reason,
      'application' as lb_type
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "lb.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "lb.")}
    from
      aws_ec2_application_load_balancer as lb
      left join targets as t on t.lb_arn = lb.arn;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "elb"
    resource_type   = "ec2_application_load_balancer"
  }
}

control "ec2_network_load_balancer_unused" {
  title       = "Network load balancers without registered targets should be deleted"
  description = "Fixes upstream: an NLB with no target group at all was reported as ok."
  severity    = "high"
  sql = <<-EOQ
    with targets as (
      select
        lb_arn,
        sum(jsonb_array_length(coalesce(tg.target_health_descriptions, '[]'::jsonb))) as target_count
      from
        aws_ec2_target_group as tg,
        jsonb_array_elements_text(tg.load_balancer_arns) as lb_arn
      group by
        lb_arn
    )
    select
      lb.arn as resource,
      case
        when coalesce(t.target_count, 0) = 0 then 'alarm'
        else 'ok'
      end as status,
      lb.title || ' has ' || coalesce(t.target_count, 0) || ' registered target(s).' as reason,
      'network' as lb_type
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "lb.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "lb.")}
    from
      aws_ec2_network_load_balancer as lb
      left join targets as t on t.lb_arn = lb.arn;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "elb"
    resource_type   = "ec2_network_load_balancer"
  }
}

control "ec2_gateway_load_balancer_unused" {
  title       = "Gateway load balancers without registered targets should be deleted"
  description = "A GWLB is billed hourly even with no targets."
  severity    = "high"
  sql = <<-EOQ
    with targets as (
      select
        lb_arn,
        sum(jsonb_array_length(coalesce(tg.target_health_descriptions, '[]'::jsonb))) as target_count
      from
        aws_ec2_target_group as tg,
        jsonb_array_elements_text(tg.load_balancer_arns) as lb_arn
      group by
        lb_arn
    )
    select
      lb.arn as resource,
      case
        when coalesce(t.target_count, 0) = 0 then 'alarm'
        else 'ok'
      end as status,
      lb.title || ' has ' || coalesce(t.target_count, 0) || ' registered target(s).' as reason,
      'gateway' as lb_type
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "lb.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "lb.")}
    from
      aws_ec2_gateway_load_balancer as lb
      left join targets as t on t.lb_arn = lb.arn;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "elb"
    resource_type   = "ec2_gateway_load_balancer"
  }
}

control "ec2_classic_load_balancer_unused" {
  title       = "Classic load balancers without registered instances should be deleted"
  description = "A CLB is billed hourly even with no instances."
  severity    = "high"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when jsonb_array_length(coalesce(instances, '[]'::jsonb)) > 0 then 'ok'
        else 'alarm'
      end as status,
      title || ' has ' || jsonb_array_length(coalesce(instances, '[]'::jsonb)) || ' registered instance(s).' as reason,
      'classic' as lb_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ec2_classic_load_balancer;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "elb"
    resource_type   = "ec2_classic_load_balancer"
  }
}
