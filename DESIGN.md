# bug-checkup — design

A coding-agent skill that finds **correctness bugs** — a program violating a contract it states about
itself — and proves each one with an executable artifact.

Sibling of [`security-audit`](https://github.com/cloudflare/security-audit-skill): that skill hunts
violations of a **trust boundary**; this one hunts violations of a **stated contract**. They compose:
run `security-audit` for authorization/injection/sandbox questions, this one for data and lifecycle
correctness.

## Why this exists

- Security auditing has a mature, widely used skill; correctness auditing has nothing comparable.
- The bugs that hurt most in library code are contract bugs: "lossless" that loses types,
  "idempotent" that isn't, "reversible" that isn't, "preserves order" that doesn't.
- Agents are good at reading claims and generating property tests, and bad at guessing where bugs
  are. Anchor on the claim, not on intuition.
- Maintainers reject findings without reproducible evidence. Every finding here carries a failing
  test and, when a fix is in scope, a red/green verification.

## Non-goals

- Not a security audit (no trust-boundary reasoning, no CVE work).
- Not a general code review (style, naming, architecture).
- Not a fuzzing-heavy campaign; fuzzing is one probe source among several.
- No unsolicited PRs. Contribution mode is opt-in and gated.

## Phases

| # | Phase | Input | Artifact |
|---|-------|-------|----------|
| 0 | **Target gate** (contribution mode only) | repo | `gate.json` |
| 1 | **Claim map** | source, docs, tests | `claims.json` |
| 2 | **Probe** | claims | `probes/` + `probe-results.json` |
| 3 | **Shrink & classify** | failing probes | `candidates.json` |
| 4 | **Trace** | candidates | `findings.json` (root cause + impact) |
| 5 | **Fix & verify** | findings | `verification.json` (red/green, suite) |
| 6 | **Report or contribute** | verified findings | `REPORT.md` / issue + PR kit |

Phase 0 is the only phase that talks to GitHub. Phases 1–5 run entirely on a local checkout, which
keeps the engine usable for private repos and offline.

## Phase 0 — Target gate

Runs before any work on a repo you do not own. Anything that fails a gate is dropped, with the
reason recorded.

- PR policy: `CONTRIBUTING.md` / `README` / template text — `collaborators-only`, invite-only,
  "get maintainer approval first", or "issue assignment required" all change the plan.
- First-timer evidence: merged PRs by `NONE`-association authors in the last ~100 closed PRs.
- Fork CI: check-runs on a recent external PR are non-zero and not `action_required` for the whole
  history.
- Queue depth: open PR count (a 600-PR queue means a new PR is invisible).
- Dedupe: search open issues/PRs, then confirm with the **issue timeline** cross-references — text
  search misses linked PRs (it missed four in one repo).

Artifact: `{"repo", "policy", "policy_evidence", "first_timer_merges", "fork_ci", "open_prs", "verdict": "go|no-go", "reason"}`

## Phase 1 — Claim map

Extract claims a program makes about itself. Sources, in order of trust: tests, docstrings,
type/doc prose, README, comments. Each claim becomes a row:

```json
{"id": "c-001", "kind": "roundtrip", "text": "Lossless and reversible.", "source": "src/x.py:42",
 "subject": "encode/decode pair", "probe": "prop-roundtrip", "confidence": "high"}
```

Claim families and the probe each implies:

| Family | Trigger words | Probe |
|--------|---------------|-------|
| roundtrip | lossless, reversible, round-trip, preserves | decode(encode(x)) == x |
| idempotence | idempotent, safe to re-run, no-op | f(f(x)) == f(x) |
| determinism | deterministic, stable, reproducible | f(x) twice, byte-equal |
| ordering | preserves order, stable order | relative order check |
| preservation | keeps, unchanged, untouched, verbatim | subset/equality oracle |
| exclusivity | never, cannot, must not, exactly one | negative case search |
| concurrency | thread-safe, concurrent, race-free | interleaving test |
| bounds | at most, bounded, no larger than | limit assertion |

A claim is only actionable if it is **observable at a public boundary** (callable, CLI, wire format).
Claims about internals are recorded but not probed.

## Phase 2 — Probe

Turn each claim into a test that fails if the claim is false.

- **Property probes**: `hypothesis` generators over the claim's domain. A round-trip claim gets a
  composite strategy over the types the encoder accepts.
- **Differential probes**: compare against an independent oracle — stdlib, a reference
  implementation, or the same function on an equivalent input (e.g., two serializers).
- **Differential-by-version probes**: compare current checkout against the last release for paths
  the claim covers.
- **Example probes**: run every example in the docs/README; examples are claims in disguise.

Rules:
- The probe must fail on the current code, or it proves nothing.
- The probe must be runnable without network/model keys unless the claim is about them.
- Every probe is stored under `probes/` and referenced by claim id.

## Phase 3 — Shrink & classify

Minimize the failing input (hypothesis shrinks automatically; otherwise bisect). Then classify:

| Verdict | Meaning |
|---------|---------|
| `confirmed` | probe fails, claim is public, behavior is reachable without contrived state |
| `known` | documented limitation, open issue, or explicitly unsupported case |
| `not_a_bug` | claim was about internals, or the probe asserted something the claim never said |
| `needs_validation` | cannot run in this environment (missing toolchain, network) |

`known` requires a link (issue/PR/docs line) in `candidates.json`.

## Phase 4 — Trace

For each `confirmed` candidate: smallest source location that decides the behavior, plus the
call chain from the public boundary, plus impact in user terms (silent data loss, wrong type,
dropped rows, lost compression, crash).

The trace must name a **line**, not a file, and must explain why the claim is violated there.

## Phase 5 — Fix & verify

- Patch on a branch.
- Red first: the new test fails on the unpatched tree (record the exact failure output).
- Green: the same test passes with the patch.
- Suite: the repo's own tests for the touched area.
- Lint/format/type as the repo requires.
- Record every command and its real output in `verification.json`; never paraphrase.

## Phase 6 — Report or contribute

- **Audit mode**: `REPORT.md` from `findings.json`, one section per finding, with repro, trace,
  impact, and fix status. Machine-readable twin in `findings.json`.
- **Contribute mode**: issue-first. Issue body from the finding; PR body from the repo's template;
  `Closes #N`; dedupe re-check before posting; never open a PR the repo forbids.

## Anti-patterns

- Reporting a claim you did not test. "Looks wrong" is not a finding.
- Probing internals, private members, or states only reachable by monkeypatching — not a public bug.
- Ignoring the repo's contribution policy and getting the work thrown away.
- Opening a PR on an issue someone is already implementing (check the timeline first).
- Presenting unit tests as runtime proof — run the public boundary.
- Burying the "not tested" caveat; stating it is what makes the rest credible.

## MVP

`claim_scan.py` (this repo) implements Phase 1 for Python and Markdown. Next: `probe_gen.py`
(round-trip and idempotence templates), `verify.py` (red/green runner), `report.py` (schema-validated
REPORT.md), `gate.py` (Phase 0).
