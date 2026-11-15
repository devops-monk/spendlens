import types

import typer
from typer.testing import CliRunner

from spendlens.plugins import AITool, Registry, load_plugins

runner = CliRunner()




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

