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
import ast
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


def _probe_plan(args: argparse.Namespace) -> tuple[list[Path], str, Path, dict[str, str]] | None:
    """Resolve probes, interpreter, cwd and environment for a probe run."""
    path = run_dir(args.name)
    probes_dir = path / "probes"
    if not probes_dir.exists():
        print(f"no probes/ under {path}", file=sys.stderr)
        return None
    probes = sorted(probes_dir.glob("*.py"))
    python = args.python or sys.executable
    cwd = Path(args.cwd).resolve() if args.cwd else path
    env = dict(os.environ)
    for pair in args.env or []:
        key, _, value = pair.partition("=")
        env[key] = value
    return probes, python, cwd, env


def _run_probes(
    probes: list[Path], python: str, cwd: Path, env: dict[str, str], timeout: int
) -> list[dict]:
    results = []
    for probe in probes:
        claim = _probe_claim(probe)
        try:
            completed = subprocess.run(
                [python, str(probe)],
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            code = completed.returncode
            output = (completed.stdout or "") + (completed.stderr or "")
        except subprocess.TimeoutExpired:
            code = -1
            output = f"timeout after {timeout}s"
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
    return results


def cmd_probe(args: argparse.Namespace) -> int:
    """Execute every probe under runs/<name>/probes and record the verdicts.

    Generic by construction: a probe is any ``.py`` file that exits 0/1/2/3. The tool
    does not import it, does not know the language of the audited repo, and does not
    assume what a claim is about — it records what the probe printed.
    """
    plan = _probe_plan(args)
    if plan is None:
        return 2
    probes, python, cwd, env = plan
    if not probes:
        print(f"no probe scripts under {run_dir(args.name) / 'probes'}")
        return 0
    results = _run_probes(probes, python, cwd, env, args.timeout)
    path = run_dir(args.name)
    (path / "probe-results.json").write_text(
        json.dumps({"python": python, "cwd": str(cwd), "results": results}, indent=2) + "\n",
        encoding="utf-8",
    )
    failures = sum(1 for r in results if r["verdict"] in {"FAIL", "CHECKER_BUG", "ERROR"})
    print(f"\n{len(results)} probes, {failures} needing attention -> {path / 'probe-results.json'}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """Snapshot the current probe verdicts as the pre-fix baseline."""
    plan = _probe_plan(args)
    if plan is None:
        return 2
    probes, python, cwd, env = plan
    if not probes:
        print("no probes to baseline", file=sys.stderr)
        return 2
    results = _run_probes(probes, python, cwd, env, args.timeout)
    path = run_dir(args.name)
    (path / "probe-baseline.json").write_text(
        json.dumps({"python": python, "cwd": str(cwd), "results": results}, indent=2) + "\n",
        encoding="utf-8",
    )
    failing = [r["probe"] for r in results if r["verdict"] in {"FAIL", "CHECKER_BUG"}]
    print(f"\nbaseline: {len(results)} probes, {len(failing)} failing -> {path / 'probe-baseline.json'}")
    return 0


def _compare_verdicts(baseline: dict, results: list[dict]) -> dict:
    """Pure before/after comparison used by `verify` (and self-tested)."""
    before = {row["probe"]: row for row in baseline.get("results", [])}
    after = {row["probe"]: row for row in results}
    fixed, still_failing, regressed, new = [], [], [], []
    for name, row in after.items():
        old = before.get(name)
        if old is None:
            new.append(name)
            continue
        was_bad = old["verdict"] in {"FAIL", "CHECKER_BUG", "ERROR"}
        is_good = row["verdict"] == "PASS"
        if was_bad and is_good:
            fixed.append(name)
        elif was_bad and not is_good:
            still_failing.append(name)
        elif not was_bad and not is_good:
            regressed.append(name)
    return {
        "fixed": fixed,
        "still_failing": still_failing,
        "regressed": regressed,
        "new_probes": new,
        "before": {name: row["verdict"] for name, row in before.items()},
        "after": {name: row["verdict"] for name, row in after.items()},
    }


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-run probes and compare with the baseline: the automated red-to-green loop.

    Exits 0 only when at least one previously failing probe now passes, nothing that passed
    before fails now, and no previously failing probe still fails.
    """
    path = run_dir(args.name)
    baseline_path = path / "probe-baseline.json"
    if not baseline_path.exists():
        print(f"no baseline at {baseline_path}; run `baseline` before the fix", file=sys.stderr)
        return 2
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    plan = _probe_plan(args)
    if plan is None:
        return 2
    probes, python, cwd, env = plan
    results = _run_probes(probes, python, cwd, env, args.timeout)

    verification = _compare_verdicts(baseline, results)
    before = {row["probe"]: row for row in baseline.get("results", [])}
    after = {row["probe"]: row for row in results}
    fixed = verification["fixed"]
    still_failing = verification["still_failing"]
    regressed = verification["regressed"]
    new = verification["new_probes"]
    (path / "verification.json").write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")

    print(f"\nfixed (red -> green): {len(fixed)}")
    for name in fixed:
        print(f"  {before[name]['verdict']} -> PASS  {name}")
        print(f"    before: {before[name]['output'].strip().splitlines()[-1][:160]}")
        print(f"    after : {after[name]['output'].strip().splitlines()[-1][:160]}")
    if still_failing:
        print(f"still failing: {still_failing}")
    if regressed:
        print(f"REGRESSED: {regressed}")
    if new:
        print(f"new probes (not in baseline): {new}")
    print(f"artifact: {path / 'verification.json'}")

    ok = bool(fixed) and not still_failing and not regressed
    return 0 if ok else 1


VERDICTS = {0: "PASS", 1: "FAIL", 2: "INCONCLUSIVE", 3: "CHECKER_BUG"}


def _probe_claim(probe: Path) -> str | None:
    """Read ``CLAIM = "c-0001"`` from a probe without importing it."""
    try:
        text = probe.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'^CLAIM\s*=\s*["\'](c-\d+)["\']', text, re.MULTILINE)
    return match.group(1) if match else None

def _pip_venv(name: str, package: str, version: str, python_version: str) -> str:
    """Create (or reuse) a venv with ``package==version`` and return its interpreter.

    This is how a differential run gets a released baseline without manual setup: uv
    installs the published wheel, the scenario runs against it, and the candidate source
    stays on PYTHONPATH.
    """
    import shutil as _shutil

    uv = _shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH; install it or pass --old-python explicitly")
    safe = f"{package.replace('/', '_')}-{version}".replace(":", "_")
    base = run_dir(name) / f".diff-venv-{safe}"
    python = base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        subprocess.run([uv, "venv", str(base), "--python", python_version], check=True)
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), f"{package}=={version}"],
            check=True,
        )
    return str(python)


