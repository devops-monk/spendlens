# AI assistant

`spendlens ask` answers questions about your spend in plain English, using your data, with any major LLM provider. It's read-only: it can look things up, never change them.

```bash
spendlens ask "What are my top 5 savings opportunities?"
spendlens ask "Why did EC2 cost go up last month?" --show-tools
spendlens ask "Which teams own the most untagged spend?"
spendlens ask "Which running instances are outside the allowed families?"
spendlens ask "What changed in the account today, and who did it?"
spendlens ask "Export unattached EBS volumes with their monthly cost to Excel"
spendlens ask -a budget.xlsx "Compare this budget with last month's cost per service"
spendlens ask -i                                       # follow-up questions in one session
spendlens ask --source demo "Summarise the waste findings"
```

| Option | Meaning |
|---|---|
| `-i, --interactive` | Keep asking follow-up questions in one conversation |
| `--source` | History to answer from: `last` (yours, default) or `demo` |
| `--live` / `--no-live` | Allow read-only live AWS queries through Steampipe. On by default (`llm.live: true`); `--no-live` answers from saved runs only |
| `-a, --file FILE` | Attach a file for the assistant to read: csv, xlsx, json, yaml, pdf, docx, logs, Terraform and other text. Repeatable. File paths written in the question are attached too |
| `--out-dir DIR` | Where the assistant writes files you ask for (default `llm.output_dir`, `./spendlens-output`) |
| `--show-tools` | Show each tool call as it runs: the tool, its SQL, the result and the first rows |
| `--max-steps` | Tool-call limit per question (default `llm.max_steps`, 12). At the limit the assistant answers from what it found and says it may be incomplete |
| `--json` | The answer plus the full audit trail, machine-readable |
| `--fresh` | Ignore cached answers and live results; ask and query again |
| `--new` | Start a new session that later questions can follow up (with `-S NAME`: start that session over) |
| `-C, --continue` | Follow up in the last session used: the assistant remembers its earlier questions and answers |
| `-S, --session NAME` | Continue (or start) a named session |
| `-o, --out FILE` | Also save the answer as a page: `.html` (styled, light and dark, with the tool calls) or `.md` |

## Sessions: follow up across commands

`-i` keeps a conversation going while it's open. A session keeps it between separate commands, so you can ask, read the answer, and follow up later:

```bash
spendlens ask --new "Which ECS services are over-provisioned?"   # starts a session
spendlens ask -C "Which cluster has the most?"                   # remembers the first answer
spendlens ask -C --live "Show their CPU and memory settings"     # mix history and live
spendlens ask -S rds "How many Aurora clusters are there?"       # a separate, named session
```

Each session stores the questions and the final answers in `sessions/<name>.json` next to the history DB. A follow-up replays the last 12 exchanges as plain text, so the model has the context without paying again for earlier tool results. It works with every provider; a follow-up is never answered from the answer cache.

| Command | What it does |
|---|---|
| `spendlens session list` | Every session, most recent first; `*` marks the one `-C` continues |
| `spendlens session show [NAME]` | The questions and answers of a session (default: the current one); `-o session.html` saves them all as one page |
| `spendlens session delete NAME` | Delete one session |
| `spendlens session clear --yes` | Delete every session |

## Save an answer as a page

Ask for a report and the assistant writes Markdown, never HTML. Add `-o` and spendlens saves the answer as a self-contained page in the same style as the other reports, with the question, the answer's tables and a "How it was answered" list of every tool call:

```bash
spendlens ask --live "Which ECS services should be right-sized? Name each service." -o ecs-rightsize.html
spendlens ask -C "Which of those have no autoscaling?" -o ecs-autoscaling.md
spendlens session show ecs -o ecs-session.html     # the whole conversation on one page
```

Raw HTML in an answer is shown as text, so names and tags from your account can never become markup.

