"""install.sh: lint-clean POSIX sh, and a dry run that plans the right steps and changes nothing."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[2] / "install.sh"


def _fake(bindir: Path, name: str, body: str = "exit 0") -> None:
    f = bindir / name
    f.write_text(f"#!/bin/sh\n{body}\n")
    f.chmod(0o755)


def _dry_run(tmp_path: Path, *tools: str, **env: str) -> subprocess.CompletedProcess[str]:
    if os.geteuid() == 0:
        pytest.skip("installer refuses root")
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for t in tools:
        _fake(bindir, t, 'echo "$0 $*" >> "$HOME/calls.log"; echo "Fake v1"')
    home = tmp_path / "home"
    home.mkdir()
    py = Path(shutil.which("python3") or "/usr/bin/python3").parent
    path = f"{bindir}:{py}:/usr/bin:/bin"
    return subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, timeout=60, check=False,
                          env={"PATH": path, "HOME": str(home), "SPENDLENS_DRY_RUN": "1", **env})


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck_clean() -> None:
    p = subprocess.run(["shellcheck", "-s", "sh", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stdout


def test_dry_run_with_existing_engines_and_pipx(tmp_path: Path) -> None:
    p = _dry_run(tmp_path, "steampipe", "powerpipe", "pipx")
    assert p.returncode == 0, p.stderr
    out = p.stdout
    assert "already installed" in out
    assert "plugin install aws@1.35.0" in out
    assert "pipx install --force" in out and "spendlens[ai] @ https://github.com/devops-monk/spendlens/archive/main.tar.gz" in out
    calls = (tmp_path / "home" / "calls.log").read_text().splitlines()
    assert calls and all(c.endswith(" --version") for c in calls), calls  # dry run: version checks only
    assert not (tmp_path / "home" / ".local").exists()


def test_dry_run_without_engines_downloads_pinned_releases(tmp_path: Path) -> None:
    p = _dry_run(tmp_path, SPENDLENS_EXTRAS="", SPENDLENS_REF="v0.1.0")
    assert p.returncode == 0, p.stderr
    assert "steampipe/releases/download/v2.5.0/" in p.stdout
    assert "powerpipe/releases/download/v1.6.0/" in p.stdout
    assert "spendlens @ https://github.com/devops-monk/spendlens/archive/v0.1.0.tar.gz" in p.stdout
    assert "-m venv" in p.stdout  # no pipx/uv on PATH -> private venv


def test_package_override_becomes_file_url(tmp_path: Path) -> None:
    p = _dry_run(tmp_path, "steampipe", "powerpipe", "pipx", SPENDLENS_PACKAGE="/opt/w/spendlens-0.1.0-py3-none-any.whl")
    assert "spendlens[ai] @ file:///opt/w/spendlens-0.1.0-py3-none-any.whl" in p.stdout
