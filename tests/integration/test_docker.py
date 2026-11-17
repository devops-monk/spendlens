"""Container packaging: the entrypoint's start/skip logic (run with fake binaries) and Dockerfile invariants.

The image itself is built and smoke-tested with `make docker-build docker-smoke` (offline, --network none).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
DOCKERFILE = ROOT / "Dockerfile"


@pytest.fixture
def fakebin(tmp_path: Path) -> Path:
    """Fake `steampipe` and `spendlens` that only log their arguments; spendlens exits with $FAKE_STATUS."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    (bindir / "steampipe").write_text(f'#!/usr/bin/env bash\necho "steampipe $*" >> "{log}"\n')
    (bindir / "spendlens").write_text(
        f'#!/usr/bin/env bash\necho "spendlens $*" >> "{log}"\nexit "${{FAKE_STATUS:-0}}"\n')
    for f in bindir.iterdir():
        f.chmod(0o755)
    return bindir


def run(fakebin: Path, *args: str, **env: str) -> tuple[int, list[str]]:
    if os.geteuid() == 0:
        pytest.skip("entrypoint deliberately refuses root")
    if not shutil.which("bash"):
        pytest.skip("bash not available")
    log = fakebin.parent / "calls.log"
    log.unlink(missing_ok=True)
    e = {"PATH": f"{fakebin}:/usr/bin:/bin", "HOME": str(fakebin.parent), **env}
    p = subprocess.run(["bash", str(ENTRYPOINT), *args], env=e, capture_output=True, text=True, timeout=30,
                       check=False)
    return p.returncode, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("args", [
    ["tags"],
    ["rightsize", "--min-saving", "20"],
    ["cost", "diff", "--mtd"],
    ["report", "--out", "reports"],
    ["ask", "--live", "which instances are idle?"],
    ["tags", "--source", "live"],
    ["dashboard", "-o", "r.html"],          # reuses today's runs, runs the rest live
    ["notify", "--slack", "--dry-run"],
    ["savings", "summary"],
])
def test_live_commands_start_and_stop_steampipe(fakebin: Path, args: list[str]) -> None:
    code, calls = run(fakebin, *args)
    assert code == 0
    assert calls[0] == "steampipe service start --database-listen local"
    assert calls[1] == "spendlens " + " ".join(args)
    assert calls[2].startswith("steampipe service stop")


@pytest.mark.parametrize("args", [
    [],
    ["--help"],
    ["tags", "--help"],
    ["tags", "--source", "demo"],
    ["waste", "--source=last"],
    ["ask", "summarise waste"],
    ["dashboard", "--source", "last", "-o", "r.html"],
    ["notify", "--slack", "--dry-run", "--source", "last"],
    ["savings", "summary", "--source=last"],
    ["history", "list"],
    ["params"],
    ["init"],
])
def test_offline_commands_skip_steampipe(fakebin: Path, args: list[str]) -> None:
    code, calls = run(fakebin, *args)
    assert code == 0
    assert calls == ["spendlens " + " ".join(args)]


def test_service_mode_overrides(fakebin: Path) -> None:
    _, calls = run(fakebin, "tags", SPENDLENS_STEAMPIPE_SERVICE="off")
    assert calls == ["spendlens tags"]
    _, calls = run(fakebin, "doctor", "--offline", "--source", "demo", SPENDLENS_STEAMPIPE_SERVICE="on")
    assert calls[0].startswith("steampipe service start")


def test_exit_code_passes_through_and_service_still_stops(fakebin: Path) -> None:
    code, calls = run(fakebin, "tags", "--fail-on-alarm", FAKE_STATUS="3")
    assert code == 3  # alarms found: the CI gate must survive the wrapper
    assert calls[-1].startswith("steampipe service stop")


def test_dockerfile_invariants() -> None:
    text = DOCKERFILE.read_text()
    # non-root runtime (Steampipe refuses root) and the entrypoint wired through tini
    users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
    assert users and users[-1] not in ("root", "0")
    assert 'ENTRYPOINT ["tini", "--", "spendlens-entrypoint"]' in text
    # every engine version pinned
    for arg in ("STEAMPIPE_VERSION", "POWERPIPE_VERSION", "AWS_PLUGIN_VERSION"):
        assert re.search(rf"^ARG {arg}=\d+\.\d+\.\d+$", text, re.MULTILINE), arg
    # no credentials or keys baked in
    assert not re.search(r"AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)\s*=", text)
    assert not re.search(r"(ANTHROPIC|OPENAI)_API_KEY\s*=", text)
    # the build context excludes everything but what the image needs
    ignore = (ROOT / ".dockerignore").read_text().split()
    assert ignore[0] == "*" or "*" in ignore
    assert {"!pyproject.toml", "!src/", "!docker/"} <= set(ignore)
