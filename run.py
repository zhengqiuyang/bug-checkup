#!/usr/bin/env python3
"""bug-checkup run orchestrator.

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
import os
import re
import subprocess
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


def cmd_probe(args: argparse.Namespace) -> int:
    """Execute every probe under runs/<name>/probes and record the verdicts.

    Generic by construction: a probe is any ``.py`` file that exits 0/1/2/3. The tool
    does not import it, does not know the language of the audited repo, and does not
    assume what a claim is about — it records what the probe printed.
    """
    path = run_dir(args.name)
    probes_dir = path / "probes"
    if not probes_dir.exists():
        print(f"no probes/ under {path}", file=sys.stderr)
        return 2

    probes = sorted(probes_dir.glob("*.py"))
    if not probes:
        print(f"no probe scripts under {probes_dir}")
        return 0

    python = args.python or sys.executable
    cwd = Path(args.cwd).resolve() if args.cwd else path
    results = []
    for probe in probes:
        claim = _probe_claim(probe)
        try:
            completed = subprocess.run(
                [python, str(probe)],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
            )
            code = completed.returncode
            output = (completed.stdout or "") + (completed.stderr or "")
        except subprocess.TimeoutExpired:
            code = -1
            output = f"timeout after {args.timeout}s"
        verdict = VERDICTS.get(code, f"ERROR({code})")
        results.append(
            {
                "probe": probe.name,
                "claim": claim,
                "exit_code": code,
                "verdict": verdict,
                "output": output.strip()[-4000:],
            }
        )
        print(f"{verdict:12s} {probe.name}")

    (path / "probe-results.json").write_text(
        json.dumps({"python": python, "cwd": str(cwd), "results": results}, indent=2) + "\n",
        encoding="utf-8",
    )
    failures = sum(1 for r in results if r["verdict"] in {"FAIL", "CHECKER_BUG", "ERROR"})
    print(f"\n{len(results)} probes, {failures} needing attention -> {path / 'probe-results.json'}")
    return 0


VERDICTS = {0: "PASS", 1: "FAIL", 2: "INCONCLUSIVE", 3: "CHECKER_BUG"}


def _probe_claim(probe: Path) -> str | None:
    """Read ``CLAIM = "c-0001"`` from a probe without importing it."""
    try:
        text = probe.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'^CLAIM\s*=\s*["\'](c-\d+)["\']', text, re.MULTILINE)
    return match.group(1) if match else None


def cmd_diff(args: argparse.Namespace) -> int:
    """Run one scenario against two environments and report where the outputs differ.

    Language-agnostic by protocol: the scenario reads JSONL on stdin and writes one JSONL
    result per input line on stdout. The tool never imports the code under test; it only
    compares the two runs. That makes it usable for Python, Node, Go, Rust — anything that
    can read stdin and print JSON.
    """
    path = run_dir(args.name)
    path.mkdir(parents=True, exist_ok=True)
    scenario = Path(args.scenario).resolve()
    inputs = Path(args.inputs).resolve()
    if not scenario.exists() or not inputs.exists():
        print("scenario and inputs must exist", file=sys.stderr)
        return 2

    def run_env(label: str, python: str, extra_env: list[str] | None) -> list[str] | str:
        env = dict(os.environ)
        for pair in extra_env or []:
            key, _, value = pair.partition("=")
            env[key] = value
        try:
            completed = subprocess.run(
                [python, str(scenario)],
                stdin=inputs.open("r", encoding="utf-8"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
                cwd=args.cwd or str(scenario.parent),
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"{type(exc).__name__}: {exc}"
        if completed.returncode != 0:
            return f"exit {completed.returncode}: {(completed.stderr or '').strip()[-800:]}"
        return [line for line in (completed.stdout or "").splitlines() if line.strip()]

    old = run_env("old", args.old_python or sys.executable, args.old_env)
    new = run_env("new", args.new_python or sys.executable, args.new_env)
    if isinstance(old, str) or isinstance(new, str):
        print(f"scenario failed:\n  old: {old}\n  new: {new}", file=sys.stderr)
        return 1

    differences = []
    for index, (before, after) in enumerate(zip(old, new, strict=False)):
        if before != after:
            differences.append({"index": index, "old": before[:800], "new": after[:800]})
    if len(old) != len(new):
        differences.append(
            {"index": -1, "old": f"{len(old)} result lines", "new": f"{len(new)} result lines"}
        )

    results = {
        "scenario": str(scenario),
        "inputs": str(inputs),
        "old_python": args.old_python,
        "new_python": args.new_python,
        "lines": {"old": len(old), "new": len(new)},
        "different": len(differences),
        "differences": differences[: args.max_diffs],
    }
    out = path / "diff-results.json"
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"{len(differences)} differing result(s) over {len(new)} input(s) -> {out}")
    for diff in differences[: min(3, len(differences))]:
        print(f"  input #{diff['index']}:")
        print(f"    old: {diff['old'][:160]}")
        print(f"    new: {diff['new'][:160]}")
    return 0



def cmd_new_probe(args: argparse.Namespace) -> int:
    """Scaffold a probe from a per-ecosystem template.

    The template provides the exit-code contract and the self-check hook; only the
    claim-specific body is left to fill in. Adding an ecosystem means adding templates
    under ``templates/probes/<ecosystem>/``; the runner stays unchanged.
    """
    template = HERE / "templates" / "probes" / f"{args.ecosystem}_probe.py.tmpl"
    if not template.exists():
        available = sorted(p.name for p in (HERE / "templates" / "probes").glob("*_probe.py.tmpl"))
        print(f"no template for ecosystem {args.ecosystem!r}; available: {available}", file=sys.stderr)
        return 2
    path = _ensure(args.name)
    filename = args.out or f"probe_{args.claim}_{args.family}.py"
    target = path / "probes" / filename
    body = template.read_text(encoding="utf-8")
    body = (
        body.replace("{{claim}}", args.claim)
        .replace("{{family}}", args.family)
        .replace("{{filename}}", filename)
        .replace("{{description}}", args.description or f"{args.family} contract")
    )
    target.write_text(body, encoding="utf-8")
    print(f"scaffolded {target}")
    print("next: fill in check()/self_check(), then `python run.py probe <run> ...`")
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
    parser = argparse.ArgumentParser(description="bug-checkup orchestrator (v0.1)")
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

    p_probe = sub.add_parser("probe", help="phase 2: run every probe and record verdicts")
    p_probe.add_argument("name")
    p_probe.add_argument("--python", help="interpreter that can import the audited repo")
    p_probe.add_argument("--cwd", help="working directory for the probes (default: the run dir)")
    p_probe.add_argument("--timeout", type=int, default=300)
    p_probe.set_defaults(func=cmd_probe)

    p_diff = sub.add_parser("diff", help="run a scenario against two versions and diff the output")
    p_diff.add_argument("name")
    p_diff.add_argument("--scenario", required=True, help="script: JSONL on stdin -> JSONL on stdout")
    p_diff.add_argument("--inputs", required=True, help="JSONL, one case per line")
    p_diff.add_argument("--old-python", help="interpreter for the baseline (default: current)")
    p_diff.add_argument("--new-python", help="interpreter for the candidate (default: current)")
    p_diff.add_argument("--cwd", help="working directory for the scenario")
    p_diff.add_argument("--timeout", type=int, default=600)
    p_diff.add_argument("--old-env", action="append", metavar="KEY=VALUE")
    p_diff.add_argument("--new-env", action="append", metavar="KEY=VALUE")
    p_diff.add_argument("--max-diffs", type=int, default=50)
    p_diff.set_defaults(func=cmd_diff)

    p_new = sub.add_parser("new-probe", help="scaffold a probe from a per-ecosystem template")
    p_new.add_argument("name")
    p_new.add_argument("--claim", required=True)
    p_new.add_argument("--family", default="roundtrip")
    p_new.add_argument("--ecosystem", default="python")
    p_new.add_argument("--description")
    p_new.add_argument("--out")
    p_new.set_defaults(func=cmd_new_probe)

    for phase in ("gate", "shrink", "verify", "report"):
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
