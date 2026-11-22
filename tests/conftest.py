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

import os
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
    for k in [k for k in os.environ if k.startswith("SPENDLENS__")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("SPENDLENS__HISTORY__PATH", str(tmp_path / "history.db"))
    # never reach a real cloud or paid API from unit/integration tests, even by accident
    for k in ("AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_SESSION_TOKEN", "ANTHROPIC_API_KEY",
              "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    # any accidental AWS SDK/CLI call goes to a dead local port, never to real AWS
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:9")
    # Steampipe runs with the user's own credentials, so it must be unreachable too -- including from child
    # processes (`spendlens report` steps, `savings summary` refreshes), which never see in-process fakes.
    monkeypatch.setenv("SPENDLENS__STEAMPIPE__DSN", "postgresql://steampipe:testing@127.0.0.1:9/steampipe")
    monkeypatch.setenv("STEAMPIPE_DATABASE_PASSWORD", "testing")
    # a Tailpipe installed on this machine must not be used either: only tests that bring a fake one see one
    monkeypatch.setenv("SPENDLENS__GRAPH__TAILPIPE_PATH", "/nonexistent/tailpipe")
    monkeypatch.setattr("spendlens.ai.tailpipe_tools.available",
                        lambda path: os.path.isabs(path) and os.access(path, os.X_OK))
    monkeypatch.delenv("STEAMPIPE_INSTALL_DIR", raising=False)
    monkeypatch.setattr("spendlens.common.caller_identity", lambda: {"Account": "999", "Arn": "arn:test"})


def make_finding(control: str, resource: str | None = None, status: str = "alarm", **kw) -> Finding:
    dims = kw.pop("dims", {})
    return Finding(control=control, resource=resource or f"arn:{control}", status=Status(status),
                   dimensions={k: str(v) for k, v in dims.items()}, **kw)
