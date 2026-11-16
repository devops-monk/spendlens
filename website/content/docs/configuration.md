# Configuration

Every threshold, policy and setting is a typed parameter. Set them in one file and override them per environment or per command.

## Where values come from

Later layers win:

| Layer | Example |
|---|---|
| Built-in defaults | n/a |
| `finops.yaml` | `sizing: {ec2_cpu_low: 15}` |
| Environment variable | `SPENDLENS__SIZING__EC2_CPU_LOW=12` (double underscore separates levels) |
| Command line | `--set sizing.ec2_cpu_low=10` (repeatable) |

The policy file is found in this order:
1. `--config PATH`
2. `$SPENDLENS_CONFIG`
3. `./finops.yaml`
4. `~/.config/spendlens/finops.yaml`

Values passed with `--set` and environment variables are parsed as YAML, so lists and maps work:

```bash
spendlens tags --set 'tagging.mandatory=[Environment, Owner, CostCentre]'
spendlens rightsize --set 'sizing.rds_multi_az_by_env={prod: true, "*": false}'
```

Unknown keys, typos and out-of-range values are rejected, and the error says which layer they came from.

## Inspect the effective settings

```bash
spendlens params --filter ec2       # every parameter: value, where it came from, env-var name
spendlens config                    # the merged config plus the Powerpipe variables it produces
```

## Sections

| Section | Controls | Used by |
|---|---|---|
| `tagging` | `mandatory`, `allowed_values`, `prohibited`, `tag_limit`, `environment_tag` | tags, environment-aware standards |
| `sizing` | instance-type allow-lists, CPU and age thresholds, `*_by_env` policies | rightsize, waste |
| `pricing` | unit prices for $ estimates (us-east-1 list prices by default) | rightsize, waste, savings |
| `cost` | metric, credits/refunds handling, change thresholds, showback tag keys | cost, anomalies |
| `anomaly` | lookback, threshold, minimum increase | anomalies |
| `budgets` | `monthly_total`, `per_tag`, `warn_pct` | forecast, showback |
| `savings` | commitment coverage target, Savings Plan expiry warning | savings |
| `llm` | provider, model, region, effort, step limit | ask |
| `notifications` | names of the webhook environment variables | notify |
| `dashboard` | title, organisation, brand colours, font | dashboard, `--format html` |
| `steampipe`, `powerpipe` | connection, port, parallelism, timeouts | all live commands |
| `history` | path, retention | all |
| `aws` | profile, accounts, regions | all live commands |
| `plugins` | free-form settings per plugin | plugins |

## Environment-aware standards

Some standards differ by environment. `*_by_env` maps are matched (case-insensitive) against the value of the `tagging.environment_tag` tag, and `"*"` covers everything else:

```yaml
sizing:
  rds_multi_az_by_env: {prod: true, "*": false}          # Multi-AZ only in prod
  rds_backup_retention_max_days_by_env: {prod: 14, "*": 1}
  ecs_desired_tasks_by_env: {prod: 3, "*": 1}             # prod tasks spread over 3 AZs
```

## Raw Powerpipe variables

To pass a variable the config doesn't model (for example from a plugin's mod), use `--var name=value` on control-based commands.
