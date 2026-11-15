from typer.testing import CliRunner

from spendlens.cli import app

runner = CliRunner()


import json

from typer.testing import CliRunner

runner = CliRunner()




def test_init_and_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "finops.yaml").exists()
    assert runner.invoke(app, ["init"]).exit_code == 1  # refuses overwrite
    r = runner.invoke(app, ["config"])
    assert r.exit_code == 0 and "CostCentre" in r.output



def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and "spendlens" in r.output



def test_params_command_json():
    r = runner.invoke(app, ["params", "-f", "json", "--filter", "ec2_cpu", "-s", "sizing.ec2_cpu_low=5"])
    assert r.exit_code == 0, r.output
    rows = {row["parameter"]: row for row in json.loads(r.output)["rows"]}
    assert rows["sizing.ec2_cpu_low"]["value"] == 5 and rows["sizing.ec2_cpu_low"]["source"] == "--set"
    assert rows["sizing.ec2_cpu_high"]["env"] == "SPENDLENS__SIZING__EC2_CPU_HIGH"



def test_bad_set_exits_2():
    r = runner.invoke(app, ["params", "-s", "sizing.bogus=1"])
    assert r.exit_code == 2



def test_plugins_command():
    assert runner.invoke(app, ["plugins"]).exit_code == 0

