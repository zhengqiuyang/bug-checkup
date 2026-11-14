"""Edge-case integration probe for `MemoryToolStreamFilter` continuation rounds.

Probe 1 covered the single-round happy path. The subtle part is what happens across rounds:
a continuation round restarts upstream indices at 0, keeps the client message open
(``forward_message_start=False``) and must append its visible blocks at ``next_index`` of the
previous round. Also checked: two hidden blocks in one round, inline input (no deltas), usage
aggregation in the tail, and pass-through of unknown or unparseable frames.

Standalone script (no pytest); exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE / 3 CHECKER BUG.
"""

from __future__ import annotations

import json

from headroom.proxy.memory_tool_stream import MemoryToolStreamFilter

MEMORY = "memory_save"


def frame(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def block_start(index: int, block: dict) -> bytes:
    return frame("content_block_start", {"type": "content_block_start", "index": index, "content_block": block})


def block_stop(index: int) -> bytes:
    return frame("content_block_stop", {"type": "content_block_stop", "index": index})


def text_block(index: int, text: str) -> list[bytes]:
    return [
        block_start(index, {"type": "text", "text": ""}),
        frame("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}),
        block_stop(index),
    ]


def tool_block(index: int, name: str, input_json: str | None = None) -> list[bytes]:
    out = [block_start(index, {"type": "tool_use", "id": f"t{index}", "name": name, "input": {}})]
    if input_json is not None:
        out.append(
            frame(
                "content_block_delta",
                {"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": input_json}},
            )
        )
    out.append(block_stop(index))
    return out


def started_indices(frames: list[bytes]) -> list[int]:
    out = []
    for item in frames:
        payload = _payload(item)
        if payload and payload.get("type") == "content_block_start":
            out.append(payload["index"])
    return out


def _payload(item: bytes) -> dict | None:
    for line in item.decode().splitlines():
        if line.startswith("data:"):
            try:
                value = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, dict) else None
    return None


def check_hidden_first_and_two_hidden(failures: list[str]) -> None:
    stream = b"".join(
        [frame("message_start", {"type": "message_start", "message": {"id": "m1"}})]
        + tool_block(0, MEMORY, '{"a":1}')
        + text_block(1, "middle")
        + tool_block(2, "memory_search", '{"q":"x"}')
        + text_block(3, "tail")
        + [frame("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {}}), frame("message_stop", {"type": "message_stop"})]
    )
    filt = MemoryToolStreamFilter({MEMORY, "memory_search"})
    forwarded = list(filt.feed(stream))
    if started_indices(forwarded) != [0, 1]:
        failures.append(f"two hidden blocks: client indices were {started_indices(forwarded)}, expected [0, 1]")
    names = sorted(call["name"] for call in filt.hidden_calls())
    if names != ["memory_save", "memory_search"]:
        failures.append(f"hidden_calls() returned {names}")


def check_inline_input_without_deltas(failures: list[str]) -> None:
    stream = b"".join(
        [frame("message_start", {"type": "message_start", "message": {"id": "m1"}})]
        + [block_start(0, {"type": "tool_use", "id": "t0", "name": MEMORY, "input": {"q": "inline"}}), block_stop(0)]
        + [frame("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {}}), frame("message_stop", {"type": "message_stop"})]
    )
    filt = MemoryToolStreamFilter({MEMORY})
    list(filt.feed(stream))
    calls = filt.hidden_calls()
    if len(calls) != 1 or calls[0].get("input") != {"q": "inline"}:
        failures.append(f"inline input was not preserved: {calls}")


def check_continuation_round(failures: list[str]) -> None:
    # Round 1: text block 0, hidden memory block 1 -> client sees block 0, next_index=1.
    round1 = b"".join(
        [frame("message_start", {"type": "message_start", "message": {"id": "m1"}})]
        + text_block(0, "one")
        + tool_block(1, MEMORY, "{}")
        + [frame("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}}), frame("message_stop", {"type": "message_stop"})]
    )
    first = MemoryToolStreamFilter({MEMORY})
    first_forwarded = list(first.feed(round1))
    if first.next_index != 1:
        failures.append(f"round 1 next_index={first.next_index}, expected 1")
    # Round 2 continues the same client message: upstream indices restart at 0.
    round2 = b"".join(
        [frame("message_start", {"type": "message_start", "message": {"id": "m1"}})]
        + text_block(0, "two")
        + tool_block(1, "bash", '{"cmd":"ls"}')
        + [frame("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}), frame("message_stop", {"type": "message_stop"})]
    )
    second = MemoryToolStreamFilter(
        {MEMORY},
        index_offset=first.next_index,
        forward_message_start=False,
    )
    second_forwarded = list(second.feed(round2))
    if started_indices(second_forwarded) != [1, 2]:
        failures.append(f"continuation round indices were {started_indices(second_forwarded)}, expected [1, 2]")
    if any(b"message_start" in item for item in second_forwarded):
        failures.append("a continuation round forwarded message_start")
    tail = b"".join(second.tail_frames(prior_usage={"output_tokens": 5}))
    if b'"output_tokens": 8' not in tail:
        failures.append("prior_usage output tokens were not added to the tail message_delta")
    if first_forwarded and b'"index": 0' not in first_forwarded[1]:
        failures.append("round 1 visible block was not forwarded as index 0")


def check_passthrough(failures: list[str]) -> None:
    ping = frame("ping", {"type": "ping"})
    broken = b"event: content_block_delta\ndata: {not json}\n\n"
    filt = MemoryToolStreamFilter({MEMORY})
    out = list(filt.feed(ping + broken))
    if ping not in out:
        failures.append("an unknown event was not forwarded byte-exact")
    if broken not in out:
        failures.append("an unparseable frame was not forwarded byte-exact")


def main() -> int:
    failures: list[str] = []
    for check in (
        check_hidden_first_and_two_hidden,
        check_inline_input_without_deltas,
        check_continuation_round,
        check_passthrough,
    ):
        try:
            check(failures)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: two hidden blocks, inline input, continuation indices, usage aggregation, passthrough")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
