"""`spendlens waste`: idle and orphaned resources that are billed but unused."""

from __future__ import annotations

from ..plugins import Registry
from .controls import ControlFeature, make_control_app

FEATURE = ControlFeature(
    name="waste",
    benchmark="waste",
    title="Idle and orphaned resources",
    help="Idle/orphaned resources (unattached volumes, unused load balancers, old snapshots ...) with $ estimates.",
    examples=(
        "spendlens waste",
        "spendlens waste --summary --by service",
        "spendlens waste -S ebs --min-saving 10 -f json -o waste.json",
        "spendlens waste --source demo",
    ),
)


def register(reg: Registry) -> None:
    reg.add_typer(make_control_app(FEATURE), FEATURE.name)
