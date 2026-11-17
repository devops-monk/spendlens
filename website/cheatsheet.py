"""Cheat sheet content (/cheatsheet/). Every command is validated against the real CLI at build time.

kind: "offline" = no AWS calls · "aws" = read-only AWS API calls (Steampipe) · "ce" = Cost Explorer (~$0.01/request)
      · "ai" = sends the question to your AI provider (Bedrock by default; billed per token)
"""

from __future__ import annotations

# (section id, title, one-line intro, [(what it tells you, command, kind)])
SECTIONS: list[tuple[str, str, str, list[tuple[str, str, str]]]] = [
    ("start", "First run", "Set up, check access, and see the whole product on sample data.", [
        ("Create your policy file (finops.yaml)", "spendlens init", "offline"),
        ("Check tools, AWS identity, regions and Cost Explorer access", "spendlens doctor", "ce"),
        ("Check the local setup only", "spendlens doctor --offline", "offline"),
        ("Everything on sample data: no AWS account needed", "spendlens report --source demo --out demo-report",
         "offline"),
        ("See every threshold and where it comes from", "spendlens params", "offline"),
    ]),
    ("changed", "What changed in my bill?", "Compare months, ranges or month-to-date, and drill down.", [
        ("Last month vs the month before, by service", "spendlens cost diff", "ce"),
        ("This month so far vs the same days last month", "spendlens cost diff --mtd", "ce"),
        ("What inside each service changed (logs? storage? data transfer?)", "spendlens cost diff -g usage-type --top 15",
         "ce"),
        ("Per service: which usage types changed", "spendlens cost diff -g service -b usage-type", "ce"),
        ("Per service: which tagged resources or apps changed", "spendlens cost diff -g service -b tag:Name", "ce"),
        ("Which regions changed", "spendlens cost diff -g region", "ce"),
        ("Which accounts changed", "spendlens cost diff -g account", "ce"),
        ("Only the 10 biggest increases", "spendlens cost diff --show increases --top 10", "ce"),
        ("Two specific months", "spendlens cost diff --from 2026-09 --to 2026-10", "ce"),
        ("Quarter vs quarter", "spendlens cost diff --from 2026-07..2026-09 --to 2026-10..2026-12", "ce"),
        ("Flag every change of $10 or more, whatever the %", "spendlens cost diff --set cost.change_alarm_pct=0", "ce"),
        ("The charted HTML report", "spendlens cost diff -f html -o cost-diff.html", "ce"),
    ]),
    ("trends", "Trends and ownership", "Where spend is heading, and who it belongs to.", [
        ("Six-month trend per service, including this month", "spendlens cost trend -n 6 --include-current", "ce"),
        ("Trend per account", "spendlens cost trend -g account", "ce"),
        ("Spend per team and environment, with the untagged share",
         "spendlens cost showback -t Team -t Environment", "ce"),
        ("Spend per Name tag this month", "spendlens cost showback -t Name --mtd", "ce"),
        ("Which resources' cost changed this week (needs resource-level data)", "spendlens cost resources --days 7",
         "ce"),
    ]),
    ("spikes", "Spikes and budget", "Catch unusual days early; know where the month will land.", [
        ("Unusual daily spikes per service", "spendlens anomalies", "ce"),
        ("…per account, stricter", "spendlens anomalies -g account --days 7 --threshold 4", "ce"),
        ("Also sudden drops (a workload that stopped)", "spendlens anomalies --drops", "ce"),
        ("Projected month end vs every AWS Budget", "spendlens forecast", "ce"),
        ("…against a budget you choose", "spendlens forecast --set budgets.monthly_total=9000", "ce"),
    ]),
    ("waste", "Waste and rightsizing", "Resources you pay for that do nothing, or are bigger than they need to be.", [
        ("Idle and orphaned resources, per service", "spendlens waste --summary --by service", "aws"),
        ("Every waste finding as an interactive report", "spendlens waste --status all -f html -o waste.html", "aws"),
        ("Only unattached EBS volumes", "spendlens waste -C ebs_volume_unattached", "aws"),
        ("Rightsizing savings per service", "spendlens rightsize --summary --by service", "aws"),
        ("Only RDS and ECS", "spendlens rightsize -S rds -S ecs", "aws"),
        ("Only findings worth $50/month or more", "spendlens rightsize --min-saving 50", "aws"),
        ("Try a different CPU threshold for one run", "spendlens rightsize --set sizing.rds_cpu_low=20", "aws"),
        ("Every check you can run", "spendlens rightsize controls", "offline"),
    ]),
    ("tags", "Tags", "Without tags, spend can't be allocated to a team.", [
        ("Compliance per check and resource type", "spendlens tags --summary", "aws"),
        ("Only missing mandatory tags", "spendlens tags -k mandatory", "aws"),
        ("Only S3 buckets and EC2 instances", "spendlens tags -k mandatory -r s3_bucket -r ec2_instance", "aws"),
        ("The 70 resource types covered", "spendlens tags types", "offline"),
    ]),
    ("savings", "Savings", "One number you can defend, and where it comes from.", [
        ("One de-duplicated total from your saved runs", "spendlens savings summary", "offline"),
        ("…per service", "spendlens savings summary --by service", "offline"),
        ("Savings Plan and RI coverage", "spendlens savings commitments", "ce"),
        ("AWS Cost Optimization Hub recommendations", "spendlens savings hub --min-saving 50", "aws"),
    ]),
    ("share", "Reports and sharing", "Turn runs into something you can send.", [
        ("Everything you've run, on one branded page", "spendlens dashboard -o report.html", "offline"),
        ("Run every check and build the dashboard", "spendlens report --out reports", "ce"),
        ("Any result as CSV for a spreadsheet", "spendlens waste -f csv -o waste.csv", "aws"),
        ("Preview the Slack / Teams summary", "spendlens notify --slack --teams --dry-run --source demo", "offline"),
    ]),
    ("ci", "Gates for CI", "Exit code 3 when something needs attention, so a pipeline can fail or alert.", [
        ("Fail on any tag-policy violation", "spendlens tags --fail-on-alarm", "aws"),
        ("Fail when a cost change crosses your thresholds", "spendlens cost diff --show flagged --fail-on-flag", "ce"),
        ("Fail on a daily cost spike", "spendlens anomalies --fail-on-anomaly", "ce"),
        ("Fail if the month is forecast over budget", "spendlens forecast --fail-on-risk", "ce"),
        ("Write a scheduled GitLab pipeline", "spendlens init --ci gitlab", "offline"),
    ]),
    ("replay", "Replay and history", "Every live run is saved: re-render, compare and export without calling AWS.", [
        ("Re-render your last run in another format", "spendlens rightsize --source last -f csv -o rightsize.csv",
         "offline"),
        ("Every saved run", "spendlens history list", "offline"),
        ("New and resolved alarms since the previous run", "spendlens history diff --feature rightsize", "offline"),
    ]),
    ("ai", "Ask in plain English", "Answers from your saved runs; --live adds read-only AWS queries.", [
        ("Ask a question", "spendlens ask \"Why did CloudWatch cost go up?\"", "ai"),
        ("…and see every query behind the answer", "spendlens ask \"What are my top savings?\" --show-tools",
         "ai"),
        ("A conversation with follow-ups", "spendlens ask -i", "ai"),
    ]),
    ("scope", "Scope and settings", "Change what's checked without editing files.", [
        ("Query two regions for one run",
         "spendlens rightsize --set 'aws.regions=[\"eu-west-1\",\"eu-west-2\"]'", "aws"),
        ("Use a named AWS profile", "spendlens doctor --profile finops", "aws"),
        ("See the merged configuration", "spendlens config", "offline"),
    ]),
]

KINDS = {
    "offline": ("Offline", "No AWS calls"),
    "aws": ("Reads AWS", "Read-only AWS API calls through Steampipe"),
    "ce": ("Cost Explorer", "Cost Explorer API: about $0.01 per request, billed by AWS"),
    "ai": ("AI provider", "Sends the question and query results to your AI provider (Bedrock by default)"),
}

# Short labels for the sticky section bar.
NAV = {"start": "First run", "changed": "Bill changes", "trends": "Trends", "spikes": "Spikes & budget",
       "waste": "Waste", "tags": "Tags", "savings": "Savings", "share": "Reports", "ci": "CI gates",
       "replay": "History", "ai": "AI", "scope": "Settings"}