When an answer is about how resources connect, the assistant adds a ```` ```graph ```` block naming a resource. The saved page draws that resource and its links from the saved [resource graph](../graph/); the terminal shows just the id:

```bash
spendlens ask "What is attached to legacy-reporting, and can it go?" -o legacy.html
```

## The executive brief

`spendlens brief` uses the same FinOps analyst profile to write a one-page summary for managers: a headline, key insights, recommended next steps, risks, and a likely cause for each anomaly. spendlens computes every figure first; the AI only writes the words, using the same read-only history tools (never live AWS). See [spendlens brief](../dashboard/#spendlens-brief-one-page-for-managers).

```bash
spendlens brief -o brief.html
```

## How it answers

1. It reads `history_overview`: which saved runs exist, each cost run's periods and dimensions, and the exact service names.
2. For "why did a service's cost change", `history_cost` returns that service's saved cost in every cost run (diff, trend, the breakdown inside it), plus its findings, in one call. Short names work: `rds`, `ec2`, `s3`, `nat`.
3. For questions about specific resources ("what is vol-0abc attached to?", "who owns these untagged volumes?", "what can we clean up?", "why does the NAT gateway cost so much?"), it uses the saved [resource graph](../graph/):
   - `resource_context`: a resource's links, the group's cost, its cleanup group, suggested owner, impact and shared NAT or load balancer cost;
   - `cleanup_groups`: what can be removed together;
   - `suggested_owners`: owner tags for untagged resources;
   - `traffic_paths`: data transfer by path (cross-AZ, NAT, internet) from VPC flow logs, with gateway-endpoint proposals.
4. Anything else comes from the history database with `history_query`. All seven history tools are offline and free.
5. When history can't answer, it queries AWS live through Steampipe (below): on by default, read-only, off with `--no-live`.
6. It answers with the key numbers, the evidence, and the run each number came from. Savings are labelled as estimates with their source.

Text in tool results (resource names, tags) is treated as data, never as instructions.

It answers only what you asked: no savings or recommendations unless you ask for them. The history holds cost data and check results, not a full inventory, so configuration questions ("how many Aurora clusters, and their ACU min and max?") go to live AWS. With `--no-live`, the assistant says what the history can't answer instead.

With `--show-tools`, every step prints as it happens: the tool and its SQL when it starts, then the row count, the time and the first rows when it returns. A spinner shows while the model is thinking.

```text
▸ 2 history_cost {"service": "rds"}
    ✓ 32 row(s) · 2 ms
    cost_diff · run #7
     kind  period   dimension  grp                                 amount
     p0    2026-09  service    Amazon Relational Database Service  9365.26
     p1    2026-10  service    Amazon Relational Database Service  9056.54
