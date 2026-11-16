# spendlens

FinOps lens for AWS, built on [Steampipe](https://steampipe.io) and [Powerpipe](https://powerpipe.io).
It finds untagged and non-compliant resources, over-provisioned and wasted spend, and month-over-month cost changes, and you can ask an AI about your bill. All of it uses the AWS role you already have assumed in your terminal.

> Status: all 8 build phases are done. Next is the live verification pass with an assumed role (see PLAN.md).

## Install

```bash
curl -fsSL https://spendlens.devops-monk.com/install.sh | sh      # macOS / Linux, no sudo
# or run it in a container, with Steampipe, Powerpipe and the AWS plugin included:
docker build -t spendlens . && docker run --rm spendlens tags --source demo --summary
```

The installer (`install.sh`) puts pinned Steampipe and Powerpipe in place (Homebrew if present, otherwise `~/.local/bin`), installs the AWS plugin, and installs spendlens in an isolated environment (pipx, `uv tool`, or a private venv). `SPENDLENS_DRY_RUN=1` shows each step without changing anything. See `docs/docker.md` for the image.

## Install (development)

```bash
brew install turbot/tap/steampipe turbot/tap/powerpipe
steampipe plugin install aws && steampipe service start

python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/spendlens init        # writes finops.yaml (your company policy)
.venv/bin/spendlens doctor      # verifies tools, AWS identity, Cost Explorer access
```

## Try it without AWS

```bash
spendlens tags --source demo --summary          # bundled sample account
spendlens tags --source demo -k mandatory -r s3_bucket -f csv
spendlens history diff --source demo            # what changed between two pipeline runs
```

## Tag compliance

```bash
spendlens tags                                   # all checks, alarms only
spendlens tags -k mandatory -k prohibited -r ec2_instance --status all
spendlens tags --summary -f json -o tags.json
spendlens tags --fail-on-alarm                   # exit 3 on any alarm (CI gate)
spendlens tags types                             # 70 resource types covered
```

Checks: `mandatory`, `untagged`, `prohibited`, `limit` and `expected_values` (SQL LIKE patterns). They come from `tagging.*` in `finops.yaml`.

## Rightsizing and waste

```bash
spendlens rightsize --summary --by service       # 41 controls; company standards come from finops.yaml sizing.*
spendlens waste --min-saving 20                  # 24 controls; idle/orphaned resources with $/month estimates
spendlens rightsize -S rds -C rds_instance_multi_az_policy --status all
spendlens waste controls                         # what is checked
```

Catalog: [docs/controls.md](docs/controls.md). Mapping from the legacy mods: [docs/control-mapping.md](docs/control-mapping.md).

## Cost analysis

```bash
spendlens cost diff                              # last full month vs the month before, by service
spendlens cost diff --mtd -g account             # month to date vs the same days last month
spendlens cost diff --from 2026-07..2026-09 --to 2026-10..2026-12 -g tag:Team
spendlens cost diff --show flagged --fail-on-flag   # CI gate on big changes (cost.change_alarm_*)
spendlens cost trend -n 6 --include-current      # monthly pivot with month-over-month change
spendlens cost showback -t Team                  # share per tag value, untagged spend, budget variance
spendlens cost resources --days 7                # resources whose cost rose/fell (needs CE resource-level data)
```

New and removed services are reported as `new` and `gone`; the legacy mod dropped them. Credits and refunds are excluded by default (`cost.exclude_record_types`).

## Anomalies, forecast and savings

```bash
spendlens anomalies                              # daily spikes per service vs a 30-day robust baseline
spendlens anomalies -g account --days 7 --fail-on-anomaly
spendlens forecast                               # month-end projection vs budgets.monthly_total + AWS Budgets
spendlens savings hub --min-saving 50            # AWS Cost Optimization Hub recommendations
spendlens savings commitments                    # Savings Plan / RI coverage per service, expiring plans
spendlens savings summary                        # one de-duplicated total across rightsize, waste, hub, commitments
```

`savings summary` never double counts:
- When several actions apply to one resource, it keeps only the largest.
- When AWS and spendlens both estimate the same resource, AWS's estimate wins.
- Commitments are counted once.

Required IAM permissions: [docs/permissions.md](docs/permissions.md).

## HTML dashboard

```bash
spendlens dashboard -o report.html               # from your latest saved runs (no AWS calls)
spendlens dashboard --source demo -o demo.html   # try it with the bundled sample data
spendlens waste -f html -o waste.html            # any command can write a branded HTML table
```

The dashboard is a single self-contained file with no network requests, so it works offline, as a CI artifact, and when printed.
- **Tabs:** Overview, Cost diff & trend, Showback, Anomalies, Forecast, Tags, Rightsizing, Waste and Savings.
- **Readability:** light and dark mode, hover and keyboard tooltips, and a table view for every chart.
- **Branding:** `dashboard.*` in `finops.yaml` sets the title, organisation, brand colours and font. Brand colours style chrome only; chart data colours are a fixed palette validated for colour-blind readers.
- **Layout and style:** live in `src/spendlens/templates/report/` (Jinja2 templates plus `style.css` and `app.js`). Python only builds the view model, which is also embedded in the page as JSON (`#spendlens-data`).
- **Extending:** plugins add tabs with `registry.add_dashboard_section(id, title, fn)`.

## Ask the AI assistant

```bash
spendlens ask "What are my top 5 savings opportunities?"
spendlens ask "Why did EC2 cost go up last month?" --show-tools     # see every query behind the answer
spendlens ask --live "Which running instances are outside the allowed families?"   # also query live AWS
spendlens ask -i                                                    # interactive follow-ups
spendlens ask --source demo "Summarise the waste findings"          # try it on sample data
```

The assistant is read-only. It answers from saved history first (offline and free), and queries live AWS through Steampipe only with `--live`.

Every model-written query goes through three safety layers:
1. A SQL guard that allows one read-only `SELECT`.
2. A read-only Steampipe connection with a timeout and a row cap.
3. A read-only SQLite connection with an authorizer that denies anything except reads.

Providers are set with `llm.*` in `finops.yaml`:
- **Claude** on the Anthropic API or Amazon Bedrock (official Anthropic SDK). Bedrock uses your assumed AWS role and needs `llm.region` or `AWS_REGION`. Refusal fallbacks are on by default.
- **OpenAI, Azure OpenAI**, or any **OpenAI-compatible** server such as Ollama or vLLM (official OpenAI SDK).

Install with `pip install 'spendlens[ai]'`. `spendlens doctor` checks the AI setup without calling the model.

## Run everything, notify, schedule in CI

```bash
spendlens report --out reports                  # every check -> reports/*.json + reports/report.html
spendlens notify --slack --teams --link <url>   # summary of the latest runs (webhook URLs from env vars)
spendlens notify --slack --only-if-changes      # stay quiet unless something needs attention
spendlens notify --teams --dry-run --source demo
spendlens init --ci gitlab                      # scheduled pipeline (.gitlab-ci.yml); or --ci github
```

`report` keeps going when a step fails (for example a missing permission) and still writes the other outputs and the dashboard.

`notify` posts Block Kit to Slack and an Adaptive Card to Teams (Workflows webhook). Webhook URLs come from `$SPENDLENS_SLACK_WEBHOOK` and `$SPENDLENS_TEAMS_WEBHOOK`, must use https, and are never stored in config.

The CI templates:
- assume a read-only AWS role through OIDC, with no stored keys;
- keep the history DB between runs (S3, or the CI cache as fallback);
- run the checks as a non-root user (Steampipe refuses root);
- post the summary.

Setup: [docs/ci.md](docs/ci.md).

## Data sources and history

`--source live` (default) queries AWS and saves the run to the local history DB. `--source last` replays the latest saved run offline. `--source demo` uses the bundled sample data.

```bash
spendlens history list
spendlens history diff --feature tags           # new alarms / resolved / resource gone
spendlens history path                          # for CI: restore/save this file between jobs
```

In CI, set `SPENDLENS__HISTORY__PATH=.spendlens/history.db` and restore and save that file (S3 or cache) around the job, so trends work across pipeline runs.

## Control parameters

Every threshold and policy value can be set in four layers. Later layers win:

| Layer | Example |
|---|---|
| built-in defaults | n/a |
| `finops.yaml` | `sizing: {ec2_cpu_low: 15}` |
| environment | `SPENDLENS__SIZING__EC2_CPU_LOW=12` |
| command line | `--set sizing.ec2_cpu_low=10` (repeatable; YAML values: `--set 'tagging.mandatory=[Env, Owner]'`) |

```bash
spendlens params --filter ec2          # value, source and env-var name of every parameter
spendlens config --set llm.provider=openai
spendlens <cmd> --var my_mod_variable=5  # raw Powerpipe variable passthrough
```

Unknown keys, typos and out-of-range values are rejected, and the error says which layer they came from.

## Output

Every report command takes `--format table|csv|json|html` and `--out FILE`.

```bash
spendlens query "select name, region from aws_s3_bucket" -f csv -o buckets.csv
```

## Extending

Features are plugins. A package can add commands, doctor checks, AI tools and extra Powerpipe mods:

```toml
[project.entry-points."spendlens.plugins"]
acme = "acme_spendlens"
```

```python
def register(reg):
    reg.add_typer(my_typer_app, "acme")      # spendlens acme ...
    reg.add_mod(Path(__file__).parent / "mod")
    reg.add_doctor_check(check_fn)
```

Plugin settings go under `plugins.<name>` in `finops.yaml`. Run `spendlens plugins` to see what is loaded. Full API: `src/spendlens/plugins.py`.

## Development

```bash
make test              # unit + integration (fast, no cloud)
make test-system       # end-to-end against the Moto AWS emulator via an isolated Steampipe install
make lint
make generate          # after changing config schema, tag catalog, controls or demo generators
```

Tag control SQL is derived from turbot/steampipe-mod-aws-tags (Apache-2.0).
