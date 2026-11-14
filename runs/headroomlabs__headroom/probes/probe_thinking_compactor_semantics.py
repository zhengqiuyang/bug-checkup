"""Probe for `thinking_compactor.compact_reasoning_openai_chat` (live code, no tests).

Claims under test (function docstring):

1. the last ``keep_last_turns`` assistant turns keep their reasoning intact;
2. ``drop=True`` removes the reasoning outright (cold-prefix hook);
3. ``drop_first_turns`` forces the DROP path for the first N non-kept ordinals, and
   ``dropped_upto`` in the stats equals (highest dropped ordinal + 1);
4. deterministic output (memoised per content) — needed for prompt-cache stability;
5. never mutates the input list or its message dicts;
6. fail-open: a compressor error leaves the block untouched and does not raise.

Standalone script (no pytest, no hypothesis), with a self-check so a PASS means the
fake compressor actually shrank something.

    python probe_thinking_compactor_semantics.py
"""

from __future__ import annotations
CLAIM = "c-0086"  # bug-checkup claim id

import copy
import sys
from types import SimpleNamespace

from headroom.transforms.thinking_compactor import compact_reasoning_openai_chat

WORDS = "reasoning about the problem step by step ".split()


def _reasoning(tag: str, words: int = 60) -> str:
    return " ".join(f"{tag}-{WORDS[i % len(WORDS)]}" for i in range(words))


