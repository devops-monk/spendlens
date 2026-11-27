"""spendlens security: sources, filters, de-duplication, the gate, history, the CLI, the AI tool and log detections.

Fakes only (no AWS). Log detections are parsed from real Powerpipe output (tests/fixtures/detections_s3_access.json,
produced by the vendored S3 access-log mod on sample logs)."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from spendlens import log_detections as ld
from spendlens import security as sec
from spendlens.ai.catalog import load as load_catalog
from spendlens.ai.hints import command_for
from spendlens.ai.sql_check import check
from spendlens.cli import app
from spendlens.config import load_config
from spendlens.steampipe import SteampipeError

TODAY = date(2026, 11, 27)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "detections_s3_access.json"

INSPECTOR = [
    {"arn": "arn:insp/1", "severity": "CRITICAL", "title": "CVE-2024-3094 - xz", "status": "ACTIVE",
     "resource_type": "AWS_ECR_CONTAINER_IMAGE", "resource_id": "sha256:aaa", "region": "eu-west-1",
     "account_id": "1", "vulnerability_id": "CVE-2024-3094", "inspector_score": 10.0, "epss_score": 0.9,
     "exploit_available": "YES", "fix_available": "YES", "first_seen": "2026-10-01T00:00:00",
     "ecr_image_repository_name": "payments-api", "ecr_image_tags": ["1.4.2"],
     "vulnerable_packages": [{"name": "xz", "version": "5.6.0", "fixedInVersion": "5.6.2"}]},
    {"arn": "arn:insp/2", "severity": "MEDIUM", "title": "CVE-2023-1 - openssl", "status": "ACTIVE",
     "resource_type": "AWS_LAMBDA_FUNCTION", "resource_id": "arn:lambda:orders", "lambda_function_name": "orders",
     "region": "eu-west-1", "vulnerability_id": "CVE-2023-1", "fix_available": "NO", "exploit_available": "NO",
     "first_seen": "2026-11-20T00:00:00", "vulnerable_packages": [{"name": "openssl", "version": "3.0.1"}]},
]
HUB = [{"id": "sh/1", "title": "S3 bucket should block public access", "severity": "HIGH", "product_name": "Security Hub",
        "workflow_status": "NEW", "resources": [{"Type": "AwsS3Bucket", "Id": "arn:aws:s3:::data"}],
        "region": "eu-west-1", "first_seen": "2026-11-01T00:00:00",
        "remediation": {"Recommendation": {"Text": "Turn on Block Public Access", "Url": "https://docs"}}}]
GUARD = [{"id": "gd/1", "title": "EC2 instance is querying a crypto mining domain", "type": "CryptoCurrency:EC2/BitcoinTool",
          "severity": 8.0, "resource": {"ResourceType": "Instance", "InstanceDetails": {"InstanceId": "i-123"}},
          "region": "eu-west-1", "first_seen": "2026-11-26T00:00:00"}]
ANALYZER = [{"id": "aa/1", "resource": "arn:aws:s3:::shared", "resource_type": "AWS::S3::Bucket", "is_public": True,
             "region": "eu-west-1", "first_seen": "2026-11-25T00:00:00"}]
ECR = [{"repository_name": "legacy", "image_tag": "v1", "name": "CVE-2022-9", "severity": "HIGH", "uri": "https://cve",
        "attributes": [{"Key": "package_name", "Value": "glibc"}, {"Key": "package_version", "Value": "2.31"},
                       {"Key": "CVSS3_SCORE", "Value": "7.5"}], "region": "eu-west-1"}]


class Steampipe:
    def __init__(self, deny: tuple[str, ...] = ()) -> None:
        self.sql: list[str] = []
        self.deny = deny

    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        self.sql.append(sql)
        for table, rows in (("aws_inspector2_finding", INSPECTOR), ("aws_securityhub_finding", HUB),
                            ("aws_guardduty_finding", GUARD), ("aws_accessanalyzer_finding", ANALYZER),
                            ("aws_ecr_image_scan_finding", ECR)):
            if f"from {table} " in sql:
                if table in self.deny:
                    raise SteampipeError("AccessDeniedException: not authorized")
                if table == "aws_inspector2_finding" and "ecr_image_repository_name = 'legacy'" in sql:
                    return []
                if table == "aws_inspector2_finding" and "lambda_function_name = 'orders'" in sql:
                    return [r for r in rows if r.get("lambda_function_name") == "orders"]
                return [dict(r) for r in rows]
        if "aws_securityhub_hub" in sql:
            return [{"region": "eu-west-1"}]
        if "aws_guardduty_detector" in sql:
            return [{"region": "eu-west-1", "status": "ENABLED"}]
        if "aws_inspector2_coverage_statistics" in sql:
            return [{"region": "eu-west-1", "total_counts": 12}]
        if "aws_inspector2_coverage " in sql:
            return [{"resource_type": "AWS_ECR_CONTAINER_IMAGE", "scan_status_code": "ACTIVE", "n": 12}]
        return []


def cfg(*sets: str) -> Any:
    return load_config(environ={}, sets=list(sets))


@pytest.mark.parametrize("scope", [sec.Scope(), sec.Scope(image="payments-api:1.4.2", fixable=True, exploitable=True,
                                                          min_severity="HIGH", cve="CVE-2024-3094"),
                                   sec.Scope(function="orders", resource_type="lambda", region="eu-west-1"),
                                   sec.Scope(instance="i-1", resource="o'brien")])
def test_generated_sql_passes_the_checker(scope: sec.Scope) -> None:
    for sql in (sec.inspector_sql(scope), sec.securityhub_sql(scope, ["Inspector"]), sec.guardduty_sql(scope),
                sec.access_analyzer_sql(scope)):
        assert check(sql, load_catalog()).ok, [str(p) for p in check(sql, load_catalog()).errors]
    img = sec.Scope(image="repo@sha256:" + "a" * 64)
    assert check(sec.ecr_sql(img), load_catalog()).ok and "image_digest = 'sha256:" in sec.ecr_sql(img)


def test_all_sources_normalised_worst_first_and_hub_copies_skipped() -> None:
    sp = Steampipe()
    res = sec.search(cfg(), sec.Scope(), steampipe=sp, today=TODAY)
    assert [f.source for f in res.findings][:2] == ["inspector", "guardduty"]  # critical, then high (score 8)
    assert {f.source for f in res.findings} == {"inspector", "securityhub", "guardduty", "access_analyzer"}
    hub_sql = next(s for s in sp.sql if "aws_securityhub_finding" in s)
    assert "product_name <> 'Inspector'" in hub_sql and "product_name <> 'GuardDuty'" in hub_sql
    xz = res.findings[0]
    assert (xz.resource, xz.package, xz.fixed_in, xz.age_days) == ("payments-api:1.4.2", "xz@5.6.0", "5.6.2", 57)
    s = res.summary()
    assert s["by_severity"]["CRITICAL"] == 1 and s["fix_first"] == 1 and s["top_cves"][0]["cve"] == "CVE-2024-3094"


def test_filters_and_scoping() -> None:
    res = sec.search(cfg(), sec.Scope(min_severity="HIGH"), steampipe=Steampipe(), today=TODAY)
    assert {f.severity for f in res.findings} <= {"CRITICAL", "HIGH"}
    res = sec.search(cfg(), sec.Scope(function="orders"), steampipe=Steampipe(), today=TODAY)
    assert [f.resource for f in res.findings] == ["orders"]  # vulnerability question: Inspector only
    res = sec.search(cfg(), sec.Scope(image="payments-api:9.9.9"), steampipe=Steampipe(), today=TODAY)
    assert not [f for f in res.findings if f.source == "inspector"]  # a different tag
    res = sec.search(cfg(), sec.Scope(package="openssl"), steampipe=Steampipe(), today=TODAY)
    assert [f.package for f in res.findings] == ["openssl@3.0.1"]
    with pytest.raises(sec.SecurityError):
        sec.search(cfg(), sec.Scope(sources=["nope"]), steampipe=Steampipe())
    with pytest.raises(sec.SecurityError):
        sec.at_least("urgent")


def test_ecr_basic_scanning_when_inspector_has_nothing() -> None:
    res = sec.search(cfg(), sec.Scope(sources=["inspector", "ecr"], image="legacy:v1"), steampipe=Steampipe(),
                     today=TODAY)
    f = res.findings[0]
    assert (f.source, f.vulnerability_id, f.package, f.score) == ("ecr", "CVE-2022-9", "glibc@2.31", 7.5)
    assert any("ECR basic scanning" in n for n in res.notes)


def test_a_denied_source_is_reported_not_fatal() -> None:
    res = sec.search(cfg(), sec.Scope(), steampipe=Steampipe(deny=("aws_guardduty_finding",)), today=TODAY)
    assert "guardduty" in res.unavailable and "access denied" in res.unavailable["guardduty"]
    assert any(f.source == "inspector" for f in res.findings)


def test_gate_rules() -> None:
    res = sec.search(cfg(), sec.Scope(), steampipe=Steampipe(), today=TODAY)
    v = sec.gate(cfg(), res, today=TODAY)  # default: fail on critical; high older than 30 days
    assert [x.rule for x in v] == ["severity critical"]
    v = sec.gate(cfg("security.fail_on=high"), res, today=TODAY)
    assert {x.rule for x in v} == {"severity critical", "severity high"}
    ok = cfg("security.ignore=[{id: CVE-2024-3094, reason: patched in next release, expires: 2026-12-31}]")
    assert sec.gate(ok, res, today=TODAY) == []
    late = cfg("security.ignore=[{id: CVE-2024-3094, reason: patched in next release, expires: 2026-11-01}]")
    assert [x.rule for x in sec.gate(late, res, today=TODAY)] == ["expired exception"]
    age = cfg("security.fail_on=critical", "security.max_age_days={medium: 3}",
              "security.ignore=[{id: CVE-2024-3094, reason: accepted}]")
    assert [x.rule for x in sec.gate(age, res, today=TODAY)] == ["medium older than 3 days"]
    st = sec.status(cfg("aws.regions=[eu-west-1, eu-west-2]"), steampipe=Steampipe())
    need = cfg("security.required_services=[guardduty]", "security.ignore=[{id: CVE-2024-3094, reason: accepted}]")
    v = sec.gate(need, res, st, today=TODAY)
    assert [x.detail for x in v] == ["guardduty is off in eu-west-2"]


def test_status_and_history() -> None:
    st = sec.status(cfg(), steampipe=Steampipe())
    assert st["services"]["guardduty"]["on"] == ["eu-west-1"] and st["inspector_coverage"][0]["n"] == 12
    res = sec.search(cfg(), sec.Scope(), steampipe=Steampipe(), today=TODAY)
    h = sec.to_history(res)
    assert h[0].control == "security_inspector" and h[0].dimensions["vulnerability_id"] == "CVE-2024-3094"


def test_log_detections_from_real_powerpipe_output() -> None:
    doc = json.loads(FIXTURE.read_text())
    found, summary = ld.parse(doc, "s3_access")
    assert {(s["detection"], s["hits"]) for s in summary} == {
        ("s3_object_accessed_using_suspicious_user_agent", 2), ("s3_object_accessed_using_insecure_tls_version", 1)}
    tls = next(f for f in found if f.vulnerability_id == "s3_object_accessed_using_insecure_tls_version")
    assert (tls.severity, tls.actor, tls.source_ip, tls.mitre, tls.action) == (
        "MEDIUM", "arn:aws:iam::123456789012:user/alice", "192.0.2.3", "TA0001:T1078", "REST.GET.OBJECT")
    assert tls.url.endswith("/detections/s3_object_accessed_using_insecure_tls_version")
    from datetime import UTC, datetime
    assert ld.parse(doc, "s3_access", datetime(2026, 11, 27, tzinfo=UTC))[0] == []  # outside the window
    got = ld.run(cfg(), ["s3_access", "cloudtrail"], runner=lambda pack: FIXTURE.read_text(),
                 ready=lambda kind: kind == "s3_access_logs")
    assert got["ran"] == ["s3_access"] and "cloudtrail" in got["skipped"] and len(got["findings"]) == 3
    with pytest.raises(ld.DetectionError):
        ld.run(cfg(), ["nope"], ready=lambda kind: True)


def test_vendored_mods_are_complete() -> None:
    for pack in ld.PACKS:
        d = ld.mod_dir(pack)
        assert (d / "mod.pp").is_file() and (d / "LICENSE").is_file() and (d / "docs" / "index.md").is_file()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client(Steampipe):
        def close(self) -> None:
            pass
    monkeypatch.setattr("spendlens.steampipe.SteampipeClient", lambda *a, **k: Client())


def test_cli(tmp_path, fake_client) -> None:
    wide = {"COLUMNS": "220"}
    r = CliRunner().invoke(app, ["security", "findings", "--severity", "high", "--no-save"], env=wide)
    assert r.exit_code == 0, r.output
    assert "CVE-2024-3094" in r.stdout and "fix first" in " ".join(r.output.split())
    out = tmp_path / "f.csv"
    assert CliRunner().invoke(app, ["security", "findings", "-o", str(out), "--no-save"]).exit_code == 0
    assert out.read_text().splitlines()[0].startswith("severity,source,title")
    assert CliRunner().invoke(app, ["security", "image", "Bad Image!"]).exit_code == 2
    r = CliRunner().invoke(app, ["security", "image", "payments-api:1.4.2"], env=wide)
    assert r.exit_code == 0 and "xz" in r.stdout
    r = CliRunner().invoke(app, ["security", "gate", "--json"])
    assert r.exit_code == 1 and json.loads(r.stdout)["passed"] is False
    r = CliRunner().invoke(app, ["security", "gate", "--lambda", "orders"], env=wide)
    assert r.exit_code == 0 and "gate passed" in r.stdout
    assert CliRunner().invoke(app, ["security", "summary"]).exit_code == 0
    r = CliRunner().invoke(app, ["security", "status"], env=wide)
    assert r.exit_code == 0 and "GuardDuty" in r.stdout


def test_ai_tool_and_hint(tmp_path) -> None:
    from spendlens.ai.tools import Toolbox

    tb = Toolbox(cfg(), tmp_path / "h.db", live=True)
    tb._tailpipe_ok = False
    tb._client = lambda: Steampipe()  # type: ignore[method-assign]
    assert {"security_findings", "security_status"} <= {s.name for s in tb.specs()}
    out = json.loads(tb.execute("1", "security_findings", {"image": "payments-api:1.4.2"}).content)
    assert out["rows"][0]["vulnerability_id"] == "CVE-2024-3094" and out["by_severity"]["CRITICAL"] == 1
    assert command_for("security_findings", {"image": "payments-api:1.4.2", "min_severity": "HIGH",
                                             "fixable": True}) == \
        "spendlens security image payments-api:1.4.2 --severity high --fixable"
    assert command_for("security_findings", {"instance": "i-1", "fixable": True}) == "spendlens security instance i-1"
    assert command_for("security_findings", {"sources": ["detections"], "days": 7}) == \
        "spendlens security detections --days 7"


def test_unknown_severities_are_informational_not_dropped() -> None:
    assert sec.label("UNTRIAGED") == "INFORMATIONAL" and sec.label(None) == "INFORMATIONAL"
    f = sec.from_inspector({**INSPECTOR[1], "severity": "UNTRIAGED"}, TODAY)
    assert f.severity == "INFORMATIONAL" and sec.matches(f, sec.Scope(min_severity="INFORMATIONAL"))
    assert not sec.matches(f, sec.Scope(min_severity="LOW"))


@pytest.mark.parametrize("sev,expected", [("critical", {"CRITICAL"}), ("high", {"CRITICAL", "HIGH"}),
                                          ("medium", {"CRITICAL", "HIGH", "MEDIUM"})])
def test_severity_is_a_floor(sev: str, expected: set[str]) -> None:
    res = sec.search(cfg(), sec.Scope(min_severity=sev.upper()), steampipe=Steampipe(), today=TODAY)
    assert {f.severity for f in res.findings} <= expected and res.findings


def test_image_exports(tmp_path, fake_client) -> None:
    r = CliRunner().invoke(app, ["security", "image", "payments-api:1.4.2", "-f", "json"])
    rows = json.loads(r.stdout)
    assert r.exit_code == 0 and rows[0]["vulnerability_id"] == "CVE-2024-3094" and rows[0]["fixed_in"] == "5.6.2"
    assert [x["vulnerability_id"] for x in rows if x["fix_available"] == "YES"] == ["CVE-2024-3094"]  # the jq recipe
    for ext in ("csv", "xlsx", "jsonl", "md", "html", "yaml", "tsv"):
        out = tmp_path / f"v.{ext}"
        r = CliRunner().invoke(app, ["security", "image", "payments-api:1.4.2", "--severity", "high", "-o", str(out)])
        assert r.exit_code == 0 and out.stat().st_size > 0, (ext, r.output)
    assert (tmp_path / "v.csv").read_text().splitlines()[0].startswith("severity,source,title,resource_type")
    out = tmp_path / "review.xlsx"
    assert CliRunner().invoke(app, ["security", "summary", "-o", str(out)]).exit_code == 0
    import openpyxl
    assert openpyxl.load_workbook(out).sheetnames == ["by severity", "by resource type", "top CVEs", "findings"]
