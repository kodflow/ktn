---
name: ktn-review-pipeline
ktn-managed: true
---

# Pipeline — `ktn:ktn-review`

Phases run by `ktn-review-orchestrator`. Unlike `ktn-comments` (which
discovers its own targets via `Glob`), discovery is already done by
`ktn-linter prompt` — this pipeline consumes its output rather than
re-deriving it.

## Phase 0 — Generate

Run `ktn-linter prompt [path]` (default `./...`). This (re)writes
`.claude/plans/linter-feedback.md` and `.claude/contexts/linter-feedback.md`
when violations exist, and reports the total violation/rule/package count.
Zero violations means nothing to do — stop here.

## Phase 1 — Sequence

Read both generated files. Violations are already grouped by KTN phase
(1-9) in the order the context file presents them. Phases 1-3
(structural, signatures, logic) carry inter-file risk — a structural fix
can move or delete a file another phase's violation still points at — so
they run **sequentially**, one phase fully resolved and re-verified
before the next starts. Phases 4-8 (performance, modern, style, comment,
tests) don't restructure files, but a rename or signature change can
still surface a call site in another package once the tracer looks —
schedule these by each chain's actual touched-file set (violation file
plus tracer-found call sites), not blindly by package: two chains run
**in parallel** only when their touched-file sets are disjoint.

## Phase 2 — Audit (per phase, per package within a parallel phase)

For the violations in scope, dispatch a `ktn-violation-auditor` worker
(read-only) with: the rule codes, file:line list, and messages from the
context file for that scope. The auditor cross-references each rule's
good/bad example (`ktn-linter rules <code>`) and returns a structured,
per-violation fix brief — what pattern the fix should follow, not a
guess at the diff itself.

## Phase 3 — Trace (per file with violations)

Dispatch `ktn-violation-tracer` (read-only) to gather what the auditor's
brief doesn't cover: `git blame` on the affected lines for relevant
history, and — critically, unlike comment-only rewrites — `Grep` for
call sites of anything the fix might rename, move, or resignature, so
the fixer knows the full blast radius before editing.

## Phase 4 — Fix (sequential per file, parallel across independent files)

Dispatch `ktn-violation-fixer` with edit access, given: the violation,
the auditor's brief, and the tracer's context (including call-site list).
The fixer applies the minimal diff per `doctrine.md`, updates any call
site the tracer flagged, and never suppresses.

## Phase 5 — Validate

For each file touched, dispatch `ktn-go-validator` against its **owning
package**, not the bare file — one violation, one dispatch, one scope
(`--only-rule <code>`) — or, once a whole phase batch converges,
`--phases <n>` for a single combined check against the same package.
`ktn-go-validator` only runs `go build`/`go test` when its target is a
package; a bare-file target skips that step entirely (its own contract),
so a file-scoped dispatch here would validate the lint rule but never
catch a fix that compiles clean per-file yet breaks the build or an
existing test. If a diagnostic remains, hand the file back to
`ktn-violation-fixer` with the diagnostic as feedback. Up to two
retries; otherwise mark the violation `unresolved` in the report.

A phase whose violations came from `NeedsRerun: true` in the context file
(any phase that can create/move/delete files) requires a full
`ktn-linter prompt <path>` re-generation before the next phase starts,
not just a per-file check or a bare `ktn-linter run` (which prints
diagnostics but never rewrites the plan/context files) — file layout may
have changed under later phases' feet, and the next phase's data must
come from a fresh generation, not the one read at Phase 1.

## Phase 6 — Report

Aggregate into the JSON summary documented in `ktn-review.md`, carrying
forward every violation already marked `unresolved` in an earlier pass
of this same run — its retry budget is spent, and re-running
`ktn-linter prompt` will surface it again verbatim. Then loop: re-run
`ktn-linter prompt [path]`; if violations remain, exclude any that match
an already-`unresolved` fingerprint — rule + file + diagnostic message,
NOT line number: a later fix elsewhere in the same file can shift line
numbers up or down, so a line-based fingerprint can both miss the
violation it should exclude (it moved) and wrongly exclude an unrelated
new violation that happens to land on the old line. Resume Phase 1 on
the rest. Stop when 0 violations remain, or when a full pass resolves
nothing new (every remaining violation is already
`unresolved`) — re-running prompt and resuming Phase 1 forever on a
violation that cannot converge is not progress.
