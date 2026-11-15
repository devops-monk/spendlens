import json
import types

import pytest
import typer
from typer.testing import CliRunner

from spendlens.cli import app
from spendlens.config import ConfigError, env_assignments, load_config, param_keys
from spendlens.plugins import AITool, Registry, load_plugins

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SPENDLENS_CONFIG", raising=False)


def test_set_overrides_file_and_env(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text("sizing: {ec2_cpu_low: 15, ec2_cpu_high: 40}\n")
    env = {"SPENDLENS__SIZING__EC2_CPU_LOW": "12", "SPENDLENS__SIZING__EC2_CPU_HIGH": "45"}
    cfg = load_config(p, sets=["sizing.ec2_cpu_low=10"], environ=env)
    assert cfg.sizing.ec2_cpu_low == 10 and cfg.param_source("sizing.ec2_cpu_low") == "--set"
    assert cfg.sizing.ec2_cpu_high == 45 and cfg.param_source("sizing.ec2_cpu_high") == "env"
    assert cfg.param_source("sizing.ebs_volume_max_size_gb") == "default"


def test_set_yaml_values():
    cfg = load_config(sets=["tagging.mandatory=[Env, Owner, Env]", "budgets.per_tag={Team: {pay: 5000}}"],
                      environ={})
    assert cfg.tagging.mandatory == ["Env", "Owner"]
    assert cfg.budgets.per_tag == {"Team": {"pay": 5000.0}}


@pytest.mark.parametrize("bad", ["sizing.ec2_cpu_lo=1", "nope=1", "sizing=1x"])
def test_unknown_or_invalid_keys_rejected(bad):
    with pytest.raises(ConfigError):
        load_config(sets=[bad], environ={})


def test_range_validation():
    with pytest.raises(ConfigError, match="sizing.ec2_cpu_low"):
        load_config(sets=["sizing.ec2_cpu_low=150"], environ={})


def test_typo_in_file_rejected(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text("sizing: {ec2_cpu_lowe: 1}\n")
    with pytest.raises(ConfigError, match="ec2_cpu_lowe"):
        load_config(p, environ={})


def test_pp_vars_and_plugin_settings():
    cfg = load_config(pp_vars=["my_custom=7"], sets=["plugins.acme.gpu_idle_pct=5"], environ={})
    assert cfg.to_powerpipe_vars()["my_custom"] == 7
    assert cfg.plugins["acme"]["gpu_idle_pct"] == 5


def test_env_key_mapping():
    assert env_assignments({"SPENDLENS__LLM__MODEL": "x", "OTHER": "y"}) == [("llm.model", "x")]


def test_param_keys_cover_sections():
    keys = param_keys()
    assert "sizing.ec2_cpu_low" in keys and "llm.provider" in keys
    assert "budgets.per_tag" in keys  # dict-valued field stays whole


def test_params_command_json():
    r = runner.invoke(app, ["params", "-f", "json", "--filter", "ec2_cpu", "-s", "sizing.ec2_cpu_low=5"])
    assert r.exit_code == 0, r.output
    rows = {row["parameter"]: row for row in json.loads(r.output)["rows"]}
    assert rows["sizing.ec2_cpu_low"]["value"] == 5 and rows["sizing.ec2_cpu_low"]["source"] == "--set"
    assert rows["sizing.ec2_cpu_high"]["env"] == "SPENDLENS__SIZING__EC2_CPU_HIGH"


def test_bad_set_exits_2():
    r = runner.invoke(app, ["params", "-s", "sizing.bogus=1"])
    assert r.exit_code == 2


# ------------------------------------------------------------------ plugins


def test_builtin_plugin_registration(monkeypatch):
    sub = typer.Typer()

    @sub.command()
    def hello():
        typer.echo("hi from plugin")

    def register(reg: Registry):
        reg.add_typer(sub, "acme")
        reg.add_ai_tool(AITool("t", "d", {"type": "object"}, lambda: 1))

    mod = types.ModuleType("fake_feature")
    mod.register = register
    monkeypatch.setitem(__import__("sys").modules, "fake_feature", mod)

    reg = load_plugins(Registry(), builtins=("fake_feature",))
    assert reg.plugins[0].error is None and "t" in reg.ai_tools
    host = typer.Typer()

    @host.command()
    def noop():
        pass

    reg.apply(host)
    r = runner.invoke(host, ["acme", "hello"])
    assert r.exit_code == 0 and "hi from plugin" in r.output


def test_broken_plugin_does_not_crash():
    reg = load_plugins(Registry(), builtins=("spendlens.does_not_exist",))
    assert reg.plugins[0].error and "ModuleNotFoundError" in reg.plugins[0].error


def test_plugins_command():
    assert runner.invoke(app, ["plugins"]).exit_code == 0
