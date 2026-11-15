// Additional cost controls collected from turbot/steampipe-mod-aws-thrifty and turbot/steampipe-mod-aws-compliance
// (Apache-2.0), with fixes noted per control.

// ------------------------------------------------------------------ storage / data

control "ebs_volume_piops_to_gp3" {
  title       = "Provisioned-IOPS volumes with modest IOPS should move to gp3"
  description = "io1/io2 volumes at or below sizing.ebs_volume_piops_to_gp3_max_iops can usually run on gp3 for much less."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when volume_type not in ('io1', 'io2') then 'skip'
        when iops <= $1 then 'alarm'
        else 'ok'
      end as status,
      volume_id || ' (' || volume_type || ', ' || size || ' GB) has ' || coalesce(iops, 0) || ' provisioned IOPS.' as reason,
      volume_type,
      size as size_gb,
      iops
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ebs_volume;
  EOQ
  param "ebs_volume_piops_to_gp3_max_iops" {
    default = var.ebs_volume_piops_to_gp3_max_iops
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ebs"
    resource_type   = "ebs_volume"
  }
}

control "ec2_instance_ebs_delete_on_termination" {
  title       = "EC2 instance EBS volumes should be deleted on termination"
  description = "Company standard: prevents orphaned volumes when instances are terminated (from aws-compliance)."
  severity    = "low"
  sql = <<-EOQ
    with mappings as (
      select
        arn,
        count(*) filter (where not coalesce((bdm -> 'Ebs' ->> 'DeleteOnTermination')::bool, false)) as keep_count
      from
        aws_ec2_instance,
        jsonb_array_elements(coalesce(block_device_mappings, '[]'::jsonb)) as bdm
      group by
        arn
    )
    select
      i.arn as resource,
      case
        when i.instance_state = 'terminated' then 'skip'
        when coalesce(m.keep_count, 0) = 0 then 'ok'
        else 'alarm'
      end as status,
      i.title || ' has ' || coalesce(m.keep_count, 0) || ' EBS volume(s) that survive termination.' as reason
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "i.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "i.")}
    from
      aws_ec2_instance as i
      left join mappings as m on m.arn = i.arn;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "ec2"
    resource_type   = "ec2_instance"
  }
}

control "ec2_ami_old" {
  title       = "Old self-owned AMIs should be deregistered with their snapshots"
  description = "AMIs older than sizing.ec2_ami_age_max_days keep their EBS snapshots billing (from aws-compliance)."
  severity    = "low"
  sql = <<-EOQ
    with sizes as (
      select
        image_id,
        sum(coalesce((bdm -> 'Ebs' ->> 'VolumeSize')::numeric, 0)) as size_gb
      from
        aws_ec2_ami,
        jsonb_array_elements(coalesce(block_device_mappings, '[]'::jsonb)) as bdm
      group by
        image_id
    )
    select
      'arn:' || a.partition || ':ec2:' || a.region || ':' || a.account_id || ':image/' || a.image_id as resource,
      case
        when a.creation_date::timestamptz < now() - ($1::int || ' days')::interval then 'alarm'
        else 'ok'
      end as status,
      coalesce(a.name, a.image_id) || ' was created ' || to_char(a.creation_date::timestamptz, 'YYYY-MM-DD')
        || ' (' || coalesce(s.size_gb, 0) || ' GB of snapshots).' as reason,
      coalesce(s.size_gb, 0) as size_gb
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "a.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "a.")}
    from
      aws_ec2_ami as a
      left join sizes as s on s.image_id = a.image_id;
  EOQ
  param "ec2_ami_age_max_days" {
    default = var.ec2_ami_age_max_days
  }
  tags = {
    spendlens_check = "waste"
    service         = "ec2"
    resource_type   = "ec2_ami"
  }
}