```

If the step limit is reached, the assistant still answers from what it found and notes that the answer may be incomplete. Raise the limit with `--max-steps`.

## Live AWS questions: right first time

Live AWS is on by default, so the assistant can answer from any of the 589 tables of the Steampipe AWS plugin: inventory, security groups, IAM, Lambda runtimes, CloudWatch metrics, Cost Explorer and more. Four layers make sure it writes a correct query, waits for slow ones and doesn't ask AWS twice:

| Layer | What it does |
|---|---|
| **Catalog** | Built from the AWS plugin's own source and docs (v1.35.0, the version spendlens installs): every table's columns with their meaning, **required filters** with valid values, fast filter columns and example queries. When Steampipe is running, the columns your installed plugin reports are merged in and decide what exists. |
| **Checker** | Runs before every query, offline: real tables and columns (with "did you mean"), required filters present, JSON operators only on JSON columns, a date range on Cost Explorer and a time range on metrics. A mistake goes back to the model with the exact fix and never reaches AWS. It passes 1,613 of the plugin's 1,620 documented examples; the 7 it rejects are mistakes in those examples. |
| **Runner** | Each query runs as a background job on its own read-only connection. If it takes longer than `llm.query_wait_s` (30 s), the model gets a job id and waits for it with `aws_query_result`; waiting doesn't use up `llm.max_steps`. A query is cancelled after `llm.query_timeout_s` (300 s). Throttling is retried once. |
| **Cache** | Fresh results are reused: resources for 15 minutes, CloudWatch metrics, CloudTrail and logs for 5 minutes, Cost Explorer for 6 hours. The same question on the same data is answered from the cache with no tokens, until a new run is saved or the live data's window ends. Results are scoped to your Steampipe connection, regions and AWS identity. |

The model works in a fixed order: `aws_find_tables` → `aws_describe_table` → `aws_query` (→ `aws_query_result` while a job runs). `aws_check_query` checks a query without running it. Each result says when its data is from (`as_of`) and whether it came from the cache.

```bash
spendlens ask --live "Which EBS volumes are unattached and how big are they?" --show-tools
spendlens ask --live "Which security groups allow 0.0.0.0/0 on port 22?"
spendlens ask --live --fresh "How many instances are running right now?"   # skip the cache
```

## Files in, files out

**Files you give it.** Attach files with `-a` (repeatable), or write their path in the question. The assistant reads them with a `read_file` tool, page by page, so big files never go to the model in one piece. It reads text formats (csv, tsv, json, yaml, md, logs, sql, Terraform, html and more), Excel (`.xlsx`, every sheet), PDF and Word (`.docx`). Only the files you attached can be read. Their content is treated as data, never as instructions.

```bash
spendlens ask -a budget.xlsx "Compare this budget with last month's actual cost per service"
spendlens ask "Which of the instances in ./inventory.csv no longer exist?"
spendlens ask -a incident.md "Which resources named in this incident changed in the last 48 hours?"
```

**Files you ask for.** Ask for a file and the assistant writes it with a `write_file` tool into `./spendlens-output` (or `--out-dir`):
- **Tables:** `.xlsx` (one sheet per result), `.csv`, `.tsv`, `.json`, `.jsonl`, `.md`, `.html`, `.yaml`.
- **Documents:** `.docx` (from headings, lists and paragraphs, with tables), `.md`, `.html`, `.txt`.
- **Code and config:** `.sql`, `.sh`, `.tf`, `.py`, `.yaml`, `.json`, and diagrams as `.mmd`, `.dot` or `.svg`.

Table data is exported straight from the query result by its id, so a 500-row export doesn't pass through the model again. Files are never overwritten (`report-1.xlsx` is used instead), names can't leave the output folder, and nothing written is ever run: a script or Terraform file is a proposal for you to review.

```bash
spendlens ask "Export idle load balancers and unattached volumes, one sheet each, to Excel"
spendlens ask "Write a one-page Word summary of this month's top 5 savings for my manager"
spendlens ask "Give me a CSV of untagged resources with a suggested owner"
spendlens ask --out-dir reports "Write the AWS CLI commands to tag those volumes as a script I can review"
```

`-o answer.html` still saves the answer itself as a styled page.

## CloudTrail: what changed, who changed it

The assistant answers activity questions from CloudTrail:
- who deleted, created or changed something;
- when it happened;
- API calls by a user or role;
- console logins.

```bash
spendlens ask "What changed in the account today, and who did it?"
spendlens ask "Who deleted the old-reports bucket?"
spendlens ask "Which security group rules changed in the last 24 hours, and by whom?"
spendlens ask "Show console logins this week, with source IPs"
```

**`aws_changes` first.** For "what changed" and "who changed X", the assistant calls `aws_changes`. It lists CloudTrail write events, newest first, with counts by person or role and by API call, and filters by user, resource, service or API call. Routine calls (role assumptions, KMS, log delivery) are left out. It uses the trail's logs in Tailpipe when they have been collected (any age, data events), otherwise CloudTrail LookupEvents (90 days of management events). The same thing is a command:

```bash
spendlens changes                                   # the last 24 hours
spendlens changes --days 7 --event UpdateService      # ECS deployments
spendlens changes --days 7 --service ec2
spendlens changes --user alice --days 30
spendlens changes --resource sg-0abc123 --days 90 -f csv -o sg-changes.csv
```

**Without Tailpipe, an action is faster than a service.** LookupEvents returns 50 events a page and is rate-limited, and it takes one filter. An event name (`UpdateService` for ECS deployments, `UpdateFunctionCode` for Lambda deploys, `ModifyDBInstance` for RDS changes), a resource or a user matches few events and comes back in seconds. A whole service, or the whole account, can mean thousands of events: spendlens then searches newest first in widening windows (the last hour, 6 hours, a day, 3 days, a week and so on) and stops after about a minute (`--max-wait`, 60 seconds by default) with what it found and how far back it got. The assistant picks the event name for you when the question implies one, and says when an answer covers only part of the window. With CloudTrail logs collected by Tailpipe, there is no such limit.

**Other ways it searches:**
- **LookupEvents first.** It uses `aws_cloudtrail_lookup_event`, the last 90 days of management events with no setup. It always sets `start_time` and `end_time`, starts with the last 24 hours and widens only if needed. It narrows with one filter such as the event name (`DeleteBucket`, `TerminateInstances`, `ConsoleLogin`), the resource name or the user.
- **Time ranges are enforced.** Before any query reaches AWS, a CloudTrail query without a time range that AWS receives is refused, with the exact fix. A filter on `event_time` alone doesn't count: AWS would page through 90 days at about two calls a second.
- **Older or more detailed events** (error codes, source IPs beyond 90 days) come from the trail's CloudWatch Logs log group (`aws_cloudtrail_trail_event`), if your trail sends logs there.
- **Older history, data events, flow logs and the CUR, with Tailpipe.** The installer and the Docker image include [Tailpipe](https://tailpipe.io) and its AWS plugin. `spendlens tailpipe init` finds where your CloudTrail logs, VPC flow logs and Cost and Usage Reports land in S3 (read-only: trails and flow logs through Steampipe, CUR exports through the AWS CLI, which needs version 2.15 or later for CUR 2.0 exports, plus any locations in `finops.yaml` under `tailpipe:`) and writes `~/.tailpipe/config/spendlens.tpc`. `spendlens tailpipe collect` collects them (the last `tailpipe.collect_days`, 30 by default); `spendlens tailpipe status` shows what is there. The [Tailpipe page](../tailpipe/) has the design, the commands in order and troubleshooting. The assistant then gets a `tailpipe_query` tool: local DuckDB queries over `aws_cloudtrail_log`, `aws_vpc_flow_log` and `aws_cost_and_usage_report`, with no AWS calls. Each query must have a time range and is capped in rows; table functions and file-reading functions such as `read_csv` and `getenv` are refused.
- **What CloudTrail can't answer.** S3 object reads and writes are data events: they are recorded only by a trail with data event selectors. If nothing can answer, the assistant says so.

Questions that only *sound* like activity still use inventory, because the current state answers them: "which instances are stopped", "list my buckets".

Permissions: `cloudtrail:LookupEvents`, plus `logs:FilterLogEvents` for the CloudWatch Logs table. Both are in `ReadOnlyAccess`.

## Schema commands: the assistant's tools, for you

`spendlens schema` gives you the same table search, table description and query checker the assistant uses. All three work offline from the bundled catalog: no AWS calls, no Steampipe and no AI provider needed. Use them to write your own Steampipe queries, or to see why the assistant chose a table.

| Command | What it does | Options |
|---|---|---|
| `schema find "<question>"` | The tables that can answer a question, best first, with their service and required filters | `-n, --limit` (default 8), `--json` |
| `schema show <table>` | Every column with its type and meaning, required filters with valid values, fast filter columns, cost and time notes, example queries | `--no-examples`, `--json` |
| `schema check "<sql>"` | Checks one query without running it. Exit code 0 when it would run, 1 when it would fail | `--json` |

### Find a table

```bash
spendlens schema find "unattached EBS volumes"
spendlens schema find "IAM users without MFA"
spendlens schema find "monthly cost by service" --limit 3
spendlens schema find "nat gateways" --json | jq -r '.[].table'     # names only
```

```text
                      Tables for: unattached EBS volumes
 table                            service  what it holds                       required filters
 aws_ebs_volume                   EBS      Allows users to query AWS Elastic …
 aws_ebs_volume_metric_read_ops   EBS      Allows users to query AWS EBS …
 aws_ebs_volume_metric_write_ops  EBS      Allows users to query AWS Elastic …
