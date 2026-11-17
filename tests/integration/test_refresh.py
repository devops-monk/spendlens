"""dashboard / notify / savings summary: reuse today's runs, run the rest live (the live runner is faked)."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from spendlens import refresh
from spendlens.cli import app
from spendlens.features.dashboard import FEATURES
from spendlens.history import demo_db_path

pytestmark = pytest.mark.integration
runner = CliRunner()


@pytest.fixture
def history(tmp_path, monkeypatch):
    """The demo history, with tags and waste re-dated to today; every live run is recorded, never executed."""
    db = tmp_path / "h.db"
    shutil.copy(demo_db_path(), db)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with sqlite3.connect(db) as c:
        for f in ("tags", "waste"):
            c.execute("update runs set started_at = ? where id = (select max(id) from runs where feature = ?)", (now, f))
    monkeypatch.setenv("SPENDLENS__HISTORY__PATH", str(db))
    calls: list[str] = []
    monkeypatch.setattr(refresh, "run_live", lambda feat, *a, **k: calls.append(feat) or {"result": "ok", "detail": ""})
    return calls


def test_every_live_feature_has_a_refresh_command():
    assert set(FEATURES) <= set(refresh.REFRESH_ARGS)


def test_dashboard_runs_only_what_was_not_checked_today(history, tmp_path):
    r = runner.invoke(app, ["dashboard", "-o", str(tmp_path / "d.html")])
    assert r.exit_code == 0, r.output
    assert history == [f for f in FEATURES if f not in ("tags", "waste")]
    assert "not run today" in r.output and (tmp_path / "d.html").stat().st_size > 10_000


def test_dashboard_refresh_and_last(history, tmp_path):
    assert runner.invoke(app, ["dashboard", "--refresh", "-o", str(tmp_path / "d.html")]).exit_code == 0
    assert history == list(FEATURES)
    history.clear()
    assert runner.invoke(app, ["dashboard", "--source", "last", "-o", str(tmp_path / "d.html")]).exit_code == 0
    assert history == []


def test_notify_reuses_todays_runs(history):
    r = runner.invoke(app, ["notify", "--slack", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "tags" not in history and "cost_diff" in history
    history.clear()
    assert runner.invoke(app, ["notify", "--slack", "--dry-run", "--source", "last"]).exit_code == 0
    assert history == []


def test_a_failed_section_is_reported_and_the_old_run_used(tmp_path, monkeypatch):
    db = tmp_path / "h.db"
    shutil.copy(demo_db_path(), db)
    monkeypatch.setenv("SPENDLENS__HISTORY__PATH", str(db))
    monkeypatch.setattr(refresh, "run_live", lambda feat, *a, **k: {"result": "failed", "detail": "AccessDenied"})
    r = runner.invoke(app, ["dashboard", "-o", str(tmp_path / "d.html")])
    assert r.exit_code == 0 and "could not run" in r.output and "AccessDenied" in r.output
    assert "Using older saved runs" in r.output
