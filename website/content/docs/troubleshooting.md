# Troubleshooting

Start with `spendlens doctor`. It checks Steampipe, Powerpipe, the AWS plugin, your AWS identity, Cost Explorer access and Tailpipe, and prints the fix for anything that isn't right. `spendlens sources` shows where each kind of data is coming from.

## AWS access

| What you see | What to do |
|---|---|
| "the AWS SSO session has expired", "failed to refresh cached credentials" | `aws sso login --profile <profile>`, then run the command again |
| "Cannot connect to Steampipe … Is the service running?" | `steampipe service start`, after assuming your role: the service keeps the credentials it started with |
| AccessDenied on one table or check | The role lacks that read action; the error names it. See [IAM permissions](../permissions/). Other checks still run |
| Results are empty for resources you know exist | They are probably in a region spendlens doesn't query: set `aws.regions` ([Multi-account and regions](../multi-account/)) |
| Cost Explorer errors | The role needs `ce:GetCostAndUsage` (and `ce:GetCostAndUsageWithResources` for `cost resources`, plus resource-level data turned on in Cost Explorer) |

## The AI assistant

| What you see | What to do |
|---|---|
| "Bedrock needs a region" | Set `llm.region` in `finops.yaml`, or export `AWS_REGION` |
| Bedrock 403, "not authorized to perform bedrock…:CreateInference" (or `InvokeModel`) | The role can't call Bedrock: ask for Bedrock inference access for the model, or use another provider (`llm.provider`) |
| "answered at the tool-step limit" | A broad question: narrow it, or raise `--max-steps` (default 20) |
| "only the first N of M rows fit" | Ask for a count or total instead (the assistant aggregates in SQL), or ask for the full list as a file: it uses `export_query` |
| An answer looks stale | Add `--fresh`: cached live results and answers are skipped |
| The data note about your provider | Data the tools read goes to a provider outside your AWS account. See [Data and security](../data-security/) and `llm.redact` |

## CloudTrail

| What you see | What to do |
|---|---|
| "still reading after 60s", partial | LookupEvents is slow for broad searches: give an exact `--event`, `--resource` or `--user`, raise `--max-wait`, or collect the trail's logs with Tailpipe |
| No IAM events | IAM is recorded in us-east-1: add it to `aws.regions`, or use a multi-region trail through Tailpipe or CloudWatch Logs |
| `--user alice` finds nothing | LookupEvents matches the exact user or session name: try the full name or the role |
| No S3 object events | Those are data events: they need a trail with data event selectors, and come only from Tailpipe or CloudWatch Logs |

More on the [CloudTrail activity](../cloudtrail/#when-something-goes-wrong) page.

## Tailpipe

| What you see | What to do |
|---|---|
| `tailpipe init`: "none found" | The trail may be an organisation trail in another account: list its bucket under `tailpipe.cloudtrail` with a profile that can read it |
| `tailpipe init`: "this AWS CLI has no `bcm-data-exports` command" | Upgrade the AWS CLI to 2.15 or later |
| `collect`: AccessDenied | The profile needs `s3:ListBucket` and `s3:GetObject` on the bucket (and `kms:Decrypt` for KMS-encrypted logs) |
| spendlens isn't using Tailpipe | It is used only when enabled, installed, configured and up to date for the window: check `spendlens sources` |

More on the [Tailpipe](../tailpipe/#when-something-goes-wrong) page.

## Install and Docker

| What you see | What to do |
|---|---|
| `spendlens: command not found` after installing | Open a new terminal, or add `~/.local/bin` to `PATH` |
| `docker pull` fails with 401 | Docker Desktop has stale Hub credentials: `docker logout`, then pull again (the image is public) |
| The demo works, but your account shows nothing | Run `spendlens doctor`: usually credentials, the Steampipe service or the region |

Still stuck? Run the command with `--help` for every option, or open an issue on [GitHub](https://github.com/devops-monk/spendlens/issues) with the output of `spendlens doctor`.
