#!/usr/bin/env python3
"""promisebreaker run orchestrator.

One CLI for every phase. Each run lives in ``runs/<name>/`` and always uses the same
artifact names, so runs are comparable and resumable. Phases whose implementation
has not landed yet say so and exit 3 instead of pretending to work.

    python run.py init   <name>
    python run.py claims <path> --name <name> [--tier A|B|all] [--max-per-family N]
    python run.py gate   <owner/repo> --name <name>
    python run.py probe  <name>
    python run.py shrink <name>
    python run.py verify <name>
    python run.py report <name>
    python run.py status <name>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"

ARTIFACTS = [
    "gate.json",
    "claims.json",
    "probes",
    "probe-results.json",
    "candidates.json",
    "findings.json",
    "verification.json",
    "REPORT.md",
    "issue.md",
    "pr.md",
]

PENDING = {
    "gate": "Phase 0: pr policy (CONTRIBUTING/README), first-timer merges, fork CI, open queue, dedupe.",
    "probe": "Phase 2: turn tier-A claims into hypothesis/differential tests under probes/.",
    "shrink": "Phase 3: minimize failing inputs, classify confirmed/known/not_a_bug/needs_validation.",
    "verify": "Phase 5: run the new test red (unpatched) and green (patched), record real output.",
    "report": "Phase 6: render REPORT.md + issue.md/pr.md from findings/verification.",
}


def run_dir(name: str) -> Path:
    return RUNS / name


def _ensure(name: str) -> Path:
    path = run_dir(name)
    path.mkdir(parents=True, exist_ok=True)
    (path / "probes").mkdir(exist_ok=True)
    return path


def cmd_init(args: argparse.Namespace) -> int:
    path = _ensure(args.name)
    print(f"run directory: {path}")
    return 0


def cmd_claims(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(HERE))
    from claim_scan import scan  # noqa: PLC0415

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 2

    claims = scan(
        root,
        include_tests=args.include_tests,
        tier=args.tier,
        max_per_family=args.max_per_family,
    )
    path = _ensure(args.name)
    out = path / "claims.json"
    out.write_text(json.dumps([c.__dict__ for c in claims], indent=2) + "\n", encoding="utf-8")

    by_kind: dict[str, int] = {}
    for claim in claims:
        by_kind[claim.kind] = by_kind.get(claim.kind, 0) + 1
    print(f"{len(claims)} claims -> {out}")
    for kind, count in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        print(f"  {kind:14s} {count:4d}")
    if claims:
        print("\nnext: pick tier-A claims, write probes, then `python run.py verify <run>`")
    return 0


def cmd_pending(phase: str) -> int:
    print(f"phase '{phase}' is not implemented in v0.1.")
    print(f"planned: {PENDING[phase]}")
    print("interface and artifact shape are frozen in schemas/ and README.md.")
    return 3


def cmd_status(args: argparse.Namespace) -> int:
    path = run_dir(args.name)
    if not path.exists():
        print(f"no run named {args.name!r} under {RUNS}", file=sys.stderr)
        return 2
    print(f"run: {path}")
    for artifact in ARTIFACTS:
        target = path / artifact
        if target.is_dir():
            files = sorted(p.name for p in target.iterdir())
            print(f"  {artifact:20s} dir ({len(files)} files)")
        elif target.exists():
            size = target.stat().st_size
            print(f"  {artifact:20s} {size} bytes")
        else:
            print(f"  {artifact:20s} -")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="promisebreaker orchestrator (v0.1)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a run directory")
    p_init.add_argument("name")
    p_init.set_defaults(func=cmd_init)

    p_claims = sub.add_parser("claims", help="phase 1: extract stated contracts")
    p_claims.add_argument("path")
    p_claims.add_argument("--name", required=True)
    p_claims.add_argument("--tier", choices=["A", "B", "all"], default="A")
    p_claims.add_argument("--max-per-family", type=int, default=40)
    p_claims.add_argument("--include-tests", action="store_true")
    p_claims.set_defaults(func=cmd_claims)

    for phase in ("gate", "probe", "shrink", "verify", "report"):
        p = sub.add_parser(phase, help=f"phase: {phase}")
        p.add_argument("name")
        p.set_defaults(func=lambda _a, _p=phase: cmd_pending(_p))

    p_status = sub.add_parser("status", help="show which artifacts a run has")
    p_status.add_argument("name")
    p_status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