Next: spendlens schema show aws_ebs_volume
```

The search matches table names, services, descriptions and column names, and knows common words: "volume" finds EBS, "bucket" S3, "database" RDS, "function" Lambda. Naming a table (`aws_eks_cluster versions`) puts it first.

### Read a table

```bash
spendlens schema show aws_ebs_volume
spendlens schema show aws_cost_usage --no-examples
spendlens schema show aws_cost_usage --json | jq -r .required_filters
```

The output starts with the filters, then lists the columns:
- **required filters** (red): `WHERE` conditions AWS needs before it can list the table at all, with their valid values. For example, `aws_cost_usage` needs `granularity`, `dimension_type_1` and `dimension_type_2`.
- **fast filters**: columns AWS filters for you when you use them in `WHERE` (`state`, `volume_type`, `encrypted` on `aws_ebs_volume`). These are fewer API calls and much faster than filtering afterwards.
- **cost note**: Cost Explorer tables bill about $0.01 per request, so bound them by `period_start`.
- **time note**: CloudWatch metric, CloudTrail and log tables need a time range.

The catalog ends with the plugin version it was built from (`Catalog: Steampipe AWS plugin v1.35.0`).

### Check a query before you run it

```bash
spendlens schema check "select volume_id, size from aws_ebs_volume where state = 'available'"
# ✓ OK · tables: aws_ebs_volume

