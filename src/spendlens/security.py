"""Security guardrails, read-only: vulnerabilities and threats from AWS's own security services, in one model.

Sources (all through the Steampipe AWS plugin; nothing is scanned or changed by spendlens):
  inspector        Amazon Inspector v2: CVEs in ECR images, Lambda functions (packages and code) and EC2
                   instances, with exploit / fix availability, EPSS and the Inspector score
  ecr              ECR basic scanning: CVEs in an image, when Inspector enhanced scanning is not on
  securityhub      AWS Security Hub: standards controls (CIS, FSBP ...) and partner findings
  guardduty        Amazon GuardDuty: threats (compromised instances, credentials, crypto mining ...)
  access_analyzer  IAM Access Analyzer: resources shared publicly or outside the account
  detections       98 threat detections (Turbot's Tailpipe detection mods, MITRE ATT&CK mapped) run on the CloudTrail,
                   S3 access and VPC flow logs Tailpipe collected: local, no AWS calls (log_detections.py)

Every source maps onto one Finding (severity CRITICAL / HIGH / MEDIUM / LOW / INFORMATIONAL), so commands, the gate
and the assistant treat them alike. Security Hub also carries Inspector and GuardDuty findings: when those sources
are read directly, their copies in Security Hub are left out, so nothing is counted twice.

Guardrails (finops.yaml `security:`): which severity fails, fix deadlines by severity, services that must be on,
and accepted findings (an id, a reason and an expiry date). `spendlens security gate` checks them and exits 1 on a
violation, for CI: for example before deploying an image.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")
RANK = {s: i for i, s in enumerate(SEVERITIES)}  # lower is worse
SOURCES = ("inspector", "ecr", "securityhub", "guardduty", "access_analyzer", "detections")
RESOURCE_KINDS = {"image": "AWS_ECR_CONTAINER_IMAGE", "lambda": "AWS_LAMBDA_FUNCTION", "instance": "AWS_EC2_INSTANCE"}
COLUMNS = ["severity", "source", "title", "resource_type", "resource", "vulnerability_id", "package", "fixed_in",
           "fix_available", "exploit_available", "score", "epss", "actor", "source_ip", "action", "mitre",
           "first_seen", "age_days", "region", "account_id", "status", "remediation", "url", "id"]


class SecurityError(RuntimeError):
    pass


@dataclass
class SecFinding:
    source: str
    id: str
    severity: str
    title: str
    resource_type: str = ""
    resource: str = ""
    region: str = ""
    account_id: str = ""
    status: str = "ACTIVE"
    vulnerability_id: str = ""
    package: str = ""
    fixed_in: str = ""
    fix_available: str = ""
    exploit_available: str = ""
    score: float | None = None
    epss: float | None = None
    first_seen: str = ""
    last_seen: str = ""
    remediation: str = ""
    url: str = ""
    age_days: int | None = None
    actor: str = ""  # detections: who did it
    source_ip: str = ""
    action: str = ""  # detections: the API call or operation
    mitre: str = ""  # MITRE ATT&CK tactic:technique ids

    def row(self) -> dict[str, Any]:
        return {c: getattr(self, c) for c in COLUMNS}


@dataclass
class Scope:
    """What to look at. Empty = every resource."""
    sources: list[str] = field(default_factory=lambda: ["inspector", "securityhub", "guardduty", "access_analyzer"])
    min_severity: str = "LOW"
    image: str | None = None  # repo, repo:tag or repo@sha256:...
    function: str | None = None  # Lambda function name
    instance: str | None = None  # EC2 instance id
    resource: str | None = None  # any id, name or ARN (part of it)
    resource_type: str | None = None  # image | lambda | instance
    cve: str | None = None
    package: str | None = None
    fixable: bool = False  # only findings with a fix available
    exploitable: bool = False  # only findings with a known exploit
    region: str | None = None
    limit: int = 2000
    days: int | None = None  # detections: only hits in the last N days

    def image_parts(self) -> tuple[str, str | None, str | None]:
        """repo, tag, digest."""
        img = self.image or ""
        if "@" in img:
            repo, digest = img.split("@", 1)
            return repo, None, digest
        if ":" in img:
            repo, tag = img.rsplit(":", 1)
            return repo, tag, None
        return img, None, None


def _q(v: Any) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def at_least(severity: str) -> list[str]:
    s = severity.upper()
    if s not in RANK:
        raise SecurityError(f"severity: one of {', '.join(SEVERITIES)}")
    return [x for x in SEVERITIES if RANK[x] <= RANK[s]]


def _age(first_seen: str, today: date) -> int | None:
    try:
        return (today - datetime.fromisoformat(str(first_seen)[:19].replace("Z", "")).date()).days
    except ValueError:
        return None


def _gd_severity(n: Any) -> str:
    """GuardDuty's 0-10 score -> a label (AWS: 7-8.9 high, 4-6.9 medium, 1-3.9 low; 9+ critical)."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "LOW"
    return "CRITICAL" if v >= 9 else "HIGH" if v >= 7 else "MEDIUM" if v >= 4 else "LOW"


