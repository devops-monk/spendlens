# spendlens: FinOps for AWS, from your terminal

**spendlens** finds untagged resources, oversized instances, idle waste and unexpected cost spikes in your AWS accounts, puts a dollar figure on each one, and answers your questions in plain English. **It is read-only by design**: it never creates, changes, tags or deletes anything.

> 🛡️ **100% read-only.** spendlens never creates, changes, tags or deletes anything in your AWS account: only Describe, List, Get and Lookup calls, a read-only role, and read-only SQL. Safe to point at production.

This image is ready to run. It includes spendlens with its AI assistant, plus pinned versions of [Steampipe](https://steampipe.io) 2.5.0, [Powerpipe](https://powerpipe.io) 1.6.0, the Steampipe AWS plugin 1.35.0, [Tailpipe](https://tailpipe.io) 0.7.6 and the Tailpipe AWS plugin 0.14.2. Steampipe's database is set up when the image is built, so a container starts without downloading anything.

📖 **Docs:** https://spendlens.devops-monk.com · **Architecture:** https://spendlens.devops-monk.com/architecture/ · **Source:** https://github.com/devops-monk/spendlens

---

## Quick start: try it without AWS

Every command can run on bundled sample data. This needs no credentials and no network:

```bash
docker run --rm --network none abhaypratap/spendlens tags --source demo --summary
docker run --rm --network none abhaypratap/spendlens waste --source demo --summary --by service
docker run --rm --network none -v "$PWD:/work" abhaypratap/spendlens report --source demo --out demo-report
# open demo-report/report.html
```

## Run against your AWS account

The container uses the standard AWS credential chain. **Credentials are never baked into the image**; pass them in at run time. Use a read-only role (the [IAM permissions](https://spendlens.devops-monk.com/docs/permissions/) page lists exactly what's needed).

```bash
# a profile / SSO session from your machine (mounted read-only)
docker run --rm \
  -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE=finops -e AWS_REGION=eu-west-1 \
  -v "$PWD:/work" -v spendlens-data:/data \
  abhaypratap/spendlens report --out reports

# or the role already assumed in your terminal
docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_REGION \
  -v "$PWD:/work" -v spendlens-data:/data abhaypratap/spendlens cost diff --mtd
```

To use it like a local command:

```bash
alias spendlens='docker run --rm -it -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  -v "$PWD:/work" -v spendlens-data:/data abhaypratap/spendlens'
spendlens tags --summary
```

### Dashboards and summaries that are never half-empty

`dashboard`, `notify` and `savings summary` combine many checks. By default they reuse every check already run **today** and run the rest live first, so the result covers everything and is at most a day old. With the alias above:

```bash
# Default: reuse today's checks, run the rest
spendlens dashboard -o report.html
spendlens notify --slack
spendlens savings summary

# Re-run everything now, e.g. after a deployment or clean-up
spendlens dashboard --refresh -o report.html
spendlens notify --slack --refresh
spendlens savings summary --refresh

# Saved runs only, no AWS calls (and no Steampipe start-up)
spendlens dashboard --source last -o report.html
spendlens notify --slack --source last
spendlens savings summary --source last
```

**Keep `-v spendlens-data:/data`.** Today's checks are reused from the run history in `/data`. Without that volume, each `docker run` starts with an empty history and re-checks everything. If a check can't run, for example because of a missing permission, the output says why and uses that check's last saved run.

---

## What you get

### Tag compliance: 70 resource types, 5 checks
Mandatory, untagged, prohibited, tag-limit and allowed-value checks, so every dollar can be allocated to a team.

![spendlens tags](https://spendlens.devops-monk.com/static/screens/cli-tags.png)

### Waste detection, priced
Unattached volumes, orphaned snapshots, idle load balancers and NAT gateways, unused IPs and secrets: things you pay for that do nothing.

![spendlens waste](https://spendlens.devops-monk.com/static/screens/cli-waste.png)

### Rightsizing & company standards: 41 controls
Allowed instance families, gp2 → gp3, previous generations, Graviton candidates, Multi-AZ only in prod, versions in paid Extended Support.

![spendlens rightsize](https://spendlens.devops-monk.com/static/screens/cli-rightsize.png)

### Cost diff: what changed, and where
Any two months or quarters, grouped by service, account, region or tag. New and removed services are always shown; big changes are flagged.

![spendlens cost diff](https://spendlens.devops-monk.com/static/screens/cli-cost-diff.png)

### Anomalies & month-end forecast
Daily spikes measured against a robust baseline, and the projected month-end spend checked against your budget and every AWS Budget.

![spendlens anomalies](https://spendlens.devops-monk.com/static/screens/cli-anomalies.png)

![spendlens forecast](https://spendlens.devops-monk.com/static/screens/cli-forecast.png)

### One savings number, no double counting
AWS Cost Optimization Hub, Savings Plan and RI coverage, and spendlens's own findings merged into one total. Each resource is counted once. `spendlens savings summary` reuses sources checked today and runs the rest; `--refresh` re-checks all four now.

![spendlens savings summary](https://spendlens.devops-monk.com/static/screens/cli-savings.png)

### Self-contained HTML dashboard
`spendlens dashboard` or `spendlens report` writes one HTML file with nine tabs. It works offline, as a CI artifact, and printed. `dashboard` always covers every tab: it reuses checks run today and runs the rest (`--refresh` re-runs all of them, `--source last` uses saved runs only).

![Dashboard overview](https://spendlens.devops-monk.com/static/screens/dashboard-overview.png)

![Dashboard cost diff and trend](https://spendlens.devops-monk.com/static/screens/dashboard-cost.png)

![Dashboard savings](https://spendlens.devops-monk.com/static/screens/dashboard-savings.png)

### One command. The whole review.
`spendlens review -o review.html` writes the full FinOps review on one page: the AI analyst's summary, AWS spend explained (a bridge from last month to this month, trends, a flow from each service to its workloads, owners), anomalies with their likely cause, and every saving by source, service, effort × value and kind of change. A review queue lets you approve, defer or reject each proposal with a note, then export your decisions as CSV. Nothing in AWS is changed. `spendlens report` also writes `review.html`. Add `--skip tags` (or any check) to leave a check out.

![FinOps review](https://spendlens.devops-monk.com/static/shots/review-light.webp)

### One page for managers
`spendlens brief -o brief.html` writes a printable executive brief: an analyst summary written by the FinOps AI profile, five headline numbers, key insights, monthly spend against the budget, anomalies, and a checklist of the largest savings to review. spendlens computes every figure; `--no-ai` uses a built-in rule-based summary and sends nothing to an AI provider. `spendlens report` also writes `brief.html`.

![Executive brief](https://spendlens.devops-monk.com/static/shots/brief-light.webp)

### Security guardrails
`spendlens security` gathers what AWS's own security services know into one list, worst first, read-only. It covers vulnerabilities in ECR images, Lambda functions and EC2 instances (Amazon Inspector, ECR scanning), threats (GuardDuty), standards findings (Security Hub) and public access (IAM Access Analyzer). It also runs 98 MITRE-mapped detections on the logs Tailpipe collected. `security gate` exits 1 when a guardrail in `finops.yaml` is broken, for CI before a deploy.

```bash
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  abhaypratap/spendlens security image payments-api:1.4.2 --severity high
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  abhaypratap/spendlens security gate --image payments-api:1.4.2 --fail-on high
```

Guide: [spendlens.devops-monk.com/docs/security](https://spendlens.devops-monk.com/docs/security/).

### CloudTrail: who did what, when
`spendlens cloudtrail events` searches CloudTrail with any filter: a time range or exact dates, user or role, API calls (with `*` wildcards), service, resource, resource type, access key, source IP or CIDR, error codes, reads or writes, region. Presets cover console logins, deletes, deployments, security groups, IAM, access denied, errors and the root user. `cloudtrail summary` breaks the same events down by who, API call, service, source IP, error and day. Both export to CSV, Excel, JSON, JSON Lines, Markdown, HTML or YAML. `spendlens changes` is the short form for write events, and the AI assistant answers the same questions in plain English.

```bash
docker run --rm -v ~/.aws:/home/finops/.aws:ro -v "$PWD:/work" -e AWS_PROFILE -e AWS_REGION \
  abhaypratap/spendlens cloudtrail events --from 2026-11-01 --to 2026-11-20 --service iam -o iam.xlsx
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  abhaypratap/spendlens cloudtrail events --preset console-logins --days 30
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  abhaypratap/spendlens ask "What changed in the account today, and who did it?"   # AI via Bedrock, same role
```

It reads the best source it can reach. CloudTrail logs collected by Tailpipe go furthest back and include data events. Next is the trail's CloudWatch Logs group, if it has one. LookupEvents needs no setup and covers the last 90 days of management events. A slow search stops after a minute and shows what it found. Guide: [spendlens.devops-monk.com/docs/cloudtrail](https://spendlens.devops-monk.com/docs/cloudtrail/).

### Tailpipe: CloudTrail logs, flow logs and the CUR, queried locally
Tailpipe and its AWS plugin are in the image. `spendlens tailpipe init` finds where your CloudTrail logs, VPC flow logs and Cost and Usage Reports land in S3 (read-only) and writes the Tailpipe config; `spendlens tailpipe collect` copies them for local queries. Keep a volume on `/home/finops/.tailpipe` so the collected data outlives the container:

```bash
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  -v spendlens-tailpipe:/home/finops/.tailpipe abhaypratap/spendlens tailpipe init
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  -v spendlens-tailpipe:/home/finops/.tailpipe abhaypratap/spendlens tailpipe collect
```

That gives `graph` billed per-resource costs, `graph traffic` NAT and cross-AZ costs, and the assistant CloudTrail history beyond 90 days.

### Files in, files out
The assistant reads files you give it (`-a`, or a path in the question): CSV, Excel, JSON, YAML, PDF, Word, logs, Terraform. Ask for a file and it writes it to `spendlens-output/` in the mounted folder: Excel, CSV, Word, Markdown, HTML, JSON, scripts or Terraform for review. It never overwrites a file and never runs what it writes.

```bash
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  -v "$PWD:/work" -v spendlens-data:/data abhaypratap/spendlens \
  ask -a budget.xlsx "Compare this budget with last month's cost per service, and export it to Excel"
# writes ./spendlens-output/<name>.xlsx
```

### The assistant's tools and cache, for you
The table search, table description and query checker the AI assistant uses also run as commands. They work offline from the catalog built into the image, so you can run them with `--network none`. They need no AWS credentials, and Steampipe never starts:

```bash
docker run --rm --network none abhaypratap/spendlens schema find "unattached EBS volumes"
docker run --rm --network none abhaypratap/spendlens schema show aws_cost_usage --no-examples
docker run --rm --network none abhaypratap/spendlens \
  schema check "select volume_id, size_gb from aws_ebs_volume"    # exit 1: did you mean size?
```

`schema check` exits with 1 when a query would fail: an unknown table or column, a missing required filter, or a JSON operator on a text column. It warns about Cost Explorer queries with no date range. Use it in CI to check your own Steampipe queries.

The assistant caches live results, answers and brief summaries in `/data/ai-cache.db`, next to the history. Each kind is reused only while it's still fresh: resources for 15 minutes, metrics for 5 and Cost Explorer for 6 hours. Mount the same volume to keep the cache, and to inspect or clear it:

```bash
docker run --rm -v spendlens-data:/data abhaypratap/spendlens cache show --entries
docker run --rm -v spendlens-data:/data abhaypratap/spendlens cache clear --queries   # or --answers, or everything
```

Add `--fresh` to `ask` or `brief` to skip the cache once.

### And more
- **`spendlens ask "why did EC2 go up?"`:** an AI assistant that answers from your data (Amazon Bedrock with your AWS role, Anthropic, OpenAI, Azure OpenAI or any OpenAI-compatible model). It answers from saved runs first and, when they can't answer, from any of the AWS plugin's 589 tables, live and read-only (`--no-live` turns that off): it looks up the real columns and required filters in a catalog built from the plugin's source, every query is checked before it reaches AWS, slow queries are waited for, and fresh results and repeated answers come from a cache (keep `-v spendlens-data:/data` to reuse it). Its SQL is read-only at three layers.
- **`spendlens notify --slack --teams`:** a summary of what changed, posted to your channel. Like the dashboard, it reuses today's checks and runs the rest (`--refresh` re-checks everything first).
- **`spendlens init --ci gitlab|github`:** a scheduled pipeline on a read-only OIDC role.
- **Table, CSV, JSON or HTML** output from every command, and every threshold set from one `finops.yaml`.

*Screenshots use the bundled demo data (`--source demo`).*

---

## Image reference

| | |
|---|---|
| **Tags** | `latest`, `0.4.11`, `0.4` (plus `sha-<commit>` per build) |
| **Platforms** | `linux/amd64` and `linux/arm64` (Graviton, Apple silicon), each built natively and smoke-tested |
| **License** | spendlens: Apache-2.0. The image also contains Steampipe, Powerpipe and Tailpipe (AGPL-3.0, unmodified) and their AWS plugins (Apache-2.0); see `/usr/share/doc/spendlens/NOTICE` |
| **User** | `finops` (uid 10001). Steampipe refuses to run as root |
| **Working directory** | `/work`: put `finops.yaml` here; reports are written here |
| **Volume** | `/data`: the run history (`/data/spendlens.db`), which keeps trends and "what changed" between runs, and the AI cache (`/data/ai-cache.db`) |
| **Entrypoint** | Starts the local Steampipe service only when a command needs live data, then stops it. `dashboard`, `brief`, `review`, `notify` and `savings summary` need it unless you pass `--source last` or `--source demo`; `schema` and `cache` never do. Exit codes pass through (`--fail-on-alarm` → 3, for CI gates) |

| Environment variable | Purpose |
|---|---|
| `AWS_PROFILE`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`, `AWS_ROLE_ARN` + `AWS_WEB_IDENTITY_TOKEN_FILE` | Standard AWS credentials (read-only role recommended) |
| `SPENDLENS_STEAMPIPE_SERVICE` | `auto` (default), `on` or `off` (use an external Steampipe) |
| `SPENDLENS__<SECTION>__<KEY>` | Override any setting, e.g. `SPENDLENS__SIZING__EC2_CPU_LOW=10` |
| `SPENDLENS_SLACK_WEBHOOK`, `SPENDLENS_TEAMS_WEBHOOK` | Webhooks for `spendlens notify` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY` | Only if `ask` uses those providers. Bedrock uses your AWS role |

If you bind-mount `/work` or `/data` on Linux, they must be writable by uid 10001.

## Use in CI

```yaml
# GitLab
spendlens:
  image: { name: abhaypratap/spendlens:0.4.11, entrypoint: [""] }
  script:
    - spendlens-entrypoint report --out reports
    # report has just run every check: post from those saved runs, no second round of AWS calls
    - spendlens-entrypoint notify --slack --source last
  artifacts: { paths: [reports/] }
```

The CI guide covers OIDC role assumption, keeping history between runs, and Slack and Teams summaries: https://spendlens.devops-monk.com/docs/ci/

## Prefer a local install?

```bash
curl -fsSL https://spendlens.devops-monk.com/install.sh | sh
```