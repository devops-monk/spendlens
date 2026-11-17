#!/usr/bin/env python3
"""Build the AWS table catalog the AI assistant uses to write correct Steampipe queries.

    python scripts/build_aws_catalog.py ../steampipe-plugin-aws            # tag = the pinned plugin version
    python scripts/build_aws_catalog.py ../steampipe-plugin-aws --ref v1.36.0

Reads the plugin source at a git ref with `git cat-file` (your checkout is never changed) and writes
src/spendlens/ai/data/aws_catalog.json.gz. Per table:
  * service folder, description (Go + docs), first sentence of the docs usage guide;
  * every column with its Steampipe type and description, including the shared helper columns;
  * key columns: required ones (Require: Required / SingleColumn / AllColumns, AnyOf groups), and the optional
    filter columns with their operators (these are pushed down to the AWS API, so filtering on them is fast);
  * up to three Postgres examples from docs/tables/<table>.md;
  * flags: cost_explorer ($0.01 per request) and time_series (needs a time range to stay small).

Re-run after bumping AWS_PLUGIN_VERSION in install.sh / Dockerfile.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "spendlens" / "ai" / "data" / "aws_catalog.json.gz"
PINNED = re.search(r'AWS_PLUGIN_VERSION="([^"]+)"', (ROOT / "install.sh").read_text()).group(1)

STEAMPIPE_COLUMNS = [  # added by Steampipe to every table
    {"name": "sp_connection_name", "type": "STRING", "description": "Steampipe connection name."},
    {"name": "sp_ctx", "type": "JSON", "description": "Steampipe context in JSON form."},
    {"name": "_ctx", "type": "JSON", "description": "Steampipe context in JSON form."},
]
EXAMPLES_PER_TABLE = 3
EXAMPLE_MAX_CHARS = 900
TIME_SERIES = re.compile(r"^aws_(cloudwatch_metric|.*_metric_|cloudtrail_trail_event|cloudwatch_log_event|"
                         r"vpc_flow_log_event|cost_|ec2_instance_metric|rds_db_instance_metric|ebs_volume_metric)")


# ------------------------------------------------------------------ git access (read-only)


class Source:
    def __init__(self, repo: Path, ref: str):
        self.repo, self.ref = repo, ref
        names = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref, "aws", "docs/tables"],
                               capture_output=True, text=True, check=True).stdout.split()
        self.files = self._cat(names)

    def _cat(self, names: list[str]) -> dict[str, str]:
        spec = "".join(f"{self.ref}:{n}\n" for n in names).encode()
        out = subprocess.run(["git", "-C", str(self.repo), "cat-file", "--batch"], input=spec,
                             capture_output=True, check=True).stdout
        files, pos = {}, 0
        for n in names:
            nl = out.index(b"\n", pos)
            size = int(out[pos:nl].split()[2])
            text = out[nl + 1: nl + 1 + size].decode("utf-8", "replace")
            files[n] = strip_comments(text) if n.endswith(".go") else text
            pos = nl + 1 + size + 1
        return files

    def go(self) -> str:
        return "\n".join(t for n, t in self.files.items() if n.startswith("aws/") and n.endswith(".go")
                         and not n.endswith("_test.go"))


# ------------------------------------------------------------------ tiny Go literal helpers


def strip_comments(go: str) -> str:
    """Drop whole-line // comments: commented-out column blocks would otherwise parse as columns."""
    return re.sub(r"^[ \t]*//.*\n", "", go, flags=re.MULTILINE)


def matching(text: str, start: int, open_: str = "{", close: str = "}") -> int:
    """Index just past the bracket matching text[start] (which must be open_); skips strings and comments."""
    depth, i, n = 0, start, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "`":
            i = text.index("`", i + 1)
        elif text.startswith("//", i):
            i = text.find("\n", i)
            if i < 0:
                return n
            continue
        elif c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def expression(text: str, start: int, stop_at_newline: bool = False) -> str:
    """The Go expression starting at `start`, up to the first top-level ',' or closing bracket (or line end)."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "`":
            i = text.index("`", i + 1)
        elif c in "({[":
            depth += 1
        elif c in ")}]":
            if depth == 0:
                break
            depth -= 1
        elif (c == "," or (c == "\n" and stop_at_newline)) and depth == 0:
            break
        i += 1
    return text[start:i]


def go_string(expr: str) -> str:
    """Concatenated Go string literals ("a" + "b", `raw`) -> text."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"|`([^`]*)`', expr)
    return "".join(json.loads(f'"{a}"') if a or not b else b for a, b in parts).strip()


def elements(literal: str) -> list[str]:
    """Top-level {...} elements of a slice literal body."""
    out, i = [], 0
    while (j := literal.find("{", i)) >= 0:
        k = matching(literal, j)
        out.append(literal[j + 1: k - 1])
        i = k
    return out


def field(block: str, name: str) -> str | None:
    """Value expression of `Name: value` at the top level of a struct literal body."""
    depth, i = 0, 0
    pat = re.compile(rf"\b{name}\s*:")
    while i < len(block):
        c = block[i]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        elif c == '"':
            i += 1
            while block[i] != '"':
                i += 2 if block[i] == "\\" else 1
        elif c == "`":
            i = block.index("`", i + 1)
        elif depth == 0 and (m := pat.match(block, i)) and (i == 0 or not block[i - 1].isalnum()):
            return expression(block, m.end()).strip()
        i += 1
    return None


