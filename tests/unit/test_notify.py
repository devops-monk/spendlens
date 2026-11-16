import pytest

from spendlens.config import Config
from spendlens.features.notify import (
    WebhookError,
    _redact,
    build_summary,
    slack_payload,
    teams_payload,
    validate_webhook,
)


def vm(**over):
    base = {
        "account": "123456789012", "source": "last",
        "cost_diff": {"p0": "2026-09", "p1": "2026-10", "flagged": 1,
                      "total": {"compare": 55409.93, "change": 959.07, "change_pct": 1.8},
                      "bars": [{"label": "Amazon Redshift", "value": -3709.74}, {"label": "Amazon EC2", "value": 3.0}]},
        "forecast": {"account": {"forecast": 59120.8, "limit": 55000.0, "label": "forecast over budget"}},
        "anomalies": {"count": 1, "bars": [{"label": "EC2 (2026-11-07)", "value": 665.8}]},
        "savings": {"total": "$4,613", "yearly": "$55.4K", "table": {
            "cols": [{"key": "est_monthly_saving"}, {"key": "control"}, {"key": "resource"}],
            "rows": [[{"text": "1,240.00"}, {"text": "coh_purchasesavingsplans"}, {"text": "arn:sp"}]]}},
        "tags": {"overall": 92.2},
    }
    base.update(over)
    return base


def test_summary_facts_highlights_and_noteworthy():
    s = build_summary(vm(), {"tags": {"new_alarm": 2, "resolved": 5}}, Config(), "https://ci/report")
    facts = dict(s["facts"])
    assert facts["Spend 2026-10"] == "$55.4K (+1.8% vs 2026-09)"
    assert facts["Potential saving"] == "$4,613/month ($55.4K/year)" and facts["Tag compliance"] == "92.2%"
    assert any("Amazon Redshift" in h for h in s["highlights"])
    assert not any("Amazon EC2" in h for h in s["highlights"])  # below cost.change_alarm_abs
    assert any("forecast over budget" in h for h in s["highlights"])
    assert any("2 new alarm(s), 5 resolved" in h for h in s["highlights"])
    assert s["top_savings"] == ["1,240.00/mo · coh_purchasesavingsplans · arn:sp"] and s["noteworthy"]


def test_quiet_run_is_not_noteworthy():
    quiet = vm(cost_diff=None, anomalies=None, forecast={"account": {"forecast": 1, "limit": 10, "label": "on track"}})
    assert not build_summary(quiet, {"waste": {"new_alarm": 0, "resolved": 1}}, Config(), None)["noteworthy"]


def test_slack_payload_shape():
    p = slack_payload(build_summary(vm(), {}, Config(), "https://ci/report"))
    types = [b["type"] for b in p["blocks"]]
    assert types[0] == "header" and "actions" in types and p["text"].startswith("spendlens FinOps report:")
    assert p["blocks"][-1]["elements"][0]["text"].endswith("estimates for human review")
    assert len(p["blocks"][2]["fields"]) <= 10  # Slack's per-section field limit


def test_teams_adaptive_card_shape():
    p = teams_payload(build_summary(vm(), {}, Config(), "https://ci/report"))
    att = p["attachments"][0]
    assert p["type"] == "message" and att["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = att["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    assert any(b["type"] == "FactSet" for b in card["body"]) and card["actions"][0]["url"] == "https://ci/report"


@pytest.mark.parametrize("url", ["https://hooks.slack.com/services/T/B/x", "http://127.0.0.1:8080/hook",
                                 "http://localhost/hook", "http://[::1]:9/x"])
def test_webhook_urls_allowed(url):
    assert validate_webhook(url) == url


@pytest.mark.parametrize("url", ["http://hooks.example.com/x", "ftp://x/y", "https://user:pw@hooks.example.com/x",
                                 "https:///nohost", "file:///etc/passwd"])
def test_webhook_urls_rejected(url):
    with pytest.raises(WebhookError):
        validate_webhook(url)


def test_redact_hides_webhook_secret_path():
    assert _redact("https://hooks.slack.com/services/T000/B000/SECRET") == "https://hooks.slack.com/…"
