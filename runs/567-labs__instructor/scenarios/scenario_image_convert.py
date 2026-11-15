"""Scenario: Anthropic image encoding across versions (JSONL in, JSONL out).

Each input line is ``{"source": ..., "media_type": ..., "data": ...}``. The result records whether
the encoder produced a usable base64 payload or raised a clear error, which is exactly the
behavior the fix changes.
"""

from __future__ import annotations

import json
import sys

from instructor.v2.core.multimodal import Image
from instructor.v2.providers.anthropic.multimodal import image_to_anthropic


def run(case: dict) -> dict:
    image = Image(
        source=case["source"],
        media_type=case["media_type"],
        data=case.get("data"),
    )
    try:
        block = image_to_anthropic(image)
    except Exception as exc:  # noqa: BLE001 - an exception is a diffable outcome
        return {"error": type(exc).__name__, "message": str(exc)[:80]}
    return {
        "data_present": block["source"].get("data") is not None,
        "media_type": block["source"].get("media_type"),
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        print(json.dumps(run(json.loads(line)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
