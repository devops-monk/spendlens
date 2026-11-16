# Cost analysis

`spendlens cost` answers "what changed, where, and who owns it" from Cost Explorer, through Steampipe.

## cost diff: what changed

```bash
spendlens cost diff                                   # last full month vs the month before, by service
spendlens cost diff --mtd -g account                  # month to date vs the same days last month
spendlens cost diff --from 2026-09 --to 2026-10 -g region
spendlens cost diff --from 2026-07..2026-09 --to 2026-10..2026-12 -g tag:Team   # quarter vs quarter
spendlens cost diff --show flagged --fail-on-flag     # CI gate on big changes
```

Each row shows the base and compare amounts, the change and change %, and a status:
- `increase` or `decrease`;
- `new` (a service you didn't use before);
- `gone` (a service you've stopped using);
- `flat`.

New and removed services are always included.

A change is **flagged** when it crosses both thresholds:
- `cost.change_alarm_abs` (default 10);
- `cost.change_alarm_pct` (default 20%).

New and removed groups only need the absolute threshold. When the two periods differ in length (30 vs 31 days), JSON and CSV output also include a daily-rate change.

| Option | Meaning |
|---|---|
| `--from`, `--to` | `YYYY-MM` or `YYYY-MM..YYYY-MM` (inclusive month range) |
| `--mtd` | Current month to date vs the same days of last month |
| `-g, --group-by` | `service`, `account`, `region`, `usage-type`, `instance-type`, `operation`, `record-type`, `purchase-type` or `tag:<Key>` |
| `--metric` | `net_unblended` (default), `unblended`, `amortized`, `net_amortized`, `blended` |
| `--include-credits` | Keep credit and refund record types (excluded by default) |
| `--show` | `changed` (default), `flagged`, `increases`, `decreases` or `all` |
| `--top`, `--total/--no-total` | Limit rows; append a TOTAL row |

## cost trend: the last N months

```bash
spendlens cost trend -n 6 --include-current --top 10
```

A pivot with one row per service (or other group), one column per month, and the latest month-over-month change.

## cost showback: who owns the spend

```bash
spendlens cost showback -t Team -t Environment --month 2026-10
```

For each tag value: the amount, its share of the total, the **untagged** share, and the variance against `budgets.per_tag`. Untagged spend is reported explicitly, because it's the spend nobody owns.

## cost resources: which resources changed

```bash
spendlens cost resources --days 7 --top 20
```

Compares the last N days with the N days before, per resource.
- Requires *resource-level data* enabled in Cost Explorer settings.
- AWS keeps only 14 days of it, which is why N is at most 7.

## Notes

- Each period is one bounded Cost Explorer call, billed at $0.01 per request.
- Tag grouping can't exclude credits (a Cost Explorer limitation); spendlens says so when it applies.
- Cost Explorer data lags about 24 hours, and the current month is an estimate.
