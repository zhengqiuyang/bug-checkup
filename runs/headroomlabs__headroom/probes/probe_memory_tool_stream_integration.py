"""Integration probe for `MemoryToolStreamFilter` (proxy/memory_tool_stream.py).

The filter sits between the upstream SSE stream and the client. Contracts from the module
docstring that a unit test can miss because they only appear end to end:

1. the memory tool's block events never reach the client, and the client's block indices stay
   contiguous after the hidden block;
2. `hidden_calls()` returns the withheld call with its streamed JSON input;
3. a round whose only tool call was withheld ends the client message as ``end_turn`` (a
   ``tool_use`` stop with no visible tool block leaves the client waiting on a tool it never saw);
4. feeding one byte at a time produces the same frames as feeding the whole stream at once;
5. the retained-byte cap raises `MemoryToolStreamOverflowError` instead of forwarding withheld bytes.

Standalone script (no pytest); exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE / 3 CHECKER BUG.
"""

from __future__ import annotations

import json

from headroom.proxy.memory_tool_stream import (
    MemoryToolStreamFilter,
    MemoryToolStreamOverflowError,
)

MEMORY_TOOL = "memory_save"
CLIENT_TOOL = "bash"


def _frame(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def _stream(*, with_client_tool: bool = False) -> bytes:
    frames = [
        _frame("message_start", {"type": "message_start", "message": {"id": "m1", "role": "assistant"}}),
        _frame("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        _frame("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}}),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _frame("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": MEMORY_TOOL, "input": {}}}),
        _frame("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"content":"x"'}}),
        _frame("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "}"}}),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _frame("content_block_start", {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}}),
        _frame("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "after"}}),
        _frame("content_block_stop", {"type": "content_block_stop", "index": 2}),
    ]
    if with_client_tool:
        frames += [
            _frame("content_block_start", {"type": "content_block_start", "index": 3, "content_block": {"type": "tool_use", "id": "t2", "name": CLIENT_TOOL, "input": {}}}),
            _frame("content_block_delta", {"type": "content_block_delta", "index": 3, "delta": {"type": "input_json_delta", "partial_json": '{"cmd":"ls"}'}}),
            _frame("content_block_stop", {"type": "content_block_stop", "index": 3}),
        ]
    frames += [
        _frame("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}}),
        _frame("message_stop", {"type": "message_stop"}),
    ]
    return b"".join(frames)


def _start_indices(frames: list[bytes]) -> list[int]:
    out = []
    for frame in frames:
        for line in frame.decode().splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if payload.get("type") == "content_block_start":
                    out.append(payload["index"])
    return out


def check_visible_stream(failures: list[str]) -> None:
    stream = _stream()
    filt = MemoryToolStreamFilter({MEMORY_TOOL})
    forwarded = list(filt.feed(stream))
    joined = b"".join(forwarded)
    if MEMORY_TOOL.encode() in joined:
        failures.append("a memory tool block reached the client")
    if b'"index": 1' in joined.replace(b'"index": 1,', b""):
        pass
    indices = _start_indices(forwarded)
    if indices != [0, 1]:
        failures.append(f"client block indices are not contiguous after hiding: {indices}")
    if not filt.hid_tool_calls:
        failures.append("hid_tool_calls is False after a withheld memory call")
    calls = filt.hidden_calls()
    if len(calls) != 1 or calls[0].get("name") != MEMORY_TOOL:
        failures.append(f"hidden_calls() did not return the withheld call: {calls}")
    elif calls[0].get("input") != {"content": "x"}:
        failures.append(f"hidden call input was not assembled from deltas: {calls[0].get('input')}")
    closing = filt.closing_frames()
    closing_text = b"".join(closing)
    if b'"stop_reason": "end_turn"' not in closing_text:
        failures.append("a hidden-only tool round did not end the client message with stop_reason=end_turn")


def check_bytewise_equivalence(failures: list[str]) -> None:
    whole = b"".join(MemoryToolStreamFilter({MEMORY_TOOL}).feed(_stream()))
    filt = MemoryToolStreamFilter({MEMORY_TOOL})
    pieces: list[bytes] = []
    for index in range(len(_stream())):
        pieces.extend(filt.feed(_stream()[index : index + 1]))
    bytewise = b"".join(pieces)
    if bytewise != whole:
        failures.append("byte-at-a-time feeding produced different frames than the whole stream")


def check_overflow_raises(failures: list[str]) -> None:
    filt = MemoryToolStreamFilter({MEMORY_TOOL}, max_retained_bytes=40)
    forwarded: list[bytes] = []
    raised = False
    try:
        for index in range(len(_stream())):
            forwarded.extend(filt.feed(_stream()[index : index + 1]))
    except MemoryToolStreamOverflowError:
        raised = True
    if not raised:
        failures.append("retained-byte cap did not raise MemoryToolStreamOverflowError")
    if MEMORY_TOOL.encode() in b"".join(forwarded):
        failures.append("an overflow forwarded withheld memory-tool bytes to the client")


def check_hold_and_release(failures: list[str]) -> None:
    filt = MemoryToolStreamFilter({MEMORY_TOOL}, hold_tool_use=True)
    forwarded = list(filt.feed(_stream(with_client_tool=True)))
    if not filt.holds_client_tool_use:
        failures.append("hold_tool_use did not hold the client tool_use block")
    if any(b'"name": "bash"' in frame for frame in forwarded):
        failures.append("the held client tool_use reached the client before release")
    released = filt.release_held(drop_tool_use=True)
    if any(b'"name": "bash"' in frame for frame in released):
        failures.append("release_held(drop_tool_use=True) still emitted the client tool_use")
    # Visible blocks before the first tool_use still stream live.
    if not any(b'"text": "hello"' in frame for frame in forwarded):
        failures.append("text before the first tool call did not stream live")


def main() -> int:
    failures: list[str] = []
    for check in (
        check_visible_stream,
        check_bytewise_equivalence,
        check_overflow_raises,
        check_hold_and_release,
    ):
        try:
            check(failures)
        except Exception as exc:  # noqa: BLE001 - a crash is a finding, not a checker bug
            failures.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: hidden blocks, contiguous indices, assembled input, end_turn, bytewise parity, overflow, hold/release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
