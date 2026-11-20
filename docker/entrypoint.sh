#!/usr/bin/env bash
# Container entrypoint: start the local Steampipe service when the command needs live data, run spendlens,
# stop the service, and pass spendlens's exit code through (3 = alarms found, used as a CI gate).
#
#   SPENDLENS_STEAMPIPE_SERVICE=auto (default) | on | off
#     off: use an external Steampipe (set steampipe.* in finops.yaml or SPENDLENS__STEAMPIPE__*)
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  echo "spendlens: Steampipe refuses to run as root; do not override the image user with --user 0." >&2
  exit 2
fi

needs_service() {
  # Commands that never query Steampipe.
  case "${1:-}" in
    ""|-h|--help|--version|--install-completion|--show-completion|init|params|config|plugins|history|schema|cache|session)
      return 1 ;;
  esac
  # The graph's explore commands read the saved graph.
  if [ "${1:-}" = graph ]; then case "${2:-}" in show|impact|stats|owners|traffic) return 1 ;; esac; fi
  for a in "$@"; do case "$a" in -h|--help) return 1 ;; esac; done
  # Replays of saved or demo data never query Steampipe.
  local prev=""
  for a in "$@"; do
    case "$a" in --source=demo|--source=last) return 1 ;; esac
    if [ "$prev" = "--source" ] && { [ "$a" = "demo" ] || [ "$a" = "last" ]; }; then return 1; fi
    prev="$a"
  done
  # `ask` only reaches Steampipe with --live.
  if [ "${1:-}" = "ask" ]; then
    for a in "$@"; do [ "$a" = "--live" ] && return 0; done
    return 1
  fi
  return 0
}

mode="${SPENDLENS_STEAMPIPE_SERVICE:-auto}"
started=0
if [ "$mode" = "on" ] || { [ "$mode" = "auto" ] && needs_service "$@"; }; then
  steampipe service start --database-listen local >/dev/null
  started=1
fi

status=0
spendlens "$@" || status=$?

if [ "$started" = "1" ]; then
  steampipe service stop >/dev/null 2>&1 || steampipe service stop --force >/dev/null 2>&1 || true
fi
exit "$status"
