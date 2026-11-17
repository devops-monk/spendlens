"""The AWS table catalog: what the Steampipe AWS plugin offers, so the assistant never has to guess.

Built from the plugin source at the pinned version by scripts/build_aws_catalog.py (data/aws_catalog.json.gz):
every table's service, description, columns (type + description), required filters, optional filter columns
(pushed down to the AWS API), and examples from the plugin docs.

When Steampipe is reachable, `with_live()` merges the installed plugin's real column list (information_schema,
no AWS calls): that list decides which columns exist; the catalog adds descriptions, filters and examples.
"""

from __future__ import annotations

import difflib
import gzip
import json
import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

PG_TYPES = {"STRING": "text", "INT": "bigint", "DOUBLE": "double precision", "BOOL": "boolean", "JSON": "jsonb",
            "TIMESTAMP": "timestamptz", "IPADDR": "inet", "CIDR": "cidr", "INET": "inet", "LTREE": "ltree",
            "DATETIME": "timestamptz", "UNKNOWN": "text"}

STOPWORDS = {"a", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "get", "give", "has", "have", "how", "i", "in", "is", "it", "its", "list", "me", "my", "of", "on", "or", "our", "show", "that", "the", "their", "there", "these", "this", "those", "to", "us", "was", "we", "what", "when", "where", "which", "who", "why", "with", "all", "many", "much", "per", "find", "tell", "see", "about", "each", "into", "than", "then", "them", "they", "your", "you"}

# question words -> table-name tokens (plural/singular are handled by _stem)
SYNONYMS = {
    "volume": ["ebs"], "disk": ["ebs", "volume"], "snapshot": ["snapshot"], "bucket": ["s3"], "object": ["s3"],
    "server": ["ec2", "instance"], "vm": ["ec2", "instance"], "machine": ["ec2", "instance"],
    "database": ["rds", "dynamodb"], "db": ["rds"], "postgres": ["rds"], "mysql": ["rds"], "aurora": ["rds"],
    "function": ["lambda"], "serverless": ["lambda"], "container": ["ecs", "eks"], "kubernetes": ["eks"],
    "k8s": ["eks"], "cluster": ["cluster"], "balancer": ["lb", "elb", "load"], "loadbalancer": ["lb", "elb"],
    "alb": ["application", "lb"], "nlb": ["network", "lb"], "ip": ["eip", "ip"], "elastic": ["eip"],
    "firewall": ["security", "group"], "sg": ["security", "group"], "port": ["security", "group", "rule"],
    "user": ["iam", "user"], "role": ["iam", "role"], "policy": ["iam", "policy"], "mfa": ["iam", "mfa"],
    "key": ["kms", "key", "access"], "secret": ["secretsmanager"], "password": ["iam", "password"],
    "certificate": ["acm"], "cert": ["acm"], "dns": ["route53"], "domain": ["route53"], "cdn": ["cloudfront"],
    "queue": ["sqs"], "topic": ["sns"], "stream": ["kinesis"], "log": ["cloudwatch", "log"], "alarm": ["cloudwatch"],
    "metric": ["metric"], "cpu": ["cpu", "utilization", "metric"], "memory": ["memory", "metric"],
    "trail": ["cloudtrail"], "audit": ["cloudtrail"], "cost": ["cost"], "spend": ["cost"], "bill": ["cost"],
    "billing": ["cost"], "price": ["pricing"], "budget": ["budgets"], "forecast": ["forecast"],
    "reservation": ["reserved"], "ri": ["reserved"], "commitment": ["savingsplans", "reserved"],
    "savings": ["savingsplans", "costoptimizationhub"], "recommendation": ["costoptimizationhub", "recommendation"],
    "rightsizing": ["costoptimizationhub"], "tag": ["tags", "tagging"], "untagged": ["tags", "tagging"],
    "cache": ["elasticache"], "redis": ["elasticache"], "search": ["opensearch", "elasticsearch"],
    "warehouse": ["redshift"], "network": ["vpc"], "subnet": ["vpc", "subnet"], "nat": ["nat", "gateway"],
    "vpn": ["vpn"], "image": ["ami", "ecr"], "ami": ["ami"], "registry": ["ecr"], "account": ["account"],
    "organization": ["organizations"], "region": ["region"], "encrypted": ["encryption", "encrypted"],
    "public": ["public"], "unattached": ["ebs", "volume"], "idle": ["metric"], "unused": ["unused"],
    "vulnerability": ["inspector2", "finding"], "threat": ["guardduty", "finding"], "compliance": ["config"],
    "backup": ["backup"], "patch": ["ssm", "patch"], "parameter": ["ssm", "parameter"],
}


