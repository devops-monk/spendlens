"""Normalised data models shared by every engine and renderer."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Status(str, Enum):
    OK = "ok"
    ALARM = "alarm"
    INFO = "info"
    SKIP = "skip"
    ERROR = "error"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Finding(BaseModel):
    """One control result for one resource (tags, rightsizing, waste ...)."""

    control: str
    title: str = ""
    check: str | None = None  # e.g. tags_mandatory, rightsize, waste
    service: str | None = None  # e.g. ec2, rds
    resource_type: str | None = None  # e.g. s3_bucket
    resource: str
    account_id: str | None = None
    region: str | None = None
    status: Status
    reason: str = ""
    severity: Severity = Severity.LOW
    est_monthly_saving: float | None = None
    tags: dict[str, Any] = Field(default_factory=dict)
    dimensions: dict[str, Any] = Field(default_factory=dict)


class CostDelta(BaseModel):
    """Cost of one group (service/account/tag value/resource) across two periods."""

    group: str
    previous: float
    current: float
    currency: str = "USD"

    @property
    def change(self) -> float:
        return round(self.current - self.previous, 2)

    @property
    def change_pct(self) -> float | None:
        if self.previous == 0:
            return None
        return round((self.current - self.previous) / self.previous * 100, 2)

    def flat(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "previous": round(self.previous, 2),
            "current": round(self.current, 2),
            "change": self.change,
            "change_pct": self.change_pct,
            "currency": self.currency,
        }


class Anomaly(BaseModel):
    date: str
    group: str
    cost: float
    baseline: float
    score: float
    currency: str = "USD"


class ReportMeta(BaseModel):
    title: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    account_id: str | None = None
    caller_arn: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
