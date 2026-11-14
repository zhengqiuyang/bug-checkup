"""Probe for instructor's cache contract (instructor/cache/__init__.py).

Claims from the module and class docstrings:

* ``AutoCache`` is a *thread-safe in-process LRU* :class:`collections.OrderedDict`;
* ``BaseCache.get`` returns ``None`` only to indicate a cache miss;
* ``make_cache_key`` is *deterministic* and every component that changes the request changes
  the key (provider/model, messages, system, mode, response-model schema, generation settings),
  while components that do not change the request (e.g. ``user``) leave it alone.

Standalone script (no pytest); exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE / 3 CHECKER BUG.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from instructor.cache import AutoCache, make_cache_key


class Answer(BaseModel):
    answer: str = Field(description="the answer")
    confidence: float = 0.0


class AnswerRephrased(BaseModel):
    answer: str = Field(description="the answer, rephrased")
    confidence: float = 0.0


MESSAGES = [{"role": "user", "content": "what is 2+2?"}]


def check_lru(failures: list[str]) -> None:
    cache = AutoCache(maxsize=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # a becomes most recently used
    cache.set("c", 3)  # b should be evicted
    if cache.get("b") is not None:
        failures.append("LRU: the least recently used entry survived an eviction")
    if cache.get("a") != 1 or cache.get("c") != 3:
        failures.append("LRU: recently used entries were lost")
    cache.set("a", 10)
    if cache.get("a") != 10:
        failures.append("LRU: overwriting a key did not update the value")


def check_thread_safety(failures: list[str]) -> None:
    cache = AutoCache(maxsize=8)
    errors: list[str] = []

    def worker(worker_id: int) -> None:
        try:
            for step in range(300):
                key = f"k{(worker_id * 300 + step) % 40}"
                cache.set(key, step)
                cache.get(key)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    if any(thread.is_alive() for thread in threads):
        failures.append("thread safety: workers deadlocked")
    if errors:
        failures.append(f"thread safety: {errors[:2]}")
    if len(cache._cache) > 8:  # noqa: SLF001 - the bound is the contract
        failures.append(f"thread safety: cache exceeded maxsize ({len(cache._cache)} > 8)")


def check_key_determinism(failures: list[str]) -> None:
    first = make_cache_key(messages=MESSAGES, model="gpt-4o", response_model=Answer)
    second = make_cache_key(messages=MESSAGES, model="gpt-4o", response_model=Answer)
    if first != second:
        failures.append("make_cache_key is not deterministic")
    if make_cache_key(messages=MESSAGES, model="gpt-4o", response_model=None) == first:
        failures.append("response_model does not change the key")
    if make_cache_key(messages=MESSAGES, model="gpt-4o-mini", response_model=Answer) == first:
        failures.append("model does not change the key")
    if make_cache_key(messages=MESSAGES, model="gpt-4o", response_model=AnswerRephrased) == first:
        failures.append("a field description change does not invalidate the key")
    if make_cache_key(messages=MESSAGES, model="gpt-4o", response_model=Answer, system="be terse") == first:
        failures.append("a system prompt does not change the key")
    if make_cache_key(messages=MESSAGES, model="gpt-4o", response_model=Answer, mode="tools") == first:
        failures.append("mode does not change the key")


def check_generation_kwargs(failures: list[str]) -> None:
    base = make_cache_key(messages=MESSAGES, model="gpt-4o", response_model=Answer, request_kwargs={"temperature": 0.1})
    reordered = make_cache_key(
        messages=MESSAGES,
        model="gpt-4o",
        response_model=Answer,
        request_kwargs={"temperature": 0.1, "user": "abc"},  # not a generation field
    )
    if base != reordered:
        failures.append("a non-generation kwarg ('user') changed the key")
    hotter = make_cache_key(messages=MESSAGES, model="gpt-4o", response_model=Answer, request_kwargs={"temperature": 0.9})
    if hotter == base:
        failures.append("temperature does not change the key")


def check_value_canonicalization(failures: list[str]) -> None:
    bool_message = [{"role": "user", "content": True}]
    int_message = [{"role": "user", "content": 1}]
    if make_cache_key(messages=bool_message, model="m", response_model=None) == make_cache_key(
        messages=int_message, model="m", response_model=None
    ):
        failures.append("True and 1 produce the same key")
    left = [{"role": "user", "content": {"a": 1, "b": 2}}]
    right = [{"role": "user", "content": {"b": 2, "a": 1}}]
    if make_cache_key(messages=left, model="m", response_model=None) != make_cache_key(
        messages=right, model="m", response_model=None
    ):
        failures.append("dict key order changes the key")
    with_time = [{"role": "user", "content": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()}]
    if len(make_cache_key(messages=with_time, model="m", response_model=None)) != 64:
        failures.append("a normal message does not produce a sha256-length key")


def main() -> int:
    failures: list[str] = []
    for check in (
        check_lru,
        check_thread_safety,
        check_key_determinism,
        check_generation_kwargs,
        check_value_canonicalization,
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
    print("PASS: LRU order/eviction, thread safety, key determinism and sensitivity, value canonicalization")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
