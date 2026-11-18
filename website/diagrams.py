"""Hand-built inline SVG diagrams for the website (no libraries, no runtime).

Everything is drawn in currentColor so it follows the page's light/dark theme; one accent (ACCENT) marks the
spendlens boundary. Each function returns (svg, caption).
"""

from __future__ import annotations

from html import escape

ACCENT = "#0a84ff"  # readable on both white and black grounds
FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', Roboto, sans-serif"


HALO = ' style="paint-order:stroke;stroke:var(--bg-elev,#fff);stroke-width:5px;stroke-linejoin:round"'


def _t(x: float, y: float, s: str, size: int = 13, anchor: str = "middle", weight: int = 400, opacity: float = 1,
       fill: str = "currentColor", halo: bool = False) -> str:
    return (f'<text x="{x:.0f}" y="{y:.0f}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}" fill-opacity="{opacity}"{HALO if halo else ""}>{escape(s)}</text>')


def _defs(uid: str) -> str:
    return (f'<defs><marker id="{uid}-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>')


def _box(x: float, y: float, w: float, h: float, title: str, sub: str = "", kind: str = "", accent: bool = False,
         ext: bool = False) -> str:
    stroke = ACCENT if accent else "currentColor"
    dash = ' stroke-dasharray="5 4"' if ext else ""
    parts = [(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{stroke}" fill-opacity="{0.10 if accent else 0.05}" '
             f'stroke="{stroke}" stroke-opacity="{0.9 if accent else 0.45}" stroke-width="1.5"{dash}/>')]
    cy = y + h / 2
    lines = [s for s in (kind, title, sub) if s]
    offs = {1: [4], 2: [-4, 13], 3: [-13, 4, 20]}[len(lines)]
    for s, dy in zip(lines, offs, strict=True):
        if s == kind:
            parts.append(_t(x + w / 2, cy + dy, s, 10.5, opacity=0.6))
        elif s == title:
            parts.append(_t(x + w / 2, cy + dy, s, 13.5, weight=600))
        else:
            parts.append(_t(x + w / 2, cy + dy, s, 11, opacity=0.7))
    return "".join(parts)


def _arrow(uid: str, x1: float, y1: float, x2: float, y2: float, label: str = "", lx: float | None = None,
           ly: float | None = None, dashed: bool = False, anchor: str = "middle") -> str:
    dash = ' stroke-dasharray="4 4"' if dashed else ""
    a = (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="currentColor" stroke-opacity="0.75" '
         f'stroke-width="1.4" marker-end="url(#{uid}-a)"{dash}/>')
    if label:
        lx = (x1 + x2) / 2 if lx is None else lx
        ly = (y1 + y2) / 2 - 6 if ly is None else ly
        a += _t(lx, ly, label, 11, anchor=anchor, opacity=0.85, halo=True)
    return a


def _svg(uid: str, w: int, h: int, label: str, body: str) -> str:
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{escape(label)}" font-family="{FONT}" '
            f'xmlns="http://www.w3.org/2000/svg">{_defs(uid)}{body}</svg>')


# ------------------------------------------------------------------ C4 level 1: system context


