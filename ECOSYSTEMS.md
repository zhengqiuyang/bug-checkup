# Per-ecosystem methods

`bug-checkup` has two language-agnostic contracts and per-ecosystem adapters around them.

## The two contracts

1. **Probe**: an executable file that exits `0` PASS, `1` FAIL, `2` INCONCLUSIVE, `3` CHECKER BUG.
   The runner never imports it; it only reads the exit code and the printed line.
2. **Scenario**: reads JSONL on stdin, writes one JSONL result per input line on stdout. Used by
   `run.py diff` to compare two environments. Any runtime that can do stdin/stdout qualifies.

Because of these, the runner (`run.py probe`, `run.py diff`) works for every ecosystem. What differs
per ecosystem is (a) how a claim is extracted from source, and (b) how a probe/scenario is scaffolded.

## Capability matrix

| Ecosystem | Claim extraction | Probe scaffold | Differential |
|---|---|---|---|
| Python | docstrings + Markdown (implemented) | `templates/probes/python_probe.py.tmpl` | two interpreters, or one interpreter with different `PYTHONPATH` |
| Node / TypeScript | JSDoc / TSDoc comments (planned) | `templates/probes/node_probe.mjs.tmpl` (planned) | two `node_modules` trees / two package versions |
| Go | doc comments (planned) | `templates/probes/go_probe.go.tmpl` (planned) | two module versions or two binaries |
| Rust | `///` doc comments (planned) | `templates/probes/rust_probe.rs.tmpl` (planned) | two builds (`cargo build` per revision) |
| JVM | javadoc (planned) | `templates/probes/java_probe.java.tmpl` (planned) | two jars |

Property-testing libraries per ecosystem, when a probe wants generated inputs rather than a fixed
case: Python `hypothesis` (call it from a script, not the pytest plugin — that plugin deadlocked a
run), Node `fast-check`, Go `testing/quick` or `go test -fuzz`, Rust `proptest`.

## The compiler boundary (learned the hard way)

`run.py diff` compares two *runtime environments*. When the audited logic lives in a compiled
extension, a released wheel and a source checkout can share the same compiled binary — the diff then
shows nothing even though the source changed. Before trusting a zero-diff result, confirm that the
two environments actually load different code for the paths under test:

```bash
# Python
python -c "import pkg, pathlib; print(pathlib.Path(pkg.__file__).parent)"
# Node
node -e "console.log(require.resolve('pkg'))"
```

If both resolve to the same compiled artifact, build the candidate revision (or run the scenario in
CI where it is built) before drawing a conclusion.

## Baseline note (headroom, 2026-11-15)

`diff-results.json` is the clean run (no canary). It records **0 differences**, and that number is
*not* evidence that the two trees behave identically: the headroom logic under test lives in the
compiled `headroom._core` binary, and both environments load the same 0.44.0 build (the source
checkout has that wheel's `.pyd`). The Python layer differs only in tests. See `ECOSYSTEMS.md`,
"the compiler boundary".