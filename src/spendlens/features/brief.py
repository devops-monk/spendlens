"""`spendlens brief`: one executive FinOps report for managers.

A single scrolling, printable page: the headline numbers, an analyst summary written by the FinOps AI profile, spend
trend and change charts, anomalies, savings proposals to review, forecast and tag governance.

Every number on the page is computed by spendlens from saved runs. The AI only reads those facts (and may query the
read-only history for detail) and writes the narrative; without an AI provider, or with --no-ai, a rule-based summary
takes its place, so the report is always produced.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..common import ConfigOpt, ProfileOpt, SetOpt, Source, err, history_path, load
from ..config import Config
from ..history import HistoryStore, demo_db_path
from ..plugins import Registry
from ..render import charts as ch
from ..render.html import render_template
from .dashboard import FEATURES, Data, _periods, _totals, merged_savings, savings_category, view_model

TOP_ACTIONS = 12
ANALYSIS_TTL_S = 7 * 86400  # keyed by the exact facts: the same data always gets the same summary
LEVELS = ("high", "medium", "low")
AREAS = ("forecast", "anomaly", "savings", "governance")

# ------------------------------------------------------------------ the analyst's output (AI or rule-based)

Level = Literal["high", "medium", "low"]
Area = Literal["cost", "forecast", "anomaly", "savings", "governance"]


def _level(v: Any) -> str:
    v = str(v or "medium").strip().lower()
    return v if v in LEVELS else "medium"


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Insight(_Loose):
    title: str
    detail: str = ""
    impact: Level = "medium"
    area: Area = "cost"

    @field_validator("impact", mode="before")
    @classmethod
    def _impact(cls, v: Any) -> str:
        return _level(v)

    @field_validator("area", mode="before")
    @classmethod
    def _area(cls, v: Any) -> str:
        v = str(v or "cost").lower()
        return next((a for a in AREAS if a in v), "cost")


class Action(_Loose):
    action: str
    why: str = ""
    est_monthly_saving: float | None = None
    effort: Level = "medium"
    owner: str = ""

    @field_validator("effort", mode="before")
    @classmethod
    def _effort(cls, v: Any) -> str:
        return _level(v)

    @field_validator("est_monthly_saving", mode="before")
    @classmethod
    def _num(cls, v: Any) -> float | None:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, int | float):
            return float(v)
        try:
            return float(re.sub(r"[^0-9.\-]", "", str(v)))
        except ValueError:
            return None


class AnomalyNote(_Loose):
    date: str = ""
    group: str = ""
    explanation: str = ""


class Analysis(_Loose):
    headline: str
    summary: str = ""
    insights: list[Insight] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    anomaly_notes: list[AnomalyNote] = Field(default_factory=list)
    # set by spendlens, never by the model
    by: str = "rules"  # "ai" | "rules"
    model: str = ""
    notes: list[str] = Field(default_factory=list)


SCHEMA_HINT = """{
  "headline": "one sentence a manager reads first, with the key number(s)",
  "summary": "2-4 sentences: what happened to spend, why, and what needs attention",
  "insights": [{"title": "...", "detail": "1-2 sentences with numbers", "impact": "high|medium|low",
                "area": "cost|forecast|anomaly|savings|governance"}],
  "actions": [{"action": "imperative, specific", "why": "evidence", "est_monthly_saving": 123.45 or null,
               "effort": "low|medium|high", "owner": "team or role, if the data shows it"}],
  "risks": ["..."],
  "anomaly_notes": [{"date": "YYYY-MM-DD", "group": "service", "explanation": "likely cause, or what to check"}]
}"""

BRIEF_TASK = f"""\
Write the executive FinOps brief for managers, from the computed facts below.

Rules:
- Audience: engineering and finance managers. Plain English, no SQL, no jargon without a short explanation.
- Every number must come from the facts or from a tool result; do not invent or extrapolate figures.
- You may call the history tools (a few calls at most) to explain drivers, e.g. which resources or usage types \
lie behind the biggest increase, or what else changed on an anomaly day.
- Savings are proposals for human review, never instructions to change resources.
- 3-6 insights ordered by impact, 3-6 actions ordered by value, at most 4 risks, one note per anomaly listed.

Reply with ONLY one JSON object (no markdown fences, no text before or after) of this shape:
{SCHEMA_HINT}

