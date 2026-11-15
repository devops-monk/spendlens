# spendlens

FinOps lens for AWS, built on [Steampipe](https://steampipe.io) and [Powerpipe](https://powerpipe.io).
It finds untagged and non-compliant resources, over-provisioned and wasted spend, and month-over-month cost changes, and you can ask an AI about your bill. All of it uses the AWS role you already have assumed in your terminal.

> Status: phase 1 (scaffold) is done. See [PLAN.md](PLAN.md) for the roadmap.

## Install (dev)

```bash
brew install turbot/tap/steampipe turbot/tap/powerpipe
steampipe plugin install aws && steampipe service start

python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/spendlens init        # writes finops.yaml (your company policy)
.venv/bin/spendlens doctor      # verifies tools, AWS identity, Cost Explorer access
```

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
.venv/bin/pytest && .venv/bin/ruff check src tests
```
