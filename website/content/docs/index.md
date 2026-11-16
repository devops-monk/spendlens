# Introduction

spendlens is a read-only FinOps command line for AWS. It checks your estate against your company's standards, finds waste and puts a price on it, explains how your bill is changing, warns you about spikes and budget overruns, and answers questions in plain English.

## What it does

| Area | Command | What you get |
|---|---|---|
| Tag compliance | `spendlens tags` | Mandatory, untagged, prohibited, tag-limit and allowed-value checks across 70 resource types |
| Rightsizing & standards | `spendlens rightsize` | Oversized or non-standard resources (instance families, gp2→gp3, Multi-AZ by environment, …) with a $ estimate |
| Waste | `spendlens waste` | Unattached volumes, orphaned snapshots, idle load balancers and NAT gateways, unused EIPs and secrets, … |
| Cost analysis | `spendlens cost diff / trend / showback / resources` | What changed between any two months or ranges, by service, account, region or tag |
| Anomalies & forecast | `spendlens anomalies`, `spendlens forecast` | Daily spikes against a robust baseline; month-end projection against your budgets |
| Savings | `spendlens savings hub / commitments / summary` | AWS Cost Optimization Hub recommendations, Savings Plan and RI coverage, and one de-duplicated total |
| Reports | `spendlens dashboard`, `--format html` | A self-contained HTML dashboard, plus table, CSV, JSON or HTML output from every command |
| AI assistant | `spendlens ask` | Questions answered from your data by Claude, OpenAI, Azure or any OpenAI-compatible model |
| Automation | `spendlens report`, `spendlens notify`, `spendlens init --ci` | One-command runs, Slack and Teams summaries, scheduled GitLab and GitHub pipelines |

## How it works, in one paragraph

spendlens uses [Steampipe](https://steampipe.io) to expose AWS APIs as SQL tables and [Powerpipe](https://powerpipe.io) to run its bundled controls. Your standards live in one `finops.yaml`. Every run is saved to a local SQLite history, so dashboards, notifications, "what changed since last week" and the AI assistant work offline from data already collected. See [Architecture](../architecture/) for the diagrams.

## Principles

- **Read-only.** spendlens never creates, modifies, tags or deletes resources. Savings are proposals for a human to review.
- **Your credentials, nothing stored.** It uses whatever AWS role your terminal or CI has already assumed.
- **Your standards.** Every threshold is a parameter, set in `finops.yaml`, through environment variables, or with `--set` on any command.
- **Try before you connect.** Every command accepts `--source demo` and runs against bundled sample data.

Next: [Getting started](getting-started/).
