from __future__ import annotations

import json
from typing import Any

from ..models import ReportMeta


def render_json(records: list[dict[str, Any]], meta: ReportMeta) -> str:
    doc = {"meta": meta.model_dump(mode="json"), "count": len(records), "rows": records}
    return json.dumps(doc, indent=2, default=str) + "\n"
