"""Probe for `bills_prior_thinking` (thinking_compactor.py:52) — the money gate.

The function decides whether prior-turn thinking is re-billed as input. A wrong `True`
turns thinking the provider would strip for free into billed text; a wrong `False` only
leaves savings on the table. The docstring states the rule: conservative `False` unless
the model is confidently >= 4.6, with one documented trap (a release-date suffix must
not be read as a minor version).

Standalone script (no pytest).
"""

from __future__ import annotations

CLAIM = "c-0060"  # bug-checkup claim id

from headroom.transforms.thinking_compactor import bills_prior_thinking

# (model id, expected) — expectations come from the docstring, not from the implementation.
CASES: list[tuple[str, bool]] = [
    ("claude-opus-4-6", True),
    ("claude-sonnet-4-6", True),
    ("claude-opus-4-6-20251101", True),
    ("claude-sonnet-4-6-20260101", True),
    ("claude-5", True),
    ("claude-opus-5-20260101", True),
    # pre-4.6 strips prior thinking server-side: compacting would create billed text.
    ("claude-sonnet-4-5", False),
    ("claude-haiku-4-5", False),
    ("claude-3-7-sonnet-20250219", False),
    ("claude-3-5-haiku-20241022", False),
    # The documented trap: a major-only id with a date suffix is 4.0, not 4.<date>.
    ("claude-sonnet-4-20250514", False),
    # Provider-prefixed spellings that reach the same model.
    ("anthropic/claude-opus-4-6", True),
    ("anthropic.claude-opus-4-6-v1:0", True),
    # Unknown / malformed: conservative False.
    ("", False),
    ("gpt-4o", False),
    ("claude", False),
]


def main() -> int:
    failures: list[str] = []
    for model, expected in CASES:
        actual = bills_prior_thinking(model)
        if actual is not expected:
            failures.append(f"{model!r}: expected {expected}, got {actual}")
    if failures:
        print(f"FAIL ({len(failures)} case(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    print(f"PASS: {len(CASES)} model ids classified as the documented rule says")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