# ------------------------------------------------------------------ SQL per source


def inspector_sql(s: Scope) -> str:
    """Amazon Inspector v2. '=' conditions on these columns are sent to the Inspector API as filters."""
    w = ["status = 'ACTIVE'"]
    if s.image:
        repo, _tag, digest = s.image_parts()  # the tag is matched afterwards: Inspector lists tags as an array
        w.append(f"ecr_image_repository_name = {_q(repo)}")
        if digest:
            w.append(f"ecr_image_hash = {_q(digest)}")
    if s.function:
        w.append(f"lambda_function_name = {_q(s.function)}")
    if s.instance:
        w.append(f"resource_id = {_q(s.instance)}")
    if s.resource_type in RESOURCE_KINDS:
        w.append(f"resource_type = {_q(RESOURCE_KINDS[s.resource_type])}")
    if s.cve:
        w.append(f"vulnerability_id = {_q(s.cve)}")
    if s.fixable:
        w.append("fix_available = 'YES'")
    if s.exploitable:
        w.append("exploit_available = 'YES'")
    if s.region:
        w.append(f"region = {_q(s.region)}")
    sev = at_least(s.min_severity)
    if len(sev) < len(SEVERITIES):
        w.append(f"severity in ({', '.join(_q(x) for x in sev)})")
    return ("select arn, severity, title, status, resource_type, resource_id, region, account_id, vulnerability_id, "
            "inspector_score, epss_score, exploit_available, fix_available, "
            "cast(first_observed_at as varchar) as first_seen, cast(last_observed_at as varchar) as last_seen, "
            "remediation_recommendation_text, remediation_recommendation_url, source_url, ecr_image_repository_name, "
            "ecr_image_tags, lambda_function_name, vulnerable_packages "
            f"from aws_inspector2_finding where {' and '.join(w)} limit {int(s.limit)}")


def ecr_sql(s: Scope) -> str:
    """ECR basic scanning (one repository, required by the API; a tag or a digest optional)."""
    repo, tag, digest = s.image_parts()
    w = [f"repository_name = {_q(repo)}"]
    if tag:
        w.append(f"image_tag = {_q(tag)}")
    if digest:
        w.append(f"image_digest = {_q(digest)}")
    return ("select repository_name, image_tag, image_digest, name, severity, description, uri, attributes, region, "
            "account_id, cast(image_scan_completed_at as varchar) as scanned_at "
            f"from aws_ecr_image_scan_finding where {' and '.join(w)} limit {int(s.limit)}")