def c4_context() -> tuple[str, str]:
    u = "ctx"
    b = []
    # people / callers (left)
    b.append(_box(20, 40, 200, 70, "FinOps / platform engineer", "terminal, assumed AWS role", "Person"))
    b.append(_box(20, 150, 200, 70, "Engineering manager", "reads the brief, asks questions", "Person"))
    b.append(_box(20, 260, 200, 70, "CI scheduler", "GitLab CI / GitHub Actions", "System"))
    # spendlens (centre)
    b.append(_box(330, 120, 220, 130, "spendlens", "FinOps CLI · read-only", "Software system", accent=True))
    # external systems (right)
    b.append(_box(720, 20, 240, 66, "AWS account(s)", "resource APIs · CloudWatch", "External system", ext=True))
    b.append(_box(720, 106, 240, 66, "AWS billing APIs", "Cost Explorer · Budgets · Opt. Hub", "External system", ext=True))
    b.append(_box(720, 192, 240, 66, "LLM provider", "Bedrock · Anthropic · OpenAI · Azure", "External system", ext=True))
    b.append(_box(720, 278, 240, 66, "Slack / Teams", "incoming webhooks", "External system", ext=True))
    # arrows
    b.append(_arrow(u, 220, 75, 330, 150, "runs commands", 262, 98))
    b.append(_arrow(u, 220, 185, 330, 185, "asks · opens brief", 275, 178))
    b.append(_arrow(u, 220, 295, 330, 225, "spendlens report", 272, 275))
    b.append(_arrow(u, 550, 140, 720, 53, "reads inventory + metrics", 635, 92))
    b.append(_arrow(u, 550, 165, 720, 139, "reads cost, budgets, recs", 635, 148))
    b.append(_arrow(u, 550, 205, 720, 225, "question + tool results", 635, 210))
    b.append(_arrow(u, 550, 230, 720, 311, "posts summary", 635, 274))
    svg = _svg(u, 980, 360, "System context: people and CI call spendlens, which only reads from AWS and talks to an LLM "
               "and chat webhooks", "".join(b))
    return svg, ("C4 level 1 (context). spendlens sits between the people and pipelines that ask cost questions and "
                 "the systems it reads. Every arrow into AWS is a read; nothing in AWS is ever changed.")


# ------------------------------------------------------------------ C4 level 2: containers


def c4_container() -> tuple[str, str]:
    u = "ctr"
    b = []
    # spendlens boundary
    b.append(f'<rect x="200" y="20" width="560" height="480" rx="18" fill="none" stroke="{ACCENT}" '
             f'stroke-opacity="0.9" stroke-width="1.5" stroke-dasharray="7 5"/>')
    b.append(_t(216, 42, "spendlens (one Python package on the machine or CI runner)", 12, anchor="start", weight=600,
                fill=ACCENT))
    # caller
    b.append(_box(20, 220, 150, 70, "You / CI", "spendlens <command>", "Caller"))
    # containers
    b.append(_box(230, 60, 200, 72, "CLI + feature plugins", "Typer · config layers", "Container", accent=True))
    b.append(_box(530, 60, 210, 72, "Powerpipe + bundled mod", "415 controls (tags, rightsize, waste)", "Container"))
    b.append(_box(230, 200, 200, 72, "Engines", "cost · anomaly · forecast · savings", "Container"))
    b.append(_box(530, 200, 210, 72, "Steampipe (Postgres)", "aws plugin · read-only session", "Container"))
    b.append(_box(230, 340, 200, 72, "History store", "SQLite: runs · findings · cost", "Container"))
    b.append(_box(530, 340, 210, 72, "AI agent", "catalog · checker · jobs · cache", "Container"))
    b.append(_box(230, 430, 200, 58, "Renderers", "table · CSV · JSON · HTML · brief", "Container"))
    # externals
    b.append(_box(830, 200, 160, 72, "AWS APIs", "role from terminal / OIDC", "External", ext=True))
    b.append(_box(830, 340, 160, 72, "LLM provider", "official SDKs", "External", ext=True))
    b.append(_box(830, 440, 160, 58, "Slack / Teams", "webhook (env var)", "External", ext=True))
    # arrows
    b.append(_arrow(u, 170, 240, 230, 110, "invokes", 186, 170, anchor="start"))
    b.append(_arrow(u, 430, 96, 530, 96, "runs benchmark", 480, 88))
    b.append(_arrow(u, 635, 132, 635, 200, "control SQL", 645, 170, anchor="start"))
    b.append(_arrow(u, 330, 132, 330, 200, "cost questions", 340, 170, anchor="start"))
    b.append(_arrow(u, 430, 236, 530, 236, "bounded SQL", 480, 228))
    b.append(_arrow(u, 740, 236, 830, 236, "API reads", 795, 228))
    b.append(_arrow(u, 300, 272, 300, 340, "save run", 310, 310, anchor="start"))
    b.append(_arrow(u, 530, 376, 430, 376, "history_query", 480, 368))
    b.append(_arrow(u, 600, 340, 600, 272, "--live only", 610, 310, anchor="start"))
    b.append(_arrow(u, 740, 376, 830, 376, "messages", 795, 368))
    b.append(_arrow(u, 330, 412, 330, 430, "", None, None))
    b.append(_arrow(u, 430, 470, 830, 470, "notify (summary)", 615, 462))
    svg = _svg(u, 1010, 520, "Containers inside spendlens: CLI, Powerpipe, engines, Steampipe, history store, AI agent "
               "and renderers, and the external systems they read from", "".join(b))
    return svg, ("C4 level 2 (containers). Controls run through Powerpipe; cost questions go straight to Steampipe as "
                 "bounded SQL; every run lands in the SQLite history, which the dashboard, notify and the AI assistant "
                 "read without touching AWS again.")


