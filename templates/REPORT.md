# <repo> correctness audit

- Run: `<run-name>`
- Commit: `<sha>`
- Date: `<date>`
- Scope: `<packages/paths audited>`
- Method: stated-contract extraction → property probes → red/green verification

## Summary

| Findings | Count |
|---|---|
| Confirmed | <n> |
| Known / documented | <n> |
| Needs validation | <n> |
| Probes run | <n> |

## Finding <id>: <title>

**Severity:** <critical|high|medium|low>
**Claim:** "<sentence>" — `<source>:<line>`
**Impact:** <user-visible consequence>

### Repro

```python
<minimal reproducer>
```

### Observed vs expected

```
observed: <output>
expected: <output>
```

### Root cause

`<source>:<line>` — <why the claim is violated here>

### Fix

<status: none / drafted / verified> — <branch or commit>

### Verification

```
red   : <command> -> <exit code>
green : <command> -> <exit code>
suite : <command> -> <exit code>
```

## Not tested

<what this audit did not cover>

## Method caveats

Claims were extracted from docstrings/docs as written; a claim that is itself wrong is a finding too,
but is reported separately from a violated claim.
