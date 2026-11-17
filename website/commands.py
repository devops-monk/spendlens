"""Curated content for the command catalog page (docs/cli). Options, defaults and allowed values are generated from
the real CLI by scripts/build_site.py; the build fails if a recipe uses a command or option that does not exist."""

from __future__ import annotations

# (category, intro, [command paths]) in page order. Every CLI command must appear exactly once (checked at build).
CATEGORIES: list[tuple[str, str, list[str]]] = [
    ("Set up and configure", "Create your policy file, check the setup, and see exactly which settings are in effect.",
     ["init", "doctor", "params", "config", "plugins", "query"]),
    ("Checks: tags, rightsizing and waste",
     "Run your company standards against every resource. Each result is a finding (alarm, ok, info, skip, error) "
     "with a reason, the resource, account, region and, where it applies, a monthly saving estimate.",
     ["tags", "tags types", "rightsize", "rightsize controls", "waste", "waste controls"]),
    ("Cost analysis", "What changed, where, and who owns it, from Cost Explorer. Each period is one bounded request "
     "($0.01 each, billed by AWS).",
     ["cost diff", "cost trend", "cost showback", "cost resources"]),
    ("Anomalies and forecast", "Spot unusual daily spend and see where the month will land against your budgets.",
     ["anomalies", "forecast"]),
    ("Savings", "AWS's own recommendations, commitment coverage, and one de-duplicated savings total.",
     ["savings hub", "savings commitments", "savings summary"]),
    ("Reports, dashboard and notifications", "Run everything at once, build the branded dashboard, post summaries.",
     ["report", "dashboard", "notify"]),
    ("AI assistant", "Ask questions in plain English; answers come from your saved runs (and, with --live, read-only "
     "Steampipe queries).", ["ask"]),
    ("History", "Every live run is saved locally, so you can compare runs and re-render reports without AWS calls.",
     ["history list", "history diff", "history path"]),
]

# Task index at the top of the page: (I want to..., command path).
TASKS: list[tuple[str, str]] = [
    ("Try spendlens without an AWS account", "report"),
    ("Check my setup and AWS access", "doctor"),
    ("Find resources missing mandatory tags", "tags"),
    ("Find oversized or non-standard resources", "rightsize"),
    ("Find idle and orphaned resources I'm paying for", "waste"),
    ("See what changed in my bill since last month", "cost diff"),
    ("See which resources or apps changed inside each service", "cost diff"),
    ("See spend per team, environment or cost centre", "cost showback"),
    ("See a multi-month trend", "cost trend"),
    ("Find which resources' cost changed this week", "cost resources"),
    ("Catch unusual daily spikes", "anomalies"),
    ("Know if we'll go over budget this month", "forecast"),
    ("Get one savings number I can defend", "savings summary"),
    ("Check Savings Plan and RI coverage", "savings commitments"),
    ("Produce one shareable HTML report", "dashboard"),
    ("Run everything on a schedule in CI", "init"),
    ("Post a summary to Slack or Teams", "notify"),
    ("Ask a question about my costs", "ask"),
    ("See what changed since the last run", "history diff"),
    ("See or change a threshold", "params"),
]

