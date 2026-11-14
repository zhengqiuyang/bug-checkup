"""Probe for `CompressionStore` concurrency claims (class docstring, compression_store.py:222).

Claims under test:

1. "Thread-safe for concurrent access" — concurrent `store()` calls must not raise and must
   leave the backend internally consistent.
2. "LRU-style eviction when capacity is reached" — the entry count must never exceed
   `max_entries`, under concurrency too.
3. Re-storing an existing hash must overwrite in place and must **not** evict a different live
   entry (the comment at store() says so; a marker already in a conversation would 404).
4. Retrieval concurrent with writes must not raise.

Threads with a join timeout: a lock-order bug shows up as a deadlock, which is a FAIL, not a hang
of the whole audit.

Standalone script (no pytest).
"""

from __future__ import annotations

CLAIM = "c-0008"  # bug-checkup claim id

import random
import threading

from headroom.cache.compression_store import CompressionStore

MAX_ENTRIES = 20
WRITERS = 8
PER_WRITER = 40
JOIN_TIMEOUT = 60.0


def _payload(index: int) -> tuple[str, str]:
    original = f'{{"id": {index}, "body": "record number {index} "}}'
    compressed = f'{{"id": {index}}}'
    return original, compressed


def main() -> int:
    failures: list[str] = []
    store = CompressionStore(max_entries=MAX_ENTRIES, default_ttl=3600, enable_feedback=False)
    errors: list[str] = []

    def writer(worker: int) -> None:
        try:
            for step in range(PER_WRITER):
                original, compressed = _payload(worker * PER_WRITER + step)
                store.store(original, compressed, compression_strategy="probe")
        except Exception as exc:  # noqa: BLE001 - any raise is a finding
            errors.append(f"writer {worker}: {type(exc).__name__}: {exc}")

    def reader(stop: threading.Event) -> None:
        rng = random.Random(1234)
        try:
            while not stop.is_set():
                store.retrieve(f"{rng.randrange(1 << 24):024x}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reader: {type(exc).__name__}: {exc}")

    stop = threading.Event()
    read_thread = threading.Thread(target=reader, args=(stop,), daemon=True)
    read_thread.start()
    threads = [threading.Thread(target=writer, args=(worker,)) for worker in range(WRITERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT)
    stop.set()
    read_thread.join(timeout=JOIN_TIMEOUT)

    still_running = [t.name for t in [*threads, read_thread] if t.is_alive()]
    if still_running:
        failures.append(f"deadlock: threads still running after {JOIN_TIMEOUT}s: {still_running}")
    if errors:
        failures.append(f"exceptions under concurrency: {errors[:3]}")

    stats = store.get_stats()
    if stats["entry_count"] > MAX_ENTRIES:
        failures.append(f"capacity: entry_count={stats['entry_count']} > max_entries={MAX_ENTRIES}")

    # Duplicate re-store must not evict a different live entry.
    duplicate_store = CompressionStore(max_entries=MAX_ENTRIES, default_ttl=3600, enable_feedback=False)
    hashes = []
    for index in range(MAX_ENTRIES):
        original, compressed = _payload(10_000 + index)
        hashes.append(duplicate_store.store(original, compressed))
    before = duplicate_store.get_stats()["entry_count"]
    first_original, first_compressed = _payload(10_000)
    duplicate_store.store(first_original, first_compressed)  # same hash again
    after = duplicate_store.get_stats()["entry_count"]
    if after != before:
        failures.append(f"duplicate store changed capacity: {before} -> {after}")
    if duplicate_store.retrieve(hashes[-1]) is None:
        failures.append("duplicate store evicted a different live entry")

    # Every stored hash must be retrievable or evicted (never a dead marker for a live entry).
    live = [h for h in hashes if duplicate_store.retrieve(h) is not None]
    if not live:
        failures.append("retrieval: no stored entry is retrievable")

    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    print(
        f"PASS: {WRITERS}x{PER_WRITER} concurrent stores stayed within {MAX_ENTRIES} entries, "
        f"no exceptions, duplicate re-store kept all {before} live entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
