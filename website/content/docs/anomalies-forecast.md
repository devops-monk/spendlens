# Anomalies & forecast

## spendlens anomalies

Finds days when a service's (or account's, or tag's) spend jumped well above its normal level.

```bash
spendlens anomalies
spendlens anomalies -g account --days 7 --threshold 4
spendlens anomalies --drops                   # also sudden decreases, e.g. a workload that stopped
spendlens anomalies --fail-on-anomaly         # exit 3 for alerting pipelines
```

**How it decides.** For each recent day it takes the trailing `anomaly.lookback_days` (default 30) as a baseline and computes a *robust* z-score from the median and median absolute deviation. Robust statistics aren't dragged around by earlier spikes the way a mean and standard deviation are.

A day is an anomaly only when all three hold:
- the z-score is at least `anomaly.threshold` (default 3);
- the increase is at least `anomaly.min_increase` per day (default 10), so tiny services don't trigger alerts;
- the day's cost is at least `anomaly.min_daily_cost`.

Services with too little history (brand new ones) are skipped. `cost diff` reports them as `new`.

Severity comes from the z-score: low, medium (≥ 4), high (≥ 6) and critical (≥ 10).

## spendlens forecast

Projects month-end spend and checks it against your budgets.

```bash
spendlens forecast
spendlens forecast --set budgets.monthly_total=55000 --fail-on-risk
```

- **Projection.** Spend so far this month plus Cost Explorer's forecast for the remaining days. When the forecast API can't answer (for example with too little history), it falls back to a linear run-rate projection and says so.
- **Your budget.** The projection is checked against `budgets.monthly_total`.
- **AWS Budgets.** Every monthly cost budget in AWS Budgets is listed, using AWS's own forecasted spend for it.
- **Statuses:**
  - `over budget`
  - `forecast over budget`
  - `at risk` (above `budgets.warn_pct`, default 80%)
  - `on track`
- **`--fail-on-risk`** exits with code 3 if anything is forecast over its limit.
