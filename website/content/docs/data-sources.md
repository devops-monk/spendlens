# Data sources

spendlens reads AWS, read-only, through a few sources. Each kind of data has an order of preference, and spendlens picks the best source that is set up and can answer the question. `spendlens sources` shows what it would use right now, and why:

```bash
spendlens sources            # local: Tailpipe, the history, finops.yaml
spendlens sources --aws      # also look up the trail's CloudWatch Logs group (read-only)
spendlens sources --json     # for scripts
```

## The sources

| Source | What it is | Used for |
|---|---|---|
| **Steampipe AWS plugin** | About 590 AWS tables you query with SQL; each query calls the AWS APIs, read-only | Every check, inventory and configuration, live questions to the assistant |
| **Cost Explorer** | AWS's billing API, through Steampipe (about $0.01 per request) | `cost`, `anomalies`, `forecast`; `cost resources` saves per-resource costs |
| **CloudTrail LookupEvents** | The last 90 days of management events, through Steampipe | CloudTrail events when nothing better is set up |
| **The trail's CloudWatch Logs group** | Your trail's events in CloudWatch Logs, through Steampipe | CloudTrail events beyond 90 days, with data events, when the trail sends there |
| **Tailpipe** | CloudTrail logs, VPC flow logs and the Cost and Usage Report, collected from S3 into a local database | Long history, data events, traffic costs, billed cost per resource (see [Tailpipe](../tailpipe/)) |
| **History** | Every saved run, in a local SQLite file | Trends, `history diff`, the dashboard, and the assistant's first source |
| **Price book** | Built-in AWS prices | Cost estimates when nothing billed is available |

## Which one is used

| Data | Sources, best first | Notes |
|---|---|---|
| **CloudTrail events** | Tailpipe → the trail's CloudWatch Logs → LookupEvents | Tailpipe only when its collected logs cover the time window you asked about; otherwise straight from AWS. `--source` on `spendlens cloudtrail` picks one |
| **Resource costs** (graph, cleanup savings) | The CUR (or a FOCUS export) through Tailpipe → Cost Explorer resource data → price book | `graph.cost_source`: `auto`, `cur` or `estimate` |
| **VPC flow logs** | Tailpipe | No Steampipe equivalent: `graph traffic` needs Tailpipe |
| **Security findings** | Inspector, ECR scanning, Security Hub, GuardDuty, Access Analyzer (Steampipe, live) → log detections (Tailpipe, local) | A service that is off is reported as unavailable, never as "no findings" ([Security guardrails](../security/)) |
| **Who uses a bucket or load balancer** | Tailpipe (S3 and load balancer access logs) | `unused buckets` / `unused load-balancers`; without logs a resource is "unknown", never "unused" |
| **Savings recommendations over time** | Tailpipe (Cost Optimization Hub export) | `savings history`; today's recommendations come live from `savings hub` |
| **Everything else** | Steampipe | Regions and accounts come from the Steampipe connection (see [Multi-account](../multi-account/)) |

**Tailpipe is used automatically only when it can help.** It must be enabled (`tailpipe.enabled`, on by default), installed, and have sources configured, either by `spendlens tailpipe init` or by your own `.tpc` files. If it isn't, spendlens uses the Steampipe tables as usual. To turn Tailpipe off everywhere:

```yaml
tailpipe:
  enabled: false
```

For one question, run `spendlens ask --no-tailpipe "..."`. For one CloudTrail search, use `spendlens cloudtrail events --source aws`.

## Freshness

- **Steampipe** queries AWS when you run them. The assistant caches live results for a while: inventory 15 minutes, metrics and logs 5 minutes, Cost Explorer 6 hours. Its answers say "as of" a time, and `--fresh` queries again. See [AI assistant](../ai-assistant/).
- **Tailpipe** holds what you last collected. Run `spendlens tailpipe collect` to bring it up to date. CloudTrail searches check coverage and use AWS when the collected logs are stale.
- **History** holds what each run saved, with its time. Reports and the assistant name the run they used.
