"""Probe for `cross_turn_dedup` accuracy invariant.

The module claims: "Only spans that are present VERBATIM in an earlier block's already-emitted
output are back-referenced (the verbatim corpus), and the earliest occurrence is never rewritten
(keep-earliest), so the original the pointer names is always physically in context."

A pointer that names content which is not in the referenced block's *emitted* output is silent
information loss: the model cannot recover the folded bytes from the conversation.

Standalone script (no pytest, no hypothesis).
"""

from __future__ import annotations

import random
import re
import sys

from headroom.transforms.cross_turn_dedup import DedupBlock, dedup_blocks

POINTER = re.compile(r"\[↑(\d+)L same as msg (\d+)(?: ([+-]\d+)L)?: (.*)\]")


def _strip_num(line: str) -> str:
    head, sep, tail = line.partition("\t")
    return tail if sep else line


def _cat_n(lines: list[str]) -> str:
    return "\n".join(f"{index + 1}\t{line}" for index, line in enumerate(lines))


def _file(index: int, size: int = 40) -> list[str]:
    return [f"def handler_{index}_{n}(payload):  # {index}-{n}" for n in range(size)]


def verify(blocks: list[DedupBlock]) -> list[str]:
    """Return accuracy violations; empty means the invariant held for this input."""
    originals = [b.text.split("\n") for b in blocks]
    out, _stats = dedup_blocks(blocks)
    emitted = [b.text.split("\n") for b in out]
    violations: list[str] = []

    for block_index, (original, produced) in enumerate(zip(originals, emitted, strict=True)):
        position = 0
        for line in produced:
            match = POINTER.search(line)
            if not match:
                if position >= len(original) or original[position] != line:
                    violations.append(
                        f"block {block_index}: output line {line!r} does not match original line "
                        f"{original[position] if position < len(original) else '<end>'!r}"
                    )
                position += 1
                continue

            count = int(match.group(1))
            ref_turn = int(match.group(2))
            delta = int(match.group(3) or 0)
            span = original[position : position + count]
            if len(span) != count:
                violations.append(f"block {block_index}: folded span runs past the block")
                break
            position += count

            # The referenced turn must exist and be strictly earlier.
            ref_indices = [i for i, b in enumerate(blocks) if b.turn == ref_turn]
            if not ref_indices or ref_indices[0] >= block_index:
                violations.append(
                    f"block {block_index}: pointer names msg {ref_turn}, which is not an earlier block"
                )
                continue

            ref_out = emitted[ref_indices[0]]
            if delta == 0:
                if not _contains_run(emitted, ref_indices[0], span):
                    violations.append(
                        f"block {block_index}: folded span is not verbatim in msg {ref_turn}: "
                        f"{span[:2]!r}"
                    )
            else:
                wanted = [_strip_num(line) for line in span]
                candidate = [_strip_num(line) for line in ref_out]
                if not _contains_run([candidate], 0, wanted):
                    violations.append(
                        f"block {block_index}: folded span (delta {delta}) is not in msg {ref_turn}"
                    )
        if position != len(original):
            violations.append(
                f"block {block_index}: consumed {position} of {len(original)} original lines"
            )
    return violations


def _contains_run(blocks: list[list[str]], index: int, run: list[str]) -> bool:
    haystack = blocks[index]
    if not run:
        return True
    for start in range(0, len(haystack) - len(run) + 1):
        if haystack[start : start + len(run)] == run:
            return True
    return False


