---
name: ktn-review
description: |
  Review and fix KTN-Linter violations across all 446 rules, phase by
  phase, using the plan `ktn-linter prompt` already generates. Reads each
  rule's good/bad example before touching code and never suppresses a
  violation. Run via `ktn:ktn-review [path]`.
ktn-managed: true
---

# ktn:ktn-review — KTN Violation Review & Fix

Read the doctrine in `ktn-review/doctrine.md` and the pipeline in
`ktn-review/pipeline.md` before touching anything — they define what a
minimal, non-suppressing fix means on this project and the phase order
that keeps sequential structural changes from invalidating parallel work.

Run `ktn-linter prompt [path]` (default `./...`), where `[path]` is a Go
package pattern like `./pkg/foo` — the same thing `ktn-linter run`/`ktn-linter
prompt` themselves take, never the generated plan file's own path. It writes
`.claude/plans/linter-feedback.md` and `.claude/contexts/linter-feedback.md`
whenever violations exist and reports the total count. Zero violations
means nothing to do — report that and stop; Phase 9 health advisories are
informational only, not something this skill acts on.

Otherwise, dispatch `ktn-review-orchestrator` with both file paths. It
works the phases in the order `pipeline.md` describes — structural phases
sequentially with a re-scan between each, later phases fanned out by
each chain's touched-file set once the tracer finds its call sites — and
validates every fix with `ktn-go-validator` before moving on, retrying a
failed fix against `ktn-violation-fixer` up to twice before reporting it
unresolved.

After the orchestrator returns, output a JSON summary:

```json
{
  "phases_completed": <int>,
  "violations_fixed": <int>,
  "violations_unresolved": [{"file": "...", "rule": "...", "message": "...", "reason": "..."}],
  "files_touched": ["..."]
}
```

`message` is the original diagnostic text (rule + file + message is the
unresolved-violation fingerprint `pipeline.md`'s Phase 6 convergence
loop uses — deliberately not line number, which a later fix elsewhere
in the file can shift); `reason` explains why it stayed unresolved
(e.g. "gofmt failure persisted after 2 retries").
