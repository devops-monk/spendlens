# Getting started

Install spendlens with one command, point it at an AWS account, and run your first checks. Allow about five minutes.

## Requirements

- macOS or Linux, on x86-64 or ARM
- Python 3.11 or newer, and `curl`
- AWS credentials for a read-only role (see [IAM permissions](../permissions/))

On Windows, or to install nothing locally, use [Docker](../docker/).

## 1. Install

```bash
curl -fsSL https://spendlens.devops-monk.com/install.sh | sh
```

The installer:
1. Installs [Steampipe](https://steampipe.io) and [Powerpipe](https://powerpipe.io) at pinned versions. It uses Homebrew if you have it, otherwise it puts them in `~/.local/bin`, without sudo.
2. Installs the Steampipe AWS plugin.
3. Installs spendlens with the AI assistant's SDKs in its own isolated environment (pipx, `uv tool`, or a private virtualenv), so it never touches your other Python packages.

It installs tools only. It never reads your AWS credentials or calls AWS. Run it again at any time to upgrade; tools already installed are kept.

To read the script before running it:

```bash
curl -fsSLO https://spendlens.devops-monk.com/install.sh
less install.sh
sh install.sh
```

| Option | Effect |
|---|---|
| `SPENDLENS_DRY_RUN=1` | Print every step without changing anything |
| `SPENDLENS_REF=v0.4.7` | Install a specific tag or branch (default `main`) |
| `SPENDLENS_EXTRAS=""` | Skip the AI assistant's SDKs |
| `SPENDLENS_SKIP_ENGINES=1` | Don't install Steampipe, Powerpipe, Tailpipe or their plugins (you manage them) |
| `SPENDLENS_SKIP_TAILPIPE=1` | Don't install Tailpipe (CloudTrail logs, flow logs and the CUR then aren't collected locally) |
| `SPENDLENS_BIN_DIR=…` | Where binaries go (default `~/.local/bin`) |
| `SPENDLENS_PACKAGE=…` | Install spendlens from a wheel path or URL, e.g. an internal mirror |

For example: `curl -fsSL https://spendlens.devops-monk.com/install.sh | SPENDLENS_DRY_RUN=1 sh`.

### Manual install

The same three steps by hand:

```bash
brew install turbot/tap/steampipe turbot/tap/powerpipe     # or the official install scripts
steampipe plugin install aws
pipx install "spendlens[ai] @ https://github.com/devops-monk/spendlens/archive/main.tar.gz"
```

> Steampipe refuses to run as `root`. Install and run spendlens as a normal user. The Docker image already does this.

## 2. Try it without AWS

Every command can replay bundled sample data, so you can explore before connecting an account:

```bash
spendlens tags --source demo --summary
spendlens waste --source demo
spendlens cost diff --source demo
spendlens report --source demo --out demo-report      # opens as demo-report/report.html
```

## 3. Connect your AWS account

spendlens uses the AWS credential chain, so whatever works for the AWS CLI works here:

```bash
aws sso login --profile finops            # or: export AWS_PROFILE=finops, or an assumed-role session
steampipe service start
```

spendlens queries **eu-west-1** by default. Change it with `aws.regions` in `finops.yaml` (see [Configuration](../configuration/#aws-regions)). Start the Steampipe service *after* assuming your role, because it keeps the credentials it started with. Use `--profile` on any command, or set `aws.profile` in `finops.yaml`. For several accounts, configure a Steampipe [aggregator connection](https://hub.steampipe.io/plugins/turbot/aws#multi-account-connections) and set `steampipe.connection` to its name.

## 4. Describe your standards

```bash
spendlens init          # writes finops.yaml
```

Edit the mandatory tags, allowed instance families, budgets and AI provider. See [Configuration](../configuration/) for every option.

## 5. Check your setup

```bash
spendlens doctor
```

`doctor` checks Python, Steampipe, Powerpipe, the AWS plugin, your AWS identity, the Steampipe service, Cost Explorer access and the AI assistant setup. Each failed check prints its fix. `spendlens doctor --offline` runs only the local checks and makes no network calls.

## 6. Run it

```bash
spendlens tags --summary
spendlens rightsize --min-saving 20
spendlens waste
spendlens cost diff
spendlens report --out reports        # everything at once, plus reports/report.html
```

While a command runs, spendlens shows what it's doing with a live timer: preparing the Steampipe connection, running the checks in Powerpipe, querying Cost Explorer. Each step ends with a ✓ line and its duration. Large accounts can take several minutes. The live display appears only in an interactive terminal, so CI logs stay clean; set `SPENDLENS_NO_PROGRESS=1` to turn it off.

Every live run is saved to your local history (`~/.local/share/spendlens/spendlens.db`), so you can re-render any report offline with `--source last`.

## Next

- [Configuration](../configuration/): tune thresholds to your company's standards
- [AI assistant](../ai-assistant/): ask questions about your spend
- [CI/CD](../ci/): run on a schedule and post summaries to Slack or Teams
