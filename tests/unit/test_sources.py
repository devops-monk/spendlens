"""spendlens sources: one answer to "where does this data come from", shared by every feature (no AWS calls)."""

import json

from typer.testing import CliRunner

from spendlens import sources
from spendlens.cli import app
from spendlens.config import load_config


def test_tailpipe_ready_needs_enabled_installed_and_configured(tmp_path, monkeypatch) -> None:
    exe = tmp_path / "tailpipe"
    exe.write_text("#!/bin/sh\necho '[]'\n")
    exe.chmod(0o755)
    monkeypatch.setenv("TAILPIPE_INSTALL_DIR", str(tmp_path / "tp"))
    cfg = load_config(environ={}, sets=[f"graph.tailpipe_path={exe}"])
    assert not sources.tailpipe_ready(cfg)  # installed, nothing configured
    (tmp_path / "tp" / "config").mkdir(parents=True)
    (tmp_path / "tp" / "config" / "mine.tpc").write_text('partition "aws_cloudtrail_log" "ct" {}\n')
    assert sources.tailpipe_ready(cfg) and sources.tailpipe_ready(cfg, "cloudtrail")
    assert not sources.tailpipe_ready(cfg, "cur")
    off = load_config(environ={}, sets=[f"graph.tailpipe_path={exe}", "tailpipe.enabled=false"])
    assert not sources.tailpipe_ready(off)
    missing = load_config(environ={}, sets=["graph.tailpipe_path=/nonexistent/tailpipe"])
    assert not sources.tailpipe_ready(missing)


def test_sources_report_and_command() -> None:
    rows = sources.report(load_config(environ={}, sets=["graph.tailpipe_path=/nonexistent/tailpipe"]))
    by = {r["data"]: r for r in rows}
    assert by["CloudTrail events"]["used"] == "LookupEvents"
    assert by["Resource costs (graph)"]["used"] == "Price book"
    assert by["VPC flow logs"]["used"].startswith("none")
    cfg = load_config(environ={}, sets=["cloudtrail.log_group=ct-logs"])
    ct = next(r for r in sources.report(cfg) if r["data"] == "CloudTrail events")
    assert ct["used"].startswith("CloudWatch Logs") and ct["sources"][1]["detail"] == "log group ct-logs"
    r = CliRunner().invoke(app, ["sources", "--json"])
    assert r.exit_code == 0 and json.loads(r.stdout)[0]["data"] == "CloudTrail events"
    assert CliRunner().invoke(app, ["sources"]).exit_code == 0


def test_help_groups_commands_by_job() -> None:
    out = CliRunner().invoke(app, ["--help"], env={"COLUMNS": "120"}).stdout
    order = [out.index(p) for p in ("Save money", "Understand spend", "Govern and audit", "Ask",
                                    "Reports and notifications", "Data sources", "Setup")]
    assert order == sorted(order)
