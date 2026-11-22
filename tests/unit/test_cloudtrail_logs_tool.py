"""The assistant's cloudtrail_logs tool (Tailpipe, DuckDB): guarded, bounded, offered only with Tailpipe."""

import json
import stat

import pytest

from spendlens.ai.sql_guard import UnsafeSQL
from spendlens.ai.tailpipe_tools import check
from spendlens.ai.tools import Toolbox
from spendlens.config import load_config
from spendlens.history import demo_db_path

OK = ("select event_name, user_identity.arn from aws_cloudtrail_log where tp_date >= current_date - interval 30 day "
      "and event_name = 'DeleteBucket'")


@pytest.mark.parametrize("sql,why", [
    ("select event_name from aws_cloudtrail_log where event_name = 'DeleteBucket'", "time range"),
    ("select * from read_csv('/etc/passwd')", "table functions"),
    ("select getenv('HOME') from aws_cloudtrail_log where tp_date > '2026-01-01'", "getenv"),
    ("select * from aws_s3_bucket where tp_date > '2026-01-01'", "only the aws_cloudtrail_log"),
    ("delete from aws_cloudtrail_log", "only SELECT"),
    ("select 1; select 2", "exactly one"),
])
def test_guard_refuses(sql, why):
    with pytest.raises(UnsafeSQL, match=why):
        check(sql, 500)


def test_guard_allows_bounded_reads_and_caps_rows():
    out = check(OK, 50)
    assert out.endswith("limit 51") and "aws_cloudtrail_log" in out
    assert check("with x as (select * from aws_cloudtrail_log where tp_date >= '2026-01-01') select count(*) from x", 5)
    assert check("select request_parameters ->> 'bucketName' from aws_cloudtrail_log "
                 "where tp_date between '2026-01-01' and '2026-02-01'", 5)


def fake_tailpipe(tmp_path, rows):
    (tmp_path / "out.json").write_text(json.dumps({"rows": rows}))
    exe = tmp_path / "tailpipe"
    exe.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/args"\ncat "{tmp_path}/out.json"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_tool_is_offered_only_with_tailpipe_and_runs(tmp_path):
    names = {s.name for s in Toolbox(load_config(environ={}, sets=["graph.tailpipe_path=/nonexistent/tailpipe"]),
                                     demo_db_path()).specs()}
    assert "cloudtrail_logs" not in names
    exe = fake_tailpipe(tmp_path, [{"event_name": "DeleteBucket", "arn": "arn:aws:iam::1:user/alice"}])
    tb = Toolbox(load_config(environ={}, sets=[f"graph.tailpipe_path={exe}"]), demo_db_path())
    assert "cloudtrail_logs" in {s.name for s in tb.specs()}
    r = tb.execute("1", "cloudtrail_logs", {"sql": OK})
    assert not r.is_error and "alice" in r.content and "Tailpipe" in r.content
    assert "limit 501" in (tmp_path / "args").read_text()
    bad = tb.execute("2", "cloudtrail_logs", {"sql": "select * from read_csv('/etc/passwd')"})
    assert bad.is_error and "table functions" in bad.content
    cols = fake_tailpipe(tmp_path, [{"column_name": "event_name", "data_type": "VARCHAR"}])
    tb2 = Toolbox(load_config(environ={}, sets=[f"graph.tailpipe_path={cols}"]), demo_db_path())
    d = json.loads(tb2.execute("3", "cloudtrail_logs", {"describe": True}).content)
    assert d["columns"] == [["event_name", "VARCHAR"]] and "information_schema" in (tmp_path / "args").read_text()
