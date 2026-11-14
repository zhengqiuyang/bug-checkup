"""Differential scenario: request shaping helpers (proxy/helpers.py).

Three helpers decide what actually goes upstream — turn trimming, dropping output-only
blocks, and tool-search deferral. A change in any of them changes every request, so the
released wheel and the current source are compared on identical inputs.

Protocol: each stdin line is ``{"messages": [...], "tools": [...], "keep": int}``.
"""

from __future__ import annotations

import copy
import json
import sys

from headroom.proxy.helpers import (
    apply_keep_last_turns,
    inject_tool_search_deferral,
    resolved_core_tools,
    strip_output_only_request_blocks,
)


def resolve(case: dict) -> dict:
    out: dict = {}
    messages = case.get("messages") or []

    try:
        trimmed, dropped = apply_keep_last_turns(copy.deepcopy(messages), int(case.get("keep", 1)))
        out["kept"] = json.dumps(trimmed, ensure_ascii=False, sort_keys=True)
        out["dropped"] = dropped
    except Exception as exc:  # noqa: BLE001
        out["keep_error"] = f"{type(exc).__name__}: {exc}"

    try:
        stripped = copy.deepcopy(messages)
        changed = strip_output_only_request_blocks(stripped)
        out["stripped_changed"] = changed
        out["stripped"] = json.dumps(stripped, ensure_ascii=False, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        out["strip_error"] = f"{type(exc).__name__}: {exc}"

    try:
        tools = case.get("tools")
        defer = inject_tool_search_deferral(copy.deepcopy(tools))
        out["deferred"] = json.dumps(defer, ensure_ascii=False, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        out["defer_error"] = f"{type(exc).__name__}: {exc}"

    try:
        out["core"] = sorted(resolved_core_tools())
    except Exception as exc:  # noqa: BLE001
        out["core_error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        print(json.dumps(resolve(json.loads(line)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
