# IAM permissions

spendlens only reads. It never creates, modifies or deletes AWS resources. Recommended setup for the role you assume:

1. AWS managed policy **`ReadOnlyAccess`**. The Steampipe aws plugin uses it to inventory resources for `tags`, `rightsize` and `waste`.
2. These cost APIs, which `ReadOnlyAccess` does not fully cover:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SpendlensCost",
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage",
        "ce:GetCostAndUsageWithResources",
        "ce:GetCostForecast",
        "ce:GetDimensionValues",
        "ce:GetTags",
        "budgets:ViewBudget",
        "cost-optimization-hub:ListRecommendations",
        "cost-optimization-hub:GetRecommendation",
        "savingsplans:DescribeSavingsPlans"
      ],
      "Resource": "*"
    }
  ]
}
```

| Command | Needs |
|---|---|
| `tags`, `rightsize`, `waste` | `ReadOnlyAccess` (resource inventory + CloudWatch metrics) |
| `ask --live` CloudTrail questions | `cloudtrail:LookupEvents`; `logs:FilterLogEvents` for a trail that logs to CloudWatch Logs |
| `graph` | `ReadOnlyAccess`, or the Describe/List actions for EC2, EBS, ELB, Auto Scaling, RDS, ECS and Lambda listed in [Resource graph](../graph/#permissions). A missing one skips that type |
| `cost diff/trend/showback`, `anomalies` | `ce:GetCostAndUsage` |
| `cost resources` | `ce:GetCostAndUsageWithResources`, plus resource-level data enabled in Cost Explorer settings |
| `forecast` | `ce:GetCostForecast`, `budgets:ViewBudget` (it falls back to a linear projection without the forecast permission) |
| `savings hub` | `cost-optimization-hub:ListRecommendations`, plus the account opted in to Cost Optimization Hub |
| `savings commitments` | `ce:GetCostAndUsage`, `savingsplans:DescribeSavingsPlans` |

Cost Explorer API calls are billed at $0.01 per request. Each `cost` or `anomalies` run makes one request per period, plus pagination.
