from typer.testing import CliRunner

from spendlens.config import SteampipeConfig
from spendlens.steampipe import build_dsn

runner = CliRunner()




def test_build_dsn_explicit():
    assert build_dsn(SteampipeConfig(dsn="postgresql://x@h:1/d")) == "postgresql://x@h:1/d"



def test_build_dsn_password_quoted():
    dsn = build_dsn(SteampipeConfig(), password="p@ss/word")
    assert dsn == "postgresql://steampipe:p%40ss%2Fword@localhost:9193/steampipe"

