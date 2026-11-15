"""finops.yaml schema, loader, parameter overrides and Powerpipe variable mapping.

Company standards (tagging policy, sizing thresholds, budgets, LLM provider ...)
are control parameters. They are resolved in layers, later layers win:

  1. built-in defaults
  2. policy file: --config PATH | $SPENDLENS_CONFIG | ./finops.yaml | ~/.config/spendlens/finops.yaml
  3. environment:  SPENDLENS__SIZING__EC2_CPU_LOW=10     (double underscore = nesting)
  4. command line: --set sizing.ec2_cpu_low=10            (repeatable)

Values from env/--set are parsed as YAML, so lists and maps work:
  --set 'tagging.mandatory=[Environment, Owner]'  --set 'budgets.per_tag={Team: {payments: 5000}}'

Raw Powerpipe mod variables not modelled here can be passed with --var name=value
(stored in `powerpipe_vars`, merged last).

Every layer is validated against the schema: unknown keys and wrong types fail fast.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator

CONFIG_ENV = "SPENDLENS_CONFIG"
ENV_PREFIX = "SPENDLENS__"


class ConfigError(ValueError):
    pass


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if not (v in seen or seen.add(v))]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TaggingPolicy(_Strict):
    mandatory: list[str] = Field(default_factory=lambda: ["Environment", "Service Owner"])
    allowed_values: dict[str, list[str]] = Field(default_factory=dict)
    prohibited: list[str] = Field(default_factory=list)
    tag_limit: int = Field(45, ge=1, le=50)
    environment_tag: str = "Environment"  # drives environment-aware standards (*_by_env)

    _dedupe_lists = field_validator("mandatory", "prohibited")(_dedupe)


def _env_map(v: dict[str, Any]) -> dict[str, Any]:
    """Environment-keyed policy maps: keys are matched case-insensitively against the environment tag;
    '*' is the fallback for any other/missing environment."""
    return {str(k).lower(): x for k, x in v.items()}


class SizingStandards(_Strict):
    """Rightsizing / waste thresholds. Field names are identical to the Powerpipe mod variable names."""

    # EC2
    ec2_allowed_instance_types: list[str] = Field(
        default_factory=lambda: ["t3.%", "t4g.%", "m6i.%", "m7g.%", "c6i.%", "c7g.%", "r6i.%", "r7g.%"]
    )
    ec2_cpu_high: int = Field(35, ge=0, le=100)
    ec2_cpu_low: int = Field(20, ge=0, le=100)
    ec2_running_instance_age_max_days: int = Field(90, ge=1)
    ec2_stopped_instance_max_days: int = Field(30, ge=1)
    ec2_reserved_instance_expiration_warning_days: int = Field(30, ge=1)
    ec2_ami_age_max_days: int = Field(180, ge=1)
    # EBS
    ebs_volume_max_size_gb: int = Field(100, ge=1)
    ebs_volume_max_iops: int = Field(32000, ge=1)
    ebs_volume_ops_low: int = Field(100, ge=0)
    ebs_volume_ops_high: int = Field(500, ge=0)
    ebs_snapshot_age_max_days: int = Field(90, ge=1)
    ebs_volume_piops_to_gp3_max_iops: int = Field(16000, ge=0)
    # RDS (thresholds from steampipe-mod-aws-cost-analysis/test.spvars)
    rds_allowed_instance_classes: list[str] = Field(
        default_factory=lambda: ["db.t3.%", "db.t4g.%", "db.m5.%", "db.m6g.%", "db.m6i.%", "db.m7g.%",
                                 "db.r5.%", "db.r6g.%", "db.r6i.%", "db.r7g.%"]
    )
    rds_avg_connections_min: int = Field(2, ge=0)
    rds_cpu_high: int = Field(50, ge=0, le=100)
    rds_cpu_low: int = Field(25, ge=0, le=100)
    rds_running_age_max_days: int = Field(90, ge=1)
    rds_running_age_warning_days: int = Field(30, ge=1)
    rds_multi_az_by_env: dict[str, bool] = Field(default_factory=lambda: {"prod": True, "*": False})
    rds_performance_insights_by_env: dict[str, bool] = Field(default_factory=lambda: {"prod": True, "*": False})
    rds_backup_retention_max_days_by_env: dict[str, int] = Field(default_factory=lambda: {"prod": 14, "*": 1})
    rds_snapshot_orphaned_max_days: int = Field(30, ge=0)
    # engine -> version prefixes in paid RDS Extended Support. Review against AWS's current calendar.
    rds_extended_support_versions: dict[str, list[str]] = Field(default_factory=lambda: {
        "mysql": ["5.7", "8.0"], "postgres": ["11", "12", "13"],
        "aurora-mysql": ["5.7"], "aurora-postgresql": ["11", "12", "13"]})
    # ECS
    ecs_desired_tasks_by_env: dict[str, int] = Field(default_factory=lambda: {"prod": 3, "*": 1})
    ecs_task_max_vcpu: float = Field(0.25, gt=0)
    ecs_task_max_memory_gb: float = Field(0.5, gt=0)
    ecs_cluster_cpu_low: int = Field(20, ge=0, le=100)
    ecs_cluster_cpu_high: int = Field(35, ge=0, le=100)
    # Lambda
    lambda_error_rate_max_pct: float = Field(10, ge=0, le=100)
    lambda_timeout_max_multiple: float = Field(3, gt=1)
    # CloudWatch
    cloudwatch_log_group_retention_max_days: int = Field(30, ge=1)
    cloudwatch_log_group_kms_required: bool = True
    # Redshift
    redshift_cpu_low: int = Field(20, ge=0, le=100)
    redshift_cpu_high: int = Field(35, ge=0, le=100)
    redshift_running_age_max_days: int = Field(90, ge=1)
    redshift_running_age_warning_days: int = Field(30, ge=1)
    # Route 53
    route53_record_min_ttl: int = Field(3600, ge=0)
    # ElastiCache
    elasticache_running_age_max_days: int = Field(90, ge=1)
    elasticache_running_age_warning_days: int = Field(30, ge=1)
    # Waste
    secretsmanager_unused_max_days: int = Field(90, ge=1)
    dynamodb_stale_max_days: int = Field(90, ge=1)
    ecr_image_pull_max_days: int = Field(90, ge=1)

    _env_maps = field_validator(
        "rds_multi_az_by_env", "rds_performance_insights_by_env", "rds_backup_retention_max_days_by_env",
        "ecs_desired_tasks_by_env",
    )(_env_map)


class PricingConfig(_Strict):
    """Unit prices for savings estimates. Defaults: approximate us-east-1 on-demand list prices (USD).
    Override for your region/discounts, e.g. --set pricing.ebs_gb_month.gp3=0.088"""

    currency: str = "USD"
    hours_per_month: float = 730
    ebs_gb_month: dict[str, float] = Field(default_factory=lambda: {
        "gp2": 0.10, "gp3": 0.08, "io1": 0.125, "io2": 0.125, "st1": 0.045, "sc1": 0.015, "standard": 0.05})
    ebs_piops_month: float = 0.065
    ebs_snapshot_gb_month: float = 0.05
    rds_snapshot_gb_month: float = 0.095
    eip_hour: float = 0.005
    nat_gateway_hour: float = 0.045
    load_balancer_hour: dict[str, float] = Field(default_factory=lambda: {
        "application": 0.0225, "network": 0.0225, "gateway": 0.0125, "classic": 0.025})
    secret_month: float = 0.40
    kms_key_month: float = 1.0
    route53_health_check_month: float = 0.50
    gp3_included_iops: int = 3000
    gp3_iops_month: float = 0.005
    ec2_hourly: dict[str, float] = Field(default_factory=lambda: {
        "t2.micro": 0.0116, "t2.small": 0.023, "t2.medium": 0.0464, "t2.large": 0.0928,
        "t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416, "t3.large": 0.0832,
        "t3.xlarge": 0.1664, "t3.2xlarge": 0.3328,
        "m4.large": 0.10, "m4.xlarge": 0.20, "m4.2xlarge": 0.40,
        "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384, "m5.4xlarge": 0.768,
        "m6i.large": 0.096, "m6i.xlarge": 0.192, "m6i.2xlarge": 0.384,
        "c5.large": 0.085, "c5.xlarge": 0.17, "c5.2xlarge": 0.34,
        "r5.large": 0.126, "r5.xlarge": 0.252, "r5.2xlarge": 0.504})
    rds_hourly: dict[str, float] = Field(default_factory=lambda: {
        "db.t2.micro": 0.017, "db.t2.small": 0.034, "db.t2.medium": 0.068,
        "db.t3.micro": 0.017, "db.t3.small": 0.034, "db.t3.medium": 0.068, "db.t3.large": 0.136,
        "db.m4.large": 0.175, "db.m5.large": 0.171, "db.m5.xlarge": 0.342, "db.m5.2xlarge": 0.684,
        "db.r5.large": 0.25, "db.r5.xlarge": 0.50})
    # fraction of the resource price saved by an action
    downsize_saving_pct: float = Field(0.5, ge=0, le=1)  # one size down
    reservation_saving_pct: float = Field(0.3, ge=0, le=1)  # 1-yr no-upfront RI / SP
    graviton_saving_pct: float = Field(0.2, ge=0, le=1)
    newer_generation_saving_pct: float = Field(0.1, ge=0, le=1)


CostMetric = Literal["unblended", "net_unblended", "amortized", "net_amortized", "blended"]


class CostConfig(_Strict):
    """`spendlens cost ...` defaults."""

    metric: CostMetric = "net_unblended"  # after discounts/credits applied at line-item level (as the legacy mod)
    exclude_record_types: list[str] = Field(default_factory=lambda: ["Credit", "Refund"])
    default_group_by: str = "service"
    change_alarm_abs: float = Field(10.0, ge=0)  # flag |change| >= this ... (legacy: 10 cost units)
    change_alarm_pct: float = Field(20.0, ge=0)  # ... and |change %| >= this
    min_amount: float = Field(0.01, ge=0)
    trend_months: int = Field(6, ge=2, le=13)  # Cost Explorer keeps ~13 months of history by default
    showback_tag_keys: list[str] = Field(default_factory=lambda: ["Service Owner", "Environment"])
    resource_days: int = Field(7, ge=1, le=7)


class AnomalyConfig(_Strict):
    """`spendlens anomalies`: daily cost per group vs a trailing baseline (median + MAD robust z-score)."""

    lookback_days: int = Field(30, ge=7, le=90)  # baseline window before each checked day
    detect_days: int = Field(3, ge=1, le=14)  # most recent complete days to check
    threshold: float = Field(3.0, gt=0)  # robust z-score
    min_daily_cost: float = Field(1.0, ge=0)  # ignore days below this
    min_increase: float = Field(10.0, ge=0)  # and changes smaller than this (currency units/day)
    include_drops: bool = False  # also report sudden decreases (e.g. a service went down)
    group_by: str = "service"


class Budgets(_Strict):
    monthly_total: float | None = None  # account-level monthly budget checked by `spendlens forecast`
    warn_pct: float = Field(80.0, gt=0, le=100)  # forecast above this % of a budget = at risk
    per_tag: dict[str, dict[str, float]] = Field(default_factory=dict)  # {"Team": {"payments": 5000}}


Provider = Literal["openai", "azure", "bedrock", "anthropic", "openai_compatible"]


class LLMConfig(_Strict):
    provider: Provider = "bedrock"
    model: str = "anthropic.claude-sonnet-5-5"
    base_url: str | None = None
    api_key_env: str | None = None  # NAME of the env var holding the key, never the key itself
    api_version: str | None = None  # azure
    region: str | None = None  # bedrock
    temperature: float = Field(0.0, ge=0, le=2)
    max_rows: int = Field(500, ge=1)
    query_timeout_s: int = Field(60, ge=1)


class Notifications(_Strict):
    slack_webhook_env: str = "SPENDLENS_SLACK_WEBHOOK"
    teams_webhook_env: str = "SPENDLENS_TEAMS_WEBHOOK"


class SteampipeConfig(_Strict):
    dsn: str | None = None  # overrides everything below
    host: str = "localhost"
    port: int = 9193
    database: str = "steampipe"
    user: str = "steampipe"
    password_env: str = "STEAMPIPE_DATABASE_PASSWORD"
    connection: str = "aws"  # steampipe connection / aggregator name used as search_path


class PowerpipeConfig(_Strict):
    binary: str = "powerpipe"
    max_parallel: int = Field(10, ge=1, le=100)
    timeout_s: int = Field(1800, ge=10)
    extra_args: list[str] = Field(default_factory=list)  # appended to every powerpipe invocation


class HistoryConfig(_Strict):
    enabled: bool = True  # save every live run to the local SQLite store
    path: str | None = None  # default ~/.local/share/spendlens/spendlens.db; in CI restore/save between jobs
    keep_runs: int = Field(200, ge=1)  # per feature


class SavingsConfig(_Strict):
    """`spendlens savings`: Cost Optimization Hub, commitment coverage, Savings Plan expiry."""

    commitment_coverage_target_pct: float = Field(70.0, ge=0, le=100)
    commitment_min_on_demand: float = Field(100.0, ge=0)  # ignore services with less on-demand spend/month
    commitment_services: list[str] = Field(default_factory=lambda: [
        "Amazon Elastic Compute Cloud - Compute", "Amazon Relational Database Service", "Amazon ElastiCache",
        "Amazon OpenSearch Service", "Amazon Redshift", "AWS Lambda", "Amazon Elastic Container Service",
        "Amazon SageMaker"])
    savings_plan_expiration_warning_days: int = Field(45, ge=1)
    hub_min_saving: float = Field(0.0, ge=0)


class DashboardConfig(_Strict):
    """HTML dashboard / --format html. Brand values are chrome only (header, accents); data colours come from a
    validated, colour-blind-safe chart palette and are not themeable. Set these from your brand guidelines."""

    title: str = "spendlens FinOps report"
    organisation: str = ""  # shown in the header, e.g. "Very Group - Platform"
    brand_primary: str = Field("#E6007E", pattern=r"^#[0-9a-fA-F]{6}$")  # accent: header rule, active tab, links
    brand_ink: str = Field("#000000", pattern=r"^#[0-9a-fA-F]{6}$")  # header background
    brand_on_ink: str = Field("#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")  # header text
    # CSS font stack only (letters, digits, spaces, quotes, commas, hyphens, dots): emitted raw inside <style>
    font_family: str = Field('system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
                             pattern=r"""^[A-Za-z0-9 ,'"\-.]+$""", max_length=200)
    top_n: int = Field(15, ge=3, le=100)  # rows per chart
    table_rows: int = Field(50, ge=5, le=1000)  # rows per findings table


class AWSConfig(_Strict):
    profile: str | None = None
    accounts: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)