Facts (JSON):
"""

# ------------------------------------------------------------------ helpers

ACRONYMS = {"ebs": "EBS", "ec2": "EC2", "rds": "RDS", "eip": "EIP", "elb": "ELB", "alb": "ALB", "nlb": "NLB",
            "az": "AZ", "piops": "provisioned IOPS", "nat": "NAT", "ecs": "ECS", "eks": "EKS", "s3": "S3",
            "sp": "Savings Plan", "ri": "RI", "cpu": "CPU", "iops": "IOPS", "dynamodb": "DynamoDB"}


def _short(resource: str | None) -> str:
    r = resource or "-"
    return (re.split(r"[/:]", r)[-1] or r) if r.startswith("arn:") else r


def _human(control: str | None, reason: str | None = None) -> str:
    """'ebs_volume_piops_to_gp3' -> 'EBS volume provisioned IOPS to gp3'; Cost Optimization Hub ('coh_*') -> its
    action type from the reason, e.g. 'PurchaseSavingsPlans: ...' -> 'Purchase savings plans'."""
    c = control or "-"
    if c.startswith("coh_") and reason and ":" in reason:
        words = re.findall(r"[A-Z][a-z]*|[a-z]+|\d+", reason.split(":", 1)[0])
        text = " ".join(w.lower() for w in words)
        if text:
            return text[:1].upper() + text[1:]
    text = " ".join(ACRONYMS.get(w, w) for w in c.split("_"))
    return text[:1].upper() + text[1:]


def _effort(control: str | None) -> str:
    c = control or ""
    return "low" if re.search(r"unattached|unused|idle|old|orphan|snapshot|retention|stopped|eip", c) else "medium"


def _anomaly_rows(d: Data) -> list[dict[str, Any]]:
    rows = [{**f.dimensions, "severity": f.severity.value} for f in d.findings.get("anomalies", [])]
    return sorted(rows, key=lambda r: -(r.get("delta") or 0))


def _by(rows: list[dict[str, Any]], key: Any) -> dict[str, float]:
    acc: dict[str, float] = defaultdict(float)
    for r in rows:
        acc[key(r)] += r["est_monthly_saving"] or 0
    return {k: round(v, 2) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])}


def _savings_rows(d: Data) -> list[dict[str, Any]] | None:
    m = merged_savings(d)
    return None if m is None else sorted(m[0], key=lambda r: -(r["est_monthly_saving"] or 0))


# ------------------------------------------------------------------ facts (computed, never by the model)


def monthly(d: Data, vm: dict[str, Any]) -> list[dict[str, Any]]:
    """Total spend per month (all groups) from cost_trend, plus this month's forecast when known."""
    out: list[dict[str, Any]] = []
    if d.has("cost_trend"):
        run, rows = d.runs["cost_trend"], d.cost["cost_trend"]
        out = [{"label": p.label, "value": round(sum(_totals(rows, f"p{i}").values()), 2)}
               for i, p in enumerate(_periods(run))]
    elif cd := vm.get("cost_diff"):
        out = [{"label": cd["p0"], "value": cd["total"]["base"]}, {"label": cd["p1"], "value": cd["total"]["compare"]}]
    acct = (vm.get("forecast") or {}).get("account") or {}
    if acct.get("forecast") is not None and d.has("forecast"):
        month = d.runs["forecast"].started_at[:7]
        if not out or out[-1]["label"] < month:
            out.append({"label": month, "value": acct["forecast"], "forecast": True, "actual": acct.get("actual")})
    return out


