# spendlens: FinOps CLI on Steampipe/Powerpipe (Architecture Plan)

## Context
Hackathon goal (from `Hackathon_Plan.md`): a CLI that uses a "FinOps AI profile" to analyse AWS spend, flag anomalies and suggest savings for human review. It must:
1. Flag resources that are missing the company's mandatory tags.
2. Flag resources that are over-provisioned or don't follow company standards.
3. Show cost changes month-over-month or for a chosen range of months, by service and resource, along with savings options.
4. Produce table, CSV and JSON output, plus an HTML dashboard styled after very.co.uk.
5. Answer natural-language questions with any LLM provider, using the AWS role assumed in the terminal.

**First implementation step after approval:** write this plan to `PLAN.md` in the project root. Then build one phase at a time, checking in with the user after each phase.

### User decisions
- Python
- Multi-provider AI: OpenAI, OpenAI-compatible, Azure OpenAI, Bedrock, Anthropic
- Name: **spendlens**
- Extras: anomaly detection + forecast, waste detection + $ savings, showback by team/tag, Slack/Teams notifications + CI schedule

### Findings from existing repos
- The user's mods (`~/Downloads/steampipe/steampipe-mod-aws-tags`, `steampipe-mod-aws-cost-analysis`) use the **old `.sp` / `steampipe check` format**. Every Turbot mod in the repo (thrifty v1.2, tags v1.0.1, compliance v1.14) has moved to **Powerpipe `.pp`**. We migrate the user's mods to Powerpipe.
- Reuse:
  - The templated `__TABLE_NAME__` SQL for mandatory and untagged tags. It checks `tags ?& $1` and lists missing tags with `to_jsonb($1) - keys`.
  - The month-over-month CTE over `aws_cost_usage` in `controls/cost_explorer.sp`. No other repo has a month-over-month delta.
  - From Turbot tags: the `prohibited`, `limit` and `expected_tag_values` benchmarks.
  - Thrifty's threshold variables and its controls for ebs, ec2, rds, ecs, lambda, cloudwatch, dynamodb, elasticache, redshift, network and s3.
  - The `common_dimensions` / `tag_dimensions` pattern.
- Fix:
  - The `mandatory_tags` default lists "Environment" twice.
  - The cost-analysis `mod.sp` is named `aws_compliance`.
- Tables in `steampipe-plugin-aws` v1.35 that we'll use:
  - Cost: `aws_cost_usage`, `aws_cost_by_service_monthly/daily`, `aws_cost_by_account_*`, `aws_cost_by_tag`, `aws_cost_by_resource_*`, `aws_cost_forecast_*`
  - Savings: `aws_costoptimizationhub_recommendation`, `aws_budgets_budget`, `aws_savingsplans_*`, `*_reserved_*`, `aws_pricing_product`
  - There is no Compute Optimizer table; Cost Optimization Hub covers that ground.
- Not yet installed locally: steampipe, powerpipe. `spendlens doctor` will print the install steps.

## Architecture

```
user (assumed AWS role / AWS_PROFILE / SSO)
  │
  ▼
spendlens CLI (Typer + Rich)
  commands: init · doctor · tags · rightsize · waste · cost diff · cost showback
            anomalies · forecast · savings · report · dashboard · ask · notify
  global flags: --format table|csv|json|html  --out FILE  --profile  --accounts  --regions
  │
  ├─ Config (finops.yaml, pydantic-validated)
  │    tagging policy (mandatory, allowed values, prohibited), sizing standards
  │    (allowed instance types, CPU/EBS/RDS/ECS thresholds), anomaly sensitivity,
  │    budgets, LLM provider, notification webhooks
  │
  ├─ Engines
  │    BenchmarkRunner → `powerpipe benchmark run <x> --output json --var ...` (bundled mod)
  │    CostEngine      → direct SQL to Steampipe service (psycopg, :9193)
  │                      MoM / range diff, showback, forecast, anomaly (z-score/rolling median)
  │    SavingsEngine   → waste controls + Cost Optimization Hub + RI/SP coverage → $/month
  │    AIAgent         → tool-calling loop over read-only tools
  │
  ├─ Findings model (pydantic): resource, account, region, control, status, reason,
  │    est_monthly_saving, tags, severity  → used by every renderer
  │
  ├─ Renderers: Rich table · CSV · JSON · HTML (Jinja2, Very theme, self-contained file, Chart.js inline)
  │
  └─ Data layer: Steampipe service + aws plugin, connection uses the default
       credential chain → inherits the role assumed in the terminal; optional
       aggregator connection for multi-account.
```

