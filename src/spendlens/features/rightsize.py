"""`spendlens rightsize`: over-provisioned resources and resources not provisioned to company standards."""

from __future__ import annotations

from ..plugins import Registry
from .controls import ControlFeature, make_control_app

FEATURE = ControlFeature(
    name="rightsize",
    benchmark="rightsize",
    title="Rightsizing and company standards",
    help="Over-provisioned resources and resources not provisioned to company standards (finops.yaml sizing.*).",
    examples=(
        "spendlens rightsize",
        "spendlens rightsize -S rds -S ec2 --min-saving 50",
        "spendlens rightsize --summary --by service",
        "spendlens rightsize --set sizing.ec2_cpu_low=10 -f csv -o rightsize.csv",
        "spendlens rightsize controls",
    ),
)


def register(reg: Registry) -> None:
    reg.add_typer(make_control_app(FEATURE), FEATURE.name)
