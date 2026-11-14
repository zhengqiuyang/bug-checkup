"""Differential scenario for headroom: SmartCrusher + inverse decoder.

Protocol (language-agnostic): JSONL on stdin, one JSONL result per input line on stdout.
Each input line is ``{"records": [...]}``. The result is a stable summary of what the
compressor did and whether the documented inverse reproduced the records.

Run under two environments (e.g. released wheel vs current source) with `run.py diff`.
"""

from __future__ import annotations

import json
import os
import sys

from headroom.transforms.compaction_codec import expand_compacted
from headroom.transforms.smart_crusher import SmartCrusher


def summarise(records: list[dict]) -> dict:
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True)
    try:
        result = SmartCrusher(with_compaction=True).crush(payload, "", 1.0)
        compressed = result.compressed
        strategy = result.strategy
        decoded = expand_compacted(compressed)
        roundtrip = decoded is not None and json.dumps(decoded, ensure_ascii=False, sort_keys=True) == payload
    except Exception as exc:  # noqa: BLE001 - a crash is a result worth diffing
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "canary": os.environ.get("SCENARIO_CANARY", ""),
        "strategy": strategy,
        "modified": result.was_modified,
        "compressed": compressed,
        "roundtrip": roundtrip,
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        case = json.loads(line)
        print(json.dumps(summarise(case["records"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