### Bundled Powerpipe mod: `mods/spendlens/`
- `.pp` files.
- Benchmarks:
  - `tags.mandatory|untagged|prohibited|expected_values`
  - `rightsize.ec2|ebs|rds|ecs|lambda|...`
  - `waste.*` (unattached EBS, unused EIPs, old snapshots, idle RDS/ELB, log groups without retention)
- spendlens turns `finops.yaml` into `--var` flags, so company standards live in one file.

### Cost diff
- Forms:
  - `spendlens cost diff --last-month`
  - `spendlens cost diff --from 2026-08 --to 2026-10`
  - `spendlens cost diff --group-by service|account|region|tag:Team|usage-type`
- Output: previous cost, current cost, absolute change, % change, and the top increases and decreases.
- Resource-level drill-down: `--by-resource` via `aws_cost_by_resource_daily`. The CLI must clearly warn that Cost Explorer only keeps 14 days of resource-level data.
- Month-to-date comparisons are normalised to the same day of the month.

### Anomalies and forecast
- **Anomalies:** daily cost per service, compared with a trailing 30-day median. The deviation is measured as MAD/z-score, and the threshold comes from config.
- **Forecast:** `aws_cost_forecast_monthly`, compared against `aws_budgets_budget` and the policy budgets.

### AI `ask`: provider-agnostic
- **LLM calls go through LiteLLM**, behind a thin `spendlens.ai.provider` adapter.
- **Supported providers:** `openai`, `azure`, `bedrock`, `anthropic`, `openai_compatible` (`base_url`, e.g. Ollama/vLLM/LM Studio).
- **Model selection:** `llm.provider` and `llm.model` in `finops.yaml`, or the `SPENDLENS_LLM_*` env vars.
- **Bedrock credentials:** the assumed role, through boto3's default chain.
- **Tools the model can call:**
  - `list_tables(prefix)`
  - `describe_table(name)` (via `information_schema`)
  - `run_sql(sql)`:
    - only accepts a single SELECT/WITH statement (parsed with `sqlglot`)
    - wrapped in `statement_timeout` and a row cap
    - runs on a read-only connection
  - `run_benchmark(name)`
  - `cost_diff(...)`, `get_findings(...)`
- **System prompt:** the "FinOps analyst profile". Answers must cite their SQL/data and list savings as *proposals that need human review*. spendlens never changes AWS resources.
- **Modes:** a one-shot question (`spendlens ask "why did EC2 go up?"`) and an interactive REPL (`spendlens ask -i`).
- **Fallback:** when a model can't do tool calls, the CLI says so clearly.

### HTML dashboard (`spendlens dashboard --out report.html`)
- A single self-contained file with tabs:
  - Overview (KPIs: spend MTD, change vs. last month, forecast, potential savings, tag compliance %)
  - Tags
  - Rightsizing
  - Waste & Savings
  - Cost Diff
  - Showback
  - Anomalies
- Very-style branding: bold pink/magenta accent, black/white, rounded cards, clean sans-serif. The CSS variables live in one theme file so exact brand colours can be dropped in.

### Notifications and CI
- `spendlens notify --slack-webhook/--teams-webhook` posts a summary card with the top changes and savings.
- A `.gitlab-ci.yml` template mirrors the user's existing CI: it runs on a schedule, assumes the role through OIDC, and keeps the HTML/JSON artifacts.

## Control parameters (product-grade)
Every control threshold and policy value is a typed, validated parameter. Values are resolved in layers, and later layers win:
1. built-in defaults
2. the policy file (`--config` → `$SPENDLENS_CONFIG` → `./finops.yaml` → `~/.config/spendlens/finops.yaml`)
3. env vars `SPENDLENS__SECTION__KEY=value`, for CI
4. `--set section.key=value`, repeatable, on every command

Rules:
- Values are parsed as YAML, so lists and maps work: `--set 'tagging.mandatory=[Env, Owner]'`.
- Unknown keys, typos and out-of-range values fail fast. The error names the key and the layer it came from.
- `--var name=value` passes any raw Powerpipe mod variable straight through.
- `spendlens params [--filter x]` lists each parameter with its value, source and env-var name. `spendlens config` shows the merged config and the Powerpipe vars it produces.

