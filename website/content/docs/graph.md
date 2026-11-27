# Resource graph & cleanup groups

Most waste checks look at one resource at a time: "this volume is attached to a stopped instance". The resource graph shows the whole picture: the stopped instance, both its volumes, its Elastic IP and the snapshots of those volumes, linked together, with one total saving for the group.

spendlens reads how your resources connect, read-only, and saves the result to its history:
- **`graph orphans`** finds **cleanup groups**: connected resources that can be removed together.
- **`graph show`** draws any resource with everything it connects to, and what the group costs.
- **`graph impact`** says what would break, or be left behind, if one resource were deleted.
- **`graph owners`** suggests owner tags for untagged resources, from what they are attached to.
- **`graph traffic`** prices data transfer from VPC flow logs: cross-AZ, through NAT gateways, out to the internet.
- **`graph age`** shows how old resources are and what they cost.

The relationships follow the [Steampipe AWS Insights mod](https://hub.powerpipe.io/mods/turbot/aws_insights)'s relationship graphs, collected for the whole account at once. spendlens adds the monthly cost of every resource and the waste alarms about it.

## Cleanup groups

```bash
spendlens graph orphans                      # collect now (read-only), then list the groups
spendlens graph orphans --source last        # the saved graph, no AWS calls
spendlens graph orphans -f html -o cleanup.html
spendlens graph orphans --source demo        # try it on the bundled sample data
```

| Group | What is in it | Saving |
|---|---|---|
| Stopped instance | An instance stopped for at least `graph.stopped_days` (14), with its volumes, its Elastic IPs and the snapshots of those volumes that no AMI uses | Volumes + IPs + snapshots (a stopped instance itself is not billed) |
| Load balancer with no targets | A load balancer whose target groups are empty, with those target groups | The load balancer's hourly charge |
| Unused AMI | An AMI no instance was launched from, older than `graph.ami_unused_days` (90), with the snapshots behind it | Its snapshots |
| Snapshots of a deleted volume | Snapshots whose volume is gone, older than `graph.snapshot_orphan_days` (30), not used by any AMI | The snapshots (an upper bound: snapshots are incremental) |
| Unattached volume | A volume that isn't attached to any instance | The volume |
| Unassociated Elastic IP | An Elastic IP not associated with anything | The hourly IP charge |
| Snapshots of a deleted database | Manual RDS snapshots of a database that no longer exists, older than `graph.rds_snapshot_orphan_days` (30) | The snapshots |

Rules that prevent double counting:
- **One group per resource.** Each resource joins at most one group, so no saving is counted twice.
- **Waste alarms are listed, not added.** Waste alarms about a group's resources are shown with it ("already flagged by waste"), and the summary line says how much of the total is new.
- **A rule only runs on complete data.** If a source couldn't be read (for example no permission to list volumes), the rules that depend on it are switched off. Otherwise every snapshot would look orphaned.

Every group comes with the evidence, the action and what to check first, for example "Check that no DNS record still points at it".

> spendlens never deletes anything. A cleanup group is a proposal for a person to review with the resource's owner.

## Explore a resource

```bash
spendlens graph show i-0abc123              # a tree in the terminal
spendlens graph show payments-api -d 3 -o payments.html
spendlens graph show vol-0abc123 -o vol.svg  # or .mmd (Mermaid), .dot (Graphviz), .json
spendlens graph impact ami-0abc123
spendlens graph stats                        # what was collected, what was skipped and why
```

You can name a resource by ARN, by AWS id (`i-...`, `vol-...`, `snap-...`, `ami-...`, `eipalloc-...`) or by its `Name` tag. If several resources match, spendlens lists them so you can pick one.

**The HTML page** draws the resource and everything within `--depth` links of it:
- Each node shows its type, name and monthly cost.
- Orange nodes are in a cleanup group; red nodes have a failing check.
- Dashed lines are references, where each side lives on without the other (a snapshot and the volume it came from).
- Hovering a node highlights its links.

Subnets and VPCs are shown but not walked through, so unrelated resources in the same subnet stay out.

**`graph impact`** sorts the effects of deleting a resource into three lists:
- what breaks or must be removed first (the instance that mounts this volume);
- what would be left unused (the volumes of an instance you terminate);
- what stays (the snapshots taken from a volume).

## Suggested owners

An untagged volume belongs to whoever owns the instance that mounts it. `graph owners` suggests the missing tags of every untagged resource from what it is attached to:
- the tags in your policy (`tagging.mandatory`);
- the usual owner tags (`Owner`, `Team`, `Service Owner`, `CostCentre` and your showback keys).

```bash
spendlens graph owners
spendlens graph owners --min-confidence high -f csv -o owners.csv
spendlens graph owners --script tag-review.sh      # commands for a person to review and run
spendlens graph owners --source demo
```

| Confidence | Where the value comes from |
|---|---|
| high | Directly attached or managed: a volume from the instance that mounts it, a network interface or Elastic IP from its instance or NAT gateway, an instance from its Auto Scaling group, a target group from its load balancer, an RDS instance from its cluster |
| medium | One step removed: a snapshot from its volume (or that volume's instance), a snapshot behind an AMI from the AMI, an RDS snapshot from its database, an AMI from the instances launched from it |
| medium | Opt-in: who created it, from CloudTrail, for the owner tag only (see below) |
| low | The subnet: the value that most tagged resources in the same subnet carry (at least 3, at least 60% agreeing) |

How a value is chosen:
- **Each missing tag gets the strongest rule that finds a value.**
- **Related resources must agree.** If a volume is attached to instances of two different teams, nothing is suggested.
- **Spelling is copied.** The key is spelled as on the resource the value comes from.
- **AWS-managed network interfaces are skipped.**

The same suggestions appear in three places:
- a **suggested** column in `spendlens tags`, for its missing-tag alarms;
- as "(suggested)" owners in the review queue and on cleanup groups;
- in a **Suggested tags** card in the review.

These all come from the saved graph, with no extra AWS calls.

**Who created it (CloudTrail, opt-in).** With `graph.owner_from_cloudtrail: true`, `graph collect` also asks CloudTrail who created each resource. It runs one LookupEvents query per creation event (RunInstances, CreateVolume, CreateSnapshot, CreateDBInstance and others, 20 in all), each bounded to the last `graph.cloudtrail_days` days. The creator is suggested for your owner tag (`graph.cloudtrail_owner_key`, default `Owner`) with medium confidence, and only when nothing attached already names an owner.

```bash
spendlens graph collect --set graph.owner_from_cloudtrail=true
spendlens graph owners -k Owner
```

It is off by default, for three reasons:
- **Speed:** LookupEvents allows about two calls a second, so it adds time to every collection in a busy account.
- **History:** it only knows resources created in the last 90 days, because that is all LookupEvents keeps.
- **Permission:** it needs `cloudtrail:LookupEvents`. Without it, the step is skipped with a note and the rest of the collection runs.

> spendlens never applies tags. `--script` writes `aws resourcegroupstaggingapi tag-resources` commands, one per resource, each with its reason as a comment. Low-confidence lines are commented out. Review the script before you run it with a role that is allowed to tag.

## Resource age

Old is not wrong, but old and costly is worth a look: a two-year-old snapshot, an instance launched in 2023 that nobody touches. `graph age` groups the saved graph's resources by age and shows:
- the monthly cost of each age band and each type;
- the costliest resources, with their owner (or a suggested one) and their cleanup group.

```bash
spendlens graph age
spendlens graph age --min-days 365
spendlens graph age -t ebs_snapshot -t ec2_ami --min-days 180 -f csv -o old.csv
spendlens open age
```

Ages come from each resource's own date: creation, launch or snapshot time. The API has no creation date for two types, which use other dates:
- **Lambda functions:** age since their last change.
- **Network interfaces:** age since they were attached.

Subnets, Elastic IPs and target groups have no date at all and are counted separately.

## Find one resource, open a page

`spendlens find` searches everything saved for a resource id, ARN, Name or tag value:
- its findings from every check;
- its cost and where the figure came from;
- what it is linked to;
- its cleanup group and suggested tags.

`spendlens open` builds a page from the saved runs and opens it in your browser: `review` (the default), `dashboard`, `brief`, `cleanup` or `traffic`.

Neither command calls AWS.

```bash
spendlens find vol-0abc123
spendlens find payments          # every resource tagged payments
spendlens open cleanup
```

When a saved graph exists, `waste` and `rightsize` end their table output with a hint: which of their alarms belong to cleanup groups, and a `graph show` command for one of them.

## Shared costs

A NAT gateway or a load balancer serves many resources. `graph show` splits its hourly charge equally:
- **NAT gateway:** across the subnets that route through it, read from the route tables (the main table counts for the subnets without one of their own);
- **load balancer:** across its target groups;
- **classic load balancer:** across its instances.

This is an allocation estimate, labelled as one. Data processing charges are not in the price book.

## Data transfer (VPC flow logs via Tailpipe)

NAT gateway processing, cross-AZ traffic and internet egress are hard to see in the bill. `graph traffic` reads your VPC flow logs with [Tailpipe](https://tailpipe.io) (the AWS plugin's `aws_vpc_flow_log` table), matches each flow to the resource that sent it, and prices the path:

```bash
spendlens graph traffic                          # last 7 days of flow logs (graph.flow_days)
spendlens graph traffic --path cross_az
spendlens graph traffic --source demo -f html -o traffic.html
```

| Path | How it is recognised | Price (finops.yaml `pricing:`) |
|---|---|---|
| Same AZ | Another collected interface in the same AZ | Free |
| Cross-AZ | Another collected interface in a different AZ | `cross_az_gb` on each side ($0.02/GB in all) |
| Gateway endpoint | S3 or DynamoDB, from a VPC with a gateway endpoint for it | Free |
| Through a NAT gateway | A public address, from a subnet whose route table points at a NAT gateway | `nat_gateway_gb` in and out, plus `internet_egress_gb` out (not to S3 or DynamoDB) |
| Straight to the internet | A public address, from a subnet without a NAT route | `internet_egress_gb` out |
| Inside the VPC (not collected) | A private address spendlens did not collect, such as peering or an RDS interface | Not priced |

How the numbers are counted:
- **Custom flow-log formats.** When your flow logs include the extra fields (`pkt-src-addr`, `pkt-dst-addr`, `pkt-src-aws-service`, `pkt-dst-aws-service`), spendlens uses the service AWS itself names, and the original packet addresses for traffic through a middlebox. With the default format it falls back to address matching.
- **Recognising S3 and DynamoDB.** Their addresses come from AWS's public `ip-ranges.json`, bundled with spendlens, unless the flow log names the service.
- **Endpoint coverage.** A gateway endpoint counts only for subnets whose route table it is associated with (read from the route tables).
- **Counted at the sender.** Traffic between two collected resources is counted once, by the one that sent it. A NAT gateway's own interface is skipped, because its traffic is already counted at the resource behind it.
- **Monthly rates.** Costs are monthly rates from the window.

**Proposals.** Where S3 or DynamoDB traffic goes through a NAT gateway, spendlens proposes one of two fixes:
- **Add an endpoint:** the VPC has no gateway endpoint for that service.
- **Associate an endpoint:** the VPC has one, but the sending subnet's route table doesn't use it, so the proposal names the route table to add it to. Gateway endpoints for S3 and DynamoDB are free, so the NAT processing charge on that traffic goes away. The proposal appears in `graph traffic`, in the review's **Data transfer** section and in its queue.

**Requirements:**
- Tailpipe with the AWS plugin, with the flow logs collected (`tailpipe collect aws_vpc_flow_log`);
- a saved resource graph (`spendlens graph collect`), to match addresses to resources.

spendlens itself makes no AWS calls here. The analysis is saved, so `--source last` replays it, and the AI assistant answers "why is NAT so expensive?" from it with its `traffic_paths` tool.

## Ask about it

The AI assistant has four tools on the saved graph and traffic analysis:
- `resource_context`: what a resource connects to, what it costs, its cleanup group, its owner, and what deleting it would affect;
- `cleanup_groups`;
- `suggested_owners`;
- `traffic_paths`.

Answers saved with `-o` draw the resources they talk about:

```bash
spendlens ask "What can we clean up in eu-west-1, and who owns it?" -o cleanup-answer.html
spendlens ask "Why does prod-nat cost so much, and which subnets use it?"
```

## In the review

`spendlens review` has a **Cleanup groups** section with a small graph of each group. Each group is one item in the review queue and replaces the waste alarms it includes. The queue's total is every saving, counted once.

`spendlens report` collects the graph as one of its steps. Use `review --skip graph` to leave the section out.

## What is collected

There is one read-only query per type:
- EC2 instances, EBS volumes and snapshots, AMIs, network interfaces, Elastic IPs and NAT gateways;
- VPCs, subnets, route tables and VPC endpoints;
- application, network, gateway and classic load balancers, target groups and Auto Scaling groups;
- RDS instances, clusters and snapshots;
- ECS clusters and services (with their task definitions);
- Lambda functions.

Each query is checked against the AWS plugin's schema in the test suite.

Each resource's cost comes from the best source available. See [Billed costs](#billed-costs-cur-via-tailpipe) below.

| Setting | Default | Meaning |
|---|---|---|
| `graph.stopped_days` | 14 | A stopped instance older than this starts a group |
| `graph.snapshot_orphan_days` | 30 | Snapshots of a deleted volume older than this |
| `graph.ami_unused_days` | 90 | AMIs nobody launched from, older than this |
| `graph.rds_snapshot_orphan_days` | 30 | Manual snapshots of a deleted database older than this |
| `graph.max_nodes` | 150 | Resources drawn in one picture |
| `graph.cost_source` | `auto` | `auto`: the CUR via Tailpipe, else Cost Explorer resource data, else the price book. `cur`: the CUR only. `estimate`: the price book only |
| `graph.cur_days` | 30 | The CUR window: full days before today |
| `graph.tailpipe_path` | `tailpipe` | The Tailpipe CLI |
| `graph.flow_days` | 7 | The VPC flow log window for `graph traffic` |
| `graph.owner_from_cloudtrail` | `false` | Opt-in: record who created each resource (CloudTrail LookupEvents) as an owner hint |
| `graph.cloudtrail_days` | 90 | How far back to look for creation events (90 at most) |
| `graph.cloudtrail_owner_key` | `Owner` | The tag the creator is suggested for |
| `pricing.nat_gateway_gb` · `cross_az_gb` · `internet_egress_gb` | 0.045 · 0.01 · 0.09 | Data transfer prices (USD per GB) |

## Billed costs (CUR via Tailpipe)

By default a resource's monthly cost is an estimate from the `pricing:` price book in `finops.yaml`, the same numbers the savings estimates use. Snapshot estimates are an upper bound, because snapshots are incremental. When spendlens can see what a resource was actually billed, it uses that instead. `graph collect` tries these in order:

| Source | What it is | Needs |
|---|---|---|
| **CUR** | Your [Cost and Usage Report](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2.html), queried with [Tailpipe](https://tailpipe.io): what each resource was billed over the last `graph.cur_days` days | Tailpipe with its AWS plugin, and the `aws_cost_and_usage_report` table collected |
| **Cost Explorer** | Resource-level data saved by `spendlens cost resources` (the last window, up to 7 days, from a run in the last 14 days) | Resource-level data turned on in Cost Explorer |
| **Estimate** | The price book | Nothing |

How billed costs are used:
- **Monthly rate.** A billed cost becomes a monthly rate from the days the resource was actually billed. A volume created five days ago is not under-counted.
- **Gaps keep their estimate.** Resources the bill doesn't name keep their estimate. Each one shows its source: `(CUR)` in the terminal tree, a Basis column on the page, and `cost_basis` in JSON and for the AI.
- **Groups follow.** Cleanup groups and their savings use the billed figures.

Set up the CUR once:

```bash
# 1. In the AWS Billing console, create a CUR 2.0 export to S3 with resource IDs included.
# 2. Tailpipe and its AWS plugin come with spendlens. Find the export (and your CloudTrail and flow log buckets)
#    and collect them:
spendlens tailpipe init       # read-only; needs AWS CLI 2.15+ to find CUR 2.0 exports, or set tailpipe.cur in finops.yaml
spendlens tailpipe collect     # see the Tailpipe page for the full setup
# 3. Check that spendlens can see it, then collect the graph:
spendlens doctor              # "tailpipe: Cost and Usage Reports: N rows"
spendlens graph collect       # "N resources priced from the CUR (Tailpipe)"
```

Tailpipe reads the CUR files it has already collected. spendlens runs one `tailpipe query` and makes no AWS calls of its own. The query sums `line_item_unblended_cost` per `line_item_resource_id`, over full days before today.

## Permissions

These are all covered by `ReadOnlyAccess`. With a narrower role, the graph needs:
- **EC2:** `ec2:DescribeInstances`, `ec2:DescribeVolumes`, `ec2:DescribeSnapshots`, `ec2:DescribeImages`, `ec2:DescribeNetworkInterfaces`, `ec2:DescribeAddresses`, `ec2:DescribeNatGateways`, `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeRouteTables`, `ec2:DescribeVpcEndpoints`;
- **Load balancing:** `elasticloadbalancing:Describe*`;
- **Auto Scaling:** `autoscaling:DescribeAutoScalingGroups`;
- **RDS:** `rds:DescribeDBInstances`, `rds:DescribeDBClusters`, `rds:DescribeDBSnapshots`, `rds:DescribeDBClusterSnapshots`;
- **ECS:** `ecs:ListClusters`, `ecs:DescribeClusters`, `ecs:ListServices`, `ecs:DescribeServices`;
- **Lambda:** `lambda:ListFunctions`;
- **Only with `graph.owner_from_cloudtrail`:** `cloudtrail:LookupEvents`.

A missing permission skips that one type, with a note, and never fails the run.
