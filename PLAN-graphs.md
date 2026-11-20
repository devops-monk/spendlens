# spendlens 0.4 plan: cost relationship graphs ("follow the money")

## Status
- **Phases 1 and 2 are built** (unreleased).
  - Phase 1: graph collection, history tables, `graph collect | show | stats`.
  - Phase 2: `graph orphans | impact`, the review's "Cleanup groups" section and its queue items, and the `report` step.
- **Two deviations from the plan below:**
  1. Edges come from **one query per resource type** (23 queries) instead of one query per edge. Each row's own columns give its links, which is fewer AWS calls for the same relationships.
  2. ECS task definitions without a service are not a cleanup group, because they cost nothing.
- **Phase 3 is built:**
  - `graph owners`: high, medium and low confidence rules, plus a review script with low-confidence lines commented out;
  - a "suggested" column in `tags`;
  - "(suggested)" owners in the review queue and on cleanup groups, and a "Suggested tags" card in the review.
- **Not built yet:** the CloudTrail creator rule waits on open question 2, and the showback owner map is folded into the review card.
- **Next:** phase 4 (AI tools).

## Context

The AWS Open Source blog post [Using Steampipe Relationship Graphs to Navigate Cloud Resources on AWS](https://aws.amazon.com/blogs/opensource/using-the-new-relationship-graph-capabilities-in-open-source-steampipe-on-aws/) (Turbot and AWS, June 2023) shows the graphs that `steampipe-mod-aws-insights` draws for one resource. For example, VPC → subnets → instance → ENI, instance → EBS volume, IAM role, key pair, and IAM user → access keys and policies. The graphs answer three kinds of question:
- **Relationships and dependencies:** "What does this load balancer target?"
- **Topology:** "Is the RDS instance in the database subnet?"
- **Identity:** "What uses this role?"

The insights mod is in this workspace (`../steampipe-mod-aws-insights`, Apache-2.0). It has 136 `node` and 287 `edge` definitions. Each edge is one SQL query that returns `from_id, to_id`, for example `ec2_instance_to_ebs_volume`, `ebs_volume_to_ebs_snapshot`, `vpc_nat_gateway_to_ec2_network_interface`, `ecs_service_to_ecs_task_definition` and `ec2_target_group_to_ec2_instance`.

**What insights does not do:**
- It shows no cost.
- It shows no findings.
- It has no owners.
- It cannot answer "what can I delete and what will it save".

These are spendlens's strengths. spendlens today judges resources one at a time: an unattached volume, an idle load balancer, an untagged instance. Real waste and real ownership come in **groups of connected resources**:
- A stopped instance still pays for its volumes, its Elastic IP and the snapshots of those volumes.
- An untagged volume belongs to the team whose instance it is attached to.
- A NAT gateway's cost belongs to the workloads in the subnets that route through it.

**Goal:** add a resource graph to spendlens with **cost, findings and owners on every node**. Use it to answer FinOps questions that single-resource checks can't:
1. What does this cost, together with everything attached to it?
2. Which groups of resources can be cleaned up together, and for how much saving?
3. Who owns this untagged resource?
4. Is it safe to delete, and what depends on it?

This must work in the CLI, the HTML reports and the AI assistant, and spendlens stays read-only.

## Other repos in this workspace, checked for graphs

| Repo | Graphs? | What spendlens can use |
|---|---|---|
| `steampipe-mod-aws-insights` | **Yes:** 61 `graph` blocks, about 1,250 `node`/`edge` blocks, 11 `flow` (sankey) charts, e.g. VPC → NACL ingress/egress | The edge SQL for resource relationships (phase 1). The node/edge/category pattern (categories colour nodes) maps directly to our "colour by finding status". |
| `tailpipe-mod-aws-vpc-flow-log-detections` | **Yes:** `dashboards/network_graph.pp`: IP ↔ IP edges from VPC flow logs, labelled with connection counts, with a `high_conn_count` category above 50 | The idea for a **data transfer graph** (section 6): map IPs to ENIs and resources, then weight edges by bytes and label them cross-AZ, NAT or internet, which are the costly paths |
| `tailpipe-mod-aws-cloudtrail-log-detections` | No graphs (detections + activity dashboard) | The `aws_cloudtrail_log` table: **who created a resource** for owner suggestions, with no 90-day limit (better than `LookupEvents`) |
| `tailpipe-mod-aws-cost-and-usage-insights` | No graphs (cost by account, region, service and tag dashboards) | Per-resource cost from CUR (phase 5) |
| `steampipe-mod-aws-thrifty` | No | Already the base of our waste/rightsize controls; its single-resource checks become the members of cleanup groups |
| compliance, perimeter, tags, top-10, well-architected, terraform-aws-compliance, gitlab-insights | No | Nothing graph-related. Perimeter's "shared with untrusted accounts" could later colour graph nodes, but that is security, not FinOps, so it is out of scope. |

## Principles (unchanged)
- **Read-only.** spendlens may suggest a deletion or a tag. It never runs one.
- **Collect once, read often.** The graph is saved to history with each run, and reports and `ask` use it offline.
- **Self-contained HTML.** Graphs are inline SVG with a small amount of inline JavaScript, with no CDN at view time, and they work in light and dark.
- **Tests stay offline:** demo data, the fake Steampipe client and the stub LLM. Never real AWS, and never `make test-system` run by the agent.

## What we build

### 1. Graph collection (the base for everything else)

**New `src/spendlens/graph/` package:**
- `edges.py`: a registry of **edge queries**. Each edge has a name, a label, `from_type`, `to_type`, its SQL and the Steampipe tables it needs. The SQL is adapted from the insights mod, with Apache-2.0 credit in the file header and the NOTICE. There is one big change:
  - Insights runs each query for **one** resource (`$1::text[]` ARNs).
  - spendlens collects edges **for the whole account in one pass per edge**, so it can find components such as orphan chains.
  - The SQL is rewritten to scan the table once, bounded by the configured regions.
- `nodes.py`: node types and how to read each one's name, state, tags, account, region and creation time.
- `collect.py`: runs the edges through the existing read-only `SteampipeClient`, in parallel up to the existing limit. Each edge query is checked by `ai/sql_check.py` against the catalog in CI. Missing permissions or tables skip that edge with a note and never fail the run.

**First set of node types (about 20):** EC2 instance, EBS volume, EBS snapshot, AMI, ENI, Elastic IP, NAT gateway, ALB/NLB/CLB, target group, Auto Scaling group, VPC, subnet, VPC endpoint, RDS instance, RDS cluster, RDS snapshot, ECS cluster, ECS service, ECS task definition, Lambda function, and EKS cluster/node group. These are the types that cost money, or that drive the cost of their neighbours. Security and IAM nodes stay out of version 1, except instance profiles as an owner hint.

**First set of edges (about 30):** instance → volume, volume → snapshot, snapshot → AMI, AMI → instance, instance → ENI, ENI → EIP, NAT gateway → ENI and subnet, subnet → VPC, VPC → endpoint, LB → target group → instance, ASG → instance, ECS cluster → service → task definition, service → target group, RDS cluster → instance → snapshot, and Lambda → subnet.

**History:**
- `graph_nodes(run_id, id, type, name, account, region, state, tags json, created, monthly_cost, cost_basis)`
- `graph_edges(run_id, from_id, to_id, label)`

`cost_basis` records where the number came from:
- `estimate`: the existing price estimators;
- `cost_explorer`: resource-level cost, if the account has opted in (14 days);
- `cur`: from the CUR / Tailpipe path in phase 5;
- `none`.

The feature name is `graph`. It is collected by `spendlens graph collect` and by `report`, and the dashboard and review keep it fresh "at most a day old", like the other features.

**Demo data:** add a realistic graph to `demo.db` so everything works with `--source demo`:
- a stopped instance with 2 volumes, an EIP and 6 snapshots;
- a NAT gateway shared by 3 subnets;
- an ECS service chain;
- an AMI with its snapshots;
- untagged volumes attached to tagged instances.

### 2. `spendlens graph` commands (offline once collected)

| Command | Answers | Output |
|---|---|---|
| `graph show <id\|arn\|name> [--depth 2]` | "What is this connected to, and what does the whole group cost?" | Terminal tree; `-o x.html` interactive SVG; `.svg`, `.mmd` (Mermaid), `.dot`, `.json` |
| `graph orphans` | "Which groups can be cleaned up together?" | Ranked **cleanup groups** with total monthly saving |
| `graph owners` | "Who owns this untagged resource?" | Suggested `Team`/`Owner` per untagged resource, with the reason and a confidence |
| `graph impact <id>` | "If I delete this, what depends on it?" | What depends on it, what becomes orphaned, and the saving |
| `graph stats` | Coverage | Nodes and edges per type, edges skipped (with the missing IAM action) |

**Cleanup groups (`graph orphans`)** go beyond single-resource waste checks. Each group carries one total saving, a risk note and the read-only evidence:
- **Stopped instance group:** instance (stopped more than N days) + its volumes + EIP + snapshots of those volumes.
- **Snapshots of deleted volumes:** the volume is gone, and the snapshots are not used by any AMI.
- **AMI group:** an AMI not used by any instance, launch template or ASG, plus the snapshots behind it.
- **Load balancer group:** an LB whose target groups have no healthy targets, plus those target groups.
- **ENI and EIP:** ENIs not attached to anything, and EIPs not associated.
- **ECS:** task definitions with no service.
- **RDS:** manual snapshots of deleted RDS instances or clusters.

The thresholds live in `finops.yaml` under a new `graph:` section (for example `stopped_days: 14`, `snapshot_orphan_days: 30`), and `--set` works as usual. The existing waste findings are linked in, so nothing is counted twice: a group lists the waste findings it includes.

**Owner suggestions (`graph owners`).** For an untagged resource, the rules are applied in order, and each suggestion gives its reason:
1. Inherit from a directly attached parent: volume → instance, ENI → instance, snapshot → its volume.
2. The ASG or ECS service it belongs to.
3. The most common owner among the other resources in the same subnet.
4. Optional: the CloudTrail creator. It is read from **Tailpipe CloudTrail logs** (`aws_cloudtrail_log`, any age) when Tailpipe is set up, or else from `aws_cloudtrail_lookup_event` (90 days). Turned on with `graph.owner_from_cloudtrail: true`.

The output is CSV or JSON, plus an optional **review script**: `aws resourcegroupstaggingapi tag-resources …` commands for a person to check and run. spendlens never runs them. `tags` and `review` show "suggested owner" next to each untagged finding.

**Shared costs (in `graph show` and the review).** A NAT gateway's cost is split across the subnets that route through it, and an ALB's cost across its target groups. The split is equal per routed subnet, or per target when that is known. This is labelled clearly as an allocation estimate, not a measured figure.

### 3. Graphs in the HTML reports
- **Review and dashboard:** a new **"Cleanup groups"** section shows the top 10 groups. Each has a small inline SVG graph, the total saving, the risk and the evidence. Each group can go into the existing review queue (approve, defer or reject) as one item.
- **Resource drill-down:** clicking any finding's resource opens a side panel. The panel shows its neighbours graph (depth 1–2), the cost of the group, tags with the suggested owner, and every finding on those nodes. The data is embedded as JSON in the page, so it works offline.
- **Showback:** an optional **Owner map** graph: team → service → top resources by cost, with untagged resources shown as "suggested: payments (inherited from i-0abc)".

**Rendering:**
- A small layered layout computed in Python (layers by node type: VPC → subnet → compute → storage → snapshot), drawn as inline SVG.
- Nodes are sized by monthly cost and coloured by worst finding status (alarm, info, ok). Edges carry their labels.
- Inline JS adds pan, zoom, hover and click to expand. There are no external libraries.
- The same SVG prints in PDF and works in light and dark.
- *Considered:* Cytoscape.js inlined (about 400 KB per page). Rejected because reports must stay small and print well. We can revisit this for a later local server mode.

### 4. AI assistant: the graph as tools
New history tools (offline and free, work without `--live`):

| Tool | Returns |
|---|---|
| `resource_context(id)` | The node, its neighbours to depth 2, the group cost, tags plus the suggested owner, and the findings on the group |
| `cleanup_groups(service?, min_saving?)` | The ranked cleanup groups |
| `graph_query(sql)` | A read-only `SELECT` over `graph_nodes` / `graph_edges` (same SQL guard) |

Example questions this answers in one or two steps:
- "What is vol-0abc attached to and who owns it?"
- "What can I clean up in uat for more than $100/month?"
- "If I delete this AMI, what breaks?"
- "Why does the NAT gateway cost so much? Which subnets use it?"

**Answer pages:** `ask -o answer.html` renders a fenced ```` ```mermaid ```` block (or a `graph:` block that spendlens fills from history) as the same inline SVG. The prompt rule "Never write HTML" stays. The model may emit a Mermaid graph of the resources it cites.

### 5. Optional: precise per-resource cost (CUR via Tailpipe)
Cost Explorer resource-level cost is opt-in, paid, and covers only 14 days. `../tailpipe-mod-aws-cost-and-usage-insights` reads the **Cost and Usage Report** with Tailpipe (the `aws_cost_and_usage_report` table in DuckDB), which has real per-resource cost.
- A new optional `cost.source: cur` setting. When `tailpipe` is installed and a CUR collection is configured, `graph collect` fills `monthly_cost` from CUR (`cost_basis=cur`). Otherwise it uses the price estimators.
- `doctor` checks Tailpipe and the CUR partition, and `docs/cur.md` explains how to set up a CUR export plus a Tailpipe collection.
- Tests use a small CUR parquet fixture, with no AWS access.

### 6. Optional: data transfer graph (VPC flow logs via Tailpipe)
Inspired by `tailpipe-mod-aws-vpc-flow-log-detections/dashboards/network_graph.pp`. That mod draws IP ↔ IP edges; spendlens turns them into a **cost** graph:
- **Nodes:** each flow-log IP is mapped to its ENI and then to the resource (from the phase 1 graph). Unknown public IPs become "internet", and AWS service ranges (`ip-ranges.json`, bundled) become "S3", "DynamoDB" and so on.
- **Edges:** total bytes, labelled by path: same-AZ (free), **cross-AZ** ($0.01/GB each way), **through a NAT gateway** ($0.045/GB processed), **internet egress**.
- **Answers:**
  - "Why is NAT so expensive?": the top talkers through each NAT gateway.
  - "Should we add an S3/DynamoDB gateway endpoint?": S3 traffic through NAT with no endpoint in the VPC gives a saving estimate.
  - "Which services talk across AZs?"
- **Command:** `spendlens graph traffic [--vpc vpc-x] [-o traffic.html]`. Its cleanup groups gain "add a gateway endpoint" proposals, and the AI gets a `traffic_paths` tool.
- **Needs:** Tailpipe and a flow-log collection. `doctor` checks for them. Tests use a small parquet fixture.

### 7. Small usability wins found along the way
- **`spendlens open`:** open the latest `review.html` / `dashboard.html` in the browser, building it from history first if it is missing.
- **`spendlens find <text>`:** search history for any resource ID, name or tag across findings and the graph. It prints the resource, its findings, its cost and a hint: `graph show <id>`.
- **Links between reports:** a finding in `tags`, `waste` or `rightsize` table output prints a short `graph show` hint for resources that have neighbours.

## Phases (each one shippable, user approval between phases)

| Phase | Ships | Version |
|---|---|---|
| 1 | Graph collection: edges, nodes, history tables, demo graph, `graph collect`, `graph show` (terminal, JSON, Mermaid, SVG, HTML), `graph stats` | 0.4.0 |
| 2 | `graph orphans` cleanup groups, `graph impact`, the review/dashboard "Cleanup groups" section with review-queue items | 0.4.1 |
| 3 | `graph owners`, suggested owners in `tags`, review and showback, the review tag script | 0.4.2 |
| 4 | AI tools (`resource_context`, `cleanup_groups`, `graph_query`), Mermaid in answer pages, NAT/ALB shared cost | 0.4.3 |
| 5 | Optional CUR cost via Tailpipe | 0.5.0 |
| 6 | Optional data transfer graph from VPC flow logs (Tailpipe): cross-AZ, NAT and egress paths, gateway endpoint proposals | 0.5.x |
| 7 | `open`, `find`, report hints (can go into any phase) | any |

## Files
- **New:**
  - `src/spendlens/graph/{__init__,edges,nodes,collect,analyze,render}.py`
  - `src/spendlens/features/graph_cmd.py`
  - `templates/report/graph.html.j2`, plus partials for the review/dashboard sections
  - `tests/unit/test_graph_*.py`, `tests/integration/test_graph_cmd.py`
  - `docs/graph.md`
  - website docs page and an Architecture-page section ("Follow the money")
  - `NOTICE` entry crediting `steampipe-mod-aws-insights`
- **Change:**
  - `history.py` (2 tables plus a migration)
  - `config.py` (the `graph:` section)
  - `plugins.py` (register the feature)
  - `refresh.py` (graph freshness)
  - `features/review.py`, `features/dashboard.py`, `features/tags.py`
  - `ai/tools.py`, `ai/prompts.py`, `render/answer.py` (Mermaid → SVG)
  - `report.py` (collect the graph)
  - `scripts/build_demo_db.py` (the demo graph)
  - `docs/permissions.md`: new read actions, e.g. `ec2:DescribeImages`, `elasticloadbalancing:DescribeTargetHealth`, `ecs:ListTaskDefinitions`, and optionally `cloudtrail:LookupEvents`

## Verification (no real AWS, no system tests by the agent)
- **Edge SQL:**
  - Every edge query passes `ai/sql_check.py` against the bundled catalog (a unit test over the registry).
  - A fake Steampipe client returns fixture rows per table, and the collected graph must match the expected nodes and edges.
- **Analysis:**
  - Unit tests per cleanup-group rule, with no double counting against waste findings.
  - Owner rules in priority order, each with its stated reason.
  - Shared-cost split totals equal the source cost.
- **Rendering:**
  - Layout is deterministic, so snapshot tests of the SVG are stable.
  - The HTML is checked at desktop and 390 px width in light and dark (headless Chrome screenshots, as for `review`).
  - XSS: resource names and tags are escaped, with a test.
- **AI:** StubLLM end-to-end tests with the real OpenAI and Anthropic SDKs. "What is vol-x attached to?" must use `resource_context` and cite the group cost.
- **Demo:** `spendlens graph orphans --source demo` and `review --source demo` show the cleanup groups.
- **CUR:** a parquet fixture through a fake Tailpipe runner.
- **Checks:** `make lint`, full pytest, and `build_site.py --check`.
- **You run the live smoke tests yourself:**
  - `spendlens graph collect && spendlens graph orphans && spendlens graph show <a real instance id> -o g.html`
  - then `make test-system`.

## Open questions for you
1. Start with phase 1 + 2 together (graph + cleanup groups) as 0.4.0, since cleanup groups are the visible payoff?
2. Owner from CloudTrail creator: on by default, or opt-in? It adds `cloudtrail:LookupEvents` and is slow for many resources.
3. Is CUR in place in your accounts? If so, phase 5 is worth doing early, because it makes every node's cost exact.
4. Are VPC flow logs on (to S3 or CloudWatch)? If NAT or data transfer is a big line in your bill, phase 7 may be worth more than phases 3–4.
