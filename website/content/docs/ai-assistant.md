# AI assistant

`spendlens ask` answers questions about your spend in plain English, using your data, with any major LLM provider. It's read-only: it can look things up, never change them.

```bash
spendlens ask "What are my top 5 savings opportunities?"
spendlens ask "Why did EC2 cost go up last month?" --show-tools
spendlens ask "Which teams own the most untagged spend?"
spendlens ask --live "Which running instances are outside the allowed families?"
spendlens ask -i                                       # follow-up questions in one session
spendlens ask --source demo "Summarise the waste findings"
```

| Option | Meaning |
|---|---|
| `-i, --interactive` | Keep asking follow-up questions in one conversation |
| `--source` | History to answer from: `last` (yours, default) or `demo` |
| `--live` | Also allow read-only live AWS queries through Steampipe |
| `--show-tools` | Show every tool call and the SQL behind the answer |
| `--max-steps` | Tool-call limit per question (default `llm.max_steps`, 12) |
| `--json` | The answer plus the full audit trail, machine-readable |
| `--fresh` | Ignore cached answers and live results; ask and query again |

## The executive brief

`spendlens brief` uses the same FinOps analyst profile to write a one-page summary for managers: a headline, key insights, recommended next steps, risks, and a likely cause for each anomaly. spendlens computes every figure first; the AI only writes the words, using the same read-only history tools (never live AWS). See [spendlens brief](../dashboard/#spendlens-brief-one-page-for-managers).

```bash
spendlens brief -o brief.html
```

## How it answers

1. It reads `history_overview` to see which saved runs exist.
2. It queries the history database with `history_query`. This is offline and free.
3. Only with `--live`, and only when history can't answer, it queries AWS live through Steampipe (below).
4. It answers with the key numbers, the evidence, and the run each number came from. Savings are labelled as estimates with their source.

Text in tool results (resource names, tags) is treated as data, never as instructions.

## Live AWS questions: right first time

With `--live` the assistant can answer from any of the 589 tables of the Steampipe AWS plugin: inventory, security groups, IAM, Lambda runtimes, CloudWatch metrics, Cost Explorer and more. Four layers make sure it writes a correct query, waits for slow ones and doesn't ask AWS twice:

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

### The same tools, for you

These work offline from the bundled catalog: no AWS calls and no Steampipe needed.

```bash
spendlens schema find "unattached EBS volumes"     # which tables answer this?
spendlens schema show aws_cost_usage              # columns, required filters, examples
spendlens schema check "select volume_id, size_gb from aws_ebs_volume"   # exit 1: size_gb → did you mean size?
spendlens cache show --entries                    # what's cached, how old, when it expires
spendlens cache clear --queries                   # forget cached live results
```

Tune freshness in `finops.yaml`:

```yaml
llm:
  query_wait_s: 30          # wait per call before the model waits again
  query_timeout_s: 300      # cancel a live query after this
  cache:
    enabled: true
    inventory_ttl_s: 900    # resources
    metrics_ttl_s: 300      # CloudWatch metrics, CloudTrail, logs
    cost_ttl_s: 21600       # Cost Explorer
    answer_ttl_s: 3600      # whole answers
```

After upgrading the AWS plugin, rebuild the catalog from a clone of `turbot/steampipe-plugin-aws`: `make catalog PLUGIN_SRC=../steampipe-plugin-aws`.

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
