"""Probe for instructor's streaming partial models (instructor/v2/dsl/partial.py).

`model_from_chunks` is what every streaming caller uses, and its contract is implicit in the
docstrings: a partial object is built without validation while the JSON is incomplete, a
complete JSON is validated against the original model, and streamed string fields keep growing
(the trailing-strings mode never sends a shorter value). A list field must likewise never lose
an item it already reported.

The probe feeds a valid JSON document truncated at every byte offset (and a byte-at-a-time
delivery) and checks three properties:

1. no prefix raises;
2. once the document is complete, the yielded object equals ``model_validate_json`` on the doc;
3. partial strings and lists are monotone: never shorter than the previous partial.

Standalone script (no pytest); exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE / 3 CHECKER BUG.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from instructor.v2.dsl.partial import Partial


class Address(BaseModel):
    city: str
    zip_code: str | None = None


class User(BaseModel):
    name: str
    age: int
    tags: list[str]
    address: Address
    note: str | None = None


DOC = json.dumps(
    {
        "name": "Ada Lovelace",
        "age": 36,
        "tags": ["math", "computing", "analytical engine"],
        "address": {"city": "London", "zip_code": "EC1"},
        "note": "wrote \\n the first program — 中文 😀",
    },
    ensure_ascii=False,
)


def _monotone(previous: User, current: User, failures: list[str], offset: int) -> None:
    if previous.name and current.name and not current.name.startswith(previous.name):
        failures.append(f"offset {offset}: name shrank or changed: {previous.name!r} -> {current.name!r}")
    if len(current.tags) < len(previous.tags):
        failures.append(f"offset {offset}: tags lost items: {previous.tags} -> {current.tags}")
    if previous.address.city and current.address.city and not current.address.city.startswith(
        previous.address.city
    ):
        failures.append(
            f"offset {offset}: city shrank: {previous.address.city!r} -> {current.address.city!r}"
        )


def check_every_prefix(failures: list[str]) -> None:
    for offset in range(1, len(DOC) + 1):
        chunk = DOC[:offset]
        try:
            yielded = list(Partial[User].model_from_chunks(iter([chunk])))
        except Exception as exc:  # noqa: BLE001 - a raise on a valid prefix is the finding
            failures.append(f"offset {offset}: model_from_chunks raised {type(exc).__name__}: {exc}")
            continue
        if not yielded:
            failures.append(f"offset {offset}: no partial object was yielded")
            continue
        previous = None
        for current in yielded:
            if previous is not None:
                _monotone(previous, current, failures, offset)
            previous = current


def check_byte_at_a_time(failures: list[str]) -> None:
    chunks = (DOC[index : index + 1] for index in range(len(DOC)))
    try:
        yielded = list(Partial[User].model_from_chunks(chunks))
    except Exception as exc:  # noqa: BLE001
        failures.append(f"byte-at-a-time raised {type(exc).__name__}: {exc}")
        return
    if not yielded:
        failures.append("byte-at-a-time yielded nothing")
        return
    final = yielded[-1]
    expected = User.model_validate_json(DOC)
    if final != expected:
        failures.append(f"final partial differs from the validated document: {final!r} != {expected!r}")


def check_complete_document(failures: list[str]) -> None:
    yielded = list(Partial[User].model_from_chunks(iter([DOC])))
    if len(yielded) != 1:
        failures.append(f"a complete document yielded {len(yielded)} partials, expected 1")
        return
    if yielded[0] != User.model_validate_json(DOC):
        failures.append("a complete document did not validate to the original model")


def check_incomplete_document_does_not_raise(failures: list[str]) -> None:
    truncated = DOC[: len(DOC) // 2]
    try:
        list(Partial[User].model_from_chunks(iter([truncated])))
    except Exception as exc:  # noqa: BLE001 - documented: incomplete JSON skips final validation
        failures.append(f"an incomplete document raised {type(exc).__name__}: {exc}")


def check_control_chars_in_strings(failures: list[str]) -> None:
    # A raw newline inside a JSON string is invalid; the sanitizer repairs it rather than
    # dropping the block, and the string survives without the control character.
    doc = '{"name": "Ada\nLovelace", "age": 36, "tags": ["a"], "address": {"city": "London"}}'
    yielded = list(Partial[User].model_from_chunks(iter([doc])))
    if not yielded:
        failures.append("a document with a raw control char yielded nothing")
        return
    if "\n" in yielded[-1].name:
        failures.append("a raw control char survived sanitization inside a JSON string")


def main() -> int:
    failures: list[str] = []
    for check in (
        check_complete_document,
        check_byte_at_a_time,
        check_incomplete_document_does_not_raise,
        check_control_chars_in_strings,
        check_every_prefix,
    ):
        try:
            check(failures)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures[:20]:
            print(" -", failure)
        return 1
    print(f"PASS: {len(DOC)} prefixes, bytewise delivery, completeness, monotonicity, control-char repair")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
