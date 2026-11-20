"""A small synthetic estate as Steampipe rows, for `--source demo` and the tests.

The rows go through the real collect() (via FakeClient), so the demo exercises the same row handling as a live run.
All data is made up: account 123456789012, invented names. `link` lets the demo builder reuse the ARNs of the demo
waste alarms, so cleanup groups show which waste findings they include.
"""

from __future__ import annotations

import re
from typing import Any

ACCOUNT = "123456789012"
R = "eu-west-1"


def _arn(kind: str, rid: str, region: str = R, service: str = "ec2", account: str = ACCOUNT) -> str:
    return f"arn:aws:{service}:{region}:{account}:{kind}/{rid}"


def _tags(team: str | None, name: str | None = None, env: str = "prod", **extra: str) -> dict[str, str]:
    t: dict[str, str] = {"Name": name} if name else {}
    if team:
        t.update({"Team": team, "Environment": env})
    t.update(extra)
    return t


def _base(region: str = R) -> dict[str, Any]:
    return {"account_id": ACCOUNT, "region": region}


AZ = {"subnet-0priv-a": f"{R}a", "subnet-0priv-b": f"{R}b", "subnet-0pub-a": f"{R}c"}
IPS = {"i-0payapi1": "10.20.1.11", "i-0payapi2": "10.20.2.12", "i-0legacyrpt": "10.20.1.30",
       "i-0batchwkr": "10.20.2.40", "i-0searchidx": "10.20.2.13"}
S3_IP, DDB_IP, WEB_IP = "3.5.64.10", "52.94.24.10", "93.184.216.34"


def flows() -> list[dict[str, Any]]:
    """A week of VPC flow log rows (aggregated per interface and address pair, as the traffic query returns them)."""
    gb = 1024 ** 3

    def f(eni: str, src: str, dst: str, gbs: float) -> dict[str, Any]:
        return {"interface_id": eni, "src_addr": src, "dst_addr": dst, "bytes": int(gbs * gb)}

    pay1, pay2, search, batch = (IPS[k] for k in ("i-0payapi1", "i-0payapi2", "i-0searchidx", "i-0batchwkr"))
    return [
        # payments-api-1 (AZ a) calls search-indexer (AZ b): cross-AZ, seen at both ends
        f("eni-0payapi1", pay1, search, 200), f("eni-0searchidx", pay1, search, 200),
        f("eni-0searchidx", search, pay1, 60), f("eni-0payapi1", search, pay1, 60),
        # payments-api-2 (private, via the NAT gateway) reads DynamoDB: no DynamoDB endpoint in the VPC
        f("eni-0payapi2", pay2, DDB_IP, 100), f("eni-0payapi2", DDB_IP, pay2, 300),
        # ... and S3, through the VPC's S3 gateway endpoint: free
        f("eni-0payapi2", pay2, S3_IP, 50), f("eni-0payapi2", S3_IP, pay2, 400),
        # search-indexer fetches from the internet through the NAT gateway
        f("eni-0searchidx", search, WEB_IP, 50), f("eni-0searchidx", WEB_IP, search, 20),
        # batch-worker and search-indexer share AZ b: free
        f("eni-0batchwkr", batch, search, 500),
        # the NAT gateway's own interface (skipped: counted at the sender) and an interface not collected
        f("eni-0nat", "10.20.3.5", WEB_IP, 50), f("eni-0elsewhere", "10.9.0.1", "10.9.0.2", 5),
    ]