# Recipes per command: (what it does, command). Shown in a copyable block; validated against the CLI at build time.
RECIPES: dict[str, list[tuple[str, str]]] = {
    "init": [
        ("Write finops.yaml (your company policy) in this folder", "spendlens init"),
        ("Write a scheduled GitLab pipeline (.gitlab-ci.yml)", "spendlens init --ci gitlab"),
        ("Write a scheduled GitHub Actions workflow", "spendlens init --ci github"),
        ("Overwrite an existing file", "spendlens init --force"),
    ],
    "doctor": [
        ("Check tools, AWS identity, Steampipe, regions and Cost Explorer access", "spendlens doctor"),
        ("Local checks only: no network calls", "spendlens doctor --offline"),
        ("Check with a named AWS profile", "spendlens doctor --profile finops"),
    ],
    "params": [
        ("Every parameter: value, where it came from, and its environment variable", "spendlens params"),
        ("Only parameters matching a word", "spendlens params --filter ec2"),
        ("See the effect of an override before using it", "spendlens params --set sizing.ec2_cpu_low=10 --filter ec2_cpu"),
        ("Export all parameters", "spendlens params -f csv -o params.csv"),
    ],
    "config": [
        ("Show the merged configuration and the Powerpipe variables it produces", "spendlens config"),
        ("Preview an override", "spendlens config --set 'aws.regions=[\"eu-west-1\",\"eu-west-2\"]'"),
    ],
    "plugins": [("List loaded plugins and any that failed to load", "spendlens plugins")],
    "query": [
        ("Run your own read-only SQL against Steampipe", "spendlens query \"select name, region from aws_s3_bucket\""),
        ("…and save it as CSV", "spendlens query \"select name, region from aws_s3_bucket\" -f csv -o buckets.csv"),
        ("Cap the number of rows", "spendlens query \"select * from aws_ec2_instance\" --limit 50"),
    ],
    "tags": [
        ("All tag checks; alarms and errors only", "spendlens tags"),
        ("Compliance % per check and resource type", "spendlens tags --summary"),
        ("Only mandatory tags, only S3 buckets and EC2 instances",
         "spendlens tags -k mandatory -r s3_bucket -r ec2_instance"),
        ("Every result (incl. passing) as an interactive HTML report",
         "spendlens tags --status all -f html -o tags.html"),
        ("…or as CSV", "spendlens tags --status all -f csv -o tags.csv"),
        ("Fail a CI job (exit 3) on any alarm", "spendlens tags --fail-on-alarm"),
        ("Re-render your last run without calling AWS", "spendlens tags --source last -f html -o tags.html"),
        ("Try it on sample data", "spendlens tags --source demo --summary"),
    ],
    "tags types": [("List the 70 resource types tag checks cover", "spendlens tags types")],
    "rightsize": [
        ("All rightsizing and standards checks; alarms first, largest saving first", "spendlens rightsize"),
        ("Counts and savings per service", "spendlens rightsize --summary --by service"),
        ("Only RDS, ECS and CloudWatch; every result; HTML report",
         "spendlens rightsize -S rds -S ecs -S cloudwatch --status all -f html -o checks.html"),
        ("Only findings worth at least $50/month", "spendlens rightsize --min-saving 50"),
        ("One specific check", "spendlens rightsize -C rds_instance_low_utilization --status all"),
        ("Change a threshold for one run", "spendlens rightsize --set sizing.rds_cpu_low=20"),
        ("Fail a CI job (exit 3) on any alarm", "spendlens rightsize --fail-on-alarm"),
        ("Re-render the last run without calling AWS", "spendlens rightsize --source last -f csv -o rightsize.csv"),
    ],
    "rightsize controls": [("List every rightsizing check with its service", "spendlens rightsize controls")],
    "waste": [
        ("Idle and orphaned resources; alarms first", "spendlens waste"),
        ("Counts and savings per service", "spendlens waste --summary --by service"),
        ("Only unattached EBS volumes, as JSON", "spendlens waste -C ebs_volume_unattached -f json -o unattached.json"),
        ("Every result as an interactive HTML report", "spendlens waste --status all -f html -o waste.html"),
        ("Fail a CI job (exit 3) on any alarm", "spendlens waste --fail-on-alarm"),
    ],
    "waste controls": [("List every waste check with its service", "spendlens waste controls")],
    "cost diff": [
        ("Last full month vs the month before, by service", "spendlens cost diff"),
        ("This month to date vs the same days last month, by account", "spendlens cost diff --mtd -g account"),
        ("Two specific months, by region", "spendlens cost diff --from 2026-09 --to 2026-10 -g region"),
        ("Quarter vs quarter, by team tag", "spendlens cost diff --from 2026-07..2026-09 --to 2026-10..2026-12 -g tag:Team"),
        ("Inside each service: which tagged resources/apps went up or down",
         "spendlens cost diff -g service -b tag:Name -f html -o breakdown.html"),
        ("Inside each account: which services changed", "spendlens cost diff -g account -b service"),
        ("Only increases, top 10", "spendlens cost diff --show increases --top 10"),
        ("Amortized cost (spreads RI/SP upfront fees)", "spendlens cost diff --metric amortized"),
        ("Fail a CI job (exit 3) when a change crosses your thresholds", "spendlens cost diff --show flagged --fail-on-flag"),
        ("HTML report", "spendlens cost diff -f html -o cost-diff.html"),
    ],
    "cost trend": [
        ("Last 6 full months by service", "spendlens cost trend -n 6"),
        ("…including this month so far, top 10", "spendlens cost trend -n 6 --include-current --top 10"),
        ("Trend per account", "spendlens cost trend -g account"),
    ],
    "cost showback": [
        ("Spend per team and environment last month, with the untagged share", "spendlens cost showback -t Team -t Environment"),
        ("A specific month", "spendlens cost showback -t \"Service Owner\" --month 2026-10"),
        ("Month to date", "spendlens cost showback -t CostCentre --mtd -f html -o showback.html"),
    ],
    "cost resources": [
        ("Which resources' cost changed: last 7 days vs the 7 before", "spendlens cost resources --days 7"),
        ("Top 20 changes", "spendlens cost resources --days 7 --top 20"),
    ],
    "anomalies": [
        ("Daily spikes per service over the last week", "spendlens anomalies"),
        ("Per account, stricter threshold", "spendlens anomalies -g account --days 7 --threshold 4"),
        ("Also report sudden drops (e.g. a workload that stopped)", "spendlens anomalies --drops"),
        ("Fail a CI job (exit 3) on any anomaly", "spendlens anomalies --fail-on-anomaly"),
    ],
    "forecast": [
        ("Projected month end vs your budget and every AWS Budget", "spendlens forecast"),
        ("With a budget for this run", "spendlens forecast --set budgets.monthly_total=55000"),
        ("Fail a CI job (exit 3) if anything is forecast over budget", "spendlens forecast --fail-on-risk"),
    ],
    "savings hub": [
        ("AWS Cost Optimization Hub recommendations", "spendlens savings hub"),
        ("Only rightsizing and Graviton, at least $50/month",
         "spendlens savings hub --action Rightsize --action MigrateToGraviton --min-saving 50"),
    ],
    "savings commitments": [("Savings Plan and RI coverage per compute service, expiring plans",
                             "spendlens savings commitments")],
    "savings summary": [
        ("One de-duplicated total across rightsize, waste, hub and commitments", "spendlens savings summary"),
        ("Per service", "spendlens savings summary --by service"),
        ("The 50 largest individual items", "spendlens savings summary --by none --top 50"),
        ("HTML report", "spendlens savings summary -f html -o savings.html"),
    ],
    "report": [
        ("Run every check and build the dashboard in ./reports", "spendlens report --out reports"),
        ("Only some steps", "spendlens report --only tags --only waste --out reports"),
        ("Skip steps (e.g. no Cost Optimization Hub opt-in)", "spendlens report --skip savings_hub --out reports"),
        ("Everything on sample data: no AWS account needed", "spendlens report --source demo --out demo-report"),
        ("Longer timeout per step for large accounts", "spendlens report --out reports --step-timeout 3600"),
    ],
    "dashboard": [
        ("Branded HTML dashboard from your latest saved runs (no AWS calls)", "spendlens dashboard -o report.html"),
        ("The dashboard on sample data", "spendlens dashboard --source demo -o demo.html"),
    ],
    "notify": [
        ("Post a summary to Slack (webhook from $SPENDLENS_SLACK_WEBHOOK)",
         "spendlens notify --slack --link https://ci.example.com/report.html"),
        ("Post to Teams only when something needs attention", "spendlens notify --teams --only-if-changes"),
        ("Preview both messages without sending", "spendlens notify --slack --teams --dry-run --source demo"),
    ],
    "ask": [
        ("Ask a question (answers from your saved runs)", "spendlens ask \"What are my top 5 savings opportunities?\""),
        ("Show every query behind the answer", "spendlens ask \"Why did EC2 cost go up last month?\" --show-tools"),
        ("Allow read-only live AWS queries too", "spendlens ask --live \"Which running instances are outside the allowed families?\""),
        ("A conversation with follow-up questions", "spendlens ask -i"),
        ("On sample data", "spendlens ask --source demo \"Summarise the waste findings\""),
        ("Machine-readable answer and audit trail", "spendlens ask \"Top savings?\" --json"),
    ],
    "history list": [
        ("Every saved run", "spendlens history list"),
        ("Only tag runs, last 10", "spendlens history list --feature tags --limit 10"),
    ],
    "history diff": [
        ("New and resolved alarms between the last two tag runs", "spendlens history diff --feature tags"),
        ("Between two specific runs", "spendlens history diff --feature waste --from 12 --to 15"),
    ],
    "history path": [("Where the history database is (for CI caching)", "spendlens history path")],
}

# Options shared by many commands: documented once at the top, linked from each command.
COMMON_OPTIONS: dict[str, str] = {
    "--config": "Path to finops.yaml. Default: $SPENDLENS_CONFIG, ./finops.yaml, then ~/.config/spendlens/finops.yaml.",
    "--profile": "AWS profile for this run (sets AWS_PROFILE). Default: whatever is assumed in your terminal.",
    "--set": "Override any setting for this run, e.g. --set sizing.ec2_cpu_low=10. Repeatable; values are YAML.",
    "--var": "Pass a raw Powerpipe mod variable, e.g. --var my_variable=5 (plugins' own controls).",
    "--source": "live (query AWS and save the run), last (replay your latest saved run, no AWS calls) or demo "
                "(bundled sample data).",
    "--format": "table (terminal), csv, json or html.",
    "--out": "Write the output to a file instead of the terminal.",
    "--today": "Pretend today is this date (YYYY-MM-DD), for reproducible periods.",
}
