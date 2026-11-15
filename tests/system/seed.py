"""Seed a Moto server with resources that should trigger specific spendlens controls.

Every resource is named `e2e-*` and is listed in EXPECT with the control/status it must produce.
Only ever pointed at a local emulator: the endpoint is required and dummy credentials are forced.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import boto3

REGION = "us-east-1"
GOOD_TAGS = {"Environment": "prod", "Service Owner": "team@example.com", "CostCentre": "CC-100"}


def _client(svc: str, endpoint: str):
    return boto3.client(svc, region_name=REGION, endpoint_url=endpoint, aws_access_key_id="testing",
                        aws_secret_access_key="testing")


def _tags(d: dict[str, str]) -> list[dict[str, str]]:
    return [{"Key": k, "Value": v} for k, v in d.items()]


def seed(endpoint: str) -> dict[str, str]:
    """Create resources; returns name -> id/arn for assertions."""
    assert endpoint.startswith(("http://127.0.0.1", "http://localhost")), "seed only targets a local emulator"
    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_DEFAULT_REGION=REGION)
    ids: dict[str, str] = {}
    ec2 = _client("ec2", endpoint)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]

    # --- EC2 -------------------------------------------------------------------------------------------
    bad = ec2.run_instances(ImageId=ami, InstanceType="t2.medium", MinCount=1, MaxCount=1)["Instances"][0]
    ids["ec2_bad"] = bad["InstanceId"]  # untagged, older gen, not allowed, low CPU
    good = ec2.run_instances(
        ImageId=ami, InstanceType="m6i.large", MinCount=1, MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": _tags({**GOOD_TAGS, "Name": "e2e-good"})}],
    )["Instances"][0]
    ids["ec2_good"] = good["InstanceId"]
    cw = _client("cloudwatch", endpoint)
    now = datetime.now(UTC)
    for days_ago in range(1, 8):
        ts = now - timedelta(days=days_ago)
        cw.put_metric_data(Namespace="AWS/EC2", MetricData=[
            {"MetricName": "CPUUtilization", "Dimensions": [{"Name": "InstanceId", "Value": ids["ec2_bad"]}],
             "Timestamp": ts, "Value": 3.0, "Unit": "Percent"},
            {"MetricName": "CPUUtilization", "Dimensions": [{"Name": "InstanceId", "Value": ids["ec2_good"]}],
             "Timestamp": ts, "Value": 80.0, "Unit": "Percent"},
        ])

    # --- EBS ---------------------------------------------------------------------------------------------
    az = bad["Placement"]["AvailabilityZone"]
    ids["vol_gp2"] = ec2.create_volume(AvailabilityZone=az, Size=100, VolumeType="gp2")["VolumeId"]
    ids["vol_io1"] = ec2.create_volume(AvailabilityZone=az, Size=200, VolumeType="io1", Iops=6000)["VolumeId"]
    tmp = ec2.create_volume(AvailabilityZone=az, Size=50, VolumeType="gp3")["VolumeId"]
    ids["snap_orphan"] = ec2.create_snapshot(VolumeId=tmp)["SnapshotId"]
    ec2.delete_volume(VolumeId=tmp)

    # --- Network -----------------------------------------------------------------------------------------
    ids["eip"] = ec2.allocate_address(Domain="vpc")["AllocationId"]
    subnets = [s["SubnetId"] for s in ec2.describe_subnets()["Subnets"]][:2]
    elb = _client("elbv2", endpoint)
    ids["alb"] = elb.create_load_balancer(Name="e2e-empty-alb", Subnets=subnets, Type="application")[
        "LoadBalancers"][0]["LoadBalancerArn"]

    # --- RDS ---------------------------------------------------------------------------------------------
    rds = _client("rds", endpoint)
    ids["rds"] = rds.create_db_instance(
        DBInstanceIdentifier="e2e-legacy-db", DBInstanceClass="db.m4.large", Engine="mysql", EngineVersion="5.7.44",
        MasterUsername="admin", MasterUserPassword="password123", AllocatedStorage=20, MultiAZ=True,
        BackupRetentionPeriod=7, Tags=_tags({"Environment": "dev"}),
    )["DBInstance"]["DBInstanceArn"]

    # --- S3 ----------------------------------------------------------------------------------------------
    s3 = _client("s3", endpoint)
    s3.create_bucket(Bucket="e2e-versioned-bucket")
    s3.put_bucket_versioning(Bucket="e2e-versioned-bucket", VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_tagging(Bucket="e2e-versioned-bucket", Tagging={"TagSet": _tags({**GOOD_TAGS, "Password": "x"})})
    s3.create_bucket(Bucket="e2e-trail-logs")

    # --- Misc --------------------------------------------------------------------------------------------
    ddb = _client("dynamodb", endpoint)
    ddb.create_table(TableName="e2e-provisioned", BillingMode="PROVISIONED",
                     AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                     KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                     ProvisionedThroughput={"ReadCapacityUnits": 50, "WriteCapacityUnits": 50})
    kms = _client("kms", endpoint)
    ids["kms"] = kms.create_key(Description="e2e-disabled")["KeyMetadata"]["KeyId"]
    kms.disable_key(KeyId=ids["kms"])
    r53 = _client("route53", endpoint)
    ids["health_check"] = r53.create_health_check(CallerReference="e2e-hc", HealthCheckConfig={
        "Type": "HTTP", "FullyQualifiedDomainName": "example.com", "Port": 80, "ResourcePath": "/"})["HealthCheck"]["Id"]
    ct = _client("cloudtrail", endpoint)
    for name in ("e2e-trail-a", "e2e-trail-b"):
        ct.create_trail(Name=name, S3BucketName="e2e-trail-logs", IsMultiRegionTrail=True)
    logs = _client("logs", endpoint)
    logs.create_log_group(logGroupName="e2e-forever-logs")
    _client("ecr", endpoint).create_repository(repositoryName="e2e-no-lifecycle")
    return ids


# (feature, control, resource key in seed() ids -- or a literal name substring --, expected status)
EXPECTATIONS: list[tuple[str, str, str, str]] = [
    # tags (policy from tests/system/conftest.py FINOPS_YAML)
    ("tags", "tags_untagged_ec2_instance", "ec2_bad", "alarm"),
    ("tags", "tags_untagged_ec2_instance", "ec2_good", "ok"),
    ("tags", "tags_mandatory_ec2_instance", "ec2_bad", "alarm"),
    ("tags", "tags_mandatory_ec2_instance", "ec2_good", "ok"),
    ("tags", "tags_prohibited_s3_bucket", "e2e-versioned-bucket", "alarm"),
    ("tags", "tags_prohibited_ec2_instance", "ec2_good", "ok"),
    ("tags", "tags_expected_values_ec2_instance", "ec2_good", "ok"),
    ("tags", "tags_expected_values_ec2_instance", "ec2_bad", "skip"),
    ("tags", "tags_limit_ec2_instance", "ec2_good", "ok"),
    ("tags", "tags_mandatory_rds_db_instance", "e2e-legacy-db", "alarm"),
    # rightsize
    ("rightsize", "ec2_instance_older_generation", "ec2_bad", "alarm"),
    ("rightsize", "ec2_instance_older_generation", "ec2_good", "ok"),
    ("rightsize", "ec2_instance_type_not_allowed", "ec2_bad", "alarm"),
    ("rightsize", "ec2_instance_type_not_allowed", "ec2_good", "ok"),
    ("rightsize", "ec2_instance_low_utilization", "ec2_bad", "alarm"),
    ("rightsize", "ec2_instance_low_utilization", "ec2_good", "ok"),
    ("rightsize", "ebs_volume_gp2", "vol_gp2", "alarm"),
    ("rightsize", "ebs_volume_piops_to_gp3", "vol_io1", "alarm"),
    ("rightsize", "rds_instance_multi_az_policy", "e2e-legacy-db", "alarm"),
    ("rightsize", "rds_instance_class_not_allowed", "e2e-legacy-db", "alarm"),
    ("rightsize", "rds_instance_extended_support_version", "e2e-legacy-db", "alarm"),
    ("rightsize", "rds_instance_backup_retention_policy", "e2e-legacy-db", "alarm"),
    ("rightsize", "dynamodb_table_without_autoscaling", "e2e-provisioned", "alarm"),
    ("rightsize", "cloudwatch_log_group_retention_policy", "e2e-forever-logs", "alarm"),
    # waste
    ("waste", "ebs_volume_unattached", "vol_gp2", "alarm"),
    ("waste", "ebs_snapshot_orphaned", "snap_orphan", "alarm"),
    ("waste", "vpc_eip_unattached", "eip", "alarm"),
    ("waste", "ec2_application_load_balancer_unused", "e2e-empty-alb", "alarm"),
    ("waste", "s3_bucket_versioned_without_lifecycle", "e2e-versioned-bucket", "alarm"),
    ("waste", "kms_key_disabled", "kms", "alarm"),
    ("waste", "route53_health_check_unused", "health_check", "alarm"),
    ("waste", "cloudtrail_trail_redundant", "e2e-trail-a", "alarm"),
    ("waste", "ecr_repository_without_lifecycle", "e2e-no-lifecycle", "alarm"),
]