control "s3_bucket_versioned_without_lifecycle" {
  title       = "Versioned S3 buckets should expire noncurrent versions"
  description = "Without a noncurrent-version expiration rule, every overwrite/delete keeps a billed copy forever (from aws-compliance)."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when not coalesce(versioning_enabled, false) then 'skip'
        when exists (
          select 1
          from jsonb_array_elements(coalesce(lifecycle_rules, '[]'::jsonb)) as r
          where r ->> 'Status' = 'Enabled' and r ? 'NoncurrentVersionExpiration'
        ) then 'ok'
        else 'alarm'
      end as status,
      case
        when not coalesce(versioning_enabled, false) then name || ' is not versioned.'
        else name || ' is versioned; noncurrent-version expiration '
          || case when exists (
               select 1
               from jsonb_array_elements(coalesce(lifecycle_rules, '[]'::jsonb)) as r
               where r ->> 'Status' = 'Enabled' and r ? 'NoncurrentVersionExpiration'
             ) then 'is configured.' else 'is missing.' end
      end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_s3_bucket;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "s3"
    resource_type   = "s3_bucket"
  }
}

control "ecr_repository_without_lifecycle" {
  title       = "ECR repositories should have a lifecycle policy"
  description = "Without one, every pushed image is stored forever (from aws-compliance)."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when lifecycle_policy is null then 'alarm'
        else 'ok'
      end as status,
      title || case when lifecycle_policy is null then ' has no lifecycle policy.' else ' has a lifecycle policy.' end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ecr_repository;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "ecr"
    resource_type   = "ecr_repository"
  }
}

control "dynamodb_table_without_autoscaling" {
  title       = "Provisioned DynamoDB tables should use auto scaling or on-demand"
  description = "Fixed provisioned capacity is paid for at peak 24/7."
  severity    = "medium"
  sql = <<-EOQ
    with scaled as (
      select
        split_part(resource_id, '/', 2) as table_name,
        region,
        account_id,
        count(*) as targets
      from
        aws_appautoscaling_target
      where
        service_namespace = 'dynamodb'
      group by
        1, 2, 3
    )
    select
      t.arn as resource,
      case
        when t.billing_mode = 'PAY_PER_REQUEST' then 'ok'
        when coalesce(a.targets, 0) > 0 then 'ok'
        else 'alarm'
      end as status,
      case
        when t.billing_mode = 'PAY_PER_REQUEST' then t.name || ' uses on-demand capacity.'
        when coalesce(a.targets, 0) > 0 then t.name || ' has auto scaling.'
        else t.name || ' uses provisioned capacity (' || coalesce(t.read_capacity, 0) || ' RCU / '
          || coalesce(t.write_capacity, 0) || ' WCU) without auto scaling.'
      end as reason,
      t.billing_mode
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "t.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "t.")}
    from
      aws_dynamodb_table as t
      left join scaled as a on a.table_name = t.name and a.region = t.region and a.account_id = t.account_id;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "dynamodb"
    resource_type   = "dynamodb_table"
  }
}

// ------------------------------------------------------------------ compute

control "rds_instance_extended_support_version" {
  title       = "RDS instances on versions in paid Extended Support should be upgraded"
  description = "Engines/versions in sizing.rds_extended_support_versions incur per-vCPU-hour Extended Support charges. Review the defaults against AWS's current calendar."
  severity    = "high"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when not ($1::jsonb ? engine) then 'skip'
        when exists (
          select 1
          from jsonb_array_elements_text($1::jsonb -> engine) as v
          where engine_version = v or engine_version like v || '.%'
        ) then 'alarm'
        else 'ok'
      end as status,
      title || ' runs ' || engine || ' ' || engine_version || '.' as reason,
      class as db_class,
      engine,
      engine_version
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_rds_db_instance;
  EOQ
  param "rds_extended_support_versions" {
    default = var.rds_extended_support_versions
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "rds"
    resource_type   = "rds_db_instance"
  }
}

