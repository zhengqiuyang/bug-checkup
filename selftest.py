#!/usr/bin/env python3
"""Self-tests for bug-checkup itself — the tool needs its own red/green.

Runs without pytest (same discipline as the probes): exit 0 PASS, 1 FAIL.

    python selftest.py
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import claim_scan

ROOT = Path(__file__).resolve().parent


def _load_run():
    spec = importlib.util.spec_from_file_location("bugcheckup_run", ROOT / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_claim_families(failures: list[str]) -> None:
    cases = {
        "lossless round-trip": "roundtrip",
        "must be thread-safe": "concurrency",
        "raises ValueError for bad input": "error_contract",
        "returns None on any error": "return_shape",
        "ordering is preserved": "ordering",
        "forwarded as-is": "passthrough",
        "rejects invalid input": "error_contract",
        "validates the payload": "validation",
    }
    for text, expected in cases.items():
        found = [kind for kind, _sentence, _offset in claim_scan._claims_in(text)]
        if expected not in found:
            failures.append(f"claim family {expected!r} did not match {text!r} (got {found})")


def check_examples_are_stripped(failures: list[str]) -> None:
    text = "Raises ValueError on bad input.\n\n```python\nraise ValueError('x')\n```\n"
    found = claim_scan._claims_in(text)
    if not found:
        failures.append("a real claim outside a code fence was lost")
    fenced_only = "```python\nraises ValueError\n```\n"
    if claim_scan._claims_in(fenced_only):
        failures.append("a claim inside a code fence was treated as a contract")


def check_package_init_reachability(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            '"""Package."""\n\n\ndef helper():\n    """Lossless round-trip."""\n',
            encoding="utf-8",
        )
        (Path(tmp) / "user.py").write_text("from pkg import helper\n", encoding="utf-8")
        claims = claim_scan.scan(Path(tmp), tier="all")
        if not claims:
            failures.append("claims in a reachable package __init__ were dropped")
        elif not claims[0].reachable:
            failures.append("package __init__ claim is marked unreachable")


def check_verify_delta(failures: list[str]) -> None:
    run = _load_run()
    before = {
        "results": [
            {"probe": "a.py", "verdict": "FAIL", "output": "boom"},
            {"probe": "b.py", "verdict": "PASS", "output": "ok"},
        ]
    }
    after = [
        {"probe": "a.py", "verdict": "PASS", "output": "fixed"},
        {"probe": "b.py", "verdict": "PASS", "output": "ok"},
    ]
    delta = run._compare_verdicts(before, after) if hasattr(run, "_compare_verdicts") else None
    if delta is None:
        failures.append("run.py has no _compare_verdicts helper to test")
        return
    if delta["fixed"] != ["a.py"] or delta["still_failing"] or delta["regressed"]:
        failures.append(f"verify delta is wrong: {delta}")
    broken = run._compare_verdicts(before, [{"probe": "a.py", "verdict": "PASS", "output": ""}, {"probe": "b.py", "verdict": "FAIL", "output": "regressed"}])
    if broken["regressed"] != ["b.py"]:
        failures.append(f"verify did not flag a regression: {broken}")


def check_shrinker(failures: list[str]) -> None:
    import shrinker

    def has_boom(node):
        if isinstance(node, dict):
            if node.get("boom") is True:
                return True
            return any(has_boom(item) for item in node.values())
        if isinstance(node, list):
            return any(has_boom(item) for item in node)
        return False

    value = {"meta": {"tags": ["a", "b"]}, "rows": [{"x": 1}, {"y": {"boom": True, "noise": "z"}}], "tail": "text"}
    minimal, runs, accepted = shrinker.shrink(has_boom, value, max_runs=200)
    if minimal != {"boom": True}:
        failures.append(f"shrinker did not reach the minimal failing value: {minimal!r}")
    if runs == 0 or not accepted:
        failures.append("shrinker reported no candidate runs for a reducible input")


def main() -> int:
    failures: list[str] = []
    for check in (
        check_claim_families,
        check_examples_are_stripped,
        check_package_init_reachability,
        check_verify_delta,
        check_shrinker,
    ):
        try:
            check(failures)
        except Exception as exc:  # noqa: BLE001 - a crash is a failing self-test
            failures.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: claim families, example stripping, package reachability, verify delta, shrinker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
