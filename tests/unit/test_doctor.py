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


def test_expired_login_is_not_reported_as_a_missing_permission():
    from spendlens.doctor import credential_fix

    sso = ("rpc error: get credentials: failed to refresh cached credentials, the SSO session has expired or is "
           "invalid: failed to read cached SSO token file")
    assert credential_fix(sso, "non-prd-fs", "Role needs ce:GetCostAndUsage").startswith(
        "Your AWS login has expired: aws sso login --profile non-prd-fs")
    assert "aws sso login" in credential_fix("Error loading SSO Token: Token for https://x/start does not exist", None, "d")
    assert credential_fix("AccessDeniedException: not authorized to perform ce:GetCostAndUsage", "p",
                          "Role needs ce:GetCostAndUsage") == "Role needs ce:GetCostAndUsage"
