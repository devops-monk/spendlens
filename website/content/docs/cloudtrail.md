# CloudTrail activity

CloudTrail records every call made in your AWS account: who made it, when, from where, and whether it worked. `spendlens cloudtrail` searches those events with any filter you need, summarises them, and exports them to a file. The AI assistant can do the same from a plain-English question.

It is read-only. It never changes a trail, a log group or a bucket. It writes only the file you ask for.

```bash
spendlens cloudtrail events --days 7 --event UpdateService           # ECS deployments this week
spendlens cloudtrail events --preset console-logins --days 30        # who signed in, failed sign-ins too
spendlens cloudtrail summary --days 7                                # who did most, failed calls, source IPs
spendlens ask "Who deleted anything in production last week? Export it to Excel"
```

## What you can answer

- **What changed, and who changed it:** a resource, a security group, an IAM policy, a deployment.
- **Everything one person, role or access key did** in a time range.
- **What was deleted or terminated**, and by whom.
- **Who signed in to the console**, from where, and which sign-ins failed.
- **Which calls were refused** (AccessDenied), and for which roles. This often shows a missing permission or someone probing.
- **Calls from an unexpected network**, by source IP or CIDR range.
- **Who uses an access key** before you rotate or delete it.

## Where the events come from

spendlens reads the best source it can reach and tells you which one it used. `--source` picks one yourself.

| Source | Needs | History | Data events | Speed |
|---|---|---|---|---|
| **Tailpipe** (`aws_cloudtrail_log`) | `spendlens tailpipe init` and `collect` (see [Tailpipe](../tailpipe/)) | As far back as you collect | Yes, if the trail records them | Fast: local queries, no AWS calls |
| **CloudWatch Logs** (`aws_cloudtrail_trail_event`) | A trail that sends to CloudWatch Logs (found automatically) | As long as the log group keeps events | Yes, if the trail records them | Good: the time range and most filters are sent to AWS in one filter pattern |
| **LookupEvents** (`aws_cloudtrail_lookup_event`) | Nothing: always there | The last 90 days | No, management events only | Fast when narrow, slow when broad |

`--source auto` (the default) picks one like this:
- **Tailpipe, only when it's set up and up to date.** CloudTrail sources must be configured for Tailpipe (by `spendlens tailpipe init` or your own `.tpc` files), and the logs it has collected must cover the time range you asked about.
  - Nothing configured: spendlens goes straight to Steampipe, as usual.
  - Configured, but the logs are stale or start too late: it reads from AWS through Steampipe and says which dates Tailpipe covers.
  - Older than the 90 days LookupEvents keeps: it uses what Tailpipe has and names the gap.
- **Then the trail's CloudWatch Logs group**, if the trail has one.
- **Then LookupEvents.** If the CloudWatch Logs group can't be read, for example because it's in another account, spendlens falls back to LookupEvents and says so.

### Steampipe only, even when Tailpipe is on

