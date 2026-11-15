from typer.testing import CliRunner

from spendlens import doctor

runner = CliRunner()




def test_missing_binary(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: None)
    c = doctor.check_binary("powerpipe", "brew install turbot/tap/powerpipe")
    assert c.status == "fail" and "brew" in c.fix



def test_sts_command_forces_endpoint(monkeypatch):
    from spendlens.common import sts_command

    monkeypatch.setattr("spendlens.common.shutil.which", lambda _n: "/usr/bin/aws")
    monkeypatch.delenv("AWS_ENDPOINT_URL_STS", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    assert "--endpoint-url" not in sts_command()
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:5000")
    cmd = sts_command("p1")
    assert cmd[-4:] == ["--profile", "p1", "--endpoint-url", "http://127.0.0.1:5000"]
