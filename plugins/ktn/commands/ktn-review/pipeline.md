---
name: ktn-review-pipeline
ktn-managed: true
---

# Pipeline — `/ktn-review`

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
tests) are independent of each other and of file layout, so they run
**in parallel**, one worker per package.

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

For each file touched, dispatch `ktn-go-validator` scoped to the rule
just fixed (`--only-rule <code>`) — one violation, one dispatch, one
scope — or, once a whole phase batch converges, `--phases <n>` for a
single combined check. Also `go build`/`go test` on the owning package.
If a diagnostic remains, hand the file back to `ktn-violation-fixer` with
the diagnostic as feedback. Up to two retries; otherwise mark the
violation `unresolved` in the report.

A phase whose violations came from `NeedsRerun: true` in the context file
(any phase that can create/move/delete files) requires a full
`ktn-linter prompt <path>` re-generation before the next phase starts,
not just a per-file check or a bare `ktn-linter run` (which prints
diagnostics but never rewrites the plan/context files) — file layout may
have changed under later phases' feet, and the next phase's data must
come from a fresh generation, not the one read at Phase 1.

## Phase 6 — Report

Aggregate into the JSON summary documented in `ktn-review.md`. Then loop:
re-run `ktn-linter prompt [path]`; if violations remain, a fresh plan is
generated automatically and Phase 1 resumes on the new context. Repeat
until 0 violations or nothing further converges.
