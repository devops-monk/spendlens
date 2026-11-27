#!/usr/bin/env python3
"""Answer-quality evaluation of `spendlens ask` against a REAL AWS account, for any AI provider. Run it yourself.

For each question in tests/eval/questions.yaml, a reference query (read-only) gives the true answer on this account;
then `spendlens ask --json --fresh` answers the question with your configured provider and model. A question passes
when the answer contains every expected value. The report also shows tool steps, query errors, tokens and time,
so providers, models and prompt changes can be compared.

    python scripts/eval_ai.py --profile non-prd-fs                      # every question
    python scripts/eval_ai.py --only ec2-running,ssh-open --set llm.model=...
    python scripts/eval_ai.py --area security --min-score 0.8           # exit 1 below 80 %

Output: eval-<timestamp>/report.json and report.md. It contains account data: review before sharing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "tests" / "eval" / "questions.yaml"


def load_questions(path: Path = QUESTIONS) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text())


def norm(text: Any) -> str:
    return re.sub(r"(?<=\d),(?=\d{3})", "", str(text)).lower()


def contains(answer: str, value: Any) -> bool:
    """The expected value appears in the answer (numbers with or without thousands separators)."""
    a, v = norm(answer), norm(value)
    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        return re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", a) is not None
    return v in a


def truth(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from spendlens.config import load_config
    from spendlens.steampipe import SteampipeClient, SteampipeError

    out: dict[str, dict[str, Any]] = {}
    with SteampipeClient(load_config().steampipe, timeout_s=300) as c:
        for q in questions:
            try:
                rows = c.query(q["reference"])
                out[q["id"]] = {"row": rows[0] if rows else {}, "error": None}
            except SteampipeError as e:
                out[q["id"]] = {"row": {}, "error": str(e).splitlines()[0][:300]}
    return out


def ask(exe: str, question: str, sets: list[str], env: dict[str, str]) -> dict[str, Any]:
    cmd = [exe, "ask", "--json", "--fresh", "--live", question]
    for s in sets:
        cmd += ["--set", s]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900, check=False)
    secs = round(time.monotonic() - t0, 1)
    try:
        doc = json.loads(p.stdout[p.stdout.index("{"):])
    except ValueError:
        return {"answer": "", "error": (p.stderr or p.stdout)[-500:], "seconds": secs, "steps": []}
    return {**doc, "seconds": secs, "error": None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--region")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--area", help="inventory, security, cost, activity or network")
    ap.add_argument("--set", action="append", default=[], help="passed to spendlens ask, e.g. llm.model=...")
    ap.add_argument("--min-score", type=float, default=0.0, help="exit 1 when the pass rate is below this (0-1)")
    a = ap.parse_args()
    exe = shutil.which("spendlens")
    if not exe:
        print("spendlens is not on PATH")
        return 2
    if a.profile:
        os.environ["AWS_PROFILE"] = a.profile
    if a.region:
        os.environ["AWS_REGION"] = a.region
    qs = load_questions()
    if a.only:
        qs = [q for q in qs if q["id"] in a.only.split(",")]
    if a.area:
        qs = [q for q in qs if q["area"] == a.area]
    print(f"{len(qs)} question(s): reference queries first (read-only)")
    ref = truth(qs)
    out = Path(f"eval-{datetime.now(UTC):%Y%m%d-%H%M%S}")
    out.mkdir()
    results = []
    for q in qs:
        r = ref[q["id"]]
        if r["error"]:
            results.append({"id": q["id"], "status": "skipped", "why": f"reference query failed: {r['error']}"})
            print(f"  skip {q['id']:20} reference failed")
            continue
        got = ask(exe, q["question"], a.set, dict(os.environ))
        expected = {k: r["row"].get(k) for k in q["expect"]}
        missing = [k for k, v in expected.items() if v is None or not contains(got.get("answer", ""), v)]
        steps = got.get("steps") or []
        res = {"id": q["id"], "area": q["area"], "question": q["question"], "expected": expected,
               "status": "pass" if not missing and not got["error"] else "fail", "missing": missing,
               "answer": got.get("answer", "")[:2000], "error": got["error"], "seconds": got["seconds"],
               "tool_calls": len(steps), "tool_errors": sum(1 for s in steps if not s.get("ok")),
               "tools": [s.get("tool") for s in steps], "tokens": got.get("usage", {}),
               "hit_step_limit": got.get("hit_step_limit", False)}
        results.append(res)
        print(f"  {res['status']:4} {q['id']:20} {got['seconds']:6.1f}s {len(steps):2} calls "
              f"{res['tool_errors']} errors" + (f"  missing {missing}" if missing else ""))
    scored = [r for r in results if r["status"] in ("pass", "fail")]
    rate = sum(r["status"] == "pass" for r in scored) / len(scored) if scored else 0.0
    report = {"at": datetime.now(UTC).isoformat(), "sets": a.set, "pass_rate": round(rate, 3), "results": results}
    (out / "report.json").write_text(json.dumps(report, default=str, indent=2))
    lines = [f"# spendlens AI evaluation · {report['at'][:16]}", "", f"Pass rate: **{rate:.0%}** of {len(scored)}",
             "", "| id | area | result | calls | errors | seconds | missing |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r.get('area', '')} | {r['status']} | {r.get('tool_calls', '')} | "
              f"{r.get('tool_errors', '')} | {r.get('seconds', '')} | {', '.join(r.get('missing') or [])} |"
              for r in results]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(f"\npass rate {rate:.0%} ({len(scored)} scored) · {out}/report.md (contains account data)")
    return 1 if rate < a.min_score else 0


if __name__ == "__main__":
    sys.exit(main())
