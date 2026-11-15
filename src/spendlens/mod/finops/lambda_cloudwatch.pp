// Lambda and CloudWatch Logs controls.
// Lambda: turbot/steampipe-mod-aws-thrifty (Apache-2.0), fixing the error-rate join (errors were joined to
// every day of invocations) and making the timeout threshold a parameter.
// CloudWatch: migrated from the internal steampipe-mod-aws-cost-analysis (cloudwatch.sp).

control "lambda_function_high_error_rate" {
  title       = "Lambda functions with a high error rate should be investigated"
  description = "Errors / invocations over the last 7 days above sizing.lambda_error_rate_max_pct. Failed invocations (and retries) are paid for."
  severity    = "medium"
  sql = <<-EOQ
    with rates as (
      select
        i.name,
        i.region,
        i.account_id,
        sum(i.sum) as invocations,
        sum(coalesce(e.sum, 0)) as errors
      from
        aws_lambda_function_metric_invocations_daily as i
        left join aws_lambda_function_metric_errors_daily as e
          on e.name = i.name and e.timestamp = i.timestamp and e.region = i.region and e.account_id = i.account_id
      where
        i.timestamp >= now() - interval '7 days'
      group by
        i.name, i.region, i.account_id
    )
    select
      f.arn as resource,
      case
        when r.invocations is null or r.invocations = 0 then 'skip'
        when r.errors / r.invocations * 100 > $1 then 'alarm'
        else 'ok'
      end as status,
      case
        when r.invocations is null or r.invocations = 0 then f.title || ' had no invocations in 7 days.'
        else f.title || ' error rate is ' || round((r.errors / r.invocations * 100)::numeric, 1) || '% over 7 days ('
          || r.errors || ' / ' || r.invocations || ').'
      end as reason
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "f.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "f.")}
    from
      aws_lambda_function as f
      left join rates as r on r.name = f.name and r.region = f.region and r.account_id = f.account_id;
  EOQ
  param "lambda_error_rate_max_pct" {
    default = var.lambda_error_rate_max_pct
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "lambda"
    resource_type   = "lambda_function"
  }
}

control "lambda_function_excessive_timeout" {
  title       = "Lambda timeouts far above the average duration should be reduced"
  description = "Timeout greater than sizing.lambda_timeout_max_multiple x the 7-day average duration; hung invocations bill until timeout."
  severity    = "low"
  sql = <<-EOQ
    with duration as (
      select
        name,
        region,
        account_id,
        avg(average)::numeric as avg_ms
      from
        aws_lambda_function_metric_duration_daily
      where
        timestamp >= now() - interval '7 days'
      group by
        name, region, account_id
    )
    select
      f.arn as resource,
      case
        when d.avg_ms is null then 'skip'
        when f.timeout::numeric * 1000 > d.avg_ms * $1 and f.timeout::numeric > 3 then 'alarm'
        else 'ok'
      end as status,
      case
        when d.avg_ms is null then f.title || ' has no duration metrics in 7 days.'
        else f.title || ' timeout is ' || f.timeout || ' s vs ' || round(d.avg_ms) || ' ms average duration.'
      end as reason,
      f.memory_size
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "f.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "f.")}
    from
      aws_lambda_function as f
      left join duration as d on d.name = f.name and d.region = f.region and d.account_id = f.account_id;
  EOQ
  param "lambda_timeout_max_multiple" {
    default = var.lambda_timeout_max_multiple
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "lambda"
    resource_type   = "lambda_function"
  }
}

control "lambda_function_without_graviton" {
  title       = "Lambda functions should consider arm64 (Graviton)"
  description = "arm64 Lambda is ~20% cheaper per GB-second."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when architectures ? 'arm64' then 'ok'
        else 'info'
      end as status,
      title || ' architectures: ' || coalesce(architectures::text, '["x86_64"]') || '.' as reason,
      memory_size
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_lambda_function;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "lambda"
    resource_type   = "lambda_function"
  }
}

control "cloudwatch_log_group_retention_policy" {
  title       = "CloudWatch log groups should not keep logs longer than the company maximum"
  description = "Retention unset (never expire) or above sizing.cloudwatch_log_group_retention_max_days."
  severity    = "high"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when retention_in_days is null or retention_in_days > $1 then 'alarm'
        else 'ok'
      end as status,
      case
        when retention_in_days is null then title || ' never expires logs (' || round(coalesce(stored_bytes, 0) / 1073741824.0, 2) || ' GB stored).'
        else title || ' retains logs for ' || retention_in_days || ' days (max ' || $1 || ').'
      end as reason,
      round(coalesce(stored_bytes, 0) / 1073741824.0, 3) as stored_gb
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_cloudwatch_log_group;
  EOQ
  param "cloudwatch_log_group_retention_max_days" {
    default = var.cloudwatch_log_group_retention_max_days
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "cloudwatch"
    resource_type   = "cloudwatch_log_group"
  }
}

control "cloudwatch_log_group_kms_policy" {
  title       = "CloudWatch log groups should be KMS-encrypted when required by policy"
  description = "Company standard (sizing.cloudwatch_log_group_kms_required)."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when not $1::bool then 'skip'
        when kms_key_id is null then 'alarm'
        else 'ok'
      end as status,
      case
        when kms_key_id is null then title || ' is not encrypted with a KMS key.'
        else title || ' is encrypted with a KMS key.'
      end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_cloudwatch_log_group;
  EOQ
  param "cloudwatch_log_group_kms_required" {
    default = var.cloudwatch_log_group_kms_required
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "cloudwatch"
    resource_type   = "cloudwatch_log_group"
  }
}