def securityhub_sql(s: Scope, skip_products: list[str]) -> str:
    w = ["record_state = 'ACTIVE'", "workflow_status <> 'RESOLVED'", "workflow_status <> 'SUPPRESSED'",
         "coalesce(compliance_status, '') <> 'PASSED'"]
    w += [f"product_name <> {_q(p)}" for p in skip_products]
    if s.resource or s.instance or s.function or s.image:
        needle = s.resource or s.instance or s.function or s.image_parts()[0]
        w.append(f"resources::text ilike {_q('%' + str(needle) + '%')}")
    if s.region:
        w.append(f"region = {_q(s.region)}")
    sev = at_least(s.min_severity)
    if len(sev) < len(SEVERITIES):
        w.append(f"severity ->> 'Label' in ({', '.join(_q(x) for x in sev)})")
    return ("select id, title, severity ->> 'Label' as severity, product_name, compliance_status, workflow_status, "
            "resources, region, account_id, cast(created_at as varchar) as first_seen, "
            "cast(updated_at as varchar) as last_seen, remediation, source_url "
            f"from aws_securityhub_finding where {' and '.join(w)} limit {int(s.limit)}")


def guardduty_sql(s: Scope) -> str:
    w = ["coalesce(service ->> 'Archived', 'false') = 'false'"]
    if s.resource or s.instance:
        w.append(f"resource::text ilike {_q('%' + str(s.resource or s.instance) + '%')}")
    if s.region:
        w.append(f"region = {_q(s.region)}")
    return ("select id, title, type, severity, resource, region, account_id, cast(created_at as varchar) as first_seen, "
            f"cast(updated_at as varchar) as last_seen from aws_guardduty_finding where {' and '.join(w)} "
            f"limit {int(s.limit)}")


def access_analyzer_sql(s: Scope) -> str:
    w = ["status = 'ACTIVE'"]
    if s.resource:
        w.append(f"resource ilike {_q('%' + s.resource + '%')}")
    if s.region:
        w.append(f"region = {_q(s.region)}")
    return ("select id, resource, resource_type, is_public, principal, action, region, account_id, "
            "cast(created_at as varchar) as first_seen, cast(updated_at as varchar) as last_seen "
            f"from aws_accessanalyzer_finding where {' and '.join(w)} limit {int(s.limit)}")


# ------------------------------------------------------------------ rows -> findings


def _pkg(packages: Any) -> tuple[str, str]:
    """('name@version', fixed in) of the first vulnerable package."""
    if isinstance(packages, list) and packages and isinstance(packages[0], dict):
        p = packages[0]
        name = f"{p.get('name') or p.get('Name') or ''}@{p.get('version') or p.get('Version') or ''}".strip("@")
        return name, str(p.get("fixedInVersion") or p.get("FixedInVersion") or "")
    return "", ""


def from_inspector(r: dict[str, Any], today: date) -> SecFinding:
    pkg, fixed = _pkg(r.get("vulnerable_packages"))
    rtype = {"AWS_ECR_CONTAINER_IMAGE": "image", "AWS_LAMBDA_FUNCTION": "lambda", "AWS_EC2_INSTANCE": "instance"}.get(
        str(r.get("resource_type") or ""), str(r.get("resource_type") or "").lower())
    resource = str(r.get("resource_id") or "")
    if rtype == "image" and r.get("ecr_image_repository_name"):
        tags = r.get("ecr_image_tags") if isinstance(r.get("ecr_image_tags"), list) else []
        resource = f"{r['ecr_image_repository_name']}:{tags[0]}" if tags else str(r["ecr_image_repository_name"])
    elif rtype == "lambda" and r.get("lambda_function_name"):
        resource = str(r["lambda_function_name"])
    first = str(r.get("first_seen") or "")
    return SecFinding("inspector", str(r.get("arn") or ""), str(r.get("severity") or "LOW").upper(),
                      str(r.get("title") or ""), rtype, resource, str(r.get("region") or ""),
                      str(r.get("account_id") or ""), str(r.get("status") or ""), str(r.get("vulnerability_id") or ""),
                      pkg, fixed, str(r.get("fix_available") or ""), str(r.get("exploit_available") or ""),
                      r.get("inspector_score"), r.get("epss_score"), first[:19], str(r.get("last_seen") or "")[:19],
                      str(r.get("remediation_recommendation_text") or "")[:500],
                      str(r.get("remediation_recommendation_url") or r.get("source_url") or ""), _age(first, today))


