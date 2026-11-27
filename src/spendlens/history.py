"""Local SQLite store of past runs (findings + cost snapshots), plus the bundled demo database.

* history DB: `history.path` (default ~/.local/share/spendlens/spendlens.db). In CI, restore/save this
  file between jobs (S3 or pipeline cache) to get trends across pipeline runs.
* demo DB: shipped inside the package (spendlens/data/demo.db), read-only, used by `--source demo`.

Schema is versioned through PRAGMA user_version; migrations are additive.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, Self

from .models import Finding

SCHEMA_VERSION = 3
SCHEMA = """
create table if not exists runs (
  id            integer primary key autoincrement,
  feature       text not null,           -- tags, rightsize, waste, cost_diff ...
  started_at    text not null,           -- ISO-8601 UTC
  account_id    text,
  caller_arn    text,
  params        text not null default '{}',  -- JSON: effective parameters for reproducibility
  meta          text not null default '{}'   -- JSON: CI job id, git sha, target list ...
);
create table if not exists findings (
  run_id              integer not null references runs(id) on delete cascade,
  control             text not null,
  title               text,
  "check"             text,
  service             text,
  resource_type       text,
  resource            text not null,
  account_id          text,
  region              text,
  status              text not null,
  reason              text,
  severity            text,
  est_monthly_saving  real,
  tags                text,              -- JSON
  dimensions          text               -- JSON
);
create index if not exists findings_run on findings(run_id);
create index if not exists findings_resource on findings(resource, control);
create table if not exists cost_rows (
  run_id    integer not null references runs(id) on delete cascade,
  kind      text not null,               -- monthly, daily, forecast ...
  period    text not null,               -- 2026-10 / 2026-10-31
  dimension text not null,               -- service, account, region, tag:Team ...
  grp       text not null,
  amount    real not null,
  currency  text not null default 'USD'
);
create index if not exists cost_rows_run on cost_rows(run_id);
create table if not exists graph_nodes (
  run_id        integer not null references runs(id) on delete cascade,
  id            text not null,             -- ARN where the resource has one, else its AWS id
  type          text not null,             -- ec2_instance, ebs_volume, ebs_snapshot ...
  name          text,
  account_id    text,
  region        text,
  state         text,
  created       text,                      -- ISO-8601
  monthly_cost  real,                      -- estimate (USD/month) or null
  cost_basis    text,                      -- estimate | none
  tags          text,                      -- JSON
  attrs         text                       -- JSON: size_gb, instance_type, stopped_since ...
);
create index if not exists graph_nodes_run on graph_nodes(run_id, id);
create table if not exists graph_edges (
  run_id   integer not null references runs(id) on delete cascade,
  from_id  text not null,
  to_id    text not null,
  label    text not null,                  -- mounts, eni, snapshot of, backed by ...
  kind     text not null                   -- uses (from needs to) | contains (to needs from) | ref
);
create index if not exists graph_edges_run on graph_edges(run_id);
"""

# Additive migrations: version -> statements to go from version-1 to version.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: ("alter table findings add column service text",),
    3: tuple(f"create {x.strip()}" for x in SCHEMA.split("create ") if "graph_" in x),
}

_FINDING_COLS = ("control", "title", "check", "service", "resource_type", "resource", "account_id", "region", "status",
                 "reason", "severity", "est_monthly_saving", "tags", "dimensions")


def default_history_path() -> Path:
    return Path.home() / ".local" / "share" / "spendlens" / "spendlens.db"


def demo_db_path() -> Path:
    return Path(str(resources.files("spendlens").joinpath("data", "demo.db")))


@dataclass
class Run:
    id: int
    feature: str
    started_at: str
    account_id: str | None
    caller_arn: str | None
    params: dict[str, Any]
    meta: dict[str, Any]


class HistoryStore:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = Path(path).expanduser()
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(f"History database not found: {self.path}")
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path)
            from .privacy import private

            private(self.path)  # findings, costs and the resource graph: your user only
            self._migrate()
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("pragma foreign_keys = on")

    def _migrate(self) -> None:
        version = self.conn.execute("pragma user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version == 0:
            self.conn.executescript(SCHEMA)
        else:
            for v in range(version + 1, SCHEMA_VERSION + 1):
                for stmt in MIGRATIONS.get(v, ()):
                    self.conn.execute(stmt)
        self.conn.execute(f"pragma user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ writes

    def save_run(
        self,
        feature: str,
        findings: Iterable[Finding] = (),
        *,
        cost_rows: Iterable[dict[str, Any]] = (),
        account_id: str | None = None,
        caller_arn: str | None = None,
        params: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> int:
        ts = (started_at or datetime.now(UTC)).isoformat(timespec="seconds")
        with self.conn:
            cur = self.conn.execute(
                "insert into runs(feature, started_at, account_id, caller_arn, params, meta) values (?,?,?,?,?,?)",
                (feature, ts, account_id, caller_arn, json.dumps(params or {}, default=str),
                 json.dumps(meta or {}, default=str)),
            )
            run_id = int(cur.lastrowid)
            self.conn.executemany(
                f"insert into findings(run_id, {', '.join(_q(c) for c in _FINDING_COLS)}) "
                f"values (?{', ?' * len(_FINDING_COLS)})",
                [(run_id, *_finding_row(f)) for f in findings],
            )
            self.conn.executemany(
                "insert into cost_rows(run_id, kind, period, dimension, grp, amount, currency) values (?,?,?,?,?,?,?)",
                [(run_id, r["kind"], r["period"], r["dimension"], r["grp"], float(r["amount"]),
                  r.get("currency", "USD")) for r in cost_rows],
            )
        return run_id

    def save_graph(self, run_id: int, nodes: Iterable[dict[str, Any]], edges: Iterable[dict[str, Any]]) -> None:
        """Nodes/edges of a `graph` run (see spendlens.graph)."""
        with self.conn:
            self.conn.executemany(
                "insert into graph_nodes(run_id, id, type, name, account_id, region, state, created, monthly_cost, "
                "cost_basis, tags, attrs) values (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, n["id"], n["type"], n.get("name"), n.get("account_id"), n.get("region"), n.get("state"),
                  n.get("created"), n.get("monthly_cost"), n.get("cost_basis") or "none",
                  json.dumps(n.get("tags") or {}, default=str), json.dumps(n.get("attrs") or {}, default=str))
                 for n in nodes])
            self.conn.executemany(
                "insert into graph_edges(run_id, from_id, to_id, label, kind) values (?,?,?,?,?)",
                [(run_id, e["from_id"], e["to_id"], e["label"], e["kind"]) for e in edges])

    def graph(self, run_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return read_graph(self.conn, run_id)

    def prune(self, keep_runs_per_feature: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                """delete from runs where id in (
                     select id from (
                       select id, row_number() over (partition by feature order by id desc) as rn from runs
                     ) where rn > ?)""",
                (keep_runs_per_feature,),
            )
        return cur.rowcount

    # ------------------------------------------------------------ reads

    def runs(self, feature: str | None = None, limit: int = 20) -> list[Run]:
        sql = "select * from runs" + (" where feature = ?" if feature else "") + " order by id desc limit ?"
        rows = self.conn.execute(sql, ((feature, limit) if feature else (limit,))).fetchall()
        return [_run(r) for r in rows]

    def latest_run(self, feature: str, offset: int = 0) -> Run | None:
        r = self.conn.execute(
            "select * from runs where feature = ? order by id desc limit 1 offset ?", (feature, offset)
        ).fetchone()
        return _run(r) if r else None

    def get_run(self, run_id: int) -> Run | None:
        r = self.conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
        return _run(r) if r else None

    def findings(self, run_id: int) -> list[Finding]:
        return read_findings(self.conn, run_id)

    def cost_rows(self, run_id: int, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "select kind, period, dimension, grp, amount, currency from cost_rows where run_id = ?"
        args: tuple = (run_id,)
        if kind:
            sql += " and kind = ?"
            args += (kind,)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]


def read_graph(conn: sqlite3.Connection, run_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Nodes and edges of a `graph` run, from any connection (also the AI's read-only one)."""
    cols = ("id", "type", "name", "account_id", "region", "state", "created", "monthly_cost", "cost_basis", "tags",
            "attrs")
    nodes = []
    for r in conn.execute(f"select {', '.join(cols)} from graph_nodes where run_id = ?", (run_id,)).fetchall():
        d = dict(zip(cols, tuple(r), strict=True))
        d["tags"] = json.loads(d["tags"] or "{}")
        d["attrs"] = json.loads(d["attrs"] or "{}")
        nodes.append(d)
    edges = [dict(zip(("from_id", "to_id", "label", "kind"), tuple(r), strict=True)) for r in conn.execute(
        "select from_id, to_id, label, kind from graph_edges where run_id = ?", (run_id,)).fetchall()]
    return nodes, edges


