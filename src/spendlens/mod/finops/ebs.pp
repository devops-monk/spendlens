// EBS rightsizing and waste controls. Derived from turbot/steampipe-mod-aws-thrifty (Apache-2.0).

control "ebs_volume_gp2" {
  title       = "EBS gp2 volumes should be migrated to gp3"
  description = "gp3 is ~20% cheaper per GB than gp2 with a better performance baseline."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when volume_type = 'gp2' then 'alarm'
        when volume_type = 'gp3' then 'ok'
        else 'skip'
      end as status,
      volume_id || ' is ' || volume_type || ' (' || size || ' GB).' as reason,
      volume_type,
      size as size_gb
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ebs_volume;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "ebs"
    resource_type   = "ebs_volume"
  }
}

control "ebs_volume_io1" {
  title       = "EBS io1 volumes should be migrated to io2"
  description = "io2 offers higher durability and IOPS per GB at the same price."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when volume_type = 'io1' then 'alarm'
        when volume_type = 'io2' then 'ok'
        else 'skip'
      end as status,
      volume_id || ' is ' || volume_type || '.' as reason,
      volume_type,
      size as size_gb
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ebs_volume;
  EOQ
  tags = {
    spendlens_check = "rightsize"
    service         = "ebs"
    resource_type   = "ebs_volume"
  }
}

control "ebs_volume_oversized" {
  title       = "EBS volumes larger than the company maximum should be reviewed"
  description = "Volumes larger than sizing.ebs_volume_max_size_gb."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when size > $1 then 'alarm'
        else 'ok'
      end as status,
      volume_id || ' is ' || size || ' GB (max ' || $1 || ' GB).' as reason,
      volume_type,
      size as size_gb
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ebs_volume;
  EOQ
  param "ebs_volume_max_size_gb" {
    default = var.ebs_volume_max_size_gb
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ebs"
    resource_type   = "ebs_volume"
  }
}

control "ebs_volume_high_iops" {
  title       = "Provisioned-IOPS EBS volumes above the company maximum should be reviewed"
  description = "io1/io2 volumes with more than sizing.ebs_volume_max_iops provisioned IOPS."
  severity    = "low"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when volume_type not in ('io1', 'io2') then 'skip'
        when iops > $1 then 'alarm'
        else 'ok'
      end as status,
      volume_id || ' (' || volume_type || ') has ' || coalesce(iops, 0) || ' provisioned IOPS.' as reason,
      volume_type,
      size as size_gb,
      iops
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ebs_volume;
  EOQ
  param "ebs_volume_max_iops" {
    default = var.ebs_volume_max_iops
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ebs"
    resource_type   = "ebs_volume"
  }
}

control "ebs_volume_low_usage" {
  title       = "EBS volumes with very low I/O should be reviewed"
  description = "Average daily peak read+write ops over 30 days below sizing.ebs_volume_ops_low (alarm) / _high (info)."
  severity    = "low"
  sql = <<-EOQ
    with ops as (
      select volume_id, maximum, timestamp from aws_ebs_volume_metric_read_ops_daily
      where timestamp >= now() - interval '30 days'
      union all
      select volume_id, maximum, timestamp from aws_ebs_volume_metric_write_ops_daily
      where timestamp >= now() - interval '30 days'
    ),
    usage as (
      select
        volume_id,
        round(avg(maximum)::numeric) as avg_max,
        count(distinct timestamp) as days
      from
        ops
      group by
        volume_id
    )
    select
      v.arn as resource,
      case
        when u.avg_max is null then 'info'
        when u.avg_max <= $1 then 'alarm'
        when u.avg_max <= $2 then 'info'
        else 'ok'
      end as status,
      case
        when u.avg_max is null then 'CloudWatch metrics not available for ' || v.volume_id || '.'
        else v.volume_id || ' averages ' || u.avg_max || ' peak ops/day over ' || u.days || ' days.'
      end as reason,
      v.volume_type,
      v.size as size_gb
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "v.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "v.")}
    from
      aws_ebs_volume as v
      left join usage as u on u.volume_id = v.volume_id;
  EOQ
  param "ebs_volume_ops_low" {
    default = var.ebs_volume_ops_low
  }
  param "ebs_volume_ops_high" {
    default = var.ebs_volume_ops_high
  }
  tags = {
    spendlens_check = "rightsize"
    service         = "ebs"
    resource_type   = "ebs_volume"
  }
}

