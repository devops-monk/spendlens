# History & data sources

Every live run is saved locally. That's what makes offline reports, "what changed" and the AI assistant possible without calling AWS again, and lets the dashboard, notifications and savings summary reuse anything already checked today.

## Sources

Commands that read data accept `--source`:

| Source | Reads | AWS calls |
|---|---|---|
| `live` (default for checks) | Your AWS account, through Steampipe; saves the run | Yes |
| `last` | Your latest saved run of that feature | No |
| `demo` | Bundled sample data (account `123456789012`) | No |

## The history database

- **Location:** `~/.local/share/spendlens/spendlens.db` by default. Change it with `history.path` or `SPENDLENS__HISTORY__PATH`.
- **Contents:** each run's findings, cost rows, the parameters used, the caller identity and CI metadata.
- **Retention:** old runs are pruned per feature after `history.keep_runs` (default 200).
- **AI cache:** `spendlens ask` and `brief` keep a separate `ai-cache.db` in the same folder. It never holds findings, only reusable answers and live results; `spendlens cache clear` empties it (see [Cache commands](../ai-assistant/#cache-commands)).

```bash
spendlens history list
spendlens history diff --feature tags            # new alarms, resolved, resources gone, new resources
spendlens history diff --feature waste --from 12 --to 15
spendlens history path
```

## In CI

CI runners are ephemeral. To keep trends between pipelines:
1. Set `SPENDLENS__HISTORY__PATH`.
2. Restore the file before the run and save it after (S3 recommended, CI cache as fallback).

The generated CI templates do both (see [CI/CD](../ci/)).
