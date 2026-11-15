#!/usr/bin/env python3
"""Snapshot table -> columns for every aws_* table the bundled mod references, from steampipe-plugin-aws source.

    python scripts/snapshot_aws_schema.py ../steampipe-plugin-aws

Writes tests/fixtures/aws_schema.json, used by tests/test_finops_mod.py to check control SQL offline
(no AWS / database needed). Re-run after upgrading the plugin or adding controls on new tables.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "src" / "spendlens" / "mod"
OUT = ROOT / "tests" / "fixtures" / "aws_schema.json"

COMMON = ["partition", "region", "account_id", "_ctx", "sp_connection_name", "sp_ctx"]
CW_METRIC = ["metric_name", "namespace", "average", "maximum", "minimum", "sample_count", "sum", "unit", "timestamp"]
NAME_RE = re.compile(r'\bName:\s*"([a-z0-9_]+)"')


def table_columns(go_file: Path) -> tuple[str, list[str]]:
    src = go_file.read_text()
    table = NAME_RE.search(src).group(1)  # first Name: is the table name
    idx = src.find("\t\tColumns:")
    if idx < 0:
        idx = src.find("Columns:")
    section = src[idx:]
    end = section.find("\nfunc ")
    section = section[: end if end > 0 else None]
    cols = NAME_RE.findall(section)
    if "cwMetricColumns(" in section:
        cols += CW_METRIC
    return table, sorted(set(cols + COMMON))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src_dir = Path(sys.argv[1]).expanduser() / "aws"
    used = set()
    for f in MOD.rglob("*.pp"):
        used.update(re.findall(r"\b(aws_[a-z0-9_]+)\b", f.read_text()))
    schema: dict[str, list[str]] = {}
    for go in sorted(src_dir.glob("table_aws_*.go")):
        if go.name.endswith("_test.go"):
            continue
        name = go.stem.removeprefix("table_")
        if name not in used:
            continue
        table, cols = table_columns(go)
        schema[table] = cols
    missing = sorted(used - schema.keys())
    version = ""
    try:
        version = subprocess.run(["git", "-C", str(src_dir.parent), "describe", "--tags", "--always"],
                                 capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        pass
    OUT.write_text(json.dumps({"plugin_version": version, "tables": schema}, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(schema)} tables (plugin {version or 'unknown'})")
    if missing:
        print("Referenced but not found in plugin source (typo or non-table):", ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