class Config(_Strict):
    common_dimensions: list[str] = Field(default_factory=lambda: ["account_id", "region"])
    tag_dimensions: list[str] = Field(default_factory=list)
    tagging: TaggingPolicy = Field(default_factory=TaggingPolicy)
    sizing: SizingStandards = Field(default_factory=SizingStandards)
    cost: CostConfig = Field(default_factory=CostConfig)
    anomaly: AnomalyConfig = Field(default_factory=AnomalyConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    budgets: Budgets = Field(default_factory=Budgets)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    notifications: Notifications = Field(default_factory=Notifications)
    steampipe: SteampipeConfig = Field(default_factory=SteampipeConfig)
    powerpipe: PowerpipeConfig = Field(default_factory=PowerpipeConfig)
    savings: SavingsConfig = Field(default_factory=SavingsConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    aws: AWSConfig = Field(default_factory=AWSConfig)
    powerpipe_vars: dict[str, Any] = Field(default_factory=dict)  # raw passthrough, merged last
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)  # per-plugin settings

    _source: str | None = PrivateAttr(default=None)
    _param_sources: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def source(self) -> str | None:
        return self._source

    def param_source(self, key: str) -> str:
        return self._param_sources.get(key, "default")

    def to_powerpipe_vars(self) -> dict[str, Any]:
        """Variables passed to the bundled Powerpipe mod (only those the mod declares are sent)."""
        return {
            "common_dimensions": self.common_dimensions,
            "tag_dimensions": self.tag_dimensions,
            "mandatory_tags": self.tagging.mandatory,
            "prohibited_tags": self.tagging.prohibited,
            "expected_tag_values": self.tagging.allowed_values,
            "tag_limit": self.tagging.tag_limit,
            "environment_tag": self.tagging.environment_tag,
            **self.sizing.model_dump(),
            **self.powerpipe_vars,
        }


# ---------------------------------------------------------------- parameters


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """{'sizing': {'ec2_cpu_low': 20}} -> {'sizing.ec2_cpu_low': 20}. Maps under dict-typed fields stay whole."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and _is_section(key):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _model_at(path: list[str]) -> type[BaseModel] | None:
    model: type[BaseModel] = Config
    for part in path:
        field = model.model_fields.get(part)
        ann = field.annotation if field else None
        if not (isinstance(ann, type) and issubclass(ann, BaseModel)):
            return None
        model = ann
    return model


def _is_section(dotted: str) -> bool:
    return _model_at(dotted.split(".")) is not None


def param_keys() -> list[str]:
    return list(flatten(Config().model_dump(mode="json", exclude=set(FREEFORM_SECTIONS))).keys())


def parse_value(raw: str) -> Any:
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


FREEFORM_SECTIONS = ("plugins", "powerpipe_vars")


def _set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    freeform = parts[0] in FREEFORM_SECTIONS and len(parts) > 1
    parent = _model_at(parts[:-1])
    if not freeform and (parent is None or parts[-1] not in parent.model_fields):
        raise ConfigError(
            f"Unknown parameter '{dotted}'. Run `spendlens params` to list valid keys."
        )
    node = data
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise ConfigError(f"'{p}' in '{dotted}' is not a section")
    node[parts[-1]] = value


def parse_assignments(items: list[str] | None, flag: str = "--set") -> list[tuple[str, str]]:
    pairs = []
    for item in items or []:
        if "=" not in item:
            raise ConfigError(f"{flag} expects KEY=VALUE, got '{item}'")
        k, v = item.split("=", 1)
        pairs.append((k.strip(), v))
    return pairs


def env_assignments(environ: dict[str, str] | None = None) -> list[tuple[str, str]]:
    environ = os.environ if environ is None else environ
    return [
        (k[len(ENV_PREFIX):].lower().replace("__", "."), v)
        for k, v in sorted(environ.items())
        if k.startswith(ENV_PREFIX) and len(k) > len(ENV_PREFIX)
    ]


# ---------------------------------------------------------------- loading


def find_config(explicit: Path | None = None) -> Path | None:
    if explicit:
        if not explicit.is_file():
            raise FileNotFoundError(f"Config file not found: {explicit}")
        return explicit
    if env := os.environ.get(CONFIG_ENV):
        p = Path(env).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"${CONFIG_ENV} points to a missing file: {p}")
        return p
    candidates = (Path("finops.yaml"), Path.home() / ".config" / "spendlens" / "finops.yaml")
    return next((p for p in candidates if p.is_file()), None)


def load_config(
    explicit: Path | None = None,
    *,
    sets: list[str] | None = None,
    pp_vars: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    path = find_config(explicit)
    data: dict[str, Any] = {}
    sources: dict[str, str] = {}
    if path:
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: top level must be a mapping")
        sources.update(dict.fromkeys(flatten(data), f"file:{path}"))

    for label, pairs in (("env", env_assignments(environ)), ("--set", parse_assignments(sets))):
        for key, raw in pairs:
            _set_path(data, key, parse_value(raw))
            sources[key] = label

    for key, raw in parse_assignments(pp_vars, "--var"):
        data.setdefault("powerpipe_vars", {})[key] = parse_value(raw)
        sources[f"powerpipe_vars.{key}"] = "--var"

    try:
        cfg = Config.model_validate(data)
    except ValidationError as e:
        lines = []
        for err in e.errors():
            key = ".".join(str(x) for x in err["loc"])
            src = next((sources[k] for k in sources if key == k or key.startswith(k + ".")), "default")
            lines.append(f"  {key}: {err['msg']}  (from {src})")
        raise ConfigError("Invalid parameters:\n" + "\n".join(lines)) from e
    cfg._source = str(path) if path else None
    cfg._param_sources = sources
    return cfg


def template_text() -> str:
    return resources.files("spendlens.templates").joinpath("finops.yaml").read_text()
