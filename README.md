# bug-checkup · v0.1

**Find the bugs where code breaks its own promise.**

Find **correctness bugs** — code contradicting a contract it states about itself — and prove each
one with an executable repro, a root-cause trace, and a red/green patch.

Sibling of [`security-audit`](https://github.com/cloudflare/security-audit-skill):
security audits trust boundaries; this audits stated contracts.

Not a security scanner, not a linter, not a code review bot.

## Quickstart

```bash
# 1. Phase 1 on a package you want to audit: extract the claims it makes about itself
python run.py claims ../headroom/headroom --name headroomlabs__headroom

# 2. Inspect the claims worth probing (tier A = strong, specific contracts)
cat runs/headroomlabs__headroom/claims.json

# 3. Later phases (interface stable, implementation lands as we use it)
python run.py gate   <owner/repo> --name <run>
python run.py probe  <run>
python run.py verify <run>
python run.py report <run>
```

Every run lives in `runs/<name>/` and holds the same artifact names, so runs are comparable and
resumable.

## Run layout

```
runs/<owner>__<repo>/
  gate.json           # phase 0: policy, first-timer merges, fork CI, queue, dedupe
  claims.json         # phase 1: stated contracts with source line + probe
  probes/             # phase 2: generated tests, one file per claim id
  probe-results.json  # phase 2: per-probe pass/fail + observed output
  candidates.json     # phase 3: shrunk failing cases + verdict
  findings.json       # phase 4: root cause, impact, severity
  verification.json   # phase 5: red/green commands and their real output
  REPORT.md           # phase 6: human report
  issue.md / pr.md    # phase 6 (contribute mode): ready-to-post drafts
```

Artifact shapes are frozen in [`schemas/`](schemas/). A phase may leave an artifact absent, but must
never invent a different shape.

## Status

| Phase | Artifact | State |
|---|---|---|
| 0 gate | `gate.json` | interface frozen, implementation pending |
| 1 claims | `claims.json` | **implemented** (`claim_scan.py`) |
| 2 probe | `probes/`, `probe-results.json` | **implemented** (`run.py probe`, `run.py new-probe`, `run.py autoprobe`) |
| 3 diff | `diff-results.json` | **implemented** (`run.py diff`, two environments) |
| 3 examples | `examples-results.json` | **implemented** (`run.py examples`) |
| 3 shrink | `candidates.json` | interface frozen, implementation pending |
| 4 trace | `findings.json` | interface frozen, implementation pending |
| 5 verify | `verification.json` | interface frozen, implementation pending |
| 6 report | `REPORT.md`, `issue.md`, `pr.md` | templates fixed |

Implementation order: `claims → probe → verify → gate → report`, because claims→probe→verify is the
loop that produces a first real finding.

## Phase 1 — claim map (implemented)

```bash
python run.py claims <path> --name <run> [--tier A|B|all] [--max-per-family N]
```

Scans Python docstrings and Markdown prose for contract vocabulary and maps each hit to the probe
that would falsify it.

- **Tier A** (probe first): roundtrip / lossless / reversible, idempotent, deterministic,
  thread-safe, bounded.
- **Tier B** (needs a human pass): "preserves/keeps/leaves … unchanged", "must never / exactly one".

Output is ranked by visibility (public → module → docs) and capped per family so a chatty docstring
cannot drown the list.

## Hard rules

- No finding without a failing repro.
- No probe on internals, private members, or monkeypatched-only states.
- Check the repo's contribution policy before writing code; check the issue **timeline** for an
  existing PR, not just text search.
- State what you did not test.

## Files

| File | Purpose |
|---|---|
| `SKILL.md` | agent-facing instructions (guidance / audit / contribute modes) |
| `DESIGN.md` | full design: phases, decisions, anti-patterns |
| `claim_scan.py` | phase 1 implementation |
| `run.py` | run-directory orchestrator; stable CLI for all phases |
| `schemas/` | frozen artifact shapes |
| `templates/` | report / issue / PR templates |

## Versioning

`VERSION` + `CHANGELOG.md`. Breaking an artifact shape requires a minor bump and a migration note.
