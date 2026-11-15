from __future__ import annotations

import csv
import io
import json
from typing import Any


def _flat(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str) if v else ""
    return "" if v is None else v


def render_csv(records: list[dict[str, Any]], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in records:
        w.writerow({c: _flat(r.get(c)) for c in cols})
    return buf.getvalue()
