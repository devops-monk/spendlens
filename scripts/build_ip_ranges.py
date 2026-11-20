#!/usr/bin/env python3
"""Bundle the S3 and DynamoDB address ranges from AWS's public ip-ranges.json (src/spendlens/graph/data/).

    python scripts/build_ip_ranges.py                 # downloads https://ip-ranges.amazonaws.com/ip-ranges.json
    python scripts/build_ip_ranges.py ip-ranges.json  # or from a saved copy

`spendlens graph traffic` uses them to tell S3 and DynamoDB traffic (free through a gateway endpoint) from the rest
of the internet. A public, static file: no credentials, no account data.
"""

from __future__ import annotations

import gzip
import json
import sys
import urllib.request
from pathlib import Path

URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"
OUT = Path(__file__).resolve().parents[1] / "src" / "spendlens" / "graph" / "data" / "aws_service_ranges.json.gz"
SERVICES = ("S3", "DYNAMODB")


def main() -> int:
    if len(sys.argv) > 1:
        doc = json.loads(Path(sys.argv[1]).read_text())
    else:
        with urllib.request.urlopen(URL, timeout=60) as r:
            doc = json.load(r)
    out = {"source": URL, "created": doc.get("createDate"), "prefixes": sorted(
        {(p["ip_prefix"], p["service"], p["region"]) for p in doc["prefixes"] if p["service"] in SERVICES})}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(gzip.compress(json.dumps(out, separators=(",", ":")).encode(), mtime=0))
    print(f"Wrote {OUT.name}: {len(out['prefixes'])} prefixes ({OUT.stat().st_size // 1024} KiB), AWS list of {out['created']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
