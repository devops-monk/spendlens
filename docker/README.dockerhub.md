# spendlens: FinOps for AWS, from your terminal

**spendlens** finds untagged resources, oversized instances, idle waste and unexpected cost spikes in your AWS accounts, puts a dollar figure on each one, and answers your questions in plain English. **It is read-only by design**: it never creates, changes, tags or deletes anything.

This image is ready to run. It includes spendlens with its AI assistant, plus pinned versions of [Steampipe](https://steampipe.io) 2.5.0, [Powerpipe](https://powerpipe.io) 1.6.0 and the Steampipe AWS plugin 1.35.0. Steampipe's database is set up when the image is built, so a container starts without downloading anything.

📖 **Docs:** https://spendlens.devops-monk.com · **Architecture:** https://spendlens.devops-monk.com/architecture/ · **Source:** https://github.com/devops-monk/spendlens

---

## Quick start: try it without AWS

Every command can run on bundled sample data. This needs no credentials and no network:

```bash
docker run --rm --network none abhaypratap/spendlens tags --source demo --summary
docker run --rm --network none abhaypratap/spendlens waste --source demo --summary --by service
docker run --rm --network none -v "$PWD:/work" abhaypratap/spendlens report --source demo --out demo-report
# open demo-report/report.html
```

## Run against your AWS account

The container uses the standard AWS credential chain. **Credentials are never baked into the image**; pass them in at run time. Use a read-only role (the [IAM permissions](https://spendlens.devops-monk.com/docs/permissions/) page lists exactly what's needed).

```bash
# a profile / SSO session from your machine (mounted read-only)
docker run --rm \
  -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE=finops -e AWS_REGION=eu-west-1 \
  -v "$PWD:/work" -v spendlens-data:/data \
  abhaypratap/spendlens report --out reports

# or the role already assumed in your terminal
docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_REGION \
  -v "$PWD:/work" -v spendlens-data:/data abhaypratap/spendlens cost diff --mtd
```

To use it like a local command:

```bash
alias spendlens='docker run --rm -it -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  -v "$PWD:/work" -v spendlens-data:/data abhaypratap/spendlens'
spendlens tags --summary
```

---

## What you get

### Tag compliance: 70 resource types, 5 checks
Mandatory, untagged, prohibited, tag-limit and allowed-value checks, so every dollar can be allocated to a team.

![spendlens tags](https://spendlens.devops-monk.com/static/screens/cli-tags.png)

### Waste detection, priced
Unattached volumes, orphaned snapshots, idle load balancers and NAT gateways, unused IPs and secrets: things you pay for that do nothing.

![spendlens waste](https://spendlens.devops-monk.com/static/screens/cli-waste.png)

### Rightsizing & company standards: 41 controls
Allowed instance families, gp2 → gp3, previous generations, Graviton candidates, Multi-AZ only in prod, versions in paid Extended Support.

![spendlens rightsize](https://spendlens.devops-monk.com/static/screens/cli-rightsize.png)

### Cost diff: what changed, and where
Any two months or quarters, grouped by service, account, region or tag. New and removed services are always shown; big changes are flagged.

![spendlens cost diff](https://spendlens.devops-monk.com/static/screens/cli-cost-diff.png)

### Anomalies & month-end forecast
Daily spikes measured against a robust baseline, and the projected month-end spend checked against your budget and every AWS Budget.

![spendlens anomalies](https://spendlens.devops-monk.com/static/screens/cli-anomalies.png)

![spendlens forecast](https://spendlens.devops-monk.com/static/screens/cli-forecast.png)

### One savings number, no double counting
AWS Cost Optimization Hub, Savings Plan and RI coverage, and spendlens's own findings merged into one total. Each resource is counted once.

![spendlens savings summary](https://spendlens.devops-monk.com/static/screens/cli-savings.png)

### Self-contained HTML dashboard
`spendlens dashboard` or `spendlens report` writes one HTML file with nine tabs. It works offline, as a CI artifact, and printed.

![Dashboard overview](https://spendlens.devops-monk.com/static/screens/dashboard-overview.png)

![Dashboard cost diff and trend](https://spendlens.devops-monk.com/static/screens/dashboard-cost.png)

![Dashboard savings](https://spendlens.devops-monk.com/static/screens/dashboard-savings.png)

### And more
- **`spendlens ask "why did EC2 go up?"`:** an AI assistant that answers from your data (Amazon Bedrock with your AWS role, Anthropic, OpenAI, Azure OpenAI or any OpenAI-compatible model). Its SQL is checked by three read-only safety layers.
- **`spendlens notify --slack --teams`:** a summary of what changed, posted to your channel.
- **`spendlens init --ci gitlab|github`:** a scheduled pipeline on a read-only OIDC role.
- **Table, CSV, JSON or HTML** output from every command, and every threshold set from one `finops.yaml`.

*Screenshots use the bundled demo data (`--source demo`).*

---

## Image reference

| | |
|---|---|
| **Tags** | `latest`, `0.1.0` |
| **Platforms** | `linux/arm64` (Graviton, Apple silicon); `linux/amd64` is added by the release pipeline |
| **User** | `finops` (uid 10001). Steampipe refuses to run as root |
| **Working directory** | `/work`: put `finops.yaml` here; reports are written here |
| **Volume** | `/data`: the run history (`/data/spendlens.db`), which keeps trends and "what changed" between runs |
| **Entrypoint** | Starts the local Steampipe service only when a command needs live data, then stops it. Exit codes pass through (`--fail-on-alarm` → 3, for CI gates) |

| Environment variable | Purpose |
|---|---|
| `AWS_PROFILE`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`, `AWS_ROLE_ARN` + `AWS_WEB_IDENTITY_TOKEN_FILE` | Standard AWS credentials (read-only role recommended) |
| `SPENDLENS_STEAMPIPE_SERVICE` | `auto` (default), `on` or `off` (use an external Steampipe) |
| `SPENDLENS__<SECTION>__<KEY>` | Override any setting, e.g. `SPENDLENS__SIZING__EC2_CPU_LOW=10` |
| `SPENDLENS_SLACK_WEBHOOK`, `SPENDLENS_TEAMS_WEBHOOK` | Webhooks for `spendlens notify` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY` | Only if `ask` uses those providers. Bedrock uses your AWS role |

If you bind-mount `/work` or `/data` on Linux, they must be writable by uid 10001.

## Use in CI

```yaml
# GitLab
spendlens:
  image: { name: abhaypratap/spendlens:0.1.0, entrypoint: [""] }
  script: [ "spendlens-entrypoint report --out reports" ]
  artifacts: { paths: [reports/] }
```

The CI guide covers OIDC role assumption, keeping history between runs, and Slack and Teams summaries: https://spendlens.devops-monk.com/docs/ci/

## Prefer a local install?

```bash
curl -fsSL https://spendlens.devops-monk.com/install.sh | sh
```