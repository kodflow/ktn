---
name: ktn-comments-pipeline
ktn-managed: true
---

# Pipeline — `ktn:ktn-comments`

Sequential phases run by the orchestrator (`ktn-orchestrator` agent).

## Phase 1 — Discover

- Walk the target path with `Glob "**/*.go"`.
- Skip `*_test.go` unless `--include-tests` is set.
- Skip vendored files and files carrying the generated-file marker
  `Code generated ... DO NOT EDIT.`. Do NOT treat a `//go:generate`
  directive line as evidence the file itself is generated — it only
  tells `go generate` which tool to invoke, and commonly appears in an
  otherwise hand-maintained file.

## Phase 2 — Audit (parallel per file)

For each file, dispatch a `ktn-comment-auditor` worker with read-only
access. The auditor returns a JSON list of declarations whose doc
comments are missing, malformed, or Javadoc-shaped, plus the line
ranges of `//:` blocks that need clarification.

## Phase 3 — Trace (parallel per affected file)

For each file with edits planned, dispatch `ktn-issue-tracer` to:

- Detect any `See: <repo>#<n>` or `//ktn:keep` markers.
- Optionally `git blame` the surrounding lines to surface useful
  history that the writer can incorporate.

## Phase 4 — Write (sequential per file, parallel across files)

Dispatch `ktn-comment-writer` with edit access. The writer:

- Rewrites only doc comments and `//:` block-intention comments.
- Never touches preserved blocks.
- Mentions named parameters / returns inline in prose.
- Uses `[Name]` symbol links for cross-references.

## Phase 5 — Validate

For each file touched, dispatch `ktn-go-validator` scoped to
`KTN-COMMENT-FUNC`. If the file also had `//:` block-intention rewrites
(Phase 4), dispatch it a second time scoped to `KTN-COMMENT-BLOCK` — one
invocation validates one scope and returns one JSON object, never both
at once. If either returns diagnostics, hand the file back to
`ktn-comment-writer` with them as feedback. Up to two retries; otherwise
mark the file as unresolved in the report.

## Phase 6 — Report

Aggregate per-file results into the global JSON summary:

```json
{
  "files": 12,
  "edits": [...],
  "preserved": [...],
  "unresolved": [...],
  "out_of_scope_skipped": [...]
}
```

The host process (`ktn-linter skill comments`) commits the changes in
the dedicated worktree and squash-merges back to the original branch.
