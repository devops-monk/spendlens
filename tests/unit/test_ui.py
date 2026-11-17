"""Live progress (spendlens.ui): only on an interactive terminal, ✓/✗ summary lines, nested steps."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from spendlens import ui

pytestmark = pytest.mark.unit


@pytest.fixture
def term(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(ui, "console", Console(file=buf, force_terminal=True, width=120, color_system=None))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SPENDLENS_NO_PROGRESS", raising=False)
    return buf


def test_done_and_failed_lines_with_nesting(term):
    with ui.activity("Running checks") as a:
        with ui.activity("Preparing connection"):
            pass
        a.done("rightsize: 135 results from 9 checks")
    with ui.activity("step") as b:
        b.done("waste · failed", failed=True)
    lines = [ln for ln in term.getvalue().splitlines() if "✓" in ln or "✗" in ln]
    assert lines[0].startswith("✓ rightsize: 135 results from 9 checks")
    assert lines[1].startswith("✗ waste · failed")
    assert ui._live is None  # the shared display is closed after the outermost step


def test_quiet_when_not_a_terminal_or_in_ci(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(ui, "console", Console(file=buf, force_terminal=False))
    with ui.activity("x") as a:
        a.done("never printed")
    assert buf.getvalue() == ""
    monkeypatch.setattr(ui, "console", Console(file=buf, force_terminal=True))
    monkeypatch.setenv("CI", "true")
    assert ui.enabled() is False


def test_no_done_line_when_the_step_raises(term):
    with pytest.raises(RuntimeError), ui.activity("boom", done="should not print"):
        raise RuntimeError("x")
    assert "should not print" not in term.getvalue()


def test_elapsed_format():
    assert ui.elapsed(8.42) == "8.4s" and ui.elapsed(133) == "2m 13s"
