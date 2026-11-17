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

[[shot:cost|The biggest cost changes between two months, as shown in the dashboard.]]

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

## Inside each service: which resources or apps changed (`--breakdown`)

```bash
spendlens cost diff -g service -b tag:Name                     # per service: which Name values went up or down
spendlens cost diff -g service -b "tag:Service Owner" --mtd    # per service: which teams, month to date
spendlens cost diff -g account -b service --from 2026-07..2026-09 --to 2026-10..2026-12
spendlens cost diff -g service -b tag:Name -f html -o breakdown.html
```

`-b/--breakdown` adds a second level inside each group:
- It works with any `--group-by` value: service, account, region, usage type, or a tag.
- Results are ordered by each group's total change, then by each item's change within it.
- `--top N` keeps the N largest changes per group.

The HTML report contains:
- the total change, and the biggest single increase and decrease;
- a one-line overview per service;
- a section per service listing its items with before, after, change, change % and status (`increase`, `decrease`, `new`, `gone`);
- search across everything.

How it works:
- Steampipe's cost tables can't group by a service and a tag together, so this command calls Cost Explorer's read-only `GetCostAndUsage` API directly. It needs `ce:GetCostAndUsage`, which is already in the IAM policy.
- It makes one request per period, plus pagination. AWS charges $0.01 per request.
- Credits and refunds are excluded, as with `cost diff`.
- Each run is saved, so `--source last` replays it offline.

Tags need to be **activated as cost allocation tags** in the Billing console. Spend from before activation, and spend on resources without the tag, appears as `(untagged)`. A tag that's unique per resource, such as `Name`, gives a near per-resource view across any months, without the 14-day limit of `cost resources`.

## cost trend: the last N months

```bash
spendlens cost trend -n 6 --include-current --top 10
```

[[shot:trend|Monthly trend of the top services.]]

A pivot with one row per service (or other group), one column per month, and the latest month-over-month change.

## cost showback: who owns the spend

```bash
spendlens cost showback -t Team -t Environment --month 2026-10
```

[[shot:showback|Showback by Service Owner and Environment, with the untagged share called out.]]

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