def facts(d: Data, vm: dict[str, Any], cfg: Config) -> dict[str, Any]:
    cur = cfg.pricing.currency
    f: dict[str, Any] = {"currency": cur, "account": d.account,
                         "data_as_of": {k: r.started_at[:10] for k, r in d.runs.items()}}
    if months := monthly(d, vm):
        f["monthly_spend"] = months
    if cd := vm.get("cost_diff"):
        rows = [{"group": r["group"], "before": r["base"], "after": r["compare"], "change": r["change"],
                 "change_pct": r["change_pct"], "status": r["status"]} for r in cd["rows"]]
        f["cost_change"] = {
            "from": cd["p0"], "to": cd["p1"], "group_by": cd["group_by"], "total": cd["total"], "flag_rule": cd["rule"],
            "top_increases": [r for r in sorted(rows, key=lambda r: -r["change"]) if r["change"] > 0][:6],
            "top_decreases": [r for r in sorted(rows, key=lambda r: r["change"]) if r["change"] < 0][:4],
            "flagged": [r["group"] for r in cd["flagged_rows"]],
        }
    if (fc := vm.get("forecast")) and fc.get("account"):
        f["forecast_this_month"] = fc["account"]
    if vm.get("anomalies") is not None:
        f["anomalies"] = [{k: r.get(k) for k in ("date", "group", "cost", "baseline", "delta", "severity")}
                          for r in _anomaly_rows(d)][:10]
        f["anomaly_count"] = vm["anomalies"]["count"]
    if (merged := _savings_rows(d)) is not None:
        total = round(sum(r["est_monthly_saving"] or 0 for r in merged), 2)
        f["savings"] = {
            "monthly_total": total, "yearly_total": round(total * 12, 2), "items": len(merged),
            "by_source": _by(merged, savings_category),
            "by_service": dict(list(_by(merged, lambda r: r["service"]).items())[:8]),
            "top": [{"resource": _short(r["resource"]), "service": r["service"], "check": r["control"],
                     "est_monthly_saving": r["est_monthly_saving"], "reason": (r.get("reason") or "")[:160],
                     "source": r["source"]} for r in merged[:15]],
        }
    if (g := vm.get("tags")) and g.get("overall") is not None:
        f["tag_compliance_pct"] = g["overall"]
        f["mandatory_tags"] = cfg.tagging.mandatory
    if sb := vm.get("showback"):
        f["showback"] = [{"tag": s["key"], "untagged_share": s["untagged_pct"],
                          "top": [{"value": b["label"], "amount": b["value"]} for b in s["bars"][:6]]} for s in sb]
    return f


# ------------------------------------------------------------------ rule-based analyst (no AI)


def rules(fx: dict[str, Any]) -> Analysis:
    cur = fx["currency"]

    def m(v: float | None) -> str:
        return ch.money(v, cur)

    ins: list[Insight] = []
    risks: list[str] = []
    head: list[str] = []
    if cc := fx.get("cost_change"):
        t = cc["total"]
        word = "up" if t["change"] > 0 else "down"
        head.append(f"Spend was {m(t['compare'])} in {cc['to']}, {word} {m(abs(t['change']))}"
                    + (f" ({ch.pct(t['change_pct'], True)})" if t.get("change_pct") is not None else "")
                    + f" on {cc['from']}")
        if up := cc["top_increases"]:
            u = up[0]
            ins.append(Insight(title=f"Biggest increase: {u['group']}", area="cost",
                               impact="high" if u["change"] >= abs(t["change"] or 1) * 0.5 else "medium",
                               detail=f"{m(u['before'])} → {m(u['after'])} ({ch.signed_money(u['change'], cur)}). "
                                      "Drill down with spendlens cost diff -g service -b usage-type."))
        if cc["flagged"]:
            ins.append(Insight(title=f"{len(cc['flagged'])} cost change(s) crossed your thresholds", area="cost",
                               impact="medium", detail=f"{', '.join(cc['flagged'][:5])}. Rule: {cc['flag_rule']}."))
    if fc := fx.get("forecast_this_month"):
        limit, proj = fc.get("limit"), fc.get("forecast")
        if limit and proj is not None:
            over = proj - limit
            head.append(f"this month is forecast at {m(proj)}, "
                        + (f"{m(over)} over the {m(limit)} budget" if over > 0 else f"within the {m(limit)} budget"))
            ins.append(Insight(title="Forecast " + ("over budget" if over > 0 else "within budget"), area="forecast",
                               impact="high" if over > 0 else "low",
                               detail=f"Projected month end {m(proj)} against a {m(limit)} budget "
                                      f"({ch.pct(fc.get('forecast_pct_of_limit'))}); {m(fc.get('actual'))} spent so far."))
            if over > 0:
                risks.append(f"The month is projected to end {m(over)} over budget unless spend slows.")
    if an := fx.get("anomalies"):
        a = an[0]
        ins.append(Insight(title=f"{fx.get('anomaly_count', len(an))} unusual daily spike(s)", area="anomaly",
                           impact="high" if any(x["severity"] in ("high", "critical") for x in an) else "medium",
                           detail=f"Largest: {a['group']} on {a['date']}, {m(a['cost'])} against a usual "
                                  f"{m(a['baseline'])} (+{m(a['delta'])})."))
        if any(x["severity"] == "critical" for x in an):
            risks.append("A critical cost spike may still be running; confirm it has stopped.")
    actions: list[Action] = []
    if sv := fx.get("savings"):
        ins.append(Insight(title=f"{m(sv['monthly_total'])}/month of savings to review", area="savings", impact="high",
                           detail=f"{sv['items']} proposal(s), about {m(sv['yearly_total'])} a year. "
                                  f"Largest source: {next(iter(sv['by_source']), '-')}."))
        head.append(f"{m(sv['monthly_total'])}/month of savings are ready for review")
        for r in sv["top"][:5]:
            actions.append(Action(action=f"{_human(r['check'], r['reason'])}: {r['resource']}", why=r["reason"],
                                  est_monthly_saving=r["est_monthly_saving"], effort=_effort(r["check"])))
    if (tc := fx.get("tag_compliance_pct")) is not None:
        untagged = next((s["untagged_share"] for s in fx.get("showback", []) if s.get("untagged_share")), None)
        ins.append(Insight(title=f"Tag compliance {ch.pct(tc)}", area="governance",
                           impact="medium" if tc < 95 else "low",
                           detail=f"Mandatory tags: {', '.join(fx.get('mandatory_tags', []))}."
                                  + (f" {untagged} of spend has no owner tag, so it cannot be charged back."
                                     if untagged else "")))
        if tc < 90:
            risks.append("Under 90% tag compliance: part of the spend cannot be attributed to a team.")
    ins.sort(key=lambda i: LEVELS.index(i.impact))
    if head:
        headline = "; ".join(head[:2]) + "."
        summary = " ".join(h[:1].upper() + h[1:] + "." for h in head[2:])
    else:
        headline, summary = "No saved runs yet: run spendlens report to fill this brief.", ""
    return Analysis(headline=headline[:1].upper() + headline[1:], summary=summary, insights=ins[:6],
                    actions=actions, risks=risks, by="rules")