def _prepare_diff_python(args: argparse.Namespace) -> str | None:
    if not args.pip_version:
        return args.old_python
    if not args.pip_package:
        print("--pip-version requires --pip-package (e.g. instructor)", file=sys.stderr)
        return None
    try:
        return _pip_venv(args.name, args.pip_package, args.pip_version, args.pip_python)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"could not prepare the baseline venv: {exc}", file=sys.stderr)
        return None



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

    old_python = _prepare_diff_python(args)
    if old_python is None:
        return 2
    old = run_env("old", old_python, args.old_env)
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


FENCE = re.compile(r"^\s*```(\w+)?\s*$", re.MULTILINE)
EXAMPLE_LANGS = {"python", "python3", "py"}
# Blocks that cannot run as-is, or need credentials/network: reported as SKIP, not FAIL.
EXAMPLE_SKIP_MARKERS = (
    ">>>",
    "...",
    "<your",
    "your_api_key",
    "YOUR_API_KEY",
    "pip install",
    "os.environ[\"OPENAI_API_KEY\"] = \"sk-",
)


def _fenced_blocks(text: str, language: str) -> list[tuple[int, str]]:
    """(line number, code) for every fenced block whose tag matches ``language``."""
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    start: int | None = None
    tag: str | None = None
    body: list[str] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if start is None:
                start, tag, body = number, stripped[3:].strip().lower(), []
            else:
                if tag in EXAMPLE_LANGS or (not tag and language in EXAMPLE_LANGS):
                    blocks.append((start, "\n".join(body)))
                start, tag, body = None, None, []
            continue
        if start is not None:
            body.append(line)
    return blocks



EXAMPLE_ENV_MARKERS = (
    "timeout after",
    "ConnectionError",
    "Connection refused",
    "SSLError",
    "ProxyError",
    "No module named",
    "ModuleNotFoundError",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "api_key",
    "API key",
)


def _classify_example(exit_code: int, output: str) -> str:
    """PASS / FRAGMENT (doc snippet, missing surrounding code) / NEEDS_ENV / FAIL.

    Only FAIL is a contract problem worth investigating: a fragment is the docs' shape,
    and a needs-env example proves nothing about the code.
    """
    if exit_code == 0:
        return "PASS"
    if "NameError" in output and "is not defined" in output:
        return "FRAGMENT"
    if any(marker in output for marker in EXAMPLE_ENV_MARKERS):
        return "NEEDS_ENV"
    return "FAIL"

