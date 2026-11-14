"""Probe for `cross_turn_dedup` (no test references in the repo).

The module claims three hard invariants (module docstring):

1. prefix-monotonic: appending a later block never changes an earlier block's rewritten
   bytes, so the upstream prompt-cache prefix stays byte-stable;
2. accuracy: only spans present verbatim in an earlier block's emitted output are folded;
3. never raises: returns the input unchanged and stats["error"]=True on failure.

Invariant 1 is the expensive one to break: a violation invalidates the cached prefix on
every turn. The module ships `is_prefix_monotonic` to assert it, so a probe can use the
implementation's own oracle.

Standalone script (no pytest, no hypothesis):

    python probe_cross_turn_dedup_prefix_monotonic.py
"""

from __future__ import annotations

import random
import sys

from headroom.transforms.cross_turn_dedup import DedupBlock, dedup_blocks, is_prefix_monotonic


def _cat_n(lines: list[str]) -> str:
    """Mimic `cat -n`: line number + tab + content."""
    return "\n".join(f"{index + 1}\t{line}" for index, line in enumerate(lines))


def _file(index: int, size: int = 40) -> list[str]:
    return [f"def handler_{index}_{n}(payload):  # {index}-{n}" for n in range(size)]


def _scenarios() -> list[tuple[str, list[DedupBlock]]]:
    base = _file(1)
    edited = list(base)
    edited[5] = "def handler_1_5(payload):  # changed"
    scenarios: list[tuple[str, list[DedupBlock]]] = []

    scenarios.append(("plain repeated dump", [DedupBlock(_cat_n(base), 0), DedupBlock(_cat_n(base), 1)]))

    scenarios.append(
        (
            "dump, edit, dump",
            [
                DedupBlock(_cat_n(base), 0),
                DedupBlock(_cat_n(base), 1),
                DedupBlock(_cat_n(edited), 2),
                DedupBlock(_cat_n(base), 3),
            ],
        )
    )

    scenarios.append(
        (
            "windowed reads of the same file",
            [
                DedupBlock(_cat_n(base), 0),
                DedupBlock(_cat_n(base[10:30]), 1),
                DedupBlock(_cat_n(base[20:40]), 2),
                DedupBlock(_cat_n(base[10:30]), 3),
            ],
        )
    )

    scenarios.append(
        (
            "protected block still a reference target",
            [
                DedupBlock(_cat_n(base), 0, protected=True),
                DedupBlock(_cat_n(base), 1),
                DedupBlock(_cat_n(base), 2),
            ],
        )
    )

    scenarios.append(
        (
            "insert at head shifts every line",
            [
                DedupBlock(_cat_n(base), 0),
                DedupBlock(_cat_n(["# new header"] + base), 1),
            ],
        )
    )

    scenarios.append(
        (
            "unicode and blank lines",
            [
                DedupBlock(_cat_n(["值 = '中文'", "", "x = 1", "y = 2"]), 0),
                DedupBlock(_cat_n(["值 = '中文'", "", "x = 1", "y = 2"]), 1),
            ],
        )
    )

    scenarios.append(("empty blocks", [DedupBlock("", 0), DedupBlock("", 1)]))

    scenarios.append(
        (
            "same content different turn ordinals",
            [DedupBlock(_cat_n(base), 7), DedupBlock(_cat_n(base), 9)],
        )
    )

    return scenarios


def _random_scenarios(seed: int = 20261115, count: int = 30) -> list[tuple[str, list[DedupBlock]]]:
    rng = random.Random(seed)
    out: list[tuple[str, list[DedupBlock]]] = []
    for case in range(count):
        size = rng.randint(8, 40)
        base = [f"line {case}-{n} " + "x" * rng.randint(0, 12) for n in range(size)]
        blocks: list[DedupBlock] = []
        for turn in range(rng.randint(2, 5)):
            kind = rng.choice(["full", "window", "edit", "protected", "shuffle"])
            if kind == "full":
                text = _cat_n(base)
            elif kind == "window":
                start = rng.randint(0, max(0, size - 5))
                text = _cat_n(base[start : start + rng.randint(5, size)])
            elif kind == "edit":
                mutated = list(base)
                mutated[rng.randrange(size)] = "edited " + str(case)
                text = _cat_n(mutated)
            elif kind == "protected":
                text = _cat_n(base)
            else:
                shuffled = list(base)
                rng.shuffle(shuffled)
                text = _cat_n(shuffled)
            blocks.append(DedupBlock(text, turn, protected=(kind == "protected")))
        out.append((f"random-{case}", blocks))
    return out


def main() -> int:
    failures: list[str] = []
    folded = 0

    for label, blocks in _scenarios() + _random_scenarios():
        try:
            first, stats = dedup_blocks(blocks)
            second, _ = dedup_blocks(blocks)
        except Exception as exc:  # noqa: BLE001 - the module promises never to raise
            failures.append(f"[{label}] dedup_blocks raised {type(exc).__name__}: {exc}")
            continue
        if stats.get("error"):
            failures.append(f"[{label}] stats reported error=True: {stats}")
            continue
        folded += int(stats.get("spans_folded", 0))
        if [b.text for b in first] != [b.text for b in second]:
            failures.append(f"[{label}] non-deterministic output")
            continue
        if not is_prefix_monotonic(blocks):
            failures.append(
                f"[{label}] is_prefix_monotonic returned False\n"
                f"blocks={[b.text[:60] for b in blocks]}\n"
                f"out={[b.text[:80] for b in first]}"
            )

    if failures:
        print(f"FAIL ({len(failures)} case(s))")
        print("\n\n".join(failures[:3]))
        return 1
    print(f"PASS: {len(_scenarios()) + 30} scenarios, prefix-monotonic and deterministic ({folded} spans folded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
