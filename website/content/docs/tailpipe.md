# Tailpipe: logs, findings and billing exports

Some questions can't be answered from what AWS reports about resources today:
- "Who deleted this bucket four months ago?"
- "Has anyone read this bucket this quarter?"
- "How much NAT gateway traffic goes to S3?"
- "What did this exact volume cost last month?"

The answers sit in files AWS writes for you: CloudTrail logs, access logs, flow logs, findings and billing exports.

[Tailpipe](https://tailpipe.io) collects those files into a local database (DuckDB) on your machine, from S3 buckets or CloudWatch Logs groups, and spendlens queries it there. It comes with spendlens: the installer and the Docker image include Tailpipe and its AWS plugin, and `spendlens tailpipe` sets it up for you.

## What it's used for

| Data | Tailpipe table | What spendlens uses it for |
|---|---|---|
| CloudTrail logs | `aws_cloudtrail_log` | `spendlens changes` and the assistant's "what changed, who did it" answers beyond 90 days, with data events (S3 object reads and writes) and error codes |
| VPC flow logs | `aws_vpc_flow_log` | `spendlens graph traffic`: data transfer priced by path (cross-AZ, through NAT gateways, out to the internet) |
| Cost and Usage Report (CUR 2.0) | `aws_cost_and_usage_report` | `spendlens graph collect`: the billed cost of each resource, instead of an estimate from the price book |
| FOCUS 1.0 export | `aws_cost_and_usage_focus` | The same billed costs, when your account exports FOCUS instead of CUR 2.0 |
| Cost Optimization Hub recommendations export | `aws_cost_optimization_recommendation` | `spendlens savings history`: AWS's savings recommendations over time, and the ones left open longest |
| S3 server access logs | `aws_s3_server_access_log` | `spendlens unused buckets`: buckets nobody read or wrote; `security detections` (S3 pack) |
| ALB, NLB and Classic LB access logs | `aws_alb_access_log`, `aws_nlb_access_log`, `aws_clb_access_log` | `spendlens unused load-balancers`: load balancers with no requests |
| GuardDuty and Security Hub findings exported to S3 | `aws_guardduty_finding`, `aws_securityhub_finding` | Long finding history for the assistant (live findings come from [Security](../security/)) |
| WAF logs | `aws_waf_traffic_log` | The assistant's questions about blocked and allowed requests |

CloudTrail logs and VPC flow logs also feed `spendlens security detections`, which runs 98 MITRE-mapped detections on them (see [Security guardrails](../security/#log-detections)).

**Two ways to collect.** Every kind of data can be collected from its S3 bucket. CloudTrail, VPC flow logs and WAF logs can also come straight from their CloudWatch Logs group. That suits trails that send logs to CloudWatch Logs, and roles that can't read the S3 bucket. `spendlens tailpipe init` finds both, read-only.

The AI assistant gets a `tailpipe_query` tool over every one of these tables when there is data, and it is told which ones have data.

**Without Tailpipe, everything still works.** spendlens falls back to:
- **CloudTrail LookupEvents** for changes. It keeps only 90 days of management events and returns 50 events a page, so a broad search is slow.
- **Cost Explorer resource data or the price book** for costs.
- No flow-log traffic.

Tailpipe adds history, detail and speed.

## How it works

```
 AWS account (read-only)                       your machine
 ───────────────────────                       ────────────────────────────────────────────
 CloudTrail trail  ──┐                         spendlens tailpipe init
 VPC flow logs     ──┼── S3 buckets ──────►      finds the buckets (read-only) and writes
 CUR 2.0 export    ──┘        │                  ~/.tailpipe/config/spendlens.tpc
                              │
                              └─ s3:GetObject ─► spendlens tailpipe collect
                                                   runs `tailpipe collect` per table
                                                   files land in ~/.tailpipe/data (DuckDB)
                                                        │
                                                        ▼
                                                spendlens changes · graph collect · graph traffic
                                                spendlens ask (tailpipe_query, cloudtrail_events)
                                                   local SQL, no AWS calls
```

- **Discovery is read-only.**
  - CloudTrail trails and flow logs are read through Steampipe (`aws_cloudtrail_trail`, `aws_vpc_flow_log`).
  - CUR exports are read through the AWS CLI in us-east-1: `bcm-data-exports list-exports` and `get-export` for CUR 2.0, and `cur describe-report-definitions` for legacy CURs.
  - Locations you list in `finops.yaml` under `tailpipe:` are always used, even with discovery off.
- **spendlens owns one file.** `init` writes only `~/.tailpipe/config/spendlens.tpc`, rewritten each time. It holds a connection named `aws.spendlens` with your AWS profile, plus one partition per bucket found. Your other `.tpc` files are never touched. Bucket names and prefixes are checked before they are written.
- **Collecting reads S3 and changes nothing in AWS.** Each `collect` adds new files to the local data, starting from the date you give.
- **Queries are local.** Once collected, a question costs no AWS calls, has no rate limits and isn't capped at 90 days.
- **The assistant's access is guarded.** Each `tailpipe_query` must be a single SELECT on one of the three tables, with a time range, and is capped in rows. Table functions and file or environment functions (`read_csv`, `getenv` and the like) are refused.
- **Data stays with you.** Collected files live in `~/.tailpipe` on your machine, or in the Docker volume. Remove that folder to delete them.

## When spendlens uses it

spendlens reads Tailpipe on its own only when sources are configured: CloudTrail, flow logs or the CUR, through `spendlens tailpipe init` or your own `.tpc` files. Without them, everything works as usual from the Steampipe tables. For CloudTrail it also checks that the collected logs cover the time range you asked about, and otherwise reads from AWS through Steampipe (see [CloudTrail activity](../cloudtrail/#where-the-events-come-from)).

To turn it off completely, set this in `finops.yaml`:

```yaml
tailpipe:
  enabled: false     # never read Tailpipe automatically: Steampipe tables only
```

For one AI question, use `spendlens ask --no-tailpipe "..."`. For one CloudTrail search, use `spendlens cloudtrail events --source aws`.

## Before you start

- **Tailpipe and its AWS plugin are installed.** `spendlens doctor` shows a `tailpipe` line. If it says "not installed", re-run the spendlens installer.
- **A valid AWS login**, for example `aws sso login --profile <profile>`.
- **AWS CLI 2.15 or later**, to find CUR 2.0 exports: check with `aws --version`.
- **The data is going to S3.**
  - CloudTrail: a trail that delivers to S3. A trail only keeps history from when it was created.
  - Flow logs: flow logs with an S3 destination.
  - CUR: a CUR 2.0 export (Billing console → Data Exports) with resource IDs included.
- **Permissions.** See the [IAM permissions](../permissions/) page.
  - For `init`: `cloudtrail:DescribeTrails`, `ec2:DescribeFlowLogs`, `bcm-data-exports:ListExports` / `GetExport` and `cur:DescribeReportDefinitions`. Each is optional.
  - For `collect`: `s3:ListBucket` and `s3:GetObject` on the buckets, and `kms:Decrypt` if they are encrypted with KMS.

**Organisation trails.** Many companies send every account's CloudTrail to one bucket in a central log-archive account. Then `init` may find no trail in a member account, or `collect` fails with AccessDenied. Either get read access to that bucket, or list it yourself with a profile that can read it (see [Your own buckets](#your-own-buckets)).

## Set it up: commands in order

```bash
# 1. Log in and check the tools
aws sso login --profile <profile>
aws --version                              # 2.15 or later for CUR 2.0 exports (brew upgrade awscli)
spendlens doctor                           # "tailpipe: installed, no sources configured"

# 2. See what it finds, without writing anything
spendlens tailpipe init --dry-run

# 3. Write the Tailpipe config (~/.tailpipe/config/spendlens.tpc)
spendlens tailpipe init

# 4. Collect: start small, it downloads from S3
spendlens tailpipe collect --days 7

# 5. Check what is there
spendlens tailpipe status
spendlens doctor                           # "tailpipe: CloudTrail logs: N rows, ..."

# 6. Use it
spendlens changes --days 30                # now from the trail's logs: any age, data events
spendlens graph collect                    # "N resources priced from the CUR (Tailpipe)"
spendlens graph traffic                    # data transfer paths from flow logs, priced
spendlens ask "Who deleted objects in the reports bucket last month?"
```

**Keep it fresh.** Collecting isn't scheduled: questions see the data as of your last `collect`. Re-run it when you need newer data, or schedule it daily (cron, launchd or a CI job) while your AWS login is valid:

```bash
spendlens tailpipe collect                 # the last tailpipe.collect_days (30 by default)
spendlens tailpipe collect cloudtrail      # one kind only (cloudtrail, flow_logs, cur, focus, s3_access_logs ...)
spendlens tailpipe collect cloudtrail --days 90
```

Re-run `spendlens tailpipe init` only when buckets change: a new trail, a new export, a new flow log destination.

## Every command

| Command | What it does | AWS calls |
|---|---|---|
| `spendlens tailpipe init` | Finds CloudTrail, flow log and CUR locations and writes `~/.tailpipe/config/spendlens.tpc` | Read-only discovery |
| `spendlens tailpipe init --dry-run` | Shows what it found and the config it would write; writes nothing | Read-only discovery |
| `spendlens tailpipe init --no-discover` | Uses only the locations in `finops.yaml` | None |
| `spendlens tailpipe collect [KIND] [--days N]` | Runs `tailpipe collect` for each configured kind (all by default) from N days back | S3 reads |
| `spendlens tailpipe status` | Partitions configured, rows collected and the date range for each kind | None |
| `spendlens doctor` | Includes a `tailpipe` line: installed, configured, rows per kind, and the next command | None for Tailpipe |
| `spendlens cloudtrail events --source tailpipe` | CloudTrail events with every filter, from the trail's logs only (`auto`, the default, tries Tailpipe first; see [CloudTrail activity](../cloudtrail/)) | None |
| `spendlens changes --source tailpipe` | The short form for write events | None |
| `spendlens graph collect` | Prices resources from the CUR when it has been collected (`graph.cost_source: auto` or `cur`) | Inventory reads |
| `spendlens graph traffic` | Prices data transfer paths from flow logs | None for the flow logs |
| `spendlens ask "..."` | The assistant uses `tailpipe_query` and `cloudtrail_events` when there is data | Only for other tools |

`init` and `collect` also take `--config`, `--set` and (for `init`) `--profile`, like other commands. Tailpipe itself is still there for direct queries:

```bash
tailpipe query "select event_time, event_name, user_identity.arn from aws_cloudtrail_log
                where tp_date >= current_date - interval 7 day and event_name = 'DeleteBucket'"
```

## Your own buckets

When discovery can't see a bucket, list it in `finops.yaml`. This covers an organisation trail, an export in the payer account, or a role without the describe permissions:

```yaml
tailpipe:
  discover: true               # also look in the account; false = only the lists below
  profile: log-archive-read    # optional: a profile that can read these buckets (default aws.profile)
  cloudtrail: [{bucket: my-org-trail, prefix: AWSLogs/}]
  flow_logs:  [{bucket: my-flow-logs, prefix: vpc/}]
  cur:        [{bucket: my-billing, prefix: cur2}]
  focus:      [{bucket: my-billing, prefix: focus}]
  cost_recommendations: [{bucket: my-billing, prefix: recommendations}]
  s3_access_logs:  [{bucket: my-s3-logs, prefix: s3/}]
  alb_access_logs: [{bucket: my-lb-logs, prefix: alb/}]
  guardduty:  [{bucket: my-security-exports, prefix: guardduty/}]
  cloudtrail_log_groups: [{name: aws-cloudtrail-logs, region: eu-west-1}]   # CloudWatch Logs instead of S3
  flow_log_groups:       [{name: vpc-flow-logs, region: eu-west-1}]
  collect_days: 30             # how far back `spendlens tailpipe collect` goes (1 to 400)
```

What `init` finds on its own, read-only:
- **CloudTrail:** from each trail's S3 bucket and CloudWatch Logs group.
- **VPC flow logs:** from each flow log's S3 or CloudWatch Logs destination.
- **S3 access logs:** from each bucket's logging settings, as one partition per log bucket.
- **Load balancer access logs:** from each load balancer's attributes.
- **GuardDuty:** from its publishing destinations.
- **WAF logs:** from each web ACL's logging.
- **CUR 2.0, FOCUS and Cost Optimization Hub exports:** from Data Exports, plus legacy CUR reports.

Security Hub has no built-in S3 export, so list yours if you stream it to S3 (for example through Firehose).

Or set it for one run: `spendlens tailpipe init --no-discover --set 'tailpipe.cur=[{bucket: my-billing, prefix: cur2}]'`.

## In Docker

Mount a volume so collected data survives between runs:

```bash
docker run --rm -v ~/.aws:/home/finops/.aws:ro -v spendlens-tailpipe:/home/finops/.tailpipe \
  -e AWS_PROFILE=<profile> abhaypratap/spendlens tailpipe init
docker run --rm -v ~/.aws:/home/finops/.aws:ro -v spendlens-tailpipe:/home/finops/.tailpipe \
  -e AWS_PROFILE=<profile> abhaypratap/spendlens tailpipe collect --days 7
```

## When something goes wrong

| What you see | What to do |
|---|---|
| `init`: "the AWS SSO session has expired" | `aws sso login --profile <profile>`, then run it again |
| `init`: "this AWS CLI has no `bcm-data-exports` command" | Upgrade the AWS CLI to 2.15 or later. Legacy CURs are still found |
| `init`: "none found" for CloudTrail | The trail may be an organisation trail in another account: list its bucket under `tailpipe.cloudtrail` with a profile that can read it |
| `collect`: AccessDenied | The profile needs `s3:ListBucket` and `s3:GetObject` on the bucket (and `kms:Decrypt` for KMS-encrypted logs) |
| `status`: "not collected" | Run `spendlens tailpipe collect` for that kind |
| Collecting is slow or large | Use fewer days (`--days 7`) or one kind at a time; later runs only add new files |
| The assistant still uses LookupEvents | No CloudTrail rows in Tailpipe for that window: check `spendlens tailpipe status` |
