"""spendlens ask end to end with the REAL openai / anthropic SDKs against a local stub LLM (no network, no cost)."""

import json

import pytest
from stubllm import StubLLM
from typer.testing import CliRunner

from spendlens.cli import app

runner = CliRunner()

WASTE_ALARMS = ("select count(*) as alarms from findings where status = 'alarm' and run_id = "
                "(select max(id) from runs where feature = 'waste')")


def ask(stub, *args, provider="openai_compatible", extra_sets=()):
    sets = [f"llm.provider={provider}", "llm.model=stub-model"]
    if provider == "openai_compatible":
        sets.append(f"llm.base_url={stub.url}/v1")
    elif provider == "anthropic":
        sets.append(f"llm.base_url={stub.url}")
    elif provider == "azure":
        sets += [f"llm.base_url={stub.url}", "llm.api_key_env=STUB_KEY"]
    cmd = ["ask", "--source", "demo", *args]
    for s in [*sets, *extra_sets]:
        cmd += ["--set", s]
    return runner.invoke(app, cmd, env={"STUB_KEY": "x", "ANTHROPIC_API_KEY": "stub"})


def test_openai_compatible_full_loop_with_audit():
    script = [("tool", "history_overview", {}), ("tool", "history_query", {"sql": WASTE_ALARMS}),
              ("final", "There are waste alarms: {last}")]
    with StubLLM(script) as stub:
        r = ask(stub, "How many waste alarms?", "--json")
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout[r.stdout.index("{"):])
    assert '"alarms"' in doc["answer"] and "[[14]]" in doc["answer"]  # value flowed: demo DB -> tool -> model
    assert [s["tool"] for s in doc["steps"]] == ["history_overview", "history_query"]
    assert all(s["ok"] for s in doc["steps"])
    first, last = stub.requests[0]["body"], stub.requests[-1]["body"]
    assert {t["function"]["name"] for t in first["tools"]} == {"history_overview", "history_query"}
    assert first["tool_choice"] == "auto" and first["max_tokens"] == 16000 and "temperature" not in first
    assert first["messages"][0]["role"] == "system" and "read-only" in first["messages"][0]["content"]
    assert [m["role"] for m in last["messages"]][-2:] == ["assistant", "tool"]


def test_anthropic_request_shape_with_real_sdk():
    with StubLLM([("tool", "history_overview", {}), ("final", "done")]) as stub:
        r = ask(stub, "Overview please", provider="anthropic")
    assert r.exit_code == 0, r.output
    req = stub.requests[0]
    body, headers = req["body"], {k.lower(): v for k, v in req["headers"].items()}
    assert body["model"] == "stub-model" and body["max_tokens"] == 16000
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in headers.get("anthropic-beta", "")
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert all(t["strict"] is True and t["input_schema"]["additionalProperties"] is False for t in body["tools"])
    assert "temperature" not in body and "thinking" not in body and "tool_choice" not in body
    tr = stub.requests[1]["body"]["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "toolu_1" and tr["is_error"] is False


def test_anthropic_refusal_exit_code():
    with StubLLM([("refusal",)]) as stub:
        r = ask(stub, "x", provider="anthropic")
    assert r.exit_code == 4 and "declined" in r.output


def test_azure_uses_deployment_path():
    with StubLLM([("final", "hi")]) as stub:
        r = ask(stub, "hello", provider="azure")
    assert r.exit_code == 0, r.output
    assert "/openai/deployments/stub-model/chat/completions" in stub.requests[0]["path"]
    assert "api-version=2024-10-21" in stub.requests[0]["path"]


def test_guard_error_goes_back_to_model_and_show_tools():
    script = [("tool", "history_query", {"sql": "delete from findings"}),
              ("tool", "history_query", {"sql": "select count(*) n from runs"}), ("final", "runs: {last}")]
    with StubLLM(script) as stub:
        r = ask(stub, "count runs", "--show-tools")
    assert r.exit_code == 0, r.output
    tool_msg = stub.requests[1]["body"]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"].startswith("ERROR:") and "only SELECT" in tool_msg["content"]
    assert "Tool calls (audit trail)" in r.output and "delete from findings" in r.output


def test_step_limit():
    with StubLLM([("tool", "history_overview", {})]) as stub:  # never stops calling tools
        r = ask(stub, "loop", "--max-steps", "2", "--json")
    doc = json.loads(r.stdout[r.stdout.index("{"):])
    assert doc["hit_step_limit"] and len(doc["steps"]) == 2


def test_live_tools_only_with_flag():
    with StubLLM([("final", "ok")]) as stub:
        ask(stub, "x", "--live")
        live_tools = {t["function"]["name"] for t in stub.requests[0]["body"]["tools"]}
    assert "steampipe_query" in live_tools


def test_interactive_session_keeps_context():
    with StubLLM([("final", "first"), ("final", "second")]) as stub:
        cmd = ["ask", "-i", "--source", "demo", "--set", "llm.provider=openai_compatible", "--set", "llm.model=m",
               "--set", f"llm.base_url={stub.url}/v1"]
        r = runner.invoke(app, cmd, input="q1\nq2\nexit\n")
    assert r.exit_code == 0, r.output
    assert "first" in r.output and "second" in r.output
    roles = [m["role"] for m in stub.requests[1]["body"]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]  # follow-up carries the conversation


@pytest.mark.parametrize("args", [[], ["--source", "live", "x"]])
def test_bad_invocations(args):
    assert runner.invoke(app, ["ask", *args]).exit_code == 2


def test_doctor_reports_llm_without_calling_it():
    r = runner.invoke(app, ["doctor", "--offline", "--set", "llm.provider=openai", "--set", "llm.model=x"],
                      env={"COLUMNS": "200"})
    assert "ai assistant" in r.output and "OPENAI_API_KEY not set" in r.output
    assert "aws identity" not in r.output  # --offline makes no network calls (no STS)
