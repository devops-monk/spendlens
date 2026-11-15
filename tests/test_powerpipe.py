import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from spendlens.config import load_config
from spendlens.features.tags_catalog import TAGGABLE_RESOURCES
from spendlens.features.tags_checks import TAG_CHECKS
from spendlens.models import Status
from spendlens.powerpipe import (
    PowerpipeError,
    PowerpipeRunner,
    bundled_mod_dir,
    connection_text,
    declared_variables,
    hcl_value,
    iter_json_documents,
    parse_results,
    var_file_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "powerpipe_tags.json"
ROOT = Path(__file__).resolve().parents[1]


def test_hcl_encoding():
    assert hcl_value(["a", "b c"]) == '["a", "b c"]'
    assert hcl_value({"Environment": ["prod", "dev%"]}) == '{"Environment" = ["prod", "dev%"]}'
    assert hcl_value(True) == "true" and hcl_value(None) == "null" and hcl_value(0.25) == "0.25"
    assert hcl_value("${evil}") == '"$${evil}"' and hcl_value("%{x}") == '"%%{x}"'
    assert hcl_value('quo"te') == '"quo\\"te"'


def test_var_file_only_declared_vars():
    text = var_file_text({"mandatory_tags": ["A"], "not_in_mod": 1}, {"database", "mandatory_tags"})
    assert text.splitlines() == ["database = connection.steampipe.spendlens", 'mandatory_tags = ["A"]']


def test_connection_text_omits_empty_password():
    t = connection_text({"host": "h", "port": 9193, "db": "steampipe", "username": "u", "password": None})
    assert 'connection "steampipe" "spendlens"' in t and "password" not in t and "port     = 9193" in t


def test_bundled_mod_declares_all_tag_vars():
    declared = declared_variables(bundled_mod_dir())
    assert {"database", "mandatory_tags", "prohibited_tags", "expected_tag_values", "tag_limit",
            "common_dimensions", "tag_dimensions"} <= declared


def test_parse_results_fixture():
    findings = parse_results(json.loads(FIXTURE.read_text()))
    assert [f.status for f in findings] == [Status.ALARM, Status.OK, Status.ERROR]
    alarm = findings[0]
    assert alarm.control == "tags_mandatory_s3_bucket" and alarm.check == "tags_mandatory"
    assert alarm.resource_type == "s3_bucket" and alarm.account_id == "111122223333"
    assert alarm.region == "eu-west-1" and alarm.dimensions == {"Environment": "prod"}
    assert alarm.severity.value == "high"
    assert "AccessDenied" in findings[2].reason


def test_concatenated_documents_and_dedupe():
    doc = FIXTURE.read_text()
    docs = list(iter_json_documents(doc + "\n" + doc))
    assert len(docs) == 2
    # the same control under two benchmarks in one document is only counted once
    d = json.loads(doc)
    d["groups"].append(d["groups"][0])
    assert len(parse_results(d)) == 3


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