def from_ecr(r: dict[str, Any], today: date) -> SecFinding:
    attrs = {a.get("Key"): a.get("Value") for a in (r.get("attributes") or []) if isinstance(a, dict)}
    pkg = f"{attrs.get('package_name', '')}@{attrs.get('package_version', '')}".strip("@")
    image = f"{r.get('repository_name')}:{r.get('image_tag')}" if r.get("image_tag") else str(r.get("repository_name"))
    score = attrs.get("CVSS3_SCORE") or attrs.get("CVSS2_SCORE")
    return SecFinding("ecr", f"{image}/{r.get('name')}", str(r.get("severity") or "LOW").upper(),
                      str(r.get("description") or r.get("name") or "")[:300], "image", image, str(r.get("region") or ""),
                      str(r.get("account_id") or ""), "ACTIVE", str(r.get("name") or ""), pkg, "", "", "",
                      float(score) if score else None, None, str(r.get("scanned_at") or "")[:19], "", "",
                      str(r.get("uri") or ""), None)


def from_securityhub(r: dict[str, Any], today: date) -> SecFinding:
    res = r.get("resources") if isinstance(r.get("resources"), list) else []
    first_res = res[0] if res and isinstance(res[0], dict) else {}
    rem = r.get("remediation") if isinstance(r.get("remediation"), dict) else {}
    rec = rem.get("Recommendation") if isinstance(rem.get("Recommendation"), dict) else {}
    first = str(r.get("first_seen") or "")
    return SecFinding("securityhub", str(r.get("id") or ""), str(r.get("severity") or "LOW").upper(),
                      str(r.get("title") or ""), str(first_res.get("Type") or ""), str(first_res.get("Id") or ""),
                      str(r.get("region") or ""), str(r.get("account_id") or ""), str(r.get("workflow_status") or ""),
                      "", "", "", "", "", None, None, first[:19], str(r.get("last_seen") or "")[:19],
                      str(rec.get("Text") or "")[:500], str(rec.get("Url") or r.get("source_url") or ""),
                      _age(first, today))


def from_guardduty(r: dict[str, Any], today: date) -> SecFinding:
    res = r.get("resource") if isinstance(r.get("resource"), dict) else {}
    inst = (res.get("InstanceDetails") or {}) if isinstance(res.get("InstanceDetails"), dict) else {}
    key = (res.get("AccessKeyDetails") or {}) if isinstance(res.get("AccessKeyDetails"), dict) else {}
    resource = inst.get("InstanceId") or key.get("UserName") or key.get("AccessKeyId") or res.get("ResourceType") or ""
    first = str(r.get("first_seen") or "")
    return SecFinding("guardduty", str(r.get("id") or ""), _gd_severity(r.get("severity")),
                      str(r.get("title") or r.get("type") or ""), str(res.get("ResourceType") or ""), str(resource),
                      str(r.get("region") or ""), str(r.get("account_id") or ""), "ACTIVE", str(r.get("type") or ""),
                      "", "", "", "", float(r["severity"]) if r.get("severity") is not None else None, None,
                      first[:19], str(r.get("last_seen") or "")[:19], "", "", _age(first, today))


def from_access_analyzer(r: dict[str, Any], today: date) -> SecFinding:
    public = bool(r.get("is_public"))
    first = str(r.get("first_seen") or "")
    rtype = str(r.get("resource_type") or "")
    return SecFinding("access_analyzer", str(r.get("id") or ""), "HIGH" if public else "MEDIUM",
                      f"{rtype} is shared {'publicly' if public else 'outside the account'}", rtype,
                      str(r.get("resource") or ""), str(r.get("region") or ""), str(r.get("account_id") or ""),
                      "ACTIVE", "", "", "", "", "", None, None, first[:19], str(r.get("last_seen") or "")[:19],
                      "Review the resource policy: remove the public or external principal if it is not intended.",
                      "", _age(first, today))


