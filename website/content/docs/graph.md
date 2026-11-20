# Resource graph & cleanup groups

Most waste checks look at one resource at a time: "this volume is attached to a stopped instance". The resource graph shows the whole picture: the stopped instance, both its volumes, its Elastic IP and the snapshots of those volumes, linked together, with one total saving for the group.

spendlens reads how your resources connect, read-only, and saves the result to its history:
- **`graph orphans`** finds **cleanup groups**: connected resources that can be removed together.
- **`graph show`** draws any resource with everything it connects to, and what the group costs.
- **`graph impact`** says what would break, or be left behind, if one resource were deleted.
- **`graph owners`** suggests owner tags for untagged resources, from what they are attached to.

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

> spendlens never applies tags. `--script` writes `aws resourcegroupstaggingapi tag-resources` commands, one per resource, each with its reason as a comment. Low-confidence lines are commented out. Review the script before you run it with a role that is allowed to tag.

## In the review

`spendlens review` has a **Cleanup groups** section with a small graph of each group. Each group is one item in the review queue and replaces the waste alarms it includes. The queue's total is every saving, counted once.

`spendlens report` collects the graph as one of its steps. Use `review --skip graph` to leave the section out.

## What is collected

There is one read-only query per type:
- EC2 instances, EBS volumes and snapshots, AMIs, network interfaces, Elastic IPs and NAT gateways;
- VPCs, subnets and VPC endpoints;
- application, network, gateway and classic load balancers, target groups and Auto Scaling groups;
- RDS instances, clusters and snapshots;
- ECS clusters and services (with their task definitions);
- Lambda functions.

Each query is checked against the AWS plugin's schema in the test suite.

Costs are estimates from the `pricing:` price book in `finops.yaml`, the same numbers the savings estimates use. A type with no price in the book shows no cost rather than a guess.

| Setting | Default | Meaning |
|---|---|---|
| `graph.stopped_days` | 14 | A stopped instance older than this starts a group |
| `graph.snapshot_orphan_days` | 30 | Snapshots of a deleted volume older than this |
| `graph.ami_unused_days` | 90 | AMIs nobody launched from, older than this |
| `graph.rds_snapshot_orphan_days` | 30 | Manual snapshots of a deleted database older than this |
| `graph.max_nodes` | 150 | Resources drawn in one picture |

## Permissions

These are all covered by `ReadOnlyAccess`. With a narrower role, the graph needs:
- **EC2:** `ec2:DescribeInstances`, `ec2:DescribeVolumes`, `ec2:DescribeSnapshots`, `ec2:DescribeImages`, `ec2:DescribeNetworkInterfaces`, `ec2:DescribeAddresses`, `ec2:DescribeNatGateways`, `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeVpcEndpoints`;
- **Load balancing:** `elasticloadbalancing:Describe*`;
- **Auto Scaling:** `autoscaling:DescribeAutoScalingGroups`;
- **RDS:** `rds:DescribeDBInstances`, `rds:DescribeDBClusters`, `rds:DescribeDBSnapshots`, `rds:DescribeDBClusterSnapshots`;
- **ECS:** `ecs:ListClusters`, `ecs:DescribeClusters`, `ecs:ListServices`, `ecs:DescribeServices`;
- **Lambda:** `lambda:ListFunctions`.

A missing permission skips that one type, with a note, and never fails the run.