# ------------------------------------------------------------------ AI analyst


def _extract_json(text: str) -> dict[str, Any]:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the answer contained no JSON object")
    return json.loads(t[start:end + 1])


def ai_analysis(cfg: Config, db: Path, fx: dict[str, Any], max_steps: int | None, *, fresh: bool = False) -> Analysis:
    """Ask the configured LLM (FinOps analyst profile, read-only history tools). Raises on any failure.
    Cached by the exact facts + model: regenerating the brief on unchanged data costs no tokens."""
    from ..ai.agent import Agent
    from ..ai.cache import AICache, digest
    from ..ai.prompts import system_prompt
    from ..ai.provider import build_provider
    from ..ai.tools import Toolbox
    from ..ui import activity

    provider = build_provider(cfg)
    cache = AICache.for_config(cfg, history_path(cfg))
    key = digest("brief", fx, provider.label, cfg.llm.model, BRIEF_TASK)
    if not fresh and (hit := cache.get("brief", key)):
        cache.close()
        a = Analysis.model_validate(hit[0])
        a.notes.append(f"AI summary reused from {datetime.fromtimestamp(hit[1], UTC):%Y-%m-%d %H:%M UTC} "
                       "(same data; --fresh to regenerate).")
        return a
    toolbox = Toolbox(cfg, db, live=False)
    try:
        agent = Agent(provider, toolbox, system_prompt(cfg, False), max_steps or min(cfg.llm.max_steps, 6))
        with activity(f"[bold]AI analyst[/] · {provider.label} · {cfg.llm.model}"):
            ans = agent.ask(BRIEF_TASK + json.dumps(fx, default=str, separators=(",", ":")))
    finally:
        toolbox.close()
    if ans.refused:
        raise RuntimeError("the model declined to answer")
    a = Analysis.model_validate(_extract_json(ans.text))
    cap = ((fx.get("savings") or {}).get("monthly_total") or 0) * 1.01
    for act in a.actions:  # a figure above every saving found put together cannot come from the data
        if act.est_monthly_saving is not None and not 0 <= act.est_monthly_saving <= cap:
            act.est_monthly_saving = None
    a.by, a.model, a.notes = "ai", ans.model or cfg.llm.model, list(ans.notes)
    a.insights, a.actions, a.risks = a.insights[:6], a.actions[:6], a.risks[:4]
    cache.put("brief", key, a.model_dump(mode="json"), ANALYSIS_TTL_S, label=a.headline)
    cache.close()
    return a


# ------------------------------------------------------------------ page view model