# ------------------------------------------------------------------ sequence diagrams


def _sequence(uid: str, participants: list[tuple[str, str]], steps: list[tuple], label: str, width: int = 980,
              row: int = 38) -> str:
    """steps: ("msg", from, to, text, dashed) | ("note", from, to, text) | ("self", who, text)."""
    n = len(participants)
    left, right = 70, width - 70
    xs = [left + i * (right - left) / (n - 1) for i in range(n)]
    idx = {k: i for i, (k, _) in enumerate(participants)}
    top = 30
    head_h = 46
    y = top + head_h + 28
    body = []
    for i, (k, name) in enumerate(participants):
        accent = k == "sl"
        stroke = ACCENT if accent else "currentColor"
        body.append(f'<rect x="{xs[i] - 66:.0f}" y="{top}" width="132" height="{head_h}" rx="10" fill="{stroke}" '
                    f'fill-opacity="{0.1 if accent else 0.05}" stroke="{stroke}" stroke-opacity="{0.9 if accent else 0.45}"/>')
        body.append(_t(xs[i], top + head_h / 2 + 5, name, 12.5, weight=600))
    rows = []
    for s in steps:
        if s[0] == "msg":
            _, a, b_, text, dashed = s
            x1, x2 = xs[idx[a]], xs[idx[b_]]
            pad = 4 if x2 > x1 else -4
            rows.append(_arrow(uid, x1 + pad, y, x2 - pad, y, text, (x1 + x2) / 2, y - 7, dashed=dashed))
            y += row
        elif s[0] == "self":
            _, a, text = s
            x = xs[idx[a]]
            rows.append(f'<path d="M{x + 4:.0f},{y - 8} h34 v16 h-30" fill="none" stroke="currentColor" stroke-opacity="0.75" '
                        f'stroke-width="1.4" marker-end="url(#{uid}-a)"/>')
            rows.append(_t(x + 44, y + 4, text, 11, anchor="start", opacity=0.8))
            y += row
        elif s[0] == "note":
            _, a, b_, text = s
            x1, x2 = min(xs[idx[a]], xs[idx[b_]]) - 60, max(xs[idx[a]], xs[idx[b_]]) + 60
            rows.append(f'<rect x="{x1:.0f}" y="{y - 16}" width="{x2 - x1:.0f}" height="24" rx="6" fill="{ACCENT}" '
                        f'fill-opacity="0.12" stroke="{ACCENT}" stroke-opacity="0.6"/>')
            rows.append(_t((x1 + x2) / 2, y + 1, text, 11, weight=600))
            y += row
    height = y + 10
    for x in xs:
        body.insert(0, f'<line x1="{x:.0f}" y1="{top + head_h}" x2="{x:.0f}" y2="{height - 6}" stroke="currentColor" '
                       f'stroke-opacity="0.25" stroke-dasharray="3 4"/>')
    return _svg(uid, width, int(height), label, "".join(body + rows))


