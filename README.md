# spendlens

FinOps lens for AWS, built on [Steampipe](https://steampipe.io) and [Powerpipe](https://powerpipe.io).
It finds untagged and non-compliant resources, over-provisioned and wasted spend, and month-over-month cost changes, and you can ask an AI about your bill. All of it uses the AWS role you already have assumed in your terminal.

> Status: phases 1–4 are done (tags, rightsizing, waste and savings, cost analysis). See [PLAN.md](PLAN.md).

## Install (dev)

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
