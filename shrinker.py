"""Delta-debugging shrinker for JSON inputs.

Given a predicate ("the probe still fails on this input") and a starting value, repeatedly try
smaller values and keep the first candidate that still fails. The result is the smallest input
in the greedy neighbourhood: fewer keys, fewer list items, shorter strings.

Pure by design: the predicate is any callable, so the CLI can run a probe subprocess and the
self-tests can use a lambda without touching the filesystem.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

Candidate = tuple[str, object]


def candidates(value: object) -> Iterator[Candidate]:
    """Yield (label, smaller value) in a deterministic order, simplest-first."""
    if isinstance(value, dict):
        keys = list(value)
        if len(keys) > 1:
            # Dropping half the keys at once shrinks fast on large fixtures.
            half = keys[: len(keys) // 2]
            yield "drop half the keys", {k: v for k, v in value.items() if k not in half}
        for key in keys:
            yield f"drop key {key!r}", {k: v for k, v in value.items() if k != key}
        for key, item in value.items():
            for label, smaller in candidates(item):
                yield f"{key}.{label}", {**value, key: smaller}
        if len(value) == 1:
            yield "unwrap single-key object", next(iter(value.values()))
        if value:
            yield "empty object", {}
    elif isinstance(value, list):
        if len(value) > 1:
            yield "keep first half", value[: len(value) // 2]
            yield "keep second half", value[len(value) // 2 :]
        for index in range(len(value)):
            yield f"drop item {index}", value[:index] + value[index + 1 :]
        for index, item in enumerate(value):
            for label, smaller in candidates(item):
                yield f"[{index}].{label}", value[:index] + [smaller] + value[index + 1 :]
        if len(value) == 1:
            yield "unwrap single-item list", value[0]
        if value:
            yield "empty list", []
    elif isinstance(value, str):
        if value:
            yield "empty string", ""
            if len(value) > 1:
                yield "first half", value[: len(value) // 2]
            if "\n" in value:
                yield "first line", value.split("\n", 1)[0]
    elif isinstance(value, bool):
        if value:
            yield "false", False
    elif value is None:
        return
    elif isinstance(value, (int, float)):
        if value != 0:
            yield "zero", 0


def _fingerprint(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def shrink(
    predicate: Callable[[object], bool],
    value: object,
    *,
    max_runs: int = 300,
) -> tuple[object, int, list[str]]:
    """Return (smallest still-failing value, runs used, accepted step labels)."""
    accepted: list[str] = []
    runs = 0
    seen = {_fingerprint(value)}
    while runs < max_runs:
        progressed = False
        for label, candidate in candidates(value):
            if runs >= max_runs:
                break
            fingerprint = _fingerprint(candidate)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            runs += 1
            if predicate(candidate):
                value = candidate
                accepted.append(label)
                progressed = True
                break
        if not progressed:
            break
    return value, runs, accepted
