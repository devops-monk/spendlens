"""Cleanup groups (connected resources that can be removed together) and what deleting one resource affects.

A single-resource waste check sees "this volume is attached to a stopped instance". The graph sees the whole group:
the stopped instance, both its volumes, its Elastic IP and the snapshots of those volumes, with one total saving.
Rules run in a fixed order and each resource joins at most one group, so no saving is counted twice. A rule only
runs when the sources it relies on were collected (no volumes collected must not make every snapshot look orphaned).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config import GraphConfig
from ..models import Finding, Status
from .model import Graph, Node, type_label

OWNER_KEYS = ("Owner", "owner", "Team", "team", "Service Owner", "CostCentre")

KINDS = {
    "stopped_instance": "Stopped instance",
    "idle_load_balancer": "Load balancer with no targets",
    "unused_ami": "Unused AMI",
    "orphan_snapshots": "Snapshots of a deleted volume",
    "unattached_volume": "Unattached volume",
    "unassociated_eip": "Unassociated Elastic IP",
    "rds_orphan_snapshots": "Snapshots of a deleted database",
}
NEEDS = {  # sources a rule relies on
    "stopped_instance": {"ec2_instance", "ebs_volume"},
    "idle_load_balancer": {"target_group"},
    "unused_ami": {"ec2_ami", "ec2_instance", "autoscaling_group"},
    "orphan_snapshots": {"ebs_snapshot", "ebs_volume", "ec2_ami"},
    "unattached_volume": {"ebs_volume"},
    "unassociated_eip": {"vpc_eip", "vpc_nat_gateway"},
    "rds_orphan_snapshots": {"rds_db_snapshot", "rds_db_instance", "rds_db_cluster"},
}


@dataclass
class Group:
    id: str
    kind: str
    anchor: str
    members: list[str]
    monthly_saving: float
    title: str
    action: str
    risk: str
    evidence: list[str] = field(default_factory=list)
    region: str | None = None
    account_id: str | None = None
    owner: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)  # waste findings for members (alarm)
    findings_saving: float = 0.0

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.kind, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "kind_label": self.kind_label}


def _when(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _days(s: str | None, now: datetime) -> int | None:
    d = _when(s)
    return (now - d).days if d else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _money(v: float) -> str:
    return f"${v:,.2f}"


def owner_of(nodes: list[Node], keys: tuple[str, ...] = OWNER_KEYS) -> str:
    for n in nodes:
        for k in keys:
            if n.tags.get(k):
                return str(n.tags[k])
    return ""


class Analyzer:
    def __init__(self, g: Graph, cfg: GraphConfig, *, now: datetime | None = None, collected: set[str] | None = None,
                 owner_keys: tuple[str, ...] = OWNER_KEYS):
        self.g, self.cfg = g, cfg
        self.now = now or datetime.now(UTC)
        self.collected = collected
        self.owner_keys = owner_keys
        self.taken: set[str] = set()
        self.groups: list[Group] = []

    def ok(self, rule: str) -> bool:
        return self.collected is None or NEEDS[rule] <= self.collected

    def add(self, kind: str, anchor: Node, members: list[str], action: str, risk: str, evidence: list[str],
            title: str) -> None:
        members = [m for m in dict.fromkeys(members) if m not in self.taken]
        if anchor.id not in members:
            return
        saving = self.g.total_cost(members)
        if saving <= 0:
            return
        self.taken.update(members)
        nodes = [self.g.nodes[m] for m in members]
        self.groups.append(Group(f"{kind}:{anchor.short_id}", kind, anchor.id, members, saving, title, action, risk,
                                 evidence, anchor.region, anchor.account_id, owner_of(nodes, self.owner_keys)))

    # ----- helpers

    def users(self, node_id: str, label: str | None = None) -> list[str]:
        """Nodes that use this one (edges into it of kind uses)."""
        return [e.from_id for e in self.g.into(node_id) if e.kind == "uses" and (label is None or e.label == label)]

    def used(self, node_id: str, label: str | None = None) -> list[str]:
        return [e.to_id for e in self.g.out(node_id) if e.kind == "uses" and (label is None or e.label == label)]

    def snapshots_of(self, volume_id: str) -> list[str]:
        return [e.to_id for e in self.g.out(volume_id) if e.label == "snapshot"]

    def backs_ami(self, snap_id: str) -> bool:
        return any(e.label == "backed by" for e in self.g.into(snap_id))

    # ----- rules

    def stopped_instances(self) -> None:
        for n in sorted(self.g.of_type("ec2_instance"), key=lambda n: n.id):
            if n.state not in ("stopped", "stopping"):
                continue
            days = _days(n.attrs.get("stopped_since"), self.now)
            if days is not None and days < self.cfg.stopped_days:
                continue
            vols = [v for v in self.used(n.id, "mounts") if set(self.users(v, "mounts")) <= {n.id}]
            enis = self.used(n.id, "eni")
            eips = [x for e in enis for x in self.used(e, "public ip")] + self.used(n.id, "public ip")
            snaps = [s for v in vols for s in self.snapshots_of(v) if not self.backs_ami(s)]
            since = f"since {n.attrs['stopped_since'][:10]} ({days} days)" if days is not None else "(date unknown)"
            ev = [(f"{n.label} ({n.attrs.get('instance_type') or 'instance'}) is stopped {since}; stopped "
                   "instances are not billed, but what is attached to them is.")]
            if vols:
                gb = sum(self.g.nodes[v].attrs.get("size_gb") or 0 for v in vols)
                ev.append(f"{_plural(len(vols), 'volume')} ({gb:g} GB) still billed: "
                          f"{_money(self.g.total_cost(vols))}/month.")
            if eips:
                ev.append(f"{_plural(len(eips), 'Elastic IP')}: {_money(self.g.total_cost(eips))}/month.")
            if snaps:
                ev.append(f"{_plural(len(snaps), 'snapshot')} of these volumes, not used by any AMI: "
                          f"up to {_money(self.g.total_cost(snaps))}/month.")
            parts = [p for p in (_plural(len(vols), "volume") if vols else "", _plural(len(eips), "Elastic IP") if eips
                                 else "", _plural(len(snaps), "snapshot") if snaps else "") if p]
            self.add("stopped_instance", n, [n.id, *vols, *eips, *snaps],
                     "Terminate the instance, delete its volumes and snapshots, release its Elastic IP.",
                     "Hard to undo. Confirm with the owner first; keep an AMI or a final snapshot if the data may be "
                     "needed.", ev, f"Stopped instance {n.label}" + (f" with {', '.join(parts)}" if parts else ""))

    def idle_load_balancers(self) -> None:
        for n in sorted(self.g.of_type("load_balancer"), key=lambda n: n.id):
            if n.attrs.get("lb_type") == "classic":
                if n.attrs.get("targets"):
                    continue
                tgs: list[str] = []
            else:
                tgs = self.used(n.id, "forwards to")
                if any(self.g.nodes[t].attrs.get("targets") for t in tgs):
                    continue
                if any(self.users(t, "registers in") for t in tgs):  # an ASG/ECS service may register targets later
                    continue
            ev = [f"{n.label} ({n.attrs.get('lb_type')}) has no registered targets"
                  + (f" in its {_plural(len(tgs), 'target group')}" if tgs else "") + "."]
            self.add("idle_load_balancer", n, [n.id, *tgs],
                     "Delete the load balancer and its empty target groups.",
                     "Check that no DNS record (Route 53, CloudFront) still points at it.", ev,
                     f"Load balancer {n.label} with no targets")

    def unused_amis(self) -> None:
        for n in sorted(self.g.of_type("ec2_ami"), key=lambda n: n.id):
            if any(e.label == "launched from" for e in self.g.into(n.id)):
                continue
            age = _days(n.created, self.now)
            if age is not None and age < self.cfg.ami_unused_days:
                continue
            last = _days(n.attrs.get("last_launched"), self.now)
            if last is not None and last < self.cfg.ami_unused_days:
                continue
            snaps = [s for s in self.used(n.id, "backed by") if set(self.users(s, "backed by")) <= {n.id}]
            ev = [f"{n.label} is not used by any running or stopped instance"
                  + (f" and was created {age} days ago" if age is not None else "") + "."]
            if snaps:
                ev.append(f"Backed by {_plural(len(snaps), 'snapshot')}: up to {_money(self.g.total_cost(snaps))}/month.")
            self.add("unused_ami", n, [n.id, *snaps], "Deregister the AMI and delete its snapshots.",
                     "Check launch templates, Auto Scaling groups and other accounts that may launch it.", ev,
                     f"Unused AMI {n.label}" + (f" with {_plural(len(snaps), 'snapshot')}" if snaps else ""))

    def orphan_snapshots(self) -> None:
        by_vol: dict[str, list[Node]] = defaultdict(list)
        for n in self.g.of_type("ebs_snapshot"):
            vol = n.attrs.get("volume_id")
            if not vol or self.backs_ami(n.id) or any(e.label == "snapshot" for e in self.g.into(n.id)):
                continue
            age = _days(n.created, self.now)
            if age is not None and age < self.cfg.snapshot_orphan_days:
                continue
            by_vol[vol].append(n)
        for vol, snaps in sorted(by_vol.items()):
            snaps.sort(key=lambda s: s.created or "")
            newest = snaps[-1].created
            billed = all(s.cost_basis in ("cur", "cost_explorer") for s in snaps)
            ev = [f"Volume {vol} no longer exists; {_plural(len(snaps), 'snapshot')} of it remain"
                  + (f", the newest from {newest[:10]}" if newest else "") + ".",
                  "No AMI uses them." + (" The saving is what they were billed." if billed else
                                         " Snapshots are incremental, so the saving is an upper bound.")]
            self.add("orphan_snapshots", snaps[-1], [s.id for s in snaps],
                     "Delete the snapshots (keep the newest if the data may be needed).",
                     "Snapshots may be kept for compliance or recovery; check retention rules first.", ev,
                     f"{_plural(len(snaps), 'snapshot')} of deleted volume {vol}")

    def unattached_volumes(self) -> None:
        for n in sorted(self.g.of_type("ebs_volume"), key=lambda n: n.id):
            if n.state != "available" or self.users(n.id, "mounts"):
                continue
            age = _days(n.created, self.now)
            size = n.attrs.get("size_gb")
            self.add("unattached_volume", n, [n.id], "Snapshot the volume if needed, then delete it.",
                     "Hard to undo without a snapshot.",
                     [f"{n.label} ({f'{size:g}' if isinstance(size, (int, float)) else '?'} GB "
                      f"{n.attrs.get('volume_type') or ''}) is not "
                      "attached to any instance" + (f"; created {age} days ago" if age is not None else "") + "."],
                     f"Unattached volume {n.label}")

    def unassociated_eips(self) -> None:
        for n in sorted(self.g.of_type("vpc_eip"), key=lambda n: n.id):
            if n.state != "unassociated" or self.users(n.id):
                continue
            self.add("unassociated_eip", n, [n.id], "Release the Elastic IP.",
                     "A released address cannot be recovered; check allow-lists that name it.",
                     [f"{n.attrs.get('public_ip') or n.label} is not associated with anything and is billed hourly."],
                     f"Unassociated Elastic IP {n.attrs.get('public_ip') or n.label}")

    def rds_orphan_snapshots(self) -> None:
        by_src: dict[str, list[Node]] = defaultdict(list)
        for n in self.g.of_type("rds_db_snapshot"):
            if n.attrs.get("snapshot_type") != "manual" or not n.attrs.get("source"):
                continue
            if any(e.label == "snapshot" for e in self.g.into(n.id)):
                continue
            age = _days(n.created, self.now)
            if age is not None and age < self.cfg.rds_snapshot_orphan_days:
                continue
            by_src[str(n.attrs["source"])].append(n)
        for src, snaps in sorted(by_src.items()):
            snaps.sort(key=lambda s: s.created or "")
            self.add("rds_orphan_snapshots", snaps[-1], [s.id for s in snaps],
                     "Delete the manual snapshots (or export the newest to S3 if it must be kept).",
                     "Often kept on purpose as a final backup; check with the owner and retention rules.",
                     [f"Database {src} no longer exists; {_plural(len(snaps), 'manual snapshot')} of it remain."],
                     f"{_plural(len(snaps), 'manual snapshot')} of deleted database {src}")

    def run(self) -> list[Group]:
        for rule in ("stopped_instance", "idle_load_balancer", "unused_ami", "orphan_snapshots", "unattached_volume",
                     "unassociated_eip", "rds_orphan_snapshots"):
            if self.ok(rule):
                getattr(self, {"stopped_instance": "stopped_instances", "idle_load_balancer": "idle_load_balancers",
                               "unused_ami": "unused_amis", "orphan_snapshots": "orphan_snapshots",
                               "unattached_volume": "unattached_volumes", "unassociated_eip": "unassociated_eips",
                               "rds_orphan_snapshots": "rds_orphan_snapshots"}[rule])()
        self.groups.sort(key=lambda g: -g.monthly_saving)
        return self.groups


def _short(resource: str) -> str:
    return resource.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if resource.startswith("arn:") else resource


def link_findings(groups: list[Group], g: Graph, findings: list[Finding]) -> None:
    """Attach the waste alarms that are about a group's members (matched by ARN or AWS id)."""
    by: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.status is Status.ALARM:
            by[f.resource].append(f)
            by[_short(f.resource)].append(f)
    for grp in groups:
        seen: set[tuple[str, str]] = set()
        for m in grp.members:
            n = g.nodes[m]
            for f in by.get(n.id, []) + by.get(n.short_id, []):
                if (f.control, f.resource) in seen:
                    continue
                seen.add((f.control, f.resource))
                grp.findings.append({"control": f.control, "resource": f.resource,
                                     "saving": f.est_monthly_saving or 0.0, "reason": f.reason})
        grp.findings_saving = round(sum(x["saving"] for x in grp.findings), 2)


