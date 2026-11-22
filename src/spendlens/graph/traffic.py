"""Data transfer paths from VPC flow logs (collected with Tailpipe), priced, on top of the resource graph.

Each accepted flow is matched to the network interface that logged it and then to its resource (the instance, or the
interface itself). The other end decides the path and the price (finops.yaml `pricing:`):

  same_az        another collected interface in the same AZ                       free
  cross_az       another collected interface in a different AZ                    cross_az_gb on each side (out only,
                                                                                  so a flow seen at both ends counts once)
  endpoint       S3 / DynamoDB through a gateway endpoint in the VPC              free
  nat            a public address, from a subnet that routes through a NAT        nat_gateway_gb on bytes in and out,
                 gateway (S3 / DynamoDB included when the VPC has no endpoint)    + internet_egress_gb out (not S3/DynamoDB)
  internet       a public address, straight out (public subnet)                   internet_egress_gb out (S3/DynamoDB free)
  vpc_other      a private address spendlens did not collect (peering, RDS ...)   not priced

S3 and DynamoDB addresses come from AWS's public ip-ranges.json (bundled; scripts/build_ip_ranges.py). Interfaces of
NAT gateways and other AWS-managed interfaces are skipped, so traffic is counted at the resource that sent it.
Costs are monthly rates from the window (graph.flow_days). Proposals: a gateway endpoint for S3 or DynamoDB traffic
that goes through a NAT gateway today (gateway endpoints are free).
"""

from __future__ import annotations

import gzip
import ipaddress
import json
from collections import defaultdict
from functools import cache
from importlib import resources
from typing import Any

from ..config import PricingConfig
from .model import Graph, Node

DAYS_PER_MONTH = 30.4375
GB = 1024 ** 3
PATHS = {"same_az": "Same AZ", "cross_az": "Cross-AZ", "endpoint": "Gateway endpoint", "nat": "Through a NAT gateway",
         "internet": "Straight to the internet", "vpc_other": "Inside the VPC (not collected)"}
SERVICE_NAMES = {"S3": "S3", "DYNAMODB": "DynamoDB"}


EXTENDED = ("pkt_src_addr", "pkt_dst_addr", "pkt_src_aws_service", "pkt_dst_aws_service")


def flow_sql(days: int, limit: int = 20000, *, extended: bool = False) -> str:
    """extended: also the custom-format fields (v3+): the original packet addresses, and the AWS service AWS itself
    names. The default format has no such columns; collect() falls back to the basic query when they are missing."""
    days, limit = int(days), int(limit)
    if not 1 <= days <= 31:
        raise ValueError("graph.flow_days must be between 1 and 31")
    keys = ["interface_id", "src_addr", "dst_addr", *(EXTENDED if extended else ())]
    return (f"select {', '.join(keys)}, sum(bytes) as bytes from aws_vpc_flow_log "
            "where action = 'ACCEPT' and interface_id is not null "
            f"and tp_timestamp >= current_date - interval {days} day and tp_timestamp < current_date "
            f"group by {', '.join(keys)} order by bytes desc limit {limit}")


def read_flows(run: Any, days: int) -> tuple[list[dict[str, Any]], bool]:
    """(rows, extended): the custom-format fields when the logs have them, else the default ones."""
    from .costs import CostSourceError

    try:
        return run(flow_sql(days, extended=True)), True
    except CostSourceError:
        return run(flow_sql(days)), False


@cache
def _ranges() -> tuple[tuple[Any, str], ...]:
    raw = resources.files("spendlens.graph").joinpath("data", "aws_service_ranges.json.gz").read_bytes()
    doc = json.loads(gzip.decompress(raw))
    return tuple((ipaddress.ip_network(p), svc) for p, svc, _ in doc["prefixes"])


def aws_service(ip: str) -> str | None:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for net, svc in _ranges():
        if a in net:
            return SERVICE_NAMES.get(svc, svc)
    return None


