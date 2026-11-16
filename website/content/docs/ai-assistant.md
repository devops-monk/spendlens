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

## How it answers

1. It reads `history_overview` to see which saved runs exist.
2. It queries the history database with `history_query`. This is offline and free.
3. Only with `--live`, and only when history can't answer, it lists Steampipe tables and runs a narrow live query.
4. It answers with the key numbers, the evidence, and the run each number came from. Savings are labelled as estimates with their source.

Text in tool results (resource names, tags) is treated as data, never as instructions.

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