def seq_report() -> tuple[str, str]:
    parts = [("ci", "CI scheduler"), ("aws", "AWS STS + APIs"), ("sl", "spendlens"), ("pp", "Powerpipe"),
             ("sp", "Steampipe"), ("db", "History DB"), ("hook", "Slack / Teams")]
    steps = [
        ("msg", "ci", "aws", "OIDC token → AssumeRoleWithWebIdentity", False),
        ("msg", "aws", "ci", "short-lived read-only credentials", True),
        ("msg", "ci", "db", "restore history.db (S3 / cache)", False),
        ("msg", "ci", "sl", "spendlens report --out reports", False),
        ("note", "sl", "sp", "each step = its own process; a failure never stops the rest"),
        ("msg", "sl", "pp", "benchmark run tags / rightsize / waste", False),
        ("msg", "pp", "sp", "control SQL (read-only session)", False),
        ("msg", "sp", "aws", "Describe* / List* / GetMetricStatistics", False),
        ("msg", "pp", "sl", "results JSON → findings + $ estimates", True),
        ("msg", "sl", "sp", "cost diff · anomalies · forecast: bounded SQL", False),
        ("msg", "sp", "aws", "GetCostAndUsage / GetCostForecast", False),
        ("msg", "sl", "db", "save each run (findings, cost rows, params)", False),
        ("self", "sl", "savings summary · dashboard · executive brief from history"),
        ("msg", "sl", "hook", "notify: summary + new alarms since last run", False),
        ("msg", "ci", "db", "save history.db · keep reports/ artifact", False),
    ]
    svg = _sequence("sr", parts, steps, "Sequence of a scheduled spendlens report run in CI")
    return svg, ("A scheduled run. The pipeline holds no AWS keys: it trades an OIDC token for a read-only role. "
                 "spendlens writes every result to the history DB, so the dashboard, the executive brief, "
                 "notifications and next week's 'what changed' all come from data already collected.")


def seq_ask() -> tuple[str, str]:
    parts = [("user", "You"), ("sl", "spendlens ask"), ("llm", "LLM provider"), ("guard", "SQL guard"),
             ("db", "History DB"), ("sp", "Steampipe")]
    steps = [
        ("msg", "user", "sl", "“Why did EC2 go up last month?”", False),
        ("msg", "sl", "llm", "system prompt + question + tool schemas", False),
        ("msg", "llm", "sl", "tool_use: history_overview", True),
        ("msg", "sl", "db", "latest run per feature (read-only)", False),
        ("msg", "sl", "llm", "tool_result", False),
        ("msg", "llm", "sl", "tool_use: history_query(SELECT …)", True),
        ("msg", "sl", "guard", "1 · parse: single read-only SELECT?", False),
        ("msg", "guard", "db", "2 · mode=ro + query_only + authorizer", False),
        ("msg", "db", "sl", "rows (capped, truncated)", True),
        ("note", "sl", "sp", "with --live only: 3 · read-only Steampipe session + timeout + row cap"),
        ("msg", "sl", "llm", "tool_result (data, never instructions)", False),
        ("msg", "llm", "sl", "answer citing runs and numbers", True),
        ("msg", "sl", "user", "answer + audit trail (--show-tools)", True),
    ]
    svg = _sequence("sa", parts, steps, "Sequence of a spendlens ask question with tool calls through the SQL guard")
    return svg, ("One question. The model never touches a database: it asks for a query, spendlens checks it with a "
                 "SQL parser, then runs it on a connection that cannot write. Live AWS is only reachable with --live.")


