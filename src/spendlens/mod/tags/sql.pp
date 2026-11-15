// Tag control SQL templates. __TABLE_NAME__ is replaced per resource type in the generated controls.
// Derived from turbot/steampipe-mod-aws-tags (Apache-2.0), with the expected_tag_values
// operator-precedence bug fixed (tagged resources were also emitted as a spurious 'skip' row).

locals {
  tags_mandatory_sql = <<-EOQ
    with analysis as (
      select
        arn,
        title,
        tags ?& $1 as has_mandatory_tags,
        to_jsonb($1) - array(select jsonb_object_keys(coalesce(tags, '{}'::jsonb))) as missing_tags,
        region,
        account_id,
        tags,
        _ctx
      from
        __TABLE_NAME__
    )
    select
      arn as resource,
      case
        when has_mandatory_tags then 'ok'
        else 'alarm'
      end as status,
      case
        when has_mandatory_tags then title || ' has all mandatory tags.'
        else title || ' is missing tags: ' || array_to_string(array(select jsonb_array_elements_text(missing_tags)), ', ') || '.'
      end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      analysis;
  EOQ

  tags_untagged_sql = <<-EOQ
    select
      arn as resource,
      case
        when tags = '{}' or tags is null then 'alarm'
        else 'ok'
      end as status,
      case
        when tags = '{}' or tags is null then title || ' has no tags.'
        else title || ' has tags.'
      end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      __TABLE_NAME__;
  EOQ

  tags_prohibited_sql = <<-EOQ
    with analysis as (
      select
        arn,
        array_agg(k) as prohibited_tags
      from
        __TABLE_NAME__,
        jsonb_object_keys(tags) as k,
        unnest($1::text[]) as prohibited_key
      where
        k = prohibited_key
      group by
        arn
    )
    select
      r.arn as resource,
      case
        when a.prohibited_tags is not null then 'alarm'
        else 'ok'
      end as status,
      case
        when a.prohibited_tags is not null then r.title || ' has prohibited tags: ' || array_to_string(a.prohibited_tags, ', ') || '.'
        else r.title || ' has no prohibited tags.'
      end as reason
      ${replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "r.")}
      ${replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "r.")}
    from
      __TABLE_NAME__ as r
      left join analysis as a on a.arn = r.arn;
  EOQ

  tags_limit_sql = <<-EOQ
    with analysis as (
      select
        arn,
        title,
        cardinality(array(select jsonb_object_keys(coalesce(tags, '{}'::jsonb)))) as num_tag_keys,
        region,
        account_id,
        tags,
        _ctx
      from
        __TABLE_NAME__
    )
    select
      arn as resource,
      case
        when num_tag_keys > $1::integer then 'alarm'
        else 'ok'
      end as status,
      title || ' has ' || num_tag_keys || ' tag(s).' as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      analysis;
  EOQ

  tags_expected_values_sql = <<-EOQ
    with raw_data as (
      select
        arn,
        title,
        tags,
        row_to_json(json_each($1)) as expected_tag_values,
        region,
        account_id,
        _ctx
      from
        __TABLE_NAME__
      where
        tags is not null
        and tags::text != '{}'
    ),
    exploded as (
      select
        arn,
        title,
        expected_tag_values ->> 'key' as tag_key,
        jsonb_array_elements_text((expected_tag_values ->> 'value')::jsonb) as expected_value,
        tags ->> (expected_tag_values ->> 'key') as current_value,
        region,
        account_id,
        tags,
        _ctx
      from
        raw_data
    ),
    status_by_tag as (
      select
        arn,
        title,
        tag_key,
        bool_or(current_value like expected_value) as has_expected_value,
        bool_and(current_value is null) as tag_absent,
        region,
        account_id,
        tags,
        _ctx
      from
        exploded
      group by
        arn, title, tag_key, region, account_id, tags, _ctx
    )
    select
      arn as resource,
      case
        when bool_and(tag_absent) then 'skip'
        when bool_and(has_expected_value or tag_absent) then 'ok'
        else 'alarm'
      end as status,
      case
        when bool_and(tag_absent) then title || ' has none of the governed tag keys.'
        when bool_and(has_expected_value or tag_absent) then title || ' has expected values for tags: ' || array_to_string(array_agg(tag_key) filter (where has_expected_value), ', ') || '.'
        else title || ' has unexpected values for tags: ' || array_to_string(array_agg(tag_key) filter (where not has_expected_value and not tag_absent), ', ') || '.'
      end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      status_by_tag
    group by
      arn, title, region, account_id, tags, _ctx
    union all
    select
      arn as resource,
      'skip' as status,
      title || case when $1::text = '{}' then ' (no expected tag values configured).' else ' has no tags.' end as reason
      ${local.tag_dimensions_sql}
      ${local.common_dimensions_sql}
    from
      __TABLE_NAME__
    where
      tags is null
      or tags::text = '{}'
      or $1::text = '{}';
  EOQ
}
