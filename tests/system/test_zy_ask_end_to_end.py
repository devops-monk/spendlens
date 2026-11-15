"""Runs after the feature end-to-end tests (file name) so the history DB exists. spendlens ask --live end to end: real CLI -> real openai SDK -> local stub LLM, and the model's SQL ->
read-only Steampipe -> aws plugin -> Moto. No real AWS, no AI provider, no API key."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stubllm import StubLLM

pytestmark = pytest.mark.system


def _ask(spendlens, stub, *args):
    return spendlens("ask", *args, "--json", "--set", "llm.provider=openai_compatible", "--set", "llm.model=stub",
                     "--set", f"llm.base_url={stub.url}/v1", expect_json=False)


def test_ask_live_query_reaches_emulated_aws(system_env, spendlens):
    script = [("tool", "steampipe_query", {"sql": "select instance_id, instance_type from aws_ec2_instance "
                                                  "order by instance_type"}),
              ("final", "Instances: {last}")]
    with StubLLM(script) as stub:
        code, out, err = _ask(spendlens, stub, "--live", "Which EC2 instances exist?")
    assert code == 0, err
    doc = json.loads(out[out.index("{"):])
    assert system_env["ids"]["ec2_bad"] in doc["answer"] and "t2.medium" in doc["answer"]
    assert doc["steps"][0]["tool"] == "steampipe_query" and doc["steps"][0]["ok"]


def test_ask_live_write_is_blocked(system_env, spendlens):
    script = [("tool", "steampipe_query", {"sql": "delete from aws_ec2_instance"}), ("final", "Result: {last}")]
    with StubLLM(script) as stub:
        code, out, err = _ask(spendlens, stub, "--live", "Delete my instances")
    assert code == 0, err
    doc = json.loads(out[out.index("{"):])
    assert doc["steps"][0]["ok"] is False and "only SELECT" in doc["steps"][0]["error"]


def test_ask_answers_from_live_history(system_env, spendlens):
    sql = ("select count(*) from findings where status = 'alarm' and run_id = "
           "(select max(id) from runs where feature = 'waste')")
    with StubLLM([("tool", "history_query", {"sql": sql}), ("final", "Waste alarms: {last}")]) as stub:
        code, out, err = _ask(spendlens, stub, "How many waste alarms?")
    assert code == 0, err
    doc = json.loads(out[out.index("{"):])
    assert doc["steps"][0]["ok"] and doc["steps"][0]["rows"] == 1
    assert "steampipe_query" not in {t["function"]["name"] for t in stub.requests[0]["body"]["tools"]}  # no --live
