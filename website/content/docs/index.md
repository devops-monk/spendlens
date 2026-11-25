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

spendlens uses [Steampipe](https://steampipe.io) to expose AWS APIs as SQL tables and [Powerpipe](https://powerpipe.io) to run its bundled controls. Your standards live in one `finops.yaml`. Every run is saved to a local SQLite history, so "what changed since last week" and the AI assistant work offline from data already collected, and dashboards and notifications reuse anything already checked today. See [Architecture](../architecture/) for the diagrams.

## Principles

- **Read-only.** spendlens never creates, modifies, tags or deletes resources. Savings are proposals for a human to review.
- **Your credentials, nothing stored.** It uses whatever AWS role your terminal or CI has already assumed.
- **Your standards.** Every threshold is a parameter, set in `finops.yaml`, through environment variables, or with `--set` on any command.
- **Try before you connect.** Every command accepts `--source demo` and runs against bundled sample data.

## Present it to your team

A 13-slide PowerPoint deck introduces spendlens to colleagues and proposes a read-only pilot:
- the problem, what spendlens checks, the resource graph, suggested owners and data transfer;
- the AI assistant, the review page and how it stays read-only;
- how it runs, and a three-step rollout.

Every slide has speaker notes. Before presenting, fill in the bracketed placeholders (presenter, account, dates, channel, teams). The dollar figures in it come from the bundled sample data and are labelled that way.

[Download the deck (.pptx)](https://github.com/devops-monk/spendlens/raw/main/docs/ppt/spendlens%20FinOps%20for%20our%20AWS%20estate.pptx)

Next: [Getting started](getting-started/).
