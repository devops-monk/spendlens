"""`spendlens report`: run every check, save JSON outputs, build the dashboard -- one command for CI and laptops.

Each step runs as its own `python -m spendlens ...` process, so one failing step (e.g. a missing permission for
Cost Optimization Hub) is reported but never stops the rest. Exit code 3 from a step means "findings/alarms" and
counts as success; anything else is a failed step and makes the report exit 1 (after writing everything it could).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from ..common import BRAND, ConfigOpt, ProfileOpt, SetOpt, Source, console, err
from ..plugins import Registry

# (name, cli args) -- every step writes <out>/<name>.json
STEPS: list[tuple[str, list[str]]] = [
    ("tags", ["tags", "--status", "all"]),
    ("rightsize", ["rightsize", "--status", "all"]),
    ("waste", ["waste", "--status", "all"]),
    ("cost_diff", ["cost", "diff", "--show", "all"]),
    ("cost_trend", ["cost", "trend"]),
    ("cost_showback", ["cost", "showback"]),
    ("anomalies", ["anomalies"]),
    ("forecast", ["forecast"]),
    ("savings_hub", ["savings", "hub"]),
    ("savings_commitments", ["savings", "commitments"]),
]
OK_CODES = {0, 3}  # 3 = alarms / flagged findings, which is a successful run


def report(
    out: Annotated[Path, typer.Option("--out", "-o", help="Directory for JSON outputs and report.html.")] = Path("reports"),
    only: Annotated[list[str] | None, typer.Option("--only", help="Run only these steps (repeatable).")] = None,
    skip: Annotated[list[str] | None, typer.Option("--skip", help="Skip these steps (repeatable).")] = None,
    source: Annotated[Source, typer.Option("--source", help="live = query AWS; demo = replay bundled sample data.")] = Source.live,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
    timeout: Annotated[int, typer.Option("--step-timeout", help="Seconds per step.")] = 1800,
    ai: Annotated[bool, typer.Option("--ai", help="Write the analysis in brief.html and review.html with your AI provider (llm.*); "
                                                  "default: the rule-based summary, nothing sent to an AI.")] = False,
) -> None:
    """Run all checks, write <out>/*.json, the savings summary, <out>/report.html, <out>/brief.html and <out>/review.html.

    Steps: tags, rightsize, waste, cost_diff, cost_trend, cost_showback, anomalies, forecast, savings_hub,
    savings_commitments (then savings summary, dashboard, brief and the full review from the saved runs).
    Examples:
      spendlens report --out reports
      spendlens report --skip savings_hub --skip cost_showback
      spendlens report --source demo --out /tmp/demo-report
      spendlens report --ai          # brief.html and review.html with an AI-written analysis
    """
    names = [n for n, _ in STEPS]
    for opt, vals in (("--only", only or []), ("--skip", skip or [])):
        bad = sorted(set(vals) - set(names))
        if bad:
            raise typer.BadParameter(f"Unknown step(s): {', '.join(bad)}. Steps: {', '.join(names)}", param_hint=opt)
    if source is Source.last:
        raise typer.BadParameter("report runs the checks: use --source live (default) or demo.", param_hint="--source")
    out.mkdir(parents=True, exist_ok=True)
    common: list[str] = []
    if config:
        common += ["--config", str(config)]
    for s in sets or []:
        common += ["--set", s]
    selected = [(n, a) for n, a in STEPS if (not only or n in only) and n not in (skip or [])]
    results = []
    total = len(selected) + 4  # + savings summary + dashboard + brief + review
    for i, (name, args) in enumerate(selected, 1):
        cmd = [sys.executable, "-m", "spendlens", *args, *common, "--source", source.value, "-f", "json",
               "-o", str(out / f"{name}.json")]
        if profile:
            cmd += ["--profile", profile]
        results.append(_run(name, cmd, timeout, step=f"{i}/{total}"))
    # derived artifacts read the history the steps just wrote (or the demo DB)
    hist = "demo" if source is Source.demo else "last"
    cfg_args = ["--config", str(config)] if config else []
    results.append(_run("savings_summary", [sys.executable, "-m", "spendlens", "savings", "summary", "--by", "none",
                                            "--top", "500", "--source", hist, *cfg_args, "-f", "json",
                                            "-o", str(out / "savings_summary.json")], timeout,
                        step=f"{total - 3}/{total}"))
    results.append(_run("dashboard", [sys.executable, "-m", "spendlens", "dashboard", "--source", hist, *cfg_args,
                                      "-o", str(out / "report.html")], timeout, step=f"{total - 2}/{total}"))
    set_args = [a for s in sets or [] for a in ("--set", s)]
    results.append(_run("brief", [sys.executable, "-m", "spendlens", "brief", "--source", hist, *cfg_args, *set_args,
                                  "--ai" if ai else "--no-ai", "-o", str(out / "brief.html")], timeout,
                        step=f"{total - 1}/{total}"))
    # the full review reuses the brief's AI analysis from the cache (same data), so it costs no extra tokens
    results.append(_run("review", [sys.executable, "-m", "spendlens", "review", "--source", hist, *cfg_args, *set_args,
                                   "--ai" if ai else "--no-ai", "-o", str(out / "review.html")], timeout,
                        step=f"{total}/{total}"))
    (out / "report-steps.json").write_text(json.dumps(results, indent=2))

    t = Table(title=f"spendlens report → {out}", header_style=f"bold {BRAND}")
    for c in ("step", "result", "seconds", "detail"):
        t.add_column(c, overflow="fold")
    for r in results:
        status = {"ok": "[green]ok[/]", "alarms": "[yellow]ok · findings[/]", "failed": "[red]failed[/]"}[r["result"]]
        t.add_row(r["step"], status, f"{r['seconds']:.1f}", r["detail"])
    console.print(t)
    failed = [r["step"] for r in results if r["result"] == "failed"]
    if failed:
        err.print(f"[red]{len(failed)} step(s) failed:[/] {', '.join(failed)} (other outputs were still written)")
        raise typer.Exit(1)


def _run(name: str, cmd: list[str], timeout: int, step: str = "") -> dict:
    from ..ui import activity

    t0 = time.monotonic()
    label = name.replace("_", " ")
    with activity(f"[dim]{step}[/] [bold]{label}[/]") as act:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            code, stderr = p.returncode, p.stderr
        except subprocess.TimeoutExpired:
            code, stderr = -1, f"timed out after {timeout}s"
        result = "ok" if code == 0 else "alarms" if code == 3 else "failed"
        note = "[red]failed[/]" if result == "failed" else "[yellow]findings[/]" if result == "alarms" else "ok"
        act.done(f"[dim]{step}[/] {label} · {note}", failed=result == "failed")
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    detail = (lines[-1] if lines else "")[:300]
    return {"step": name, "exit_code": code, "result": result, "seconds": round(time.monotonic() - t0, 1),
            "detail": detail}


def register(reg: Registry) -> None:
    reg.add_command(report, "report")
