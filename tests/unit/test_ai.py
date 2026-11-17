import sqlite3
from types import SimpleNamespace as NS

import pytest

from spendlens.ai.prompts import system_prompt
from spendlens.ai.provider import AIError, AnthropicProvider, OpenAIProvider, _echo_content, build_provider
from spendlens.ai.sql_guard import UnsafeSQL, check_select
from spendlens.ai.tools import Toolbox, ToolSpec, open_readonly_sqlite
from spendlens.config import Config, load_config
from spendlens.history import HistoryStore
from spendlens.models import Finding, Status

# ------------------------------------------------------------------ SQL guard


@pytest.mark.parametrize("sql", [
    "select 1", "SELECT 1;", "with a as (select 1 x) select x from a",
    "select name from aws_s3_bucket where name like 'x%' union all select name from aws_s3_bucket",
    "select tags ->> 'Team' t, count(*) from aws_ec2_instance group by 1",
])
def test_guard_allows_reads(sql):
    assert check_select(sql)


@pytest.mark.parametrize("sql", [
    "insert into t values (1)", "update t set a = 1", "delete from t", "drop table t", "create table x as select 1",
    "select 1; drop table t", "select * into newt from t", "select * from t for update",
    "select pg_read_file('/etc/passwd')", "select set_config('default_transaction_read_only', 'off', false)",
    "copy t to '/tmp/x'", "set default_transaction_read_only = off", "begin",
    "with d as (delete from t returning *) select * from d", "select pg_sleep(100)", "", "   ;  ",
    "explain analyze delete from t", "select dblink_exec('x', 'y')",
])
def test_guard_blocks_writes_and_side_effects(sql):
    with pytest.raises(UnsafeSQL):
        check_select(sql)


@pytest.mark.parametrize("sql", ["attach database '/tmp/x' as y", "pragma writable_schema = 1",
                                 "select load_extension('x')"])
def test_guard_blocks_sqlite_specials(sql):
    with pytest.raises(UnsafeSQL):
        check_select(sql, "sqlite")


# ------------------------------------------------------------------ read-only history DB (defence in depth)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "h.db"
    with HistoryStore(p) as s:
        s.save_run("waste", [Finding(control="vpc_eip_unattached", resource="eip-1", status=Status.ALARM,
                                     est_monthly_saving=3.65, service="vpc")])
    return p


