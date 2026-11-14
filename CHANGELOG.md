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