def _stem(word: str) -> str:
    w = word.lower()
    for suf, rep in (("ies", "y"), ("sses", "ss"), ("es", ""), ("s", "")):
        if len(w) > 4 and w.endswith(suf) and not w.endswith("ss"):
            return w[: -len(suf)] + rep
    return w


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.split(r"[^a-zA-Z0-9]+", text or "") if t]


@dataclass
class Column:
    name: str
    type: str
    description: str = ""

    @property
    def pg_type(self) -> str:
        return PG_TYPES.get(self.type, "text")


@dataclass
class Table:
    name: str
    service: str
    description: str
    summary: str
    guide: str
    columns: dict[str, Column]
    required: list[str]
    any_of: list[str]
    filters: dict[str, list[str]]
    get_keys: list[str]
    examples: list[dict[str, str]]
    cost_explorer: bool
    time_series: bool
    live: bool = False  # column list confirmed by the installed plugin
    missing_live: list[str] = field(default_factory=list)  # catalog columns the installed plugin lacks

    def required_text(self) -> str:
        parts = []
        for c in self.required:
            parts.append(f"{c} = <value>")
        if self.any_of:
            parts.append("one of: " + " / ".join(f"{c} = <value>" for c in self.any_of))
        return " AND ".join(parts)


class Catalog:
    def __init__(self, raw: dict[str, Any]):
        self.version: str = raw.get("version", "")
        self.tables: dict[str, Table] = {}
        for name, t in raw["tables"].items():
            self.tables[name] = Table(
                name=name, service=t["service"], description=t["description"], summary=t["summary"],
                guide=t["guide"], columns={c[0]: Column(*c) for c in t["columns"]}, required=t["required"],
                any_of=t["any_of"], filters=t["filters"], get_keys=t["get_keys"], examples=t["examples"],
                cost_explorer=t["cost_explorer"], time_series=t["time_series"])
        self._index: dict[str, tuple[set[str], set[str], set[str], set[str]]] = {}
        for name, t in self.tables.items():
            self._index[name] = (set(tokens(name.removeprefix("aws_"))), set(tokens(t.service)),
                                 set(tokens(f"{t.description} {t.summary} {t.guide}")) - STOPWORDS,
                                 {p for c in t.columns for p in tokens(c)})

    # ----- lookup

    def get(self, name: str) -> Table | None:
        return self.tables.get((name or "").lower().split(".")[-1].strip('"'))

    def similar(self, name: str, n: int = 5) -> list[str]:
        return difflib.get_close_matches(name.lower(), list(self.tables), n=n, cutoff=0.6)

    def with_live(self, live: dict[str, list[tuple[str, str]]]) -> Catalog:
        """A copy where each table's columns are the installed plugin's (live wins; descriptions kept)."""
        new = object.__new__(Catalog)
        new.version, new._index, new.tables = self.version, self._index, dict(self.tables)
        for name, cols in live.items():
            base = self.tables.get(name)
            if base is None:
                new.tables[name] = Table(name, name.split("_")[1].upper() if "_" in name else "", "", "", "",
                                         {c: Column(c, _from_pg(t)) for c, t in cols}, [], [], {}, [], [], False,
                                         False, live=True)
                continue
            merged = {c: base.columns.get(c) or Column(c, _from_pg(t)) for c, t in cols}
            new.tables[name] = Table(**{**base.__dict__, "columns": merged, "live": True,
                                        "missing_live": [c for c in base.columns if c not in merged]})
        return new

    # ----- search

    def find(self, text: str, k: int = 8) -> list[dict[str, Any]]:
        """Tables most likely to answer `text`, best first, with why they matched."""
        words = [w for w in tokens(text) if w not in STOPWORDS]
        exact = [w for w in re.findall(r"aws_[a-z0-9_]+", text.lower()) if w in self.tables]
        query: dict[str, float] = {}
        for w in words:
            query[w] = max(query.get(w, 0), 1.0)
            for s in SYNONYMS.get(w, []):
                query[_stem(s)] = max(query.get(_stem(s), 0), 0.7)
        wants_metric = any(w in query for w in ("metric", "cpu", "utilization", "memory", "idle"))
        wants_cost = "cost" in query
        scored = []
        for name, (ntok, svc, desc, cols) in self._index.items():
            t = self.tables[name]
            score, why = 0.0, []
            for w, wt in query.items():
                if w in ntok:
                    score += 6 * wt
                    why.append(w)
                elif w in svc:
                    score += 3 * wt
                elif w in desc:
                    score += 1.5 * wt
                elif w in cols:
                    score += 0.8 * wt
            if name in exact:
                score += 100
            if score <= 0:
                continue
            score -= 0.15 * max(len(ntok) - 2, 0)  # prefer the general table over narrow child tables
            if "metric" in ntok and not wants_metric:
                score -= 4
            if t.cost_explorer and not wants_cost:
                score -= 4
            scored.append((score, name, why))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self.brief(n, why) for _, n, why in scored[:k]]

    def brief(self, name: str, why: list[str] | None = None) -> dict[str, Any]:
        t = self.tables[name]
        out: dict[str, Any] = {"table": name, "service": t.service, "description": t.summary or t.description}
        if t.required or t.any_of:
            out["required_filters"] = t.required_text()
        if t.cost_explorer:
            out["note"] = "Cost Explorer: about $0.01 per request; always bound period_start/period_end"
        elif t.time_series:
            out["note"] = "time series: filter on a time range"
        if why:
            out["matched"] = sorted(set(why))
        return out

    def describe(self, name: str, *, examples: bool = True) -> dict[str, Any]:
        t = self.tables[name]
        req = set(t.required) | set(t.any_of)
        out: dict[str, Any] = {
            "table": name, "service": t.service, "description": t.summary or t.description,
            "columns": [{"name": c.name, "type": c.pg_type, **({"description": c.description[:240]} if c.description else {}),
                         **({"required_filter": True} if c.name in req else {}),
                         **({"fast_filter": t.filters[c.name]} if c.name in t.filters else {})}
                        for c in t.columns.values() if c.name not in ("_ctx", "sp_ctx")],
        }
        if t.required or t.any_of:
            out["required_filters"] = (f"Every query on {name} MUST have WHERE {t.required_text()}. Valid values are "
                                       "in each column's description.")
        if t.filters:
            out["fast_filters"] = ("Filtering on these columns in WHERE is pushed down to the AWS API (faster, "
                                   f"fewer calls): {', '.join(sorted(t.filters))}")
        if t.cost_explorer:
            out["cost_note"] = ("Cost Explorer bills about $0.01 per API request: always filter period_start / "
                                "period_end to the range you need")
        elif t.time_series:
            out["time_note"] = "Time series: always filter on a time range (e.g. timestamp >= now() - interval '7 days')"
        if t.get_keys:
            out["lookup_keys"] = t.get_keys
        if examples and t.examples:
            out["examples"] = t.examples
        if t.live:
            out["schema"] = "columns confirmed against the installed plugin"
        return out

    def schema(self) -> dict[str, dict[str, Column]]:
        return {n: t.columns for n, t in self.tables.items()}


def _from_pg(pg: str) -> str:
    p = (pg or "").lower()
    return ("JSON" if "json" in p else "BOOL" if p == "boolean" else "INT" if "int" in p else
            "DOUBLE" if p in ("double precision", "real", "numeric") else "TIMESTAMP" if "timestamp" in p else
            "IPADDR" if p == "inet" else "CIDR" if p == "cidr" else "STRING")


@cache
def load() -> Catalog:
    data = resources.files("spendlens.ai").joinpath("data", "aws_catalog.json.gz").read_bytes()
    return Catalog(json.loads(gzip.decompress(data)))
