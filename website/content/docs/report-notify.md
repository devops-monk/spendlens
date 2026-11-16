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

Then it builds the savings summary and the dashboard. Output in `reports/`:

| File | Content |
|---|---|
| `<step>.json` | Each step's findings or cost data |
| `savings_summary.json` | De-duplicated savings across sources |
| `report.html` | The dashboard |
| `report-steps.json` | Each step's result, duration and last message |

Each step runs in its own process. If one step fails (for example, missing permission for Cost Optimization Hub), the others still run and every output that can be written is written. The command then exits 1. Alarms are findings, not failures (step exit code 3 counts as success).

## spendlens notify: Slack and Teams

```bash
export SPENDLENS_SLACK_WEBHOOK=https://hooks.slack.com/services/...
spendlens notify --slack --link https://ci.example.com/report.html
spendlens notify --teams --only-if-changes
spendlens notify --slack --teams --dry-run --source demo     # print the payloads, send nothing
```

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