control "ecs_container_instance_without_graviton" {
  title       = "ECS container instances should consider Graviton"
  description = "EC2-backed ECS capacity on x86; Graviton is typically ~20% cheaper."
  severity    = "low"
  sql = <<-EOQ
    select
      c.arn as resource,
      case
        when i.instance_id is null then 'skip'
        when i.platform = 'windows' then 'skip'
        when i.architecture = 'arm64' then 'ok'
        else 'info'
      end as status,
      case
        when i.instance_id is null then c.ec2_instance_id || ' instance details not found.'
        when i.architecture = 'arm64' then i.title || ' uses Graviton.'
        else i.title || ' (' || i.instance_type || ') is ' || coalesce(i.architecture, 'unknown') || '.'
      end as reason,
      i.instance_type
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
    from
      aws_ecs_container_instance as c
      left join aws_ec2_instance as i on i.instance_id = c.ec2_instance_id;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "ecs"
    resource_type   = "ecs_container_instance"
  }
}

control "eks_node_group_without_graviton" {
  title       = "EKS managed node groups should consider Graviton"
  description = "Fixes upstream: '=' with a wildcard never matched CUSTOM AMIs. CUSTOM/Windows/Bottlerocket x86 AMIs are reported as info."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when ami_type like '%ARM%' then 'ok'
        when ami_type like 'WINDOWS%' then 'skip'
        when ami_type = 'CUSTOM' then 'info'
        else 'info'
      end as status,
      title || ' uses AMI type ' || coalesce(ami_type, 'unknown') || ' (' || coalesce(instance_types::text, '[]') || ').' as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_eks_node_group;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "eks"
    resource_type   = "eks_node_group"
  }
}

control "emr_instance_group_older_generation" {
  title       = "EMR instance groups should not use previous-generation instance types"
  description = "Fixes upstream: malformed LIKE patterns and only master groups being checked."
  severity    = "low"
  sql = <<-EOQ
    select
      'arn:' || ig.partition || ':elasticmapreduce:' || ig.region || ':' || ig.account_id || ':cluster/' || ig.cluster_id || '/instance-group/' || ig.id as resource,
      case
        when ig.state in ('TERMINATED', 'ENDED') then 'skip'
        when split_part(ig.instance_type, '.', 1) in ('c1', 'c3', 'c4', 'cc2', 'cr1', 'g2', 'i2', 'm1', 'm2', 'm3', 'm4', 'r3', 'r4', 'd2') then 'alarm'
        else 'ok'
      end as status,
      ig.cluster_id || ' ' || ig.instance_group_type || ' group uses ' || ig.instance_type || '.' as reason,
      ig.instance_type
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "ig.")}
    from
      aws_emr_instance_group as ig;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "emr"
    resource_type   = "emr_instance_group"
  }
}

control "emr_cluster_idle" {
  title       = "EMR clusters idle for 30+ minutes should be terminated"
  description = "IsIdle reported for every 5-minute datapoint in the last 35 minutes."
  severity    = "high"
  sql = <<-EOQ
    with idle as (
      select
        id,
        count(*) as points,
        min(maximum) as min_idle
      from
        aws_emr_cluster_metric_is_idle
      where
        timestamp >= now() - interval '35 minutes'
      group by
        id
    )
    select
      c.cluster_arn as resource,
      case
        when c.state not in ('RUNNING', 'WAITING') then 'skip'
        when i.id is null then 'info'
        when i.points >= 6 and i.min_idle = 1 then 'alarm'
        else 'ok'
      end as status,
      case
        when c.state not in ('RUNNING', 'WAITING') then c.name || ' is ' || c.state || '.'
        when i.id is null then 'IsIdle metrics not available for ' || c.name || '.'
        when i.points >= 6 and i.min_idle = 1 then c.name || ' has been idle for at least 30 minutes.'
        else c.name || ' is active.'
      end as reason
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
    from
      aws_emr_cluster as c
      left join idle as i on i.id = c.id;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "emr"
    resource_type   = "emr_cluster"
  }
}

