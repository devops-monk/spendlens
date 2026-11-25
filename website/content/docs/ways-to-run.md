# Ways to run

spendlens is one command line, but you can install it, feed it data and get results out in several ways. This page lists every way, so you can pick the one that fits your laptop, a server or a pipeline. For every command and option, see [Commands & options](../cli/).

## 1. Install it

### One-line installer (macOS, Linux)

For everyday use. It installs spendlens, pinned Steampipe and Powerpipe, and the AWS plugin, without sudo.

```bash
curl -fsSL https://spendlens.devops-monk.com/install.sh | sh
```

### Docker image (amd64, arm64)

For Windows, CI or servers, or when you want to install nothing locally. Everything is included.

```bash
docker pull abhaypratap/spendlens
```

### pipx

When you manage Steampipe and Powerpipe yourself.

```bash
pipx install "spendlens[ai] @ https://github.com/devops-monk/spendlens/archive/main.tar.gz"
```

### From source

For contributing; see [Development](../development/).

```bash
git clone https://github.com/devops-monk/spendlens.git && cd spendlens
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Installer options are environment variables set before `sh`:

| Variable | Effect |
|---|---|
| `SPENDLENS_DRY_RUN=1` | Print every step; change nothing |
| `SPENDLENS_REF=v0.4.8` | Install a tag or branch (default `main`) |
| `SPENDLENS_EXTRAS=""` | Skip the AI assistant's SDKs |
| `SPENDLENS_SKIP_ENGINES=1` | Don't install Steampipe, Powerpipe or the AWS plugin |
| `SPENDLENS_BIN_DIR=…` | Where binaries go (default `~/.local/bin`) |
| `SPENDLENS_PACKAGE=…` | Install spendlens from a wheel path or URL (e.g. an internal mirror) |

## 2. Run it

### On your machine, against your AWS account

```bash
steampipe service start        # once, after assuming your role (it keeps the credentials it started with)
spendlens doctor               # check tools, identity, regions and Cost Explorer access
spendlens rightsize --status all -f html -o checks.html
```

- **Credentials:** spendlens uses whatever is assumed in your terminal: environment variables, `AWS_PROFILE` or SSO. To pick a profile for one run, use `--profile name`; to set one permanently, use `aws.profile` in `finops.yaml`.
- **Region:** spendlens queries **eu-west-1** unless you change `aws.regions`.
- **Progress:** while a command runs, it shows the current step and an elapsed timer.

### With Docker

```bash
# a profile / SSO session from your machine, mounted read-only
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  -v "$PWD:/work" -v spendlens-data:/data abhaypratap/spendlens rightsize -f html -o checks.html

# the role already assumed in your terminal
docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  -v "$PWD:/work" -v spendlens-data:/data abhaypratap/spendlens cost diff
```

- **`/work`:** your folder. Put `finops.yaml` here; reports are written here.
- **`/data`:** the run history. Keep it on a volume to keep trends between runs.
- **Steampipe:** the container starts it only when a command needs live data. Set `SPENDLENS_STEAMPIPE_SERVICE` to `auto` (the default), `on` or `off`.
- **More:** see [Docker](../docker/) for an alias that makes the container behave like a local command.

### On a schedule in CI

```bash
spendlens init --ci gitlab     # or: --ci github
```

This writes a pipeline that:
- assumes a read-only role through OIDC, so no stored keys;
- runs `spendlens report`;
- keeps the run history between jobs (S3, or the CI cache);
- posts to Slack or Teams.

See [CI/CD](../ci/).

### As a Python module

```bash
python -m spendlens tags --summary
```

That's the same CLI, useful when the `spendlens` script isn't on your `PATH`.

## 3. Choose where the data comes from: `--source`

| Source | What it does | AWS calls |
|---|---|---|
| `live` (default) | Queries your account through Steampipe and Cost Explorer, and saves the run to your history. For `dashboard`, `notify` and `savings summary`: reuses checks run today, runs the rest | Yes |
| `last` | Replays your latest saved run of that command: re-render, re-filter or re-export instantly. For `dashboard`, `notify` and `savings summary`, uses saved runs only | None |
| `demo` | Uses bundled sample data (account `123456789012`) | None |

```bash
# live, and saved to your history
spendlens rightsize
# the same results in another format, no AWS calls
spendlens rightsize --source last -f csv -o r.csv
# the whole product on sample data
spendlens report --source demo --out demo-report
```

### Keep summaries fresh: `--refresh`

`dashboard`, `notify` and `savings summary` combine many checks. By default they reuse every check already run **today** (UTC) and run the others live first, so the result always covers everything and is at most a day old. A check run by one of them is reused by the others later the same day.

```bash
# Default: reuse today's checks, run the rest
spendlens dashboard -o report.html
spendlens notify --slack
spendlens savings summary

