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