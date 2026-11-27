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
| `cloudtrail events` / `summary`, `changes`, `ask` CloudTrail questions | `cloudtrail:LookupEvents`; `logs:FilterLogEvents` on the trail's log group when it logs to CloudWatch Logs |
| `cloudtrail trails` | `cloudtrail:DescribeTrails`, `cloudtrail:GetTrailStatus`, `cloudtrail:GetEventSelectors` |
| `tailpipe init` | `cloudtrail:DescribeTrails`, `ec2:DescribeFlowLogs`, `s3:GetBucketLogging`, `elasticloadbalancing:DescribeLoadBalancers` / `DescribeLoadBalancerAttributes`, `guardduty:ListDetectors` / `ListPublishingDestinations` / `DescribePublishingDestination`, `wafv2:ListWebACLs` / `GetLoggingConfiguration`, `bcm-data-exports:ListExports` / `GetExport`, `cur:DescribeReportDefinitions` (each is optional: a missing one is skipped with a note). Finding Data Exports needs AWS CLI 2.15 or later. |
| `tailpipe collect` | `s3:ListBucket` and `s3:GetObject` on the log and export buckets (and `kms:Decrypt` if they are KMS-encrypted); `logs:FilterLogEvents` on CloudWatch Logs groups used as a source |
| `unused buckets` / `load-balancers` | `s3:ListAllMyBuckets`, `s3:GetBucketLogging`, `elasticloadbalancing:Describe*` (activity comes from Tailpipe, locally) |
| `savings history`, `security detections` | None in AWS: they read what Tailpipe collected, locally |
| `security findings` / `image` / `lambda` / `instance` / `resource` / `summary` | `inspector2:ListFindings`, `ecr:DescribeImageScanFindings`, `securityhub:GetFindings`, `guardduty:ListDetectors` / `ListFindings` / `GetFindings`, `access-analyzer:ListAnalyzers` / `ListFindings`. A missing one is reported as unavailable, never as "no findings". The `SecurityAudit` managed policy covers them all |
| `security status`, `security gate` | The above, plus `inspector2:ListCoverage` / `ListCoverageStatistics`, `securityhub:DescribeHub`, `ecr:GetRegistryScanningConfiguration` |
| `graph` | `ReadOnlyAccess`, or the Describe/List actions for EC2, EBS, ELB, Auto Scaling, RDS, ECS and Lambda listed in [Resource graph](../graph/#permissions). A missing one skips that type. With `graph.owner_from_cloudtrail`: `cloudtrail:LookupEvents` |
| `cost diff/trend/showback`, `anomalies` | `ce:GetCostAndUsage` |
| `cost resources` | `ce:GetCostAndUsageWithResources`, plus resource-level data enabled in Cost Explorer settings |
| `forecast` | `ce:GetCostForecast`, `budgets:ViewBudget` (it falls back to a linear projection without the forecast permission) |
| `savings hub` | `cost-optimization-hub:ListRecommendations`, plus the account opted in to Cost Optimization Hub |
| `savings commitments` | `ce:GetCostAndUsage`, `savingsplans:DescribeSavingsPlans` |

Cost Explorer API calls are billed at $0.01 per request. Each `cost` or `anomalies` run makes one request per period, plus pagination.
