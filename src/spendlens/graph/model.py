"""Nodes, edges and the graph built from them."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any

# Display names, and the column (left to right) each type is drawn in.
TYPES: dict[str, tuple[str, int]] = {
    "vpc": ("VPC", 0),
    "vpc_subnet": ("Subnet", 1),
    "vpc_endpoint": ("VPC endpoint", 2),
    "vpc_nat_gateway": ("NAT gateway", 2),
    "load_balancer": ("Load balancer", 2),
    "autoscaling_group": ("Auto Scaling group", 2),
    "ecs_cluster": ("ECS cluster", 2),
    "rds_db_cluster": ("RDS cluster", 2),
    "target_group": ("Target group", 3),
    "ecs_service": ("ECS service", 3),
    "lambda_function": ("Lambda function", 3),
    "ec2_instance": ("EC2 instance", 4),
    "rds_db_instance": ("RDS instance", 4),
    "ecs_task_definition": ("Task definition", 4),
    "ec2_network_interface": ("Network interface", 5),
    "ebs_volume": ("EBS volume", 5),
    "vpc_eip": ("Elastic IP", 6),
    "ebs_snapshot": ("EBS snapshot", 6),
    "rds_db_snapshot": ("RDS snapshot", 6),
    "ec2_ami": ("AMI", 7),
}
KINDS = ("uses", "contains", "ref")
HUBS = ("vpc", "vpc_subnet")


def type_label(t: str) -> str:
    return TYPES.get(t, (t.replace("_", " "), 0))[0]


def layer(t: str) -> int:
    return TYPES.get(t, ("", 4))[1]


@dataclass
class Node:
    id: str
    type: str
    name: str = ""
    account_id: str | None = None
    region: str | None = None
    state: str | None = None
    created: str | None = None
    monthly_cost: float | None = None
    cost_basis: str = "none"
    tags: dict[str, Any] = field(default_factory=dict)
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.name or self.short_id

    @property
    def short_id(self) -> str:
        i = self.id
        if i.startswith("arn:"):
            i = i.split(":", 5)[-1]
            i = i.rsplit("/", 1)[-1] if "/" in i else i.rsplit(":", 1)[-1]
        return i

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Edge:
    from_id: str
    to_id: str
    label: str
    kind: str = "uses"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Graph:
    def __init__(self, nodes: list[Node] | None = None, edges: list[Edge] | None = None):
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._out: dict[str, list[Edge]] = defaultdict(list)
        self._in: dict[str, list[Edge]] = defaultdict(list)
        for n in nodes or []:
            self.add_node(n)
        for e in edges or []:
            self.add_edge(e)

    # ----- building

    def add_node(self, n: Node) -> None:
        old = self.nodes.get(n.id)
        if old is None or (not old.name and n.name):  # keep the richer record when a type is seen twice
            self.nodes[n.id] = n

    def add_edge(self, e: Edge) -> None:
        """Edges to resources that weren't collected are dropped (the analysis records missing parents itself)."""
        if e.from_id == e.to_id or e.from_id not in self.nodes or e.to_id not in self.nodes:
            return
        if any(x.to_id == e.to_id and x.label == e.label for x in self._out[e.from_id]):
            return
        self.edges.append(e)
        self._out[e.from_id].append(e)
        self._in[e.to_id].append(e)

    @classmethod
    def from_rows(cls, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> Graph:
        fields = Node.__dataclass_fields__
        return cls([Node(**{k: v for k, v in n.items() if k in fields}) for n in nodes],
                   [Edge(e["from_id"], e["to_id"], e["label"], e.get("kind", "uses")) for e in edges])

    # ----- reading

    def out(self, node_id: str) -> list[Edge]:
        return self._out.get(node_id, [])

    def into(self, node_id: str) -> list[Edge]:
        return self._in.get(node_id, [])

    def neighbours(self, node_id: str) -> list[tuple[Edge, str]]:
        return [(e, e.to_id) for e in self.out(node_id)] + [(e, e.from_id) for e in self.into(node_id)]

    def of_type(self, t: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.type == t]

    def find(self, text: str) -> list[Node]:
        """A node by ARN, AWS id or name (exact first, then case-insensitive substring)."""
        t = text.strip()
        if t in self.nodes:
            return [self.nodes[t]]
        exact = [n for n in self.nodes.values() if t in (n.short_id, n.name)]
        if exact:
            return exact
        low = t.lower()
        return [n for n in self.nodes.values() if low in n.id.lower() or low in (n.name or "").lower()]

    def around(self, node_id: str, depth: int = 2, *, skip_types: tuple[str, ...] = HUBS) -> Graph:
        """The node and everything within `depth` links of it. Hubs (a VPC or subnet links to everything in it) are
        shown but not walked through, so unrelated neighbours stay out."""
        seen = {node_id: 0}
        q = deque([node_id])
        while q:
            cur = q.popleft()
            d = seen[cur]
            if d >= depth or (cur != node_id and self.nodes[cur].type in skip_types):
                continue
            for _, other in self.neighbours(cur):
                if other not in seen:
                    seen[other] = d + 1
                    q.append(other)
        return self.subgraph(seen)

    def subgraph(self, ids: Any) -> Graph:
        keep = set(ids)
        return Graph([n for i, n in self.nodes.items() if i in keep],
                     [e for e in self.edges if e.from_id in keep and e.to_id in keep])

    def total_cost(self, ids: Any = None) -> float:
        nodes = self.nodes.values() if ids is None else (self.nodes[i] for i in ids if i in self.nodes)
        return round(sum(n.monthly_cost or 0 for n in nodes), 2)

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            by_type[n.type] += 1
        by_label: dict[str, int] = defaultdict(int)
        for e in self.edges:
            by_label[e.label] += 1
        return {"nodes": len(self.nodes), "edges": len(self.edges), "by_type": dict(by_type),
                "by_label": dict(by_label), "monthly_cost": self.total_cost()}

    def to_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return [n.to_dict() for n in self.nodes.values()], [e.to_dict() for e in self.edges]
