from typer.testing import CliRunner

from spendlens import doctor
from spendlens.cli import app
from spendlens.config import SteampipeConfig
from spendlens.steampipe import build_dsn

runner = CliRunner()


def test_build_dsn_explicit():
    assert build_dsn(SteampipeConfig(dsn="postgresql://x@h:1/d")) == "postgresql://x@h:1/d"


def test_build_dsn_password_quoted():
    dsn = build_dsn(SteampipeConfig(), password="p@ss/word")
    assert dsn == "postgresql://steampipe:p%40ss%2Fword@localhost:9193/steampipe"


def test_missing_binary(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: None)
    c = doctor.check_binary("powerpipe", "brew install turbot/tap/powerpipe")
    assert c.status == "fail" and "brew" in c.fix


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
