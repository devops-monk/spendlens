# Multi-account and regions

By default spendlens queries one account, the one your credentials belong to, in **eu-west-1**. It does this through a Steampipe connection it manages, named `spendlens`.

## More regions

Set the regions in `finops.yaml`:

```yaml
aws:
  regions: [eu-west-1, eu-west-2]   # names, wildcards ("eu-*"), or ["*"] for every enabled region
```

spendlens rewrites its connection the next time it runs. More regions mean more API calls and slower checks, so list only the ones you use.

IAM, Route 53, CloudFront and other global services are answered once, whichever regions you set. CloudTrail records global service events in **us-east-1**, so add us-east-1 to `aws.regions` when you search IAM changes through LookupEvents. Alternatively, use a multi-region trail through CloudWatch Logs or Tailpipe.

## Several accounts: a Steampipe aggregator

Define one Steampipe connection per account and an aggregator over them, in `~/.steampipe/config/aws.spc`:

```hcl
connection "aws_dev" {
  plugin  = "aws"
  profile = "dev-readonly"
  regions = ["eu-west-1"]
}

connection "aws_prod" {
  plugin  = "aws"
  profile = "prod-readonly"
  regions = ["eu-west-1", "eu-west-2"]
}

connection "aws_all" {
  plugin      = "aws"
  type        = "aggregator"
  connections = ["aws_*"]
}
```

Then point spendlens at the aggregator:

```yaml
steampipe:
  connection: aws_all          # spendlens leaves your own connections alone
```

Every query now covers all the accounts, and each row has an `account_id`. The assistant is told which connection it is using, so you can ask "Which account has the most unattached volumes?" and it groups by `account_id`. Steampipe picks up `.spc` changes on its own; if a new connection doesn't appear, restart the service (`steampipe service restart`).

For one account at a time instead, keep one `finops.yaml` per account (`--config prod.yaml`), or pass `--profile` on any command.

## In CI

Give the pipeline a read-only role in each account, and either:
- build the aggregator into the CI image, or
- run one job per account with its own role.

See [CI/CD](../ci/).

## Ask the assistant about scope

The assistant knows the connection, regions and profile it can query. For a region or account outside them, it says so and names the setting to change (`aws.regions`, or `steampipe.connection`). It won't answer as if there were nothing there.
