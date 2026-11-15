# spendlens

FinOps lens for AWS, built on [Steampipe](https://steampipe.io) and [Powerpipe](https://powerpipe.io).
It finds untagged and non-compliant resources, over-provisioned and wasted spend, and month-over-month cost changes, and you can ask an AI about your bill. All of it uses the AWS role you already have assumed in your terminal.

> Status: phases 1–2 are done (scaffold, tag compliance, history and demo data). See [PLAN.md](PLAN.md).

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
.venv/bin/pytest && .venv/bin/ruff check src tests scripts
python scripts/gen_tag_controls.py     # after editing features/tags_catalog.py
python scripts/build_demo_db.py        # after changing demo generators
```

Tag control SQL is derived from turbot/steampipe-mod-aws-tags (Apache-2.0).