def seq_ask_live() -> tuple[str, str]:
    parts = [("user", "You"), ("sl", "spendlens ask"), ("llm", "LLM provider"), ("cat", "Catalog · checker"),
             ("cache", "Cache"), ("sp", "Steampipe + plugin"), ("aws", "AWS APIs")]
    steps = [
        ("msg", "user", "sl", "ask --live “Which EBS volumes are unattached?”", False),
        ("msg", "sl", "llm", "question + workflow + 5 AWS tools", False),
        ("note", "sl", "cat", "1 · learn the schema: no guessed tables or columns"),
        ("msg", "llm", "sl", "aws_find_tables(question)", True),
        ("msg", "sl", "cat", "search 589 tables", False),
        ("msg", "cat", "sl", "aws_ebs_volume, … + required filters", True),
        ("msg", "llm", "sl", "aws_describe_table(aws_ebs_volume)", True),
        ("msg", "sl", "sp", "installed columns (once a day, no AWS call)", False),
        ("msg", "sl", "llm", "columns + meaning · filters · examples", False),
        ("note", "sl", "cat", "2 · every query is checked before it runs"),
        ("msg", "llm", "sl", "aws_query(SELECT … size_gb …)", True),
        ("msg", "sl", "cat", "tables · columns · required filters", False),
        ("msg", "cat", "sl", "✗ size_gb → did you mean size?", True),
        ("msg", "sl", "llm", "error + exact fix (nothing sent to AWS)", False),
        ("msg", "llm", "sl", "aws_query(SELECT volume_id, size …)", True),
        ("msg", "sl", "cache", "fresh result? (≤ 15 min)", False),
        ("msg", "cache", "sl", "miss", True),
        ("note", "sl", "sp", "3 · background job · read-only · waits, never times out early"),
        ("msg", "sl", "sp", "SELECT … limit 501", False),
        ("msg", "sp", "aws", "DescribeVolumes · per region", False),
        ("msg", "aws", "sp", "pages", True),
        ("msg", "sp", "sl", "rows (slow? job q1: aws_query_result)", True),
        ("msg", "sl", "cache", "store (TTL by data class)", False),
        ("msg", "sl", "llm", "rows + as_of", False),
        ("msg", "llm", "sl", "answer citing the SQL", True),
        ("msg", "sl", "cache", "answer (same question + data → 0 tokens)", False),
        ("msg", "sl", "user", "answer + audit trail", True),
    ]
    svg = _sequence("sal", parts, steps, "Sequence of a spendlens ask --live question: schema lookup, query check, "
                                         "cache, background job and answer", width=1100)
    return svg, ("One live question. The model learns the real tables and columns from the plugin catalog, every query "
                 "is checked before it reaches AWS, slow queries run as jobs it waits for, and fresh results and "
                 "answers are reused from the cache.")


def ai_pipeline() -> tuple[str, str]:
    u = "aip"
    b = []
    b.append(_box(20, 70, 130, 70, "LLM", "plans, writes SQL", "Your provider", ext=True))
    cols = [("Catalog", "589 tables · 12.7k columns", "required filters · examples"),
            ("Checker", "tables · columns · filters", "JSON ops · cost guards"),
            ("Runner", "background job · waits", "timeout · retry · cancel"),
            ("Cache", "results by data class", "answers by data fingerprint")]
    x = 200
    cmds = ["schema find · show", "schema check", "ask --live", "cache show · clear"]
    b.append(_t(85, 209, "Run it yourself:", 11, weight=600, opacity=0.75))
    for i, ((title, a, c), cmd) in enumerate(zip(cols, cmds)):
        b.append(f'<rect x="{x}" y="30" width="170" height="150" rx="14" fill="{ACCENT}" fill-opacity="0.08" '
                 f'stroke="{ACCENT}" stroke-opacity="0.7" stroke-width="1.5"/>')
        b.append(_t(x + 85, 58, f"{i + 1}", 12, weight=700, fill=ACCENT))
        b.append(_t(x + 85, 84, title, 15, weight=700))
        b.append(_t(x + 85, 112, a, 11, opacity=0.8))
        b.append(_t(x + 85, 132, c, 11, opacity=0.8))
        b.append(f'<rect x="{x + 10}" y="192" width="150" height="26" rx="13" fill="{ACCENT}" fill-opacity="0.16"/>')
        b.append(_t(x + 85, 209, cmd, 11, weight=600, fill=ACCENT))
        if i:
            b.append(_arrow(u, x - 28, 105, x - 2, 105))
        x += 200
    b.append(_box(1000, 70, 130, 70, "Steampipe", "AWS plugin v1.35.0", "Read-only", ext=False))
    b.append(_arrow(u, 150, 105, 198, 105))
    b.append(_arrow(u, 972, 105, 998, 105))
    b.append(_t(575, 254, "Built from the plugin's own source and docs · merged with the columns your installed plugin "
                     "reports · nothing here can write to AWS", 11.5, opacity=0.7))
    svg = _svg(u, 1150, 270, ("The four layers between the model and AWS: catalog, checker, runner and cache, each with the "
               "spendlens command that exposes it"), "".join(b))
    return svg, ("Four layers sit between the model and AWS. A wrong table or column is caught by the checker in "
                 "milliseconds instead of failing in AWS, and a repeated question is answered from the cache.")


ALL = {"c4_context": c4_context, "c4_container": c4_container, "seq_report": seq_report, "seq_ask": seq_ask,
       "seq_ask_live": seq_ask_live, "ai_pipeline": ai_pipeline}