control "redshift_cluster_low_utilization" {
  title       = "Redshift clusters with low CPU utilization should be downsized"
  description = "Average daily peak CPU over 30 days below sizing.redshift_cpu_low (alarm) / _high (info)."
  severity    = "high"
  sql = <<-EOQ
    with usage as (
      select
        cluster_identifier,
        round((sum(maximum) / count(maximum))::numeric, 1) as avg_max,
        count(maximum) as days
      from
        aws_redshift_cluster_metric_cpu_utilization_daily
      where
        timestamp >= now() - interval '30 days'
      group by
        cluster_identifier
    )
    select
      c.arn as resource,
      case
        when u.avg_max is null then 'info'
        when u.avg_max < $1 then 'alarm'
        when u.avg_max < $2 then 'info'
        else 'ok'
      end as status,
      case
        when u.avg_max is null then 'CloudWatch metrics not available for ' || c.title || '.'
        else c.title || ' (' || c.node_type || ' x ' || c.number_of_nodes || ') averages ' || u.avg_max || '% peak CPU over ' || u.days || ' days.'
      end as reason,
      c.node_type
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
    from
      aws_redshift_cluster as c
      left join usage as u on u.cluster_identifier = c.cluster_identifier;
  EOQ
  param "redshift_cpu_low" {
    default = var.redshift_cpu_low
  }
  param "redshift_cpu_high" {
    default = var.redshift_cpu_high
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "redshift"
    resource_type   = "redshift_cluster"
  }
}

control "redshift_cluster_reservation_candidate" {
  title       = "Long-running Redshift clusters should be covered by reserved nodes"
  description = "Older than sizing.redshift_running_age_max_days = alarm; _warning_days = info."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when date_part('day', now() - cluster_create_time) > $1 then 'alarm'
        when date_part('day', now() - cluster_create_time) > $2 then 'info'
        else 'ok'
      end as status,
      title || ' (' || node_type || ' x ' || number_of_nodes || ') created ' || date_part('day', now() - cluster_create_time) || ' days ago.' as reason,
      node_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_redshift_cluster;
  EOQ
  param "redshift_running_age_max_days" {
    default = var.redshift_running_age_max_days
  }
  param "redshift_running_age_warning_days" {
    default = var.redshift_running_age_warning_days
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "redshift"
    resource_type   = "redshift_cluster"
  }
}

control "redshift_cluster_without_pause_schedule" {
  title       = "Redshift clusters should pause outside working hours"
  description = "No scheduled pause + resume actions; non-prod clusters often only need business hours."
  severity    = "low"
  sql = <<-EOQ
    with actions as (
      select
        arn,
        bool_or(s -> 'TargetAction' ? 'PauseCluster') as has_pause,
        bool_or(s -> 'TargetAction' ? 'ResumeCluster') as has_resume
      from
        aws_redshift_cluster,
        jsonb_array_elements(coalesce(scheduled_actions, '[]'::jsonb)) as s
      group by
        arn
    )
    select
      c.arn as resource,
      case
        when coalesce(a.has_pause, false) and coalesce(a.has_resume, false) then 'ok'
        else 'info'
      end as status,
      c.title || case when coalesce(a.has_pause, false) and coalesce(a.has_resume, false)
        then ' has a pause/resume schedule.' else ' has no pause/resume schedule.' end as reason,
      c.node_type
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "c.")}
    from
      aws_redshift_cluster as c
      left join actions as a on a.arn = c.arn;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "redshift"
    resource_type   = "redshift_cluster"
  }
}

// ------------------------------------------------------------------ edge / network / misc

control "cloudfront_distribution_price_class_all" {
  title       = "CloudFront distributions using PriceClass_All should be reviewed"
  description = "PriceClass_All serves from the most expensive edge locations; PriceClass_100/200 may suffice for your audience."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when price_class = 'PriceClass_All' then 'info'
        else 'ok'
      end as status,
      title || ' uses ' || price_class || '.' as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_cloudfront_distribution;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "cloudfront"
    resource_type   = "cloudfront_distribution"
  }
}

