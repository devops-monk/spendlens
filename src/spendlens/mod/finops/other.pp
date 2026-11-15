// Network, storage and misc waste controls. Derived from turbot/steampipe-mod-aws-thrifty (Apache-2.0).

control "vpc_eip_unattached" {
  title       = "Unattached Elastic IPs should be released"
  description = "Every public IPv4 address is billed hourly; unattached ones serve no purpose."
  severity    = "high"
  sql = <<-EOQ
    select
      'arn:' || partition || ':ec2:' || region || ':' || account_id || ':eip/' || allocation_id as resource,
      case
        when association_id is null then 'alarm'
        else 'ok'
      end as status,
      case
        when association_id is null then public_ip || ' is not associated.'
        else public_ip || ' is associated with ' || coalesce(private_ip_address, 'a resource') || '.'
      end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_vpc_eip;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "vpc"
    resource_type   = "vpc_eip"
  }
}

control "vpc_nat_gateway_unused" {
  title       = "NAT gateways without traffic should be deleted"
  description = "Fixes upstream: a gateway with no datapoints at all was reported as in use."
  severity    = "high"
  sql = <<-EOQ
    with traffic as (
      select
        nat_gateway_id,
        sum(coalesce(sum, 0)) as bytes_out
      from
        aws_vpc_nat_gateway_metric_bytes_out_to_destination
      group by
        nat_gateway_id
    )
    select
      n.arn as resource,
      case
        when n.state <> 'available' then 'skip'
        when coalesce(t.bytes_out, 0) = 0 then 'alarm'
        else 'ok'
      end as status,
      case
        when n.state <> 'available' then n.title || ' is ' || n.state || '.'
        when coalesce(t.bytes_out, 0) = 0 then n.title || ' sent no traffic in the metric window.'
        else n.title || ' is in use (' || round(t.bytes_out / 1048576.0, 1) || ' MB out).'
      end as reason
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "n.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "n.")}
    from
      aws_vpc_nat_gateway as n
      left join traffic as t on t.nat_gateway_id = n.nat_gateway_id;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "vpc"
    resource_type   = "vpc_nat_gateway"
  }
}

control "secretsmanager_secret_unused" {
  title       = "Secrets Manager secrets not used recently should be deleted"
  description = "Not accessed within sizing.secretsmanager_unused_max_days (or never). Each secret is billed monthly."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when last_accessed_date is null and created_date > now() - ($1::int || ' days')::interval then 'ok'
        when last_accessed_date is null then 'alarm'
        when last_accessed_date < now() - ($1::int || ' days')::interval then 'alarm'
        else 'ok'
      end as status,
      case
        when last_accessed_date is null then title || ' has never been accessed (created ' || to_char(created_date, 'YYYY-MM-DD') || ').'
        else title || ' was last accessed ' || to_char(last_accessed_date, 'YYYY-MM-DD') || '.'
      end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_secretsmanager_secret;
  EOQ
  param "secretsmanager_unused_max_days" {
    default = var.secretsmanager_unused_max_days
  }
  tags = {
    spendlens_check = "waste"
    service         = "secretsmanager"
    resource_type   = "secretsmanager_secret"
  }
}

control "ecr_repository_unused" {
  title       = "ECR repositories whose images are never pulled should be cleaned up"
  description = "No image pulled within sizing.ecr_image_pull_max_days; storage is billed per GB."
  severity    = "low"
  sql = <<-EOQ
    with pulls as (
      select
        repository_name,
        registry_id,
        region,
        max(last_recorded_pull_time) as last_pull,
        round(sum(coalesce(image_size_in_bytes, 0)) / 1073741824.0, 3) as stored_gb
      from
        aws_ecr_image
      group by
        repository_name, registry_id, region
    )
    select
      r.arn as resource,
      case
        when p.last_pull is null and r.created_at > now() - ($1::int || ' days')::interval then 'ok'
        when p.last_pull is null then 'alarm'
        when p.last_pull < now() - ($1::int || ' days')::interval then 'alarm'
        else 'ok'
      end as status,
      case
        when p.last_pull is null then r.title || ' has no recorded image pulls.'
        else r.title || ' last pull ' || to_char(p.last_pull, 'YYYY-MM-DD') || '.'
      end as reason,
      coalesce(p.stored_gb, 0) as stored_gb
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "r.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "r.")}
    from
      aws_ecr_repository as r
      left join pulls as p
        on p.repository_name = r.repository_name and p.registry_id = r.registry_id and p.region = r.region;
  EOQ
  param "ecr_image_pull_max_days" {
    default = var.ecr_image_pull_max_days
  }
  tags = {
    spendlens_check = "waste"
    service         = "ecr"
    resource_type   = "ecr_repository"
  }
}

control "s3_bucket_without_lifecycle" {
  title       = "S3 buckets should have lifecycle rules"
  description = "Without lifecycle rules, old objects/versions/incomplete uploads accumulate forever."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when lifecycle_rules is null or jsonb_array_length(lifecycle_rules) = 0 then 'alarm'
        else 'ok'
      end as status,
      case
        when lifecycle_rules is null or jsonb_array_length(lifecycle_rules) = 0 then name || ' has no lifecycle rules.'
        else name || ' has ' || jsonb_array_length(lifecycle_rules) || ' lifecycle rule(s).'
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

control "dynamodb_table_stale" {
  title       = "DynamoDB tables without recent changes should be reviewed"
  description = "Based on the latest stream label (only available when streams are enabled)."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when latest_stream_label is null then 'skip'
        when latest_stream_label::timestamptz < now() - ($1::int || ' days')::interval then 'alarm'
        else 'ok'
      end as status,
      case
        when latest_stream_label is null then name || ' has no stream; change activity unknown.'
        else name || ' last changed ' || to_char(latest_stream_label::timestamptz, 'YYYY-MM-DD') || '.'
      end as reason,
      billing_mode,
      round(coalesce(table_size_bytes, 0) / 1073741824.0, 3) as stored_gb
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_dynamodb_table;
  EOQ
  param "dynamodb_stale_max_days" {
    default = var.dynamodb_stale_max_days
  }
  tags = {
    spendlens_check = "waste"
    service         = "dynamodb"
    resource_type   = "dynamodb_table"
  }
}

control "elasticache_cluster_reservation_candidate" {
  title       = "Long-running ElastiCache clusters should be covered by reserved nodes"
  description = "Older than sizing.elasticache_running_age_max_days = alarm; _warning_days = info."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when date_part('day', now() - cache_cluster_create_time) > $1 then 'alarm'
        when date_part('day', now() - cache_cluster_create_time) > $2 then 'info'
        else 'ok'
      end as status,
      cache_cluster_id || ' (' || engine || ', ' || cache_node_type || ' x ' || num_cache_nodes || ') created '
        || date_part('day', now() - cache_cluster_create_time) || ' days ago.' as reason,
      cache_node_type
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_elasticache_cluster;
  EOQ
  param "elasticache_running_age_max_days" {
    default = var.elasticache_running_age_max_days
  }
  param "elasticache_running_age_warning_days" {
    default = var.elasticache_running_age_warning_days
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "elasticache"
    resource_type   = "elasticache_cluster"
  }
}