# Re-run everything now
spendlens dashboard --refresh -o report.html
spendlens notify --slack --refresh
spendlens savings summary --refresh

# Saved runs only, no AWS calls
spendlens dashboard --source last -o report.html
spendlens notify --slack --source last
spendlens savings summary --source last
```

Use `--refresh` after a deployment or clean-up, when this morning's results are out of date. Use `--source last` in CI right after `spendlens report`, or whenever you must not call AWS. `history …` and `ask` (without `--live`) always work from saved runs only.

## 4. Choose the output: `--format` and `--out`

| Format | You get |
|---|---|
| `table` (default) | A table in the terminal |
| `csv` | One row per result, for spreadsheets |
| `json` | `{meta, count, rows}`, for scripts and other tools |
| `html` | A self-contained page that works offline (open it, attach it, publish it as a CI artifact). Check commands produce the interactive findings report, and `cost diff -b` produces the per-service breakdown. |

```bash
spendlens waste -f html -o waste.html
spendlens waste -f json | jq '.rows[] | select(.est_monthly_saving > 100)'
```

`spendlens dashboard` and `spendlens report` combine everything into one branded HTML page.

## 5. Choose what to check

| To narrow it to… | Use |
|---|---|
| Services | `-S rds -S ecs` (rightsize, waste) |
| Specific checks | `-C rds_instance_low_utilization` (rightsize, waste) · `-k mandatory` (tags) |
| Resource types | `-r s3_bucket -r ec2_instance` (tags) |
| Statuses shown | `--status alarm` (default: alarms and errors) or `--status all` |
| Savings worth acting on | `--min-saving 50` |
| Regions | `aws.regions` in finops.yaml, `SPENDLENS__AWS__REGIONS='["eu-west-1"]'` or `--set 'aws.regions=["eu-west-2"]'` |
| A cost grouping | `-g service`, `-g account`, `-g region`, `-g tag:Team`, … and `-b` for a second level |
| Periods | `--from 2026-09 --to 2026-10`, ranges `2026-07..2026-09`, or `--mtd` |

## 6. Tune the thresholds

Every threshold has a default, and you can change it in four layers. Later layers win:

1. Built-in defaults.
2. `finops.yaml`, e.g. `sizing: {rds_cpu_low: 25}`.
3. Environment variables, e.g. `SPENDLENS__SIZING__RDS_CPU_LOW=20`. A double underscore separates the levels.
4. The command line, e.g. `--set sizing.rds_cpu_low=15`.

`spendlens params` lists every threshold with its current value, where that value came from, and its environment-variable name. Every check in an HTML report shows the exact thresholds it used. See [Configuration](../configuration/).

## 7. Use it in automation: exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Error (e.g. AWS access denied, Steampipe not running) |
| `2` | Invalid option or configuration |
| `3` | A gate found something: `--fail-on-alarm` (checks), `--fail-on-flag` (cost diff), `--fail-on-anomaly`, `--fail-on-risk` (forecast) |

```bash
spendlens tags --fail-on-alarm || echo "tag policy violations"
```

`spendlens report` treats code 3 from a step as a success with findings, and returns 1 only if a step failed.

## 8. Environment variables

| Variable | Purpose |
|---|---|
| `SPENDLENS_CONFIG` | Path to `finops.yaml` |
| `SPENDLENS__<SECTION>__<KEY>` | Override any setting, e.g. `SPENDLENS__AWS__REGIONS='["eu-west-1"]'` |
| `SPENDLENS__HISTORY__PATH` | Where the run history is kept (CI: restore and save this file between jobs) |
| `SPENDLENS_HISTORY_S3` | In the generated CI pipelines: the S3 location to restore and save the history |
| `SPENDLENS_SLACK_WEBHOOK`, `SPENDLENS_TEAMS_WEBHOOK` | Webhooks for `spendlens notify`, read only from the environment, never from config |
| `SPENDLENS_NO_PROGRESS=1` | Turn off the live progress display |
| `SPENDLENS_DISABLE_PLUGINS=name` | Skip a plugin, or `*` for all external plugins |
| `SPENDLENS_STEAMPIPE_SERVICE` | Docker image only: `auto`, `on` or `off` |
| `STEAMPIPE_DATABASE_PASSWORD` | The Steampipe password, if it can't be discovered automatically |
| `AWS_PROFILE`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, … | Standard AWS credentials, read by Steampipe and Cost Explorer calls |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY` | Only if `spendlens ask` uses those providers. Bedrock uses your AWS role. |
