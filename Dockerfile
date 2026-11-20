# syntax=docker/dockerfile:1.7
# spendlens: read-only FinOps CLI for AWS, with Steampipe, Powerpipe and the AWS plugin baked in.
#
#   docker build -t spendlens .
#   docker run --rm spendlens tags --source demo --summary
#   docker run --rm -e AWS_PROFILE=finops -v ~/.aws:/home/finops/.aws:ro -v "$PWD:/work" spendlens report --out reports
#
# Multi-arch: docker buildx build --platform linux/amd64,linux/arm64 -t <registry>/spendlens:0.3.2 --push .

ARG PYTHON_VERSION=3.13

# ---------------------------------------------------------------- build the wheels
FROM python:${PYTHON_VERSION}-slim AS wheels
WORKDIR /src
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[ai]"

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim

ARG STEAMPIPE_VERSION=2.5.0
ARG POWERPIPE_VERSION=1.6.0
ARG AWS_PLUGIN_VERSION=1.35.0
ARG TARGETARCH

LABEL org.opencontainers.image.title="spendlens" \
      org.opencontainers.image.description="Read-only FinOps CLI for AWS on Steampipe and Powerpipe" \
      org.opencontainers.image.source="https://github.com/devops-monk/spendlens" \
      org.opencontainers.image.url="https://spendlens.devops-monk.com" \
      org.opencontainers.image.licenses="Apache-2.0"

# tini reaps the Steampipe/Postgres child processes; no compilers or shells beyond bash needed at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

# Pinned Steampipe + Powerpipe release binaries (amd64 / arm64 from TARGETARCH).
RUN set -eux; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    mkdir -p /tmp/sp /tmp/pp; \
    curl -fsSL "https://github.com/turbot/steampipe/releases/download/v${STEAMPIPE_VERSION}/steampipe_linux_${arch}.tar.gz" | tar -xz -C /tmp/sp; \
    curl -fsSL "https://github.com/turbot/powerpipe/releases/download/v${POWERPIPE_VERSION}/powerpipe.linux.${arch}.tar.gz" | tar -xz -C /tmp/pp; \
    install -m 0755 "$(find /tmp/sp -type f -name steampipe | head -1)" /usr/local/bin/steampipe; \
    install -m 0755 "$(find /tmp/pp -type f -name powerpipe | head -1)" /usr/local/bin/powerpipe; \
    rm -rf /tmp/sp /tmp/pp

COPY --from=wheels /wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheels "spendlens[ai]" && rm -rf /tmp/wheels

# Steampipe refuses to run as root, so everything runs as an unprivileged user.
RUN useradd --create-home --uid 10001 --shell /bin/bash finops \
 && mkdir -p /work /data \
 && chown finops:finops /work /data
USER finops
ENV HOME=/home/finops \
    STEAMPIPE_UPDATE_CHECK=false \
    POWERPIPE_UPDATE_CHECK=false \
    STEAMPIPE_TELEMETRY=none \
    POWERPIPE_TELEMETRY=none \
    SPENDLENS__HISTORY__PATH=/data/spendlens.db \
    PYTHONUNBUFFERED=1

# Bake in the AWS plugin and Steampipe's embedded Postgres + FDW, so containers start without downloads.
# Starting the service only initialises the local database; the AWS plugin makes no AWS calls until queried.
RUN steampipe plugin install "aws@${AWS_PLUGIN_VERSION}" \
 && steampipe service start --database-listen local \
 && steampipe service stop \
 && rm -rf "$HOME/.steampipe/logs/"*

COPY --chown=finops:finops --chmod=0755 docker/entrypoint.sh /usr/local/bin/spendlens-entrypoint
# licence texts and third-party notices (Steampipe/Powerpipe are AGPL-3.0, shipped unmodified)
COPY LICENSE NOTICE /usr/share/doc/spendlens/

WORKDIR /work
VOLUME ["/data"]
ENTRYPOINT ["tini", "--", "spendlens-entrypoint"]
CMD ["--help"]