# ------------------------------------------------------------------ search


@dataclass
class Result:
    findings: list[SecFinding]
    scope: Scope
    queries: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unavailable: dict[str, str] = field(default_factory=dict)  # source -> why it could not be read

    def summary(self) -> dict[str, Any]:
        f = self.findings
        by_sev = Counter(x.severity for x in f)
        top_cves = Counter(x.vulnerability_id for x in f if x.vulnerability_id.startswith(("CVE-", "GHSA-")))
        top_res = Counter((x.resource_type, x.resource) for x in f if x.resource)
        return {"total": len(f), "by_severity": {s: by_sev.get(s, 0) for s in SEVERITIES},
                "by_source": dict(Counter(x.source for x in f)),
                "by_resource_type": dict(Counter(x.resource_type or "?" for x in f).most_common()),
                "fix_first": sum(1 for x in f if x.fix_available == "YES" and x.exploit_available == "YES"
                                 and RANK.get(x.severity, 9) <= RANK["HIGH"]),
                "fixable": sum(1 for x in f if x.fix_available == "YES"),
                "exploitable": sum(1 for x in f if x.exploit_available == "YES"),
                "top_cves": [{"cve": c, "resources": n} for c, n in top_cves.most_common(10)],
                "top_resources": [{"resource_type": t, "resource": r, "findings": n}
                                  for (t, r), n in top_res.most_common(10)]}

    def to_dict(self, rows: int | None = None) -> dict[str, Any]:
        return {"scope": {k: v for k, v in asdict(self.scope).items() if v not in (None, False, [], "")},
                **self.summary(), "findings": [x.row() for x in self.findings[: rows or len(self.findings)]],
                "unavailable": self.unavailable, "queries": self.queries, "notes": self.notes}


def _why(e: Exception) -> str:
    t = str(e)
    low = t.lower()
    if "accessdenied" in low or "not authorized" in low:
        return "access denied (the role lacks the read permission)"
    if any(k in low for k in ("not enabled", "not subscribed", "subscription", "is not active", "no detector",
                              "invalidaccessexception")):
        return "the service is not enabled in this region or account"
    return (t.splitlines()[0] if t else type(e).__name__)[:200]


def matches(f: SecFinding, s: Scope) -> bool:
    if RANK.get(f.severity, 9) > RANK[s.min_severity.upper()]:
        return False
    if s.package and s.package.lower() not in f.package.lower():
        return False
    if s.image and f.source in ("inspector", "ecr"):
        repo, tag, _ = s.image_parts()
        if tag and f.source == "inspector" and f"{repo}:{tag}" != f.resource and not f.resource.endswith(f":{tag}"):
            return False
    if s.resource and s.resource.lower() not in f"{f.resource} {f.id}".lower():
        return False
    if s.fixable and f.fix_available != "YES":
        return False
    return not (s.exploitable and f.exploit_available != "YES")


