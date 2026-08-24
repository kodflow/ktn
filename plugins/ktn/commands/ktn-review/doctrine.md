---
name: ktn-review-doctrine
ktn-managed: true
---

# KTN Violation Fix Doctrine

Source of truth: this project's own `CLAUDE.md` and `ktn-linter rules
<code>`. When either disagrees with a general Go convention, the project's
own doctrine wins — it exists precisely because KTN encodes stricter rules
than the language requires.

## Mandatory

- Before touching a violation, run `ktn-linter rules <code>` and fix
  toward the documented good pattern — never invent a different shape.
  The plan/context files carry the violation locations and messages, not
  the good/bad examples; those come from `rules <code>` specifically.
- Fix the minimal diff that resolves the violation. A bug fix does not need
  surrounding cleanup; do not refactor, rename, or restructure beyond what
  the rule requires.
- Preserve behavior exactly. A lint fix changes structure, naming, or
  idiom — never the program's observable behavior — unless the violation
  itself is a correctness rule (e.g. `KTN-ERROR-*`, `govet` wrappers),
  in which case the fix corrects the bug the rule caught.
- Re-run the linter (`ktn-linter run <path>`, scoped with `--only-rule` or
  `--phases` where possible) after every fix and confirm the diagnostic is
  gone before moving to the next one.

## Forbidden

- `//nolint`, `//ktn:scaffold`, or any other suppression marker. This
  project runs zero-marker exemptions — heuristics decide what's exempt,
  never a comment.
- Path-based exclusions of any kind (`IsTestdataPath`, `strings.Contains(path,
  "testdata")`, a new `exclude:` entry, or similar) used to make a
  violation stop being reported instead of fixing it.
- Modifying, creating, or loosening `.ktn-linter.yaml` to disable a rule or
  shrink the active phase/rule set. Configuration management is not this
  skill's responsibility — fix the code, not the linter's config.
- Marking a violation as fixed without the corresponding diagnostic having
  actually disappeared from a real `ktn-linter run` invocation. A fix that
  "should" work is not a fix until it's verified.
- Touching a file outside the violation's own package/scope unless the fix
  genuinely requires it (e.g. updating a caller after a signature change
  the rule mandated) — and even then, the change stays minimal.

## Structural violations need extra care

Rules in phase 1 (`KTN-STRUCT-ONEFILE`, `KTN-TEST-SUFFIX`, ...) can
create, move, or delete files. Any phase whose `NeedsRerun` is true in the
context file changes the set of files/lines every later phase refers to —
fix and re-run before trusting subsequent phase data, never work multiple
`NeedsRerun` phases from a single stale scan.

## DTO convention

A struct targeted by `KTN-STRUCT-ONEFILE` or `KTN-STRUCT-CTOR` may be a
DTO (serialization boundary type). Check for a `dto:"..."` tag or a
`json:`/`yaml:`/`xml:` tag, or a name ending in `DTO`, `Request`,
`Response`, `Params`, `Input`, `Output`, `Payload`, `Message`, `Event`,
`Command`, or `Query` before assuming a constructor or single-struct-file
split is the right fix — DTOs are exempt from both rules by design, not by
suppression.

## Verbosity

Concision is not a goal for commit-adjacent reasoning, but the code diff
itself should be as small as the rule allows. A fix that touches ten lines
to satisfy a rule a two-line change would have resolved is not minimal.
