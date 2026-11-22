"""Resource age with cost, from the saved graph."""

from datetime import UTC, datetime

from spendlens.config import load_config
from spendlens.graph.age import age_report, bucket
from spendlens.graph.collect import collect
from spendlens.graph.demo import FakeClient, rows

NOW = datetime(2026, 11, 9, tzinfo=UTC)


def test_buckets_and_filters():
    assert [bucket(d) for d in (0, 45, 100, 200, 400, 900)] == [
        "under 30 days", "30-90 days", "3-6 months", "6-12 months", "1-2 years", "2+ years"]
    g = collect(load_config(environ={}), client=FakeClient(rows()))[0]
    rep = age_report(g, now=NOW, min_days=365, owner=lambda n: n.tags.get("Team", ""))
    assert rep["rows"] and all(r["age_days"] >= 365 for r in rep["rows"])
    assert rep["monthly_cost"] == round(sum(r["monthly_cost"] for r in rep["rows"]), 2)
    assert sum(b["count"] for b in rep["buckets"]) == len(rep["rows"])
    old = next(r for r in rep["rows"] if r["name"] == "experiment-gpu-2023")
    assert old["age"] == "2+ years" and old["created"] == "2023-10-02"
    snaps = age_report(g, now=NOW, types=["ebs_snapshot"])
    assert {r["kind"] for r in snaps["rows"]} == {"ebs_snapshot"}
    fn = next(r for r in age_report(g, now=NOW)["rows"] if r["kind"] == "lambda_function")
    assert fn["since"] == "since its last change"
    assert rep["undated"] > 0  # subnets, Elastic IPs, target groups have no date
