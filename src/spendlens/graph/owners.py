"""Suggested owner tags for untagged resources, from what they are attached to.

An untagged volume belongs to whoever owns the instance that mounts it. Rules, strongest first; for each missing tag
the first rule that finds a value wins, and every suggestion says where it came from:

  high    directly attached or managed: volume <- the instance mounting it, network interface / Elastic IP <- its
          instance or NAT gateway, instance <- its Auto Scaling group, target group <- its load balancer,
          task definition <- its ECS service, RDS instance <- its cluster
  medium  one step removed: snapshot <- the volume it was taken from (or that volume's instance), AMI snapshot <-
          the AMI, RDS snapshot <- its database, AMI <- the instances launched from it (when they all agree)
  medium  opt-in (graph.owner_from_cloudtrail): who created it, from CloudTrail, for the owner tag only
          (graph.cloudtrail_owner_key), when nothing attached already names an owner
  low     the subnet: the value most tagged resources in the same subnet carry (at least 3, at least 60% agreeing)

Only tags in the tagging policy (tagging.mandatory) and the usual owner keys are suggested, only when the resource
lacks them (compared case-insensitively), and only values that a related resource actually carries. spendlens never
applies them: `graph owners --script` writes the commands for a person to review and run.
"""

from __future__ import annotations

import json
import shlex
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from .model import Graph, Node, type_label

LEVELS = {"high": 3, "medium": 2, "low": 1}
SKIP_TYPES = {"vpc", "vpc_subnet", "ecs_task_definition"}  # hubs, and nodes whose tags are not collected
SUBNET_MIN, SUBNET_SHARE = 3, 0.6


@dataclass
class Suggestion:
    resource: str
    type: str
    name: str
    region: str
    key: str
    value: str
    source: str  # the resource the value comes from
    source_name: str
    confidence: str  # high | medium | low
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has(n: Node, key: str) -> bool:
    k = key.lower()
    return any(str(t).lower() == k and str(v).strip() for t, v in n.tags.items())


def _tag(n: Node, key: str) -> tuple[str, str] | None:
    """(key as this resource spells it, value), matching the key case-insensitively."""
    k = key.lower()
    for t, v in n.tags.items():
        if str(t).lower() == k and str(v).strip():
            return str(t), str(v)
    return None


def _value(n: Node, key: str) -> str | None:
    t = _tag(n, key)
    return t[1] if t else None


