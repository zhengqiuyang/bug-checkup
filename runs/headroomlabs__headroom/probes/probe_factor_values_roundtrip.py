"""Probe for claim c-0125: `factor_values` is "Lossless and reversible".

compaction_codec.py:255. The pass rewrites low-cardinality columns into an
`@col=[...]` legend plus integer indices. If the rewrite is not reversible for
some cell values, `expand_compacted` returns different data than the block it was
given: silent corruption in a codec documented as lossless.

Standalone on purpose: no pytest, no hypothesis. Probe scripts must run with the
repository's own interpreter and nothing else, so a plugin interaction cannot
hide the result.

    python probe_factor_values_roundtrip.py [--examples N]
"""

from __future__ import annotations

import json
import random
import sys

from headroom.transforms.compaction_codec import expand_compacted, factor_values
from headroom.transforms.smart_crusher import SmartCrusher

FIXED_VALUES = [
    ["x", "y", "x", "y"],
    ["", "x", "", "y"],
    ["null", "true", "null", "0"],
    ["a,b", 'q"q', "a,b", 'q"q'],
    ["line1\nline2", "x", "line1\nline2", "x"],
    ["@tag=[1,2]", "x", "@tag=[1,2]", "y"],
    ["__dict:1", "x", "__dict:1", "y"],
    ["中文", "😀", "中文", "😀"],
    ["  padded  ", "x", "  padded  ", "y"],
]

ALPHABET = list('ab,"\\\n @[]{}:=_0123') + ["null", "true", "😀", "中文"]


def _crush(records: list[dict]) -> str:
    return SmartCrusher().crush_array_json(json.dumps(records)).get("compacted") or ""


APPLIED = 0


def check(values: list[str], label: str) -> str | None:
    """Return a failure description, or None when the round-trip holds."""
    global APPLIED
    records = [{"tag": value, "id": index} for index, value in enumerate(values * 3)]
    block = _crush(records)
    if not block:
        return None  # did not compact; nothing to test
    before = expand_compacted(block)
    factored = factor_values(block)
    if factored != block:
        APPLIED += 1
    after = expand_compacted(factored)
    if before is None or after is None:
        return f"[{label}] decode failed: before={before is not None} after={after is not None}\n{block}"
    if after != before:
        return (
            f"[{label}] factor_values changed the data\n"
            f"block   : {block!r}\n"
            f"factored: {factored!r}\n"
            f"before  : {before!r}\n"
            f"after   : {after!r}"
        )
    return None


def main() -> int:
    failures: list[str] = []
    for index, values in enumerate(FIXED_VALUES):
        failure = check(values, f"fixed-{index}")
        if failure:
            failures.append(failure)

    rng = random.Random(20261115)
    examples = 60
    if "--examples" in sys.argv:
        examples = int(sys.argv[sys.argv.index("--examples") + 1])
    for index in range(examples):
        length = rng.randint(12, 40)
        values = [
            "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 8))) for _ in range(length)
        ]
        failure = check(values, f"random-{index}")
        if failure:
            failures.append(failure)
            break

    if failures:
        print(f"FAIL ({len(failures)} case(s))")
        print("\n\n".join(failures[:3]))
        return 1
    if APPLIED == 0:
        print("INCONCLUSIVE: factor_values never changed a block; the probe exercised nothing")
        return 2
    print(
        f"PASS: {len(FIXED_VALUES)} fixed + {examples} random cases preserved the decoded data "
        f"({APPLIED} blocks actually rewritten)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
