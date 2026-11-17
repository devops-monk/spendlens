"""Guard: tests can never reach the user's real Steampipe service (it runs with their AWS credentials)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from spendlens.common import load
from spendlens.steampipe import build_dsn

pytestmark = pytest.mark.integration


def test_config_points_at_a_dead_port():
    assert ":9/" in build_dsn(load(None).steampipe)


def test_a_live_child_process_cannot_reach_steampipe(tmp_path):
    p = subprocess.run([sys.executable, "-m", "spendlens", "waste", "--source", "live", "-f", "json", "-o",
                        str(tmp_path / "w.json")], capture_output=True, text=True, timeout=120, check=False)
    assert p.returncode not in (0, 3), p.stderr[-500:]
    assert not (tmp_path / "w.json").exists()
