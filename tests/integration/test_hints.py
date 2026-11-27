"""After an answer: the commands that get the same data without the AI (built from the tool calls, no model)."""

import json
import shlex
from types import SimpleNamespace as S

from stubllm import StubLLM
from test_ask import ask
from typer.testing import CliRunner

from spendlens.ai.hints import command_for, commands
from spendlens.cli import app
from spendlens.features.cloudtrail_cmd import app as ct_app


def step(tool, ok=True, **args):
    return S(tool=tool, ok=ok, arguments=args)


def test_each_tool_maps_to_its_command() -> None:
    assert command_for("aws_query", {"sql": "select\n  instance_id\nfrom aws_ec2_instance"}) == \
        "spendlens query 'select instance_id from aws_ec2_instance'"
    assert command_for("cloudtrail_events", {"hours": 168, "events": ["UpdateService", "Delete*"], "user": "o'neil",
                                             "reads": "all", "errors_only": True, "source": "steampipe"}) == (
        "spendlens cloudtrail events --days 7 --user 'o'\"'\"'neil' -e UpdateService -e 'Delete*' --errors-only "
        "--reads all --source aws")
    assert command_for("cloudtrail_events", {"hours": 6, "preset": "deletes"}) == \
        "spendlens cloudtrail events --preset deletes --hours 6"
    assert command_for("cloudtrail_events", {"from": "2026-11-01", "to": "2026-11-20", "service": "iam"}) == \
        "spendlens cloudtrail events --from 2026-11-01 --to 2026-11-20 --service iam"
    assert command_for("export_query", {"sql": "select 1 from aws_vpc", "name": "v.xlsx", "engine": "aws"}) == \
        "spendlens query 'select 1 from aws_vpc' --limit 100000 -f csv -o v.csv"
    assert command_for("history_query", {"sql": "select 1"}).startswith('sqlite3 -header -column "$(spendlens history')
    assert command_for("history_query", {"sql": "select 1"}, "demo") is None
    assert command_for("cleanup_groups", {"min_saving": 20}, "demo") == \
        "spendlens graph orphans --source demo --min-saving 20"
    assert command_for("resource_context", {"resource": "vol-1"}) == "spendlens graph show vol-1 --source last"
    assert command_for("traffic_paths", {"path": "nat"}) == "spendlens graph traffic --source last --path nat"
    assert command_for("aws_describe_table", {"table": "x"}) is None


def test_generated_cloudtrail_commands_parse() -> None:
    cmd = command_for("cloudtrail_events", {"hours": 30, "events": ["UpdateService"], "source_ip": "10.0.0.0/8",
                                            "errors": ["AccessDenied*"], "region": "eu-west-1"})
    args = shlex.split(cmd)[3:]
    r = CliRunner().invoke(ct_app, ["events", *args, "--help"])
    assert r.exit_code == 0  # every flag exists on the real command


def test_only_successful_data_calls_newest_last_no_repeats() -> None:
    steps = [step("aws_find_tables", question="x"), step("aws_query", ok=False, sql="bad"),
             step("aws_query", sql="select 1 from aws_vpc"), step("aws_query", sql="select 1 from aws_vpc"),
             step("history_overview"), step("write_file", name="a.csv")]
    assert commands(steps) == ["spendlens query 'select 1 from aws_vpc'", "spendlens history list"]
    assert len(commands([step("aws_query", sql=f"select {i} from aws_vpc") for i in range(9)])) == 4
    assert commands([{"tool": "history_overview", "ok": True, "arguments": {}}]) == ["spendlens history list"]


def test_ask_prints_the_commands_and_json_carries_them() -> None:
    script = [("tool", "history_overview", {}), ("final", "Done: {last}")]
    with StubLLM(script) as stub:
        r = ask(stub, "What runs are saved?", "--fresh")
    assert r.exit_code == 0 and "Without AI" in r.output and "spendlens history list" in r.output
    with StubLLM(script) as stub:
        r = ask(stub, "What runs are saved?", "--fresh", "--no-hints")
    assert "Without AI" not in r.output
    with StubLLM(script) as stub:
        r = ask(stub, "What runs are saved?", "--fresh", "--json")
    assert json.loads(r.stdout[r.stdout.index("{"):])["cli_commands"] == ["spendlens history list"]
    with StubLLM(script) as stub:
        r = ask(stub, "What runs are saved?", extra_sets=["llm.command_hints=false"])
    assert "Without AI" not in r.output
    assert CliRunner().invoke(app, ["ask", "--help"]).exit_code == 0
