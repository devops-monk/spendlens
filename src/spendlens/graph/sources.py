"""One read-only query per resource type, and how each row becomes nodes and edges.

Edges point at other resources by their AWS id (vol-..., i-...) or ARN; collect.py resolves them once every source
has run. Costs are monthly estimates from the finops.yaml price book (`pricing:`), the same numbers the savings
estimates use; types without a price in the book get no cost rather than a guess.

Relationships follow turbot/steampipe-mod-aws-insights (Apache-2.0), e.g. its ec2_instance_to_ebs_volume,
ebs_volume_to_ebs_snapshot, ec2_target_group_to_ec2_instance and ecs_service_to_ecs_task_definition edges.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..config import PricingConfig
from .model import Edge, Node

Ref = tuple[str, str, str, str]  # (from ref, to ref, label, kind)
Built = tuple[list[Node], list[Ref]]


@dataclass(frozen=True)
class Source:
    name: str
    table: str
    sql: str
    build: Callable[[dict[str, Any], PricingConfig], Built]
    what: str  # shown when the source is skipped


def _j(v: Any) -> Any:
    """JSON columns arrive parsed from psycopg; tolerate strings (fixtures, other drivers)."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _list(v: Any) -> list[Any]:
    v = _j(v)
    return v if isinstance(v, list) else []


def _ts(v: Any) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat(timespec="seconds")
    return str(v)


def _tags(v: Any) -> dict[str, Any]:
    v = _j(v)
    return v if isinstance(v, dict) else {}


def _name(tags: dict[str, Any], fallback: str = "") -> str:
    return str(tags.get("Name") or fallback)


def _arn(region: str | None, account: str | None, kind: str, rid: str, service: str = "ec2") -> str:
    return f"arn:aws:{service}:{region or ''}:{account or ''}:{kind}/{rid}"


def _common(r: dict[str, Any]) -> dict[str, Any]:
    return {"account_id": r.get("account_id"), "region": r.get("region")}


def _month(p: PricingConfig, hourly: float | None) -> float | None:
    return round(hourly * p.hours_per_month, 2) if hourly else None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_GMT = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) GMT\)")


# ------------------------------------------------------------------ compute


