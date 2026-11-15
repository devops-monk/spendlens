"""Estimated monthly savings for rightsize/waste findings.

Each estimator takes (finding, pricing) and returns USD/month or None when it can't estimate. Estimators use the
finding's dimensions (instance_type, size_gb, ...) that the controls emit, and unit prices from finops.yaml
`pricing:` (approximate us-east-1 list prices by default; override for your region/discounts).

These are indicative figures for prioritisation and human review, not invoices. Phase 5 refines them with
AWS Cost Optimization Hub estimates where available.

Plugins add estimators with `registry.add_estimator("control_name", fn)`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ..config import PricingConfig
from ..models import Finding, Status

Estimator = Callable[[Finding, PricingConfig], float | None]


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dim(f: Finding, key: str) -> Any:
    return f.dimensions.get(key)


def _ec2_month(f: Finding, p: PricingConfig) -> float | None:
    hourly = p.ec2_hourly.get(str(_dim(f, "instance_type") or ""))
    return hourly * p.hours_per_month if hourly else None


def _rds_month(f: Finding, p: PricingConfig) -> float | None:
    hourly = p.rds_hourly.get(str(_dim(f, "db_class") or ""))
    if not hourly:
        return None
    multi_az = str(_dim(f, "multi_az")).lower() == "true"
    return hourly * p.hours_per_month * (2 if multi_az else 1)


def _ebs_month(f: Finding, p: PricingConfig) -> float | None:
    size = _num(_dim(f, "size_gb"))
    vtype = str(_dim(f, "volume_type") or "gp3")
    price = p.ebs_gb_month.get(vtype)
    if size is None or price is None:
        return None
    total = size * price
    iops = _num(_dim(f, "iops"))
    if vtype in ("io1", "io2") and iops:
        total += iops * p.ebs_piops_month
    return total


def _pct(base: Callable[[Finding, PricingConfig], float | None], attr: str) -> Estimator:
    def est(f: Finding, p: PricingConfig) -> float | None:
        b = base(f, p)
        return b * getattr(p, attr) if b is not None else None
    return est


def _gp2_to_gp3(f: Finding, p: PricingConfig) -> float | None:
    size = _num(_dim(f, "size_gb"))
    if size is None:
        return None
    return size * (p.ebs_gb_month.get("gp2", 0) - p.ebs_gb_month.get("gp3", 0))


def _piops_to_gp3(f: Finding, p: PricingConfig) -> float | None:
    current = _ebs_month(f, p)
    size = _num(_dim(f, "size_gb"))
    iops = _num(_dim(f, "iops")) or 0
    if current is None or size is None:
        return None
    gp3 = size * p.ebs_gb_month.get("gp3", 0) + max(iops - p.gp3_included_iops, 0) * p.gp3_iops_month
    return current - gp3


def _snapshot(f: Finding, p: PricingConfig) -> float | None:
    # Upper bound: snapshots are incremental, billed on changed blocks only.
    size = _num(_dim(f, "size_gb"))
    return size * p.ebs_snapshot_gb_month if size is not None else None


def _rds_snapshot(f: Finding, p: PricingConfig) -> float | None:
    size = _num(_dim(f, "size_gb"))
    return size * p.rds_snapshot_gb_month if size is not None else None


def _lb(f: Finding, p: PricingConfig) -> float | None:
    hourly = p.load_balancer_hour.get(str(_dim(f, "lb_type") or ""))
    return hourly * p.hours_per_month if hourly else None


def _multi_az(f: Finding, p: PricingConfig) -> float | None:
    # Only saves money when Multi-AZ is on but the policy says off.
    if str(_dim(f, "multi_az")).lower() != "true":
        return None
    m = _rds_month(f, p)
    return m / 2 if m else None


def _const(attr: str, hourly: bool = False) -> Estimator:
    def est(f: Finding, p: PricingConfig) -> float | None:
        v = getattr(p, attr)
        return v * p.hours_per_month if hourly else v
    return est


ESTIMATORS: dict[str, Estimator] = {
    # rightsize
    "ec2_instance_older_generation": _pct(_ec2_month, "newer_generation_saving_pct"),
    "ec2_instance_low_utilization": _pct(_ec2_month, "downsize_saving_pct"),
    "ec2_instance_without_graviton": _pct(_ec2_month, "graviton_saving_pct"),
    "ec2_instance_reservation_candidate": _pct(_ec2_month, "reservation_saving_pct"),
    "ebs_volume_gp2": _gp2_to_gp3,
    "ebs_volume_piops_to_gp3": _piops_to_gp3,
    "rds_instance_class_not_allowed": _pct(_rds_month, "newer_generation_saving_pct"),
    "rds_instance_low_utilization": _pct(_rds_month, "downsize_saving_pct"),
    "rds_instance_reservation_candidate": _pct(_rds_month, "reservation_saving_pct"),
    "rds_instance_without_graviton": _pct(_rds_month, "graviton_saving_pct"),
    "rds_instance_multi_az_policy": _multi_az,
    # waste
    "ec2_application_load_balancer_unused": _lb,
    "ec2_network_load_balancer_unused": _lb,
    "ec2_gateway_load_balancer_unused": _lb,
    "ec2_classic_load_balancer_unused": _lb,
    "ebs_volume_unattached": _ebs_month,
    "ebs_volume_on_stopped_instance": _ebs_month,
    "ebs_snapshot_old": _snapshot,
    "ebs_snapshot_orphaned": _snapshot,
    "rds_instance_low_connections": _rds_month,
    "rds_snapshot_orphaned": _rds_snapshot,
    "vpc_eip_unattached": _const("eip_hour", hourly=True),
    "vpc_nat_gateway_unused": _const("nat_gateway_hour", hourly=True),
    "secretsmanager_secret_unused": _const("secret_month"),
    "ec2_ami_old": _snapshot,
    "kms_key_disabled": _const("kms_key_month"),
    "route53_health_check_unused": _const("route53_health_check_month"),
}


def apply_estimates(findings: Iterable[Finding], pricing: PricingConfig,
                    extra: dict[str, Estimator] | None = None) -> list[Finding]:
    """Fill est_monthly_saving on alarm findings (in place) and return them."""
    estimators = {**ESTIMATORS, **(extra or {})}
    out = []
    for f in findings:
        if f.status is Status.ALARM and f.est_monthly_saving is None and (fn := estimators.get(f.control)):
            try:
                v = fn(f, pricing)
            except Exception:  # noqa: BLE001 - a bad estimator must not break the report
                v = None
            if v is not None and v > 0:
                f.est_monthly_saving = round(v, 2)
        out.append(f)
    return out


def total_saving(findings: Iterable[Finding]) -> float:
    return round(sum(f.est_monthly_saving or 0 for f in findings if f.status is Status.ALARM), 2)
