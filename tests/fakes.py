"""Stand-in for spendlens.steampipe.SteampipeClient that enforces the psycopg parameter contract.

    fake = FakeSteampipe.install(monkeypatch, {"aws_budgets_budget": [...], "aws_cost_forecast": RuntimeError("..")})

Responses are matched by the first key that appears in the SQL text. Values are row lists, a callable
(sql, params) -> rows, or an exception instance to raise as SteampipeError.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Self

from spendlens.steampipe import SteampipeError

PATCH_TARGETS = ("spendlens.steampipe.SteampipeClient", "spendlens.features.cost.SteampipeClient",
                 "spendlens.features.forecast.SteampipeClient")


class FakeSteampipe:
    responses: ClassVar[dict[str, Any]] = {}
    calls: ClassVar[list[tuple[str, Any]]] = []

    def __init__(self, cfg: Any = None, **kw: Any) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def close(self) -> None:
        pass

    def query(self, sql: str, params: Any = None, **kw: Any) -> list[dict[str, Any]]:
        names = set(re.findall(r"%\((\w+)\)s", sql))
        assert names == set(params or {}), f"placeholders {names} != params {set(params or {})}"
        assert "$1" not in sql, "psycopg needs %(name)s placeholders, not $n"
        FakeSteampipe.calls.append((sql, params))
        for key, resp in FakeSteampipe.responses.items():
            if key in sql:
                if isinstance(resp, BaseException):
                    raise SteampipeError(str(resp))
                return resp(sql, params) if callable(resp) else list(resp)
        return []

    @classmethod
    def install(cls, monkeypatch: Any, responses: dict[str, Any]) -> type[FakeSteampipe]:
        cls.responses, cls.calls = dict(responses), []
        for target in PATCH_TARGETS:
            monkeypatch.setattr(target, cls)
        return cls