def _private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


class Traffic:
    def __init__(self, g: Graph, pricing: PricingConfig, days: int):
        self.g, self.p, self.days = g, pricing, days
        self.scale = DAYS_PER_MONTH / days
        self.enis = {n.attrs.get("eni_id"): n for n in g.of_type("ec2_network_interface")}
        self.by_ip: dict[str, Node] = {}
        for n in self.enis.values():
            for ip in n.attrs.get("ips") or []:
                self.by_ip.setdefault(ip, n)
        self.subnets = {n.attrs.get("subnet_id"): n for n in g.of_type("vpc_subnet")}
        # vpc id -> [(service, endpoint node)] gateway endpoints
        self.endpoints: dict[str, list[tuple[str, Node]]] = defaultdict(list)
        for n in g.of_type("vpc_endpoint"):
            if str(n.attrs.get("endpoint_type") or "").lower() == "gateway":
                svc = str(n.attrs.get("service_name") or "").rsplit(".", 1)[-1].upper()
                for e in g.out(n.id):
                    if e.label == "in vpc":
                        self.endpoints[g.nodes[e.to_id].attrs.get("vpc_id")].append((SERVICE_NAMES.get(svc, svc), n))

    def owner(self, eni: Node) -> Node:
        users = [self.g.nodes[e.from_id] for e in self.g.into(eni.id) if e.label == "eni"]
        return users[0] if users else eni

    def nat_of(self, eni: Node) -> Node | None:
        sub = self.subnets.get(eni.attrs.get("subnet_id"))
        if sub is None:
            return None
        nats = [self.g.nodes[e.to_id] for e in self.g.out(sub.id) if e.label == "routes via"]
        return nats[0] if nats else None

    def vpc_of(self, eni: Node) -> str | None:
        sub = self.subnets.get(eni.attrs.get("subnet_id"))
        return sub.attrs.get("vpc_id") if sub else None

    def analyse(self, flows: list[dict[str, Any]]) -> dict[str, Any]:
        agg: dict[tuple[str, str, str, str], dict[str, float]] = defaultdict(lambda: {"in": 0.0, "out": 0.0})
        unmatched: set[str] = set()
        skipped_managed = 0
        for f in flows:
            eni = self.enis.get(str(f.get("interface_id") or ""))
            if eni is None:
                unmatched.add(str(f.get("interface_id")))
                continue
            if eni.attrs.get("requester_managed") or eni.attrs.get("interface_type") == "nat_gateway":
                skipped_managed += 1
                continue
            src, dst = str(f.get("src_addr") or ""), str(f.get("dst_addr") or "")
            own = set(eni.attrs.get("ips") or [])
            if src not in own and dst not in own:  # through a middlebox: the original packet addresses tell
                src, dst = str(f.get("pkt_src_addr") or src), str(f.get("pkt_dst_addr") or dst)
            if src in own:
                direction, peer, named = "out", dst, f.get("pkt_dst_aws_service")
            elif dst in own:
                direction, peer, named = "in", src, f.get("pkt_src_aws_service")
            else:
                continue
            try:
                nbytes = float(f.get("bytes") or 0)
            except (TypeError, ValueError):
                continue
            path, peer_id, via = self.classify(eni, peer, str(named or "") or None)
            agg[(self.owner(eni).id, path, peer_id, via)][direction] += nbytes
        rows = []
        for (rid, path, peer_id, via), b in agg.items():
            cost = self.price(path, peer_id, b["in"], b["out"])
            res = self.g.nodes[rid]
            peer = self.g.nodes[peer_id].label if peer_id in self.g.nodes else peer_id
            rows.append({"resource": rid, "resource_name": res.label, "resource_type": res.type, "path": path,
                         "path_label": PATHS[path], "peer": peer_id, "peer_name": peer, "via": via,
                         "via_name": self.g.nodes[via].label if via in self.g.nodes else "",
                         "gb_in_month": round(b["in"] / GB * self.scale, 2),
                         "gb_out_month": round(b["out"] / GB * self.scale, 2),
                         "monthly_cost": round(cost * self.scale, 2), "region": res.region or ""})
        rows.sort(key=lambda r: (-r["monthly_cost"], -(r["gb_in_month"] + r["gb_out_month"])))
        by_path: dict[str, dict[str, float]] = defaultdict(lambda: {"gb": 0.0, "cost": 0.0})
        for r in rows:  # between two collected resources each flow is seen at both ends: count what was sent
            internal = r["path"] in ("same_az", "cross_az")
            by_path[r["path"]]["gb"] += r["gb_out_month"] if internal else r["gb_in_month"] + r["gb_out_month"]
            by_path[r["path"]]["cost"] += r["monthly_cost"]
        return {"window_days": self.days, "rows": rows, "flows_read": len(flows),
                "by_path": {k: {"label": PATHS[k], "gb_month": round(v["gb"], 2), "monthly_cost": round(v["cost"], 2)}
                            for k, v in sorted(by_path.items(), key=lambda kv: -kv[1]["cost"])},
                "monthly_cost": round(sum(r["monthly_cost"] for r in rows), 2),
                "proposals": self.proposals(rows), "unmatched_interfaces": len(unmatched),
                "skipped_managed": skipped_managed}

    def endpoint_for(self, eni: Node, svc: str) -> tuple[bool, Node | None]:
        """(the interface's subnet uses a gateway endpoint for svc, an endpoint in its VPC it could use)."""
        sub = self.subnets.get(eni.attrs.get("subnet_id"))
        rt = sub.attrs.get("route_table") if sub else None
        cands = [ep for s, ep in self.endpoints.get(self.vpc_of(eni) or "", []) if s == svc]
        for ep in cands:
            tables = ep.attrs.get("route_tables")
            if not tables or not rt or rt in tables:  # unknown associations: assume the whole VPC (older graphs)
                return True, ep
        return False, cands[0] if cands else None

    def classify(self, eni: Node, peer: str, named: str | None = None) -> tuple[str, str, str]:
        """(path, peer id or label, NAT gateway id or ''). named: the AWS service the flow log itself names."""
        if _private(peer) and not named:
            other = self.by_ip.get(peer)
            if other is None:
                return "vpc_other", peer, ""
            target = self.owner(other)
            same = (other.attrs.get("az") or "") == (eni.attrs.get("az") or "")
            return ("same_az" if same else "cross_az"), target.id, ""
        svc = SERVICE_NAMES.get(named.upper(), named) if named else aws_service(peer)
        if svc and self.endpoint_for(eni, svc)[0]:
            return "endpoint", svc, ""
        nat = self.nat_of(eni)
        if nat is not None:
            return "nat", svc or "Internet", nat.id
        return "internet", svc or "Internet", ""

    def price(self, path: str, peer: str, gb_in: float, gb_out: float) -> float:
        i, o = gb_in / GB, gb_out / GB
        if path == "cross_az":
            return o * self.p.cross_az_gb * 2
        if path == "nat":
            return (i + o) * self.p.nat_gateway_gb + (0 if peer in SERVICE_NAMES.values() else o * self.p.internet_egress_gb)
        if path == "internet":
            return 0 if peer in SERVICE_NAMES.values() else o * self.p.internet_egress_gb
        return 0.0

    def proposals(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            if r["path"] != "nat" or r["peer"] not in SERVICE_NAMES.values():
                continue
            eni = next((n for n in self.enis.values() if self.owner(n).id == r["resource"]), None)
            eni_vpc = self.vpc_of(eni) if eni else None
            existing = self.endpoint_for(eni, r["peer"])[1] if eni else None
            sub = self.subnets.get(eni.attrs.get("subnet_id")) if eni else None
            rt = (sub.attrs.get("route_table") if sub else None) or ""
            key = (eni_vpc or "", r["peer"], existing.short_id if existing else "", rt if existing else "")
            p = by.setdefault(key, {"vpc": eni_vpc, "service": r["peer"], "gb_month": 0.0, "monthly_saving": 0.0,
                                    "nat": r["via_name"], "resources": [],
                                    "endpoint": existing.short_id if existing else None,
                                    "route_table": rt if existing else None})
            p["gb_month"] += r["gb_in_month"] + r["gb_out_month"]
            p["monthly_saving"] += r["monthly_cost"]
            p["resources"].append(r["resource_name"])
        out = []
        for p in by.values():
            p["gb_month"], p["monthly_saving"] = round(p["gb_month"], 2), round(p["monthly_saving"], 2)
            src = ", ".join(sorted(set(p["resources"]))[:5])
            if p["endpoint"]:
                p["title"] = f"Associate {p['service']} gateway endpoint {p['endpoint']} with route table {p['route_table']}"
                p["why"] = (f"{p['gb_month']:,.0f} GB/month of {p['service']} traffic from {src} goes through NAT "
                            f"gateway {p['nat']}: the VPC has {'an' if p['service'] == 'S3' else 'a'} {p['service']} gateway endpoint, but this subnet's "
                            "route table does not use it. Gateway endpoints are free.")
                p["check_first"] = ("Check endpoint and bucket or table policies that restrict by source IP or VPC.")
            else:
                p["title"] = f"Add {'an' if p['service'] == 'S3' else 'a'} {p['service']} gateway endpoint to {p['vpc']}"
                p["why"] = (f"{p['gb_month']:,.0f} GB/month of {p['service']} traffic goes through NAT gateway "
                            f"{p['nat']} (from {src}). Gateway endpoints for {p['service']} are free, so the NAT "
                            "processing charge goes away.")
                p["check_first"] = ("Associate it with the route tables of these subnets; check bucket or table "
                                    "policies that restrict by source IP or VPC.")
            out.append(p)
        return sorted(out, key=lambda p: -p["monthly_saving"])


def analyse(g: Graph, flows: list[dict[str, Any]], pricing: PricingConfig, days: int) -> dict[str, Any]:
    return Traffic(g, pricing, days).analyse(flows)


def drawing(g: Graph, result: dict[str, Any], top: int = 20) -> tuple[Graph, dict[str, int]]:
    """The costliest paths in three columns, sender -> NAT gateway -> destination; edges labelled GB and cost.
    Destinations are their own nodes, so a resource that both sends and receives is drawn on each side."""
    from dataclasses import replace

    from .model import Edge

    nodes: dict[str, Node] = {}
    cols: dict[str, int] = {}
    edges: list[Edge] = []

    def add(n: Node, col: int) -> str:
        nodes[n.id], cols[n.id] = n, col
        return n.id

    for r in [r for r in result["rows"] if r["path"] not in ("same_az", "vpc_other")][:top]:
        a = add(g.nodes[r["resource"]], 0) if r["resource"] in g.nodes else None
        if a is None:
            continue
        peer = r["peer"]
        if peer in g.nodes:
            b = add(replace(g.nodes[peer], id=f"to:{peer}"), 2)
        else:
            b = add(Node(f"to:{peer}", "aws_service" if peer in SERVICE_NAMES.values() else "internet", peer), 2)
        gb = r["gb_out_month"] if r["path"] == "cross_az" else r["gb_in_month"] + r["gb_out_month"]
        label = f"{gb:,.0f} GB · ${r['monthly_cost']:,.2f}"
        if r["via"] and r["via"] in g.nodes:
            v = add(g.nodes[r["via"]], 1)
            edges += [Edge(a, v, label), Edge(v, b, "")]
        else:
            edges.append(Edge(a, b, label))
    return Graph(list(nodes.values()), edges), cols
