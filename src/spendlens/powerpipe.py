"""Run benchmarks from the bundled (or a plugin's) Powerpipe mod and normalise results into Findings.

How a run is wired (verified against Powerpipe v1.6):
  * the mod declares `variable "database" { type = connection.steampipe }`;
  * spendlens writes a temp config dir with `connection "steampipe" "spendlens" {...}` built from
    finops.yaml and passes it via --config-path (the user's ~/.powerpipe/config is kept after it);
  * all mod variables go through a generated .ppvars file (`database = connection.steampipe.spendlens`);
  * `benchmark run a b` prints one JSON document per benchmark, concatenated;
  * exit code is non-zero whenever there are alarms, so success is judged by parseable JSON output.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Config
from .models import Finding, Severity, Status
from .steampipe import connection_params

CONNECTION_NAME = "spendlens"
_VAR_RE = re.compile(r'^\s*variable\s+"([A-Za-z0-9_]+)"', re.MULTILINE)


class PowerpipeError(RuntimeError):
    pass


def bundled_mod_dir() -> Path:
    return Path(str(resources.files("spendlens").joinpath("mod")))


# ------------------------------------------------------------------ HCL encoding


def _hcl_str(s: str) -> str:
    # JSON string escaping is valid HCL; additionally neutralise template sequences.
    return json.dumps(s).replace("${", "$${").replace("%{", "%%{")


def hcl_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _hcl_str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(hcl_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_hcl_str(str(k))} = {hcl_value(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"Cannot encode {type(v).__name__} as HCL")


def declared_variables(mod_dir: Path) -> set[str]:
    names: set[str] = set()
    for f in mod_dir.rglob("*.pp"):
        names.update(_VAR_RE.findall(f.read_text()))
    return names


def var_file_text(variables: dict[str, Any], declared: set[str]) -> str:
    lines = [f"database = connection.steampipe.{CONNECTION_NAME}"] if "database" in declared else []
    lines += [f"{k} = {hcl_value(v)}" for k, v in sorted(variables.items()) if k in declared and k != "database"]
    return "\n".join(lines) + "\n"


def connection_text(params: dict[str, Any]) -> str:
    body = [
        f"  host     = {_hcl_str(str(params['host']))}",
        f"  port     = {int(params['port'])}",
        f"  db       = {_hcl_str(str(params['db']))}",
        f"  username = {_hcl_str(str(params['username']))}",
    ]
    if params.get("password"):
        body.append(f"  password = {_hcl_str(str(params['password']))}")
    return f'connection "steampipe" "{CONNECTION_NAME}" {{\n' + "\n".join(body) + "\n}\n"


# ------------------------------------------------------------------ result parsing


def iter_json_documents(text: str) -> Iterator[dict[str, Any]]:
    dec = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] != "{":
            i += 1
        if i >= n:
            break
        doc, i = dec.raw_decode(text, i)
        yield doc


def _walk_controls(group: dict[str, Any], path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], dict]]:
    here = path + ((group.get("title") or group.get("group_id") or ""),)
    for c in group.get("controls") or []:
        yield here, c
    for g in group.get("groups") or []:
        yield from _walk_controls(g, here)


def _severity(s: str | None) -> Severity:
    try:
        return Severity((s or "low").lower())
    except ValueError:
        return Severity.LOW


def parse_results(doc: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()  # a control can appear under several benchmarks
    for path, ctl in _walk_controls(doc, ()):
        control = (ctl.get("control_id") or "").removeprefix("control.").split(".")[-1]
        tags = ctl.get("tags") or {}
        common = {
            "control": control,
            "title": ctl.get("title") or "",
            "check": tags.get("spendlens_check"),
            "resource_type": tags.get("resource_type"),
            "severity": _severity(ctl.get("severity")),
        }
        if ctl.get("run_error"):
            if (control, "-") in seen:
                continue
            seen.add((control, "-"))
            findings.append(Finding(**common, resource="-", status=Status.ERROR, reason=ctl["run_error"],
                                    dimensions={"benchmark": " / ".join(p for p in path if p)}))
            continue
        for r in ctl.get("results") or []:
            resource = r.get("resource") or "-"
            if (control, resource) in seen:
                continue
            seen.add((control, resource))
            dims = {d["key"]: d.get("value") for d in r.get("dimensions") or []}
            account = dims.pop("account_id", None)
            region = dims.pop("region", None)
            try:
                status = Status(r.get("status", "error"))
            except ValueError:
                status = Status.ERROR
            findings.append(Finding(**common, resource=resource, account_id=account, region=region,
                                    status=status, reason=r.get("reason") or "", dimensions=dims))
    return findings


# ------------------------------------------------------------------ runner


@dataclass
class RunResult:
    findings: list[Finding]
    documents: list[dict[str, Any]] = field(default_factory=list)
    stderr: str = ""


@dataclass
class PowerpipeRunner:
    cfg: Config
    mod_dir: Path = field(default_factory=bundled_mod_dir)

    def executable(self) -> str:
        exe = shutil.which(self.cfg.powerpipe.binary)
        if not exe:
            raise PowerpipeError("powerpipe not found on PATH. Install: brew install turbot/tap/powerpipe")
        return exe

    def command(self, targets: list[str], workdir: Path, where: str | None = None) -> list[str]:
        user_cfg = Path.home() / ".powerpipe" / "config"
        cmd = [
            self.executable(), "benchmark", "run", *targets,
            "--mod-location", str(self.mod_dir),
            "--config-path", f"{workdir}:{user_cfg}",
            "--var-file", str(workdir / "spendlens.ppvars"),
            "--output", "json",
            "--max-parallel", str(self.cfg.powerpipe.max_parallel),
        ]
        if self.cfg.steampipe.connection:
            cmd += ["--search-path-prefix", self.cfg.steampipe.connection]
        if where:
            cmd += ["--where", where]
        return cmd + list(self.cfg.powerpipe.extra_args)

    def prepare(self, workdir: Path) -> None:
        declared = declared_variables(self.mod_dir)
        (workdir / "spendlens.ppvars").write_text(var_file_text(self.cfg.to_powerpipe_vars(), declared))
        conn = workdir / f"{CONNECTION_NAME}.ppc"
        conn.write_text(connection_text(connection_params(self.cfg.steampipe)))
        conn.chmod(0o600)

    def run(self, targets: list[str], *, where: str | None = None) -> RunResult:
        mod = self._mod_name()
        targets = [t if t.startswith(f"{mod}.") else f"{mod}.benchmark.{t}" for t in targets]
        with tempfile.TemporaryDirectory(prefix="spendlens-pp-") as tmp:
            workdir = Path(tmp)
            os.chmod(workdir, 0o700)
            self.prepare(workdir)
            try:
                p = subprocess.run(
                    self.command(targets, workdir, where), capture_output=True, text=True,
                    timeout=self.cfg.powerpipe.timeout_s, check=False,
                )
            except subprocess.TimeoutExpired as e:
                raise PowerpipeError(f"powerpipe timed out after {self.cfg.powerpipe.timeout_s}s") from e
        try:
            docs = list(iter_json_documents(p.stdout))
        except json.JSONDecodeError:
            docs = []
        if not docs:
            msg = (p.stderr or p.stdout).strip().splitlines()
            raise PowerpipeError("powerpipe failed: " + ("\n".join(msg[:8]) or f"exit code {p.returncode}"))
        findings = [f for d in docs for f in parse_results(d)]
        return RunResult(findings, docs, p.stderr)

    def _mod_name(self) -> str:
        m = re.search(r'^\s*mod\s+"([^"]+)"', (self.mod_dir / "mod.pp").read_text(), re.MULTILINE)
        return m.group(1) if m else "spendlens"
