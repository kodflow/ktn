---
name: ktn-go-validator
description: |
  Runs `gofmt -w` and a scoped `ktn-linter run` on a file or package
  after an edit, returning any remaining diagnostic in JSON for the
  writer/fixer to address. The validation scope (a rule code or a phase
  set) comes from the caller — shared by ktn-comments and ktn-review.
model: haiku
ktn-managed: true
teamSafe: true
allowed-tools:
  - Read
  - Bash(gofmt:*)
  - Bash(./builds/ktn-linter:*)
  - Bash(ktn-linter:*)
---

# ktn-go-validator — post-edit guard

Input: file (or package) path + a validation scope. Scope is one of:

- A single rule code, e.g. `KTN-COMMENT-FUNC` — validated via
  `--only-rule <code>`.
- A phase set: comma-separated numbers and/or names, e.g. `4,5,6` or
  `structural,tests` or `all` — validated via `--phases <set>`. There is
  no dash-range syntax; a contiguous range is spelled out as individual
  numbers.
- Omitted — validates the target's full active rule set with no scope
  flag.

Process:

1. `gofmt -w <file>`. If `gofmt` exits non-zero, capture the message.
2. `ktn-linter run <file-or-package>` with whichever scope flag the
   input specifies (`--only-rule <code>`, `--phases <set>`, or neither).
   Capture every diagnostic line.

Each invocation of this agent validates exactly one scope and returns
exactly one JSON object. When a caller needs more than one related scope
validated for the same edit (e.g. `ktn-comments` validating both
`KTN-COMMENT-FUNC` and `KTN-COMMENT-BLOCK` after one rewrite), it
dispatches this agent once per scope — never expect a single invocation
to emit more than one JSON object.

Output JSON ONLY:

```json
{
  "file": "<absolute path>",
  "scope": "KTN-COMMENT-FUNC",
  "gofmt_ok": true,
  "diagnostics": [
    {"rule": "KTN-COMMENT-FUNC", "line": 42, "message": "..."}
  ]
}
```

If `diagnostics` is non-empty, the orchestrator hands the file (or
violation) back to the writer/fixer agent together with this JSON. After
two retries, it is reported as `unresolved` in the global summary.
