"""Probe for the streaming JSON object scanner (instructor/v2/dsl/iterable.py).

`_TaskObjects.feed` is a hand-written scanner that must find complete ``{...}`` objects in a
stream that arrives in arbitrary chunks, while ignoring braces inside strings and honouring
backslash escapes split across chunk boundaries. It also unwraps a ``{"tasks": [...]}`` envelope.
A missed brace drifts every later object, so this is worth testing at every chunk boundary.

Properties checked:

1. scanning a whole string and scanning it one character at a time yield the same objects;
2. every yielded object is valid JSON and matches the source object;
3. braces and escaped quotes inside strings do not end an object;
4. the ``{"tasks": [...]}`` envelope is unwrapped to its items;
5. an unfinished trailing item raises `IncompleteOutputException` at ``finish()``.

Standalone script (no pytest); exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE / 3 CHECKER BUG.
"""

from __future__ import annotations

import json

from instructor.v2.core.errors import IncompleteOutputException
from instructor.v2.dsl.iterable import _TaskObjects


def collect(chunks: list[str], *, unwrap_tasks: bool = True) -> list[str]:
    scanner = _TaskObjects(unwrap_tasks=unwrap_tasks)
    found: list[str] = []
    for chunk in chunks:
        found.extend(text for text, _end in scanner.feed(chunk))
    scanner.finish()
    return found


def expected_objects(test: dict) -> list[dict]:
    """Collect every complete object in the serialised test, using a reference parse."""
    return test["expect"]


def check_chunk_equivalence(failures: list[str]) -> None:
    objects = [
        {"id": 1, "text": "plain"},
        {"id": 2, "text": 'braces { } and "quotes"'},
        {"id": 3, "text": 'escaped \\" quote and a backslash \\'},
        {"id": 4, "nested": {"a": [1, 2, {"b": "}"}]}, "unicode": "中文 😀"},
        {"id": 5, "text": "ends with backslash \\\\"},
    ]
    source = json.dumps(objects, ensure_ascii=False)
    tests = [
        {"source": source, "expect": objects},
        {"source": '{"tasks": ' + source + "}", "expect": objects},
    ]
    for test in tests:
        whole = [json.loads(item) for item in collect([test["source"]])]
        if whole != test["expect"]:
            failures.append(f"whole-string scan mismatch: {whole} != {test['expect']}")
        charwise = [json.loads(item) for item in collect(list(test["source"]))]
        if charwise != test["expect"]:
            failures.append(f"charwise scan mismatch: {charwise} != {test['expect']}")
        for step in (2, 3, 5, 7):
            chunked = [json.loads(item) for item in collect([test["source"][i : i + step] for i in range(0, len(test["source"]), step)])]
            if chunked != test["expect"]:
                failures.append(f"chunk size {step} mismatch: {chunked} != {test['expect']}")


def check_strings_with_braces(failures: list[str]) -> None:
    # Braces inside strings must not close the object; the object after them must still parse.
    source = '[{"a": "}{", "b": 1}, {"c": "\\\\}", "d": 2}]'
    parsed = [json.loads(item) for item in collect([source])]
    if parsed != [{"a": "}{", "b": 1}, {"c": "\\}", "d": 2}]:
        failures.append(f"brace-in-string handling produced {parsed}")


def check_incomplete_tail(failures: list[str]) -> None:
    scanner = _TaskObjects()
    list(scanner.feed('[{"a": 1}, {"b": '))
    try:
        scanner.finish()
    except IncompleteOutputException:
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"finish() raised {type(exc).__name__} instead of IncompleteOutputException")
        return
    failures.append("finish() did not raise on an unfinished trailing item")


def main() -> int:
    failures: list[str] = []
    for check in (
        check_chunk_equivalence,
        check_strings_with_braces,
        check_incomplete_tail,
    ):
        try:
            check(failures)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures[:12]:
            print(" -", failure)
        return 1
    print("PASS: chunk equivalence, brace/escape handling, envelope unwrap, incomplete-tail detection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
