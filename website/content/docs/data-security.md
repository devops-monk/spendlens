# Data and security

spendlens is read-only in AWS, and it keeps what it reads on your machine. The one exception is the AI assistant: when you use it, what its tools read goes to the AI provider you chose. This page says exactly what is read, where it goes, and what is stored.

## What spendlens reads

- **AWS APIs, read-only,** through the Steampipe AWS plugin: Describe, List, Get and Lookup calls, CloudWatch metrics and Cost Explorer. The permissions each command needs are on [IAM permissions](../permissions/). `ReadOnlyAccess` plus Cost Explorer read access covers everything.
- **S3 log files,** only if you set up Tailpipe: CloudTrail logs, VPC flow logs and the CUR, with `s3:GetObject`.
- **Files you attach** to `spendlens ask`, read as data and never run.

spendlens never creates, changes or deletes anything in AWS. Scripts it writes, such as tagging commands, are for a person to review and run.

## Where data goes

| Where | What | When |
|---|---|---|
| **Your machine** | Everything below | Always |
| **The AI provider** | Your questions, the tool results the assistant reads (query rows, CloudTrail events, file contents) and its answers | Only when you use `ask`, `session open` or `brief` (the AI summary; `brief --no-ai` sends nothing) |
| **Slack or Teams** | A summary of findings and savings | Only `spendlens notify`, to the webhooks you configure |

Your choice of AI provider decides where that data goes:

| Provider | Where the data goes |
|---|---|
| **Amazon Bedrock** | Stays in your AWS account and region, under your AWS agreements |
| **Anthropic, OpenAI, Azure OpenAI, an OpenAI-compatible service** | Leaves your AWS account for that provider, under its terms |

When account data is about to go to a provider outside your AWS account, `spendlens ask` prints a one-line data note. Turn it off with `llm.egress_notice: false`.

## Hide identifiers from the AI provider

`llm.redact` replaces account ids, email addresses and IP addresses with placeholders before anything reaches the provider:

```yaml
llm:
  redact: [account_ids, emails, ips]
```

- **The provider sees placeholders**, like `ACCOUNT_1`, `EMAIL_1` and `IP_1`, consistently within one conversation.
- **Queries still work.** The placeholders are mapped back to the real values before any query runs.
- **You see the real values.** The answer is mapped back too, so you see real ids, emails and addresses. Only the provider never receives them.
- **Some values stay visible.** Resource names, tags and ARNs other than their account id are not hidden.

## What is stored on your machine

| What | Where | Holds | Kept |
|---|---|---|---|
| History | `spendlens history path` | Findings, costs, the resource graph | The newest `history.keep_runs` per check (200) |
| AI cache | next to the history | Live query results and answers | Until they expire (minutes to hours) |
| Sessions | `sessions/` next to the history | Questions, answers, tool calls and the start of their results | The newest 100 automatic sessions; named ones until deleted ([Sessions](../sessions/)) |
| Files you ask for | `./spendlens-output` | Exports and documents | Until you delete them |
| Tailpipe data | `~/.tailpipe` | Collected CloudTrail logs, flow logs, CUR | Until you delete them |

History, cache and session files are readable by your user only (file mode 600, folder 700).

To remove everything:
```bash
spendlens session clear --yes
spendlens cache clear
rm "$(spendlens history path)"
```

## Credentials

spendlens uses the standard AWS credential chain (profiles, SSO, roles, environment variables) and never stores credentials. AI keys come from environment variables named in `finops.yaml` (`llm.api_key_env`), never from the file itself. In CI, use a read-only role through OIDC, with no stored keys (see [CI/CD](../ci/)).

## Guard rails on AI queries

- Only one read-only `SELECT` per query, run in a read-only transaction with a statement timeout and a row cap.
- Queries are checked before they run: tables, columns, required filters, CloudTrail rules, and time ranges for Cost Explorer.
- Tailpipe queries can't use table functions or read files or environment variables.
- Files the assistant writes never overwrite existing ones, and are never run.