def rows(link: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Rows per Steampipe table. link: {"eip": [{"arn", "region"}], "volume": [{"arn", "region", "size_gb",
    "volume_type"}], "lb": [{"arn", "region"}]} -- resources to reuse from the demo waste alarms."""
    link = link or {}
    t: dict[str, list[dict[str, Any]]] = {k: [] for k in (
        "aws_ec2_instance", "aws_ebs_volume", "aws_ebs_snapshot", "aws_ec2_ami", "aws_ec2_network_interface",
        "aws_vpc_eip", "aws_vpc_nat_gateway", "aws_vpc", "aws_vpc_subnet", "aws_vpc_endpoint", "aws_vpc_route_table",
        "aws_ec2_application_load_balancer", "aws_ec2_network_load_balancer", "aws_ec2_gateway_load_balancer",
        "aws_ec2_classic_load_balancer", "aws_ec2_target_group", "aws_ec2_autoscaling_group", "aws_rds_db_cluster",
        "aws_rds_db_instance", "aws_rds_db_snapshot", "aws_rds_db_cluster_snapshot", "aws_ecs_cluster",
        "aws_ecs_service", "aws_lambda_function")}

    # network
    t["aws_vpc"].append({**_base(), "arn": _arn("vpc", "vpc-0prod"), "vpc_id": "vpc-0prod", "cidr_block": "10.20.0.0/16",
                         "state": "available", "is_default": False, "tags": _tags("platform", "prod-vpc")})
    for az, sid in (("a", "subnet-0priv-a"), ("b", "subnet-0priv-b"), ("c", "subnet-0pub-a")):
        t["aws_vpc_subnet"].append({**_base(), "subnet_arn": _arn("subnet", sid), "subnet_id": sid, "vpc_id": "vpc-0prod",
                                    "availability_zone": f"{R}{az}", "cidr_block": "10.20.0.0/20", "state": "available",
                                    "tags": _tags("platform", sid.removeprefix("subnet-0"))})
    t["aws_vpc_endpoint"].append({**_base(), "vpc_endpoint_id": "vpce-0s3", "vpc_id": "vpc-0prod",
                                  "service_name": f"com.amazonaws.{R}.s3", "vpc_endpoint_type": "Gateway",
                                  "state": "available", "creation_timestamp": "2025-02-01T09:00:00+00:00",
                                  "tags": _tags("platform")})
    # private subnets route through the NAT gateway (b via the main table), the public one through the internet gateway
    t["aws_vpc_route_table"] += [
        {**_base(), "route_table_id": "rtb-0main", "vpc_id": "vpc-0prod", "associations": [{"Main": True}],
         "routes": [{"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-0prod"}]},
        {**_base(), "route_table_id": "rtb-0priva", "vpc_id": "vpc-0prod",
         "associations": [{"Main": False, "SubnetId": "subnet-0priv-a"}],
         "routes": [{"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-0prod"}]},
        {**_base(), "route_table_id": "rtb-0pub", "vpc_id": "vpc-0prod",
         "associations": [{"Main": False, "SubnetId": "subnet-0pub-a"}],
         "routes": [{"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-0prod"}]}]
    t["aws_vpc_nat_gateway"].append({**_base(), "arn": _arn("natgateway", "nat-0prod"), "nat_gateway_id": "nat-0prod",
                                     "state": "available", "create_time": "2025-02-01T09:00:00+00:00",
                                     "subnet_id": "subnet-0pub-a", "tags": _tags("platform", "prod-nat"),
                                     "nat_gateway_addresses": [{"AllocationId": "eipalloc-0nat",
                                                                "NetworkInterfaceId": "eni-0nat"}]})
    t["aws_ec2_network_interface"].append({**_base(), "network_interface_id": "eni-0nat", "status": "in-use",
                                           "interface_type": "nat_gateway", "description": "Interface for NAT Gateway",
                                           "private_ip_address": "10.20.3.5", "availability_zone": f"{R}c",
                                           "subnet_id": "subnet-0pub-a", "requester_managed": True, "tags": {}})
    t["aws_vpc_eip"].append({**_base(), "arn": _arn("eip", "eipalloc-0nat"), "allocation_id": "eipalloc-0nat",
                             "public_ip": "34.240.1.10", "association_id": "eipassoc-0nat",
                             "network_interface_id": "eni-0nat", "tags": _tags("platform", "prod-nat-ip")})

    def instance(iid: str, name: str, itype: str, team: str | None, subnet: str, vols: list[tuple[str, str, int, int | None]],
                 state: str = "running", stopped: str | None = None, image: str = "ami-0payments7",
                 asg: str | None = None, eip: str | None = None, launched: str = "2025-06-02T08:00:00+00:00") -> None:
        tags = _tags(team, name)
        if asg:
            tags["aws:autoscaling:groupName"] = asg
        eni = f"eni-{iid.removeprefix('i-')}"
        t["aws_ec2_instance"].append({
            **_base(), "arn": _arn("instance", iid), "instance_id": iid, "instance_type": itype, "instance_state": state,
            "launch_time": launched, "state_transition_time": stopped,
            "state_transition_reason": f"User initiated ({stopped[:19].replace('T', ' ')} GMT)" if stopped else "",
            "image_id": image, "subnet_id": subnet, "vpc_id": "vpc-0prod", "tags": tags,
            "block_device_mappings": [{"DeviceName": f"/dev/xvd{chr(97 + i)}", "Ebs": {"VolumeId": v[0]}}
                                      for i, v in enumerate(vols)],
            "network_interfaces": [{"NetworkInterfaceId": eni}]})
        t["aws_ec2_network_interface"].append({**_base(), "network_interface_id": eni, "status": "in-use",
                                               "interface_type": "interface", "description": "", "subnet_id": subnet,
                                               "availability_zone": AZ[subnet], "private_ip_address": IPS[iid],
                                               "attached_instance_id": iid, "attachment_time": launched,
                                               "association_allocation_id": eip, "requester_managed": False,
                                               "tags": {}})
        if eip:
            t["aws_vpc_eip"].append({**_base(), "arn": _arn("eip", eip), "allocation_id": eip, "public_ip": "52.18.7.21",
                                     "association_id": f"eipassoc-{iid}", "instance_id": iid,
                                     "network_interface_id": eni, "tags": _tags(team, f"{name}-ip")})
        for vid, vtype, size, iops in vols:
            t["aws_ebs_volume"].append({**_base(), "arn": _arn("volume", vid), "volume_id": vid, "volume_type": vtype,
                                        "size": size, "iops": iops, "state": "in-use", "create_time": launched,
                                        "snapshot_id": "", "tags": {},  # untagged: owners come from the instance
                                        "attachments": [{"InstanceId": iid, "State": "attached"}]})

    def snapshot(sid: str, vol: str, size: int, created: str, team: str | None = None, desc: str = "") -> None:
        t["aws_ebs_snapshot"].append({"account_id": ACCOUNT, "region": R,
                                      "arn": f"arn:aws:ec2:{R}::snapshot/{sid}", "snapshot_id": sid, "volume_id": vol,
                                      "volume_size": size, "state": "completed", "start_time": created,
                                      "description": desc, "tags": _tags(team)})

    # payments: ASG behind an ALB, launched from a current AMI
    for i, sub in ((1, "subnet-0priv-a"), (2, "subnet-0priv-b")):
        instance(f"i-0payapi{i}", f"payments-api-{i}", "m5.large", "payments", sub,
                 [(f"vol-0payapi{i}", "gp3", 50, None)], asg="payments-api-asg")
    t["aws_ec2_autoscaling_group"].append({
        **_base(), "autoscaling_group_arn": f"arn:aws:autoscaling:{R}:{ACCOUNT}:autoScalingGroup:1a2b:autoScalingGroupName/payments-api-asg",
        "name": "payments-api-asg", "status": None, "created_time": "2025-06-01T08:00:00+00:00", "desired_capacity": 2,
        "launch_template_id": "lt-0payments", "tags": _tags("payments"),
        "instances": [{"InstanceId": "i-0payapi1"}, {"InstanceId": "i-0payapi2"}],
        "target_group_arns": [f"arn:aws:elasticloadbalancing:{R}:{ACCOUNT}:targetgroup/payments-tg/1a"]})
    alb = f"arn:aws:elasticloadbalancing:{R}:{ACCOUNT}:loadbalancer/app/payments-alb/9f"
    t["aws_ec2_application_load_balancer"].append({
        **_base(), "arn": alb, "name": "payments-alb", "scheme": "internet-facing", "state_code": "active",
        "created_time": "2025-06-01T08:00:00+00:00", "tags": _tags("payments"),
        "availability_zones": [{"SubnetId": "subnet-0priv-a"}, {"SubnetId": "subnet-0priv-b"}]})
    t["aws_ec2_target_group"].append({
        **_base(), "target_group_arn": f"arn:aws:elasticloadbalancing:{R}:{ACCOUNT}:targetgroup/payments-tg/1a",
        "target_group_name": "payments-tg", "target_type": "instance", "load_balancer_arns": [alb],
        "tags": _tags("payments"), "target_health_descriptions": [
            {"Target": {"Id": "i-0payapi1"}, "TargetHealth": {"State": "healthy"}},
            {"Target": {"Id": "i-0payapi2"}, "TargetHealth": {"State": "healthy"}}]})
    t["aws_ec2_ami"].append({**_base(), "image_id": "ami-0payments7", "name": "payments-api-v7", "state": "available",
                             "creation_date": "2025-05-30T10:00:00+00:00", "last_launched_time": "2026-11-01T06:00:00+00:00",
                             "tags": _tags("payments"),
                             "block_device_mappings": [{"Ebs": {"SnapshotId": "snap-0ami7root"}}]})
    snapshot("snap-0ami7root", "vol-0buildhost", 30, "2025-05-30T10:00:00+00:00", "payments", "Created by CreateImage")

    # a long-stopped reporting box: 2 volumes, an Elastic IP and snapshots of its data volume
    instance("i-0legacyrpt", "legacy-reporting", "m5.xlarge", "data", "subnet-0priv-a",
             [("vol-0rptroot", "gp2", 100, None), ("vol-0rptdata", "gp2", 500, None)], state="stopped",
             stopped="2026-08-02T17:40:00+00:00", image="ami-0golden2024", eip="eipalloc-0rpt",
             launched="2024-03-11T09:00:00+00:00")
    for i, d in enumerate(("2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01")):
        # the two newest were taken by a script that does not copy tags
        snapshot(f"snap-0rptdata{i}", "vol-0rptdata", 500, f"{d}T02:00:00+00:00", "data" if i < 2 else None,
                 "nightly backup")

    # stopped last week: not a group yet (graph.stopped_days)
    instance("i-0batchwkr", "batch-worker", "t3.large", "data", "subnet-0priv-b", [("vol-0batch", "gp3", 80, None)],
             state="stopped", stopped="2026-11-05T11:00:00+00:00")

    # search: a big provisioned-IOPS volume on a running instance
    instance("i-0searchidx", "search-indexer", "r5.large", "search", "subnet-0priv-b",
             [("vol-0searchio1", "io1", 1000, 10000)])

    # snapshots of a volume deleted long ago, and an old golden AMI nobody launches
    for i in range(5):
        snapshot(f"snap-0oldvol{i}", "vol-0deleted01", 250, f"2025-0{i + 1}-15T03:00:00+00:00", None, "weekly")
    t["aws_ec2_ami"].append({**_base(), "image_id": "ami-0golden2024", "name": "golden-base-2024", "state": "available",
                             "creation_date": "2024-06-20T10:00:00+00:00", "last_launched_time": "2024-09-01T10:00:00+00:00",
                             "tags": _tags("platform"),
                             "block_device_mappings": [{"Ebs": {"SnapshotId": "snap-0golden"}}]})
    snapshot("snap-0golden", "vol-0bakery", 40, "2024-06-20T10:00:00+00:00", "platform", "Created by CreateImage")
    # (the legacy box was launched from it, so it is still in use: no group)

    # an unused AMI nobody launched from
    t["aws_ec2_ami"].append({**_base(), "image_id": "ami-0exp2023", "name": "experiment-gpu-2023", "state": "available",
                             "creation_date": "2023-10-02T10:00:00+00:00", "last_launched_time": None,
                             "tags": _tags(None),
                             "block_device_mappings": [{"Ebs": {"SnapshotId": "snap-0exproot"}},
                                                       {"Ebs": {"SnapshotId": "snap-0expdata"}}]})
    snapshot("snap-0exproot", "vol-0exp1", 60, "2023-10-02T10:00:00+00:00", None, "Created by CreateImage")
    snapshot("snap-0expdata", "vol-0exp2", 200, "2023-10-02T10:00:00+00:00", None, "Created by CreateImage")

    # unattached volumes and unassociated Elastic IPs (reusing the demo waste alarms' ARNs when given)
    vols = link.get("volume") or [{"arn": _arn("volume", "vol-0olddata"), "region": R, "size_gb": 200, "volume_type": "gp2"}]
    for v in vols[:3]:
        vid = v["arn"].rsplit("/", 1)[-1]
        t["aws_ebs_volume"].append({"account_id": ACCOUNT, "region": v["region"], "arn": v["arn"], "volume_id": vid,
                                    "volume_type": v.get("volume_type") or "gp2", "size": int(float(v.get("size_gb") or 100)),
                                    "iops": int(float(v["iops"])) if v.get("iops") else None, "state": "available",
                                    "create_time": "2025-09-14T12:00:00+00:00",
                                    "snapshot_id": "", "attachments": [], "tags": {}})
    eips = link.get("eip") or [{"arn": _arn("eip", "eipalloc-0spare"), "region": R}]
    for i, e in enumerate(eips[:3]):
        aid = e["arn"].rsplit("/", 1)[-1]
        t["aws_vpc_eip"].append({"account_id": ACCOUNT, "region": e["region"], "arn": e["arn"], "allocation_id": aid,
                                 "public_ip": f"52.{i}.10.{i}", "association_id": None, "instance_id": None,
                                 "network_interface_id": None, "tags": {}})

    # a load balancer whose targets are gone (one of the demo waste alarms when given)
    for lb in (link.get("lb") or [{"arn": f"arn:aws:elasticloadbalancing:{R}:{ACCOUNT}:loadbalancer/app/old-promo/7c",
                                   "region": R}])[:1]:
        segs = lb["arn"].split("loadbalancer/", 1)[-1].split("/")  # app/<name>/<id> or app/<name>
        name = segs[1] if len(segs) > 1 else segs[0]
        tg = f"arn:aws:elasticloadbalancing:{lb['region']}:{ACCOUNT}:targetgroup/{name}-tg/2b"
        t["aws_ec2_application_load_balancer"].append({
            "account_id": ACCOUNT, "region": lb["region"], "arn": lb["arn"], "name": name, "scheme": "internet-facing",
            "state_code": "active", "created_time": "2025-11-20T08:00:00+00:00", "tags": _tags("checkout", env="prod"),
            "availability_zones": []})
        t["aws_ec2_target_group"].append({"account_id": ACCOUNT, "region": lb["region"], "target_group_arn": tg,
                                          "target_group_name": f"{name}-tg", "target_type": "instance",
                                          "load_balancer_arns": [lb["arn"]], "target_health_descriptions": [],
                                          "tags": _tags("checkout")})

    # checkout on ECS behind its own ALB (ip targets)
    cluster = f"arn:aws:ecs:{R}:{ACCOUNT}:cluster/prod"
    t["aws_ecs_cluster"].append({**_base(), "cluster_arn": cluster, "cluster_name": "prod", "status": "ACTIVE",
                                 "active_services_count": 1, "running_tasks_count": 3, "tags": _tags("platform")})
    calb = f"arn:aws:elasticloadbalancing:{R}:{ACCOUNT}:loadbalancer/app/checkout-alb/3d"
    ctg = f"arn:aws:elasticloadbalancing:{R}:{ACCOUNT}:targetgroup/checkout-tg/4e"
    t["aws_ec2_application_load_balancer"].append({
        **_base(), "arn": calb, "name": "checkout-alb", "scheme": "internet-facing", "state_code": "active",
        "created_time": "2025-03-01T08:00:00+00:00", "tags": _tags("checkout"),
        "availability_zones": [{"SubnetId": "subnet-0priv-a"}, {"SubnetId": "subnet-0priv-b"}]})
    t["aws_ec2_target_group"].append({**_base(), "target_group_arn": ctg, "target_group_name": "checkout-tg",
                                      "target_type": "ip", "load_balancer_arns": [calb], "tags": _tags("checkout"),
                                      "target_health_descriptions": [
                                          {"Target": {"Id": f"10.20.1.{i}"}, "TargetHealth": {"State": "healthy"}}
                                          for i in range(3)]})
    t["aws_ecs_service"].append({
        **_base(), "arn": f"arn:aws:ecs:{R}:{ACCOUNT}:service/prod/checkout", "service_name": "checkout",
        "cluster_arn": cluster, "status": "ACTIVE", "task_definition": f"arn:aws:ecs:{R}:{ACCOUNT}:task-definition/checkout:42",
        "desired_count": 3, "running_count": 3, "launch_type": "FARGATE", "created_at": "2025-03-01T08:00:00+00:00",
        "tags": _tags("checkout"), "load_balancers": [{"TargetGroupArn": ctg}],
        "network_configuration": {"AwsvpcConfiguration": {"Subnets": ["subnet-0priv-a", "subnet-0priv-b"]}}})

    # databases: a live Multi-AZ instance with a manual snapshot, and manual snapshots of a deleted one
    t["aws_rds_db_instance"].append({
        **_base(), "arn": f"arn:aws:rds:{R}:{ACCOUNT}:db:payments-db", "db_instance_identifier": "payments-db",
        "db_cluster_identifier": None, "status": "available", "class": "db.r5.large", "engine": "postgres",
        "multi_az": True, "allocated_storage": 200, "create_time": "2024-02-01T08:00:00+00:00", "tags": _tags("payments"),
        "subnets": [{"SubnetIdentifier": "subnet-0priv-a"}, {"SubnetIdentifier": "subnet-0priv-b"}]})
    t["aws_rds_db_snapshot"].append({**_base(), "arn": f"arn:aws:rds:{R}:{ACCOUNT}:snapshot:payments-db-pre-upgrade",
                                     "db_snapshot_identifier": "payments-db-pre-upgrade",
                                     "db_instance_identifier": "payments-db", "type": "manual", "status": "available",
                                     "allocated_storage": 200, "create_time": "2026-09-30T22:00:00+00:00",
                                     "tags": _tags("payments")})
    for i, d in enumerate(("2025-01-31", "2025-03-31")):
        t["aws_rds_db_snapshot"].append({**_base(), "arn": f"arn:aws:rds:{R}:{ACCOUNT}:snapshot:reports-db-final-{i}",
                                         "db_snapshot_identifier": f"reports-db-final-{i}",
                                         "db_instance_identifier": "reports-db-2023", "type": "manual",
                                         "status": "available", "allocated_storage": 100,
                                         "create_time": f"{d}T22:00:00+00:00", "tags": _tags("data")})

    # platform functions in the public subnet, and one nobody tagged
    for name, team in (("log-shipper", "platform"), ("dns-sync", "platform"), ("report-export", None)):
        t["aws_lambda_function"].append({**_base(), "arn": f"arn:aws:lambda:{R}:{ACCOUNT}:function:{name}",
                                         "name": name, "state": "Active", "runtime": "python3.12", "memory_size": 256,
                                         "last_modified": "2026-09-01T09:00:00+00:00", "tags": _tags(team),
                                         "vpc_subnet_ids": ["subnet-0pub-a"]})

    # a function in the VPC
    t["aws_lambda_function"].append({**_base(), "arn": f"arn:aws:lambda:{R}:{ACCOUNT}:function:thumbnailer",
                                     "name": "thumbnailer", "state": "Active", "runtime": "python3.12",
                                     "memory_size": 512, "last_modified": "2026-10-01T09:00:00+00:00",
                                     "tags": _tags("checkout"), "vpc_subnet_ids": ["subnet-0priv-b"]})
    return t


def created_by() -> dict[str, dict[str, str]]:
    """Who created resources made in the last 90 days (all LookupEvents keeps), as `graph collect` records it with
    graph.owner_from_cloudtrail."""
    return {
        "report-export": {"by": "bob@example.com", "at": "2026-09-01T09:00:00+00:00", "event": "CreateFunction20150331"},
        "payments-db-pre-upgrade": {"by": "carol@example.com", "at": "2026-09-30T22:00:00+00:00",
                                    "event": "CreateDBSnapshot"},
    }


class FakeClient:
    """Answers collect()'s queries from rows(), matched by the table in `from <table>`."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]], fail: dict[str, str] | None = None):
        self.tables, self.fail, self.sql = tables, fail or {}, []

    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        from ..steampipe import SteampipeError

        self.sql.append(sql)
        m = re.search(r"\bfrom\s+(\w+)", sql)
        table = m.group(1) if m else ""
        if table in self.fail:
            raise SteampipeError(self.fail[table])
        return [dict(r) for r in self.tables.get(table, [])]

    def close(self) -> None:
        pass
