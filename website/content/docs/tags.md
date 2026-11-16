# Tag compliance

`spendlens tags` checks resources against your tagging policy across 70 resource types. Without tags, spend can't be allocated to a team or cost centre.

## The five checks

| Check | Alarm when | Parameter |
|---|---|---|
| `mandatory` | A required tag key is missing | `tagging.mandatory` |
| `untagged` | A resource has no tags at all | n/a |
| `prohibited` | A forbidden key is present (e.g. `Password`) | `tagging.prohibited` |
| `limit` | A resource is over the tag limit | `tagging.tag_limit` |
| `expected_values` | A governed key has a value outside the allowed list (SQL `LIKE` patterns allowed) | `tagging.allowed_values` |

```yaml
tagging:
  mandatory: [Environment, Service Owner, CostCentre]
  allowed_values:
    Environment: [prod, staging, dev, "test%"]
  prohibited: [Password, Secret]
```

[[shot:tags|Tag compliance by check and resource type, with the exact missing tags per resource.]]

## Usage

```bash
spendlens tags                                       # all checks, alarms and errors only
spendlens tags --summary                             # compliance % per check and resource type
spendlens tags -k mandatory -r s3_bucket -r ec2_instance
spendlens tags --status all -f csv -o tags.csv
spendlens tags --fail-on-alarm                       # exit 3 on any alarm: a CI gate
spendlens tags types                                 # the 70 resource types covered
```

| Option | Meaning |
|---|---|
| `-k, --check` | `mandatory`, `untagged`, `prohibited`, `limit`, `expected_values` or `all` (repeatable) |
| `-r, --resource-type` | Limit to resource types, e.g. `s3_bucket` (repeatable) |
| `--status` | Statuses to show: `alarm`, `ok`, `info`, `skip`, `error` or `all` |
| `--summary` | Counts and compliance % instead of individual findings |
| `--fail-on-alarm` | Exit code 3 if any alarm |
| `--source` | `live` (default), `last` (replay your latest run) or `demo` |

## Adding a resource type

Add one line to `features/tags_catalog.py`, then run `python scripts/gen_tag_controls.py`. A test fails if the generated controls are out of date.
