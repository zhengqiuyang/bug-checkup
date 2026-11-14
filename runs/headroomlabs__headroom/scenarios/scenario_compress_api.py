"""Differential scenario: public `compress()` behavior (headroom/compress.py).

The public entry point is what users actually call; a behavior change between releases
(different ratio, different message shape, a different exception) is user-visible. The
scenario reduces each run to a stable summary so two environments can be compared.

Protocol: each stdin line is ``{"messages": [...], "kwargs": {...}}``.
"""

from __future__ import annotations

import hashlib
import json
import sys

from headroom import compress


def summarise(case: dict) -> dict:
    messages = case["messages"]
    kwargs = case.get("kwargs") or {}
    try:
        result = compress(messages, **kwargs)
    except Exception as exc:  # noqa: BLE001 - an exception is a diffable outcome
        return {"error": f"{type(exc).__name__}: {exc}"}
    dumped = json.dumps(result.messages, ensure_ascii=False, sort_keys=True)
    return {
        "changed": dumped != json.dumps(messages, ensure_ascii=False, sort_keys=True),
        "message_count": len(result.messages),
        "digest": hashlib.sha256(dumped.encode()).hexdigest()[:16],
        "ratio": round(float(result.compression_ratio), 6),
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        print(json.dumps(summarise(json.loads(line)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