def _kpis(fx: dict[str, Any], cfg: Config) -> list[dict[str, str]]:
    cur = fx["currency"]
    out = []
    if cc := fx.get("cost_change"):
        t = cc["total"]
        out.append({"k": f"Spend in {cc['to']}", "v": ch.money(t["compare"], cur),
                    "d": f"{ch.signed_money(t['change'], cur)} ({ch.pct(t['change_pct'], True)}) vs {cc['from']}",
                    "tone": "bad" if t["change"] > 0 else "good"})
    acct = fx.get("forecast_this_month") or {}
    if acct.get("forecast") is not None:
        pct = acct.get("forecast_pct_of_limit") or 0
        out.append({"k": "Forecast this month", "v": ch.money(acct["forecast"], cur),
                    "d": (f"{ch.pct(pct)} of {ch.money(acct['limit'], cur)} budget" if acct.get("limit")
                          else "no budget set"),
                    "tone": "bad" if pct > 100 else "warn" if pct > cfg.budgets.warn_pct else "good"})
    if sv := fx.get("savings"):
        out.append({"k": "Savings to review", "v": ch.money(sv["monthly_total"], cur) + "/mo",
                    "d": f"{ch.money(sv['yearly_total'], cur)} a year · {sv['items']} proposals", "tone": "good"})
    if "anomaly_count" in fx:
        excess = sum(a["delta"] or 0 for a in fx["anomalies"])
        out.append({"k": "Cost anomalies", "v": str(fx["anomaly_count"]),
                    "d": f"+{ch.money(excess, cur)} excess spend" if fx["anomalies"] else "spend within normal range",
                    "tone": "bad" if fx["anomaly_count"] else "good"})
    if (tc := fx.get("tag_compliance_pct")) is not None:
        out.append({"k": "Tag compliance", "v": ch.pct(tc), "d": "resources with mandatory tags",
                    "tone": "good" if tc >= 95 else "warn" if tc >= 80 else "bad"})
    return out


def page(d: Data, cfg: Config, source: Source, analysis: Analysis, fx: dict[str, Any]) -> dict[str, Any]:
    cur = cfg.pricing.currency
    vm = view_model(d, cfg, source)
    months = fx.get("monthly_spend") or []
    cols = []
    for i, mo in enumerate(months):
        is_last_actual = not mo.get("forecast") and (i == len(months) - 1 or months[i + 1].get("forecast"))
        kind = "forecast" if mo.get("forecast") else "latest" if is_last_actual else ""
        if mo.get("forecast"):
            tip = f"{mo['label']}: forecast {ch.money(mo['value'], cur)} ({ch.money(mo.get('actual'), cur)} so far)"
        else:
            tip = f"{mo['label']}: {ch.money(mo['value'], cur)}"
            if i and months[i - 1]["value"]:
                prev = months[i - 1]
                tip += f" ({ch.pct((mo['value'] - prev['value']) / prev['value'] * 100, True)} vs {prev['label']})"
        cols.append({"label": mo["label"], "value": mo["value"], "kind": kind, "tip": tip})
    actions = [{"id": r["resource"], "what": _human(r["control"], r.get("reason")), "resource": _short(r["resource"]),
                "full": r["resource"], "service": r["service"], "reason": r.get("reason") or "",
                "monthly": ch.money(r["est_monthly_saving"], cur, compact=False),
                "yearly": ch.money((r["est_monthly_saving"] or 0) * 12, cur),
                "source": "AWS" if r["source"] == "cost_optimization_hub" else "spendlens",
                "effort": _effort(r["control"])}
               for r in (_savings_rows(d) or [])[:TOP_ACTIONS]]
    sv = fx.get("savings") or {}
    by_service = [{"label": k, "value": v, "tip": f"{k}: {ch.money(v, cur)}/month"}
                  for k, v in (sv.get("by_service") or {}).items() if v > 0]
    notes = {(n.date, n.group): n.explanation for n in analysis.anomaly_notes}
    anomalies = [{**r, "cost_s": ch.money(r.get("cost"), cur), "baseline_s": ch.money(r.get("baseline"), cur),
                  "delta_s": ch.signed_money(r.get("delta") or 0, cur),
                  "note": notes.get((str(r.get("date")), str(r.get("group"))), "")} for r in _anomaly_rows(d)[:8]]
    started = [r.started_at for r in d.runs.values()]
    return {
        "currency": cur, "kpis": _kpis(fx, cfg), "a": analysis, "months": cols,
        "budget": (fx.get("forecast_this_month") or {}).get("limit"),
        "cost_diff": vm["cost_diff"], "trend": vm["trend"], "showback": vm["showback"][:1],
        "anomalies": anomalies, "anomalies_run": d.has("anomalies"),
        "anomaly_bars": (vm.get("anomalies") or {}).get("bars", [])[:8],
        "savings": vm["savings"], "by_service": by_service, "actions": actions,
        "forecast": vm["forecast"], "tags": vm["tags"],
        "ages": [{"section": k.replace("_", " "), "run": r.id, "date": r.started_at[:16].replace("T", " ")}
                 for k, r in d.runs.items()],
        "freshest": max(started, default="")[:10], "oldest": min(started, default="")[:10],
    }


