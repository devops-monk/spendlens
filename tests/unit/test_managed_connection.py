"""The Steampipe connection spendlens manages from aws.regions / aws.profile (no Steampipe needed)."""

from __future__ import annotations

import pytest

from spendlens.config import load_config
from spendlens.steampipe import (
    SteampipeError,
    ensure_connection,
    is_managed,
    managed_spc,
    managed_spc_path,
    wait_for_connection,
    write_managed_connection,
)

pytestmark = pytest.mark.unit


def _cfg(tmp_path, *sets):
    return load_config(sets=[f"steampipe.install_dir={tmp_path}", *sets], environ={})


def test_default_is_eu_west_1_and_managed(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.aws.regions == ["eu-west-1"]
    assert cfg.steampipe.connection == "spendlens" and is_managed(cfg.steampipe)


def test_spc_content_regions_and_profile():
    text = managed_spc(["eu-west-1", "us-east-1"], "finops")
    assert 'connection "spendlens" {' in text and 'plugin  = "aws"' in text
    assert 'regions = ["eu-west-1", "us-east-1"]' in text
    assert 'profile = "finops"' in text
    assert "profile =" not in managed_spc(["*"], None)


def test_regions_can_be_changed_in_every_layer(tmp_path):
    assert _cfg(tmp_path, 'aws.regions=["us-east-1"]').aws.regions == ["us-east-1"]
    cfg = load_config(sets=[f"steampipe.install_dir={tmp_path}"], environ={"SPENDLENS__AWS__REGIONS": '["eu-*"]'})
    assert cfg.aws.regions == ["eu-*"]
    (tmp_path / "finops.yaml").write_text("aws:\n  regions: [ap-southeast-2]\n")
    cfg = load_config(tmp_path / "finops.yaml", sets=[f"steampipe.install_dir={tmp_path}"], environ={})
    assert cfg.aws.regions == ["ap-southeast-2"]


def test_write_only_when_changed(tmp_path):
    cfg = _cfg(tmp_path)
    assert write_managed_connection(cfg.steampipe) is True
    path = managed_spc_path(cfg.steampipe)
    assert path == tmp_path / "config" / "spendlens.spc" and '"eu-west-1"' in path.read_text()
    assert write_managed_connection(cfg.steampipe) is False          # unchanged: no rewrite, no reload
    cfg2 = _cfg(tmp_path, 'aws.regions=["eu-west-1","eu-west-2"]')
    assert write_managed_connection(cfg2.steampipe) is True
    assert '"eu-west-2"' in path.read_text()


@pytest.mark.parametrize("sets", [["steampipe.connection=aws"], ["steampipe.connection=aws_all"],
                                  ["steampipe.dsn=postgresql://u:p@db:5432/steampipe"],
                                  ["steampipe.host=10.0.0.5"]])
def test_never_touches_user_connections_or_remote(tmp_path, sets):
    cfg = _cfg(tmp_path, *sets)
    assert not is_managed(cfg.steampipe)
    ensure_connection(cfg.steampipe, conn=None)                       # no-op: would fail if it used conn
    assert not (tmp_path / "config").exists()


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        assert "steampipe_internal.steampipe_connection" in sql and params == ("spendlens",)

    def fetchone(self):
        return self.rows.pop(0) if len(self.rows) > 1 else self.rows[0]


class _Conn:
    def __init__(self, *rows):
        self.rows = list(rows)

    def cursor(self):
        return _Cur(self.rows)


def test_wait_until_ready_after_edit():
    sleeps = []
    conn = _Conn(None, {"state": "updating", "error": None, "mod": 50.0},
                 {"state": "ready", "error": None, "mod": 60.0}, {"state": "ready", "error": None, "mod": 101.0})
    wait_for_connection(conn, "spendlens", since=100.0, sleep=sleeps.append)
    assert len(sleeps) == 3                                            # waited for the reload, not the old config


def test_unchanged_and_ready_returns_immediately():
    sleeps = []
    wait_for_connection(_Conn({"state": "ready", "error": None, "mod": 1.0}), "spendlens", since=None,
                        sleep=sleeps.append)
    assert sleeps == []


def test_connection_error_is_reported():
    with pytest.raises(SteampipeError, match="bad profile"):
        wait_for_connection(_Conn({"state": "error", "error": "bad profile", "mod": 1.0}), "spendlens", since=None,
                            sleep=lambda s: None)


def test_times_out_with_restart_hint():
    with pytest.raises(SteampipeError, match="steampipe service restart"):
        wait_for_connection(_Conn({"state": "pending", "error": None, "mod": None}), "spendlens", since=None,
                            timeout_s=0, sleep=lambda s: None)


def test_empty_regions_from_older_finops_yaml_mean_default(tmp_path):
    assert _cfg(tmp_path, "aws.regions=[]").aws.regions == ["eu-west-1"]


@pytest.mark.parametrize("bad", ['aws.regions=["EU-WEST-1"]', 'aws.regions=["eu-west-1\\""]', 'aws.profile=a"b'])
def test_unsafe_values_rejected(tmp_path, bad):
    with pytest.raises(Exception):  # noqa: B017 - any validation error is fine
        _cfg(tmp_path, bad)