def cleanup_groups(g: Graph, cfg: GraphConfig, *, now: datetime | None = None, collected: set[str] | None = None,
                   findings: list[Finding] | None = None, owner_keys: tuple[str, ...] = OWNER_KEYS) -> list[Group]:
    groups = Analyzer(g, cfg, now=now, collected=collected, owner_keys=owner_keys).run()
    if findings:
        link_findings(groups, g, findings)
    return groups


# ------------------------------------------------------------------ impact


def impact(g: Graph, node_id: str) -> dict[str, Any]:
    """If this resource is deleted: what breaks or must go first, what is left behind unused, what is kept."""
    n = g.nodes[node_id]
    breaks = [{"id": e.from_id, "why": f"{type_label(g.nodes[e.from_id].type)} {g.nodes[e.from_id].label} "
                                       f"{e.label} it"} for e in g.into(node_id) if e.kind == "uses"]
    first = [{"id": e.to_id, "why": f"{type_label(g.nodes[e.to_id].type)} {g.nodes[e.to_id].label} must be removed "
                                    "first (or is removed with it)"} for e in g.out(node_id) if e.kind == "contains"]
    left = []
    for e in g.out(node_id):
        if e.kind != "uses":
            continue
        other_users = [x for x in g.into(e.to_id) if x.kind == "uses" and x.from_id != node_id]
        t = g.nodes[e.to_id]
        if not other_users and (t.monthly_cost or t.type in ("ebs_volume", "vpc_eip", "ebs_snapshot")):
            left.append({"id": t.id, "why": f"{type_label(t.type)} {t.label} would be left unused",
                         "monthly_cost": t.monthly_cost})
    kept = [{"id": e.to_id, "why": f"{type_label(g.nodes[e.to_id].type)} {g.nodes[e.to_id].label} ({e.label}) is "
                                   "independent and stays"} for e in g.out(node_id) if e.kind == "ref"]
    return {"node": n.to_dict(), "breaks": breaks + first, "left_behind": left, "kept": kept,
            "monthly_cost": n.monthly_cost or 0.0,
            "left_behind_cost": round(sum(x["monthly_cost"] or 0 for x in left), 2)}


# ------------------------------------------------------------------ shared costs


def shared_costs(g: Graph) -> list[dict[str, Any]]:
    """Split the cost of shared network resources across what uses them, as an allocation estimate:
    a NAT gateway's hourly charge equally across the subnets that route through it, a load balancer's across its
    target groups (a classic one's across its instances). Data processing charges are not in the price book."""
    out: list[dict[str, Any]] = []
    for n in g.nodes.values():
        if not n.monthly_cost:
            continue
        if n.type == "vpc_nat_gateway":
            users = sorted(e.from_id for e in g.into(n.id) if e.label == "routes via")
            basis = f"equal split across {_plural(len(users), 'subnet')} routing through it"
        elif n.type == "load_balancer":
            users = sorted(e.to_id for e in g.out(n.id) if e.label in ("forwards to", "targets"))
            what = "instance" if n.attrs.get("lb_type") == "classic" else "target group"
            basis = f"equal split across {_plural(len(users), what)}"
        else:
            continue
        for u in users:
            out.append({"source": n.id, "source_name": n.label, "target": u, "target_name": g.nodes[u].label,
                        "amount": round(n.monthly_cost / len(users), 2), "basis": basis})
    return out
