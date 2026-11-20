"""Opt-in CloudTrail creators as an owner hint (graph.owner_from_cloudtrail)."""

import json

import pytest

from spendlens.ai.catalog import load
from spendlens.ai.sql_check import check
from spendlens.config import load_config
from spendlens.graph.cloudtrail import CREATE_EVENTS, apply, creators, event_sql
from spendlens.graph.collect import collect
from spendlens.graph.demo import FakeClient, rows
from spendlens.graph.owners import suggest_owners
from spendlens.steampipe import SteampipeError


def test_queries_are_bounded_and_pass_the_checker():
    cat = load()
    for ev in CREATE_EVENTS:
        sql = event_sql(ev, 90)
        assert check(sql, cat).ok, ev
        assert "start_time = now() - interval '90 days' and end_time = now()" in sql and f"event_name = '{ev}'" in sql
    with pytest.raises(ValueError):
        event_sql("RunInstances", 91)
    with pytest.raises(ValueError):
        event_sql("DeleteEverything", 7)


def test_creators_earliest_event_wins_and_permission_errors_stop():
    events = {
        "CreateVolume": [{"event_name": "CreateVolume", "event_time": "2026-10-02T10:00:00Z", "username": "late",
                          "resources": [{"ResourceName": "vol-0olddata"}]},
                         {"event_name": "CreateVolume", "event_time": "2026-10-01T10:00:00Z", "username": "alice",
                          "resources": json.dumps([{"ResourceName": "vol-0olddata"}])},
                         {"event_name": "RunInstances", "event_time": "2026-10-01T09:00:00Z", "username": "wrong",
                          "resources": [{"ResourceName": "vol-x"}]}],  # another event's row is ignored
    }

    def q(sql):
        ev = sql.rsplit("'", 2)[-2]
        return events.get(ev, [])

    found, skipped = creators(q, 30)
    assert found == {"vol-0olddata": {"by": "alice", "at": "2026-10-01T10:00:00Z", "event": "CreateVolume"}}
    assert not skipped
    calls = []

    def denied(sql):
        calls.append(sql)
        raise SteampipeError("AccessDeniedException: not authorized to perform cloudtrail:LookupEvents")

    found, skipped = creators(denied, 30)
    assert found == {} and len(calls) == 1 and "AccessDenied" in next(iter(skipped.values()))


def test_owner_rule_only_for_the_owner_key_and_never_over_an_attached_owner():
    cfg = load_config(environ={})
    g = collect(cfg, client=FakeClient(rows()))[0]
    apply(g, {"vol-0payapi1": {"by": "alice", "at": "2026-10-01T10:00:00Z", "event": "CreateVolume"},
              "report-export": {"by": "bob", "at": "2026-09-01T09:00:00Z", "event": "CreateFunction20150331"},
              "payments-api-1": {"by": "ci-deployer", "at": "2026-09-01T09:00:00Z", "event": "RunInstances"}})
    g.nodes[g.find("payments-api-1")[0].id].tags["Owner"] = "payments-team"
    s = suggest_owners(g, ["Team"], creator_key="Owner")
    by = {(x.name, x.key): x for x in s}
    assert by[("report-export", "Owner")].value == "bob" and by[("report-export", "Owner")].confidence == "medium"
    assert "CloudTrail" in by[("report-export", "Owner")].reason
    # the volume's instance has an Owner tag: the attached (high) rule wins over who created the volume
    assert (by[("vol-0payapi1", "Owner")].value, by[("vol-0payapi1", "Owner")].confidence) == ("payments-team", "high")
    assert ("payments-api-1", "Owner") not in by  # already tagged
    assert all(x.key != "Team" or x.value != "bob" for x in s)  # never used for other keys