def read_findings(conn: sqlite3.Connection, run_id: int) -> list[Finding]:
    rows = conn.execute(
        f"select {', '.join(_q(c) for c in _FINDING_COLS)} from findings where run_id = ?", (run_id,)
    ).fetchall()
    return [_finding(r) for r in rows]


def diff_findings(old: list[Finding], new: list[Finding]) -> list[dict[str, Any]]:
    """What changed between two runs: new alarms, resolved alarms, status changes."""
    before = {(f.control, f.resource): f for f in old}
    after = {(f.control, f.resource): f for f in new}
    rows = []
    for key in sorted(before.keys() | after.keys()):
        a, b = before.get(key), after.get(key)
        sa = a.status.value if a else None
        sb = b.status.value if b else None
        if sa == sb:
            continue
        if b is None:
            change = "resource_gone"
        elif sb == "alarm":
            change = "new_alarm"
        elif sa == "alarm":
            change = "resolved"
        elif a is None:
            change = "new_resource"
        else:
            change = "changed"
        f = b or a
        assert f is not None
        rows.append({"change": change, "control": f.control, "resource": f.resource, "before": sa, "after": sb,
                     "reason": f.reason, "account_id": f.account_id, "region": f.region})
    order = {"new_alarm": 0, "resolved": 1, "resource_gone": 2, "new_resource": 3, "changed": 4}
    rows.sort(key=lambda r: order[r["change"]])
    return rows


# ------------------------------------------------------------ helpers


def _q(col: str) -> str:
    return f'"{col}"'


def _finding_row(f: Finding) -> tuple:
    d = f.model_dump(mode="json")
    return tuple(json.dumps(d[c]) if c in ("tags", "dimensions") else d[c] for c in _FINDING_COLS)


def _finding(r: sqlite3.Row) -> Finding:
    d = dict(r)
    for c in ("tags", "dimensions"):
        d[c] = json.loads(d[c]) if d[c] else {}
    return Finding.model_validate({k: v for k, v in d.items() if v is not None or k in ("tags", "dimensions")})


def _run(r: sqlite3.Row) -> Run:
    return Run(r["id"], r["feature"], r["started_at"], r["account_id"], r["caller_arn"],
               json.loads(r["params"] or "{}"), json.loads(r["meta"] or "{}"))


def open_store(path: Path | None = None, *, read_only: bool = False) -> HistoryStore:
    return HistoryStore(path or default_history_path(), read_only=read_only)