def _instance(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    state = r.get("instance_state")
    itype = r.get("instance_type")
    hourly = p.ec2_hourly.get(str(itype or ""))
    cost = 0.0 if state in ("stopped", "stopping", "terminated") else _month(p, hourly)
    stopped_since = None
    if state in ("stopped", "stopping"):
        stopped_since = _ts(r.get("state_transition_time"))
        if not stopped_since and (m := _GMT.search(str(r.get("state_transition_reason") or ""))):
            stopped_since = m.group(1).replace(" ", "T") + "+00:00"
    n = Node(r["arn"], "ec2_instance", _name(tags, r["instance_id"]), state=state, created=_ts(r.get("launch_time")),
             monthly_cost=cost, cost_basis="estimate" if cost is not None else "none", tags=tags,
             attrs={"instance_id": r["instance_id"], "instance_type": itype, "stopped_since": stopped_since,
                    "image_id": r.get("image_id"), "subnet_id": r.get("subnet_id"), "vpc_id": r.get("vpc_id"),
                    "asg": tags.get("aws:autoscaling:groupName")}, **_common(r))
    refs: list[Ref] = []
    for bd in _list(r.get("block_device_mappings")):
        if vol := ((bd or {}).get("Ebs") or {}).get("VolumeId"):
            refs.append((n.id, vol, "mounts", "uses"))
    for ni in _list(r.get("network_interfaces")):
        if eni := (ni or {}).get("NetworkInterfaceId"):
            refs.append((n.id, eni, "eni", "uses"))
    if r.get("subnet_id"):
        refs.append((n.id, r["subnet_id"], "in subnet", "uses"))
    if r.get("image_id"):
        refs.append((n.id, r["image_id"], "launched from", "ref"))
    return [n], refs


def _volume(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    vtype, size, iops = r.get("volume_type"), _num(r.get("size")), _num(r.get("iops"))
    price = p.ebs_gb_month.get(str(vtype or ""))
    cost = None
    if size is not None and price is not None:
        cost = size * price + (iops * p.ebs_piops_month if vtype in ("io1", "io2") and iops else 0)
    attached = [a.get("InstanceId") for a in _list(r.get("attachments")) if (a or {}).get("InstanceId")]
    n = Node(r["arn"], "ebs_volume", _name(tags, r["volume_id"]), state=r.get("state"), created=_ts(r.get("create_time")),
             monthly_cost=round(cost, 2) if cost is not None else None, cost_basis="estimate" if cost is not None else "none",
             tags=tags, attrs={"volume_id": r["volume_id"], "volume_type": vtype, "size_gb": size, "iops": iops,
                               "attached_to": attached, "from_snapshot": r.get("snapshot_id") or None}, **_common(r))
    return [n], [(i, n.id, "mounts", "uses") for i in attached]


def _snapshot(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    size = _num(r.get("volume_size"))
    cost = round(size * p.ebs_snapshot_gb_month, 2) if size is not None else None
    vol = r.get("volume_id") or None
    if vol == "vol-ffffffff":  # AWS's placeholder for snapshots copied from elsewhere
        vol = None
    n = Node(r["arn"], "ebs_snapshot", _name(tags, r["snapshot_id"]), state=r.get("state"),
             created=_ts(r.get("start_time")), monthly_cost=cost, cost_basis="estimate" if cost is not None else "none",
             tags=tags, attrs={"snapshot_id": r["snapshot_id"], "size_gb": size, "volume_id": vol,
                               "description": r.get("description") or "", "cost_note": "upper bound (incremental)"},
             **_common(r))
    return [n], ([(vol, n.id, "snapshot", "ref")] if vol else [])


def _ami(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    snaps = [((b or {}).get("Ebs") or {}).get("SnapshotId") for b in _list(r.get("block_device_mappings"))]
    snaps = [s for s in snaps if s]
    n = Node(_arn(r.get("region"), r.get("account_id"), "image", r["image_id"]), "ec2_ami",
             r.get("name") or _name(tags, r["image_id"]), state=r.get("state"), created=_ts(r.get("creation_date")),
             monthly_cost=0.0, cost_basis="estimate", tags=tags,
             attrs={"image_id": r["image_id"], "snapshots": snaps, "last_launched": _ts(r.get("last_launched_time")),
                    "cost_note": "billed through its snapshots"}, **_common(r))
    return [n], [(n.id, s, "backed by", "uses") for s in snaps]


def _eni(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    eid = r["network_interface_id"]
    n = Node(_arn(r.get("region"), r.get("account_id"), "network-interface", eid), "ec2_network_interface",
             _name(tags, eid), state=r.get("status"), created=_ts(r.get("attachment_time")), tags=tags,
             attrs={"eni_id": eid, "interface_type": r.get("interface_type"), "description": r.get("description") or "",
                    "attached_to": r.get("attached_instance_id"), "requester_managed": r.get("requester_managed"),
                    "az": r.get("availability_zone"), "subnet_id": r.get("subnet_id"),
                    "ips": sorted({str(x) for x in [r.get("private_ip_address"), r.get("association_public_ip"),
                                                    *[(p or {}).get("PrivateIpAddress") for p in
                                                      _list(r.get("private_ip_addresses"))]] if x})},
             **_common(r))
    refs: list[Ref] = []
    if r.get("association_allocation_id"):
        refs.append((n.id, r["association_allocation_id"], "public ip", "uses"))
    if r.get("subnet_id"):
        refs.append((n.id, r["subnet_id"], "in subnet", "uses"))
    return [n], refs


# ------------------------------------------------------------------ network


def _eip(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    n = Node(r["arn"], "vpc_eip", _name(tags, str(r.get("public_ip") or r["allocation_id"])),
             state="associated" if r.get("association_id") else "unassociated",
             monthly_cost=_month(p, p.eip_hour), cost_basis="estimate", tags=tags,
             attrs={"allocation_id": r["allocation_id"], "public_ip": str(r.get("public_ip") or ""),
                    "instance_id": r.get("instance_id"), "eni_id": r.get("network_interface_id")}, **_common(r))
    refs: list[Ref] = []
    if r.get("network_interface_id"):
        refs.append((r["network_interface_id"], n.id, "public ip", "uses"))
    elif r.get("instance_id"):
        refs.append((r["instance_id"], n.id, "public ip", "uses"))
    return [n], refs


def _nat(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    live = r.get("state") in (None, "available", "pending")
    n = Node(r["arn"], "vpc_nat_gateway", _name(tags, r["nat_gateway_id"]), state=r.get("state"),
             created=_ts(r.get("create_time")), monthly_cost=_month(p, p.nat_gateway_hour) if live else 0.0,
             cost_basis="estimate", tags=tags,
             attrs={"nat_gateway_id": r["nat_gateway_id"], "cost_note": "hourly charge only; data processed is extra"},
             **_common(r))
    refs: list[Ref] = []
    if r.get("subnet_id"):
        refs.append((n.id, r["subnet_id"], "in subnet", "uses"))
    for a in _list(r.get("nat_gateway_addresses")):
        if (a or {}).get("AllocationId"):
            refs.append((n.id, a["AllocationId"], "public ip", "uses"))
        if (a or {}).get("NetworkInterfaceId"):
            refs.append((n.id, a["NetworkInterfaceId"], "eni", "uses"))
    return [n], refs


def _vpc(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    return [Node(r["arn"], "vpc", _name(tags, r["vpc_id"]), state=r.get("state"), tags=tags,
                 attrs={"vpc_id": r["vpc_id"], "cidr": str(r.get("cidr_block") or ""), "default": r.get("is_default")},
                 **_common(r))], []


def _subnet(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    n = Node(r["subnet_arn"], "vpc_subnet", _name(tags, r["subnet_id"]), state=r.get("state"), tags=tags,
             attrs={"subnet_id": r["subnet_id"], "vpc_id": r.get("vpc_id"), "az": r.get("availability_zone"),
                    "cidr": str(r.get("cidr_block") or "")}, **_common(r))
    return [n], ([(n.id, r["vpc_id"], "in vpc", "uses")] if r.get("vpc_id") else [])


def _endpoint(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    eid = r["vpc_endpoint_id"]
    svc = str(r.get("service_name") or "").rsplit(".", 1)[-1]
    n = Node(_arn(r.get("region"), r.get("account_id"), "vpc-endpoint", eid), "vpc_endpoint",
             _name(tags, f"{svc} endpoint" if svc else eid), state=r.get("state"),
             created=_ts(r.get("creation_timestamp")), tags=tags,
             attrs={"vpc_endpoint_id": eid, "service_name": r.get("service_name"),
                    "endpoint_type": r.get("vpc_endpoint_type")}, **_common(r))
    return [n], ([(n.id, r["vpc_id"], "in vpc", "uses")] if r.get("vpc_id") else [])


def _route_table(r: dict[str, Any], p: PricingConfig) -> Built:
    """No node: a route table only says which subnets send traffic through which NAT gateway. The main table applies
    to the subnets of its VPC that have no table of their own (resolved in resolve())."""
    nats = sorted({str(x["NatGatewayId"]) for x in _list(r.get("routes")) if (x or {}).get("NatGatewayId")})
    refs: list[Ref] = []
    for a in _list(r.get("associations")):
        a = a or {}
        if a.get("Main"):
            refs += [(f"main:{r.get('vpc_id')}", n, "routes via", "uses") for n in nats]
        elif a.get("SubnetId"):
            refs.append((a["SubnetId"], "", "_explicit", "_explicit"))  # this subnet does not use the main table
            refs += [(a["SubnetId"], n, "routes via", "uses") for n in nats]
    return [], refs


def _lb(kind: str) -> Callable[[dict[str, Any], PricingConfig], Built]:
    def build(r: dict[str, Any], p: PricingConfig) -> Built:
        tags = _tags(r.get("tags"))
        n = Node(r["arn"], "load_balancer", r.get("name") or "", state=r.get("state_code") or "active",
                 created=_ts(r.get("created_time")), monthly_cost=_month(p, p.load_balancer_hour.get(kind)),
                 cost_basis="estimate", tags=tags, attrs={"lb_type": kind, "scheme": r.get("scheme")}, **_common(r))
        refs: list[Ref] = []
        for az in _list(r.get("availability_zones")):
            if (az or {}).get("SubnetId"):
                refs.append((n.id, az["SubnetId"], "in subnet", "uses"))
        for s in _list(r.get("subnets")):  # classic
            if isinstance(s, str):
                refs.append((n.id, s, "in subnet", "uses"))
        instances = [i.get("InstanceId") for i in _list(r.get("instances")) if (i or {}).get("InstanceId")]
        if kind == "classic":
            n.attrs["targets"] = len(instances)
        refs += [(n.id, i, "targets", "uses") for i in instances]
        return [n], refs
    return build


def _target_group(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    targets = [((t or {}).get("Target") or {}).get("Id") for t in _list(r.get("target_health_descriptions"))]
    targets = [t for t in targets if t]
    healthy = sum(1 for t in _list(r.get("target_health_descriptions"))
                  if ((t or {}).get("TargetHealth") or {}).get("State") == "healthy")
    n = Node(r["target_group_arn"], "target_group", r.get("target_group_name") or "", tags=tags,
             attrs={"target_type": r.get("target_type"), "targets": len(targets), "healthy": healthy},
             **_common(r))
    refs: list[Ref] = [(lb, n.id, "forwards to", "uses") for lb in _list(r.get("load_balancer_arns"))]
    if r.get("target_type") in (None, "instance"):
        refs += [(n.id, t, "targets", "uses") for t in targets]
    return [n], refs


def _asg(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    n = Node(r["autoscaling_group_arn"], "autoscaling_group", r.get("name") or "", state=r.get("status") or "active",
             created=_ts(r.get("created_time")), tags=tags,
             attrs={"name": r.get("name"), "desired": r.get("desired_capacity"),
                    "launch_template": r.get("launch_template_id")}, **_common(r))
    refs: list[Ref] = [(n.id, (i or {}).get("InstanceId"), "manages", "contains")
                       for i in _list(r.get("instances")) if (i or {}).get("InstanceId")]
    refs += [(n.id, tg, "registers in", "uses") for tg in _list(r.get("target_group_arns"))]
    return [n], refs


# ------------------------------------------------------------------ databases, containers, functions


def _rds_instance(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    hourly = p.rds_hourly.get(str(r.get("class") or ""))
    running = r.get("status") not in ("stopped", "stopping")
    cost = _month(p, hourly * (2 if r.get("multi_az") else 1)) if hourly else None
    if not running:
        cost = 0.0
    n = Node(r["arn"], "rds_db_instance", r["db_instance_identifier"], state=r.get("status"),
             created=_ts(r.get("create_time")), monthly_cost=cost, cost_basis="estimate" if cost is not None else "none",
             tags=tags, attrs={"db_class": r.get("class"), "engine": r.get("engine"), "multi_az": r.get("multi_az"),
                               "storage_gb": r.get("allocated_storage"), "cluster": r.get("db_cluster_identifier"),
                               "cost_note": "instance hours only; storage is extra"}, **_common(r))
    refs: list[Ref] = []
    if r.get("db_cluster_identifier"):
        refs.append((f"rds-cluster:{r['db_cluster_identifier']}", n.id, "member", "contains"))
    for s in _list(r.get("subnets")):
        if (s or {}).get("SubnetIdentifier"):
            refs.append((n.id, s["SubnetIdentifier"], "in subnet", "uses"))
    return [n], refs


def _rds_cluster(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    return [Node(r["arn"], "rds_db_cluster", r["db_cluster_identifier"], state=r.get("status"),
                 created=_ts(r.get("create_time")), tags=tags,
                 attrs={"engine": r.get("engine"), "engine_mode": r.get("engine_mode")}, **_common(r))], []


def _rds_snapshot(cluster: bool) -> Callable[[dict[str, Any], PricingConfig], Built]:
    def build(r: dict[str, Any], p: PricingConfig) -> Built:
        tags = _tags(r.get("tags"))
        kind = r.get("type") or r.get("snapshot_type") or ""
        size = _num(r.get("allocated_storage"))
        manual = kind == "manual"
        cost = round(size * p.rds_snapshot_gb_month, 2) if manual and size is not None else 0.0
        src = r.get("db_cluster_identifier") if cluster else r.get("db_instance_identifier")
        ident = r.get("db_cluster_snapshot_identifier") if cluster else r.get("db_snapshot_identifier")
        n = Node(r["arn"], "rds_db_snapshot", ident or "", state=r.get("status"), created=_ts(r.get("create_time")),
                 monthly_cost=cost, cost_basis="estimate", tags=tags,
                 attrs={"snapshot_type": kind, "size_gb": size, "source": src, "cluster": cluster,
                        "cost_note": "manual snapshots are billed; automated ones are included up to the database size"},
                 **_common(r))
        ref = f"rds-cluster:{src}" if cluster else f"rds:{src}"
        return [n], ([(ref, n.id, "snapshot", "ref")] if src else [])
    return build


def _ecs_cluster(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    return [Node(r["cluster_arn"], "ecs_cluster", r.get("cluster_name") or "", state=r.get("status"), tags=tags,
                 attrs={"services": r.get("active_services_count"), "tasks": r.get("running_tasks_count")},
                 **_common(r))], []


def _ecs_service(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    n = Node(r["arn"], "ecs_service", r.get("service_name") or "", state=r.get("status"),
             created=_ts(r.get("created_at")), tags=tags,
             attrs={"desired": r.get("desired_count"), "running": r.get("running_count"),
                    "launch_type": r.get("launch_type")}, **_common(r))
    nodes = [n]
    refs: list[Ref] = []
    if r.get("cluster_arn"):
        refs.append((r["cluster_arn"], n.id, "runs", "contains"))
    if td := r.get("task_definition"):
        family = td.rsplit("/", 1)[-1]
        nodes.append(Node(td, "ecs_task_definition", family, **_common(r)))
        refs.append((n.id, td, "uses", "uses"))
    for lb in _list(r.get("load_balancers")):
        if (lb or {}).get("TargetGroupArn"):
            refs.append((n.id, lb["TargetGroupArn"], "registers in", "uses"))
    net = _j(r.get("network_configuration")) or {}
    for s in ((net.get("AwsvpcConfiguration") or {}).get("Subnets") or []) if isinstance(net, dict) else []:
        refs.append((n.id, s, "in subnet", "uses"))
    return nodes, refs


def _lambda(r: dict[str, Any], p: PricingConfig) -> Built:
    tags = _tags(r.get("tags"))
    n = Node(r["arn"], "lambda_function", r.get("name") or "", state=r.get("state"),
             created=_ts(r.get("last_modified")), tags=tags,
             attrs={"runtime": r.get("runtime"), "memory_mb": r.get("memory_size")}, **_common(r))
    return [n], [(n.id, s, "in subnet", "uses") for s in _list(r.get("vpc_subnet_ids"))]


def _sql(table: str, cols: str) -> str:
    return f"select account_id, region, {cols} from {table}"


SOURCES: tuple[Source, ...] = (
    Source("ec2_instance", "aws_ec2_instance", _sql(
        "aws_ec2_instance", "arn, instance_id, instance_type, instance_state, launch_time, state_transition_time, "
        "state_transition_reason, image_id, subnet_id, vpc_id, block_device_mappings, network_interfaces, tags"),
        _instance, "EC2 instances"),
    Source("ebs_volume", "aws_ebs_volume", _sql(
        "aws_ebs_volume", "arn, volume_id, volume_type, size, iops, state, create_time, snapshot_id, attachments, tags"),
        _volume, "EBS volumes"),
    Source("ebs_snapshot", "aws_ebs_snapshot", _sql(
        "aws_ebs_snapshot", "arn, snapshot_id, volume_id, volume_size, state, start_time, description, tags"),
        _snapshot, "EBS snapshots"),
    Source("ec2_ami", "aws_ec2_ami", _sql(
        "aws_ec2_ami", "image_id, name, state, creation_date, last_launched_time, block_device_mappings, tags"),
        _ami, "AMIs"),
    Source("ec2_network_interface", "aws_ec2_network_interface", _sql(
        "aws_ec2_network_interface", "network_interface_id, status, interface_type, description, attached_instance_id, "
        "attachment_time, association_allocation_id, requester_managed, subnet_id, availability_zone, "
        "private_ip_address, association_public_ip, private_ip_addresses, tags"), _eni, "network interfaces"),
    Source("vpc_eip", "aws_vpc_eip", _sql(
        "aws_vpc_eip", "arn, allocation_id, public_ip, association_id, instance_id, network_interface_id, tags"),
        _eip, "Elastic IPs"),
    Source("vpc_nat_gateway", "aws_vpc_nat_gateway", _sql(
        "aws_vpc_nat_gateway", "arn, nat_gateway_id, state, create_time, subnet_id, nat_gateway_addresses, tags"),
        _nat, "NAT gateways"),
    Source("vpc", "aws_vpc", _sql("aws_vpc", "arn, vpc_id, cidr_block, state, is_default, tags"), _vpc, "VPCs"),
    Source("vpc_subnet", "aws_vpc_subnet", _sql(
        "aws_vpc_subnet", "subnet_arn, subnet_id, vpc_id, availability_zone, cidr_block, state, tags"),
        _subnet, "subnets"),
    Source("vpc_endpoint", "aws_vpc_endpoint", _sql(
        "aws_vpc_endpoint", "vpc_endpoint_id, vpc_id, service_name, vpc_endpoint_type, state, creation_timestamp, tags"),
        _endpoint, "VPC endpoints"),
    Source("vpc_route_table", "aws_vpc_route_table", _sql(
        "aws_vpc_route_table", "route_table_id, vpc_id, associations, routes"), _route_table, "route tables"),
    Source("application_load_balancer", "aws_ec2_application_load_balancer", _sql(
        "aws_ec2_application_load_balancer", "arn, name, scheme, state_code, created_time, availability_zones, tags"),
        _lb("application"), "application load balancers"),
    Source("network_load_balancer", "aws_ec2_network_load_balancer", _sql(
        "aws_ec2_network_load_balancer", "arn, name, scheme, state_code, created_time, availability_zones, tags"),
        _lb("network"), "network load balancers"),
    Source("gateway_load_balancer", "aws_ec2_gateway_load_balancer", _sql(
        "aws_ec2_gateway_load_balancer", "arn, name, scheme, state_code, created_time, availability_zones, tags"),
        _lb("gateway"), "gateway load balancers"),
    Source("classic_load_balancer", "aws_ec2_classic_load_balancer", _sql(
        "aws_ec2_classic_load_balancer", "arn, name, scheme, created_time, subnets, instances, tags"),
        _lb("classic"), "classic load balancers"),
    Source("target_group", "aws_ec2_target_group", _sql(
        "aws_ec2_target_group", "target_group_arn, target_group_name, target_type, load_balancer_arns, "
        "target_health_descriptions, tags"), _target_group, "target groups"),
    Source("autoscaling_group", "aws_ec2_autoscaling_group", _sql(
        "aws_ec2_autoscaling_group", "autoscaling_group_arn, name, status, created_time, desired_capacity, "
        "launch_template_id, instances, target_group_arns, tags"), _asg, "Auto Scaling groups"),
    Source("rds_db_cluster", "aws_rds_db_cluster", _sql(
        "aws_rds_db_cluster", "arn, db_cluster_identifier, status, create_time, engine, engine_mode, tags"),
        _rds_cluster, "RDS clusters"),
    Source("rds_db_instance", "aws_rds_db_instance", _sql(
        "aws_rds_db_instance", "arn, db_instance_identifier, db_cluster_identifier, status, class, engine, multi_az, "
        "allocated_storage, create_time, subnets, tags"), _rds_instance, "RDS instances"),
    Source("rds_db_snapshot", "aws_rds_db_snapshot", _sql(
        "aws_rds_db_snapshot", "arn, db_snapshot_identifier, db_instance_identifier, type, status, allocated_storage, "
        "create_time, tags"), _rds_snapshot(cluster=False), "RDS snapshots"),
    Source("rds_db_cluster_snapshot", "aws_rds_db_cluster_snapshot", _sql(
        "aws_rds_db_cluster_snapshot", "arn, db_cluster_snapshot_identifier, db_cluster_identifier, type, status, "
        "allocated_storage, create_time, tags"), _rds_snapshot(cluster=True), "RDS cluster snapshots"),
    Source("ecs_cluster", "aws_ecs_cluster", _sql(
        "aws_ecs_cluster", "cluster_arn, cluster_name, status, active_services_count, running_tasks_count, tags"),
        _ecs_cluster, "ECS clusters"),
    Source("ecs_service", "aws_ecs_service", _sql(
        "aws_ecs_service", "arn, service_name, cluster_arn, status, task_definition, desired_count, running_count, "
        "launch_type, created_at, load_balancers, network_configuration, tags"), _ecs_service, "ECS services"),
    Source("lambda_function", "aws_lambda_function", _sql(
        "aws_lambda_function", "arn, name, state, runtime, memory_size, last_modified, vpc_subnet_ids, tags"),
        _lambda, "Lambda functions"),
)


def aliases(n: Node) -> list[str]:
    """Every id other rows may use to point at this node (AWS ids, names, rds:<identifier>)."""
    out = [n.id, n.short_id]
    a = n.attrs
    for k in ("instance_id", "volume_id", "snapshot_id", "image_id", "eni_id", "allocation_id", "nat_gateway_id",
              "subnet_id", "vpc_id", "vpc_endpoint_id"):
        if a.get(k) and n.type in _ALIAS_TYPES.get(k, ()):
            out.append(str(a[k]))
    if n.type == "rds_db_instance":
        out.append(f"rds:{n.name}")
    if n.type == "rds_db_cluster":
        out.append(f"rds-cluster:{n.name}")
    if n.type == "autoscaling_group" and a.get("name"):
        out.append(f"asg:{a['name']}")
    return out


_ALIAS_TYPES = {"instance_id": ("ec2_instance",), "volume_id": ("ebs_volume",), "snapshot_id": ("ebs_snapshot",),
                "image_id": ("ec2_ami",), "eni_id": ("ec2_network_interface",), "allocation_id": ("vpc_eip",),
                "nat_gateway_id": ("vpc_nat_gateway",), "subnet_id": ("vpc_subnet",), "vpc_id": ("vpc",),
                "vpc_endpoint_id": ("vpc_endpoint",)}


def resolve(nodes: list[Node], refs: list[Ref]) -> list[Edge]:
    index: dict[str, str] = {}
    for n in nodes:
        for a in aliases(n):
            index.setdefault(a, n.id)
    explicit = {f for f, _, label, _ in refs if label == "_explicit"}
    by_vpc: dict[str, list[str]] = {}
    for n in nodes:
        if n.type == "vpc_subnet":
            by_vpc.setdefault(str(n.attrs.get("vpc_id") or ""), []).append(str(n.attrs.get("subnet_id")))
    expanded: list[Ref] = []
    for f, t, label, kind in refs:
        if label == "_explicit":
            continue
        if f.startswith("main:"):  # the main route table: every subnet of the VPC without a table of its own
            expanded += [(sub, t, label, kind) for sub in by_vpc.get(f[5:], []) if sub not in explicit]
        else:
            expanded.append((f, t, label, kind))
    out = []
    for f, t, label, kind in expanded:
        a, b = index.get(f), index.get(t)
        if a and b:
            out.append(Edge(a, b, label, kind))
    return out
