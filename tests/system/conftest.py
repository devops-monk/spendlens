"""System-test environment: Moto (AWS emulator) + an ISOLATED Steampipe install + the real spendlens CLI.

Safety: Steampipe runs from its own install dir (default ~/.spendlens-e2e/sp, never ~/.steampipe) whose only
connection points at the local Moto endpoint with dummy credentials. AWS_* env vars passed to subprocesses are
dummies and AWS_ENDPOINT_URL forces the aws CLI to Moto. Nothing here can reach a real AWS account.

Opt-in: SPENDLENS_SYSTEM=1 pytest -m system   (or `make test-system`)
The first run downloads the Steampipe aws plugin + Postgres into the isolated dir (a few minutes).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from seed import seed

E2E_DIR = Path(os.environ.get("SPENDLENS_E2E_DIR", Path.home() / ".spendlens-e2e"))
SP_DIR = E2E_DIR / "sp"  # short path: macOS limits the Postgres socket path to 103 bytes
SP_PORT = int(os.environ.get("SPENDLENS_E2E_DB_PORT", "9194"))
SP_PASSWORD = "spendlens-e2e"
VENV_BIN = Path(sys.executable).parent

FINOPS_YAML = """
tagging:
  mandatory: [Environment, Service Owner]
  prohibited: [Password, Secret]
  allowed_values:
    Environment: [prod, staging, dev]
sizing:
  rds_multi_az_by_env: {prod: true, "*": false}
  rds_backup_retention_max_days_by_env: {prod: 14, "*": 1}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f"{url} did not come up")


def _steampipe(*args: str, env: dict[str, str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["steampipe", *args], env=env, capture_output=True, text=True, timeout=timeout, check=False)


@pytest.fixture(scope="session")
def system_env():
    if os.environ.get("SPENDLENS_SYSTEM") != "1":
        pytest.skip("system tests are opt-in: SPENDLENS_SYSTEM=1")
    for tool in ("steampipe", "powerpipe"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not installed")
    moto = VENV_BIN / "moto_server"
    if not moto.exists():
        pytest.skip("moto[server] not installed (pip install -e '.[dev]')")

    E2E_DIR.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    moto_log = (E2E_DIR / "moto.log").open("w")  # closed in finally: must outlive the Popen call
    moto_proc = subprocess.Popen([str(moto), "-H", "127.0.0.1", "-p", str(port)], stdout=moto_log,
                                 stderr=subprocess.STDOUT)
    sp_env = {**os.environ, "STEAMPIPE_INSTALL_DIR": str(SP_DIR), "AWS_ACCESS_KEY_ID": "testing",
              "AWS_SECRET_ACCESS_KEY": "testing", "AWS_ENDPOINT_URL": endpoint}
    sp_env.pop("AWS_PROFILE", None)
    sp_env["AWS_CONFIG_FILE"] = sp_env["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    try:
        _wait_http(f"{endpoint}/moto-api/")
        (SP_DIR / "config").mkdir(parents=True, exist_ok=True)
        (SP_DIR / "config" / "aws.spc").write_text(f'''# spendlens system tests: local Moto only
connection "aws" {{
  plugin                   = "aws"
  regions                  = ["us-east-1"]
  endpoint_url             = "{endpoint}"
  access_key               = "testing"
  secret_key               = "testing"
  s3_force_path_style      = true
  max_error_retry_attempts = 1
}}
''')
        (SP_DIR / "config" / "default.spc").write_text('options "database" {\n  cache = false\n}\n')
        if not list((SP_DIR / "plugins").rglob("steampipe-plugin-aws.plugin")):
            r = _steampipe("plugin", "install", "aws", "--progress=false", env=sp_env)
            assert r.returncode == 0, r.stderr
        _steampipe("service", "stop", "--force", env=sp_env, timeout=120)
        r = _steampipe("service", "start", "--database-port", str(SP_PORT), "--database-listen", "local",
                       "--database-password", SP_PASSWORD, env=sp_env)
        assert r.returncode == 0, r.stdout + r.stderr

        ids = seed(endpoint)
        work = E2E_DIR / "work"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        (work / "finops.yaml").write_text(FINOPS_YAML)
        cli_env = {
            "PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "COLUMNS": "250",
            "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_ENDPOINT_URL": endpoint,
            # never let the aws CLI / SDKs pick up the user's real profiles or SSO cache
            "AWS_CONFIG_FILE": str(work / "no-aws-config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(work / "no-aws-credentials"),
            "SPENDLENS_CONFIG": str(work / "finops.yaml"),
            "SPENDLENS__STEAMPIPE__PORT": str(SP_PORT),
            # the isolated install's own Moto connection; never the managed one (it would carry no endpoint)
            "SPENDLENS__STEAMPIPE__CONNECTION": "aws",
            "STEAMPIPE_DATABASE_PASSWORD": SP_PASSWORD,
            "SPENDLENS__HISTORY__PATH": str(work / "history.db"),
        }
        yield {"endpoint": endpoint, "ids": ids, "env": cli_env, "work": work, "sp_env": sp_env}
    finally:
        _steampipe("service", "stop", "--force", env=sp_env, timeout=120)
        moto_proc.terminate()
        moto_proc.wait(timeout=30)
        moto_log.close()


@pytest.fixture(scope="session")
def spendlens(system_env):
    """Run the installed `spendlens` entry point as a real subprocess; returns (exit_code, json_or_text, stderr)."""

    def run(*args: str, expect_json: bool = True):
        p = subprocess.run([str(VENV_BIN / "spendlens"), *args], env=system_env["env"], cwd=system_env["work"],
                           capture_output=True, text=True, timeout=900, check=False)
        out = json.loads(p.stdout) if expect_json and p.stdout.strip().startswith("{") else p.stdout
        return p.returncode, out, p.stderr

    return run


@pytest.fixture(scope="session")
def live_results(spendlens):
    """One live run per feature (all statuses) shared by the expectation tests."""
    results = {}
    for feature in ("tags", "rightsize", "waste"):
        code, doc, err = spendlens(feature, "--status", "all", "-f", "json")
        assert code == 0, err
        results[feature] = doc["rows"]
    return results
