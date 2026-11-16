#!/usr/bin/env python3
"""Build the static website (website/ -> website/dist/).

    python scripts/build_site.py            # build
    python scripts/build_site.py --check    # build and verify every internal link resolves

Docs = website/content/docs/*.md + the repo's docs/{permissions,ci,controls,control-mapping}.md (single source of truth)
+ a CLI reference generated from the live command tree. Stats on the home page are computed from the code.
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

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
                     ("permissions", "IAM permissions"), ("docker", "Docker")]),
    ("Features", [("tags", "Tag compliance"), ("rightsize-waste", "Rightsizing & waste"), ("cost", "Cost analysis"),
                  ("anomalies-forecast", "Anomalies & forecast"), ("savings", "Savings"),
                  ("dashboard", "Dashboard & formats"), ("history", "History & sources")]),
    ("AI", [("ai-assistant", "AI assistant")]),
    ("Automation", [("report-notify", "Reports & notifications"), ("ci", "CI/CD")]),
    ("Reference", [("cli", "CLI reference"), ("controls", "Controls catalog"), ("control-mapping", "Control mapping"),
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
    ("sliders", "Your standards, your rules", ("Every threshold is a parameter in one finops.yaml, overridable per run. "
     "Plugins add controls, commands and AI tools."), "spendlens params", "configuration"),
]

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


def cli_reference_md() -> str:
    import typer.main

    from spendlens.cli import app

    out = ["# CLI reference", "",
           "Generated from the installed command tree, so it always matches the CLI. Every command also has `--help`.", ""]

    def walk(cmd, path):
        subs = getattr(cmd, "commands", None) or {}
        if path and not subs:
            doc = (cmd.help or "").strip().split("\n\n")[0].replace("\n", " ")
            doc = re.sub(r"\[/?[a-z ]+\]", "", doc)
            args = [p.name for p in cmd.params if getattr(p, "param_type_name", "") == "argument"]
            out.append(f"## `spendlens {' '.join(path)}`" + (f" `<{', '.join(args)}>`" if args else ""))
            out.append("")
            out.append(doc)
            out.append("")
            opts = [p for p in cmd.params if getattr(p, "param_type_name", "") == "option" and p.name != "help"
                    and not getattr(p, "hidden", False)]
            if opts:
                out.append("| Option | Description |")
                out.append("|---|---|")
                for o in opts:
                    names = ", ".join(f"`{n}`" for n in o.opts + list(o.secondary_opts or []))
                    help_ = re.sub(r"\[/?[a-z ]+\]", "", (o.help or "").replace("|", "\\|"))
                    out.append(f"| {names} | {help_} |")
                out.append("")
        for n, sub in subs.items():
            walk(sub, [*path, n])

    walk(typer.main.get_command(app), [])
    return "\n".join(out)


# ------------------------------------------------------------------ markdown


def render_md(text: str) -> tuple[str, str, str]:
    md = markdown.Markdown(extensions=["tables", "fenced_code", "codehilite", "toc", "sane_lists", "attr_list"],
                           extension_configs={"codehilite": {"css_class": "hl", "guess_lang": False},
                                              "toc": {"toc_depth": "2-3"}})
    html = md.convert(text)
    # repo docs link to each other as foo.md -> ../foo/
    html = re.sub(r'href="([a-z0-9-]+)\.md(#[^"]*)?"', lambda m: f'href="../{m.group(1)}/{m.group(2) or ""}"', html)
    html = re.sub(r'href="\.\./\.\./(src|scripts|tests)/', f'href="{REPO_URL}/tree/main/\\1/', html)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", html)
    toc = md.toc if "<li>" in md.toc else ""
    return html, re.sub(r"<[^>]+>", "", title.group(1)) if title else "", toc


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
    (DIST / "static" / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="g" x1="0" y1="0" '
        'x2="1" y2="1"><stop offset="0" stop-color="#0071e3"/><stop offset=".55" stop-color="#a259ff"/>'
        '<stop offset="1" stop-color="#ff375f"/></linearGradient></defs><rect width="32" height="32" rx="8" '
        'fill="url(#g)"/><circle cx="14" cy="14" r="6" fill="none" stroke="#fff" stroke-width="3"/>'
        '<path d="M18.5 18.5L24 24" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>')

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

    write("", "home.html.j2", title="spendlens: FinOps for AWS, from your terminal", section="home",
          description="Read-only FinOps CLI for AWS: tag compliance, rightsizing, waste, cost diff, anomalies, "
                      "forecast, savings and an AI assistant.",
          stats=stats(), screens=screens(), features=[{"icon": ICONS[i], "title": t, "body": b, "cmd": c, "doc": d}
                                   for i, t, b, c, d in FEATURES],
          cases=[{"who": w, "title": t, "body": b, "cmd": c} for w, t, b, c in CASES])
    write("architecture", "architecture.html.j2", title="Architecture · spendlens", section="architecture",
          description="How spendlens works: C4 context and container diagrams and sequence diagrams.",
          d={k: fn() for k, fn in diagrams.ALL.items()})

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
    nav = [(g, [{"slug": s, "title": t} for s, t in items]) for g, items in NAV]
    for i, (slug, title) in enumerate(flat):
        html, h1, toc = render_md(sources[slug])
        page = {"slug": slug, "title": title, "html": html, "toc": toc}
        prev = {"slug": flat[i - 1][0], "title": flat[i - 1][1]} if i > 0 else None
        nxt = {"slug": flat[i + 1][0], "title": flat[i + 1][1]} if i + 1 < len(flat) else None
        write(f"docs/{slug}".rstrip("/"), "doc.html.j2", title=f"{h1 or title} · spendlens docs", section="docs",
              description=f"spendlens documentation: {title}.", page=page, nav=nav, prev=prev, next=nxt)

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
