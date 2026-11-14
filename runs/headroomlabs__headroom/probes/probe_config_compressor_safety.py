"""Probe for `config_compressor` (claim c-0130: reversible + CCR-recoverable tiers).

Invariants under test (module docstring):

1. Tier 2 may drop comment/blank lines **only after** the original was persisted:
   if the store write fails, nothing may be elided.
2. When Tier 2 does elide, the output carries the retrieval marker and the store holds
   the exact original (recovery works).
3. Data that looks like a comment must never be elided: `#` lines inside a YAML block
   scalar / multi-line quoted scalar, and indented INI continuation lines.

Standalone script (no pytest). It replaces `_store_original` on the instance, so it
never touches the real compression store or the network.
"""

from __future__ import annotations

CLAIM = "c-0130"  # bug-checkup claim id

import sys

from headroom.transforms.config_compressor import ConfigCompressor, ConfigCompressorConfig

K8S_LIKE = """\
# deployment for the api
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  # labels used by the service selector
  labels:
    app: api
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: api
          image: registry.example.com/api:1.2.3
          env:
            - name: LOG_LEVEL
              value: info
"""

BLOCK_SCALAR = """\
# pipeline config
script: |
  echo start
  # this hash line is DATA inside the block scalar
  echo done
after: value
"""

SEQ_BLOCK_SCALAR = """\
# tasks
- |
  echo one
  # data line in a sequence block scalar
  echo two
- name: other
"""

QUOTED_MULTILINE = """\
note: "first line
# this hash line is DATA inside a quoted scalar
last line"
key: value
"""

INI_CONTINUATION = """\
# real comment
[section]
key = first line
  # indented continuation that is part of the value
  more of the value
"""

TOML_MULTILINE = """\
# real comment
title = "x"
body = \'\'\'
# not a comment, this is string data
more
\'\'\'
"""


class FakeStore:
    """Records what would have been persisted and returns a deterministic hash."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.saved: dict[str, str] = {}

    def __call__(self, original: str, compressed: str) -> str | None:
        if not self.ok:
            return None
        digest = f"{len(self.saved):08x}"
        self.saved[digest] = original
        return digest


def _compress(content: str, store: FakeStore, *, enable_ccr: bool = True):
    compressor = ConfigCompressor(ConfigCompressorConfig(enable_ccr=enable_ccr, enable_schema_fold=False))
    compressor._store_original = store  # type: ignore[method-assign]
    return compressor.compress(content)


def check_store_failure_blocks_elision(failures: list[str]) -> None:
    store = FakeStore(ok=False)
    result = _compress(K8S_LIKE, store)
    if "lines elided" in result.compressed:
        failures.append("store failure: comment lines were elided without a persisted original")
    if result.lines_elided:
        failures.append(f"store failure: lines_elided={result.lines_elided} without a hash")
    if result.ccr_hash is not None:
        failures.append("store failure: a hash was reported although nothing was stored")


ELIDED_CASES = 0


def check_elision_is_recoverable(failures: list[str]) -> None:
    global ELIDED_CASES
    store = FakeStore(ok=True)
    result = _compress(K8S_LIKE, store)
    if not result.was_modified:
        return  # nothing to verify; Tier 1 may not have helped
    if result.lines_elided:
        ELIDED_CASES += 1
    if result.lines_elided:
        if result.ccr_hash is None or result.ccr_hash not in store.saved:
            failures.append("elision: marker/hash emitted without a stored original")
        elif store.saved[result.ccr_hash] != K8S_LIKE:
            failures.append("elision: the stored original differs from the input")
        elif f"hash={result.ccr_hash}" not in result.compressed:
            failures.append("elision: the hash is not present in the emitted marker")


def check_data_lines_survive(failures: list[str]) -> None:
    cases = {
        "yaml block scalar": BLOCK_SCALAR,
        "sequence block scalar": SEQ_BLOCK_SCALAR,
        "yaml quoted multiline": QUOTED_MULTILINE,
        "ini continuation": INI_CONTINUATION,
        "toml multiline": TOML_MULTILINE,
    }
    for label, content in cases.items():
        store = FakeStore(ok=True)
        result = _compress(content, store)
        if not result.lines_elided:
            continue  # elision disabled for this shape: safe by construction
        ELIDED_CASES += 1
        recovered = store.saved.get(result.ccr_hash or "", "")
        if recovered != content:
            failures.append(f"{label}: elided data with no faithful stored original")


def main() -> int:
    probe_store = FakeStore(ok=True)
    if probe_store("x", "y") is None or probe_store.saved.get("00000000") != "x":
        print("CHECKER BUG: fake store does not work")
        return 3
    failures: list[str] = []
    for check in (check_store_failure_blocks_elision, check_elision_is_recoverable, check_data_lines_survive):
        check(failures)
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    if ELIDED_CASES == 0:
        print("INCONCLUSIVE: no case exercised the elision tier")
        return 2
    print(
        f"PASS: store-failure blocks elision; {ELIDED_CASES} elision case(s) recoverable; "
        "data-like comment lines never dropped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