class Owners:
    def __init__(self, g: Graph, keys: list[str], creator_key: str | None = None):
        self.g = g
        self.creator_key = (creator_key or "").lower()
        seen: set[str] = set()
        self.keys = [k for k in keys if not (k.lower() in seen or seen.add(k.lower()))]  # Team == team
        self.subnet_of: dict[str, list[str]] = defaultdict(list)  # subnet id -> resources in it
        for e in g.edges:
            if e.label == "in subnet":
                self.subnet_of[e.to_id].append(e.from_id)

    def _into(self, node_id: str, *labels: str) -> list[Node]:
        return [self.g.nodes[e.from_id] for e in self.g.into(node_id) if e.label in labels]

    def _out(self, node_id: str, *labels: str) -> list[Node]:
        return [self.g.nodes[e.to_id] for e in self.g.out(node_id) if e.label in labels]

    def _through(self, nodes: list[Node]) -> list[Node]:
        """An untagged network interface stands in for its instance or NAT gateway."""
        out = []
        for n in nodes:
            out.append(n)
            if n.type == "ec2_network_interface":
                out += self._into(n.id, "eni")
        return out

    def candidates(self, n: Node) -> list[tuple[list[Node], str, str]]:
        """(source resources, confidence, why) in priority order."""
        t, c = n.type, []
        if t == "ebs_volume":
            c.append((self._into(n.id, "mounts"), "high", "mounted by"))
        elif t == "ec2_network_interface":
            c.append((self._into(n.id, "eni"), "high", "attached to"))
        elif t == "vpc_eip":
            c.append((self._through(self._into(n.id, "public ip")), "high", "associated with"))
        elif t == "ec2_instance":
            c.append((self._into(n.id, "manages"), "high", "managed by"))
        elif t == "target_group":
            c.append((self._into(n.id, "forwards to") + self._into(n.id, "registers in"), "high", "used by"))
        elif t == "rds_db_instance":
            c.append((self._into(n.id, "member"), "high", "member of"))
        elif t == "ebs_snapshot":
            vols = self._into(n.id, "snapshot")
            c.append((vols, "medium", "snapshot of"))
            c.append(([i for v in vols for i in self._into(v.id, "mounts")], "medium", "snapshot of a volume mounted by"))
            c.append((self._into(n.id, "backed by"), "medium", "backs AMI"))
        elif t == "rds_db_snapshot":
            c.append((self._into(n.id, "snapshot"), "medium", "snapshot of"))
        elif t == "ec2_ami":
            c.append((self._into(n.id, "launched from"), "medium", "launched by"))
        return c

    def subnet_majority(self, n: Node, key: str) -> tuple[str, str] | None:
        subnets = self._out(n.id, "in subnet")
        if not subnets:
            return None
        votes: Counter[str] = Counter()
        for s in subnets:
            for rid in self.subnet_of.get(s.id, []):
                if rid != n.id and (v := _value(self.g.nodes[rid], key)):
                    votes[v] += 1
        if not votes:
            return None
        value, count = votes.most_common(1)[0]
        total = sum(votes.values())
        if count >= SUBNET_MIN and count / total >= SUBNET_SHARE:
            return value, f"{count} of {total} tagged resources in {subnets[0].label} carry it"
        return None

    def suggest(self, n: Node) -> list[Suggestion]:
        if n.type in SKIP_TYPES or n.attrs.get("requester_managed"):
            return []
        missing = [k for k in self.keys if not _has(n, k)]
        if not missing:
            return []
        out = []
        cands = self.candidates(n)
        for key in missing:
            found = None
            for sources, conf, why in cands:
                vals = {_value(s, key) for s in sources} - {None}
                if len(vals) == 1:  # every related resource agrees
                    src = next(s for s in sources if _value(s, key))
                    spelled, value = _tag(src, key)  # type: ignore[misc]
                    found = Suggestion(n.id, n.type, n.label, n.region or "", spelled, value, src.id, src.label, conf,
                                       f"{why} {type_label(src.type)} {src.label}")
                    break
            if (found is None and self.creator_key and key.lower() == self.creator_key
                    and (by := n.attrs.get("created_by"))):
                when = str(n.attrs.get("created_at") or "")[:10]
                found = Suggestion(n.id, n.type, n.label, n.region or "", key, str(by), "", "", "medium",
                                   f"created by {by} ({n.attrs.get('created_event')}{' on ' + when if when else ''}, "
                                   "CloudTrail)")
            if found is None and (m := self.subnet_majority(n, key)):
                found = Suggestion(n.id, n.type, n.label, n.region or "", key, m[0], "", "", "low", m[1])
            if found:
                out.append(found)
        return out

    def all(self, min_confidence: str = "low") -> list[Suggestion]:
        floor = LEVELS[min_confidence]
        out = [s for n in sorted(self.g.nodes.values(), key=lambda n: (n.type, n.label)) for s in self.suggest(n)
               if LEVELS[s.confidence] >= floor]
        return sorted(out, key=lambda s: (-LEVELS[s.confidence], s.type, s.name, s.key))


def suggest_owners(g: Graph, keys: list[str], *, min_confidence: str = "low",
                   creator_key: str | None = None) -> list[Suggestion]:
    keys = [*keys, creator_key] if creator_key else keys
    return Owners(g, keys, creator_key).all(min_confidence)


def by_resource(suggestions: list[Suggestion]) -> dict[str, list[Suggestion]]:
    out: dict[str, list[Suggestion]] = defaultdict(list)
    for s in suggestions:
        out[s.resource].append(s)
        short = s.resource.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if s.resource.startswith("arn:") else s.resource
        if short != s.resource:
            out[short].append(s)
    return out


def script(suggestions: list[Suggestion], *, include_low: bool = False) -> str:
    """A shell script that applies the suggestions with the Resource Groups Tagging API. Written for a person to
    review and run; spendlens never runs it. Low-confidence suggestions are commented out unless include_low."""
    per: dict[tuple[str, str], dict[str, tuple[str, str, str]]] = defaultdict(dict)
    for s in suggestions:
        per[(s.resource, s.region)][s.key] = (s.value, s.confidence, s.reason)
    lines = ["#!/bin/sh",
             "# Tags suggested by `spendlens graph owners`, from what each resource is attached to.",
             "# REVIEW BEFORE RUNNING. spendlens never runs this. Run it with a role allowed to tag resources",
             "# (tag:TagResources and the service's own tagging action). Low-confidence lines are commented out.",
             "set -eu", ""]
    for (arn, region), tags in sorted(per.items()):
        low = all(conf == "low" for _, conf, _ in tags.values())
        for k, (v, conf, why) in sorted(tags.items()):
            lines.append(f"# {k}={v} ({conf}: {why})")
        cmd = ("aws resourcegroupstaggingapi tag-resources"
               + (f" --region {shlex.quote(region)}" if region else "")
               + f" --resource-arn-list {shlex.quote(arn)}"
               + f" --tags {shlex.quote(json.dumps({k: v for k, (v, _, _) in sorted(tags.items())}))}")
        lines.append(("# " if low and not include_low else "") + cmd)
        lines.append("")
    return "\n".join(lines)