class FakeKompress:
    """Halves the text, deterministically, and records calls."""

    def __init__(self, boom: bool = False) -> None:
        self.calls = 0
        self.boom = boom

    def compress(self, text: str, allow_download: bool = False) -> SimpleNamespace:
        self.calls += 1
        if self.boom:
            raise RuntimeError("kompress exploded")
        words = text.split()
        return SimpleNamespace(compressed=" ".join(words[: max(1, len(words) // 2)]))


def _messages(turns: int = 3) -> list[dict]:
    out: list[dict] = [{"role": "user", "content": "start"}]
    for index in range(turns):
        out.append({"role": "assistant", "reasoning_content": _reasoning(f"T{index}"), "content": f"answer {index}"})
        out.append({"role": "user", "content": f"follow up {index}"})
    return out


def _assistant(msgs: list[dict]) -> list[dict]:
    return [m for m in msgs if m.get("role") == "assistant"]


def check_keep_last(failures: list[str]) -> None:
    messages = _messages(3)
    out, stats = compact_reasoning_openai_chat(messages, kompress=FakeKompress(), keep_last_turns=1)
    turns = _assistant(out)
    if turns[2]["reasoning_content"] != _reasoning("T2"):
        failures.append("keep_last: the last assistant turn's reasoning was changed")
    for index in (0, 1):
        text = turns[index]["reasoning_content"]
        if not text or len(text.split()) >= 60:
            failures.append(f"keep_last: turn {index} was not compacted ({len(text.split())} words)")
    if stats["turns_compacted"] != 2:
        failures.append(f"keep_last: turns_compacted={stats['turns_compacted']}, expected 2")


def check_drop(failures: list[str]) -> None:
    out, stats = compact_reasoning_openai_chat(_messages(3), kompress=None, keep_last_turns=1, drop=True)
    turns = _assistant(out)
    for index in (0, 1):
        if turns[index]["reasoning_content"] != "":
            failures.append(f"drop: turn {index} reasoning not emptied")
    if turns[2]["reasoning_content"] != _reasoning("T2"):
        failures.append("drop: kept turn was touched")
    if stats["dropped_upto"] != 2:
        failures.append(f"drop: dropped_upto={stats['dropped_upto']}, expected 2")


def check_sticky_drop(failures: list[str]) -> None:
    out, stats = compact_reasoning_openai_chat(
        _messages(3),
        kompress=FakeKompress(),
        keep_last_turns=1,
        drop_first_turns=1,
    )
    turns = _assistant(out)
    if turns[0]["reasoning_content"] != "":
        failures.append("sticky: ordinal 0 should be dropped")
    text = turns[1]["reasoning_content"]
    if not text or len(text.split()) >= 60:
        failures.append("sticky: ordinal 1 should be compacted, not dropped")
    if stats["dropped_upto"] != 1:
        failures.append(f"sticky: dropped_upto={stats['dropped_upto']}, expected 1")


def check_determinism(failures: list[str]) -> None:
    messages = _messages(3)
    kompress = FakeKompress()
    first, _ = compact_reasoning_openai_chat(messages, kompress=kompress, keep_last_turns=1)
    second, _ = compact_reasoning_openai_chat(messages, kompress=kompress, keep_last_turns=1)
    if first != second:
        failures.append("determinism: two calls with the same compressor differ")
    # A compressor that returns different text each call must still be memoised.
    class Drifting(FakeKompress):
        def compress(self, text: str, allow_download: bool = False) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(compressed=text.split()[0] + " " + "x" * self.calls)

    drifting = Drifting()
    third, _ = compact_reasoning_openai_chat(messages, kompress=drifting, keep_last_turns=1)
    fourth, _ = compact_reasoning_openai_chat(messages, kompress=drifting, keep_last_turns=1)
    if third != fourth:
        failures.append("determinism: a drifting compressor produced different bytes on re-run")


def check_no_mutation(failures: list[str]) -> None:
    messages = _messages(3)
    snapshot = copy.deepcopy(messages)
    compact_reasoning_openai_chat(messages, kompress=FakeKompress(), keep_last_turns=1)
    if messages != snapshot:
        failures.append("mutation: the input messages were modified in place")


def check_fail_open(failures: list[str]) -> None:
    messages = _messages(3)
    snapshot = copy.deepcopy(messages)
    try:
        out, stats = compact_reasoning_openai_chat(messages, kompress=FakeKompress(boom=True), keep_last_turns=1)
    except Exception as exc:  # noqa: BLE001 - documented fail-open
        failures.append(f"fail-open: raised {type(exc).__name__}: {exc}")
        return
    if out != snapshot:
        failures.append("fail-open: a failing compressor changed the messages")
    if stats["turns_compacted"] != 0:
        failures.append("fail-open: stats claim a compaction that did not happen")


def check_inline_think(failures: list[str]) -> None:
    body = " ".join(f"w{i}" for i in range(60))
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": f"<think>{body}</think>final answer"},
    ]
    out, _ = compact_reasoning_openai_chat(messages, kompress=None, keep_last_turns=0, drop=True)
    content = out[1]["content"]
    if "<think>" in content:
        failures.append("inline think: <think> block survived drop=True")
    if "final answer" not in content:
        failures.append("inline think: trailing answer text was lost")


def check_weird_shapes(failures: list[str]) -> None:
    # Plausible-but-unusual provider shapes, not contract violations: a non-dict entry
    # (None) is outside "provider-native messages" and is deliberately not tested here.
    weird: list = [
        {"role": "assistant"},
        {"role": "assistant", "reasoning_content": 123},
        {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
        {"role": "assistant", "content": None, "reasoning_content": None},
    ]
    try:
        compact_reasoning_openai_chat(weird, kompress=FakeKompress(), keep_last_turns=1)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - documented never-raises
        failures.append(f"weird shapes: raised {type(exc).__name__}: {exc}")


def main() -> int:
    if not FakeKompress().compress(_reasoning("S")).compressed:
        print("CHECKER BUG: fake compressor produced nothing")
        return 3
    failures: list[str] = []
    for check in (
        check_keep_last,
        check_drop,
        check_sticky_drop,
        check_determinism,
        check_no_mutation,
        check_fail_open,
        check_inline_think,
        check_weird_shapes,
    ):
        check(failures)
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: keep-last, drop, sticky floor, determinism, no-mutation, fail-open, inline think, weird shapes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
