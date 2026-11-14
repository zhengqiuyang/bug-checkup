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

    Three independent checks, because each one misses cases the others catch:
    an issue's timeline cross-references (a linked PR), a keyword search over open PRs
    (a PR that never mentions the issue number), and open PRs touching the same files.
    """
    repo = args.repo
    verdict = "CLEAR"
    findings: list[str] = []

    if args.issue:
        timeline = _gh_json(
            "api", "-H", "Accept: application/vnd.github+json",
            f"repos/{repo}/issues/{args.issue}/timeline?per_page=100",
        ) or []
        linked = [
            event["source"]["issue"]
            for event in timeline
            if isinstance(event, dict) and event.get("event") == "cross-referenced"
        ]
        open_linked = [item for item in linked if item.get("state") == "open"]
        for item in open_linked:
            findings.append(f"issue #{args.issue} already linked to open PR #{item['number']}: {item['title']}")
        if open_linked:
            verdict = "BLOCKED"

    for keyword in args.keyword or []:
        result = _gh_json(
            "api",
            "search/issues?q="
            + __import__("urllib.parse", fromlist=["quote"]).quote(
                f"repo:{repo} is:pr is:open {keyword}"
            )
            + "&per_page=20",
        ) or {}
        for item in result.get("items", []):
            findings.append(f"open PR matches {keyword!r}: #{item['number']} {item['title']}")
            verdict = "BLOCKED"

    for filename in args.file or []:
        name = Path(filename).name
        result = _gh_json(
            "api",
            "search/issues?q="
            + __import__("urllib.parse", fromlist=["quote"]).quote(f"repo:{repo} is:pr is:open {name}")
            + "&per_page=20",
        ) or {}
        for item in result.get("items", []):
            findings.append(f"open PR mentions file {name}: #{item['number']} {item['title']}")
            verdict = "BLOCKED"

    unique = sorted(dict.fromkeys(findings))
    print(f"verdict: {verdict}")
    for finding in unique:
        print(" -", finding)
    if not unique:
        print(" - no linked, matching, or file-touching open PRs found")
    print("Reminder: a PR that never mentions the issue number is only found by keyword/file search.")
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

    p_col = sub.add_parser("collide", help="check linked/keyword/file collisions before writing code")
    p_col.add_argument("repo", help="owner/name")
    p_col.add_argument("--issue", type=int, help="issue number to follow cross-references from")
    p_col.add_argument("--keyword", action="append", help="keyword to search open PRs for")
    p_col.add_argument("--file", action="append", help="file whose name is searched in open PRs")
    p_col.set_defaults(func=cmd_collide)

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
