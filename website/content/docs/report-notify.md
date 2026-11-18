# Reports & notifications

## spendlens report: everything in one command

```bash
spendlens report --out reports
spendlens report --skip savings_hub --skip cost_showback
spendlens report --only tags --only waste
spendlens report --source demo --out demo-report
```

Runs these steps:
- tags, rightsize, waste;
- cost diff, cost trend, cost showback;
- anomalies, forecast;
- savings hub, savings commitments.

Then it builds the savings summary, the dashboard, the executive brief and the full review. Output in `reports/`:

| File | Content |
|---|---|
| `<step>.json` | Each step's findings or cost data |
| `savings_summary.json` | De-duplicated savings across sources |
| `report.html` | The dashboard |
| `brief.html` | The [executive brief](../dashboard/#spendlens-brief-one-page-for-managers) for managers: rule-based summary, or AI-written with `spendlens report --ai` |
| `review.html` | The [full review](../dashboard/#spendlens-review-the-full-review): spend, anomalies, savings and a queue to approve, defer or reject each one (same summary as the brief) |
| `report-steps.json` | Each step's result, duration and last message |

Each step runs in its own process. If one step fails (for example, missing permission for Cost Optimization Hub), the others still run and every output that can be written is written. The command then exits 1. Alarms are findings, not failures (step exit code 3 counts as success).

## spendlens notify: Slack and Teams

```bash
export SPENDLENS_SLACK_WEBHOOK=https://hooks.slack.com/services/...
spendlens notify --slack --link https://ci.example.com/report.html
spendlens notify --teams --only-if-changes
spendlens notify --slack --teams --dry-run --source demo     # print the payloads, send nothing
spendlens notify --teams --refresh                           # re-check everything now, then post
spendlens notify --slack --source last                       # only saved runs (e.g. right after spendlens report)
```

**The message always covers every check, and is at most a day old:**
- A check already run **today** (UTC) is read from your history: no AWS calls for it.
- Every other check is **run live first**: read-only AWS calls, plus Cost Explorer (about $0.01 per request) for the cost checks. Each run is saved, so the dashboard and savings summary reuse it later today.
- `--refresh` re-runs every check now, e.g. after a deployment or clean-up.
- `--source last` never calls AWS. The generated CI pipelines use it, because `spendlens report` has just run every check.

If a check can't run (e.g. a missing permission), the output says why and the message uses its last saved run.

The summary covers:
- spend and its change;
- flagged cost changes;
- month-end forecast vs budget;
- anomalies;
- potential savings, with the top three items;
- tag compliance;
- new and resolved alarms since the previous run.

Formats:
- **Slack:** Block Kit, via an incoming webhook.
- **Teams:** an Adaptive Card, via a Workflows webhook ("Post to a channel when a webhook request is received").

| Option | Meaning |
|---|---|
| `--slack`, `--teams` | Targets (at least one) |
| `--only-if-changes` | Post only when something needs attention: a flagged change, an anomaly, a forecast over budget, or new alarms |
| `--link` | URL for the "Open report" button |
| `--dry-run` | Print the payloads instead of sending |
| `--source` | `last` (default) or `demo` |

Webhook security:
- Webhook URLs come only from the environment variables named in `notifications.slack_webhook_env` and `notifications.teams_webhook_env`, never from `finops.yaml`.
- They must use https, and URLs with embedded credentials are refused.
- They're redacted in logs.
