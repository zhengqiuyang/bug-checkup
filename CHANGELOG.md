# Changelog

## [0.1.0] - 2026-11-15

Form frozen: run layout, artifact schemas, report/issue/PR templates, and a phase-stable CLI.

### Added
- `SKILL.md`: agent-facing modes (guidance / audit / contribute).
- `DESIGN.md`: seven-phase design and anti-patterns.
- `claim_scan.py`: phase 1, extracts stated contracts (tier A/B, visibility-ranked, per-family cap).
- `run.py`: run-directory orchestrator; `claims` implemented, other phases exit 3 with their plan.
- `schemas/`: gate, claims, candidates, findings, verification.
- `templates/`: REPORT.md, ISSUE.md, PR.md.

### Implementation order from here
`probe` → `verify` → `gate` → `report`, driven by real audits rather than speculative features.
## [0.1.1] - 2026-11-15

- Probe format fixed: standalone scripts (exit 0/1/2), no pytest-plugin dependency; a probe must
  prove it exercised the code or report INCONCLUSIVE.
- First real probe checked in: actor_values round-trip (claim c-0125) — PASS over 9 fixed + 60
  random tables, 61 blocks actually rewritten.
## [0.1.2] - 2026-11-15

- Renamed to `bug-checkup`: the tool is a checkup for a repository — claims are the checklist,
  probes are the tests, the report is the checkup result.
## [0.1.3] - 2026-11-15

- Probes must self-validate their checker (exit 3 = CHECKER BUG) before reporting a verdict.
- Two more probes for headroom `cross_turn_dedup` (no test references in the repo): prefix
  monotonicity (38 scenarios, 72 folds) and accuracy (45 scenarios, 92 folds) — both PASS.
## [0.1.4] - 2026-11-15

- Claim scan records reachability and importers; dead-module claims are filtered by default
  (`--include-unreachable` to keep them). Multi-line imports are parsed, which corrected a
  false "dead code" call made by grep in the headroom audit.
## [0.1.5] - 2026-11-15

- Generic probe executor: `run.py probe` runs `probes/*.py`, records exit codes as verdicts, and
  maps probes to claims via a `CLAIM = "c-0001"` line read as text.
- Probes checked in for headroom: `factor_values` round-trip, `cross_turn_dedup` prefix
  monotonicity and accuracy, `thinking_compactor` keep-last/drop/sticky-floor/determinism/
  fail-open. All PASS; no finding.
## [0.1.6] - 2026-11-15

- Probe checked in for `config_compressor` (claim c-0130): elision must fail closed when the CCR
  store write fails, an emitted elision must be recoverable from the stored original, and
  data-looking comment lines must never be dropped silently. PASS (1 elision case exercised).
- Probe requires the tier to be exercised, otherwise INCONCLUSIVE: a safety probe that never
  reaches the risky branch proves nothing.
## [0.1.7] - 2026-11-15

- Concurrency probe for `CompressionStore`: 8x40 concurrent stores stay within `max_entries`, no
  exceptions, duplicate re-store keeps every live entry. PASS.
- Money-gate probe for `bills_prior_thinking`: 16 model-id spellings classified per the documented
  rule, including the release-date trap. PASS.

Seven probes on headroom, zero findings — the repo is unusually well defended on these contracts.

## [0.2.0] - 2026-11-15

- `run.py diff`: differential runner. A scenario script reads JSONL on stdin and writes one JSONL
  result per input on stdout; the runner executes it under two environments and records where the
  outputs differ. Language-agnostic by protocol; per-environment variables supported
  (`--old-env` / `--new-env`).
- `run.py new-probe`: probe scaffolding from per-ecosystem templates, carrying the exit-code
  contract and the self-check hook.
- `ECOSYSTEMS.md`: per-ecosystem methods (claim extraction, probe scaffold, property library) and
  the compiler boundary — a wheel and a source checkout can share one compiled binary, so a
  zero-diff result must be justified by checking which code each environment actually loads.


## [0.3.0] - 2026-11-15

- `run.py examples`: documentation examples are implicit contracts. Fenced blocks are extracted
  and executed with the repository's interpreter; verdicts are classified so the signal survives:
  PASS, FRAGMENT (snippet needs surrounding code — NameError), NEEDS_ENV (network / API key /
  missing SDK), FAIL (a real contract problem). Only FAIL is a finding.
- First run on headroom's README: 4 blocks -> 1 FRAGMENT, 2 SKIP, 1 NEEDS_ENV, **0 FAIL**.


## [0.4.0] - 2026-11-15

- `run.py autoprobe`: detects inverse function pairs in a module (`encode`/`decode`,
  `fold`/`unfold`, `collapse`/`expand`, `serialize`/`deserialize`, `to_dict`/`from_dict`, …) and
  scaffolds a round-trip probe for each pair. Generated inputs are chosen so compression engages;
  a probe that never transforms an input reports INCONCLUSIVE.
- First run on headroom's transforms: 2 pairs (`collapse_runs`/`expand_runs`,
  `fold_repeated_blocks`/`unfold_repeated_blocks`) -> PASS.


## [0.7.0] - 2026-11-15

- Claim recall: five new families (`error_contract`, `return_shape`, ordering, passthrough,
  validation), package `__init__.py` files now resolve importers under their package name (this was
  why the instructor scan returned zero claims), and docstring code fences / doctests are stripped
  so examples cannot be mistaken for contracts. Measured: instructor 0 -> 5 tier-A, headroom
  199 -> 254 tier-A.
- `run.py baseline` + `run.py verify`: automated red-to-green loop. `baseline` snapshots probe
  verdicts before a fix; `verify` re-runs and writes `verification.json` with `fixed`,
  `still_failing`, `regressed` and exits 0 only on a clean flip. Exercised on instructor: FAIL ->
  PASS, `fixed: 1`, exit 0.
- `verify` is no longer a pending placeholder phase.
- `selftest.py`: the tool tests itself (claim families, example stripping, package reachability,
  verify delta) without pytest, so changes to the scanner have their own red/green.


## [0.8.0] - 2026-11-15

- `run.py issues`: open-issue scan with linked-PR detection; marks unclaimed issues (no
  cross-reference, no assignee, no comments). Validated on headroom (23 bug issues, all claimed)
  and instructor (0 open bug issues).
- `run.py diff --pip-version/--pip-package`: installs the released baseline into a reusable temp
  venv, so a differential run needs no manual environment setup. Exercised on instructor 1.17.0 vs
  the fixed source: 2 of 3 inputs differ (`data_present: false` -> `true` / clear `ValueError`).
- `run.py gate`: one command that reads claims, probe verdicts, verification and collision and exits
  0 only when nothing blocks. Confirmed on the instructor run: PASS with one merged-candidate warning.


## [0.8.1] - 2026-11-15

- `run.py callsites --lang ts|js`: name-based definition/call-site scan for TypeScript and
  JavaScript (no parameter-wiring check — that stays Python-only). Verified on a two-file fixture:
  1 definition, 2 call sites.


## [0.9.0] - 2026-11-15

- `run.py shrink` + `shrinker.py`: delta debugging for failing JSON inputs. Candidates drop
  keys/list items in halves, unwrap single-element containers, truncate strings and zero numbers;
  a candidate is kept only when the probe still exits 1. Writes `shrunk/<probe>.min.json` and
  `shrink-results.json`.
- Exercised twice: synthetic fixture 263 -> 14 bytes (`{"boom": true}`), and the real instructor
  failure 360 -> 32 bytes (`{"source": "", "media_type": ""}`) — the reduced case shows the defect
  is "any image without data", not just data URIs.
- `selftest.py` now covers the shrinker too.
