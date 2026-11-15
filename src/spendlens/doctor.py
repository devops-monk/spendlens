"""Environment checks: binaries, plugin, AWS identity, Steampipe service, Cost Explorer access."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from .config import Config
from .steampipe import SteampipeClient, SteampipeError


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""
    fix: str = ""


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout or p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def check_python() -> Check:
    ok = sys.version_info >= (3, 11)
    return Check("python", "ok" if ok else "fail", sys.version.split()[0], "" if ok else "Use Python 3.11+")


def check_binary(name: str, fix: str) -> Check:
    exe = shutil.which(name)
    if not exe:
        return Check(name, "fail", "not found on PATH", fix)
    _, out = _run([exe, "--version"])
    return Check(name, "ok", out.splitlines()[0] if out else exe)


def check_aws_plugin() -> Check:
    exe = shutil.which("steampipe")
    if not exe:
        return Check("aws plugin", "fail", "steampipe missing", "Install steampipe first")
    code, out = _run([exe, "plugin", "list"])
    if code == 0 and "turbot/aws" in out:
        line = next((ln for ln in out.splitlines() if "turbot/aws" in ln), "")
        return Check("aws plugin", "ok", " ".join(line.replace("|", " ").split()))
    return Check("aws plugin", "fail", "turbot/aws not installed", "steampipe plugin install aws")


def check_aws_identity(profile: str | None) -> Check:
    from .common import sts_command

    cmd = sts_command(profile)
    if not cmd:
        return Check("aws identity", "warn", "aws CLI not found; cannot verify identity", "Install AWS CLI v2")
    code, out = _run(cmd)
    if code != 0:
        return Check(
            "aws identity", "fail", out.splitlines()[-1] if out else "no credentials",
            "Assume a role / `aws sso login` / export AWS_PROFILE",
        )
    ident = json.loads(out)
    return Check("aws identity", "ok", f"{ident.get('Arn')} (account {ident.get('Account')})")


def check_service_and_cost(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    c = SteampipeClient(cfg.steampipe, timeout_s=60)
    try:
        c.query("select 1")
        checks.append(Check("steampipe service", "ok", f"{cfg.steampipe.host}:{cfg.steampipe.port}"))
    except SteampipeError as e:
        checks.append(Check("steampipe service", "fail", str(e).splitlines()[0], "steampipe service start"))
        c.close()
        return checks
    try:
        c.query("select account_id from aws_account limit 1")
        checks.append(Check("aws tables", "ok", "aws_account readable"))
    except SteampipeError as e:
        checks.append(Check("aws tables", "fail", str(e).splitlines()[0], "Check ~/.steampipe/config/aws.spc"))
    try:
        rows = c.query(
            "select sum(unblended_cost_amount)::numeric(14,2) as cost from aws_cost_by_service_monthly "
            "where period_start >= date_trunc('month', now()) - interval '1 month'"
        )
        checks.append(Check("cost explorer", "ok", f"last+current month cost: {rows[0]['cost'] if rows else '?'}"))
    except SteampipeError as e:
        checks.append(Check(
            "cost explorer", "fail", str(e).splitlines()[0],
            "Role needs ce:GetCostAndUsage (and ce:GetCostForecast)",
        ))
    finally:
        c.close()
    return checks


def run_checks(
    cfg: Config, *, skip_service: bool = False, extra: list[Callable[[Config], list[Check]]] | None = None
) -> list[Check]:
    checks = [
        check_python(),
        check_binary("steampipe", "brew install turbot/tap/steampipe"),
        check_binary("powerpipe", "brew install turbot/tap/powerpipe"),
        check_aws_plugin(),
        check_aws_identity(cfg.aws.profile),
    ]
    if not skip_service:
        checks += check_service_and_cost(cfg)
    for fn in extra or []:
        try:
            checks += fn(cfg)
        except Exception as e:  # noqa: BLE001 - a plugin check must not break doctor
            checks.append(Check(getattr(fn, "__name__", "plugin check"), "fail", f"{type(e).__name__}: {e}"))
    return checks
