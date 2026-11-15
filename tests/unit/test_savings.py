
from typer.testing import CliRunner

from spendlens.config import PricingConfig
from spendlens.features.savings import apply_estimates, total_saving
from spendlens.models import Finding, Status

runner = CliRunner()
P = PricingConfig()




def F(control, status="alarm", **dims):
    return Finding(control=control, resource=f"arn:{control}", status=Status(status),
                   dimensions={k: str(v) for k, v in dims.items()})



def est(f, extra=None, pricing=P):
    return apply_estimates([f], pricing, extra)[0].est_monthly_saving



def test_ebs_estimates():
    assert est(F("ebs_volume_gp2", size_gb=100, volume_type="gp2")) == 2.0  # 100 * (0.10 - 0.08)
    assert est(F("ebs_volume_unattached", size_gb=100, volume_type="gp3")) == 8.0
    # io1 200 GB @ 10k IOPS: 25 + 650 = 675; gp3: 16 + 7000 * 0.005 = 51
    assert est(F("ebs_volume_piops_to_gp3", size_gb=200, volume_type="io1", iops=10000)) == 624.0
    assert est(F("ebs_snapshot_old", size_gb=100)) == 5.0



def test_compute_estimates():
    assert est(F("ec2_instance_low_utilization", instance_type="m5.xlarge")) == round(0.192 * 730 * 0.5, 2)
    assert est(F("ec2_instance_low_utilization", instance_type="x9.unknown")) is None
    assert est(F("rds_instance_low_connections", db_class="db.m5.large", multi_az="true")) == round(0.171 * 730 * 2, 2)
    # Multi-AZ policy only saves money when Multi-AZ is currently on
    assert est(F("rds_instance_multi_az_policy", db_class="db.m5.large", multi_az="false")) is None
    assert est(F("rds_instance_multi_az_policy", db_class="db.m5.large", multi_az="true")) == round(0.171 * 730, 2)



def test_fixed_price_estimates_and_non_alarms():
    assert est(F("vpc_eip_unattached")) == 3.65
    assert est(F("vpc_nat_gateway_unused")) == 32.85
    assert est(F("ec2_application_load_balancer_unused", lb_type="application")) == 16.43
    assert est(F("vpc_eip_unattached", status="ok")) is None
    assert est(F("no_estimator_control")) is None



def test_plugin_estimator_and_pricing_override():
    assert est(F("custom_ctl"), {"custom_ctl": lambda f, p: 12.5}) == 12.5
    assert est(F("custom_ctl"), {"custom_ctl": lambda f, p: 1 / 0}) is None  # broken estimator is ignored
    assert est(F("vpc_eip_unattached"), pricing=PricingConfig(eip_hour=0.01)) == 7.3
    assert total_saving([]) == 0

