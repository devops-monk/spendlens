<p align="center">
  <img src="website/static/brand/icon.svg" width="96" height="96" alt="spendlens icon">
</p>

<h1 align="center">spendlens</h1>

<p align="center"><b>See every dollar. Act on every insight.</b></p>

<p align="center">
  Read-only FinOps for AWS, from your terminal. spendlens finds untagged resources, oversized instances, idle waste
  and unexpected cost spikes, puts a price on each one, and answers your questions in plain English.
</p>

<p align="center">
  <a href="https://github.com/devops-monk/spendlens/actions/workflows/ci.yml"><img src="https://github.com/devops-monk/spendlens/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://hub.docker.com/r/abhaypratap/spendlens"><img src="https://img.shields.io/docker/v/abhaypratap/spendlens?sort=semver&label=docker" alt="Docker image"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green" alt="License: Apache-2.0"></a>
</p>

<p align="center">
  <a href="https://spendlens.devops-monk.com"><b>Website</b></a> ·
  <a href="https://spendlens.devops-monk.com/docs/">Docs</a> ·
  <a href="https://spendlens.devops-monk.com/cheatsheet/">Cheat sheet</a> ·
  <a href="https://spendlens.devops-monk.com/architecture/">Architecture</a> ·
  <a href="https://hub.docker.com/r/abhaypratap/spendlens">Docker Hub</a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="website/static/shots/hero-dark.webp">
    <img src="website/static/shots/hero-light.webp" width="100%" alt="The spendlens dashboard: spend this month, flagged cost changes, projected month end, potential saving, tag compliance and anomalies">
  </picture>
</p>

<p align="center"><sub>One self-contained HTML report, shown with the bundled demo data.</sub></p>

> [!NOTE]
> v0.4.4. spendlens is tested end to end through Steampipe against an AWS emulator. Verification against a live
> account with a read-only role is next (see [PLAN.md](PLAN.md)).

## New in 0.4.4

- **Small fixes from a full offline smoke test of every command.**
  - `spendlens schema find` takes plain words without quotes (`spendlens schema find who deleted the bucket`).
  - `spendlens history` says what to do when nothing has been saved yet.

## New in 0.4.3