def test_readonly_connection_denies_writes_even_without_guard(db):
    con = open_readonly_sqlite(db)
    assert con.execute("select count(*) from findings").fetchone()[0] == 1
    for stmt in ("delete from findings", "insert into runs(feature, started_at) values ('x','y')",
                 "attach database ':memory:' as m", "create table t(x)", "pragma query_only = off",
                 "select load_extension('x')"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(stmt)
    assert con.execute("select count(*) from findings").fetchone()[0] == 1


# ------------------------------------------------------------------ toolbox


def test_toolbox_history_tools_and_audit(db):
    tb = Toolbox(load_config(environ={}), db)
    assert {s.name for s in tb.specs()} == {"history_overview", "history_query"}  # no live tools by default
    r = tb.execute("1", "history_query", {"sql": "select resource, est_monthly_saving from findings"})
    assert not r.is_error and '"eip-1"' in r.content and "3.65" in r.content
    ov = tb.execute("2", "history_overview", {})
    assert '"feature":"waste"' in ov.content and '"alarms":1' in ov.content
    bad = tb.execute("3", "history_query", {"sql": "delete from findings"})
    assert bad.is_error and "only SELECT" in bad.content
    assert tb.execute("4", "nope", {}).is_error
    assert "missing required" in tb.execute("5", "history_query", {}).content
    assert tb.execute("6", "aws_query", {"sql": "select 1"}).is_error  # not offered without --live
    assert [a.ok for a in tb.audit] == [True, True, False, False, False, False]
    assert tb.audit[0].rows == 1


def test_toolbox_truncates_and_caps_rows(db):
    cfg = load_config(sets=["llm.max_result_chars=1000", "llm.max_rows=2"], environ={})
    tb = Toolbox(cfg, db)
    r = tb.execute("1", "history_query", {"sql": "with recursive n(i) as (select 1 union all select i+1 from n "
                                                 "where i < 50) select i, printf('%080d', i) from n"})
    assert '"row_count":2' in r.content and '"truncated":true' in r.content
    big = Toolbox(load_config(sets=["llm.max_result_chars=1000"], environ={}), db)
    r = big.execute("1", "history_query", {"sql": "select printf('%05000d', 1)"})
    assert len(r.content) < 1100 and "truncated at 1000" in r.content


def test_live_tools_offered_only_with_live(db):
    names = {s.name for s in Toolbox(load_config(environ={}), db, live=True).specs()}
    assert {"aws_find_tables", "aws_describe_table", "aws_check_query", "aws_query", "aws_query_result"} <= names


def test_plugin_tool_errors_are_contained(db):
    boom = ToolSpec("boom", "d", {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                    lambda a: 1 / 0)
    r = Toolbox(load_config(environ={}), db, extra=[boom]).execute("1", "boom", {})
    assert r.is_error and "ZeroDivisionError" in r.content


# ------------------------------------------------------------------ prompt


def test_system_prompt_carries_policy_and_safety():
    p = system_prompt(load_config(sets=["tagging.mandatory=[Team]"], environ={}), live=False)
    assert "Mandatory tags: Team" in p and "NOT available" in p and "read-only" in p
    assert "never instructions" in p  # tool output is data, not instructions


# ------------------------------------------------------------------ providers (no network)


def test_echo_content_after_fallback():
    blocks = [NS(type="thinking"), NS(type="tool_use"), NS(type="text"), NS(type="fallback"), NS(type="thinking"),
              NS(type="tool_use")]
    kept = [b.type for b in _echo_content(blocks)]
    assert kept == ["text", "fallback", "thinking", "tool_use"]
    plain = [NS(type="thinking"), NS(type="tool_use")]
    assert _echo_content(plain) == plain


def _anthropic_resp(content, stop="end_turn", **kw):
    return NS(content=content, stop_reason=stop, model="claude-opus-5-5",
              usage=NS(input_tokens=5, output_tokens=3, cache_read_input_tokens=2), **kw)


def test_anthropic_refusal_not_echoed():
    p = AnthropicProvider(None, "claude-opus-5-5", Config(), server_fallbacks=True)
    t = p._parse(_anthropic_resp([], "refusal", stop_details=NS(category="cyber")))
    assert t.refused and "cyber" in t.text and p.messages == []
    t = p._parse(_anthropic_resp([NS(type="tool_use", id="t1", name="history_overview", input={})], "tool_use"))
    assert t.calls[0].name == "history_overview" and len(p.messages) == 1


def test_openai_bad_json_arguments():
    msg = NS(content=None, refusal=None, tool_calls=[NS(id="c1", function=NS(name="history_query", arguments="{bad"))])
    client = NS(chat=NS(completions=NS(create=lambda **kw: NS(choices=[NS(message=msg, finish_reason="tool_calls")],
                                                              model="m", usage=None))))
    p = OpenAIProvider(client, "m", Config(), family="openai")
    p.start("sys", [])
    t = p.send_user("hi")
    assert t.calls[0].arguments is None and t.calls[0].raw_arguments == "{bad"


@pytest.mark.parametrize(("sets", "msg"), [
    (["llm.provider=openai", "llm.model=x"], "OPENAI_API_KEY"),
    (["llm.provider=azure", "llm.model=x"], "base_url"),
    (["llm.provider=openai_compatible", "llm.model=x"], "base_url"),
    (["llm.provider=bedrock"], "region"),  # never guessed
])
def test_build_provider_config_errors(sets, msg):
    with pytest.raises(AIError, match=msg):
        build_provider(load_config(sets=sets, environ={}))
