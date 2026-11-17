"""Direct, read-only Cost Explorer queries for what Steampipe's tables cannot express.

Steampipe's aws_cost_usage groups only by built-in dimensions and aws_cost_by_tag only by tags, but Cost Explorer
itself can group by a dimension *and* a tag at once (e.g. SERVICE + tag:Name) and filter out credits and refunds
while doing so. This module calls exactly one API, `ce:GetCostAndUsage`, which is read-only (it cannot change
anything) and billed by AWS at $0.01 per request (one request per period, plus pagination).

Credentials: the standard AWS chain of the spendlens process (the role assumed in your terminal, AWS_PROFILE /
aws.profile, SSO, OIDC in CI). AWS_ENDPOINT_URL / AWS_ENDPOINT_URL_COST_EXPLORER are honoured.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

CE_METRICS = {
    "unblended": "UnblendedCost",
    "net_unblended": "NetUnblendedCost",
    "amortized": "AmortizedCost",
    "net_amortized": "NetAmortizedCost",
    "blended": "BlendedCost",
}
UNTAGGED = "(untagged)"
_MAX_PAGES = 200  # safety stop: 200 pages is far beyond any real two-level breakdown


class CostExplorerError(RuntimeError):
    pass


def client(profile: str | None = None) -> Any:
    """A Cost Explorer client. Cost Explorer is a global service served from us-east-1."""
    try:
        import boto3
    except ImportError as e:  # pragma: no cover - boto3 is a core dependency
        raise CostExplorerError("boto3 is required for this command (pip install boto3)") from e
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client("ce", region_name="us-east-1")


def group_definition(kind: str, key: str) -> dict[str, str]:
    """spendlens GroupBy (kind 'dimension' | 'tag', key) -> Cost Explorer GroupDefinition."""
    return {"Type": "TAG" if kind == "tag" else "DIMENSION", "Key": key}


def clean_key(kind: str, key: str, value: str) -> str:
    """Cost Explorer returns tag groups as 'Key$value' ('Key$' when untagged); dimensions as plain values."""
    if kind == "tag":
        prefix = f"{key}$"
        value = value[len(prefix):] if value.startswith(prefix) else value.split("$", 1)[-1]
        return value or UNTAGGED
    return value or "(none)"


def request(groups: list[tuple[str, str]], start: str, end: str, metric: str,
            exclude_record_types: list[str]) -> dict[str, Any]:
    """The GetCostAndUsage parameters for one period, grouped by up to two (kind, key) pairs."""
    if not 1 <= len(groups) <= 2:
        raise ValueError("Cost Explorer groups by one or two keys")
    params: dict[str, Any] = {
        "TimePeriod": {"Start": start, "End": end},
        "Granularity": "MONTHLY",
        "Metrics": [CE_METRICS[metric]],
        "GroupBy": [group_definition(k, key) for k, key in groups],
    }
    if exclude_record_types:
        params["Filter"] = {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": list(exclude_record_types)}}}
    return params


def grouped_costs(ce: Any, groups: list[tuple[str, str]], start: str, end: str, metric: str,
                  exclude_record_types: list[str]) -> Iterator[dict[str, Any]]:
    """Yield {keys: [k1, k2], amount, currency} per group for the period [start, end) (all pages)."""
    params = request(groups, start, end, metric, exclude_record_types)
    name = CE_METRICS[metric]
    token = None
    for _ in range(_MAX_PAGES):
        try:
            resp = ce.get_cost_and_usage(**params, **({"NextPageToken": token} if token else {}))
        except Exception as e:  # botocore ClientError / NoCredentialsError / EndpointConnectionError
            raise CostExplorerError(_explain(e)) from e
        for result in resp.get("ResultsByTime", []):
            for g in result.get("Groups", []):
                m = g.get("Metrics", {}).get(name, {})
                keys = [clean_key(k, key, v) for (k, key), v in zip(groups, g.get("Keys", []), strict=False)]
                yield {"keys": keys, "amount": float(m.get("Amount") or 0), "currency": m.get("Unit") or "USD"}
        token = resp.get("NextPageToken")
        if not token:
            return
    raise CostExplorerError(f"Cost Explorer returned more than {_MAX_PAGES} pages; narrow the period or grouping")


def _explain(e: Exception) -> str:
    text = str(e)
    if "AccessDenied" in text or "not authorized" in text.lower():
        return f"{text}\nThe role needs ce:GetCostAndUsage (read-only)."
    if "NoCredentials" in type(e).__name__ or "Unable to locate credentials" in text:
        return "No AWS credentials found. Assume your role in this terminal (or set aws.profile) and try again."
    if "ExpiredToken" in text:
        return "Your AWS credentials have expired. Assume the role again and retry."
    return text
