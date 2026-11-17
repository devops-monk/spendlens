# Savings

Three commands turn findings into a single, honest savings number.

## savings hub: AWS's own recommendations

```bash
spendlens savings hub
spendlens savings hub --action Rightsize --action MigrateToGraviton --min-saving 50
```

Reads [AWS Cost Optimization Hub](https://aws.amazon.com/aws-cost-management/cost-optimization-hub/): rightsizing, Graviton migration, stop or delete idle resources, and Savings Plan or RI purchases. Each comes with AWS's usage-based saving, the effort involved, and whether a restart is needed. The account must be opted in to Cost Optimization Hub.

## savings commitments: Savings Plan and RI coverage

```bash
spendlens savings commitments
```

For each compute service, using the last full month's amortized spend:
- how much ran on Savings Plans or RIs vs on-demand (spot is excluded, since it's already discounted);
- the coverage %;
- the estimated saving from reaching `savings.commitment_coverage_target_pct` (default 70%) at the `pricing.reservation_saving_pct` discount.

It also warns about active Savings Plans that end within `savings.savings_plan_expiration_warning_days`. Coverage here is a spend mix, not AWS's hour-based report.

## savings summary: one number, no double counting

```bash
spendlens savings summary                 # by source
spendlens savings summary --by service    # with the biggest item per service
spendlens savings summary --by none --top 50
spendlens savings summary --refresh       # re-check all four sources now
spendlens savings summary --source last   # saved runs only, no AWS calls
```

**Every total covers all four sources, and is at most a day old:**
- A source already checked **today** (UTC) is read from your history: no AWS calls for it.
- Any other source is **run live first**: read-only calls, plus Cost Explorer for commitments. It's saved, so the next summary today reuses it.
- `--refresh` re-checks all four now.
- `--source last` never calls AWS. It lists any source with no saved run, with the command to run it.

If a source can't run (e.g. Steampipe isn't started), the summary says so and uses that source's last saved run.

[[shot:savings|The savings summary in the dashboard: one de-duplicated total with its sources.]]

Merges the latest `rightsize`, `waste`, `savings hub` and `savings commitments` runs:
- **One action per resource.** When several actions apply to the same resource, only the largest saving counts (downsizing and reserving the same instance aren't additive).
- **AWS wins on overlap.** When AWS Cost Optimization Hub and spendlens both estimate the same resource, AWS's figure is used.
- **Commitments count once.** AWS purchase recommendations replace spendlens's coverage-gap estimate, and per-instance reservation candidates fold into the service-level number.

Each adjustment is printed, so the total is explainable, and the last line says which run each source came from ("run now" or "saved 2026-11-17").