# ------------------------------------------------------------------ resolving columns / key columns


class Package:
    def __init__(self, go: str):
        self.go = go
        self.funcs: dict[str, str] = {}
        self.col_funcs: set[str] = set()  # functions returning []*plugin.Column
        for m in re.finditer(r"^func (\w+)\([^)]*\)([^{\n]*)\{", go, re.MULTILINE):
            self.funcs[m.group(1)] = go[m.end() - 1: matching(go, m.end() - 1)]
            if "[]*plugin.Column" in m.group(2):
                self.col_funcs.add(m.group(1))
        self.vars: dict[str, str] = {}
        for m in re.finditer(r"^var (\w+)\s*=\s*", go, re.MULTILINE):
            self.vars[m.group(1)] = expression(go, m.end(), stop_at_newline=True)

    def columns(self, expr: str, seen: frozenset[str] = frozenset()) -> list[dict]:
        cols: list[dict] = []
        i = 0
        while i < len(expr):
            m = re.compile(r"\[\]\*plugin\.Column\s*\{|(\w+)\s*\(|\b(\w+)\b").search(expr, i)
            if not m:
                break
            if m.group(0).startswith("[]"):
                end = matching(expr, m.end() - 1)
                for el in elements(expr[m.end() - 1 + 1: end - 1]):
                    if (name := field(el, "Name")) and (typ := field(el, "Type")):
                        cols.append({"name": go_string(name), "type": typ.split("ColumnType_")[-1],
                                     "description": go_string(field(el, "Description") or '""')})
                i = end
            elif (fn := m.group(1)) and fn in self.col_funcs and fn not in seen:
                cols += self.columns(self.funcs[fn], seen | {fn})
                i = m.end()
            elif (var := m.group(2)) and var in self.vars and var not in seen and "plugin.Column" in self.vars[var]:
                cols += self.columns(self.vars[var], seen | {var})
                i = m.end()
            else:
                i = m.end()
        return cols

    def key_columns(self, expr: str | None, seen: frozenset[str] = frozenset()) -> list[dict]:
        """[{name, require: required|optional|any_of, operators}] from a KeyColumns expression."""
        if not expr:
            return []
        out: list[dict] = []
        for m in re.finditer(r"plugin\.(SingleColumn|AllColumns|AnyColumn|OptionalColumns)\(", expr):
            arg = expr[m.end(): matching(expr, m.end() - 1, "(", ")") - 1]
            names = [go_string(s) for s in re.findall(r'"[^"]*"', arg)]
            req = {"SingleColumn": "required", "AllColumns": "required", "AnyColumn": "any_of",
                   "OptionalColumns": "optional"}[m.group(1)]
            out += [{"name": n, "require": req, "operators": ["="]} for n in names]
        for m in re.finditer(r"\[\]\*plugin\.KeyColumn\s*\{|plugin\.KeyColumnSlice\s*\{", expr):
            body = expr[m.end(): matching(expr, m.end() - 1) - 1]
            for el in elements(body):
                name = field(el, "Name")
                if not name:
                    continue
                req = (field(el, "Require") or "plugin.Required").split(".")[-1]
                ops = re.findall(r'"([^"]+)"', field(el, "Operators") or "") or ["="]
                out.append({"name": go_string(name),
                            "require": {"Required": "required", "Optional": "optional", "AnyOf": "any_of"}.get(req, "optional"),
                            "operators": ops})
        for m in re.finditer(r"(\w+)\(\)", expr):
            fn = m.group(1)
            if fn in self.funcs and fn not in seen and "KeyColumn" in self.funcs[fn]:
                out += self.key_columns(self.funcs[fn], seen | {fn})
        return out


def config_block(src: str, kind: str) -> str | None:
    m = re.search(rf"\b{kind}:\s*&plugin\.{kind}Config\s*\{{", src)
    return src[m.end(): matching(src, m.end() - 1) - 1] if m else None