## Extensibility
- **Plugin registry:** `spendlens.plugins`, using the `spendlens.plugins` entry-point group. A plugin's `register(registry)` can add:
  - CLI commands or sub-apps
  - doctor checks
  - AI tools for `ask`
  - extra Powerpipe mod paths with new controls
- **Built-in features use the same API.** Every feature (tags, rightsize, cost, …) lives in `spendlens/features/<name>.py` and is listed in `BUILTIN_FEATURES`. Adding a feature means adding one module.
- **Plugin settings:** `plugins.<name>.*` in finops.yaml, and `--set plugins.acme.x=1` works too.
- **Failure handling:** a broken plugin is reported by `spendlens plugins` and never crashes the CLI. Disable plugins with `SPENDLENS_DISABLE_PLUGINS=a,b|*`.
- **Shared helpers for feature code:**
  - `cli.load()` resolves the config and params
  - `cli.emit()` renders any format
  - `SteampipeClient` runs queries
  - The `Finding` / `CostDelta` models plug straight into every renderer, including the HTML dashboard.
- **Custom controls without Python (phase 2+):** drop `.pp` files into a mod dir listed in config. They are picked up by the benchmark runner and inherit all parameters.

## Local data: history store and demo DB (SQLite)
- **History store:** `history.path`, default `~/.local/share/spendlens/spendlens.db`.
  - Every live run is saved: findings, cost rows, effective params, caller identity, CI metadata.
  - Enables trends, `spendlens history list`, and `history diff` (new alarms, resolved, resource gone, new resource).
  - Old runs are pruned per feature (`history.keep_runs`).
- **Bundled demo DB:** `spendlens/data/demo.db`, built by `scripts/build_demo_db.py`.
  - Deterministic synthetic data for account 123456789012.
  - Each feature adds a `demo_<feature>()` generator.
- **Source switch on every command:** `--source live|last|demo`.
  - `live` queries AWS and saves the run.
  - `last` replays the latest saved run with no AWS calls.
  - `demo` uses the bundled data.
- **Laptop use:** history builds up locally. Anyone can try the tool with `--source demo` without AWS.
- **Pipeline use:** set `SPENDLENS__HISTORY__PATH`. The CI template restores and saves the DB file (S3 or cache) around the job, so trends span pipeline runs.
- **AI over history (phase 7):**
  - `ask` gets a read-only SQL tool over the history DB, alongside live Steampipe.
  - People investigating an issue can chat about cached results offline. Examples: "why is this bucket alarming?", "what changed since last week's pipeline?"
  - Live Steampipe is used only when history can't answer.

## Live verification checklist (deferred until the product is ready; run with an assumed role)
- [ ] `steampipe service start` (the first attempt failed while downloading steampipe-postgres-fdw; retry)
- [ ] `spendlens doctor` all green
- [ ] `spendlens tags` against a real account: the mandatory, prohibited and expected_values SQL return sensible rows; `--resource-type` filter (`--where`); `--search-path-prefix` with an aggregator
- [ ] Tables in the catalog that error for permissions show up as `error` findings, not crashes

## Project layout
```
spendlens/
  pyproject.toml            # typer, rich, pydantic, psycopg[binary], jinja2, litellm, sqlglot, boto3, httpx
  src/spendlens/
    cli.py                  # Typer app, command groups
    config.py               # finops.yaml schema + loader
    doctor.py
    plugins.py              # Registry, entry-point discovery
    common.py               # shared options, load(), emit(), --source handling
    history.py              # SQLite history store + diff
    data/demo.db            # bundled demo data
    mod/                    # bundled Powerpipe mod (tags/*.pp generated)
    features/               # built-in features, each registers via plugins API
    steampipe.py            # connection, service start/check, query helper
    powerpipe.py            # benchmark runner + JSON → Findings
    models.py               # Finding, CostDelta, Anomaly ...
    engines/{cost,anomaly,savings,showback}.py
    ai/{provider.py,agent.py,tools.py,sql_guard.py,prompts.py}
    render/{table,csv,json,html}.py
    templates/dashboard.html.j2, theme.css
    notify.py
  scripts/                  # gen_tag_controls.py, build_demo_db.py
  examples/finops.yaml
  ci/.gitlab-ci.yml
  tests/
PLAN.md
```

