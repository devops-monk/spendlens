# Development & testing

```bash
git clone https://github.com/devops-monk/spendlens.git && cd spendlens
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make test            # unit + integration
make test-system     # end to end against an AWS emulator
make lint
make generate        # regenerate mod variables, tag controls, control docs and the demo DB
```

## Test layers

| Layer | Scope | Network |
|---|---|---|
| **unit** | Config and parameter layers, renderers, estimators, cost/anomaly/forecast maths, SQL guard, history store and migrations | None |
| **integration** | CLI commands on the demo and history DBs; the Powerpipe runner; offline SQL verification; the AI assistant with the real `openai`/`anthropic` SDKs against a localhost stub LLM; notify against a localhost webhook | Localhost only |
| **system** | The real CLI, through Powerpipe and an isolated Steampipe install, against [Moto](https://github.com/getmoto/moto) with seeded resources; Cost Explorer and Budgets responses; `ask --live`; `report` | Localhost only |

Safety rails:
- Unit and integration tests force dummy AWS credentials, `/dev/null` AWS config files, no API keys, and an AWS endpoint on a dead local port, so a bug can't reach a real account or a paid API.
- System tests use a separate Steampipe install directory (`~/.spendlens-e2e`) whose only connection is Moto.

## Offline SQL verification

Every control's SQL, as resolved by Powerpipe, and every cost query is checked against two things:
- **The real Postgres grammar**, via pglast.
- **A snapshot of the Steampipe AWS plugin's tables, columns and column types**, taken from its source with `scripts/snapshot_aws_schema.py`.

The checker catches unknown tables and columns, JSON operators on non-JSON columns, arithmetic on text columns, and `coalesce` mixing an IP-address column with text. Several of these rules exist because the system tests once caught the bug.
