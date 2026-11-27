# Security guardrails

`spendlens security` gathers what AWS's own security services already know about your account into one list, worst first. It covers vulnerabilities in container images, Lambda functions and EC2 instances, threats, risky configuration and public access. It answers questions about one image or function, and acts as a gate in CI.

It is read-only, like the rest of spendlens. It never scans, patches, quarantines or changes anything: it reads the findings AWS has produced and the logs you collected.

```bash
spendlens security status                               # which security services are on, per region
spendlens security findings --severity high             # everything high and critical, worst first
spendlens security image payments-api:1.4.2             # is this image vulnerable?
spendlens security gate --image payments-api:1.4.2      # fail the build on critical vulnerabilities
```

## Where findings come from

| Source | What it finds | Needs |
|---|---|---|
| **Amazon Inspector** (`inspector`) | CVEs in ECR images, Lambda functions (packages, layers and code) and EC2 instances. Each comes with the vulnerable package, the fixed version, whether a fix and a known exploit exist, the EPSS score (how likely it is to be exploited) and Inspector's score | Inspector turned on |
| **ECR basic scanning** (`ecr`) | CVEs in an image when Inspector's enhanced scanning isn't on | Scan on push, or a manual scan |
| **AWS Security Hub** (`securityhub`) | Failed standards controls (CIS, AWS Foundational Security Best Practices …) and partner findings | Security Hub turned on |
| **Amazon GuardDuty** (`guardduty`) | Threats: compromised instances, leaked credentials, crypto mining, unusual API calls | GuardDuty turned on |
| **IAM Access Analyzer** (`access_analyzer`) | Resources shared publicly or outside your account or organisation | An analyzer |
| **Log detections** (`detections`) | 98 threat detections on your own logs, mapped to MITRE ATT&CK (below) | Tailpipe with CloudTrail, S3 access or VPC flow logs |

**Every source maps onto one model.** Each finding has a severity (critical, high, medium, low, informational), a resource, a title and, where it applies, the CVE, package, fixed version, fix and exploit flags, actor, source IP and MITRE ids. GuardDuty's 0–10 score maps onto the same scale (9+ critical, 7+ high, 4+ medium).

**Nothing is counted twice.** Security Hub also carries Inspector's and GuardDuty's findings. When those services are read directly, their copies in Security Hub are left out.

**A service that is off is reported, never read as "no findings".** If a source can't be read because it's off or the role lacks a permission, the output says so. `security status` shows which services are on in which region.

## Commands

| Command | What it does |
|---|---|
| `spendlens security status` | Which services are on in each configured region, ECR enhanced scanning, and what Inspector covers |
| `spendlens security findings` | Every finding, worst first. Filters: `--severity`, `--source`, `--type image/lambda/instance`, `--cve`, `--package`, `--fixable`, `--exploitable`, `--resource`, `--region` |
| `spendlens security image REPO[:TAG]` | Vulnerabilities in one image: Inspector, or ECR basic scanning when Inspector has none. A digest works too (`repo@sha256:…`) |
| `spendlens security lambda NAME` | Vulnerabilities in one Lambda function |
| `spendlens security instance ID` | Vulnerabilities and threats on one EC2 instance |
| `spendlens security resource ID` | Everything every source says about one resource |
| `spendlens security detections` | Threat detections on the logs Tailpipe collected (`--days`, `--pack`, `--severity`) |
| `spendlens security summary` | Counts by severity, source and resource type; the most widespread CVEs; the resources with most findings |
| `spendlens security gate` | Checks your guardrails and exits 1 on a violation (below) |

Every command takes `-o file.csv` (or `.xlsx`, `.json`, `.jsonl`, `.md`, `.html`, `.yaml`). `security findings` saves each full run to the history, so `spendlens history diff` shows which findings are new and which were resolved since the last run.

## Recipes

```bash
# Patch first: high or critical, a fix exists, and an exploit is known
spendlens security findings --severity high --fixable --exploitable

# Where is this CVE, across every image, function and instance?
spendlens security findings --cve CVE-2024-3094

# Every image with a vulnerable openssl, to Excel
spendlens security findings --type image --package openssl -o openssl.xlsx

# One release candidate, high and critical only, fixable
spendlens security image payments-api:1.4.2 --severity high --fixable

# Public or cross-account access
spendlens security findings --source access_analyzer

# Is anything off? Then everything at once, for the security review
spendlens security status
spendlens security summary -o security-review.xlsx
```