- **Resource age with cost.** `spendlens graph age` shows how old resources are and what they cost: by age band, by type, and the costliest old ones, each with its owner and cleanup group. `--min-days 365` lists what is over a year old; `spendlens open age` gives the page.
- **CloudTrail beyond 90 days.** If [Tailpipe](https://tailpipe.io) has collected your trail's logs, the AI assistant gets a `cloudtrail_logs` tool covering the full history and S3 data events. It runs locally and queries only `aws_cloudtrail_log`, always with a time range.
- **Sharper data transfer.** `graph traffic` uses the custom flow-log fields when they are present: the service AWS names, and the original packet addresses. It also checks which route tables each gateway endpoint is on, so it can propose "associate the S3 endpoint with route table rtb-…" where an endpoint exists but a subnet doesn't use it.

## New in 0.4.2

- **Who created it (opt-in).** With `graph.owner_from_cloudtrail: true`, `spendlens graph collect` asks CloudTrail who created each resource. It runs bounded LookupEvents queries over the last 90 days at most. `spendlens graph owners` then suggests the creator for your `Owner` tag, with medium confidence, when nothing attached already names an owner. It is off by default: it is slower in busy accounts and needs `cloudtrail:LookupEvents`. [More](https://spendlens.devops-monk.com/docs/graph/#suggested-owners)

## New in 0.4.1

- **Ask CloudTrail who did what.** `spendlens ask --live "Who deleted the old-reports bucket?"` now finds the CloudTrail event tables for "who / deleted / changed / API calls / console logins" questions. It uses LookupEvents (the last 90 days, no setup) with a time range AWS receives, and falls back to the trail's CloudWatch Logs for older or more detailed events. A CloudTrail query without such a range is refused before it reaches AWS, with the exact fix. [More](https://spendlens.devops-monk.com/docs/ai-assistant/#cloudtrail-who-did-what-when)
- **Better table search for plurals.** Questions like "how many running instances" now find the right table first.

## New in 0.4.0

- **The resource graph: follow the money, not one resource.** `spendlens graph collect` reads how your resources connect (instances, volumes, snapshots, AMIs, IPs, NAT gateways, load balancers, RDS, ECS, Lambda, route tables), read-only, with a cost on each one. The relationships follow the Steampipe AWS Insights mod. [More](https://spendlens.devops-monk.com/docs/graph/)
- **Cleanup groups.** `spendlens graph orphans` finds connected resources that can be removed together, each with one total saving: a stopped instance with its volumes, Elastic IP and snapshots; snapshots of deleted volumes; unused AMIs; load balancers with no targets; snapshots of deleted databases. Each resource is in one group at most, and waste alarms are linked, not added. The review shows each group as a small graph and as one queue item.
- **Suggested owners.** `spendlens graph owners` suggests tags for untagged resources from what they're attached to (a volume from the instance that mounts it), with a confidence and a reason. `--script` writes the tagging commands for you to review and run. Suggestions also show in `tags` and in the review.
- **Explore and ask.** `graph show` draws any resource with its links and cost (HTML, SVG, Mermaid, DOT, JSON), and `graph impact` shows what deleting it would affect. The AI assistant gets `resource_context`, `cleanup_groups`, `suggested_owners` and `traffic_paths`, and saved answers draw the resources they talk about.
- **Billed costs.** With [Tailpipe](https://tailpipe.io) and your Cost and Usage Report, each resource's cost is what it was billed. Without them, spendlens uses Cost Explorer resource data, then the price book. NAT gateway and load balancer costs are split across what uses them.
- **Data transfer.** `spendlens graph traffic` prices VPC flow logs (via Tailpipe): cross-AZ, through NAT gateways, out to the internet. It proposes free S3/DynamoDB gateway endpoints where that traffic goes through a NAT gateway today.
- **`spendlens find`** shows everything saved about one resource. **`spendlens open`** builds a report from saved runs and opens it in your browser.

## New in 0.3.4

- **Sessions: follow-up questions that remember.** `spendlens ask --new "…"` starts a session; `spendlens ask -C "…"` continues it in a later command, even another day; `-S NAME` keeps topics apart. `spendlens session list|show|delete|clear` manages them. Works with every provider.
- **Save an answer as a page.** `spendlens ask "…" -o answer.html` writes a styled page with the tool calls behind it (or `.md`); `spendlens session show NAME -o session.html` saves a whole conversation. The assistant answers in Markdown and never writes HTML, so long answers no longer hit the token limit.
- **Answers stay on the question.** No unrequested savings queries; configuration questions (e.g. Aurora ACU min/max) use live AWS with `--live`, or say that `--live` is needed.
- **ECS findings with structure.** ECS task sizing findings now carry the service, task family, launch type, vCPU, memory and the company maximum as fields.
- **Architecture page** explains how a command runs, what each flag changes, how the assistant knows the AWS tables, and sessions.

## New in 0.3.3

- **"Why did RDS cost change?" in a few steps.** A new `history_cost` tool gives the assistant a service's saved cost in every cost run (diff, trend, the breakdown inside it) plus its findings in one call; short names like `rds`, `ec2`, `nat` work. The first overview also lists each cost run's periods and exact service names.
- **Always an answer.** At the tool-step limit the assistant answers from what it found and says it may be incomplete, instead of giving up.
- **`--show-tools` is live.** Each tool call prints as it runs: the tool, its SQL, the row count, the time and the first rows, with a spinner while the model thinks.

## New in 0.3.2

- **Leave a check out of the review.** `spendlens review --skip tags -o review.html` doesn't run the tagging check, doesn't send it to the AI and doesn't show it. Works for any check and repeats (`--skip tags --skip savings_hub`); `spendlens report --skip …` passes it on to `review.html`.

## New in 0.3.1

- **One command. The whole review.** `spendlens review -o review.html` writes the full FinOps review on one page: the AI analyst's summary, AWS spend explained (a bridge from last month to this month, trends, a flow from each service to the workloads behind it, owners), anomalies with their usual vs actual spend and likely cause, and every saving by source, service, effort × value and kind of change. A review queue lets you approve, defer or reject each proposal with a note and export the decisions as CSV. spendlens computes every figure; `--no-ai` uses the rule-based summary. [More](#one-command-the-whole-review)
- **`spendlens report` also writes `review.html`**, sharing the brief's AI analysis (same data, asked once).
- Savings Plan and reservation purchases now count as high effort in `brief` too.

## New in 0.3.0

- **One page for managers.** `spendlens brief -o brief.html` writes an executive report: an analyst summary by the FinOps AI profile, five headline numbers, key insights, monthly spend against the budget, what changed, anomalies with their likely cause, and a checklist of the largest savings to review. spendlens computes every figure; `--no-ai` uses a built-in rule-based summary. [More](#one-page-for-managers)
- **Ask about any AWS table, right first time.** With `--live`, the assistant looks up the real tables, columns and required filters in a catalog built from the Steampipe AWS plugin's source (589 tables), and every query is checked before it reaches AWS. A wrong column comes back with "did you mean".
- **Patient and cached.** Slow live queries run as jobs the assistant waits for. Fresh results are reused (15 min for resources, 5 for metrics, 6 h for Cost Explorer), and the same question on the same data costs no tokens. `--fresh` skips the cache.
- **The assistant's tools, for you.** `spendlens schema find | show | check` searches the catalog and checks a query offline; `spendlens cache show | clear` inspects the cache.
- **`spendlens report` also writes `brief.html`** (rule-based; add `--ai` for the AI summary).
- **The [cheat sheet](https://spendlens.devops-monk.com/cheatsheet/)** now has 148 commands, with a section for schema and cache.

**Heads-up:** the assistant's live tools are renamed (`steampipe_query` is now `aws_query`, plus `aws_find_tables`, `aws_describe_table`, `aws_check_query`, `aws_query_result`), and a live query may now run for up to 5 minutes (`llm.query_timeout_s`, was 60 s). Full notes: [release v0.3.0](https://github.com/devops-monk/spendlens/releases/tag/v0.3.0).

## New in 0.2.0

- **Always fresh, never half-empty.** `dashboard`, `notify` and `savings summary` reuse every check already run today and run the rest first, so they always cover everything. Use `--refresh` to re-run all, or `--source last` for no AWS calls. [More](#always-fresh-never-half-empty)
- **Cost drill-down.** `spendlens cost diff -g service -b tag:Name` shows which resource or app is behind each service's change. `-b` also takes `usage-type`, `region` or any tag; add `-f html` for a searchable report.
- **Reports that explain themselves.** Every dashboard tab and HTML report has a "How to read this" box with your actual thresholds and the setting that changes each one. "Flagged" always states its rule (e.g. at least $10 AND at least 20%).
- **Charted reports for every cost command.** `-f html` on `cost diff`, `trend`, `showback`, `anomalies` and `forecast` gives bar, line and budget charts with the full table.
- **Findings at scale.** `-f html` on `tags`, `rightsize` and `waste` gives an interactive report with filters and search, each check's thresholds, and why each resource alarmed. It handles tens of thousands of results.
- **Live progress, and eu-west-1 by default.** Long runs show the current step and an elapsed timer; set `aws.regions` to query other regions.
- **A [cheat sheet](https://spendlens.devops-monk.com/cheatsheet/)** of 125 ready-to-copy commands, grouped by the question they answer.

**Heads-up:** `dashboard`, `notify` and `savings summary` now call AWS by default when a check hasn't run today. Add `--source last` to keep them offline. Full notes: [release v0.2.0](https://github.com/devops-monk/spendlens/releases/tag/v0.2.0).

---

## Install

**macOS or Linux**: one command, no sudo. Installs pinned Steampipe and Powerpipe, the AWS plugin, and spendlens in
its own isolated environment.

```bash
curl -fsSL https://spendlens.devops-monk.com/install.sh | sh
```

**Docker**: everything included, for amd64 and arm64.

```bash
docker pull abhaypratap/spendlens
docker run --rm abhaypratap/spendlens tags --source demo --summary
```

Run `SPENDLENS_DRY_RUN=1 sh install.sh` to see each installer step without changing anything. To run the image against your account, see [docs/docker.md](docs/docker.md).

## Try it in thirty seconds

Every command replays bundled sample data. You need no AWS account and no credentials:

```bash
spendlens tags --source demo --summary
spendlens waste --source demo --summary --by service
spendlens report --source demo --out demo-report     # open demo-report/report.html
```

Then connect your account:

```bash
spendlens init       # writes finops.yaml: your mandatory tags, allowed instance types, budgets
spendlens doctor     # checks tools, your AWS identity and Cost Explorer access
```

spendlens uses whatever AWS role your terminal already has assumed. It stores no keys.

---

## Know what changed. Before finance asks.

Compare any two months, quarters or date ranges, by service, account, region or any tag. New and removed services
are always shown, and changes that cross your thresholds are flagged, with the rule stated: by default at least $10
**and** at least 20% (`cost.change_alarm_abs` / `cost.change_alarm_pct`).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/static/shots/cost-dark.webp">
  <img src="website/static/shots/cost-light.webp" width="100%" alt="Biggest cost changes by service between two months">
</picture>

```bash
spendlens cost diff                                   # last full month vs the month before
spendlens cost diff --mtd -g account                  # month to date vs the same days last month
spendlens cost diff --from 2026-07..2026-09 --to 2026-10..2026-12 -g tag:Team
spendlens cost trend -n 6                            # monthly pivot with month-over-month change
spendlens cost showback -t Team                      # spend per team, untagged share called out
spendlens cost diff -g service -b tag:Name           # inside each service: which tagged resources changed
spendlens cost diff -g usage-type --top 15           # what inside the services changed (logs? storage? transfer?)
spendlens cost diff -f html -o cost-diff.html        # charted report: tiles, flagged list, change and spend charts
```

## One number. Every dollar explained.

spendlens's own rightsizing and waste findings are merged with AWS Cost Optimization Hub and Savings Plan coverage.
Each resource counts once, AWS's figure wins where both exist, and every adjustment is listed.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/static/shots/savings-dark.webp">
  <img src="website/static/shots/savings-light.webp" width="100%" alt="Savings summary: one de-duplicated monthly total and its sources">
</picture>

```bash
spendlens savings summary          # one de-duplicated total (reuses today's checks, runs the rest)
spendlens savings summary --refresh  # re-check all four sources now
spendlens savings hub              # AWS Cost Optimization Hub recommendations
spendlens savings commitments      # Savings Plan and RI coverage, expiring plans
```

## Every dollar, owned.

Mandatory, untagged, prohibited, tag-limit and allowed-value checks run across 70 resource types and show the exact
tags each resource is missing. Showback by team calls out the untagged share.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/static/shots/tags-dark.webp">
  <img src="website/static/shots/tags-light.webp" width="100%" alt="Tag compliance by check and by resource type">
</picture>

```bash
spendlens tags --summary
spendlens tags -k mandatory -r s3_bucket -r ec2_instance
spendlens tags --fail-on-alarm         # exit 3 on any alarm: a CI gate
```

## Stop paying for nothing.

65 rightsizing and waste controls cover unattached volumes, orphaned snapshots, idle load balancers and NAT gateways,
gp2 volumes that should be gp3, previous-generation instances, and Multi-AZ outside prod. Each comes with a monthly
dollar estimate.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/static/shots/waste-dark.webp">
  <img src="website/static/shots/waste-light.webp" width="100%" alt="Idle and orphaned resources with an estimated monthly saving for each">
</picture>

```bash
spendlens waste --summary --by service
spendlens rightsize -S ec2 -S rds --min-saving 50
spendlens rightsize --set sizing.ec2_cpu_low=10        # any threshold, for one run
```

The full catalog is in [docs/controls.md](docs/controls.md), and the mapping from the legacy mods is in [docs/control-mapping.md](docs/control-mapping.md).

### Clean up together

A waste check sees one resource. `spendlens graph` sees how resources connect: a stopped instance with its volumes, its Elastic IP and the snapshots of those volumes is one **cleanup group** with one total saving. It also finds snapshots of deleted volumes, unused AMIs with their snapshots, load balancers with no targets, and manual snapshots of deleted databases. Each resource is in at most one group, and waste alarms about the same resources are listed with the group rather than added to its saving.

```bash
spendlens graph orphans                          # cleanup groups (read-only)
spendlens graph show i-0abc123 -o graph.html     # what it connects to, and what the group costs
spendlens graph impact vol-0abc123               # what breaks or is left behind if it is deleted
spendlens graph owners --script tag-review.sh    # owner tags for untagged resources, from what they're attached to
spendlens graph traffic                          # NAT, cross-AZ and internet data transfer, from VPC flow logs
spendlens graph age --min-days 365               # old resources that still cost money
```

The review shows each group as a small graph, and each group is one item in its queue. With [Tailpipe](https://tailpipe.io) and your Cost and Usage Report, costs are what each resource was actually billed rather than price-book estimates. More: [Resource graph & cleanup](https://spendlens.devops-monk.com/docs/graph/).

## Month-end, without surprises.

Daily spikes are measured against a robust 30-day baseline. The projected month end is checked against your budget
and against every AWS Budget.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/static/shots/forecast-dark.webp">
  <img src="website/static/shots/forecast-light.webp" width="100%" alt="Month-end forecast against budgets, with a status for each">
</picture>

```bash
spendlens anomalies --fail-on-anomaly
spendlens forecast --fail-on-risk
```

## One command. The whole review.

```bash
spendlens review -o review.html
```

The FinOps AI analyst explains your AWS spend, flags the anomalies and lays out every saving. Then you decide what happens to each one. One printable HTML page:
- **Spend, explained:** monthly spend against the budget, a bridge from last month's total through each big change to this month's, the trend of the top services, a service → workload flow, spend by owner, and every service in a table.
- **Anomalies:** usual vs actual for each spike, with its severity and likely cause.
- **Savings, mapped:** the total by source and service, an effort × value matrix, whether each change is reversible, a removal or a commitment, and a flow from the check that found it to the service it saves on.
- **For human review:** approve, defer or reject every proposal with a note, watch the approved total update, and export the decisions as CSV. Decisions stay in your browser; nothing changes in AWS.

<img src="website/static/shots/review-light.webp" width="100%" alt="The review: the analyst's summary, five headline numbers and the steps from AWS data to your decision">

spendlens computes every number; the AI only writes the words (`--no-ai` uses a built-in rule-based summary). It shares one cached analysis with `spendlens brief`, so running both asks the AI once. `spendlens report` writes `review.html` too.

<img src="website/static/shots/review-queue-light.webp" width="100%" alt="The review queue: approve, defer or reject each proposal, add a note, export the decisions as CSV">

## One page for managers.

```bash
spendlens brief -o brief.html
```

The dashboard has every detail. The brief is the page you send to a manager: an analyst summary written by the FinOps AI profile, five headline numbers, key insights ranked by impact, monthly spend against the budget, what changed, anomalies with their likely cause, and a checklist of the largest savings to review. One printable HTML file.

<img src="website/static/shots/brief-light.webp" width="100%" alt="The executive brief: a summary of spend and forecast, five headline numbers and key insights">

- **Numbers you can trust:** spendlens computes every figure; the AI only writes the words, from those figures and read-only queries on your saved history.
- **No AI? Still a brief:** `--no-ai` (or an AI that's not set up) uses a built-in rule-based summary, and nothing is sent anywhere.
- **In CI:** `spendlens report` writes `reports/brief.html` too; add `--ai` for the AI summary.

## Ask anything. Get answers from your data.

```text
$ spendlens ask "Why did our bill go up last month?" --show-tools
› history_overview · 14 saved runs
› history_query    · cost_diff run #7 · read-only

Spend rose +$959 (+1.8%) to $55.4K in October.
  · EC2 grew +$3,698 (+17.6%)
  · Amazon Bedrock grew +$1,660 (+692%)
  · Redshift was decommissioned: −$3,710
Bedrock and Redshift crossed your alert thresholds.
Source: cost_diff run #7, 2026-11-09
```
<sub>An illustrative answer, using the figures in the bundled demo data.</sub>

Supported models:
- **Claude** on Amazon Bedrock with your own AWS role, or on the Anthropic API (official Anthropic SDK).
- **OpenAI, Azure OpenAI** or any **OpenAI-compatible** server (official OpenAI SDK).

The assistant answers from saved history first, which is offline and free. It queries live AWS only with `--live`, and then it can answer from any of the AWS plugin's 589 tables:
- **Right first time:** it looks up the real tables, columns and required filters in a catalog built from the Steampipe AWS plugin's own source, and every query is checked before it reaches AWS. A wrong column comes back with "did you mean".
- **Patient:** slow queries run as background jobs that the model waits for, instead of timing out.
- **Cached:** live results are reused while fresh (15 min for resources, 5 for metrics, 6 h for Cost Explorer), and the same question on the same data costs no tokens. `--fresh` skips the cache.
- **Sessions:** `spendlens ask --new "..."` starts a conversation, and `spendlens ask -C "..."` follows up in a later command with the earlier answers remembered. Name one with `-S ecs`; `spendlens session list | show | delete` manages them.
- **Save it:** `-o answer.html` saves the answer as a styled page (or `.md`); `spendlens session show NAME -o session.html` saves a whole conversation.

Configure it with `llm.*` in `finops.yaml`. See [the AI assistant guide](https://spendlens.devops-monk.com/docs/ai-assistant/).

### The assistant's tools, for you

The table search, table description and query checker the assistant uses also work as commands. They run offline from the bundled catalog: no AWS calls, no Steampipe and no AI provider.

```bash
spendlens schema find "unattached EBS volumes"          # which tables answer this? (-n 3, --json)
spendlens schema show aws_cost_usage                    # columns, required filters, fast filters, examples
spendlens schema check "select volume_id, size_gb from aws_ebs_volume"
```

```text
✗ column size_gb does not exist in aws_ebs_volume
  did you mean: size
```

`schema check` exits with 1 when a query would fail: an unknown table or column, a missing required filter, or a JSON operator on a text column. It warns when a Cost Explorer query has no date range, a metric query has no time range, or a wide table is read with `select *`. Use it in scripts and CI to catch mistakes before your queries reach AWS.

### The cache

Live results, answers and brief summaries are kept in `ai-cache.db`, next to the history database. Each kind is reused only while fresh, and only for the same Steampipe connection, regions and AWS identity.

```bash
spendlens cache show --entries     # what's cached, how old it is, when it expires
spendlens cache clear --queries    # forget live results; --answers for answers and brief summaries
spendlens cache clear              # everything
spendlens ask --live --fresh "How many instances are running right now?"   # skip the cache once
```

Tune the freshness, or turn the cache off, with `llm.cache.*` in `finops.yaml` (see [Cache commands](https://spendlens.devops-monk.com/docs/ai-assistant/#cache-commands)).

## Scheduled. Notified. Hands-off.

```bash
spendlens report --out reports                     # every check, plus reports/report.html
spendlens notify --slack --teams --only-if-changes # a summary, only when something needs attention
spendlens init --ci gitlab                         # or --ci github: a scheduled pipeline on a read-only OIDC role
```

- **`report`:** if one step fails, for example because of a missing permission, the others still run and write their output.
- **`notify`:** reads webhook URLs only from `$SPENDLENS_SLACK_WEBHOOK` and `$SPENDLENS_TEAMS_WEBHOOK`.
- **CI templates:** keep the run history between pipelines.

### Always fresh, never half-empty

`dashboard`, `notify` and `savings summary` combine many checks. By default they reuse every check already run **today** and run the rest live first, so the result covers everything and is at most a day old. A check run by one of them is reused by the others later that day.

```bash
# Default: reuse today's checks, run the rest
spendlens dashboard -o report.html
spendlens notify --slack
spendlens savings summary

# Re-run everything now, e.g. after a deployment or clean-up
spendlens dashboard --refresh -o report.html
spendlens notify --slack --refresh
spendlens savings summary --refresh

# Saved runs only, no AWS calls (e.g. in CI right after spendlens report)
spendlens dashboard --source last -o report.html
spendlens notify --slack --source last
spendlens savings summary --source last
```

If a check can't run, for example because of a missing permission, the output says why and uses that check's last saved run.

Setup is in [docs/ci.md](docs/ci.md).

---

## Read-only. By design.

spendlens never creates, changes, tags or deletes anything. Savings are proposals for a person to review. The
[IAM permissions](docs/permissions.md) page lists the read-only actions each feature needs.

Every query the AI assistant writes goes through three safety layers:
1. **SQL guard.** The query is parsed with sqlglot, and only a single read-only `SELECT` passes.
2. **Read-only history.** SQLite opens with `mode=ro` and `query_only`, and an authorizer denies everything except reads.
3. **Read-only sessions.** With `--live`, Steampipe sessions run with `default_transaction_read_only`, a timeout and a row cap.

## Your standards, in one file

Every threshold and policy is a typed parameter. Later layers override earlier ones:

| Layer | Example |
|---|---|
| Built-in defaults | n/a |
| `finops.yaml` | `sizing: {ec2_cpu_low: 15}` |
| Environment | `SPENDLENS__SIZING__EC2_CPU_LOW=12` |
| Command line | `--set sizing.ec2_cpu_low=10`  ·  `--set 'tagging.mandatory=[Env, Owner]'` |

Run `spendlens params --filter ec2` to see every value, where it came from, and its environment-variable name.
Unknown keys, typos and out-of-range values are rejected, and the error names the layer they came from.

## Commands at a glance

| Command | What it does |
|---|---|
| `tags` | Tag compliance across 70 resource types |
| `rightsize` · `waste` | 65 controls with $/month estimates |
| `graph orphans` · `owners` · `traffic` · `age` · `show` · `impact` | How resources connect: cleanup groups with one total saving each, suggested owner tags for untagged resources, data transfer paths from VPC flow logs, resource age with cost, a resource's neighbours drawn as a page, what deleting one affects |
| `cost diff` · `trend` · `showback` · `resources` | What changed, where, and who owns it |
| `anomalies` · `forecast` | Daily spikes; month-end projection against budgets |
| `savings hub` · `commitments` · `summary` | AWS recommendations, coverage, one de-duplicated total (`summary --refresh`: re-check now) |
| `dashboard` | A self-contained HTML report with nine tabs, always covering every check (`--refresh`: re-run all now) |
| `review` | The whole review in one page: AI analysis, spend bridge and flows, anomalies, savings, and a queue to approve, defer or reject each saving (CSV export) |
| `brief` | One page for managers: AI summary, key numbers, charts and a savings review checklist |
| `ask` | Questions in plain English, read-only (`--live`: any AWS table, schema-checked and cached; `--new` / `-C` / `-S NAME`: sessions that remember follow-ups) |
| `session list` · `show` · `delete` · `clear` | Saved `ask` sessions (offline) |
| `schema find` · `show` · `check` | Find the AWS table for a question, read its columns and filters, check a query before it runs (offline) |
| `cache show` · `clear` | What the AI assistant has cached and when it expires; forget it |
| `report` · `notify` | Run everything; post Slack or Teams summaries (`notify --refresh`: re-check first) |
| `history list` · `diff` | Saved runs, and new or resolved alarms between them |
| `find` · `open` | Everything saved about one resource (findings, cost, links, cleanup group, owner); build a report from saved runs and open it in the browser |
| `init` · `doctor` · `params` · `config` | Set up, check, and inspect configuration |

Every report command accepts:
- `--format table|csv|json|html` and `--out FILE`;
- `--source live|last|demo`.

`--source last` replays your latest saved run offline. `dashboard`, `notify` and `savings summary` also accept `--refresh` (see [Always fresh](#always-fresh-never-half-empty)). The full reference is the [CLI reference](https://spendlens.devops-monk.com/docs/cli/).

## Present it to your team

[docs/ppt/spendlens FinOps for our AWS estate.pptx](docs/ppt/spendlens%20FinOps%20for%20our%20AWS%20estate.pptx) is a 13-slide deck, with speaker notes, that introduces spendlens and proposes a read-only pilot. Fill in the bracketed placeholders before presenting.

## Extending

spendlens's own features are plugins. A package can add commands, Powerpipe controls, savings estimators, AI tools,
doctor checks and dashboard tabs:

```toml
[project.entry-points."spendlens.plugins"]
acme = "acme_spendlens"
```

```python
def register(reg):
    reg.add_typer(my_typer_app, "acme")        # spendlens acme ...
    reg.add_mod(Path(__file__).parent / "mod")
    reg.add_doctor_check(check_fn)
```

See [Extending](https://spendlens.devops-monk.com/docs/extending/) for the full API.

## Development

```bash
git clone https://github.com/devops-monk/spendlens.git && cd spendlens
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make test              # unit + integration (fast, no cloud)
make test-system       # end to end against the Moto AWS emulator through an isolated Steampipe
make lint
make generate          # after changing the config schema, tag catalog, controls or demo data
```

CI runs on every push: lint, tests on Python 3.11 and 3.13, a website check, and an offline Docker smoke test.

## License

spendlens is licensed under the [Apache License 2.0](LICENSE). Some controls are adapted from Turbot's Apache-2.0
Powerpipe mods. The Docker image includes Steampipe and Powerpipe (AGPL-3.0, unmodified); see [NOTICE](NOTICE).
