#!/usr/bin/env python3
"""Build the static website (website/ -> website/dist/).

    python scripts/build_site.py            # build
    python scripts/build_site.py --check    # build and verify every internal link resolves

Docs = website/content/docs/*.md + the repo's docs/{permissions,ci,controls,control-mapping}.md (single source of truth)
+ a CLI reference generated from the live command tree. Stats on the home page are computed from the code.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "website"))

import diagrams
import markdown
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pygments.formatters import HtmlFormatter

from spendlens import __version__

SITE = ROOT / "website"
DIST = SITE / "dist"
SITE_URL = "https://spendlens.devops-monk.com"
REPO_URL = "https://github.com/devops-monk/spendlens"

NAV = [
    ("Get started", [("", "Introduction"), ("getting-started", "Getting started"), ("configuration", "Configuration"),
                     ("permissions", "IAM permissions"), ("docker", "Docker"), ("ways-to-run", "Ways to run")]),
    ("Features", [("tags", "Tag compliance"), ("rightsize-waste", "Rightsizing & waste"), ("cost", "Cost analysis"),
                  ("anomalies-forecast", "Anomalies & forecast"), ("savings", "Savings"),
                  ("dashboard", "Dashboard & formats"), ("history", "History & sources")]),
    ("AI", [("ai-assistant", "AI assistant")]),
    ("Automation", [("report-notify", "Reports & notifications"), ("ci", "CI/CD")]),
    ("Reference", [("cli", "Commands & options"), ("controls", "Controls catalog"), ("control-mapping", "Control mapping"),
                   ("extending", "Extending"), ("development", "Development & testing")]),
]
REPO_DOCS = {"docker": "docker.md", "permissions": "permissions.md", "ci": "ci.md", "controls": "controls.md",
             "control-mapping": "control-mapping.md"}

ICONS = {  # 24x24 monoline glyphs, currentColor
    "tag": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12V4h8l9 9-8 8z"/><circle cx="7.5" cy="8.5" r="1.4"/></svg>',
    "resize": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 15l6-6M10 9h5v5"/></svg>',
    "trash": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
    "chart": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V4M4 20h16M8 16l4-5 3 3 5-7"/></svg>',
    "pulse": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-7 4 14 3-7h4"/></svg>',
    "coins": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>',
    "layout": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M9 21V9"/></svg>',
    "spark": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 17l.7 2 .3.3 2 .7-2 .7-.3.3-.7 2-.7-2-.3-.3-2-.7 2-.7.3-.3z"/></svg>',
    "bell": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 16V11a6 6 0 0112 0v5l2 2H4zM10 20a2 2 0 004 0"/></svg>',
    "sliders": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
}

FEATURES = [
    ("tag", "Tag compliance", ("Mandatory, untagged, prohibited, tag-limit and allowed-value checks across every taggable "
     "resource type, so spend can be allocated."), "spendlens tags --summary", "tags"),
    ("resize", "Rightsizing & standards", ("Oversized instances, gp2 volumes, previous generations, Multi-AZ in non-prod, "
     "versions in paid Extended Support. Each one priced."), "spendlens rightsize --min-saving 50", "rightsize-waste"),
    ("trash", "Waste detection", ("Unattached volumes, orphaned snapshots, idle load balancers and NAT gateways, unused "
     "IPs and secrets: billed, but doing nothing."), "spendlens waste", "rightsize-waste"),
    ("chart", "Cost diff & trends", ("What changed between any two months or quarters, by service, account, region or "
     "tag. New and removed services always included."), "spendlens cost diff -g tag:Team", "cost"),
    ("pulse", "Anomalies & forecast", ("Daily spikes against a robust baseline, and a month-end projection checked "
     "against your budget and every AWS Budget."), "spendlens anomalies", "anomalies-forecast"),
    ("coins", "One savings number", ("AWS Cost Optimization Hub, Savings Plan coverage and spendlens's own findings, "
     "merged without double counting."), "spendlens savings summary", "savings"),
    ("spark", "Ask in plain English", ("Claude, OpenAI, Azure or any OpenAI-compatible model answers from your data, "
     "and can never change anything."), 'spendlens ask "why did EC2 go up?"', "ai-assistant"),
    ("layout", "Dashboard & every format", ("A self-contained HTML dashboard, and table, CSV, JSON or HTML output from "
     "every command."), "spendlens dashboard -o report.html", "dashboard"),
    ("bell", "Scheduled & notified", ("One-command runs in GitLab or GitHub on a read-only OIDC role, with Slack and "
     "Teams summaries of what changed."), "spendlens init --ci gitlab", "report-notify"),
    ("terminal", "AWS tables, explained", ("Find the table for any question, read its columns and required filters, "
     "and check a query before it runs. Offline, from the AWS plugin's own source."),
     'spendlens schema find "unattached volumes"', "ai-assistant"),
    ("clock", "Cached, never stale", ("Live results and answers are reused only while fresh, per account, region and "
     "identity. See what's cached, or clear it."), "spendlens cache show --entries", "ai-assistant"),
    ("sliders", "Your standards, your rules", ("Every threshold is a parameter in one finops.yaml, overridable per run. "
     "Plugins add controls, commands and AI tools."), "spendlens params", "configuration"),
]

_G = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
ICONS.update({
    "book": f'<svg {_G}><path d="M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2zM4 5v16M8 7h7"/></svg>',
    "play": f'<svg {_G}><circle cx="12" cy="12" r="9"/><path d="M10 8.5l5 3.5-5 3.5z"/></svg>',
    "key": f'<svg {_G}><circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l2 2M14 9l2 2"/></svg>',
    "box": f'<svg {_G}><path d="M3 7.5L12 3l9 4.5v9L12 21l-9-4.5zM3 7.5l9 4.5 9-4.5M12 12v9"/></svg>',
    "clock": f'<svg {_G}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    "terminal": f'<svg {_G}><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 9l3 3-3 3M13 15h4"/></svg>',
    "list": f'<svg {_G}><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/></svg>',
    "map": f'<svg {_G}><path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14"/></svg>',
    "puzzle": f'<svg {_G}><path d="M10 4a2 2 0 014 0v2h4v4h-2a2 2 0 000 4h2v4h-4v-2a2 2 0 00-4 0v2H6v-4h2a2 2 0 000-4H6V6h4z"/></svg>',
    "flask": f'<svg {_G}><path d="M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 006 21h12a1.7 1.7 0 001.5-2.5L14 9V3M7 15h10"/></svg>',
    "flow": f'<svg {_G}><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H14a4 4 0 014 4v5.5M6 8.5V14a4 4 0 004 4h5.5"/></svg>',
})

# docs: slug -> (icon, one-line description) for page headers and the docs home topic grid
DOC_INFO = {
    "": ("book", "What spendlens does and the principles behind it."),
    "getting-started": ("play", "Install with one command, try the demo data, connect your account."),
    "configuration": ("sliders", "Every threshold and policy as a typed parameter in finops.yaml."),
    "permissions": ("key", "The read-only IAM actions each feature needs."),
    "docker": ("box", "Run the ready-made image, locally or in CI."),
    "tags": ("tag", "Five tag checks across 70 resource types."),
    "rightsize-waste": ("resize", "Oversized, non-standard and idle resources, each priced."),
    "cost": ("chart", "What changed, where, and who owns the spend."),
    "anomalies-forecast": ("pulse", "Daily spikes and the month-end projection against budgets."),
    "savings": ("coins", "One de-duplicated savings number you can defend."),
    "dashboard": ("layout", "The self-contained HTML dashboard and every output format."),
    "history": ("clock", "Saved runs, replays and what changed between them."),
    "ai-assistant": ("spark", "Plain-English answers from your data, with read-only safety."),
    "report-notify": ("bell", "One-command reports and Slack and Teams summaries."),
    "ci": ("flow", "Scheduled pipelines on a read-only OIDC role."),
    "cli": ("terminal", "Every command by category, with recipes and every option."),
    "ways-to-run": ("flow", "Local, Docker, CI, demo and replay: every way to run spendlens."),
    "controls": ("list", "The full catalog of built-in controls."),
    "control-mapping": ("map", "How spendlens controls map to their sources."),
    "extending": ("puzzle", "Plugins: commands, controls, estimators, AI tools, dashboard tabs."),
    "development": ("flask", "Build, test and contribute."),
}

CASES = [
    ("Platform team", "The weekly cost review", ("Start the meeting with what changed and what's flagged, then drill "
     "into the services behind it."), "spendlens cost diff --show flagged\nspendlens waste --summary --by service"),
    ("Engineering manager", "Who owns this spend?", ("Showback by team and environment, with the untagged share called "
     "out so it can be fixed at the source."), "spendlens cost showback -t Team\nspendlens tags -k mandatory"),
    ("Finance & FinOps", "Month-end, without surprises", ("Projected month end against the budget, and a savings "
     "total you can defend line by line."), "spendlens forecast --fail-on-risk\nspendlens savings summary"),
    ("Platform engineering", "Guardrails in CI", ("Fail a pipeline on tag-policy violations or big cost jumps, and post "
     "the result to the team channel."), "spendlens tags --fail-on-alarm\nspendlens notify --slack --only-if-changes"),
    ("On-call / SRE", "Why did spend spike?", ("Find the anomaly, then ask the assistant to explain it from your own "
     "data, with the queries it used."), "spendlens anomalies\nspendlens ask \"what drove yesterday's spike?\" --show-tools"),
    ("Architecture review", "Standards, enforced", ("Allowed instance families, Graviton adoption, gp3 everywhere, "
     "Multi-AZ only in prod, all from one policy file."), "spendlens rightsize -S ec2 -S rds -S ebs"),
]


# Home-page gallery: (file in website/static/screens, tab label, title, caption). All from the bundled demo data.
SCREENS = [
    ("dashboard-overview.png", "Overview", "The dashboard, at a glance",
     ("Spend and its change, flagged cost changes, the projected month end against budget, potential savings, tag "
     "compliance and anomalies, in one self-contained HTML file.")),
    ("dashboard-cost.png", "Cost changes", "What changed, and where",
     ("Every service's change between two months, largest first, with a monthly trend below. New and removed "
     "services are always included.")),
    ("dashboard-savings.png", "Savings", "One savings number you can defend",
     ("spendlens findings and AWS Cost Optimization Hub merged, one action per resource, with every adjustment "
     "explained.")),
    ("dashboard-tags.png", "Tags", "Tag compliance by check and resource type",
     ("Mandatory, untagged, allowed-value, limit and prohibited checks, with the exact missing tags for each "
     "resource.")),
    ("cli-cost-diff.png", "Terminal: cost diff", "spendlens cost diff",
     ("Month over month by service, with the changes that cross your thresholds flagged. Group by account, region or "
     "any tag.")),
    ("cli-waste.png", "Terminal: waste", "spendlens waste --summary",
     "Idle and orphaned resources by service, with an estimated monthly saving for each."),
    ("cli-rightsize.png", "Terminal: rightsizing", "spendlens rightsize --summary",
     "Resources that are oversized or don't match your company standards, grouped by service."),
    ("cli-forecast.png", "Terminal: forecast", "spendlens forecast",
     "The projected month end against your budget and every AWS Budget, with a status for each."),
]


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR chunk (no imaging library needed)."""
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def webp_size(path: Path) -> tuple[int, int]:
    """Width and height of a WebP file (lossy VP8, lossless VP8L or extended VP8X)."""
    b = path.read_bytes()[:30]
    if b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        raise ValueError(f"{path} is not a WebP")
    kind = b[12:16]
    if kind == b"VP8 ":
        return int.from_bytes(b[26:28], "little") & 0x3FFF, int.from_bytes(b[28:30], "little") & 0x3FFF
    if kind == b"VP8L":
        v = int.from_bytes(b[21:25], "little")
        return (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        return int.from_bytes(b[24:27], "little") + 1, int.from_bytes(b[27:30], "little") + 1
    raise ValueError(f"{path}: unknown WebP chunk {kind!r}")


def shots() -> dict[str, dict]:
    """Home-page dashboard close-ups: <name>-light.webp / <name>-dark.webp of identical size."""
    out = {}
    for f in sorted((SITE / "static" / "shots").glob("*-light.webp")):
        name = f.name.removesuffix("-light.webp")
        w, h = webp_size(f)
        if webp_size(f.with_name(f"{name}-dark.webp")) != (w, h):
            raise SystemExit(f"{name}: light and dark screenshots differ in size")
        out[name] = {"w": w, "h": h}
    return out


def screens() -> list[dict]:
    out = []
    for i, (fname, tab, title, caption) in enumerate(SCREENS):
        w, h = png_size(SITE / "static" / "screens" / fname)
        out.append({"id": f"shot-{i}", "src": f"static/screens/{fname}", "tab": tab, "title": title,
                    "caption": caption, "w": w, "h": h})
    return out


def stats() -> dict:
    from spendlens.features.tags_catalog import TAGGABLE_RESOURCES
    from spendlens.powerpipe import bundled_mod_dir, mod_controls

    return {"controls": len(mod_controls(bundled_mod_dir())), "resource_types": len(TAGGABLE_RESOURCES), "providers": 5}


# ------------------------------------------------------------------ CLI reference (generated)


def _cli_tree() -> dict[str, Any]:
    """Every leaf command of the real CLI: path -> click command."""
    import typer.main

    from spendlens.cli import app

    out: dict[str, Any] = {}

    def walk(cmd: Any, path: list[str]) -> None:
        subs = getattr(cmd, "commands", None) or {}
        runnable = getattr(cmd, "invoke_without_command", False) and any(
            getattr(p, "param_type_name", "") == "option" and p.name != "help" for p in cmd.params)
        if path and (not subs or runnable):  # leaf commands, and groups that also run on their own (tags, waste)
            out[" ".join(path)] = cmd
        for n, sub in subs.items():
            walk(sub, [*path, n])

    walk(typer.main.get_command(app), [])
    return out


def _option_names(cmd: Any) -> set[str]:
    return {o for p in cmd.params for o in (*getattr(p, "opts", ()), *(getattr(p, "secondary_opts", None) or ()))}


def _option_row(p: Any) -> tuple[str, str, str, str]:
    """(names, accepted values, default, description) for one option, from its real type."""
    import enum
    import typing

    names = ", ".join(f"`{n}`" for n in [*p.opts, *(p.secondary_opts or [])])
    ann = getattr(getattr(p, "type_descriptor", None), "annotation", None)
    origin, args = typing.get_origin(ann), typing.get_args(ann)
    repeat = origin is list or getattr(p, "multiple", False)
    inner = args[0] if repeat and args else ann
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        inner = next((a for a in args if a is not type(None)), str)
    if p.is_flag:
        values = "flag"
    elif isinstance(inner, type) and issubclass(inner, enum.Enum):
        values = " \\| ".join(f"`{e.value}`" for e in inner)
    elif inner is int or inner is float:
        values = "number"
    elif getattr(inner, "__name__", "") == "Path":
        values = "file path"
    else:
        values = "text"
    if repeat:
        values += " (repeatable)"
    d = p.default
    if p.is_flag:
        default = "on" if d else "off"
    elif d is None or d == [] or d == ():
        default = "—"
    else:
        default = f"`{d.value if isinstance(d, enum.Enum) else d}`"
    help_ = re.sub(r"\[/?[a-z ]*\]", "", (p.help or "").replace("|", "\\|")).strip()
    return names, values, default, help_


def check_command(cmdline: str, tree: dict[str, Any], expect: str | None = None) -> list[str]:
    """Problems with one example command line: unknown command, or an option that command doesn't have."""
    import shlex

    words = shlex.split(cmdline.split(" | ")[0])
    if words[:1] != ["spendlens"]:
        return [f"does not start with spendlens: {cmdline}"]
    path = next((" ".join(words[1:n]) for n in range(len(words), 1, -1) if " ".join(words[1:n]) in tree), None)
    if path is None or (expect and path != expect):
        return [f"unknown command{f' (expected {expect})' if expect else ''}: {cmdline}"]
    known = _option_names(tree[path]) | {"-h", "--help"}
    return [f"`spendlens {path}` has no option {w.split('=', 1)[0]}: {cmdline}" for w in words
            if w.startswith("-") and not w[1:2].isdigit() and w.split("=", 1)[0] not in known]


def cheatsheet_sections() -> list[dict[str, Any]]:
    """Cheat sheet content, validated against the real CLI (the build fails on any wrong command or option)."""
    import cheatsheet as cs

    tree = _cli_tree()
    problems = [x for _, _, _, items in cs.SECTIONS for _, cmd, _ in items for x in check_command(cmd, tree)]
    problems += [f"unknown kind {k}: {c}" for _, _, _, items in cs.SECTIONS for _, c, k in items if k not in cs.KINDS]
    if problems:
        raise SystemExit("cheat sheet is out of date:\n  " + "\n  ".join(problems))
    return [{"id": sid, "title": title, "intro": intro, "nav": cs.NAV.get(sid, title),
             "cmds": [{"what": w, "cmd": c, "kind": k, "badge": cs.KINDS[k][0], "tip": cs.KINDS[k][1]}
                       for w, c, k in items]} for sid, title, intro, items in cs.SECTIONS]


def _anchor(path: str) -> str:
    return "cmd-" + path.replace(" ", "-")


def cli_reference_md() -> str:
    """The command catalog: categories, a task index, recipes and every option, generated from the real CLI.
    Raises SystemExit if a CLI command is missing from website/commands.py or a recipe uses an unknown option."""
    import commands as cat

    tree = _cli_tree()
    listed = [c for _, _, cmds in cat.CATEGORIES for c in cmds]
    problems = [f"not in any category: {c}" for c in tree if c not in listed]
    problems += [f"category lists unknown command: {c}" for c in listed if c not in tree]
    for path, recipes in cat.RECIPES.items():
        if path not in tree:
            problems.append(f"recipes for unknown command: {path}")
            continue
        for _, cmdline in recipes:
            problems += check_command(cmdline, tree, expect=path)
    if problems:
        raise SystemExit("command catalog is out of date:\n  " + "\n  ".join(problems))

    out = ["# Commands & options", "",
           ("Every spendlens command, grouped by what you want to do, with ready-to-run examples and every option. "
           "This page is generated from the CLI itself, so it always matches the version you install; "
           "`spendlens <command> --help` shows the same in your terminal."), "",
           ('<input class="cmd-filter" type="search" placeholder="Filter commands and options, e.g. tag, csv, '
           'budget" aria-label="Filter commands">'), "",
           "## Find a command", "", "| I want to… | Run |", "|---|---|"]
    for task, path in cat.TASKS:
        out.append(f"| {task} | [`spendlens {path}`](#{_anchor(path)}) |")
    out += ["", "## Options most commands share", "",
            ("These work the same way on every command that accepts them; each command's table below lists which "
            "it takes."), "", "| Option | What it does |", "|---|---|"]
    out += [f"| `{k}` | {v} |" for k, v in cat.COMMON_OPTIONS.items()]
    out += ["", ("Exit codes: `0` success · `1` error · `2` invalid option or config · `3` a `--fail-on-…` gate "
            "found something (use it to fail CI jobs). See [Ways to run](../ways-to-run/) for every way to "
            "run these commands."), ""]
    common = set(cat.COMMON_OPTIONS)
    for name, intro, paths in cat.CATEGORIES:
        out += [f"## {name}", "", intro, ""]
        for path in paths:
            cmd = tree[path]
            args = [p.name for p in cmd.params if getattr(p, "param_type_name", "") == "argument"]
            doc = re.sub(r"\[/?[a-z ]*\]", "", (cmd.help or "").strip())
            first, *rest = [x.strip() for x in doc.split("\n\n")] or [""]
            out += [f'### spendlens {path}{" <" + "> <".join(args) + ">" if args else ""} {{#{_anchor(path)}}}', "",
                    first.replace("\n", " "), ""]
            extra = [x for x in rest if not x.lower().startswith(("default:", "examples", "spendlens "))
                     and "spendlens " not in x]
            for x in extra:
                out += [x.replace("\n", " "), ""]
            recipes = cat.RECIPES.get(path, [])
            if recipes:
                block = "\n\n".join(f"# {what}\n{c}" for what, c in recipes)
                out += ["```bash", block, "```", ""]
            opts = [p for p in cmd.params if getattr(p, "param_type_name", "") == "option" and p.name != "help"
                    and not getattr(p, "hidden", False)]
            own = [p for p in opts if not set(p.opts) & common]
            shared = sorted({o for p in opts for o in p.opts if o in common})
            if own:
                out += ["| Option | Values | Default | What it does |", "|---|---|---|---|"]
                out += ["| " + " | ".join(_option_row(p)) + " |" for p in own]
                out.append("")
            if shared:
                out += ["Also accepts: " + ", ".join(f"`{o}`" for o in shared)
                        + " ([shared options](#options-most-commands-share)).", ""]
    return "\n".join(out)


def gfm_lists(text: str) -> str:
    """GitHub-style lists -> Python-Markdown: a blank line before a list that follows a paragraph line,
    and 2-space nesting widened to 4 spaces. Fenced code blocks are left untouched."""
    out: list[str] = []
    fence = False
    item = re.compile(r"^(\s*)([-*+]|\d+\.)\s")
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue
        m = item.match(line)
        if m:
            indent = len(m.group(1))
            line = " " * (indent * 2) + line.lstrip() if indent else line
            prev = out[-1] if out else ""
            if prev.strip() and not item.match(prev) and not prev.startswith(("    ", "|", ">", "#")):
                out.append("")
        out.append(line)
    return "\n".join(out)


def render_md(text: str) -> tuple[str, str, str, str]:
    md = markdown.Markdown(extensions=["tables", "fenced_code", "codehilite", "toc", "sane_lists", "attr_list"],
                           extension_configs={"codehilite": {"css_class": "hl", "guess_lang": False},
                                              "toc": {"toc_depth": "2-3"}})
    html = md.convert(gfm_lists(text))
    # repo docs link to each other as foo.md -> ../foo/
    html = re.sub(r'href="([a-z0-9-]+)\.md(#[^"]*)?"', lambda m: f'href="../{m.group(1)}/{m.group(2) or ""}"', html)
    html = re.sub(r'href="\.\./\.\./(src|scripts|tests)/', f'href="{REPO_URL}/tree/main/\\1/', html)
    title = re.search(r"<h1[^>]*>(.*?)</h1>\s*", html)
    h1 = html_lib.unescape(re.sub(r"<[^>]+>", "", title.group(1))) if title else ""
    lead = ""
    if title:
        html = html[: title.start()] + html[title.end():]
        first = re.match(r"\s*<p>(.*?)</p>\s*", html, re.DOTALL)
        if first:
            lead, html = first.group(1), html[first.end():]
    toc = md.toc if "<li>" in md.toc else ""
    # drop the page title from the on-page TOC (it is the header now)
    toc = re.sub(r'^<div class="toc">\s*<ul>\s*<li><a[^>]*>[^<]*</a>\s*(<ul>.*</ul>)\s*</li>\s*</ul>\s*</div>\s*$',
                 r'<div class="toc">\1</div>', toc, flags=re.DOTALL)
    return html, h1, toc, lead


def doc_shots(html: str, root: str) -> str:
    """[[shot:name|caption]] in a docs page -> theme-aware dashboard close-up (light/dark WebP)."""
    sizes = shots()

    def fig(m: re.Match) -> str:
        name, caption = m.group(1), m.group(2)
        d = sizes[name]
        imgs = "".join(
            f'<img class="th-{t}" src="{root}static/shots/{name}-{t}.webp" width="{d["w"]}" height="{d["h"]}" '
            f'alt="{caption}" loading="lazy">' for t in ("light", "dark"))
        return f'<figure class="doc-shot">{imgs}<figcaption>{caption}</figcaption></figure>'

    return re.sub(r"<p>\[\[shot:([a-z]+)\|(.*?)\]\]</p>", fig, html)


def pygments_css() -> str:
    light = HtmlFormatter(style="friendly").get_style_defs(".hl")
    dark = HtmlFormatter(style="monokai").get_style_defs(".hl")
    dark_scoped = "\n".join(f":root[data-theme=dark] {line}" for line in dark.splitlines() if line.strip())
    dark_media = "@media (prefers-color-scheme: dark){" + "\n".join(
        f':root:where(:not([data-theme="light"])) {line}' for line in dark.splitlines() if line.strip()) + "}"
    reset = ".prose .hl, .prose .hl pre { background: transparent !important; }"
    return f"{light}\n{dark_scoped}\n{dark_media}\n{reset}"


# ------------------------------------------------------------------ build


def build() -> list[Path]:
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "static").mkdir(parents=True)
    css = (SITE / "static" / "site.css").read_text() + "\n" + pygments_css()
    (DIST / "static" / "site.css").write_text(css)
    shutil.copy(SITE / "static" / "site.js", DIST / "static" / "site.js")
    # product screenshots (also referenced by the Docker Hub overview, docker/README.dockerhub.md)
    shutil.copytree(SITE / "static" / "screens", DIST / "static" / "screens")
    # dashboard close-ups (light + dark, 2x WebP) for the home page
    shutil.copytree(SITE / "static" / "shots", DIST / "static" / "shots")
    # brand: icon, tab favicon, touch/app icons; browsers also request /favicon.ico and /apple-touch-icon.png
    shutil.copytree(SITE / "static" / "brand", DIST / "static" / "brand")
    shutil.copy(SITE / "static" / "brand" / "favicon.ico", DIST / "favicon.ico")
    shutil.copy(SITE / "static" / "brand" / "apple-touch-icon.png", DIST / "apple-touch-icon.png")
    (DIST / "manifest.json").write_text(json.dumps({
        "name": "spendlens", "short_name": "spendlens", "description": "Read-only FinOps for AWS",
        "start_url": "/", "display": "standalone", "background_color": "#000000", "theme_color": "#0a84ff",
        "icons": [
            {"src": "/static/brand/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/brand/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/brand/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ]}, indent=2) + "\n")

    env = Environment(loader=FileSystemLoader(SITE / "templates"), autoescape=True, undefined=StrictUndefined,
                      trim_blocks=True, lstrip_blocks=True)
    common = {"version": __version__, "site_url": SITE_URL, "repo_url": REPO_URL}
    written: list[Path] = []

    def write(rel: str, template: str, **ctx) -> None:
        depth = rel.count("/")
        target = DIST / rel / "index.html" if rel else DIST / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        root = "../" * (depth + (1 if rel else 0))
        target.write_text(env.get_template(template).render(root=root or "./", path=f"/{rel}/" if rel else "/",
                                                            **common, **ctx))
        written.append(target)

    tree = _cli_tree()  # every home-page command must exist, with real options
    problems = [x for *_, c, _ in FEATURES for x in check_command(c, tree)]
    problems += [x for *_, c in CASES for line in c.splitlines() for x in check_command(line, tree)]
    if problems:
        raise SystemExit("home page is out of date:\n  " + "\n  ".join(problems))
    write("", "home.html.j2", title="spendlens: FinOps for AWS, from your terminal", section="home",
          description="Read-only FinOps CLI for AWS: tag compliance, rightsizing, waste, cost diff, anomalies, "
                      "forecast, savings and an AI assistant.",
          stats=stats(), screens=screens(), shots=shots(), icons=ICONS, features=[{"icon": ICONS[i], "title": t, "body": b, "cmd": c, "doc": d}
                                   for i, t, b, c, d in FEATURES],
          cases=[{"who": w, "title": t, "body": b, "cmd": c} for w, t, b, c in CASES])
    write("cheatsheet", "cheatsheet.html.j2", title="Cheat sheet · spendlens", section="cheatsheet",
          description="The spendlens commands you'll actually use, by question, ready to copy.",
          sections=cheatsheet_sections(), icons=ICONS)
    write("architecture", "architecture.html.j2", title="Architecture · spendlens", section="architecture",
          description="How spendlens works: C4 context and container diagrams and sequence diagrams.",
          d={k: fn() for k, fn in diagrams.ALL.items()}, icons=ICONS, stats=stats())

    sources: dict[str, str] = {}
    for slug, fname in REPO_DOCS.items():
        sources[slug] = (ROOT / "docs" / fname).read_text()
    for f in (SITE / "content" / "docs").glob("*.md"):
        sources["" if f.stem == "index" else f.stem] = f.read_text()
    sources["cli"] = cli_reference_md()

    flat = [(slug, title) for _, items in NAV for slug, title in items]
    missing = [s for s, _ in flat if s not in sources]
    if missing:
        raise SystemExit(f"docs pages missing: {missing}")
    group_of = {slug: g for g, items in NAV for slug, _ in items}
    nav = [(g, [{"slug": s, "title": t, "icon": ICONS[DOC_INFO[s][0]]} for s, t in items]) for g, items in NAV]
    topics = [(g, [{"slug": s, "title": t, "icon": ICONS[DOC_INFO[s][0]], "desc": DOC_INFO[s][1]}
                   for s, t in items if s]) for g, items in NAV]
    for i, (slug, title) in enumerate(flat):
        html, h1, toc, lead = render_md(sources[slug])
        depth_root = "../../" if slug else "../"
        html = doc_shots(html, depth_root)
        if slug in REPO_DOCS:
            edit = f"{REPO_URL}/edit/main/docs/{REPO_DOCS[slug]}"
        elif slug == "cli":
            edit = None
        else:
            edit = f"{REPO_URL}/edit/main/website/content/docs/{slug or 'index'}.md"
        page = {"slug": slug, "title": title, "html": html, "toc": toc, "h1": h1 or title, "lead": lead,
                "group": group_of[slug], "icon": ICONS[DOC_INFO[slug][0]], "edit": edit}
        prev = {"slug": flat[i - 1][0], "title": flat[i - 1][1]} if i > 0 else None
        nxt = {"slug": flat[i + 1][0], "title": flat[i + 1][1]} if i + 1 < len(flat) else None
        write(f"docs/{slug}".rstrip("/"), "doc.html.j2", title=f"{h1 or title} · spendlens docs", section="docs",
              description=DOC_INFO[slug][1], page=page, nav=nav, topics=topics, prev=prev, next=nxt)

    urls = [SITE_URL + "/" + str(p.relative_to(DIST).parent).replace(".", "").strip("/") for p in written]
    now = datetime.now(UTC).date().isoformat()
    (DIST / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/'
                                      'schemas/sitemap/0.9">' + "".join(
                                          f"<url><loc>{u.rstrip('/')}/</loc><lastmod>{now}</lastmod></url>"
                                          for u in urls) + "</urlset>\n")
    # one-line installer: curl -fsSL https://spendlens.devops-monk.com/install.sh | sh
    shutil.copy(ROOT / "install.sh", DIST / "install.sh")
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    return written


def check_links(pages: list[Path]) -> list[str]:
    """Every relative href/src must resolve to a file in dist (anchors and external URLs ignored)."""
    problems = []
    for page in pages:
        for ref in re.findall(r'(?:href|src)="([^"#?]+)', page.read_text()):
            if re.match(r"^(https?:|mailto:|data:)", ref) or ref.startswith("/"):
                continue
            target = (page.parent / ref).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                problems.append(f"{page.relative_to(DIST)} -> {ref}")
    return sorted(set(problems))


def main() -> int:
    pages = build()
    print(f"Built {len(pages)} pages into {DIST.relative_to(ROOT)}")
    if "--check" in sys.argv:
        bad = check_links(pages)
        if bad:
            print("Broken internal links:\n  " + "\n  ".join(bad))
            return 1
        print("All internal links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