def search(cfg: Any, s: Scope, *, steampipe: Any = None, today: date | None = None) -> Result:
    """Findings from every selected source; a source that is off or denied is reported, never fatal."""
    from .steampipe import SteampipeClient, SteampipeError

    at_least(s.min_severity)
    bad = [x for x in s.sources if x not in SOURCES]
    if bad:
        raise SecurityError(f"unknown source(s) {', '.join(bad)}: one of {', '.join(SOURCES)}")
    today = today or datetime.now(UTC).date()
    res = Result([], s)
    own = steampipe is None
    c = steampipe or SteampipeClient(cfg.steampipe, timeout_s=300)
    resource_scoped = bool(s.image or s.function or s.instance or s.resource_type in RESOURCE_KINDS or s.cve or
                           s.package)

    def run(source: str, sql: str, conv: Any) -> list[SecFinding]:
        res.queries.append({"source": source, "sql": sql})
        try:
            rows = c.query(sql)
        except SteampipeError as e:
            res.unavailable[source] = _why(e)
            return []
        return [f for f in (conv(r, today) for r in rows) if matches(f, s)]

    try:
        if "detections" in s.sources:
            from . import log_detections

            det = log_detections.run(cfg, days=s.days)
            res.findings += [f for f in det["findings"] if matches(f, s)]
            res.queries += [{"source": "detections", "sql": f"powerpipe benchmark: {p}"} for p in det["ran"]]
            for pack, why in det["skipped"].items():
                res.unavailable[f"detections:{pack}"] = why
        if "inspector" in s.sources:
            res.findings += run("inspector", inspector_sql(s), from_inspector)
        if s.image and ("ecr" in s.sources or "inspector" in s.sources) and not any(
                f.source == "inspector" for f in res.findings):
            got = run("ecr", ecr_sql(s), from_ecr)  # basic scanning, when Inspector has nothing for the image
            res.findings += got
            if got:
                res.notes.append("From ECR basic scanning: Inspector enhanced scanning has no findings for this image.")
        if not (s.cve or s.package or s.function or s.image):  # these are vulnerability-only questions
            skip = [p for p, src in (("Inspector", "inspector"), ("GuardDuty", "guardduty")) if src in s.sources]
            if "securityhub" in s.sources:
                res.findings += run("securityhub", securityhub_sql(s, skip), from_securityhub)
            if "guardduty" in s.sources and not (resource_scoped and not s.instance):
                res.findings += run("guardduty", guardduty_sql(s), from_guardduty)
            if "access_analyzer" in s.sources and not (resource_scoped and not s.resource):
                res.findings += run("access_analyzer", access_analyzer_sql(s), from_access_analyzer)
    finally:
        if own:
            c.close()
    seen: set[tuple[str, str]] = set()
    unique = []
    for f in res.findings:
        if (f.source, f.id) not in seen:
            seen.add((f.source, f.id))
            unique.append(f)
    unique.sort(key=lambda f: (RANK.get(f.severity, 9), -(f.score or 0), -(f.epss or 0), f.resource))
    res.findings = unique[: s.limit]
    for src, why in res.unavailable.items():
        res.notes.append(f"{src}: {why}.")
    return res


# ------------------------------------------------------------------ coverage


def status(cfg: Any, *, steampipe: Any = None) -> dict[str, Any]:
    """Which security services are on, per region, and what Inspector scans. Read-only."""
    from .steampipe import SteampipeClient, SteampipeError

    own = steampipe is None
    c = steampipe or SteampipeClient(cfg.steampipe, timeout_s=300)
    regions = sorted(set(cfg.aws.regions))
    out: dict[str, Any] = {"regions": regions, "services": {}, "inspector_coverage": [], "notes": []}

    def q(sql: str) -> list[dict[str, Any]] | str:
        try:
            return c.query(sql)
        except SteampipeError as e:
            return _why(e)

    try:
        checks = {
            "inspector": "select region, total_counts from aws_inspector2_coverage_statistics",
            "securityhub": "select region from aws_securityhub_hub",
            "guardduty": "select region, status from aws_guardduty_detector",
            "access_analyzer": "select region, status from aws_accessanalyzer_analyzer",
            "ecr_scanning": "select region, scanning_configuration from aws_ecr_registry_scanning_configuration",
        }
        for svc, sql in checks.items():
            rows = q(sql)
            if isinstance(rows, str):
                out["services"][svc] = {"on": [], "error": rows}
                continue
            on = sorted({str(r.get("region")) for r in rows if str(r.get("status") or "ENABLED").upper() in
                         ("ENABLED", "ACTIVE") and (svc != "inspector" or r.get("total_counts"))})
            if svc == "ecr_scanning":
                on = sorted({str(r.get("region")) for r in rows
                             if str((r.get("scanning_configuration") or {}).get("ScanType", "")).upper() == "ENHANCED"})
            out["services"][svc] = {"on": on, "off": [r for r in regions if r not in on and "*" not in r]}
        cov = q("select resource_type, scan_status_code, count(*) as n from aws_inspector2_coverage "
                "group by resource_type, scan_status_code")
        if isinstance(cov, list):
            out["inspector_coverage"] = cov
        else:
            out["notes"].append(f"Inspector coverage not read: {cov}")
    finally:
        if own:
            c.close()
    return out


