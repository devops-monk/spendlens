// ECS controls. Migrated from the internal steampipe-mod-aws-cost-analysis (ecs.sp), fixing:
//  * integer division (cpu / 1024 = 0 for < 1 vCPU) that meant the vCPU/memory checks never alarmed;
//  * memory reason printing the CPU value; missing account/region dimensions on task controls.
// Plus turbot/steampipe-mod-aws-thrifty (Apache-2.0) autoscaling and cluster utilization checks.

control "ecs_service_task_count_policy" {
  title       = "ECS services should run the number of tasks required for their environment"
  description = "sizing.ecs_desired_tasks_by_env, e.g. {prod: 3, '*': 1}. With more than one task, tasks must be spread over that many AZs."
  severity    = "medium"
  sql = <<-EOQ
    with running as (
      select
        cluster_arn,
        service_name,
        count(*) as running_tasks,
        count(distinct availability_zone) as azs
      from
        aws_ecs_task
      where
        last_status = 'RUNNING'
      group by
        cluster_arn,
        service_name
    ),
    policy as (
      select
        s.arn,
        s.title,
        s.cluster_arn,
        s.service_name,
        s.tags,
        s.region,
        s.account_id,
        s._ctx,
        coalesce($1::jsonb ->> lower(s.tags ->> $2), $1::jsonb ->> '*')::int as expected,
        coalesce(s.tags ->> $2, '(none)') as environment,
        coalesce(r.running_tasks, 0) as running_tasks,
        coalesce(r.azs, 0) as azs
      from
        aws_ecs_service as s
        left join running as r on r.cluster_arn = s.cluster_arn and r.service_name = s.service_name
    )
    select
      arn as resource,
      case
        when expected is null then 'skip'
        when running_tasks <> expected then 'alarm'
        when expected > 1 and azs < expected then 'alarm'
        else 'ok'
      end as status,
      case
        when expected is null then title || ' has no task policy for environment ' || environment || '.'
        when running_tasks <> expected then title || ' runs ' || running_tasks || ' task(s); environment ' || environment || ' requires ' || expected || '.'
        when expected > 1 and azs < expected then title || ' runs ' || running_tasks || ' tasks in only ' || azs || ' AZ(s).'
        else title || ' runs ' || running_tasks || ' task(s) across ' || azs || ' AZ(s) as required.'
      end as reason,
      split_part(cluster_arn, '/', 2) as cluster_name,
      environment
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      policy;
  EOQ
  param "ecs_desired_tasks_by_env" {
    default = var.ecs_desired_tasks_by_env
  }
  param "environment_tag" {
    default = var.environment_tag
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ecs"
    resource_type   = "ecs_service"
  }
}

control "ecs_task_cpu_over_max" {
  title       = "ECS tasks should not exceed the company vCPU maximum"
  description = "Task vCPU above sizing.ecs_task_max_vcpu."
  severity    = "low"
  sql = <<-EOQ
    select
      task_arn as resource,
      case
        when cpu is null then 'skip'
        when cpu::numeric / 1024 > $1 then 'alarm'
        else 'ok'
      end as status,
      'Task ' || split_part(task_arn, '/', 3) || ' (' || coalesce("group", '-') || ') in ' || cluster_name
        || ' has ' || round(cpu::numeric / 1024, 2) || ' vCPU (max ' || $1 || ').' as reason,
      cluster_name,
      round(cpu::numeric / 1024, 2) as vcpu
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ecs_task
    where
      last_status = 'RUNNING';
  EOQ
  param "ecs_task_max_vcpu" {
    default = var.ecs_task_max_vcpu
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ecs"
    resource_type   = "ecs_task"
  }
}

control "ecs_task_memory_over_max" {
  title       = "ECS tasks should not exceed the company memory maximum"
  description = "Task memory above sizing.ecs_task_max_memory_gb."
  severity    = "low"
  sql = <<-EOQ
    select
      task_arn as resource,
      case
        when memory is null then 'skip'
        when memory::numeric / 1024 > $1 then 'alarm'
        else 'ok'
      end as status,
      'Task ' || split_part(task_arn, '/', 3) || ' (' || coalesce("group", '-') || ') in ' || cluster_name
        || ' has ' || round(memory::numeric / 1024, 2) || ' GB memory (max ' || $1 || ' GB).' as reason,
      cluster_name,
      round(memory::numeric / 1024, 2) as memory_gb
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ecs_task
    where
      last_status = 'RUNNING';
  EOQ
  param "ecs_task_max_memory_gb" {
    default = var.ecs_task_max_memory_gb
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ecs"
    resource_type   = "ecs_task"
  }
}

control "ecs_service_without_autoscaling" {
  title       = "Fargate ECS services should use auto scaling"
  description = "Services without an Application Auto Scaling target run at fixed (peak) capacity."
  severity    = "low"
  sql = <<-EOQ
    with scaled as (
      select distinct
        split_part(resource_id, '/', 2) as cluster_name,
        split_part(resource_id, '/', 3) as service_name
      from
        aws_appautoscaling_target
      where
        service_namespace = 'ecs'
    )
    select
      s.arn as resource,
      case
        when coalesce(s.launch_type, '') <> 'FARGATE' then 'skip'
        when a.service_name is null then 'alarm'
        else 'ok'
      end as status,
      case
        when coalesce(s.launch_type, '') <> 'FARGATE' then s.title || ' does not run on Fargate.'
        when a.service_name is null then s.title || ' has no auto scaling target.'
        else s.title || ' uses auto scaling.'
      end as reason,
      split_part(s.cluster_arn, '/', 2) as cluster_name
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "s.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "s.")}
    from
      aws_ecs_service as s
      left join scaled as a
        on a.service_name = s.service_name and a.cluster_name = split_part(s.cluster_arn, '/', 2);
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "ecs"
    resource_type   = "ecs_service"
  }
}

control "ecs_cluster_low_utilization" {
  title       = "ECS clusters with low CPU utilization should be consolidated"
  description = "Average daily peak cluster CPU over 30 days below sizing.ecs_cluster_cpu_low (alarm) / _high (info)."
  severity    = "medium"
  sql = <<-EOQ
    with usage as (
      select
        cluster_name,
        round((sum(maximum) / count(maximum))::numeric, 1) as avg_max,
        count(maximum) as days
      from
        aws_ecs_cluster_metric_cpu_utilization_daily
      where
        timestamp >= now() - interval '30 days'
      group by
        cluster_name
    )
    select
      c.cluster_arn as resource,
      case
        when u.avg_max is null then 'info'
        when u.avg_max < $1 then 'alarm'
        when u.avg_max < $2 then 'info'
        else 'ok'
      end as status,
      case
        when u.avg_max is null then 'CloudWatch metrics not available for ' || c.cluster_name || '.'
        else c.cluster_name || ' averages ' || u.avg_max || '% peak CPU over ' || u.days
          || ' days (low below ' || $1::text || '%, review below ' || $2::text || '%).'
      end as reason
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
    from
      aws_ecs_cluster as c
      left join usage as u on u.cluster_name = c.cluster_name;
  EOQ
  param "ecs_cluster_cpu_low" {
    default = var.ecs_cluster_cpu_low
  }
  param "ecs_cluster_cpu_high" {
    default = var.ecs_cluster_cpu_high
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ecs"
    resource_type   = "ecs_cluster"
  }
}
