---
name: bug-checkup
description: Find correctness bugs — code violating a contract it states about itself (lossless, idempotent, reversible, deterministic, thread-safe) — and prove each with an executable repro and a red/green patch. Use for repo audits, "find real bugs", or preparing a contribution to a repository you do not maintain. Not a security audit.
---

# bug-checkup

Find bugs where the code **contradicts its own stated contract**, and prove it. Sibling of
`security-audit` (trust boundaries); this skill covers data and lifecycle correctness.

## Operating modes

- **Guidance**: use only the phases relevant to the question; do not create artifacts.
- **Audit**: run phases 1–6 and write the artifacts below into `runs/<repo>/`.
- **Contribute**: audit plus the Phase 0 gate and the contribution kit (issue first, then PR).

## Phase 0 — gate (contribute mode only)

Before touching a repo you do not own:

```bash
gh api repos/OWNER/REPO/contents/CONTRIBUTING.md   # collaborators-only / invite-only / approval-first?
gh api "search/issues?q=repo:OWNER/REPO+is:pr+is:merged&per_page=100"  # first-timer (NONE) merges?
gh api repos/OWNER/REPO/pulls?state=closed&per_page=100                 # merge rate + latency
```

Drop the repo if PRs are collaborators-only or need issue assignment you cannot get. Record the
verdict in `gate.json` with the evidence line for each gate.

## Phase 1 — claim map

```bash
python claim_scan.py <repo>/<package> --out claims.json          # tier A: strong contracts
python claim_scan.py <repo>/<package> --tier all --out claims-full.json
```

Tier A = roundtrip / idempotence / determinism / concurrency / bounds. Tier B = preservation and
exclusivity wording, which needs a human pass before it is worth a probe.

## Phase 2 — probe

For each claim you keep, write a test that **fails if the claim is false**:

| Claim | Probe |
|---|---|
| roundtrip / lossless | `decode(encode(x)) == x` over a hypothesis strategy of the accepted domain |
| idempotent | `f(f(x)) == f(x)` and state unchanged |
| deterministic | `f(x)` twice, byte-equal; or two equivalent inputs |
| thread-safe | concurrent readers/writers, invariant holds after joins |
| bounds | generated inputs at `limit-1 / limit / limit+1` |

Rules: the probe must be runnable offline; it must fail on current code or it proves nothing; keep
each probe next to a claim id.

## Phase 3 — shrink & classify

Run the probe; shrink the failing input to the smallest case. Then classify as `confirmed`,
`known` (link the issue/docs line), `not_a_bug` (claim was about internals), or `needs_validation`
(missing environment).

## Phase 4 — trace

Name the deciding source **line**, the call chain from the public boundary, and the user-visible
impact (silent data loss, wrong type, dropped rows, lost compression, hang).

## Phase 5 — fix & verify

Patch on a branch. Record, verbatim:

1. the failing test output on the unpatched tree (red),
2. the passing output with the patch (green),
3. the repo's own tests for the touched area,
4. the repo's lint/format/type commands.

`verification.json` holds the commands and their real output. Never paraphrase output.

## Phase 6 — report or contribute

- **Audit**: `REPORT.md` one section per confirmed finding + `findings.json`.
- **Contribute**: issue first (repro, root cause, impact), then PR with the repo's template, a
  `Closes #N` link, and the red/green evidence. Re-check for an existing PR on that issue
  **through the issue timeline**, because text search misses linked PRs.

## Hard rules

- No finding without a failing repro.
- No probe on internals or monkeypatched state.
- No PR where the repo forbids them; no PR on an issue someone is already implementing.
- State what you did not test. Credibility comes from the caveat, not from omitting it.

## Probe format (learned the hard way)

A probe is a **standalone script**, not a pytest module:

- runs with the repository's own interpreter and nothing else;
- exits `0` PASS, `1` FAIL, `2` INCONCLUSIVE;
- prints one line: `PASS: <n> cases (<m> actually rewritten)`, or the failing input;
- proves it exercised the code — a probe that never changed anything is INCONCLUSIVE, not PASS.

pytest is optional for the repository under audit, but a probe must not depend on a pytest plugin:
in one audit, `hypothesis`'s pytest plugin deadlocked the whole run on Windows, and the probe
looked like a hang rather than a result.
## Probe self-validation

A probe contains a checker, and a broken checker reports false findings in both directions (we
shipped one: a pointer regex that missed the delta form, and a span comparison against the whole
block instead of a sub-run). Every probe therefore validates its own checker first — recognises a
real pointer, rejects an absent span — and exits `3` (CHECKER BUG) before evaluating the code.
"PASS" from an unvalidated checker means nothing.