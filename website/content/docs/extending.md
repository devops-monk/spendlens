# Extending spendlens

spendlens's own features are plugins. A plugin package can add commands, Powerpipe controls, savings estimators, AI tools and dashboard tabs, without forking.

## A plugin package

```toml
# pyproject.toml of your package
[project.entry-points."spendlens.plugins"]
acme = "acme_spendlens"
```

```python
# acme_spendlens/__init__.py
from pathlib import Path

import typer
from spendlens.plugins import AITool, Registry

app = typer.Typer(help="ACME-specific checks")

@app.command()
def gpu_idle():
    ...

def register(reg: Registry) -> None:
    reg.add_typer(app, "acme")                                  # spendlens acme gpu-idle
    reg.add_mod(Path(__file__).parent / "mod")                  # extra Powerpipe controls
    reg.add_estimator("acme_gpu_idle", lambda f, pricing: 250.0)  # $/month for this control's alarms
    reg.add_doctor_check(lambda cfg: [])                        # extra `spendlens doctor` rows
    reg.add_ai_tool(AITool("gpu_inventory", "List GPU nodes", {"type": "object", "properties": {},
                           "required": [], "additionalProperties": False}, lambda: {...}))
    reg.add_dashboard_section("gpu", "GPU", lambda data, cfg: "<div class='card'>…</div>")
```

- **Settings:** plugin settings live under `plugins.<name>` in `finops.yaml` (or `--set plugins.acme.threshold=5`).
- **Diagnostics:** `spendlens plugins` lists what's loaded.
- **Isolation:** a broken plugin is reported there and never breaks the CLI.
- **Disabling:** set `SPENDLENS_DISABLE_PLUGINS=acme` (or `*` for all external plugins).

## Building blocks for feature code

| Import | Purpose |
|---|---|
| `spendlens.common.load()` | Resolve config with all parameter layers |
| `spendlens.common.emit()` | Render rows as table, CSV, JSON or HTML |
| `spendlens.common.findings_from_source()` | Live run with history save, or `--source last/demo` replay |
| `spendlens.features.controls.make_control_app()` | A full control-based command (filters, summary, savings, CI gate) |
| `spendlens.steampipe.SteampipeClient` | Read-only Steampipe queries |
| `spendlens.models.Finding` | The normalised result every renderer understands |

## Adding a control

1. Add the control to a `.pp` file under `src/spendlens/mod/finops/`, with `tags = {spendlens_check, service, resource_type}`.
2. List it in the `rightsize` or `waste` benchmark.
3. If it needs a threshold: add a field to `SizingStandards` and run `python scripts/gen_mod_variables.py`.
4. Optionally add an estimator in `features/savings.py`.
5. `make test` validates its SQL offline against the Steampipe schema (names and basic types). `make generate` refreshes the generated docs.