# ------------------------------------------------------------------ docs


def docs(md: str | None) -> dict:
    if not md:
        return {}
    front = {}
    if md.startswith("---"):
        head, _, md = md[3:].partition("\n---")
        for line in head.splitlines():
            k, _, v = line.partition(":")
            if v.strip():
                front[k.strip()] = v.strip().strip('"')
    guide = ""
    if (m := re.search(r"## Table Usage Guide\s+(.+?)(?:\n\n|\n#)", md, re.DOTALL)):
        guide = re.split(r"(?<=\.)\s", m.group(1).strip(), maxsplit=1)[0]
    examples = []
    for m in re.finditer(r"^### (.+?)\n(.*?)```sql\+postgres\n(.*?)```", md, re.DOTALL | re.MULTILINE):
        sql = m.group(3).strip()
        if len(sql) <= EXAMPLE_MAX_CHARS:
            examples.append({"title": m.group(1).strip(), "sql": sql})
    return {"folder": front.get("folder", ""), "doc_description": front.get("description", ""), "guide": guide,
            "examples": examples}


# ------------------------------------------------------------------ build


def build(repo: Path, ref: str) -> dict:
    src = Source(repo, ref)
    pkg = Package(src.go())
    tables = {}
    for path, text in sorted(src.files.items()):
        if not re.fullmatch(r"aws/table_aws_\w+\.go", path) or path.endswith("_test.go"):
            continue
        m = re.search(r"return &plugin\.Table\{", text)
        if not m:
            continue
        body = text[m.end(): matching(text, m.end() - 1) - 1]
        name = go_string(field(body, "Name") or '""')
        cols_expr = field(body, "Columns") or ""
        local = Package(text)  # file-local helpers first, then the package
        local.funcs = {**pkg.funcs, **local.funcs}
        local.vars = {**pkg.vars, **local.vars}
        local.col_funcs |= pkg.col_funcs
        cols, seen = [], set()
        for c in local.columns(cols_expr) + STEAMPIPE_COLUMNS:
            if c["name"] not in seen:
                seen.add(c["name"])
                cols.append(c)
        lst, get = config_block(body, "List"), config_block(body, "Get")
        list_keys = local.key_columns(field(lst, "KeyColumns") if lst else None)
        get_keys = local.key_columns(field(get, "KeyColumns") if get else None)
        if not lst:  # get-only table: its key columns are required
            list_keys = [{**k, "require": "required" if k["require"] != "any_of" else "any_of"} for k in get_keys]
        d = docs(src.files.get(f"docs/tables/{name}.md"))
        tables[name] = {
            "service": d.get("folder") or name.split("_")[1].upper(),
            "description": go_string(field(body, "Description") or '""'),
            "summary": d.get("doc_description", ""),
            "guide": d.get("guide", ""),
            "columns": [[c["name"], c["type"], c["description"]] for c in cols],
            "required": sorted({k["name"] for k in list_keys if k["require"] == "required"}),
            "any_of": sorted({k["name"] for k in list_keys if k["require"] == "any_of"}),
            "filters": {k["name"]: k["operators"] for k in list_keys if k["require"] == "optional"},
            "get_keys": sorted({k["name"] for k in get_keys}),
            "examples": d.get("examples", [])[:EXAMPLES_PER_TABLE],
            "cost_explorer": name.startswith(("aws_cost_", "aws_costoptimizationhub")) and "forecast" not in name
                             or name.startswith("aws_cost_forecast"),
            "time_series": bool(TIME_SERIES.match(name)),
        }
    return {"plugin": "turbot/aws", "version": ref.lstrip("v"), "tables": tables}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", type=Path, help="Path to a steampipe-plugin-aws git clone")
    ap.add_argument("--ref", default=f"v{PINNED}", help=f"Git tag or commit (default: v{PINNED}, the pinned version)")
    a = ap.parse_args()
    cat = build(a.repo.expanduser(), a.ref)
    t = cat["tables"]
    empty = [n for n, x in t.items() if len(x["columns"]) <= len(STEAMPIPE_COLUMNS)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(cat, separators=(",", ":"), sort_keys=True).encode()
    OUT.write_bytes(gzip.compress(raw, 9, mtime=0))
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(t)} tables, {sum(len(x['columns']) for x in t.values())} columns, "
          f"{sum(len(x['examples']) for x in t.values())} examples, {OUT.stat().st_size // 1024} KiB "
          f"(plugin {a.ref})")
    if empty:
        print(f"WARNING: no columns parsed for {len(empty)} table(s): {', '.join(empty[:10])}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
