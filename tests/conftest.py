"""Test layers (selected with -m, or by directory):

  unit         pure logic, no subprocesses, no network           tests/unit
  integration  real components wired together, no cloud          tests/integration
               (CLI + SQLite history/demo DB, real/fake powerpipe, offline SQL verification)
  system       the whole product end-to-end against an AWS emulator (Moto) through an isolated
               Steampipe install + Powerpipe; opt-in: SPENDLENS_SYSTEM=1     tests/system

  make test             unit + integration
  make test-system      system (starts Moto + isolated Steampipe automatically)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from spendlens.models import Finding, Status

sys.path.insert(0, str(Path(__file__).parent))  # shared helpers (sqlcheck)

LAYERS = ("unit", "integration", "system")


def pytest_collection_modifyitems(config, items):
    for item in items:
        layer = next((p for p in Path(str(item.fspath)).parts if p in LAYERS), None)
        if layer:
            item.add_marker(getattr(pytest.mark, layer))


@pytest.fixture(autouse=True)
def _isolate(request, tmp_path, monkeypatch):
    """Unit/integration tests never touch the user's HOME, config, history or AWS."""
    if request.node.get_closest_marker("system"):
        return
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPENDLENS_CONFIG", raising=False)
    for k in [k for k in __import__("os").environ if k.startswith("SPENDLENS__")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("SPENDLENS__HISTORY__PATH", str(tmp_path / "history.db"))
    monkeypatch.setattr("spendlens.common.caller_identity", lambda: {"Account": "999", "Arn": "arn:test"})


def make_finding(control: str, resource: str | None = None, status: str = "alarm", **kw) -> Finding:
    dims = kw.pop("dims", {})
    return Finding(control=control, resource=resource or f"arn:{control}", status=Status(status),
                   dimensions={k: str(v) for k, v in dims.items()}, **kw)