control "ebs_volume_unattached" {
  title       = "Unattached EBS volumes should be deleted or snapshotted"
  description = "Volumes in the 'available' state are billed but not used by any instance."
  severity    = "high"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when jsonb_array_length(coalesce(attachments, '[]'::jsonb)) > 0 then 'ok'
        else 'alarm'
      end as status,
      case
        when jsonb_array_length(coalesce(attachments, '[]'::jsonb)) > 0 then volume_id || ' is attached.'
        else volume_id || ' (' || size || ' GB ' || volume_type || ') is not attached to any instance.'
      end as reason,
      volume_type,
      size as size_gb,
      iops
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ebs_volume;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "ebs"
    resource_type   = "ebs_volume"
  }
}

control "ebs_volume_on_stopped_instance" {
  title       = "EBS volumes attached only to stopped instances should be reviewed"
  description = "Volumes keep billing while their instances are stopped."
  severity    = "medium"
  sql = <<-EOQ
    with attached as (
      select
        v.volume_id,
        count(i.instance_id) as instances,
        count(i.instance_id) filter (where i.instance_state <> 'stopped') as not_stopped
      from
        aws_ebs_volume as v
        cross join jsonb_array_elements(coalesce(v.attachments, '[]'::jsonb)) as va
        left join aws_ec2_instance as i on i.instance_id = va ->> 'InstanceId'
      group by
        v.volume_id
    )
    select
      v.arn as resource,
      case
        when a.instances is null or a.instances = 0 then 'skip'
        when a.not_stopped = 0 then 'alarm'
        else 'ok'
      end as status,
      case
        when a.instances is null or a.instances = 0 then v.volume_id || ' is not attached.'
        when a.not_stopped = 0 then v.volume_id || ' is attached only to stopped instance(s).'
        else v.volume_id || ' is attached to ' || a.not_stopped || ' running instance(s).'
      end as reason,
      v.volume_type,
      v.size as size_gb,
      v.iops
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "v.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "v.")}
    from
      aws_ebs_volume as v
      left join attached as a on a.volume_id = v.volume_id;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "ebs"
    resource_type   = "ebs_volume"
  }
}

control "ebs_snapshot_old" {
  title       = "EBS snapshots older than the retention maximum should be deleted"
  description = "Snapshots older than sizing.ebs_snapshot_age_max_days that are not managed by AWS Backup/DLM."
  severity    = "medium"
  sql = <<-EOQ
    select
      arn as resource,
      case
        when tags ? 'aws:backup:source-resource' or tags ? 'aws:dlm:lifecycle-policy-id' then 'skip'
        when start_time < now() - ($1::int || ' days')::interval then 'alarm'
        else 'ok'
      end as status,
      snapshot_id || ' (' || volume_size || ' GB) was created ' || to_char(start_time, 'YYYY-MM-DD') || '.' as reason,
      volume_size as size_gb
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      aws_ebs_snapshot;
  EOQ
  param "ebs_snapshot_age_max_days" {
    default = var.ebs_snapshot_age_max_days
  }
  tags = {
    spendlens_check = "waste"
    service         = "ebs"
    resource_type   = "ebs_snapshot"
  }
}

control "ebs_snapshot_orphaned" {
  title       = "EBS snapshots whose source volume is gone and are not used by an AMI should be reviewed"
  description = "Orphaned snapshots are a common source of forgotten storage spend."
  severity    = "medium"
  sql = <<-EOQ
    with ami_snapshots as (
      select distinct
        bdm -> 'Ebs' ->> 'SnapshotId' as snapshot_id
      from
        aws_ec2_ami,
        jsonb_array_elements(block_device_mappings) as bdm
      where
        bdm -> 'Ebs' ->> 'SnapshotId' is not null
    )
    select
      s.arn as resource,
      case
        when a.snapshot_id is not null then 'ok'
        when v.volume_id is not null then 'ok'
        when s.tags ? 'aws:backup:source-resource' or s.tags ? 'aws:dlm:lifecycle-policy-id' then 'skip'
        else 'alarm'
      end as status,
      case
        when a.snapshot_id is not null then s.snapshot_id || ' is used by an AMI.'
        when v.volume_id is not null then s.snapshot_id || ' source volume ' || s.volume_id || ' still exists.'
        when s.tags ? 'aws:backup:source-resource' or s.tags ? 'aws:dlm:lifecycle-policy-id' then s.snapshot_id || ' is managed by a backup policy.'
        else s.snapshot_id || ' (' || s.volume_size || ' GB) is orphaned: source volume deleted, not used by any AMI.'
      end as reason,
      s.volume_size as size_gb
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "s.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "s.")}
    from
      aws_ebs_snapshot as s
      left join ami_snapshots as a on a.snapshot_id = s.snapshot_id
      left join aws_ebs_volume as v on v.volume_id = s.volume_id;
  EOQ
  tags = {
    spendlens_check = "waste"
    service         = "ebs"
    resource_type   = "ebs_snapshot"
  }
}
