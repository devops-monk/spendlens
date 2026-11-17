"""AI cache (freshness, scope, size) and the background query runner (wait, timeout, retry)."""

import sqlite3
import threading
import time

import pytest

from spendlens.ai import cache as cache_mod
from spendlens.ai import catalog as cat_mod
from spendlens.ai.cache import (
    AICache,
    digest,
    history_fingerprint,
    normalise_question,
    normalise_sql,
    scope,
    ttl_for,
)
from spendlens.ai.jobs import QueryJobs
from spendlens.config import load_config
from spendlens.steampipe import SteampipeError


def test_put_get_expiry_and_clear(tmp_path, monkeypatch):
    c = AICache(tmp_path / "c.db")
    c.put("query", "k", {"rows": [[1]]}, 60, label="select 1")
    payload, created = c.get("query", "k")
    assert payload == {"rows": [[1]]} and created <= time.time()
    now = time.time()
    monkeypatch.setattr(cache_mod.time, "time", lambda: now + 61)
    assert c.get("query", "k") is None  # expired
    monkeypatch.undo()
    c.put("answer", "a", {"answer": "x"}, 60)
    assert [s["kind"] for s in c.stats()] == ["answer", "query"] and c.entries("answer")[0]["expires_in_min"] > 0
    assert c.clear("answer") == 1 and c.get("answer", "a") is None


def test_disabled_and_broken_cache_never_fail(tmp_path):
    off = AICache(tmp_path / "c.db", enabled=False)
    off.put("query", "k", {}, 60)
    assert off.get("query", "k") is None and off.stats() == []
    (tmp_path / "file").write_text("x")
    broken = AICache(tmp_path / "file" / "c.db")  # parent is a file: cannot create
    broken.put("query", "k", {}, 60)
    assert broken.get("query", "k") is None


def test_size_cap_prunes_oldest(tmp_path):
    c = AICache(tmp_path / "c.db", max_mb=1)
    for i in range(12):
        c.put("query", str(i), {"blob": "x" * 200_000}, 600)
    total = sum(s["kib"] for s in c.stats())
    assert total <= 1024 + 200 and c.get("query", "11") is not None and c.get("query", "0") is None


def test_ttl_by_data_class():
    cfg, cat = load_config(environ={}), cat_mod.load()
    assert ttl_for(["aws_ebs_volume"], cat, cfg) == 900
    assert ttl_for(["aws_cost_by_service_monthly"], cat, cfg) == 21600
    assert ttl_for(["aws_ec2_instance_metric_cpu_utilization_daily"], cat, cfg) == 300
    assert ttl_for(["aws_cost_by_service_monthly", "aws_ebs_volume"], cat, cfg) == 900  # strictest wins


def test_keys_normalise_and_scope(monkeypatch):
    assert normalise_sql("SELECT  a\nFROM aws_vpc;") == normalise_sql("select a from aws_vpc")
    assert normalise_question("  How many EC2?? ") == normalise_question("how many ec2")
    cfg = load_config(environ={})
    s1 = scope(cfg)
    other = load_config(sets=["aws.regions=[us-east-1]"], environ={})
    assert scope(other) != s1
    monkeypatch.setenv("AWS_PROFILE", "prod")
    assert scope(cfg) != s1  # another identity never sees these results
    assert digest("a", 1) == digest("a", 1) != digest("a", 2)


def test_history_fingerprint_changes_with_new_runs(tmp_path):
    db = tmp_path / "h.db"
    con = sqlite3.connect(db)
    con.execute("create table runs (id integer primary key, feature text)")
    con.execute("insert into runs (feature) values ('waste')")
    con.commit()
    before = history_fingerprint(db)
    con.execute("insert into runs (feature) values ('waste')")
    con.commit()
    assert history_fingerprint(db) != before
    assert history_fingerprint(tmp_path / "missing.db") == "none"


class FakeClient:
    def __init__(self, behaviour):
        self.behaviour, self._conn = behaviour, None

    def query(self, sql, params=None, limit=None):
        return self.behaviour(sql)

    def close(self):
        pass


def jobs(behaviour, **kw):
    return QueryJobs(lambda: FakeClient(behaviour), max_rows=10, timeout_s=kw.pop("timeout_s", 30),
                     retry_pause_s=0.01, **kw)


def test_fast_query_returns_rows():
    q = jobs(lambda sql: [{"a": 1}])
    j = q.submit("select 1")
    assert q.wait(j.id, 2) == {"status": "done", "rows": [{"a": 1}], "elapsed_s": pytest.approx(0, abs=1)}
    q.close()


def test_slow_query_reports_running_then_rows():
    gate = threading.Event()
    q = jobs(lambda sql: (gate.wait(5), [{"n": 7}])[1])
    j = q.submit("select slow")
    first = q.wait(j.id, 0.05)
    assert first["status"] == "running" and first["job_id"] == j.id
    gate.set()
    assert q.wait(j.id, 2)["rows"] == [{"n": 7}]
    q.close()


def test_timeout_cancels():
    gate = threading.Event()
    q = jobs(lambda sql: (gate.wait(5), [])[1], timeout_s=0.2)
    j = q.submit("select forever")
    time.sleep(0.25)
    out = q.wait(j.id, 0.05)
    assert out["status"] == "error" and "query_timeout_s" in out["error"] and j.cancelled
    gate.set()
    q.close()


def test_throttling_retried_once_then_errors_reported():
    calls = []

    def flaky(sql):
        calls.append(sql)
        if len(calls) == 1:
            raise SteampipeError("ThrottlingException: Rate exceeded")
        return [{"ok": True}]

    q = jobs(flaky)
    assert q.wait(q.submit("select 1").id, 2)["rows"] == [{"ok": True}] and len(calls) == 2

    def denied(sql):
        raise SteampipeError("AccessDenied: not authorized to perform: ec2:DescribeVolumes")

    q2 = jobs(denied)
    out = q2.wait(q2.submit("select 1").id, 2)
    assert out["status"] == "error" and "ec2:DescribeVolumes" in out["error"]
    assert q2.wait("q99", 1)["status"] == "error"
    q.close()
    q2.close()