def scenarios() -> list[tuple[str, list[DedupBlock]]]:
    base = _file(1)
    edited = list(base)
    edited[5] = "def handler_1_5(payload):  # changed"
    return [
        ("plain repeated dump", [DedupBlock(_cat_n(base), 0), DedupBlock(_cat_n(base), 1)]),
        (
            "dump, edit, dump",
            [
                DedupBlock(_cat_n(base), 0),
                DedupBlock(_cat_n(base), 1),
                DedupBlock(_cat_n(edited), 2),
                DedupBlock(_cat_n(base), 3),
            ],
        ),
        (
            "windowed reads",
            [
                DedupBlock(_cat_n(base), 0),
                DedupBlock(_cat_n(base[10:30]), 1),
                DedupBlock(_cat_n(base[20:40]), 2),
                DedupBlock(_cat_n(base[10:30]), 3),
            ],
        ),
        (
            "protected reference target",
            [
                DedupBlock(_cat_n(base), 0, protected=True),
                DedupBlock(_cat_n(base), 1),
                DedupBlock(_cat_n(base), 2),
            ],
        ),
        (
            "shifted by a header insert",
            [DedupBlock(_cat_n(base), 0), DedupBlock(_cat_n(["# new header"] + base), 1)],
        ),
    ]


def random_scenarios(seed: int = 20261115, count: int = 40) -> list[tuple[str, list[DedupBlock]]]:
    rng = random.Random(seed)
    out: list[tuple[str, list[DedupBlock]]] = []
    for case in range(count):
        size = rng.randint(8, 40)
        base = [f"line {case}-{n} " + "x" * rng.randint(0, 10) for n in range(size)]
        blocks: list[DedupBlock] = []
        for turn in range(rng.randint(2, 5)):
            kind = rng.choice(["full", "window", "edit", "protected"])
            if kind == "full":
                text = _cat_n(base)
            elif kind == "window":
                start = rng.randint(0, max(0, size - 6))
                text = _cat_n(base[start : start + rng.randint(6, size)])
            elif kind == "edit":
                mutated = list(base)
                mutated[rng.randrange(size)] = "edited " + str(case)
                text = _cat_n(mutated)
            else:
                text = _cat_n(base)
            blocks.append(DedupBlock(text, turn, protected=(kind == "protected")))
        out.append((f"random-{case}", blocks))
    return out




def self_check() -> list[str]:
    """Validate the checker itself before trusting a PASS.

    A checker that cannot recognise a real pointer, or that accepts a span which is not
    present, would report a false verdict in both directions.
    """
    problems: list[str] = []
    probes = [
        "[↑20L same as msg 0 -10L: 'def handler_1_10(...']",
        "[↑40L same as msg 0 +1L: 'def handler_1_0(p...']",
        "[↑3L same as msg 1: 'x']",
    ]
    for sample in probes:
        if not POINTER.search(sample):
            problems.append(f"POINTER does not match a real pointer: {sample!r}")
    if POINTER.search("[↑3L same as msg 1: 'x']").group(3) is not None:
        problems.append("POINTER reports a delta for a delta-free pointer")
    if POINTER.search(probes[0]).group(3) != "-10":
        problems.append("POINTER loses the negative delta")
    haystack = [["a", "b", "c"]]
    if not _contains_run(haystack, 0, ["b", "c"]):
        problems.append("_contains_run misses a present run")
    if _contains_run(haystack, 0, ["c", "b"]):
        problems.append("_contains_run accepts an absent run")
    return problems

def main() -> int:
    failures: list[str] = []
    checker_problems = self_check()
    if checker_problems:
        print("CHECKER BUG: " + "; ".join(checker_problems))
        return 3
    folded = 0
    for label, blocks in scenarios() + random_scenarios():
        violations = verify(blocks)
        stats = dedup_blocks(blocks)[1]
        folded += int(stats.get("spans_folded", 0))
        if violations:
            failures.append(f"[{label}] " + "; ".join(violations[:3]))
    if failures:
        print(f"FAIL ({len(failures)} case(s))")
        print("\n\n".join(failures[:4]))
        return 1
    if folded == 0:
        print("INCONCLUSIVE: no span was folded")
        return 2
    print(f"PASS: {len(scenarios()) + 40} scenarios, every folded span present verbatim in an earlier block ({folded} folds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
