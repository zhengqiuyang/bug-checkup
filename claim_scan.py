#!/usr/bin/env python3
"""Phase 1 of correctness-audit: extract the claims a codebase makes about itself.

Scans Python docstrings/comments and Markdown prose for contract vocabulary
("lossless", "idempotent", "reversible", ...) and emits `claims.json`, one row per
claim with its source location and the probe that would falsify it.

Usage:
    python claim_scan.py <path> [--out claims.json] [--include-tests]

Exit code 0 always; the artifact is what matters.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# family -> (pattern, probe id, short rationale)
FAMILIES: dict[str, tuple[re.Pattern[str], str]] = {
    "roundtrip": (
        re.compile(r"\b(lossless(ly)?|reversible|round[- ]?trip(s|ped|ping)?|preserves? (exactly|the original))\b", re.I),
        "prop-roundtrip",
    ),
    "idempotence": (
        re.compile(r"\b(idempoten(t|ce)|safe to (re-?run|repeat)|no[- ]?op when)\b", re.I),
        "prop-idempotent",
    ),
    "determinism": (
        re.compile(r"\b(deterministic(ally)?|reproducible|stable (output|ordering|bytes)|same (input|result))\b", re.I),
        "prop-deterministic",
    ),
    "preservation": (
        re.compile(
            r"\b(preserves?|keeps?|kept|leaves?|left|returns?)\b[^.]{0,60}?"
            r"\b(exactly|unchanged|untouched|verbatim|intact|as[- ]is)\b",
            re.I,
        ),
        "prop-preservation",
    ),
    "exclusivity": (
        re.compile(
            r"\b(must never|never (returns?|raises?|mutates?|modifies?|loses?|drops?|writes?)|"
            r"exactly one|at most one|only one)\b",
            re.I,
        ),
        "prop-negative-case",
    ),
    "concurrency": (
        re.compile(r"\b(thread[- ]?safe|race[- ]?free|concurren(t|cy) safe|lock[- ]?free)\b", re.I),
        "prop-concurrency",
    ),
    "bounds": (
        re.compile(r"\b(at most|no (more|larger) than|bounded by|upper[- ]bound|within the limit)\b", re.I),
        "prop-bounds",
    ),
}

# A = strong, specific contract worth probing first; B = common vocabulary that needs a human
# pass before it is worth a probe.
TIER: dict[str, str] = {
    "roundtrip": "A",
    "idempotence": "A",
    "determinism": "A",
    "concurrency": "A",
    "bounds": "A",
    "preservation": "B",
    "exclusivity": "B",
}

# A claim stated in the negative ("not idempotent", "never lossless") is not a claim of the
# property; refuse to treat it as a target.
NEGATION = re.compile(r"\b(not|never|no|non|isn't|aren't|cannot|can't|without)\b[\w\s-]{0,24}$", re.I)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
WHITESPACE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class Claim:
    id: str
    kind: str
    tier: str
    text: str
    source: str
    line: int
    symbol: str | None
    visibility: str  # public | module | docs | comment
    probe: str


def _sentences(text: str) -> list[str]:
    normalized = WHITESPACE.sub(" ", text)
    return [s.strip() for s in SENTENCE_SPLIT.split(normalized) if s and s.strip()]


def _negated(sentence: str, match_start: int) -> bool:
    prefix = sentence[:match_start]
    return bool(NEGATION.search(prefix[-40:]))


def _claims_in(text: str) -> list[tuple[str, str, int]]:
    """Return (kind, sentence, offset) for every non-negated claim in ``text``."""
    found: list[tuple[str, str, int]] = []
    for sentence in _sentences(text):
        offset = text.find(sentence)
        for kind, (pattern, _probe) in FAMILIES.items():
            match = pattern.search(sentence)
            if not match or _negated(sentence, match.start()):
                continue
            if len(sentence) > 400:  # a paragraph is not a claim
                continue
            found.append((kind, sentence, offset))
            break  # one claim per sentence is enough
    return found


def _docstring_nodes(tree: ast.AST) -> list[tuple[str | None, str, int, str]]:
    """(symbol, docstring, line, visibility) for module/class/function docstrings."""
    out: list[tuple[str | None, str, int, str]] = []
    module_doc = ast.get_docstring(tree, clean=False)
    if module_doc:
        out.append((None, module_doc, 1, "module"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        private = node.name.startswith("_") and not node.name.startswith("__")
        out.append((node.name, doc, node.lineno, "docs" if private else "public"))
    return out


def scan_python(path: Path) -> list[Claim]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    claims: list[Claim] = []
    for symbol, doc, line, visibility in _docstring_nodes(tree):
        for kind, sentence, _offset in _claims_in(doc):
            claims.append(_make(path, line, symbol, visibility, kind, sentence))
    return claims


def scan_markdown(path: Path) -> list[Claim]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    claims: list[Claim] = []
    in_fence = False
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for kind, sentence, _offset in _claims_in(raw):
            claims.append(_make(path, number, None, "docs", kind, sentence))
    return claims


def _make(path: Path, line: int, symbol: str | None, visibility: str, kind: str, sentence: str) -> Claim:
    probe = FAMILIES[kind][1]
    return Claim(
        id="",
        kind=kind,
        tier=TIER[kind],
        text=sentence,
        source=str(path),
        line=line,
        symbol=symbol,
        visibility=visibility,
        probe=probe,
    )


VISIBILITY_RANK = {"public": 0, "module": 1, "docs": 2}


def scan(
    root: Path,
    *,
    include_tests: bool = False,
    tier: str = "A",
    max_per_family: int = 40,
) -> list[Claim]:
    patterns = ("*.py", "*.md", "*.rst")
    claims: list[Claim] = []
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            parts = set(path.parts)
            if ".git" in parts or ".venv" in parts or "node_modules" in parts:
                continue
            if not include_tests and ("test" in path.name or "tests" in parts):
                continue
            claims.extend(scan_python(path) if path.suffix == ".py" else scan_markdown(path))

    # Stable ids, deduped on (source, line, kind, text).
    seen: set[tuple[str, int, str, str]] = set()
    unique: list[Claim] = []
    for claim in claims:
        key = (claim.source, claim.line, claim.kind, claim.text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(claim)

    if tier != "all":
        unique = [c for c in unique if c.tier == tier]
    unique.sort(key=lambda c: (VISIBILITY_RANK.get(c.visibility, 9), c.source, c.line))

    counted: dict[str, int] = {}
    capped: list[Claim] = []
    for claim in unique:
        if max_per_family and counted.get(claim.kind, 0) >= max_per_family:
            continue
        counted[claim.kind] = counted.get(claim.kind, 0) + 1
        capped.append(claim)

    numbered = [
        Claim(**{**asdict(claim), "id": f"c-{index:04d}"}) for index, claim in enumerate(capped, start=1)
    ]
    return numbered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract stated contracts from a codebase (Phase 1).")
    parser.add_argument("path", type=Path)
    parser.add_argument("--out", type=Path, default=Path("claims.json"))
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--tier", choices=["A", "B", "all"], default="A", help="A = strong contracts only")
    parser.add_argument("--max-per-family", type=int, default=40)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    root = args.path.resolve()
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 2

    claims = scan(
        root,
        include_tests=args.include_tests,
        tier=args.tier,
        max_per_family=args.max_per_family,
    )
    args.out.write_text(json.dumps([asdict(c) for c in claims], indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        by_kind: dict[str, int] = {}
        for claim in claims:
            by_kind[claim.kind] = by_kind.get(claim.kind, 0) + 1
        print(f"{len(claims)} claims -> {args.out}")
        for kind, count in sorted(by_kind.items(), key=lambda kv: -kv[1]):
            print(f"  {kind:14s} {count:4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
