"""After an answer: the spendlens (or plain SQL) commands that get the same data without the AI.

Built from the tool calls that produced the answer, not guessed by the model: each successful data call maps to
the command that runs the same query or check. Running those commands costs no AI tokens, works in scripts and CI,
and gives the same rows every time.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

MAX_HINTS = 4
SKIP = {"aws_find_tables", "aws_describe_table", "aws_check_query", "aws_query_result", "read_file", "write_file"}


def _q(v: Any) -> str:
    return shlex.quote(str(v))


def _sql(sql: str) -> str:
    return _q(re.sub(r"\s+", " ", sql or "").strip())


def _cloudtrail(a: dict[str, Any]) -> str:
    parts = ["spendlens cloudtrail events"]
    if a.get("preset"):
        parts += ["--preset", _q(a["preset"])]
    if a.get("from"):
        parts += ["--from", _q(a["from"])]
        if a.get("to"):
            parts += ["--to", _q(a["to"])]
    elif a.get("hours"):
        h = float(a["hours"])
        parts += ["--days", f"{h / 24:g}"] if h % 24 == 0 else ["--hours", f"{h:g}"]
    for flag, key in (("--user", "user"), ("--service", "service"), ("--resource", "resource"),
                      ("--resource-type", "resource_type"), ("--access-key", "access_key"),
                      ("--source-ip", "source_ip"), ("--region", "region")):
        if a.get(key):
            parts += [flag, _q(a[key])]
    events = a.get("events") or ([a["event"]] if a.get("event") else [])
    for e in events if isinstance(events, list) else [events]:
        parts += ["-e", _q(e)]
    for e in a.get("errors") or []:
        parts += ["--error", _q(e)]
    if a.get("errors_only"):
        parts.append("--errors-only")
    if a.get("reads") and a["reads"] != "writes":
        parts += ["--reads", _q(a["reads"])]
    if a.get("include_noise"):
        parts.append("--include-noise")
    src = {"steampipe": "aws", "live": "aws"}.get(str(a.get("source") or ""), a.get("source"))
    if src and src != "auto":
        parts += ["--source", _q(src)]
    return " ".join(parts)


def _export(a: dict[str, Any]) -> str | None:
    name, engine, sql = a.get("name") or "export.csv", a.get("engine"), a.get("sql") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "csv"
    if engine == "aws":
        fmt = ext if ext in ("csv", "json", "html") else "csv"
        out = name if ext == fmt else f"{name.rsplit('.', 1)[0]}.csv"
        return f"spendlens query {_sql(sql)} --limit 100000 -f {fmt} -o {_q(out)}"
    if engine == "history":
        return f'sqlite3 -header -csv "$(spendlens history path)" {_sql(sql)} > {_q(name.rsplit(".", 1)[0] + ".csv")}'
    if engine == "tailpipe":
        return f"tailpipe query {_sql(sql)} --output csv > {_q(name.rsplit('.', 1)[0] + '.csv')}"
    return None


def command_for(tool: str, a: dict[str, Any], source: str = "last") -> str | None:
    """The command that gets what one tool call got, or None when there is none worth showing."""
    src = "demo" if source == "demo" else "last"
    if tool == "aws_query":
        return f"spendlens query {_sql(a.get('sql', ''))}"
    if tool == "cloudtrail_events":
        return _cloudtrail(a)
    if tool == "export_query":
        return _export(a)
    if tool == "security_findings":
        if a.get("image"):
            base = f"spendlens security image {_q(a['image'])}"
        elif a.get("function"):
            base = f"spendlens security lambda {_q(a['function'])}"
        elif a.get("instance"):
            base = f"spendlens security instance {_q(a['instance'])}"
        elif a.get("resource"):
            base = f"spendlens security resource {_q(a['resource'])}"
        elif a.get("sources") == ["detections"]:
            return "spendlens security detections" + (f" --days {int(a['days'])}" if a.get("days") else "")
        else:
            base = "spendlens security findings"
            if a.get("resource_type"):
                base += f" --type {_q(a['resource_type'])}"
            for k in ("cve", "package", "region"):
                if a.get(k):
                    base += f" --{k} {_q(a[k])}"
            if a.get("exploitable"):
                base += " --exploitable"
            for src in a.get("sources") or []:
                base += f" --source {_q(src)}"
        if a.get("min_severity") and a["min_severity"].upper() != "LOW":
            base += f" --severity {a['min_severity'].lower()}"
        if a.get("fixable") and not (a.get("instance") or a.get("resource")):
            base += " --fixable"  # instance and resource show every finding
        return base
    if tool == "security_status":
        return "spendlens security status"
    if tool == "tailpipe_query":
        return f"tailpipe query {_sql(a['sql'])}" if a.get("sql") else None
    if tool == "history_query":
        if src == "demo":
            return None  # the bundled demo database has no path of its own to point at
        return f'sqlite3 -header -column "$(spendlens history path)" {_sql(a.get("sql", ""))}'
    if tool == "history_overview":
        return "spendlens history list"
    if tool == "history_cost":
        svc = " --group-by service" if a.get("service") else ""
        return f"spendlens cost trend --source {src}{svc}"
    if tool == "resource_context":
        return f"spendlens graph show {_q(a.get('resource', ''))} --source {src}" if a.get("resource") else None
    if tool == "cleanup_groups":
        extra = f" --min-saving {a['min_saving']:g}" if a.get("min_saving") else ""
        return f"spendlens graph orphans --source {src}{extra}"
    if tool == "suggested_owners":
        extra = "".join(f" --key {_q(k)}" for k in ([a["key"]] if isinstance(a.get("key"), str) else a.get("key") or []))
        if a.get("min_confidence") and a["min_confidence"] != "low":
            extra += f" --min-confidence {_q(a['min_confidence'])}"
        return f"spendlens graph owners --source {src}{extra}"
    if tool == "traffic_paths":
        extra = f" --path {_q(a['path'])}" if a.get("path") else ""
        return f"spendlens graph traffic --source {src}{extra}"
    return None


def commands(steps: list[Any], source: str = "last", limit: int = MAX_HINTS) -> list[str]:
    """The commands behind an answer: successful data calls, newest last, without repeats, at most `limit`."""
    out: list[str] = []
    for s in steps:
        tool = s.tool if hasattr(s, "tool") else s.get("tool")
        ok = s.ok if hasattr(s, "ok") else s.get("ok")
        args = (s.arguments if hasattr(s, "arguments") else s.get("arguments")) or {}
        if not ok or tool in SKIP:
            continue
        cmd = command_for(str(tool), args if isinstance(args, dict) else {}, source)
        if cmd and cmd not in out:
            out.append(cmd)
    return out[-limit:]
