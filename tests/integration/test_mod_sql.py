"""Offline verification of the bundled mod's SQL: Postgres grammar, parameter count, tables and columns.

Uses Powerpipe to resolve each control's SQL (HCL interpolation of dimensions), pglast (libpg_query, the real
Postgres parser) for syntax, and tests/fixtures/aws_schema.json (snapshot of steampipe-plugin-aws source) for
table/column names. Catches typos without AWS or a database.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

pglast = pytest.importorskip("pglast")
from sqlcheck import SCHEMA, check_sql

from spendlens.powerpipe import bundled_mod_dir

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not shutil.which("powerpipe"), reason="powerpipe not installed")




@cache
def controls() -> list[dict]:
    r = subprocess.run(["powerpipe", "control", "list", "--mod-location", str(bundled_mod_dir()), "--output", "json"],
                       capture_output=True, text=True, timeout=180, check=False)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)



@cache
def benchmarks() -> dict[str, list[str]]:
    r = subprocess.run(["powerpipe", "benchmark", "list", "--mod-location", str(bundled_mod_dir()), "--output", "json"],
                       capture_output=True, text=True, timeout=180, check=False)
    assert r.returncode == 0, r.stderr
    out = {}
    for b in json.loads(r.stdout):
        out[b["resource_name"]] = [c.split(".")[-1] for c in (b.get("children") or [])]
    return out



def _finops_controls():
    return [c for c in controls() if not c["resource_name"].startswith("tags_")]



def test_schema_snapshot_covers_mod():
    used = set()
    for f in bundled_mod_dir().rglob("*.pp"):
        used.update(re.findall(r"\b(aws_[a-z0-9_]+)\b", f.read_text()))
    assert used <= SCHEMA.keys(), sorted(used - SCHEMA.keys())



def test_checker_catches_typos():
    assert check_sql("select arn as resource, instance_typ from aws_ec2_instance") == ["unknown column instance_typ"]
    assert check_sql("select i.bogus from aws_ec2_instance as i") == ["i.bogus: no column bogus in aws_ec2_instance"]
    assert check_sql("select 1 from aws_nope") == ["unknown table aws_nope"]
    with pytest.raises(pglast.parser.ParseError):
        check_sql("select from where")



@pytest.mark.parametrize("name", [c["resource_name"] for c in _finops_controls()] if shutil.which("powerpipe") else [])
def test_control_sql_is_valid(name):
    ctl = next(c for c in controls() if c["resource_name"] == name)
    sql = ctl["sql"]
    assert check_sql(sql) == []
    used_params = {int(m) for m in re.findall(r"\$(\d+)", sql)}
    declared = len(ctl.get("params") or [])
    assert used_params == set(range(1, declared + 1)), f"params used {sorted(used_params)} but {declared} declared"
    for col in ("resource", "status", "reason"):
        assert re.search(rf"\bas {col}\b", sql), f"missing '{col}' column"



def test_tag_control_sql_is_valid():
    # one representative per tag check (all 70 share the same template)
    for c in controls():
        if c["resource_name"].endswith("_s3_bucket") and c["resource_name"].startswith("tags_"):
            assert check_sql(c["sql"]) == [], c["resource_name"]



def test_every_finops_control_is_tagged_and_in_its_benchmark():
    b = benchmarks()
    for c in _finops_controls():
        tags = c.get("tags") or {}
        check = tags.get("spendlens_check")
        assert check in ("rightsize", "waste"), c["resource_name"]
        assert tags.get("service") and tags.get("resource_type"), c["resource_name"]
        assert c["resource_name"] in b[check], f"{c['resource_name']} missing from benchmark.{check}"
        other = "waste" if check == "rightsize" else "rightsize"
        assert c["resource_name"] not in b[other]



def test_mod_variables_generated_from_config():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_mod_variables.py"), "--check"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout



def test_control_docs_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_control_docs.py"), "--check"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout

