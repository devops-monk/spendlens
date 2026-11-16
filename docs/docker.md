# Docker

The image contains spendlens with the AI extras, plus pinned versions of Steampipe, Powerpipe and the Steampipe AWS plugin. Steampipe's embedded database is set up when the image is built, so a container starts without downloading anything.

**Docker or local install?**
- **Use Docker:**
  - on Windows;
  - when you don't want to install anything locally;
  - for CI jobs that shouldn't install tools on every run;
  - to run on Kubernetes or ECS on a schedule.
- **Use the [one-line installer](../getting-started/):** for everyday use on your own Mac or Linux machine. Commands start faster because the Steampipe service stays running.

## Pull the published image

```bash
docker pull abhaypratap/spendlens:0.1.0      # or :latest
docker run --rm --network none abhaypratap/spendlens tags --source demo --summary
```

Image page and screenshots: [hub.docker.com/r/abhaypratap/spendlens](https://hub.docker.com/r/abhaypratap/spendlens). Releases are built natively for each platform and published by `.github/workflows/docker-publish.yml` when a `v*` tag is pushed.

## Build it yourself

```bash
docker build -t spendlens .              # or: make docker-build  (tags spendlens:dev)
# multi-arch, pushed to your registry
docker buildx build --platform linux/amd64,linux/arm64 -t <registry>/spendlens:0.1.0 --push .
```

The image is about 1 GB, most of it Steampipe's embedded Postgres and the AWS plugin. BuildKit is required (the default in current Docker; on older versions set `DOCKER_BUILDKIT=1`).

## Use it like a local command

```bash
alias spendlens='docker run --rm -it \
  -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE -e AWS_REGION \
  -v "$PWD:/work" -v spendlens-data:/data spendlens'

spendlens tags --summary
spendlens cost diff --mtd
```

| Build arg | Default |
|---|---|
| `PYTHON_VERSION` | `3.13` |
| `STEAMPIPE_VERSION` | `2.5.0` |
| `POWERPIPE_VERSION` | `1.6.0` |
| `AWS_PLUGIN_VERSION` | `1.35.0` |

## Try it without AWS

```bash
docker run --rm --network none spendlens tags --source demo --summary
docker run --rm --network none -v "$PWD:/work" spendlens report --source demo --out demo-report
```

`make docker-build docker-smoke` builds the image and runs these offline checks with networking disabled.

## Run against your account

The container uses the standard AWS credential chain, like the CLI. Credentials are never baked into the image; pass them in at run time.

```bash
# a profile / SSO session from your machine (read-only mount)
docker run --rm -v ~/.aws:/home/finops/.aws:ro -e AWS_PROFILE=finops \
  -v "$PWD:/work" -v spendlens-data:/data spendlens report --out reports

# the role already assumed in your terminal
docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN -e AWS_REGION \
  -v "$PWD:/work" -v spendlens-data:/data spendlens cost diff
```

| Path | Purpose |
|---|---|
| `/work` | Working directory: put `finops.yaml` here; reports are written here |
| `/data` | History database (`SPENDLENS__HISTORY__PATH=/data/spendlens.db`). Mount a volume to keep trends between runs |
| `/home/finops/.aws` | Optional read-only AWS config and SSO cache |

Notes:
- **Non-root user.** The container runs as user `finops` (uid 10001) because Steampipe refuses to run as root. If you mount `/work` or `/data`, they must be writable by that uid.
- **Exit codes pass through.** For example, `--fail-on-alarm` exits with 3, so the image works as a CI gate.

## How the entrypoint handles Steampipe

The entrypoint starts the local Steampipe service (listening inside the container only) when a command needs live data, and stops it afterwards. Commands that only replay data skip it: `--source demo`, `--source last`, `ask` without `--live`, `dashboard`, `notify`, `history`, `params`, `config` and `init`.

| `SPENDLENS_STEAMPIPE_SERVICE` | Behaviour |
|---|---|
| `auto` (default) | Start the service only when needed |
| `on` | Always start it |
| `off` | Never start it; use an external Steampipe configured with `steampipe.*` settings |

## In CI

Use the image as the job image to skip installing tools on every run:
- **GitLab:** `image: <registry>/spendlens:0.1.0`, with `entrypoint: [""]`, then `spendlens-entrypoint report --out reports` in `script`.
- **GitHub Actions:** `container: <registry>/spendlens:0.1.0`.

OIDC role assumption works as described in [CI/CD](ci.md). Pass `AWS_ROLE_ARN` and `AWS_WEB_IDENTITY_TOKEN_FILE` into the container.
