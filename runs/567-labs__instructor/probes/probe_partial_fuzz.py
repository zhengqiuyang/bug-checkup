"""Fuzz probe for streaming partial models across model shapes.

`model_from_chunks` must converge to the same object as `model_validate` when the JSON is
complete, for every chunking of the stream; for truncated input it must not raise. Model shapes
that are easy to get wrong: unions, optionals, nested models, lists of models, dict values,
tuples, literal/enum fields and defaults.

Standalone script (no pytest); exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE / 3 CHECKER BUG.
"""

from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, Field
from typing import Literal

from instructor.v2.dsl.partial import Partial


class Kind(str, Enum):
    A = "a"
    B = "b"


class Address(BaseModel):
    city: str
    zip_code: str | None = None


class Inner(BaseModel):
    label: str
    kind: Kind = Kind.A


class UnionModel(BaseModel):
    value: str | int
    maybe: str | None = None


class ListModel(BaseModel):
    items: list[Inner]
    tags: list[str] = Field(default_factory=list)


class DictModel(BaseModel):
    counts: dict[str, int]
    nested: dict[str, Address]


class TupleModel(BaseModel):
    pair: tuple[str, int]
    literal: Literal["x", "y"] = "x"


class Tree(BaseModel):
    name: str
    children: list["Tree"] = Field(default_factory=list)


CASES: list[tuple[str, dict]] = [
    ("union-str", {"value": "hello", "maybe": None}),
    ("union-int", {"value": 42, "maybe": "present"}),
    ("list-models", {"items": [{"label": "a", "kind": "a"}, {"label": "b", "kind": "b"}], "tags": ["t1", "t2"]}),
    ("dicts", {"counts": {"a": 1, "b": 2}, "nested": {"home": {"city": "London", "zip_code": "EC1"}}}),
    ("tuple", {"pair": ["left", 7], "literal": "y"}),
    ("tree", {"name": "root", "children": [{"name": "kid", "children": [{"name": "grand"}]}]}),
    ("unicode", {"value": "中文 😀 \\n quote", "maybe": "x"}),
]

MODELS = {
    "union-str": UnionModel,
    "union-int": UnionModel,
    "list-models": ListModel,
    "dicts": DictModel,
    "tuple": TupleModel,
    "tree": Tree,
    "unicode": UnionModel,
}


def check_complete_document(failures: list[str]) -> None:
    for label, payload in CASES:
        model = MODELS[label]
        text = json.dumps(payload, ensure_ascii=False)
        for size in (1, 2, 3, 5, 7, 64):
            chunks = [text[i : i + size] for i in range(0, len(text), size)]
            yielded = list(Partial[model].model_from_chunks(iter(chunks)))
            if not yielded:
                failures.append(f"{label} size {size}: no partial yielded")
                continue
            expected = model.model_validate_json(text)
            if yielded[-1] != expected:
                failures.append(f"{label} size {size}: final partial {yielded[-1]!r} != {expected!r}")


def check_truncated_never_raises(failures: list[str]) -> None:
    for label, payload in CASES:
        model = MODELS[label]
        text = json.dumps(payload, ensure_ascii=False)
        for cut in range(1, len(text)):
            try:
                list(Partial[model].model_from_chunks(iter([text[:cut]])))
            except Exception as exc:  # noqa: BLE001 - incomplete JSON must not raise
                failures.append(f"{label} cut {cut}: raised {type(exc).__name__}: {exc}")
                return


def check_monotone_prefixes(failures: list[str]) -> None:
    # For string fields, a later partial must never send a shorter value than an earlier one.
    text = json.dumps({"value": "abcdefghij", "maybe": "klmnopqrst"}, ensure_ascii=False)
    previous: dict[str, str] = {}
    for cut in range(1, len(text)):
        yielded = list(Partial[UnionModel].model_from_chunks(iter([text[:cut]])))
        if not yielded:
            continue
        current = yielded[-1]
        for field_name in ("value", "maybe"):
            value = getattr(current, field_name, None)
            if isinstance(value, str) and field_name in previous:
                if not value.startswith(previous[field_name]):
                    failures.append(f"cut {cut}: {field_name} shrank {previous[field_name]!r} -> {value!r}")
                    return
            if isinstance(value, str):
                previous[field_name] = value


def main() -> int:
    if Partial[UnionModel].get_partial_model() is None:
        print("CHECKER BUG: no partial model generated")
        return 3
    failures: list[str] = []
    for check in (check_complete_document, check_truncated_never_raises, check_monotone_prefixes):
        try:
            check(failures)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures[:15]:
            print(" -", failure)
        return 1
    print(f"PASS: {len(CASES)} model shapes x 6 chunkings, {sum(len(json.dumps(p)) for _, p in CASES)} truncations, monotone strings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
