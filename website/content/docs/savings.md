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
spendlens savings summary --by service
spendlens savings summary --by none --top 50
```

Merges the latest `rightsize`, `waste`, `savings hub` and `savings commitments` runs:
- **One action per resource.** When several actions apply to the same resource, only the largest saving counts (downsizing and reserving the same instance aren't additive).
- **AWS wins on overlap.** When AWS Cost Optimization Hub and spendlens both estimate the same resource, AWS's figure is used.
- **Commitments count once.** AWS purchase recommendations replace spendlens's coverage-gap estimate, and per-instance reservation candidates fold into the service-level number.

Each adjustment is printed, so the total is explainable. `savings summary` reads saved runs only and makes no AWS calls.
