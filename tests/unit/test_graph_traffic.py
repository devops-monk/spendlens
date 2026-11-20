"""Data transfer paths from VPC flow logs, priced on top of the resource graph."""

import pytest

from spendlens.config import load_config
from spendlens.graph.collect import collect
from spendlens.graph.demo import DDB_IP, IPS, S3_IP, FakeClient, flows, rows
from spendlens.graph.traffic import analyse, aws_service, drawing, flow_sql

GB = 1024 ** 3


@pytest.fixture
def g():
    return collect(load_config(environ={}), client=FakeClient(rows()))[0]


def result(g, fl=None, days=7):
    return analyse(g, fl if fl is not None else flows(), load_config(environ={}).pricing, days)


def row(res, name, path):
    return next(r for r in res["rows"] if r["resource_name"] == name and r["path"] == path)


def test_sql_and_service_ranges():
    sql = flow_sql(7)
    assert "from aws_vpc_flow_log" in sql and "action = 'ACCEPT'" in sql and "interval 7 day" in sql
    with pytest.raises(ValueError):
        flow_sql(0)
    assert aws_service(S3_IP) == "S3" and aws_service(DDB_IP) == "DynamoDB"
    assert aws_service("93.184.216.34") is None and aws_service("not an ip") is None


def test_paths_and_prices(g):
    res = result(g)
    scale = 30.4375 / 7
    ddb = row(res, "payments-api-2", "nat")
    assert ddb["peer_name"] == "DynamoDB" and ddb["via_name"] == "prod-nat"
    # NAT processing on both directions, no internet egress for DynamoDB
    assert ddb["monthly_cost"] == round((100 + 300) * 0.045 * scale, 2)
    web = row(res, "search-indexer", "nat")
    assert web["monthly_cost"] == round(((50 + 20) * 0.045 + 50 * 0.09) * scale, 2)
    cross = row(res, "payments-api-1", "cross_az")
    assert cross["peer_name"] == "search-indexer" and cross["monthly_cost"] == round(200 * 0.02 * scale, 2)
    assert row(res, "payments-api-2", "endpoint")["monthly_cost"] == 0  # the VPC has an S3 gateway endpoint
    assert row(res, "batch-worker", "same_az")["monthly_cost"] == 0
    # cross-AZ GB counted once (sent), not at both ends
    assert res["by_path"]["cross_az"]["gb_month"] == pytest.approx((200 + 60) * scale, abs=0.02)
    assert res["unmatched_interfaces"] == 1 and res["skipped_managed"] == 1  # NAT's own interface: counted at sender
    assert res["monthly_cost"] == round(sum(r["monthly_cost"] for r in res["rows"]), 2)


def test_endpoint_proposal_only_where_traffic_goes_through_nat(g):
    props = result(g)["proposals"]
    assert [(p["service"], p["vpc"]) for p in props] == [("DynamoDB", "vpc-0prod")]
    assert props[0]["monthly_saving"] == row(result(g), "payments-api-2", "nat")["monthly_cost"]
    assert "free" in props[0]["why"] and "route tables" in props[0]["check_first"]


def test_public_subnet_goes_straight_out_and_unknown_private_peers_are_not_priced(g):
    eni = g.find("eni-0searchidx")[0]
    eni.attrs["subnet_id"] = "subnet-0pub-a"  # pretend it sits in the public subnet (no NAT route)
    res = result(g, [{"interface_id": "eni-0searchidx", "src_addr": IPS["i-0searchidx"], "dst_addr": "93.184.216.34",
                      "bytes": 10 * GB},
                     {"interface_id": "eni-0searchidx", "src_addr": IPS["i-0searchidx"], "dst_addr": "10.99.0.1",
                      "bytes": 10 * GB}])
    assert row(res, "search-indexer", "internet")["monthly_cost"] == round(10 * 0.09 * 30.4375 / 7, 2)
    assert row(res, "search-indexer", "vpc_other")["monthly_cost"] == 0


def test_drawing_columns(g):
    pic, cols = drawing(g, result(g))
    names = {pic.nodes[i].label: c for i, c in cols.items()}
    assert names["prod-nat"] == 1 and names["DynamoDB"] == 2 and names["payments-api-2"] == 0
    assert any(i.startswith("to:") for i in pic.nodes)  # destinations drawn on their own side
