# Changelog

What changed in each spendlens release, newest first. Upgrade with the [installer](https://spendlens.devops-monk.com/docs/getting-started/) or `docker pull abhaypratap/spendlens:latest`.

## 0.4.10

- **Next time, without the AI.** After each answer, `spendlens ask` lists the commands that get the same data without the AI: `spendlens query`, `spendlens cloudtrail events` with the same filters, the graph commands and more. They're built from the tool calls that answered, so they run exactly the same query, with no tokens. `--no-hints` or `llm.command_hints: false` turns it off; `--json` has them as `cli_commands`.
- **Copy buttons** on the home page's commands; the site's CSS and JS are versioned by content, so browsers never keep a stale copy.

## 0.4.9

- **CloudTrail, as its own feature.** `spendlens cloudtrail events` searches with any filter: a time range or exact dates, user, API calls with `*`, service, resource, resource type, access key, source IP or CIDR, error codes, reads or writes, region. It has presets (console logins, deletes, deployments, security groups, IAM, access denied, errors, root), `summary` breakdowns, `trails`, and exports to CSV, Excel, JSON, JSON Lines, Markdown, HTML or YAML. Sources, best first: Tailpipe, the trail's CloudWatch Logs group (new), then LookupEvents.
- **The assistant's `cloudtrail_events` tool** takes the same filters, shows the SQL it ran, and can be told to skip Tailpipe ("using Steampipe ...").
- **Sessions.** Every conversation is saved. `spendlens session list` shows the ids; `spendlens session open ID` shows the earlier chat and continues it, with the earlier answers and the data behind them in context. The newest 100 automatic sessions are kept; named ones are never deleted.
- **`export_query`.** "All instances to Excel" writes every row (up to 100,000) straight to a file, without sending the rows to the model.
- **Results that are too big for the model are trimmed by whole rows**, never cut mid-JSON, with a note to aggregate.
- **Privacy.** `llm.redact: [account_ids, emails, ips]` sends placeholders to the AI provider and restores the real values in answers. A data note says when account data goes to a provider outside your AWS account. History, cache and session files are readable by your user only.
- **Tailpipe only when it can help.** It's used automatically only when enabled, installed and configured, and for CloudTrail only when its logs cover the time window. `tailpipe.enabled: false` or `ask --no-tailpipe` turns it off. `spendlens sources` shows where each kind of data comes from.
- **The assistant answers anything about the account.** It knows its live scope (connection, regions, accounts) and names a missing IAM permission. LookupEvents SQL with two lookup filters, or an ORDER BY, is caught before it runs. The default tool-step limit is now 20.
- **`spendlens --help` groups commands by job:** save money, understand spend, govern and audit, ask, reports, data sources, setup.
- **For maintainers:** `scripts/smoke_live.py` (read-only checks against a real account) and `scripts/eval_ai.py` (answer quality on a real account, for any provider).

## 0.4.8

- **"What changed" no longer hangs without Tailpipe.** CloudTrail LookupEvents searches send AWS one filter (two made the plugin drop the request), no longer sort (which made Steampipe read every page before answering), search a whole service newest first in widening windows, and stop after a minute (`spendlens changes --max-wait`) with what they found and how far back they got. The assistant asks for the action itself when the question implies one: "which ECS service was deployed last" looks up `UpdateService`, not every ECS event.

## 0.4.7

- **`tailpipe init` says what to fix.** When discovery can't read a source, the note now names the fix: an expired AWS SSO session ("run `aws sso login`"), or an AWS CLI too old to list CUR 2.0 exports (2.15 or later needed). Before, the message was cut off or just said "help".

## 0.4.6

- **Ask about anything in the account.** `spendlens ask` now uses live AWS by default (read-only, through the Steampipe AWS plugin's table schema); `--no-live` keeps it to saved runs.
- **What changed, and who changed it.** `spendlens changes` lists CloudTrail write events with who made them, filtered by user, resource, service or API call. The assistant has the same as its `aws_changes` tool: "What changed today, and who did it?"
- **Files in and out.** Attach files with `ask -a budget.xlsx` (or write their path in the question): csv, Excel, JSON, YAML, PDF, Word, logs, Terraform. Ask for a file and the assistant writes it to `./spendlens-output`: Excel, CSV, Word, Markdown, HTML, JSON, scripts and Terraform for review.
- **Tailpipe included and set up for you.** The installer and Docker image now include Tailpipe and its AWS plugin. `spendlens tailpipe init` finds your CloudTrail, flow log and CUR buckets (read-only) and writes the config; `spendlens tailpipe collect` collects them. The assistant's `tailpipe_query` tool covers CloudTrail logs, flow logs and the CUR.

## 0.4.5

- **An expired login says so.** `spendlens doctor` now reports an expired AWS SSO login (or expired temporary credentials) as "aws sso login --profile …", not as a missing permission. `spendlens graph collect` stops at the first such error and prints the fix once, instead of failing for every resource type.
- **A deck to present spendlens.** [docs/ppt](https://github.com/devops-monk/spendlens/tree/main/docs/ppt) has a 13-slide PowerPoint deck with speaker notes that proposes a read-only pilot.

## 0.4.4

- **Small fixes from a full offline smoke test of every command.**
  - `spendlens schema find` takes plain words without quotes (`spendlens schema find who deleted the bucket`).
  - `spendlens history` says what to do when nothing has been saved yet.

## 0.4.3

- **Resource age with cost.** `spendlens graph age` shows how old resources are and what they cost: by age band, by type, and the costliest old ones, each with its owner and cleanup group. `--min-days 365` lists what is over a year old; `spendlens open age` gives the page.
- **CloudTrail beyond 90 days.** If [Tailpipe](https://tailpipe.io) has collected your trail's logs, the AI assistant gets a `cloudtrail_logs` tool covering the full history and S3 data events. It runs locally and queries only `aws_cloudtrail_log`, always with a time range.
- **Sharper data transfer.** `graph traffic` uses the custom flow-log fields when they are present: the service AWS names, and the original packet addresses. It also checks which route tables each gateway endpoint is on, so it can propose "associate the S3 endpoint with route table rtb-…" where an endpoint exists but a subnet doesn't use it.

## 0.4.2

- **Who created it (opt-in).** With `graph.owner_from_cloudtrail: true`, `spendlens graph collect` asks CloudTrail who created each resource. It runs bounded LookupEvents queries over the last 90 days at most. `spendlens graph owners` then suggests the creator for your `Owner` tag, with medium confidence, when nothing attached already names an owner. It is off by default: it is slower in busy accounts and needs `cloudtrail:LookupEvents`. [More](https://spendlens.devops-monk.com/docs/graph/#suggested-owners)

## 0.4.1

- **Ask CloudTrail who did what.** `spendlens ask --live "Who deleted the old-reports bucket?"` now finds the CloudTrail event tables for "who / deleted / changed / API calls / console logins" questions. It uses LookupEvents (the last 90 days, no setup) with a time range AWS receives, and falls back to the trail's CloudWatch Logs for older or more detailed events. A CloudTrail query without such a range is refused before it reaches AWS, with the exact fix. [More](https://spendlens.devops-monk.com/docs/ai-assistant/#cloudtrail-who-did-what-when)
- **Better table search for plurals.** Questions like "how many running instances" now find the right table first.

## 0.4.0

- **The resource graph: follow the money, not one resource.** `spendlens graph collect` reads how your resources connect (instances, volumes, snapshots, AMIs, IPs, NAT gateways, load balancers, RDS, ECS, Lambda, route tables), read-only, with a cost on each one. The relationships follow the Steampipe AWS Insights mod. [More](https://spendlens.devops-monk.com/docs/graph/)
- **Cleanup groups.** `spendlens graph orphans` finds connected resources that can be removed together, each with one total saving: a stopped instance with its volumes, Elastic IP and snapshots; snapshots of deleted volumes; unused AMIs; load balancers with no targets; snapshots of deleted databases. Each resource is in one group at most, and waste alarms are linked, not added. The review shows each group as a small graph and as one queue item.
- **Suggested owners.** `spendlens graph owners` suggests tags for untagged resources from what they're attached to (a volume from the instance that mounts it), with a confidence and a reason. `--script` writes the tagging commands for you to review and run. Suggestions also show in `tags` and in the review.
- **Explore and ask.** `graph show` draws any resource with its links and cost (HTML, SVG, Mermaid, DOT, JSON), and `graph impact` shows what deleting it would affect. The AI assistant gets `resource_context`, `cleanup_groups`, `suggested_owners` and `traffic_paths`, and saved answers draw the resources they talk about.
- **Billed costs.** With [Tailpipe](https://tailpipe.io) and your Cost and Usage Report, each resource's cost is what it was billed. Without them, spendlens uses Cost Explorer resource data, then the price book. NAT gateway and load balancer costs are split across what uses them.
- **Data transfer.** `spendlens graph traffic` prices VPC flow logs (via Tailpipe): cross-AZ, through NAT gateways, out to the internet. It proposes free S3/DynamoDB gateway endpoints where that traffic goes through a NAT gateway today.
- **`spendlens find`** shows everything saved about one resource. **`spendlens open`** builds a report from saved runs and opens it in your browser.

## 0.3.4

- **Sessions: follow-up questions that remember.** `spendlens ask --new "…"` starts a session; `spendlens ask -C "…"` continues it in a later command, even another day; `-S NAME` keeps topics apart. `spendlens session list|show|delete|clear` manages them. Works with every provider.
- **Save an answer as a page.** `spendlens ask "…" -o answer.html` writes a styled page with the tool calls behind it (or `.md`); `spendlens session show NAME -o session.html` saves a whole conversation. The assistant answers in Markdown and never writes HTML, so long answers no longer hit the token limit.
- **Answers stay on the question.** No unrequested savings queries; configuration questions (e.g. Aurora ACU min/max) use live AWS with `--live`, or say that `--live` is needed.
- **ECS findings with structure.** ECS task sizing findings now carry the service, task family, launch type, vCPU, memory and the company maximum as fields.
- **Architecture page** explains how a command runs, what each flag changes, how the assistant knows the AWS tables, and sessions.

## 0.3.3

- **"Why did RDS cost change?" in a few steps.** A new `history_cost` tool gives the assistant a service's saved cost in every cost run (diff, trend, the breakdown inside it) plus its findings in one call; short names like `rds`, `ec2`, `nat` work. The first overview also lists each cost run's periods and exact service names.
- **Always an answer.** At the tool-step limit the assistant answers from what it found and says it may be incomplete, instead of giving up.
- **`--show-tools` is live.** Each tool call prints as it runs: the tool, its SQL, the row count, the time and the first rows, with a spinner while the model thinks.

## 0.3.2

- **Leave a check out of the review.** `spendlens review --skip tags -o review.html` doesn't run the tagging check, doesn't send it to the AI and doesn't show it. Works for any check and repeats (`--skip tags --skip savings_hub`); `spendlens report --skip …` passes it on to `review.html`.

## 0.3.1

- **One command. The whole review.** `spendlens review -o review.html` writes the full FinOps review on one page: the AI analyst's summary, AWS spend explained (a bridge from last month to this month, trends, a flow from each service to the workloads behind it, owners), anomalies with their usual vs actual spend and likely cause, and every saving by source, service, effort × value and kind of change. A review queue lets you approve, defer or reject each proposal with a note and export the decisions as CSV. spendlens computes every figure; `--no-ai` uses the rule-based summary. [More](#one-command-the-whole-review)
- **`spendlens report` also writes `review.html`**, sharing the brief's AI analysis (same data, asked once).
- Savings Plan and reservation purchases now count as high effort in `brief` too.

## 0.3.0

- **One page for managers.** `spendlens brief -o brief.html` writes an executive report: an analyst summary by the FinOps AI profile, five headline numbers, key insights, monthly spend against the budget, what changed, anomalies with their likely cause, and a checklist of the largest savings to review. spendlens computes every figure; `--no-ai` uses a built-in rule-based summary. [More](#one-page-for-managers)
- **Ask about any AWS table, right first time.** With `--live`, the assistant looks up the real tables, columns and required filters in a catalog built from the Steampipe AWS plugin's source (589 tables), and every query is checked before it reaches AWS. A wrong column comes back with "did you mean".
- **Patient and cached.** Slow live queries run as jobs the assistant waits for. Fresh results are reused (15 min for resources, 5 for metrics, 6 h for Cost Explorer), and the same question on the same data costs no tokens. `--fresh` skips the cache.
- **The assistant's tools, for you.** `spendlens schema find | show | check` searches the catalog and checks a query offline; `spendlens cache show | clear` inspects the cache.
- **`spendlens report` also writes `brief.html`** (rule-based; add `--ai` for the AI summary).
- **The [cheat sheet](https://spendlens.devops-monk.com/cheatsheet/)** now has 148 commands, with a section for schema and cache.

**Heads-up:** the assistant's live tools are renamed (`steampipe_query` is now `aws_query`, plus `aws_find_tables`, `aws_describe_table`, `aws_check_query`, `aws_query_result`), and a live query may now run for up to 5 minutes (`llm.query_timeout_s`, was 60 s). Full notes: [release v0.3.0](https://github.com/devops-monk/spendlens/releases/tag/v0.3.0).

## 0.2.0

- **Always fresh, never half-empty.** `dashboard`, `notify` and `savings summary` reuse every check already run today and run the rest first, so they always cover everything. Use `--refresh` to re-run all, or `--source last` for no AWS calls. [More](#always-fresh-never-half-empty)
- **Cost drill-down.** `spendlens cost diff -g service -b tag:Name` shows which resource or app is behind each service's change. `-b` also takes `usage-type`, `region` or any tag; add `-f html` for a searchable report.
- **Reports that explain themselves.** Every dashboard tab and HTML report has a "How to read this" box with your actual thresholds and the setting that changes each one. "Flagged" always states its rule (e.g. at least $10 AND at least 20%).
- **Charted reports for every cost command.** `-f html` on `cost diff`, `trend`, `showback`, `anomalies` and `forecast` gives bar, line and budget charts with the full table.
- **Findings at scale.** `-f html` on `tags`, `rightsize` and `waste` gives an interactive report with filters and search, each check's thresholds, and why each resource alarmed. It handles tens of thousands of results.
- **Live progress, and eu-west-1 by default.** Long runs show the current step and an elapsed timer; set `aws.regions` to query other regions.
- **A [cheat sheet](https://spendlens.devops-monk.com/cheatsheet/)** of 125 ready-to-copy commands, grouped by the question they answer.

**Heads-up:** `dashboard`, `notify` and `savings summary` now call AWS by default when a check hasn't run today. Add `--source last` to keep them offline. Full notes: [release v0.2.0](https://github.com/devops-monk/spendlens/releases/tag/v0.2.0).

---
