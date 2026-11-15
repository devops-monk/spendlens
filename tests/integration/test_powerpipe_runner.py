import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from spendlens.config import load_config
from spendlens.features.tags_catalog import TAGGABLE_RESOURCES
from spendlens.features.tags_checks import TAG_CHECKS
from spendlens.powerpipe import (
    PowerpipeError,
    PowerpipeRunner,
    bundled_mod_dir,
    declared_variables,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "powerpipe_tags.json"
ROOT = Path(__file__).resolve().parents[2]




def test_bundled_mod_declares_all_tag_vars():
    declared = declared_variables(bundled_mod_dir())
    assert {"database", "mandatory_tags", "prohibited_tags", "expected_tag_values", "tag_limit",
            "common_dimensions", "tag_dimensions"} <= declared



def _fake_powerpipe(tmp_path: Path, payload: str, exit_code: int = 1) -> Path:
    exe = tmp_path / "powerpipe"
    log = tmp_path / "args.json"
    out = tmp_path / "out.json"
    out.write_text(payload)
    exe.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, pathlib\n"
        "args = sys.argv[1:]\n"
        "vf = pathlib.Path(args[args.index('--var-file') + 1]).read_text()\n"
        "cp = args[args.index('--config-path') + 1].split(':')[0]\n"
        "ppc = (pathlib.Path(cp) / 'spendlens.ppc').read_text()\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps({{'args': args, 'vars': vf, 'ppc': ppc}}))\n"
        f"sys.stdout.write(pathlib.Path({str(out)!r}).read_text())\n"
        f"sys.exit({exit_code})\n"
    )
    exe.chmod(0o755)
    return exe



def test_runner_end_to_end_with_fake_binary(tmp_path, monkeypatch):
    exe = _fake_powerpipe(tmp_path, FIXTURE.read_text())
    monkeypatch.setenv("STEAMPIPE_DATABASE_PASSWORD", "s3cret")
    cfg = load_config(sets=[f"powerpipe.binary={exe}", "tagging.mandatory=[Env, Owner]",
                            "steampipe.connection=aws_all"], environ={})
    res = PowerpipeRunner(cfg).run(["tags_mandatory"], where="tags->>'resource_type' in ('s3_bucket')")
    assert len(res.findings) == 3  # exit code 1 (alarms) is not an error
    log = json.loads((tmp_path / "args.json").read_text())
    args = log["args"]
    assert args[:3] == ["benchmark", "run", "spendlens.benchmark.tags_mandatory"]
    assert args[args.index("--where") + 1] == "tags->>'resource_type' in ('s3_bucket')"
    assert args[args.index("--search-path-prefix") + 1] == "aws_all"
    assert 'mandatory_tags = ["Env", "Owner"]' in log["vars"]
    assert "database = connection.steampipe.spendlens" in log["vars"]
    assert 'password = "s3cret"' in log["ppc"]



def test_runner_reports_failure(tmp_path):
    exe = _fake_powerpipe(tmp_path, "Error: failed to connect to steampipe", exit_code=2)
    cfg = load_config(sets=[f"powerpipe.binary={exe}", "steampipe.dsn=postgresql://u:p@h:1/d"], environ={})
    with pytest.raises(PowerpipeError, match="failed to connect"):
        PowerpipeRunner(cfg).run(["tags"])



def test_generated_mod_is_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_tag_controls.py"), "--check"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout



@pytest.mark.skipif(not shutil.which("powerpipe"), reason="powerpipe not installed")
def test_powerpipe_parses_bundled_mod():
    r = subprocess.run(["powerpipe", "control", "list", "--mod-location", str(bundled_mod_dir()), "--output", "json"],
                       capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stderr
    names = {c["resource_name"] for c in json.loads(r.stdout)}
    expected = {f"tags_{c}_{t}" for c in TAG_CHECKS for t in TAGGABLE_RESOURCES}
    assert expected <= names