spendlens schema check "select volume_id, size_gb from aws_ebs_volume where state = 'available'"
# ✗ column size_gb does not exist in aws_ebs_volume
#   did you mean: size

spendlens schema check "select dimension_1 from aws_cost_usage"
# ✗ aws_cost_usage requires these filters in WHERE: dimension_type_1 = '<value>' AND dimension_type_2 = '<value>' AND granularity = '<value>'
#   fix: add them; … granularity: The AWS cost granularity. Valid values are DAILY, MONTHLY, or HOURLY.
# ! aws_cost_usage has no date range: Cost Explorer bills about $0.01 per request
```

It checks:
- that the query is one read-only `SELECT` (writes are rejected);
- that each table and column exists, with "did you mean" for near misses, through aliases, joins and CTEs;
- that required filters are present;
- that JSON operators (`->`, `->>`) are used only on JSON columns.

It warns about:
- Cost Explorer queries without a date range;
- metric queries without a time range;
- `select *` on wide tables.

Warnings don't change the exit code, so you can use the command in scripts and CI:

```bash
spendlens schema check "$(cat query.sql)" || exit 1
spendlens schema check --json "select * from aws_ec2_instance" | jq '.warnings'
```

`--json` returns `ok`, `tables`, `errors` and `warnings`. Each error has a `problem`, a `fix`, `did_you_mean` and the table's `columns`.

## Cache commands

The assistant keeps its cache in `ai-cache.db`, next to the history database (by default `~/.local/share/spendlens/ai-cache.db`). It holds four kinds of entries:

| Kind | Reused while | Cleared by |
|---|---|---|
| Live query results | resources 15 min · metrics, CloudTrail, logs 5 min · Cost Explorer 6 h | `--queries` |
| Answers | 1 hour, and only until a new run is saved | `--answers` |
| Brief AI summaries | the brief's figures are unchanged (up to 7 days) | `--answers` |
| Installed plugin columns | 24 hours | everything (no flag) |

Results are scoped to your Steampipe connection, regions and AWS identity (`AWS_PROFILE` and the credential variables). A different account or role never sees another one's results.

```bash
spendlens cache show                 # entries, size, age and expiry of each kind
spendlens cache show --entries       # also the 10 most recent entries: question or SQL, age, expiry
spendlens cache clear --queries      # forget live results; the next question queries AWS again
spendlens cache clear --answers      # forget answers and brief summaries; the model is asked again
spendlens cache clear                # everything
```

Both take `-c, --config` to read another `finops.yaml`. To skip the cache for one command and keep it for the rest, use `--fresh` instead:

```bash
spendlens ask --live --fresh "How many instances are running right now?"
spendlens brief --fresh -o brief.html
```

`--fresh` doesn't read the cache, but it still saves the new result, so the next normal call reuses it.

Only the first question of an `ask -i` session is cached, because follow-ups depend on the conversation.

### Tune it

```yaml
llm:
  query_wait_s: 30          # wait per call before the model waits again
  query_timeout_s: 300      # cancel a live query after this
  cache:
    enabled: true           # false: never read or write the cache
    path: null              # default: ai-cache.db next to the history database
    inventory_ttl_s: 900    # resources
    metrics_ttl_s: 300      # CloudWatch metrics, CloudTrail, logs
    cost_ttl_s: 21600       # Cost Explorer
    answer_ttl_s: 3600      # whole answers
    max_mb: 100             # oldest entries are removed beyond this