def build(d: Data, cfg: Config, source: Source, analysis: Analysis, fx: dict[str, Any]) -> str:
    vm = page(d, cfg, source, analysis, fx)
    demo = " · DEMO DATA" if source is Source.demo else ""
    gen = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    by = f"AI analysis by {analysis.model}" if analysis.by == "ai" else "rule-based summary"
    footer = (f"Generated by spendlens {gen} from saved runs ({source.value}){demo} · {by}. Every figure is computed "
              "from your data; savings are estimates for human review; spendlens never changes resources.")
    return render_template(
        "brief.html.j2", title=f"{cfg.dashboard.title}: executive brief", footer=footer,
        subtitle=" · ".join([f"AWS account {d.account}" if d.account else "AWS", gen]) + demo,
        vm=vm, view_model={"facts": fx, "analysis": analysis.model_dump(mode="json")},
    )


# ------------------------------------------------------------------ command


def brief(
    out: Annotated[Path, typer.Option("--out", "-o", help="Output HTML file.")] = Path("spendlens-brief.html"),
    ai: Annotated[bool, typer.Option("--ai/--no-ai", help="Write the summary with your AI provider (llm.* in "
                                                          "finops.yaml), or use the built-in rule-based summary.")] = True,
    source: Annotated[Source, typer.Option(
        "--source", help="live = reuse today's saved runs and run every other section now; last = saved runs only, "
                         "no AWS calls; demo = bundled sample DB.")] = Source.live,
    refresh: Annotated[bool, typer.Option("--refresh", help="Re-run every section live now, even ones run today.")] = False,
    max_steps: Annotated[int | None, typer.Option("--max-steps", help="Tool calls the AI may make (default 6).")] = None,
    fresh: Annotated[bool, typer.Option("--fresh", help="Write a new AI summary even if one exists for this data.")] = False,
    config: ConfigOpt = None,
    profile: ProfileOpt = None,
    sets: SetOpt = None,
) -> None:
    """One executive FinOps report for managers: AI summary, charts, anomalies and savings to review.

    Spend trend vs budget, what changed, anomalies, savings proposals with a review checklist, forecast and tag
    governance on one printable page. The AI (FinOps analyst profile, read-only) writes the narrative from numbers
    spendlens computed; --no-ai uses a rule-based summary and sends nothing to an AI provider.
    Examples:
      spendlens brief -o brief.html
      spendlens brief --source demo -o brief.html
      spendlens brief --source last --no-ai -o brief.html
      spendlens brief --refresh -o brief.html
    """
    cfg = load(config, profile, sets)
    path = demo_db_path() if source is Source.demo else history_path(cfg)
    if source is Source.live:
        from .. import refresh as rf

        rf.ensure_fresh(path, FEATURES, config=config, profile=profile, refresh=refresh)
    elif refresh:
        raise typer.BadParameter("--refresh runs sections live: drop --source last/demo.", param_hint="--refresh")
    try:
        store = HistoryStore(path, read_only=True)
    except FileNotFoundError as e:
        err.print(f"[red]{e}[/] (run some checks first, or try --source demo)")
        raise typer.Exit(1) from e
    with store:
        data = Data(store)
    shown = Source.last if source is Source.live else source
    fx = facts(data, view_model(data, cfg, shown), cfg)
    analysis = rules(fx)
    if ai and data.runs:
        try:
            analysis = ai_analysis(cfg, path, fx, max_steps, fresh=fresh)
        except Exception as e:  # noqa: BLE001 - the report must still be written
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            err.print(f"[yellow]AI summary unavailable ({msg}); using the rule-based summary.[/]")
            analysis.notes.append(f"AI summary unavailable ({msg}); this is the rule-based summary.")
    html = build(data, cfg, shown, analysis, fx)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    by = f"AI analysis by {analysis.model}" if analysis.by == "ai" else "rule-based summary"
    err.print(f"[green]Wrote[/] {out} ({len(html) // 1024} KiB · {by})")


def register(reg: Registry) -> None:
    reg.add_command(brief, "brief")
