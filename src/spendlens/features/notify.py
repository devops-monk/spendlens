"""`spendlens notify`: post a FinOps summary of the latest saved runs to Slack and/or Microsoft Teams.

Reads the history DB (no AWS calls). Webhook URLs come only from environment variables (names in
finops.yaml notifications.*_webhook_env) so they never land in config files or CI logs.
  * Slack: incoming webhook, Block Kit payload.
  * Teams: Workflows ("Post to a channel when a webhook request is received") webhook, Adaptive Card payload.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.request
from typing import Annotated, Any
from urllib.parse import urlsplit

import typer

from ..common import ConfigOpt, Source, err, history_path, load
from ..config import Config
from ..history import HistoryStore, demo_db_path, diff_findings
from ..plugins import Registry
from ..render import charts as ch
from .dashboard import Data, view_model

DIFF_FEATURES = ("tags", "rightsize", "waste")


# ------------------------------------------------------------------ summary (pure data)


def build_summary(vm: dict[str, Any], changes: dict[str, dict[str, int]], cfg: Config, link: str | None,
                  title: str | None = None) -> dict[str, Any]:
    cur = cfg.pricing.currency
    facts: list[tuple[str, str]] = []
    highlights: list[str] = []
    if cd := vm.get("cost_diff"):
        t = cd["total"]
        facts.append((f"Spend {cd['p1']}", f"{ch.money(t['compare'], cur)} ({ch.pct(t['change_pct'], True)} vs {cd['p0']})"))
        for b in cd["bars"][:3]:
            if abs(b["value"]) >= cfg.cost.change_alarm_abs:
                highlights.append(f"Cost change: {b['label']} {ch.signed_money(b['value'], cur)}")
        if cd["flagged"]:
            facts.append(("Flagged cost changes", str(cd["flagged"])))
    if (fc := vm.get("forecast")) and (a := fc.get("account")):
        label = a.get("label") or ""
        facts.append(("Month-end forecast", f"{ch.money(a.get('forecast'), cur)}"
                      + (f" vs budget {ch.money(a.get('limit'), cur)} ({label})" if a.get("limit") else "")))
        if "over" in label or "risk" in label:
            highlights.append(f"Forecast: {label} ({ch.money(a.get('forecast'), cur)} vs {ch.money(a.get('limit'), cur)})")
    if an := vm.get("anomalies"):
        facts.append(("Cost anomalies", str(an["count"])))
        for b in an["bars"][:3]:
            highlights.append(f"Anomaly: {b['label']} +{ch.money(b['value'], cur)}")
    if s := vm.get("savings"):
        facts.append(("Potential saving", f"{s['total']}/month ({s['yearly']}/year)"))
    if (g := vm.get("tags")) and g.get("overall") is not None:
        facts.append(("Tag compliance", ch.pct(g["overall"])))
    for feat, c in changes.items():
        if c["new_alarm"] or c["resolved"]:
            highlights.append(f"{feat}: {c['new_alarm']} new alarm(s), {c['resolved']} resolved since the previous run")
    top = []
    if s := vm.get("savings"):
        for cells in s["table"]["rows"][:3]:
            row = {c["key"]: cell["text"] for c, cell in zip(s["table"]["cols"], cells, strict=True)}
            top.append(f"{row.get('est_monthly_saving', '')}/mo · {row.get('control', '')} · {row.get('resource', '')}")
    noteworthy = bool((vm.get("cost_diff") or {}).get("flagged") or (vm.get("anomalies") or {}).get("count")
                      or any(c["new_alarm"] for c in changes.values())
                      or "over" in ((vm.get("forecast") or {}).get("account") or {}).get("label", ""))
    return {
        "title": title or cfg.dashboard.title, "account": vm.get("account"), "source": vm.get("source"),
        "facts": facts, "highlights": highlights, "top_savings": top, "link": link, "noteworthy": noteworthy,
    }


def run_changes(store: HistoryStore) -> dict[str, dict[str, int]]:
    out = {}
    for feat in DIFF_FEATURES:
        new, old = store.latest_run(feat), store.latest_run(feat, offset=1)
        if not new or not old:
            continue
        rows = diff_findings(store.findings(old.id), store.findings(new.id))
        out[feat] = {k: sum(r["change"] == k for r in rows) for k in ("new_alarm", "resolved")}
    return out


# ------------------------------------------------------------------ payloads


def slack_payload(s: dict[str, Any]) -> dict[str, Any]:
    sub = " · ".join(x for x in (f"AWS account {s['account']}" if s["account"] else "", "DEMO DATA" if s["source"] == "demo" else "") if x)
    blocks: list[dict[str, Any]] = [{"type": "header", "text": {"type": "plain_text", "text": s["title"][:150]}}]
    if sub:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": sub}]})
    if s["facts"]:
        blocks.append({"type": "section", "fields": [{"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in s["facts"][:10]]})
    if s["highlights"]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Attention*\n" + "\n".join(f"• {h}" for h in s["highlights"][:8])}})
    if s["top_savings"]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Top savings (estimates, review before acting)*\n"
                                                    + "\n".join(f"• {t}" for t in s["top_savings"])}})
    if s["link"]:
        blocks.append({"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open report"},
                                                        "url": s["link"]}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "spendlens · read-only · estimates for human review"}]})
    fallback = f"{s['title']}: " + "; ".join(f"{k} {v}" for k, v in s["facts"][:4])
    return {"text": fallback, "blocks": blocks}


def teams_payload(s: dict[str, Any]) -> dict[str, Any]:
    body: list[dict[str, Any]] = [{"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": s["title"], "wrap": True}]
    if s["account"]:
        body.append({"type": "TextBlock", "isSubtle": True, "spacing": "None", "wrap": True,
                     "text": f"AWS account {s['account']}" + (" · DEMO DATA" if s["source"] == "demo" else "")})
    if s["facts"]:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in s["facts"][:10]]})
    if s["highlights"]:
        body.append({"type": "TextBlock", "weight": "Bolder", "text": "Attention", "wrap": True})
        body += [{"type": "TextBlock", "text": f"• {h}", "wrap": True, "spacing": "None"} for h in s["highlights"][:8]]
    if s["top_savings"]:
        body.append({"type": "TextBlock", "weight": "Bolder", "text": "Top savings (estimates, review before acting)", "wrap": True})
        body += [{"type": "TextBlock", "text": f"• {t}", "wrap": True, "spacing": "None"} for t in s["top_savings"]]
    card: dict[str, Any] = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                            "version": "1.4", "body": body}
    if s["link"]:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open report", "url": s["link"]}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                                                "contentUrl": None, "content": card}]}


# ------------------------------------------------------------------ sending


class WebhookError(RuntimeError):
    pass


def validate_webhook(url: str) -> str:
    """https only (plain http allowed for loopback test servers); no credentials in the URL."""
    u = urlsplit(url)
    host = u.hostname or ""
    loopback = host == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if u.scheme != "https" and not (u.scheme == "http" and loopback):
        raise WebhookError("webhook URL must use https")
    if u.username or u.password:
        raise WebhookError("webhook URL must not embed credentials")
    if not host:
        raise WebhookError("webhook URL has no host")
    return url


def post_json(url: str, payload: dict[str, Any], timeout: float = 15.0) -> int:
    req = urllib.request.Request(validate_webhook(url), data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "spendlens"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        raise WebhookError(f"HTTP {e.code} from webhook") from e
    except urllib.error.URLError as e:
        raise WebhookError(f"could not reach webhook: {e.reason}") from e


def _redact(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname}/…"


# ------------------------------------------------------------------ command


def notify(
    slack: Annotated[bool, typer.Option("--slack", help="Post to Slack (URL from $notifications.slack_webhook_env).")] = False,
    teams: Annotated[bool, typer.Option("--teams", help="Post to Teams (URL from $notifications.teams_webhook_env).")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the payload(s) instead of sending.")] = False,
    only_if_changes: Annotated[bool, typer.Option("--only-if-changes", help="Send only when something needs attention "
                                                  "(flagged change, anomaly, forecast over budget, new alarms).")] = False,
    link: Annotated[str | None, typer.Option("--link", help="Report URL for the 'Open report' button.")] = None,
    title: Annotated[str | None, typer.Option("--title", help="Message title (default: dashboard.title).")] = None,
    config: ConfigOpt = None,
    source: Annotated[Source, typer.Option("--source", help="last = your history DB; demo = bundled sample DB.")] = Source.last,
) -> None:
    """Post a summary of the latest saved runs to Slack and/or Teams (no AWS calls).

    Examples:
      spendlens notify --slack --teams --link "$CI_JOB_URL/artifacts/browse"
      spendlens notify --slack --only-if-changes
      spendlens notify --teams --dry-run --source demo
    """
    if not (slack or teams):
        raise typer.BadParameter("Choose --slack and/or --teams.", param_hint="--slack/--teams")
    if source is Source.live:
        raise typer.BadParameter("notify reads saved runs: use --source last or demo.", param_hint="--source")
    cfg = load(config)
    path = demo_db_path() if source is Source.demo else history_path(cfg)
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/] (run some checks first)")
        raise typer.Exit(1) from e
    with store:
        data = Data(store)
        changes = run_changes(store)
    if not data.runs:
        err.print("[red]No saved runs to summarise.[/]")
        raise typer.Exit(1)
    summary = build_summary(view_model(data, cfg, source), changes, cfg, link, title)
    if only_if_changes and not summary["noteworthy"]:
        err.print("[green]Nothing needs attention; no message sent (--only-if-changes).[/]")
        return
    targets = []
    if slack:
        targets.append(("Slack", cfg.notifications.slack_webhook_env, slack_payload(summary)))
    if teams:
        targets.append(("Teams", cfg.notifications.teams_webhook_env, teams_payload(summary)))
    failed = False
    for name, env, payload in targets:
        if dry_run:
            typer.echo(json.dumps({"target": name, "payload": payload}, indent=2))
            continue
        url = os.environ.get(env)
        if not url:
            err.print(f"[red]{name}:[/] set ${env} to the webhook URL (kept out of config files on purpose).")
            failed = True
            continue
        try:
            status = post_json(url, payload)
            err.print(f"[green]{name}:[/] sent to {_redact(url)} (HTTP {status})")
        except WebhookError as e:
            err.print(f"[red]{name}:[/] {e}")
            failed = True
    if failed:
        raise typer.Exit(1)


def register(reg: Registry) -> None:
    reg.add_command(notify, "notify")
