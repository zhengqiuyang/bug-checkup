"""Differential scenario: cache-prefix canonicalization (cache/prefix_tracker.py).

``_strip_cache_control`` and ``_canonicalize_for_prefix_compare`` decide whether two requests
share a prompt-cache prefix. A behavior change here silently changes cache-hit rates (and cost),
so the released wheel and the current source are compared on the same payloads.

Protocol: each stdin line is ``{"payload": <any>}``; each stdout line is the canonical forms.
"""

from __future__ import annotations

import json
import sys

from headroom.cache.prefix_tracker import (
    _canonicalize_for_prefix_compare,
    _strip_cache_control,
)


def resolve(case: dict) -> dict:
    payload = case.get("payload")
    try:
        stripped = _strip_cache_control(payload)
        canonical = _canonicalize_for_prefix_compare(payload)
        return {
            "stripped": json.dumps(stripped, ensure_ascii=False, sort_keys=True, default=str),
            "canonical": json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str),
            "canonical_stripped_equal": canonical == _canonicalize_for_prefix_compare(stripped),
        }
    except Exception as exc:  # noqa: BLE001 - an exception is a diffable outcome
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        print(json.dumps(resolve(json.loads(line)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