# ------------------------------------------------------------------ guardrails


@dataclass
class Violation:
    rule: str
    detail: str
    finding: dict[str, Any] | None = None


def _ignored(f: SecFinding, ignores: list[Any], today: date) -> tuple[bool, str | None]:
    """(ignored, why an entry has expired). An entry matches the vulnerability id, the finding id or the title."""
    for ig in ignores:
        target = ig.id.lower()
        if target not in (f.vulnerability_id.lower(), f.id.lower(), f.title.lower()):
            continue
        if ig.resource and ig.resource.lower() not in f.resource.lower():
            continue
        if ig.expires and ig.expires < today:
            return False, f"accepted until {ig.expires} ({ig.reason}), now expired"
        return True, None
    return False, None


def gate(cfg: Any, res: Result, svc_status: dict[str, Any] | None = None, *, fail_on: str | None = None,
         today: date | None = None) -> list[Violation]:
    """The guardrails in finops.yaml `security:` checked against these findings (and services, when given)."""
    g = cfg.security
    today = today or datetime.now(UTC).date()
    worst = at_least(fail_on or g.fail_on)
    out: list[Violation] = []
    for f in res.findings:
        ok, expired = _ignored(f, g.ignore, today)
        if ok:
            continue
        if g.only_fixable and f.fix_available not in ("YES", "PARTIAL") and f.source in ("inspector", "ecr"):
            continue
        if expired:
            out.append(Violation("expired exception", expired, f.row()))
            continue
        if f.severity in worst:
            out.append(Violation(f"severity {f.severity.lower()}", f"{f.title} on {f.resource}", f.row()))
            continue
        limit = g.max_age_days.get(f.severity.lower())
        if limit is not None and f.age_days is not None and f.age_days > limit:
            out.append(Violation(f"{f.severity.lower()} older than {limit} days",
                                 f"{f.title} on {f.resource}: open {f.age_days} days", f.row()))
    for svc in (g.required_services if svc_status else []):
        info = svc_status["services"].get(svc, {})
        if info.get("error"):
            out.append(Violation("required service", f"{svc}: {info['error']}"))
        elif info.get("off"):
            out.append(Violation("required service", f"{svc} is off in {', '.join(info['off'])}"))
    return out


def to_history(res: Result) -> list[Any]:
    """Findings for the history (feature 'security'), so `spendlens history diff` shows new and resolved ones."""
    from .models import Finding, Severity, Status

    sev = {"CRITICAL": Severity.CRITICAL, "HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM}
    return [Finding(control=f"security_{f.source}", title=f.title[:300], check="security", service=f.resource_type,
                    resource_type=f.resource_type, resource=f.resource or f.id, account_id=f.account_id or None,
                    region=f.region or None, status=Status.ALARM, reason=f.vulnerability_id or f.title[:200],
                    severity=sev.get(f.severity, Severity.LOW),
                    dimensions={k: v for k, v in f.row().items() if k in ("vulnerability_id", "package", "fixed_in",
                                                                         "fix_available", "exploit_available", "id",
                                                                         "source", "age_days")})
            for f in res.findings]


IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,255}(:[A-Za-z0-9._-]{1,128}|@sha256:[a-f0-9]{64})?$")