```

A query that touches several kinds of table uses the shortest of their TTLs. A TTL of `0` turns caching off for that kind. Override for one run with `--set llm.cache.enabled=false`, or in CI with `SPENDLENS__LLM__CACHE__ENABLED=false`.

### After upgrading the AWS plugin

The catalog is built from the plugin's source and docs. When Steampipe is running, the columns your installed plugin reports always decide what exists, so a newer plugin works straight away. To refresh the descriptions, filters and examples too, rebuild the catalog from a clone of `turbot/steampipe-plugin-aws`:

```bash
make catalog PLUGIN_SRC=../steampipe-plugin-aws
```

## Safety: three read-only layers

1. **SQL guard.** The model's SQL is parsed with sqlglot. Only one read-only `SELECT` is allowed. Rejected:
   - writes, DDL, `SELECT INTO`, `FOR UPDATE`, multiple statements and `SET`;
   - `DELETE` hidden in a CTE;
   - functions such as `pg_read_file` or `set_config`.
2. **History DB.** Opened `mode=ro` with `PRAGMA query_only` and a SQLite authorizer that denies anything except reads.
3. **Live Steampipe.** Sessions use `default_transaction_read_only`, a statement timeout and a row cap.

Rejected queries go back to the model as errors, and every call is recorded in the audit trail.

## Providers

Set the provider in `finops.yaml`. Claude uses the official Anthropic SDK; OpenAI-family providers use the official OpenAI SDK.

### Amazon Bedrock (default)

Uses the AWS role assumed in your terminal (`bedrock:InvokeModel`), with no API key. A region is required and never guessed.

```yaml
llm:
  provider: bedrock
  model: anthropic.claude-opus-5-5
  region: eu-west-1            # or export AWS_REGION
```

### Anthropic API

```yaml
llm:
  provider: anthropic
  model: claude-opus-5-5
  api_key_env: ANTHROPIC_API_KEY     # the NAME of the variable, never the key
```

### OpenAI

```yaml
llm:
  provider: openai
  model: <model>
  api_key_env: OPENAI_API_KEY
```

### Azure OpenAI

```yaml
llm:
  provider: azure
  model: <your deployment name>
  base_url: https://<resource>.openai.azure.com
  api_version: "2024-10-21"
  api_key_env: AZURE_OPENAI_API_KEY
```

### OpenAI-compatible (Ollama, vLLM, LM Studio …)

```yaml
llm:
  provider: openai_compatible
  model: llama3.1
  base_url: http://localhost:11434/v1
```

## Claude-specific behaviour

- **Effort:** `llm.effort` (default `medium`) controls thinking depth and token spend.
- **No temperature:** current Claude models reject sampling parameters, so `llm.temperature` applies to OpenAI-family providers only.
- **Refusal fallbacks are on by default.** If a request is declined on safety grounds, it's retried on a fallback model:
  - on the Anthropic API, server-side;
  - on Bedrock, through the SDK middleware (`llm.fallback_model`).
- **Prompt caching** is enabled for the system prompt.

## Check the setup

```bash
spendlens doctor --offline     # includes an "ai assistant" row; never calls the model
```