control "route53_record_low_ttl" {
  title       = "Route 53 records with low TTLs should be reviewed"
  description = "Low TTLs multiply DNS query charges; below sizing.route53_record_min_ttl is reported as info. Alias records are skipped."
  severity    = "low"
  sql = <<-EOQ
    select
      'arn:' || r.partition || ':route53:::hostedzone/' || r.zone_id || '/recordset/' || r.name || '/' || r.type as resource,
      case
        when r.ttl is null then 'skip'
        when r.ttl::int < $1 then 'info'
        else 'ok'
      end as status,
      r.name || ' ' || r.type || ' TTL is ' || coalesce(r.ttl::text, 'alias') || 's.' as reason
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "r.")}
    from
      aws_route53_record as r;
  EOQ
  param "route53_record_min_ttl" {
    default = var.route53_record_min_ttl
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "route53"
    resource_type   = "route53_record"
  }
}

control "route53_health_check_unused" {
  title       = "Route 53 health checks not referenced by any record should be deleted"
  description = "Health checks are billed monthly whether or not a record uses them."
  severity    = "low"
  sql = <<-EOQ
    with used as (
      select distinct
        health_check_id
      from
        aws_route53_record
      where
        health_check_id is not null
    )
    select
      'arn:' || h.partition || ':route53:::healthcheck/' || h.id as resource,
      case
        when u.health_check_id is null then 'alarm'
        else 'ok'
      end as status,
      h.title || case when u.health_check_id is null then ' is not used by any record.' else ' is used by a record.' end as reason
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "h.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "h.")}
    from
      aws_route53_health_check as h
      left join used as u on u.health_check_id = h.id;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "route53"
    resource_type   = "route53_health_check"
  }
}

control "cloudtrail_trail_redundant" {
  title       = "Redundant CloudTrail trails should be removed"
  description = "The first copy of management events is free; additional trails recording the same events are billed."
  severity    = "medium"
  sql = <<-EOQ
    with home as (
      select
        arn,
        name,
        account_id,
        region,
        home_region,
        is_multi_region_trail,
        is_organization_trail,
        tags,
        _ctx
      from
        aws_cloudtrail_trail
      where
        region = home_region
    ),
    counts as (
      select
        account_id,
        count(*) filter (where is_multi_region_trail or is_organization_trail) as global_trails
      from
        home
      group by
        account_id
    )
    select
      h.arn as resource,
      case
        when (h.is_multi_region_trail or h.is_organization_trail) and c.global_trails > 1 then 'alarm'
        when not (h.is_multi_region_trail or h.is_organization_trail) and c.global_trails > 0 then 'alarm'
        else 'ok'
      end as status,
      case
        when (h.is_multi_region_trail or h.is_organization_trail) and c.global_trails > 1 then h.name || ' is one of ' || c.global_trails || ' multi-region/organization trails.'
        when not (h.is_multi_region_trail or h.is_organization_trail) and c.global_trails > 0 then h.name || ' is a regional trail duplicated by a multi-region trail.'
        else h.name || ' is not redundant.'
      end as reason
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "h.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "h.")}
    from
      home as h
      join counts as c on c.account_id = h.account_id;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "cloudtrail"
    resource_type   = "cloudtrail_trail"
  }
}

control "kms_key_disabled" {
  title       = "Disabled customer-managed KMS keys should be scheduled for deletion"
  description = "Disabled keys are still billed monthly (from aws-compliance kms_cmk_unused)."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when key_manager <> 'CUSTOMER' then 'skip'
        when key_state = 'Disabled' then 'alarm'
        else 'ok'
      end as status,
      title || ' is ' || key_state || '.' as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_kms_key;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "kms"
    resource_type   = "kms_key"
  }
}
