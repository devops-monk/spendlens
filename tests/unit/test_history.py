import sqlite3

from typer.testing import CliRunner

from spendlens.config import PricingConfig
from spendlens.history import HistoryStore
from spendlens.models import Finding, Status

runner = CliRunner()
P = PricingConfig()



from typer.testing import CliRunner

from spendlens.history import diff_findings

runner = CliRunner()




def test_history_v1_db_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
      create table runs (id integer primary key autoincrement, feature text not null, started_at text not null,
        account_id text, caller_arn text, params text not null default '{}', meta text not null default '{}');
      create table findings (run_id integer not null, control text not null, title text, "check" text,
        resource_type text, resource text not null, account_id text, region text, status text not null,
        reason text, severity text, est_monthly_saving real, tags text, dimensions text);
      create table cost_rows (run_id integer not null, kind text not null, period text not null,
        dimension text not null, grp text not null, amount real not null, currency text not null default 'USD');
      insert into runs(feature, started_at) values ('tags', '2026-01-01T00:00:00+00:00');
      insert into findings(run_id, control, resource, status) values (1, 'c', 'r', 'ok');
      pragma user_version = 1;
    """)
    con.close()
    with HistoryStore(db) as s:
        assert s.findings(1)[0].service is None
        rid = s.save_run("rightsize", [Finding(control="c", resource="r", status=Status.OK, service="ec2")])
        assert s.findings(rid)[0].service == "ec2"
        assert s.conn.execute("pragma user_version").fetchone()[0] == 2



def F(control, resource, status, **kw):
    return Finding(control=control, resource=resource, status=Status(status), **kw)



def test_store_roundtrip_and_prune(tmp_path):
    with HistoryStore(tmp_path / "x.db") as s:
        r1 = s.save_run("tags", [F("c", "r1", "alarm", tags={"a": "b"}, est_monthly_saving=1.5)], account_id="1")
        s.save_run("tags", [F("c", "r1", "ok")])
        s.save_run("tags", [F("c", "r1", "ok")])
        got = s.findings(r1)[0]
        assert got.tags == {"a": "b"} and got.est_monthly_saving == 1.5 and got.status is Status.ALARM
        assert s.prune(2) == 1
        assert [r.id for r in s.runs("tags")] == [3, 2]
        assert s.findings(r1) == []  # cascade



def test_diff_classification():
    old = [F("c", "a", "alarm"), F("c", "b", "ok"), F("c", "gone", "ok"), F("c", "flip", "ok")]
    new = [F("c", "a", "ok"), F("c", "b", "ok"), F("c", "new", "ok"), F("c", "flip", "alarm")]
    changes = {r["resource"]: r["change"] for r in diff_findings(old, new)}
    assert changes == {"a": "resolved", "gone": "resource_gone", "new": "new_resource", "flip": "new_alarm"}