def cmd_examples(args: argparse.Namespace) -> int:
    """Extract fenced examples from docs and execute them.

    Documentation examples are implicit contracts. The runner is language-agnostic in
    shape: an example is a file executed by a configured interpreter; only the fenced
    block extraction is Markdown-specific.
    """
    path = run_dir(args.name)
    examples_dir = path / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)
    target = Path(args.path).resolve()
    if not target.exists():
        print(f"{target} does not exist", file=sys.stderr)
        return 2

    files = [target] if target.is_file() else sorted(target.rglob("*.md"))
    interpreter = args.python or sys.executable
    cwd = Path(args.cwd).resolve() if args.cwd else path
    results = []
    index = 0
    for doc in files:
        text = doc.read_text(encoding="utf-8", errors="replace")
        for line_number, code in _fenced_blocks(text, args.lang):
            index += 1
            name = f"example_{index:03d}.py"
            script = examples_dir / name
            script.write_text(code + "\n", encoding="utf-8")
            skip = any(marker in code for marker in EXAMPLE_SKIP_MARKERS) or (
                "import " not in code and "from " not in code
            )
            if skip:
                results.append(
                    {"example": name, "doc": str(doc), "line": line_number, "verdict": "SKIP"}
                )
                continue
            try:
                completed = subprocess.run(
                    [interpreter, str(script)],
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=args.timeout,
                )
                code_exit = completed.returncode
                output = ((completed.stdout or "") + (completed.stderr or "")).strip()[-2000:]
            except subprocess.TimeoutExpired:
                code_exit, output = -1, f"timeout after {args.timeout}s"
            results.append(
                {
                    "example": name,
                    "doc": str(doc),
                    "line": line_number,
                    "verdict": _classify_example(code_exit, output),
                    "exit_code": code_exit,
                    "output": output,
                }
            )
    (path / "examples-results.json").write_text(
        json.dumps({"path": str(target), "interpreter": interpreter, "results": results}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print(f"{index} example(s) -> {path / 'examples-results.json'}: {counts}")
    for row in results:
        if row["verdict"] == "FAIL":
            print(f"  FAIL {row['example']} ({Path(row['doc']).name}:{row['line']})")
    return 0


INVERSE_WORDS = (
    ("encode", "decode"),
    ("compress", "decompress"),
    ("dump", "load"),
    ("serialize", "deserialize"),
    ("pack", "unpack"),
    ("fold", "unfold"),
    ("collapse", "expand"),
    ("to_dict", "from_dict"),
    ("to_json", "from_json"),
)

AUTO_INPUTS_SOURCE = r"""import json

# Inputs are chosen to make compressing/encoding pairs actually engage: long runs,
# repeated stanzas, arrays with repeated items, unicode, empty and padded text.
INPUTS = [
    "",
    "a",
    "x\n" * 60,
    ("line\n" * 40).strip(),
    ("a\nb\nc\n" * 12).strip(),
    "a\nb\na\nb\n" * 10,
    json.dumps({"a": 1, "b": [1, 2, 3], "c": {"d": "e"}}),
    json.dumps([{"id": i % 3, "v": "same"} for i in range(40)]),
    json.dumps({"rows": [{"id": i, "v": "x"} for i in range(40)]}),
    json.dumps([{"k": "中文 😀", "v": None}] * 30, ensure_ascii=False),
    "  padded  \n\nblank\n",
]
"""


def _required_positional(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    required = len(positional) - len(args.defaults)
    return max(0, required)


def _find_inverse_pairs(source: str) -> list[tuple[str, str]]:
    """(f, g) pairs in one module whose names spell an inverse relation."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    candidates: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_"):
            if _required_positional(node) == 1:
                candidates[node.name] = node.lineno
    pairs: list[tuple[str, str]] = []
    for first, second in INVERSE_WORDS:
        for name in list(candidates):
            if not name.startswith(first):
                continue
            rest = name[len(first):]
            twin = second + rest
            if twin in candidates:
                pairs.append((name, twin))
    return sorted(set(pairs))


def cmd_autoprobe(args: argparse.Namespace) -> int:
    """Detect inverse function pairs in a package and scaffold round-trip probes.

    Generic rule, language-specific spelling: a pair is two module-level one-argument
    functions whose names are the same stem with inverse words. The generated probe calls
    f then g and asserts the original comes back, reporting INCONCLUSIVE when the pair
    never transforms anything.
    """
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"{root} is not a directory", file=sys.stderr)
        return 2
    import_root = Path(args.import_root).resolve() if args.import_root else root.parent
    run = _ensure(args.name)
    written = 0
    for file in sorted(root.rglob("*.py")):
        if "test" in file.name or ".venv" in file.parts:
            continue
        pairs = _find_inverse_pairs(file.read_text(encoding="utf-8", errors="replace"))
        if not pairs:
            continue
        try:
            module = file.relative_to(import_root).with_suffix("").as_posix().replace("/", ".")
        except ValueError:
            continue
        probe_name = f"auto_roundtrip_{file.stem}.py"
        target = run / "probes" / probe_name
        body = (
            f'"""Auto-generated round-trip probe for {module} ({len(pairs)} pair(s)).\n\n'
            'Detected from inverse function names; generated by `run.py autoprobe`."""\n\n'
            "from __future__ import annotations\n\n"
            "import " + module + " as _m\n\n"
            f"PAIRS = {pairs!r}\n\n\n"
            "def main() -> int:\n"
            "    failures = []\n"
            "    exercised = 0\n"
            "    for forward_name, backward_name in PAIRS:\n"
            "        forward = getattr(_m, forward_name, None)\n"
            "        backward = getattr(_m, backward_name, None)\n"
            "        if forward is None or backward is None:\n"
            "            continue\n"
            "        for value in INPUTS:\n"
            "            try:\n"
            "                middle = forward(value)\n"
            "                if middle == value:\n"
            "                    continue\n"
            "                exercised += 1\n"
            "                restored = backward(middle)\n"
            "            except Exception:\n"
            "                continue\n"
            "            if restored != value:\n"
            "                failures.append(\n"
            "                    f\"{forward_name}->{backward_name} did not restore the input: \"\n"
            "                    f\"{value[:80]!r}\"\n"
            "                )\n"
            "                break\n"
            "    if failures:\n"
            "        print(\"FAIL (\" + str(len(failures)) + \" pair(s))\")\n"
            "        for failure in failures:\n"
            "            print(\" -\", failure)\n"
            "        return 1\n"
            "    if exercised == 0:\n"
            "        print(\"INCONCLUSIVE: no pair changed any input\")\n"
            "        return 2\n"
            "    print(f\"PASS: {len(PAIRS)} pair(s), {exercised} round-trip(s) restored the input\")\n"
            "    return 0\n\n\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n"
        )
        target.write_text(body.replace("PAIRS =", AUTO_INPUTS_SOURCE + "\nPAIRS =", 1), encoding="utf-8")
        written += 1
        print(f"{probe_name}: {len(pairs)} pair(s) in {module}")
    if not written:
        print("no inverse function pairs found")
    else:
        print(f"\n{written} probe(s) scaffolded under {run / 'probes'}")
        print("next: `python run.py probe <run> --python <repo interpreter> --cwd <repo>`")
    return 0


def _gh_json(*args: str) -> Any:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8").stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def cmd_collide(args: argparse.Namespace) -> int:
    """Check whether the bug is already being worked on before writing any code.

    Four independent signals, because each one misses cases the others catch:
    an issue's timeline cross-references (a linked PR), a keyword search over **open** PRs
    (a PR that never mentions the issue number), a filename search over open PRs, and a
    keyword/filename search over **merged** PRs (the fix may already be in).

    Writes a machine-readable artifact and, with ``--require-clear``, exits non-zero unless the
    verdict is CLEAR -- so the check can gate opening a PR instead of living in a checklist.
    """
    repo = args.repo
    verdict = "CLEAR"
    related = False
    findings: list[dict] = []

    def add(kind: str, number: int, title: str, url: str) -> None:
        findings.append({"kind": kind, "number": number, "title": title, "url": url})

    if args.issue:
        timeline = _gh_json(
            "api", "-H", "Accept: application/vnd.github+json",
            f"repos/{repo}/issues/{args.issue}/timeline?per_page=100",
        ) or []
        for event in timeline:
            if not isinstance(event, dict) or event.get("event") != "cross-referenced":
                continue
            item = event.get("source", {}).get("issue") or {}
            if item.get("state") == "open":
                add("linked-open-pr", item.get("number", 0), item.get("title", ""), item.get("html_url", ""))
        if any(f["kind"] == "linked-open-pr" for f in findings):
            verdict = "BLOCKED"

    queries: list[tuple[str, str]] = []
    for keyword in args.keyword or []:
        queries.append(("keyword", keyword))
    for filename in args.file or []:
        queries.append(("file", Path(filename).name))

    quote = __import__("urllib.parse", fromlist=["quote"]).quote
    target_names = list(args.file or [])
    for kind, value in queries:
        for state in ("is:open", "is:merged"):
            label = "open" if state == "is:open" else "merged"
            result = _gh_json(
                "api",
                "search/issues?q=" + quote(f"repo:{repo} is:pr {state} {value}") + "&per_page=20",
            ) or {}
            for item in result.get("items", []):
                number = item["number"]
                # A keyword hit is only a duplicate when the PR actually touches one of the
                # target files: broad batch PRs match common words without overlapping the work.
                touches = False
                if target_names and state == "is:open":
                    files = _gh_json("api", f"repos/{repo}/pulls/{number}/files?per_page=100") or []
                    changed = [f.get("filename", "") for f in files]
                    touches = any(
                        any(
                            path.endswith(spec) if "/" in spec else Path(path).name == spec
                            for path in changed
                        )
                        for spec in target_names
                    )
                add(f"{label}-pr", number, item["title"], item.get("html_url", ""))
                if state == "is:open" and (touches or not target_names):
                    verdict = "BLOCKED"
                elif state == "is:open" and not touches:
                    related = True

    unique = {(f["kind"], f["number"]): f for f in findings}
    findings = sorted(unique.values(), key=lambda f: (f["kind"], f["number"]))
    has_merged = any(f["kind"] == "merged-pr" for f in findings)

    record = {
        "repo": repo,
        "issue": args.issue,
        "keywords": args.keyword or [],
        "files": args.file or [],
        "verdict": verdict,
        "merged_candidates": has_merged,
        "related_open_prs": related,
        "findings": findings,
    }
    out_dir = run_dir(args.name or repo.replace("/", "__"))
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / "collision-results.json"
    artifact.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    print(f"verdict: {verdict}")
    for finding in findings:
        print(f" - {finding['kind']}: #{finding['number']} {finding['title']}")
    if not findings:
        print(" - no linked, matching, or file-touching PRs found (open or merged)")
    if related:
        print(" ! open PRs matched keywords but touch none of the target files: related, not a duplicate")
    if has_merged:
        print(" ! merged PRs matched: confirm whether the fix is already in main before implementing")
    print(f"artifact: {artifact}")
    print("Reminder: a PR that never mentions the issue number is only found by keyword/file search.")

    if args.require_clear and (verdict != "CLEAR" or has_merged):
        return 1
    return 0
def cmd_issues(args: argparse.Namespace) -> int:
    """List open issues and mark the ones no PR or assignee has claimed.

    An issue whose timeline has no cross-reference, no assignee and no comments is the only
    shape worth starting: everything else already has someone attached. The check is
    deliberately conservative -- a false "claimed" costs one skipped issue.
    """
    repo = args.repo
    issues: list[dict] = []
    page = 1
    while len(issues) < args.limit:
        batch = _gh_json("api", f"repos/{repo}/issues?state=open&per_page=100&page={page}") or []
        if not isinstance(batch, list) or not batch:
            break
        issues.extend(item for item in batch if "pull_request" not in item)
        if len(batch) < 100:
            break
        page += 1
    if args.label:
        wanted = args.label.lower()
        issues = [
            item for item in issues
            if any(str(entry.get("name", "")).lower() == wanted for entry in item.get("labels", []))
        ]
    issues = issues[: args.limit]

    rows = []
    for issue in issues:
        timeline = _gh_json(
            "api", "-H", "Accept: application/vnd.github+json",
            f"repos/{repo}/issues/{issue['number']}/timeline?per_page=100",
        ) or []
        refs = []
        for event in timeline:
            if not isinstance(event, dict) or event.get("event") != "cross-referenced":
                continue
            source = event.get("source", {}).get("issue", {})
            if source.get("number"):
                refs.append(
                    {"number": source["number"], "state": source.get("state"), "title": source.get("title", "")}
                )
        open_refs = [ref for ref in refs if ref["state"] == "open"]
        unclaimed = not refs and not issue.get("assignees") and issue["comments"] == 0
        rows.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "created": (issue.get("created_at") or "")[:10],
                "comments": issue["comments"],
                "assignees": [person.get("login") for person in issue.get("assignees", [])],
                "linked_prs": refs,
                "open_linked_prs": open_refs,
                "unclaimed": unclaimed,
            }
        )
    rows.sort(key=lambda row: (not row["unclaimed"], row["created"]))

    path = run_dir(args.name or repo.replace("/", "__"))
    path.mkdir(parents=True, exist_ok=True)
    out = path / "issues-results.json"
    out.write_text(json.dumps({"repo": repo, "label": args.label, "rows": rows}, indent=2) + "\n", encoding="utf-8")

    free = [row for row in rows if row["unclaimed"]]
    print(f"{len(rows)} open issue(s), {len(free)} unclaimed -> {out}")
    for row in rows[: args.show]:
        state = "UNCLAIMED" if row["unclaimed"] else "claimed"
        links = ",".join(f"#{ref['number']}" for ref in row["linked_prs"]) or "-"
        print(f"  {state:9s} #{row['number']:<5d} {row['created']} links={links:12s} {row['title'][:70]}")
    if free:
        print("\nnext: collide each candidate before implementing, e.g.")
        print(f"  python run.py collide {repo} --issue {free[0]['number']} --keyword \"<terms>\"")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """One command that reads every artifact and decides whether a finding is ready.

    Checks claims, probe verdicts, the red-to-green verification and the collision verdict.
    Exit 0 only when nothing is failing; warnings (merged candidates, missing optional
    artifacts) do not fail the gate unless the matching --require flag is set.
    """
    path = run_dir(args.name)
    failures: list[str] = []
    warnings: list[str] = []

    claims_path = path / "claims.json"
    if not claims_path.exists():
        failures.append("claims.json is missing; run `claims`")
    else:
        claims = json.loads(claims_path.read_text(encoding="utf-8"))
        if not claims:
            warnings.append("claims.json is empty; the scanner found no contract")
        print(f"claims            {len(claims):4d}")

    probes_path = path / "probe-results.json"
    if not probes_path.exists():
        failures.append("probe-results.json is missing; run `probe`")
    else:
        results = json.loads(probes_path.read_text(encoding="utf-8")).get("results", [])
        bad = [row["probe"] for row in results if row["verdict"] in {"FAIL", "CHECKER_BUG", "ERROR"}]
        print(f"probes            {len(results):4d} ({len(bad)} failing)")
        if bad:
            failures.append(f"failing probes: {bad}")

    verification_path = path / "verification.json"
    if not verification_path.exists():
        message = "verification.json is missing; run `baseline` before the fix and `verify` after"
        (failures if args.require_verification else warnings).append(message)
    else:
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        print(
            f"verification      fixed={len(verification['fixed'])} "
            f"still_failing={len(verification['still_failing'])} "
            f"regressed={len(verification['regressed'])}"
        )
        if not verification["fixed"]:
            failures.append("verification shows no red-to-green flip")
        if verification["still_failing"]:
            failures.append(f"still failing: {verification['still_failing']}")
        if verification["regressed"]:
            failures.append(f"regressed: {verification['regressed']}")

    collision_path = path / "collision-results.json"
    if not collision_path.exists():
        message = "collision-results.json is missing; run `collide`"
        (failures if args.require_collision else warnings).append(message)
    else:
        collision = json.loads(collision_path.read_text(encoding="utf-8"))
        print(
            f"collision         verdict={collision['verdict']} "
            f"related={collision.get('related_open_prs')} merged={collision.get('merged_candidates')}"
        )
        if collision["verdict"] != "CLEAR":
            failures.append(f"collision verdict is {collision['verdict']}")
        if collision.get("merged_candidates"):
            warnings.append("merged PRs matched the keywords; confirm the fix is not already in main")

    for warning in warnings:
        print(f"WARN {warning}")
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        print(f"\ngate: FAIL ({len(failures)} blocking, {len(warnings)} warning) -> {path}")
        return 1
    print(f"\ngate: PASS ({len(warnings)} warning) -> {path}")
    return 0


