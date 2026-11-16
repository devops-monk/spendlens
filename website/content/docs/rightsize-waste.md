# Rightsizing & waste

Two control sets with dollar estimates:
- **`rightsize`** (41 controls): resources that are oversized or not provisioned to your standards.
- **`waste`** (24 controls): resources that are billed but unused.

## What's checked

**Rightsizing and standards:**
- **EC2:** allowed instance families, previous generations, low CPU, Graviton candidates, reservation candidates, expiring RIs, delete-on-termination.
- **EBS:** gp2→gp3, io1→io2, provisioned-IOPS→gp3, oversized volumes, high IOPS, low usage.
- **RDS:** allowed classes, low CPU, reservation candidates, Graviton, versions in paid Extended Support, and Multi-AZ / Performance Insights / backup retention **by environment**.
- **ECS:** task count by environment (spread across AZs), task vCPU and memory limits, autoscaling, cluster CPU.
- **Lambda, CloudWatch Logs, DynamoDB, ElastiCache, EKS, EMR, Redshift, CloudFront, Route 53.**

**Waste:**
- Unattached EBS volumes, and volumes attached only to stopped instances.
- Old and orphaned snapshots and AMIs.
- Load balancers without targets, unattached Elastic IPs, NAT gateways without traffic.
- RDS instances with zero connections, and orphaned RDS snapshots.
- Unused secrets, idle EMR clusters, unused Route 53 health checks, redundant CloudTrail trails, disabled KMS keys.
- S3 buckets without lifecycle rules (or without noncurrent-version expiry), ECR repositories without lifecycle rules.

The full catalog, with parameters, is on the [controls](../controls/) page.

## Usage

```bash
spendlens rightsize                                   # alarms first, largest saving first
spendlens rightsize --summary --by service
spendlens rightsize -S rds -S ec2 --min-saving 50
spendlens rightsize --set sizing.ec2_cpu_low=10       # one-off threshold change
spendlens waste --summary --by service
spendlens waste -C ebs_volume_unattached -f json -o unattached.json
spendlens rightsize controls                          # list controls
```

| Option | Meaning |
|---|---|
| `-S, --service` | Limit to services (`ec2`, `rds`, `ebs`, …) |
| `-C, --control` | Limit to specific controls |
| `--status` | `alarm`, `ok`, `info`, `skip`, `error` or `all` |
| `--summary`, `--by` | Group counts and savings by `service`, `control`, `resource_type`, `account_id` or `region` |
| `--min-saving` | Only findings saving at least this much per month |
| `--fail-on-alarm` | Exit 3 if any alarm |

## How savings are estimated

Each control with an estimator prices its alarms from `pricing:` in `finops.yaml`. For example:
- **gp2→gp3:** size × (gp2 price − gp3 price).
- **Unattached volume:** the volume's full monthly cost.
- **Low-CPU instance:** `downsize_saving_pct` of the instance price.

The defaults are approximate us-east-1 list prices. Override them for your region or discounts:

```bash
spendlens waste --set 'pricing.ebs_gb_month={gp2: 0.116, gp3: 0.0928}'
```

Estimates are indicative. Use them to rank work, then review before acting. For AWS's own usage-based figures, see [Savings](../savings/).
