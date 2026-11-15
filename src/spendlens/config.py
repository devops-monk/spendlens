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

    _dedupe_lists = field_validator("mandatory", "prohibited")(_dedupe)


class SizingStandards(_Strict):
    # EC2
    ec2_allowed_instance_types: list[str] = Field(
        default_factory=lambda: ["t3.%", "t4g.%", "m6i.%", "m7g.%", "c6i.%", "r6i.%"]
    )
    ec2_cpu_high: int = Field(35, ge=0, le=100)
    ec2_cpu_low: int = Field(20, ge=0, le=100)
    ec2_running_instance_age_max_days: int = Field(90, ge=1)
    # EBS
    ebs_volume_max_size_gb: int = Field(100, ge=1)
    ebs_volume_max_iops: int = Field(32000, ge=1)
    ebs_snapshot_age_max_days: int = Field(90, ge=1)
    # RDS (defaults from steampipe-mod-aws-cost-analysis/test.spvars)
    rds_db_instance_avg_connections: int = Field(2, ge=0)
    rds_cpu_high: int = Field(50, ge=0, le=100)
    rds_cpu_low: int = Field(25, ge=0, le=100)
    rds_running_db_instance_age_max_days: int = Field(90, ge=1)
    rds_running_db_instance_age_warning_days: int = Field(30, ge=1)
    # ECS
    ecs_desired_tasks: int = Field(1, ge=0)
    ecs_task_max_vcpu: float = Field(0.25, gt=0)
    ecs_task_max_memory: float = Field(0.5, gt=0)
    # CloudWatch
    cloudwatch_log_group_retention_period_in_days: int = Field(30, ge=1)


class AnomalyConfig(_Strict):
    lookback_days: int = Field(30, ge=7, le=365)
    threshold: float = Field(3.0, gt=0)  # robust z-score
    min_daily_cost: float = Field(1.0, ge=0)  # ignore noise below this


class Budgets(_Strict):
    monthly_total: float | None = None
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


class AWSConfig(_Strict):
    profile: str | None = None
    accounts: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)


class Config(_Strict):
    common_dimensions: list[str] = Field(default_factory=lambda: ["account_id", "region"])
    tag_dimensions: list[str] = Field(default_factory=list)
    tagging: TaggingPolicy = Field(default_factory=TaggingPolicy)
    sizing: SizingStandards = Field(default_factory=SizingStandards)
    anomaly: AnomalyConfig = Field(default_factory=AnomalyConfig)
    budgets: Budgets = Field(default_factory=Budgets)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    notifications: Notifications = Field(default_factory=Notifications)
    steampipe: SteampipeConfig = Field(default_factory=SteampipeConfig)
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
        """Variables passed to the bundled Powerpipe mod via --var."""
        s = self.sizing
        mapped = {
            "common_dimensions": self.common_dimensions,
            "tag_dimensions": self.tag_dimensions,
            "mandatory_tags": self.tagging.mandatory,
            "prohibited_tags": self.tagging.prohibited,
            "expected_tag_values": self.tagging.allowed_values,
            "tag_limit": self.tagging.tag_limit,
            "ec2_instance_allowed_types": s.ec2_allowed_instance_types,
            "ec2_instance_avg_cpu_utilization_high": s.ec2_cpu_high,
            "ec2_instance_avg_cpu_utilization_low": s.ec2_cpu_low,
            "ec2_running_instance_age_max_days": s.ec2_running_instance_age_max_days,
            "ebs_volume_max_size_gb": s.ebs_volume_max_size_gb,
            "ebs_volume_max_iops": s.ebs_volume_max_iops,
            "ebs_snapshot_age_max_days": s.ebs_snapshot_age_max_days,
            "rds_db_instance_avg_connections": s.rds_db_instance_avg_connections,
            "rds_db_instance_avg_cpu_utilization_high": s.rds_cpu_high,
            "rds_db_instance_avg_cpu_utilization_low": s.rds_cpu_low,
            "rds_running_db_instance_age_max_days": s.rds_running_db_instance_age_max_days,
            "rds_running_db_instance_age_warning_days": s.rds_running_db_instance_age_warning_days,
            "ecs_desired_tasks": s.ecs_desired_tasks,
            "ecs_task_max_vcpu": s.ecs_task_max_vcpu,
            "ecs_task_max_memory": s.ecs_task_max_memory,
            "cloudwatch_log_group_retention_period_in_days": s.cloudwatch_log_group_retention_period_in_days,
        }
        return {**mapped, **self.powerpipe_vars}


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