## Phases (built one at a time, with user sign-off after each)
1. ✅ Scaffold: write `PLAN.md`, set up pyproject and the CLI skeleton, add the config with `init` and `doctor`, the Steampipe query helper, the findings model, and the table/CSV/JSON renderers. Also delivered: the parameter layers (`--set`, `--var`, env), the `params`, `config`, `query` and `plugins` commands, and the plugin registry.
2. ✅ Tags: migrate the tags mod to `.pp` and add the `spendlens tags` command. Delivered:
   - a generated mod covering 70 resource types × 5 checks, with Turbot's expected-values precedence bug fixed
   - a Powerpipe runner that uses the non-deprecated connection config, `--where` filtering and a concatenated-JSON parser
   - the `--summary`, `--fail-on-alarm` and `tags types` commands
   - the SQLite history store, `history list|diff|path`, the bundled demo DB, and `--source live|last|demo`
3. ✅ Rightsizing and waste: migrate cost-analysis plus the chosen thrifty controls, and add the `rightsize` and `waste` commands. Delivered:
   - 65 controls (41 rightsize, 24 waste): all 12 cost-analysis controls migrated with bug fixes, plus selected Thrifty and aws-compliance controls (`docs/control-mapping.md`, `docs/controls.md`)
   - environment-aware standards (`*_by_env`, keyed on `tagging.environment_tag`)
   - $ savings estimators driven by the `pricing:` config, with plugin estimators via `registry.add_estimator`
   - a shared control-feature builder (`--service/--control/--status/--summary --by/--min-saving/--fail-on-alarm`)
   - mod variables generated from the config schema
   - offline SQL verification: pglast grammar check plus a plugin schema snapshot
   - a test enforcing coverage of the legacy controls
   - history schema v2 with migrations
   3b. ✅ Three test layers (`make test`, `make test-system`):
       - **unit:** pure logic
       - **integration:** the CLI with the SQLite and demo DBs, real or stand-in Powerpipe, and offline SQL checks
       - **system:** the real CLI → Powerpipe → isolated Steampipe → Moto, with seeded resources and 33 expected outcomes, plus scenario tests for formats, history replay, the CI gate, and fix → resolved in `history diff`
       The system layer found three real bugs that offline checks could not:
       - an inet `coalesce` in the EIP control
       - Lambda `timeout`, which the plugin exposes as text
       - `round(double, int)` in the NAT gateway control
       Doctor and run metadata now pass `--endpoint-url` explicitly, because AWS CLI < 2.13 ignores `AWS_ENDPOINT_URL`.
4. Cost: add `cost diff` (MoM and custom range, with grouping and by-resource) and `cost showback`.
5. Savings, anomalies and forecast.
6. HTML dashboard with Very branding.
7. AI `ask`: the multi-provider adapter, read-only tools, the SQL guard, and the REPL.
8. `notify` (Slack/Teams) and the GitLab CI schedule template.

## Test strategy
| Layer | Scope | How |
|---|---|---|
| unit | config/params, renderers, estimators, HCL/JSON parsing, history store/diff/migrations, plugin registry | `make test-unit` |
| integration | CLI commands (demo/last sources, filters, gates), Powerpipe runner (stand-in binary + real mod parse), offline SQL verification (pglast + plugin schema snapshot), legacy coverage, generated files up to date | `make test-integration` |
| system | full product against Moto. Steampipe runs from an isolated install (`~/.spendlens-e2e`), and dummy credentials, `AWS_CONFIG_FILE` pointed at nothing, and a forced endpoint keep it off real AWS | `make test-system` (opt-in) |

## Verification
- `spendlens doctor` passes using the user's assumed role (`aws sts get-caller-identity`, Steampipe service up, Cost Explorer readable).
- Every command runs against a real account in all `--format` values. Check the CSV and JSON shapes, and open the HTML in a browser.
- pytest coverage:
  - config → vars mapping
  - renderers
  - SQL guard, which rejects INSERT, DROP and multiple statements
  - cost diff math, using fixture rows
  - anomaly detection, using synthetic series
- AI smoke test against at least two providers: "Which services cost more this month than last month?" should produce SQL and an answer that matches `cost diff`.
