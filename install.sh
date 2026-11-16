#!/bin/sh
# spendlens installer — one command for macOS and Linux:
#
#   curl -fsSL https://spendlens.devops-monk.com/install.sh | sh
#
# Installs, without sudo and without touching other Python environments:
#   1. Steampipe + Powerpipe (pinned): via Homebrew when available, otherwise into ~/.local/bin
#   2. the Steampipe AWS plugin (pinned)
#   3. spendlens with the AI extras, isolated: pipx > uv tool > a private venv
#
# It installs tools only. It never reads AWS credentials or calls AWS.
#
# Options (environment variables):
#   SPENDLENS_REF=main            git branch/tag to install
#   SPENDLENS_EXTRAS=ai           pip extras ("" for none)
#   SPENDLENS_SKIP_ENGINES=1      don't install Steampipe/Powerpipe/plugin
#   SPENDLENS_BIN_DIR=~/.local/bin
#   SPENDLENS_PACKAGE=<path|url>  install spendlens from this wheel/sdist instead of GitHub
#   SPENDLENS_DRY_RUN=1           print what would run, change nothing
set -eu

REPO="https://github.com/devops-monk/spendlens"
STEAMPIPE_VERSION="2.5.0"
POWERPIPE_VERSION="1.6.0"
AWS_PLUGIN_VERSION="1.35.0"
REF="${SPENDLENS_REF:-main}"
EXTRAS="${SPENDLENS_EXTRAS-ai}"
BIN_DIR="${SPENDLENS_BIN_DIR:-$HOME/.local/bin}"
DRY="${SPENDLENS_DRY_RUN:-0}"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY" = "1" ]; then printf '    + %s\n' "$*"; else "$@"; fi; }
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(id -u)" = "0" ] && die "run as your normal user, not root (Steampipe refuses to run as root)"
have curl || die "curl is required"

case "$(uname -s)" in
  Darwin) OS=darwin ;;
  Linux)  OS=linux ;;
  *) die "unsupported OS: $(uname -s); use the Docker image instead: https://spendlens.devops-monk.com/docs/docker/" ;;
esac
case "$(uname -m)" in
  x86_64|amd64)  ARCH=amd64 ;;
  arm64|aarch64) ARCH=arm64 ;;
  *) die "unsupported CPU: $(uname -m)" ;;
esac

# ---------------------------------------------------------------- python
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || die "Python 3.11+ is required (macOS: brew install python; Debian/Ubuntu: apt install python3 python3-venv)"

run mkdir -p "$BIN_DIR"

# ---------------------------------------------------------------- engines
fetch_bin() {  # url, archive kind, binary name
  tmp=$(mktemp -d)
  run curl -fsSL "$1" -o "$tmp/pkg"
  if [ "$DRY" != "1" ]; then
    case "$2" in
      zip) have unzip || die "unzip is required"; unzip -q "$tmp/pkg" -d "$tmp/x" ;;
      tgz) mkdir -p "$tmp/x" && tar -xzf "$tmp/pkg" -C "$tmp/x" ;;
    esac
    found=$(find "$tmp/x" -type f -name "$3" | head -1)
    [ -n "$found" ] || die "$3 not found in $1"
    install -m 0755 "$found" "$BIN_DIR/$3"
  fi
  rm -rf "$tmp"
}

if [ "${SPENDLENS_SKIP_ENGINES:-0}" != "1" ]; then
  if have steampipe && have powerpipe; then
    say "Steampipe and Powerpipe already installed ($(steampipe --version 2>/dev/null | head -1), $(powerpipe --version 2>/dev/null | head -1))"
  elif have brew; then
    say "Installing Steampipe and Powerpipe with Homebrew"
    run brew install turbot/tap/steampipe turbot/tap/powerpipe
  else
    say "Installing Steampipe v$STEAMPIPE_VERSION and Powerpipe v$POWERPIPE_VERSION into $BIN_DIR"
    if [ "$OS" = darwin ]; then sp_kind=zip; sp_ext=zip; else sp_kind=tgz; sp_ext=tar.gz; fi
    have steampipe || fetch_bin "https://github.com/turbot/steampipe/releases/download/v$STEAMPIPE_VERSION/steampipe_${OS}_${ARCH}.$sp_ext" "$sp_kind" steampipe
    have powerpipe || fetch_bin "https://github.com/turbot/powerpipe/releases/download/v$POWERPIPE_VERSION/powerpipe.${OS}.${ARCH}.tar.gz" tgz powerpipe
  fi
  SP=$(command -v steampipe 2>/dev/null || echo "$BIN_DIR/steampipe")
  say "Installing the Steampipe AWS plugin v$AWS_PLUGIN_VERSION"
  run "$SP" plugin install "aws@$AWS_PLUGIN_VERSION"
fi

# ---------------------------------------------------------------- spendlens
# A source tarball (works for branches and tags) avoids needing git on the machine.
SRC="$REPO/archive/$REF.tar.gz"
if [ -n "$EXTRAS" ]; then SPEC="spendlens[$EXTRAS] @ $SRC"; else SPEC="spendlens @ $SRC"; fi
# SPENDLENS_PACKAGE: install from a wheel/sdist path or URL instead (e.g. an internal mirror)
if [ -n "${SPENDLENS_PACKAGE:-}" ]; then
  case "$SPENDLENS_PACKAGE" in
    *://*) ;;
    /*) SPENDLENS_PACKAGE="file://$SPENDLENS_PACKAGE" ;;
    *)  SPENDLENS_PACKAGE="file://$(pwd)/$SPENDLENS_PACKAGE" ;;
  esac
  if [ -n "$EXTRAS" ]; then SPEC="spendlens[$EXTRAS] @ $SPENDLENS_PACKAGE"; else SPEC="spendlens @ $SPENDLENS_PACKAGE"; fi
fi
if have pipx; then
  say "Installing spendlens with pipx"
  run pipx install --force --python "$PY" "$SPEC"
elif have uv; then
  say "Installing spendlens with uv"
  run uv tool install --force --python "$PY" "$SPEC"
else
  VENV="${XDG_DATA_HOME:-$HOME/.local/share}/spendlens/venv"
  say "Installing spendlens into $VENV"
  run "$PY" -m venv "$VENV"
  run "$VENV/bin/python" -m pip install -q --upgrade pip
  run "$VENV/bin/python" -m pip install -q --upgrade "$SPEC"
  run ln -sf "$VENV/bin/spendlens" "$BIN_DIR/spendlens"
fi

# ---------------------------------------------------------------- done
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    # shellcheck disable=SC2088  # "~/..." is display text for the user, not a path we use
    case "$(basename "${SHELL:-sh}")" in
      zsh) rc="~/.zshrc" ;;
      bash) rc="~/.bashrc" ;;
      *) rc="~/.profile" ;;
    esac
    warn "$BIN_DIR is not on your PATH. Add it:  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> $rc  (then open a new terminal)" ;;
esac

if [ "$DRY" != "1" ]; then
  SPENDLENS=$(command -v spendlens 2>/dev/null || echo "$BIN_DIR/spendlens")
  "$SPENDLENS" --help >/dev/null 2>&1 || die "spendlens did not install correctly"
fi
say "Done. Next:"
cat <<'EOF'
    spendlens tags --source demo --summary   # try it on sample data, no AWS needed
    spendlens init                            # write finops.yaml with your standards
    spendlens doctor                          # check AWS access and setup
EOF