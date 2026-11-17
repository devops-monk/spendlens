"""notify (dry-run / localhost webhook), report (--source demo, no AWS) and the CI templates."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import typer.main
import yaml
from typer.testing import CliRunner

from spendlens.cli import app, ci_template_text
from spendlens.history import HistoryStore
from spendlens.models import Finding, Status

runner = CliRunner()


# ------------------------------------------------------------------ notify


def test_notify_dry_run_both_targets():
    r = runner.invoke(app, ["notify", "--slack", "--teams", "--dry-run", "--source", "demo", "--link", "https://ci/r"])
    assert r.exit_code == 0, r.output
    docs = [json.loads(m) for m in re.findall(r"^\{.*?^\}", r.stdout, re.MULTILINE | re.DOTALL)]
    assert [d["target"] for d in docs] == ["Slack", "Teams"]
    assert "DEMO DATA" in json.dumps(docs[0]["payload"]) and "new alarm" in json.dumps(docs[0]["payload"])


def test_notify_requires_target_and_env():
    assert runner.invoke(app, ["notify", "--source", "demo"]).exit_code == 2
    r = runner.invoke(app, ["notify", "--slack", "--source", "demo"])  # no webhook env var set
    assert r.exit_code == 1 and "SPENDLENS_SLACK_WEBHOOK" in r.output


def test_notify_only_if_changes_stays_quiet(tmp_path):
    with HistoryStore(tmp_path / "history.db") as s:
        for _ in range(2):
            s.save_run("tags", [Finding(control="c", resource="r", status=Status.OK)])
    r = runner.invoke(app, ["notify", "--slack", "--only-if-changes", "--source", "last"])
    assert r.exit_code == 0 and "no message sent" in r.output


def test_notify_rejects_non_https_webhook(monkeypatch):
    monkeypatch.setenv("SPENDLENS_SLACK_WEBHOOK", "http://hooks.example.com/x")
    r = runner.invoke(app, ["notify", "--slack", "--source", "demo"])
    assert r.exit_code == 1 and "https" in r.output


def test_notify_posts_to_local_webhook(monkeypatch):
    received = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["content-length"])))))
            self.send_response(200)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("SPENDLENS_TEAMS_WEBHOOK", f"http://127.0.0.1:{srv.server_address[1]}/hook/SECRET")
        r = runner.invoke(app, ["notify", "--teams", "--source", "demo"])
    finally:
        srv.shutdown()
        srv.server_close()
    assert r.exit_code == 0, r.output
    assert received[0][0] == "/hook/SECRET" and received[0][1]["type"] == "message"
    assert "SECRET" not in r.output  # webhook secret path is redacted in logs


# ------------------------------------------------------------------ report


def test_report_demo_writes_everything(tmp_path):
    out = tmp_path / "reports"
    r = runner.invoke(app, ["report", "--source", "demo", "--out", str(out)])
    assert r.exit_code == 0, r.output
    steps = json.loads((out / "report-steps.json").read_text())
    assert {s["step"] for s in steps} >= {"tags", "waste", "cost_diff", "forecast", "dashboard", "savings_summary", "brief"}
    assert all(s["result"] in ("ok", "alarms") for s in steps), steps
    assert json.loads((out / "waste.json").read_text())["rows"]
    assert (out / "report.html").read_text().startswith("<!doctype html>")
    assert "executive brief" in (out / "brief.html").read_text() and "Automatic summary" in (out / "brief.html").read_text()


def test_report_continues_past_failures(tmp_path):
    out = tmp_path / "reports"
    # an invalid override makes every step fail; report still finishes, records it and exits 1
    r = runner.invoke(app, ["report", "--source", "demo", "--out", str(out), "--only", "tags", "--only", "waste",
                            "--set", "sizing.ec2_cpu_low=999"])
    assert r.exit_code == 1
    steps = {s["step"]: s for s in json.loads((out / "report-steps.json").read_text())}
    assert steps["tags"]["result"] == "failed" and steps["waste"]["result"] == "failed"
    assert steps["dashboard"]["result"] == "ok"  # derived artifacts still produced


def test_report_bad_step_name():
    assert runner.invoke(app, ["report", "--only", "nope"]).exit_code == 2


# ------------------------------------------------------------------ CI templates


@pytest.mark.parametrize(("kind", "path"), [("gitlab", ".gitlab-ci.yml"), ("github", ".github/workflows/spendlens.yml")])
def test_init_ci_writes_valid_yaml(tmp_path, kind, path):
    r = runner.invoke(app, ["init", "--ci", kind])
    assert r.exit_code == 0, r.output
    doc = yaml.safe_load((tmp_path / path).read_text())
    assert ("finops" in doc) if kind == "gitlab" else ("finops" in doc["jobs"])
    assert runner.invoke(app, ["init", "--ci", kind]).exit_code == 1  # no overwrite
    assert runner.invoke(app, ["init", "--ci", "jenkins"]).exit_code == 2




@pytest.mark.parametrize("kind", ["gitlab", "github"])
def test_ci_templates_only_use_real_commands_and_flags(kind):
    """Guards against the templates drifting from the CLI: every `spendlens <cmd> --flag` must exist."""
    text = ci_template_text(kind)
    root = typer.main.get_command(app)
    found = 0
    for line in text.splitlines():
        if line.lstrip().startswith("#") or "spendlens" not in line or "spendlens[" in line:
            continue
        # the command, not a path like ".spendlens" or "spendlens-history"
        for m in re.finditer(r"(?<![\w./-])spendlens\s+([a-z][a-z-]*(?:\s+[a-z][a-z-]*)?)([^;|&\n]*)", line):
            words = m.group(1).split()
            # resolve against the real command tree, not --help text (Rich colours help output on CI,
            # and ANSI codes split flags like "--out")
            cmd = root.commands.get(words[0])
            assert cmd is not None, (kind, words[0])
            subs = getattr(cmd, "commands", None) or {}
            if len(words) == 2 and words[1] in subs:
                cmd = subs[words[1]]
            flags = {o for prm in cmd.params for o in (*getattr(prm, "opts", ()), *getattr(prm, "secondary_opts", ()))}
            for flag in re.findall(r"(--[a-z][a-z-]*)", m.group(2)):
                assert flag in flags, (kind, words, flag)
            found += 1
    assert found >= 3