def _ts_callsites(args: argparse.Namespace, root: Path) -> int:
    """Best-effort TS/JS call-site scan: definitions and calls by name, no parameter wiring.

    A regex cannot see TypeScript's type system, so this deliberately stops at "where is it
    defined and where is it called"; the Python scanner gets the deeper unwired-option check.
    """
    name = re.escape(args.function)
    definition = re.compile(
        rf"(?:export\s+)?(?:async\s+)?function\s+{name}\s*\(|"
        rf"(?:const|let|var)\s+{name}\s*=\s*(?:async\s*)?\("
    )
    call = re.compile(rf"(?<![\w.]){name}\s*\(")
    patterns = ("*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.cjs")
    files = sorted({path for pattern in patterns for path in root.rglob(pattern)})
    definitions, calls = [], []
    for file in files:
        if "node_modules" in file.parts:
            continue
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            if definition.search(line):
                definitions.append({"file": str(file.relative_to(root)), "line": number})
                continue
            if call.search(line):
                calls.append({"file": str(file.relative_to(root)), "line": number})
    out = run_dir(args.name)
    out.mkdir(parents=True, exist_ok=True)
    (out / "callsites-results.json").write_text(
        json.dumps(
            {"function": args.function, "language": args.lang, "definitions": definitions, "calls": calls},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{args.function}: {len(calls)} call site(s), {len(definitions)} definition(s) [{args.lang}, regex]")
    for row in definitions:
        print(f"  def  {row['file']}:{row['line']}")
    for row in calls:
        print(f"  call {row['file']}:{row['line']}")
    print("note: TS/JS scan is name-based; parameter wiring is only checked for Python")
    return 0



def cmd_callsites(args: argparse.Namespace) -> int:
    """Report every production call site of a function and the options it never passes.

    Motivated by a real stall: a fix added an option to a helper and every focused test passed,
    yet no production caller enabled it, so the end-to-end behavior never changed and review kept
    asking for the same thing. An option that no call site passes is that defect's fingerprint.
    """
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"{root} is not a directory", file=sys.stderr)
        return 2
    if args.lang in {"ts", "js", "tsx", "jsx"}:
        return _ts_callsites(args, root)
    files = [
        file
        for file in sorted(root.rglob("*.py"))
        if ".venv" not in file.parts and "node_modules" not in file.parts
    ]

    definitions: list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == args.function:
                definitions.append((file, node))
    if not definitions:
        print(f"function {args.function!r} not found under {root}", file=sys.stderr)
        return 2

    calls: list[dict] = []
    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
            if name != args.function:
                continue
            keyword_names = {kw.arg for kw in node.keywords if kw.arg}
            calls.append(
                {
                    "file": str(file.relative_to(root)),
                    "line": node.lineno,
                    "positional": len(node.args),
                    "keywords": sorted(keyword_names),
                }
            )

    results = []
    for file, node in definitions:
        args_node = node.args
        params = [arg.arg for arg in [*args_node.posonlyargs, *args_node.args]]
        options = [arg.arg for arg in args_node.kwonlyargs]
        defaults = len(args_node.defaults)
        optional = params[len(params) - defaults :] if defaults else []
        passed_keywords = {name for call in calls for name in call["keywords"]}
        unwired = [name for name in [*optional, *options] if name not in passed_keywords]
        results.append(
            {
                "defined_in": str(file.relative_to(root)),
                "line": node.lineno,
                "params": params,
                "optional": optional,
                "keyword_only": options,
                "unwired": unwired,
            }
        )

    out = run_dir(args.name)
    out.mkdir(parents=True, exist_ok=True)
    (out / "callsites-results.json").write_text(
        json.dumps({"function": args.function, "calls": calls, "definitions": results}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"{args.function}: {len(calls)} call site(s), {len(results)} definition(s)")
    for row in results:
        print(f"  defined {row['defined_in']}:{row['line']}  params={row['params']}  optional={row['optional']}")
        if row["unwired"]:
            print(f"  UNWIRED options (never passed at any call site): {row['unwired']}")
    for call in calls:
        print(f"  call {call['file']}:{call['line']} positional={call['positional']} keywords={call['keywords']}")
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

    p_gate = sub.add_parser("gate", help="check every artifact before presenting a finding")
    p_gate.add_argument("name")
    p_gate.add_argument("--require-verification", action="store_true")
    p_gate.add_argument("--require-collision", action="store_true")
    p_gate.set_defaults(func=cmd_gate)

    p_probe = sub.add_parser("probe", help="phase 2: run every probe and record verdicts")
    p_probe.add_argument("name")
    p_probe.add_argument("--python", help="interpreter that can import the audited repo")
    p_probe.add_argument("--cwd", help="working directory for the probes (default: the run dir)")
    p_probe.add_argument("--timeout", type=int, default=300)
    p_probe.add_argument("--env", action="append", metavar="KEY=VALUE")
    p_probe.set_defaults(func=cmd_probe)

    p_base = sub.add_parser("baseline", help="snapshot current probe verdicts before a fix")
    p_base.add_argument("name")
    p_base.add_argument("--python")
    p_base.add_argument("--cwd")
    p_base.add_argument("--timeout", type=int, default=300)
    p_base.add_argument("--env", action="append", metavar="KEY=VALUE")
    p_base.set_defaults(func=cmd_baseline)

    p_verify = sub.add_parser("verify", help="re-run probes and report the red-to-green delta")
    p_verify.add_argument("name")
    p_verify.add_argument("--python")
    p_verify.add_argument("--cwd")
    p_verify.add_argument("--timeout", type=int, default=300)
    p_verify.add_argument("--env", action="append", metavar="KEY=VALUE")
    p_verify.set_defaults(func=cmd_verify)

    p_diff = sub.add_parser("diff", help="run a scenario against two versions and diff the output")
    p_diff.add_argument("name")
    p_diff.add_argument("--scenario", required=True, help="script: JSONL on stdin -> JSONL on stdout")
    p_diff.add_argument("--inputs", required=True, help="JSONL, one case per line")
    p_diff.add_argument("--old-python", help="interpreter for the baseline (default: current)")
    p_diff.add_argument("--new-python", help="interpreter for the candidate (default: current)")
    p_diff.add_argument("--cwd", help="working directory for the scenario")
    p_diff.add_argument("--timeout", type=int, default=600)
    p_diff.add_argument("--pip-version", help="baseline version installed from PyPI into a temp venv")
    p_diff.add_argument("--pip-package", help="PyPI package to install for --pip-version")
    p_diff.add_argument("--pip-python", default="3.12", help="Python version for the baseline venv")
    p_diff.add_argument("--old-env", action="append", metavar="KEY=VALUE")
    p_diff.add_argument("--new-env", action="append", metavar="KEY=VALUE")
    p_diff.add_argument("--max-diffs", type=int, default=50)
    p_diff.set_defaults(func=cmd_diff)

    p_issues = sub.add_parser("issues", help="list open issues and mark unclaimed ones")
    p_issues.add_argument("repo", help="owner/name")
    p_issues.add_argument("--name", help="run directory for issues-results.json")
    p_issues.add_argument("--label", help="only issues with this label")
    p_issues.add_argument("--limit", type=int, default=50)
    p_issues.add_argument("--show", type=int, default=25)
    p_issues.set_defaults(func=cmd_issues)

    p_col = sub.add_parser("collide", help="check linked/keyword/file collisions before writing code")
    p_col.add_argument("repo", help="owner/name")
    p_col.add_argument("--issue", type=int, help="issue number to follow cross-references from")
    p_col.add_argument("--keyword", action="append", help="keyword to search PRs for (open and merged)")
    p_col.add_argument("--file", action="append", help="file name searched in PRs (open and merged)")
    p_col.add_argument("--name", help="run directory for collision-results.json")
    p_col.add_argument("--require-clear", action="store_true", help="exit 1 unless the verdict is CLEAR")
    p_col.set_defaults(func=cmd_collide)

    p_call = sub.add_parser("callsites", help="list call sites and options no caller ever passes")
    p_call.add_argument("name")
    p_call.add_argument("--path", required=True, help="tree to scan")
    p_call.add_argument("--function", required=True)
    p_call.add_argument("--lang", default="python", help="python (AST) or ts/js (regex)")
    p_call.set_defaults(func=cmd_callsites)

    p_auto = sub.add_parser("autoprobe", help="scaffold round-trip probes for inverse function pairs")
    p_auto.add_argument("name")
    p_auto.add_argument("--path", required=True, help="package directory to scan")
    p_auto.add_argument("--import-root", help="directory that makes the package importable")
    p_auto.set_defaults(func=cmd_autoprobe)

    p_ex = sub.add_parser("examples", help="run fenced documentation examples as contracts")
    p_ex.add_argument("name")
    p_ex.add_argument("--path", required=True, help="Markdown file or directory of .md files")
    p_ex.add_argument("--lang", default="python")
    p_ex.add_argument("--python", help="interpreter for the examples")
    p_ex.add_argument("--cwd")
    p_ex.add_argument("--timeout", type=int, default=120)
    p_ex.set_defaults(func=cmd_examples)

    p_new = sub.add_parser("new-probe", help="scaffold a probe from a per-ecosystem template")
    p_new.add_argument("name")
    p_new.add_argument("--claim", required=True)
    p_new.add_argument("--family", default="roundtrip")
    p_new.add_argument("--ecosystem", default="python")
    p_new.add_argument("--description")
    p_new.add_argument("--out")
    p_new.set_defaults(func=cmd_new_probe)

    for phase in ("shrink", "report"):
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