## Log detections

`spendlens security detections` runs Turbot's official Tailpipe detection mods on the logs you collected. The mods are bundled, pinned and unchanged (Apache-2.0):

| Pack | Detections | Examples |
|---|---|---|
| `cloudtrail` (tailpipe-mod-aws-cloudtrail-log-detections v0.3.0) | 86 | Root console login, CloudTrail logging stopped, GuardDuty detector deleted, S3 Block Public Access disabled, AMI or EBS snapshot shared publicly, IAM access key created, KMS key scheduled for deletion |
| `s3_access` (tailpipe-mod-aws-s3-server-access-log-detections v0.2.0) | 6 | Insecure TLS, suspicious user agents, very large transfers, access outside business hours |
| `flow_logs` (tailpipe-mod-aws-vpc-flow-log-detections v0.1.0) | 6 | Risky ports and protocols, large data transfers |

Powerpipe runs them against Tailpipe's local database, so they make no AWS calls. Each hit carries its severity, MITRE ATT&CK ids, the resource, who did it (actor), the source IP and the time.
- **Only packs with logs run.** A pack runs only when Tailpipe has its logs configured; see [Tailpipe](../tailpipe/).
- **The window.** The detections read everything collected, and spendlens keeps the hits within `--days` (7 by default).

```bash
spendlens tailpipe init && spendlens tailpipe collect --days 30
spendlens security detections --days 30
spendlens security detections --pack cloudtrail --severity high -o detections.xlsx
```

To include detections in `security findings` and the gate, add `detections` to `security.sources`.

## Guardrails and the CI gate

The guardrails are in `finops.yaml`:

```yaml
security:
  sources: [inspector, securityhub, guardduty, access_analyzer]  # add detections for log detections
  fail_on: CRITICAL            # findings at or above this severity fail the gate
  only_fixable: false          # true: vulnerabilities with no fix yet don't fail it
  max_age_days:                # fix deadlines: an older finding fails, whatever fail_on says
    critical: 15
    high: 30
  required_services: [inspector, guardduty]   # fail when one is off in a configured region
  ignore:                      # accepted findings: they pass until they expire
    - id: CVE-2024-1234        # a CVE, a finding id, a detection name or a title
      reason: "No fix upstream; not reachable from the internet"
      expires: 2027-01-31      # after this date the finding fails again
      resource: payments-api   # optional: only on this resource
```

`spendlens security gate` checks them, prints each violation, and exits:

| Exit code | Meaning |
|---|---|
| 0 | Every guardrail passes |
| 1 | At least one violation: a severity, an overdue fix, an expired exception or a required service that is off |
| 2 | No source could be read, so the gate cannot pass on no data |

```bash
spendlens security gate                                             # the whole account
spendlens security gate --image "$IMAGE" --fail-on high            # one image, before deploying it
spendlens security gate --lambda orders-processor --json            # machine-readable, for the pipeline
```

In a pipeline, run the gate after the image is pushed and scanned, and before the deploy step. Use a read-only role (see [CI/CD](../ci/)).

## With the AI assistant

The assistant has a `security_findings` tool with every filter above, and `security_status`:

```bash
spendlens ask "Is payments-api:1.4.2 safe to deploy? What should we fix first?"
spendlens ask "Which critical CVEs have a known exploit and a fix, and where are they?"
spendlens ask "What threats did GuardDuty find this week, and on which instances?"
spendlens ask "Which S3 buckets are shared publicly?"
```

- **How it ranks:** by severity, then known exploit, then available fix. It names the CVE, package, fixed version and resource.
- **Off isn't safe:** it never calls something safe because a source that was off returned nothing.
- **No-AI hints:** after each answer it lists the commands that get the same data without the AI, for example `spendlens security image payments-api:1.4.2 --severity high`.

## Permissions

All read-only. The `SecurityAudit` managed policy covers every source:
- `inspector2:ListFindings`, `ListCoverage`, `ListCoverageStatistics`;
- `ecr:DescribeImageScanFindings`, `GetRegistryScanningConfiguration`;
- `securityhub:GetFindings`, `DescribeHub`;
- `guardduty:ListDetectors`, `ListFindings`, `GetFindings`;
- `access-analyzer:ListAnalyzers`, `ListFindings`.

Log detections need no AWS permissions: they read Tailpipe's local data. See [IAM permissions](../permissions/).
