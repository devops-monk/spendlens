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


_EXPIRED = ("sso session has expired", "token for", "error loading sso token", "sso/cache", "expiredtoken",
            "the security token included in the request is expired", "token has expired")


def credential_fix(error: str, profile: str | None, default: str) -> str:
    """The fix for an error: an expired login (SSO or temporary keys) is not a missing permission."""
    e = error.lower()
    if any(k in e for k in _EXPIRED):
        return (f"Your AWS login has expired: aws sso login{f' --profile {profile}' if profile else ''} "
                "(or refresh your temporary credentials), then run doctor again")
    if "no credentials" in e or "unable to locate credentials" in e:
        return "No AWS credentials: assume a role, `aws sso login`, or set aws.profile in finops.yaml"
    return default


def check_aws_identity(profile: str | None) -> Check:
    from .common import sts_command

    cmd = sts_command(profile)
    if not cmd:
        return Check("aws identity", "warn", "aws CLI not found; cannot verify identity", "Install AWS CLI v2")
    code, out = _run(cmd)
    if code != 0:
        detail = out.splitlines()[-1] if out else "no credentials"
        return Check("aws identity", "fail", detail,
                     credential_fix(out, profile, "Assume a role / `aws sso login` / export AWS_PROFILE"))
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
        checks.append(Check("aws tables", "fail", str(e).splitlines()[0],
                            credential_fix(str(e), cfg.aws.profile, "Check ~/.steampipe/config/aws.spc")))
    try:
        rows = c.query(
            "select sum(unblended_cost_amount)::numeric(14,2) as cost from aws_cost_by_service_monthly "
            "where period_start >= date_trunc('month', now()) - interval '1 month'"
        )
        checks.append(Check("cost explorer", "ok", f"last+current month cost: {rows[0]['cost'] if rows else '?'}"))
    except SteampipeError as e:
        checks.append(Check(
            "cost explorer", "fail", str(e).splitlines()[0],
            credential_fix(str(e), cfg.aws.profile, "Role needs ce:GetCostAndUsage (and ce:GetCostForecast)"),
        ))
    finally:
        c.close()
    return checks


def check_llm(cfg: Config) -> Check:
    """`spendlens ask` setup, without calling the model (no cost)."""
    import importlib.util
    import os

    llm = cfg.llm
    sdk = "anthropic" if llm.provider in ("anthropic", "bedrock") else "openai"
    if importlib.util.find_spec(sdk) is None or importlib.util.find_spec("sqlglot") is None:
        return Check("ai assistant", "warn", f"{sdk} SDK not installed", "pip install 'spendlens[ai]'")
    detail = f"{llm.provider} · {llm.model}"
    if llm.provider == "bedrock":
        return Check("ai assistant", "ok", detail + " · uses the assumed AWS role (bedrock:InvokeModel)")
    need_url = llm.provider in ("azure", "openai_compatible")
    if need_url and not llm.base_url:
        return Check("ai assistant", "fail", detail + " · llm.base_url not set", "set llm.base_url in finops.yaml")
    default_env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "azure": "AZURE_OPENAI_API_KEY"}
    env = llm.api_key_env or default_env.get(llm.provider)
    if env and not os.environ.get(env) and llm.provider != "anthropic":
        return Check("ai assistant", "warn", detail + f" · {env} not set", f"export {env}=...")
    return Check("ai assistant", "ok", detail)


def check_regions(cfg: Config) -> Check:
    """Which Steampipe connection and AWS regions live commands use (local check, no network)."""
    from .steampipe import is_managed, managed_spc_path

    sp = cfg.steampipe
    if is_managed(sp):
        prof = f" · profile {cfg.aws.profile}" if cfg.aws.profile else ""
        return Check("aws regions", "ok", f"{', '.join(cfg.aws.regions)}{prof} · managed connection "
                                          f"'{sp.connection}' ({managed_spc_path(sp)})")
    note = "aws.regions is not applied: the regions come from that connection's own .spc file"
    if sp.connection == "spendlens":
        note = "aws.regions is not applied to a remote Steampipe (steampipe.dsn/host): configure regions there"
    return Check("aws regions", "warn" if cfg.param_source("aws.regions") != "default" else "ok",
                 f"connection '{sp.connection}' · {note}", "")


def check_tailpipe(cfg: Config) -> Check:
    """Optional: exact per-resource cost for `spendlens graph` from the CUR (local; no AWS calls)."""
    if cfg.graph.cost_source == "estimate":
        return Check("tailpipe (CUR)", "ok", "not used (graph.cost_source = estimate)")
    exe = shutil.which(cfg.graph.tailpipe_path)
    if not exe:
        return Check("tailpipe (CUR)", "warn" if cfg.graph.cost_source == "cur" else "ok",
                     "not installed: graph costs come from Cost Explorer resource data or the price book",
                     "Optional, for billed per-resource costs: https://tailpipe.io + its AWS plugin, then collect "
                     "aws_cost_and_usage_report")
    code, out = _run([exe, "query", "select count(*) as n from aws_cost_and_usage_report", "--output", "json"], 120)
    if code != 0:
        return Check("tailpipe (CUR)", "warn", (out.splitlines() or ["query failed"])[-1][:160],
                     "Collect the CUR: tailpipe collect aws_cost_and_usage_report")
    from .graph.costs import CostSourceError, parse_json

    try:
        n = int((parse_json(out) or [{}])[0].get("n") or 0)
    except (CostSourceError, ValueError, TypeError):
        n = 0
    return Check("tailpipe (CUR)", "ok" if n else "warn", f"{n:,} CUR line items collected",
                 "" if n else "Collect the CUR: tailpipe collect aws_cost_and_usage_report")


def run_checks(
    cfg: Config, *, skip_service: bool = False, extra: list[Callable[[Config], list[Check]]] | None = None
) -> list[Check]:
    checks = [
        check_python(),
        check_binary("steampipe", "brew install turbot/tap/steampipe"),
        check_binary("powerpipe", "brew install turbot/tap/powerpipe"),
        check_aws_plugin(),
    ]
    if not skip_service:  # network: STS + Steampipe/Cost Explorer. --offline runs local checks only
        checks.append(check_aws_identity(cfg.aws.profile))
    checks.append(check_regions(cfg))
    checks.append(check_llm(cfg))
    checks.append(check_tailpipe(cfg))
    if not skip_service:
        checks += check_service_and_cost(cfg)
    for fn in extra or []:
        try:
            checks += fn(cfg)
        except Exception as e:  # noqa: BLE001 - a plugin check must not break doctor
            checks.append(Check(getattr(fn, "__name__", "plugin check"), "fail", f"{type(e).__name__}: {e}"))
    return checks