| To | Run |
|---|---|
| Skip Tailpipe for one search | `spendlens cloudtrail events --source aws ...` (the trail's CloudWatch Logs, then LookupEvents), or `--source lookup` / `--source cloudwatch` for one table |
| Skip Tailpipe for one AI question | `spendlens ask --no-tailpipe "..."`, or say it in the question: "using Steampipe, who changed ..." (the tool's `source: steampipe`) |
| Write the SQL yourself | `spendlens query "select ... from aws_cloudtrail_lookup_event where start_time = '2026-11-20' and end_time = '2026-11-27' and event_name = 'UpdateService' limit 100"` |
| Turn Tailpipe off everywhere | `tailpipe.enabled: false` in `finops.yaml` (or `--set tailpipe.enabled=false`, or `SPENDLENS__TAILPIPE__ENABLED=false`). spendlens then never reads Tailpipe on its own: not for CloudTrail, the AI or graph prices. `--source tailpipe` and the `spendlens tailpipe` commands still work when you ask for them |

`spendlens cloudtrail trails` shows your trails, where each one delivers, and which sources spendlens will use:

```bash
spendlens cloudtrail trails
```

## How it works

```
 spendlens cloudtrail events --days 7 --event UpdateService --user alice
        │
        ▼
 1. Filters: time range, user, events, service, resource, IP, errors, reads/writes, preset
        │
        ├─ Tailpipe has CloudTrail logs? ──► one DuckDB query, every filter, local
        │
        ├─ trail sends to CloudWatch Logs? ─► FilterLogEvents: log group + time + '=' filters in one
        │                                     CloudWatch filter pattern, newest-first windows
        │
        └─ else LookupEvents ──────────────► ONE filter sent to AWS (the most selective one),
                                              the rest applied by Steampipe; one call per exact event name;
                                              broad searches in widening windows: 1 h, 6 h, 1 day, 3 days, 1 week …
        │
        ▼
 2. The same filters again in Python, so every source gives the same answer
 3. Newest first, de-duplicated, capped at --limit; stops at --max-wait with what it found
 4. Table on screen, or a file: csv, tsv, json, jsonl, md, html, xlsx, yaml
```

Why LookupEvents needs care:
- **One filter per call.** The API accepts one filter, such as an event name or a user. A second filter makes the request invalid, so spendlens sends the most selective one and applies the rest itself.
- **50 events a page, rate-limited.** A search on a service alone, or on nothing, can mean thousands of events. spendlens searches newest first in widening windows and stops after `--max-wait` seconds (60 by default). It returns what it found and says how far back it got.
- **Exact event names are fast.** `--event UpdateService` matches only those events. Several exact names make one call each.

## Commands in order

```bash
# 0. Log in (any read-only role)
aws sso login --profile <profile>

# 1. See your trails and which sources spendlens will read
spendlens cloudtrail trails

# 2. Search
spendlens cloudtrail events --days 1
spendlens cloudtrail events --days 7 --event UpdateService --show-sql

# 3. Summarise
spendlens cloudtrail summary --days 7

# 4. Export
spendlens cloudtrail events --days 30 --preset deletes -o deletes.xlsx

# 5. Optional, for more history and data events: Tailpipe
spendlens tailpipe init && spendlens tailpipe collect cloudtrail --days 90
```

## Every filter

All filters work together, on `events` and `summary` alike. They also work with `--preset`, which they narrow further.

| Filter | Example | Matches |
|---|---|---|
| `--hours N` / `--days N` | `--days 7` | The last N hours or days (default: 24 hours) |
| `--from` / `--to` | `--from 2026-11-01 --to 2026-11-20` | A date range in UTC. A date alone for `--to` means the end of that day. Times work too (`'2026-11-01 14:30'`), and so do relative starts (`--from 6h`, `2d`, `1w`) |
| `--user`, `-u` | `--user alice` | Part of a user, role or session name, or ARN (`--user :root` for the root user) |
| `--event`, `-e` | `-e UpdateService -e CreateService`, `-e 'Delete*'` | API calls, exact (fast) or with `*` wildcards. Repeat the option or separate with commas |
| `--service`, `-S` | `--service iam` | One service: `ec2`, `iam`, `s3`, `ecs`, or the full `ecs.amazonaws.com` |
| `--resource`, `-r` | `--resource sg-0abc123` | A resource id, name or ARN |
| `--resource-type` | `--resource-type AWS::ECS::Service` | A resource type |
| `--access-key` | `--access-key AKIA...` | One access key id |
| `--source-ip` | `--source-ip 203.0.113.0/24` | An IP address, or every address in a CIDR range |
| `--error` | `--error 'AccessDenied*'` | Error codes, exact or with `*` |
| `--errors-only` | `--errors-only` | Only calls that failed |
| `--reads` | `--reads all` | `writes` (the default), `reads` (Get, List, Describe), or `all`. Sign-ins and permission errors usually need `all` |
| `--region` | `--region eu-west-1` | Only events in one region |
| `--include-noise` | `--include-noise` | Keep routine calls that are left out by default: role assumptions, KMS encrypt/decrypt, log delivery, SSM agent calls |
| `--preset`, `-P` | `--preset deletes` | Ready-made filters (below) |

Search options:

| Option | Default | What it does |
|---|---|---|
| `--source` | `auto` | `auto`, `tailpipe`, `cloudwatch` or `lookup` |
| `--max-wait` | 60 seconds (`cloudtrail.max_wait_s`) | A slow search stops here and shows what it found, with how far back it got |
| `--limit` | 500 (`cloudtrail.max_rows`) | The most events to return |
| `--show-sql` | off | Prints every query that was run |

## Presets

`spendlens cloudtrail presets` lists them. Add any filter to narrow one down.

| Preset | Finds | Filters |
|---|---|---|
| `console-logins` | Console sign-ins, failed ones included | `ConsoleLogin`, reads and writes |
| `deletes` | Anything deleted or terminated | `Delete*`, `Terminate*`, `Remove*`, `Deregister*` |
| `deployments` | ECS, Lambda, CodeDeploy and CloudFormation deployments | `UpdateService`, `CreateService`, `UpdateFunctionCode`, `UpdateFunctionConfiguration`, `PublishVersion`, `CreateDeployment`, `CreateStack`, `UpdateStack`, `ExecuteChangeSet` |
| `security-groups` | Security group rules opened, closed or changed | `Authorize*`, `Revoke*`, `CreateSecurityGroup`, `DeleteSecurityGroup`, `ModifySecurityGroupRules` |
| `iam` | IAM changes: users, roles, policies, keys | the IAM service (recorded in us-east-1) |
| `access-denied` | Calls refused for missing permissions | error `AccessDenied*`, `*UnauthorizedOperation`, `*Unauthorized*` |
| `errors` | Every failed call | errors only, reads and writes |
| `root` | Anything done as the root user | user `:root`, reads and writes |

```bash
spendlens cloudtrail events --preset deployments --days 7 --service ecs
spendlens cloudtrail events --preset access-denied --hours 6 --user ci-deployer
spendlens cloudtrail events --preset security-groups --days 30 -o sg-changes.csv
```

## Recipes

```bash
# Who changed this security group, and when?
spendlens cloudtrail events --resource sg-0abc123 --days 90

# Everything alice did between two dates, reads included, to Excel
spendlens cloudtrail events --user alice --from 2026-11-01 --to 2026-11-15 --reads all -o alice.xlsx

# Which roles hit AccessDenied today, and on which calls?
spendlens cloudtrail summary --preset access-denied --hours 24

# Calls from outside the corporate range
spendlens cloudtrail events --source-ip 203.0.113.0/24 --reads all --days 7

# Is this access key still used, and by whom?
spendlens cloudtrail events --access-key AKIAEXAMPLE --reads all --days 30

# Lambda deploys this month
spendlens cloudtrail events --event UpdateFunctionCode --days 30

# What did the root user do?
spendlens cloudtrail events --preset root --days 90
```

## Summaries

`spendlens cloudtrail summary` takes the same filters and shows breakdowns by who, API call, service, source IP, error code and day. As an Excel file, each breakdown gets its own sheet, followed by a sheet of the events:

```bash
spendlens cloudtrail summary --days 7
spendlens cloudtrail summary --preset deletes --days 30 -o deletes.xlsx
spendlens cloudtrail summary --days 7 -f json                         # for scripts
```

## Output and export

| Format | How | Notes |
|---|---|---|
| Table | default | Time, who, event, service, region, source IP, error |
| CSV / TSV | `-o events.csv` or `-f csv` | Every column |
| Excel | `-o events.xlsx` | Needs `-o`; bold header, frozen first row |
| JSON / JSON Lines | `-o events.json`, `-o events.jsonl` | JSON Lines suits log tools and `jq` |
| Markdown / HTML | `-o events.md`, `-o events.html` | For tickets, wikis and email |
| YAML | `-o events.yaml` | |

The format comes from the `-o` extension, or from `-f`. Exported columns: `event_time`, `who`, `event_name`, `event_source`, `region`, `source_ip`, `error_code`, `error_message`, `read_only`, `resources`, `request`, `user_agent`, `access_key`, `event_id`.

`spendlens changes` is the short form for write events, with the same sources and limits:

```bash
spendlens changes --days 7 --service ec2
```

## With the AI assistant

Ask in plain English. The assistant has a `cloudtrail_events` tool with every filter and preset above. It answers with names, API calls and times, and can write the events to a file.

```bash
spendlens ask "What changed in production today, and who did it?"
spendlens ask "Who opened port 22 on any security group this month?"
spendlens ask "Show failed console logins in the last 7 days with source IPs"
spendlens ask "Which roles got AccessDenied in the last 6 hours, and on which calls?"
spendlens ask "Everything ci-deployer did between 1 and 20 November, as Excel"
spendlens ask "Is access key AKIAEXAMPLE still used? By whom?"
```

How the assistant uses it:
- **It turns the question into filters.** For example, "deployed" becomes the `UpdateService` event, "failed logins" becomes the console-logins preset with errors only, and "since Monday" becomes `from`.
- **It shows its queries.** With `--show-tools` you see each call, the SQL it ran, and the first rows.
- **It exports everything it found.** Ask for "as Excel" or "to CSV" and it writes every matching event (up to `cloudtrail.max_rows`) to `./spendlens-output` (`--out-dir` to change), not just the few rows it read itself.
- **It says when an answer is partial,** and how far back it covered.
- **"Which service was deployed last?" uses the current state first.** It reads ECS service deployments or Lambda's last-modified time, which needs no CloudTrail. CloudTrail is used only when you ask who did it.

```bash
spendlens ask --show-tools "Which ECS service was deployed last, and who deployed it?"
```

The assistant can also write its own SQL on the three CloudTrail tables for questions the tool can't express. The same safety rules apply to it: read-only queries, a time range, one LookupEvents filter, and no `ORDER BY` on LookupEvents.

## Configuration

```yaml
cloudtrail:
  log_group: null              # the trail's CloudWatch Logs group; null = found from the trail
  log_group_region: null       # e.g. eu-west-1 (default: from the trail)
  discover_log_group: true
  max_wait_s: 60               # stop a slow AWS search and show what it found
  max_rows: 500                # events per search (exports: --limit)
```

Set `log_group` when the trail's log group lives somewhere discovery can't see, or to pick one of several.

## Permissions

All read-only:
- **LookupEvents:** `cloudtrail:LookupEvents`.
- **Trails:** `cloudtrail:DescribeTrails` and `GetTrailStatus`.
- **CloudWatch Logs:** `logs:FilterLogEvents` on the trail's log group.
- **Tailpipe:** `s3:GetObject` on the trail's bucket, for collecting.

`ReadOnlyAccess` covers them all. See [IAM permissions](../permissions/).

## When something goes wrong

| What you see | What to do |
|---|---|
| "still reading after 60s", partial | Narrow it with an exact `--event`, `--resource` or `--user`; raise `--max-wait`; or collect the trail's logs with Tailpipe |
| No events for an IAM change | IAM, STS and other global services are recorded in us-east-1: add it to `aws.regions`, or use a multi-region trail through Tailpipe or CloudWatch Logs |
| `--user alice` finds nothing in LookupEvents | LookupEvents matches the exact user or session name. Try the full name (`alice@example.com`), or a role name |
| No S3 object reads or writes | Those are data events: the trail needs data event selectors, and they come only from Tailpipe or CloudWatch Logs, never from LookupEvents |
| CloudWatch Logs "could not be read" | The log group is in another account, or the role lacks `logs:FilterLogEvents`; spendlens used LookupEvents instead |
| "the AWS SSO session has expired" | `aws sso login --profile <profile>` |
