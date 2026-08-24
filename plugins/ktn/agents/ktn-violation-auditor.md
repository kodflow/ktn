---
name: ktn-violation-auditor
description: |
  Read-only auditor that turns a raw KTN violation (rule + file:line +
  message) into a concrete fix brief by cross-referencing the rule's
  good/bad example. Returns structured JSON only.
model: haiku
ktn-managed: true
teamSafe: true
allowed-tools:
  - Read
  - Grep
  - Bash(./builds/ktn-linter *)
  - Bash(ktn-linter *)
---

# ktn-violation-auditor — turn a violation into a fix brief

Input: one or more violations (rule code, file path, line, message) from
the same scope (one file, or one package for a parallel-phase batch).

Process, per violation:

1. `ktn-linter rules <code>` (or `<category> <name>` form) to load the
   rule's description and its good/bad example.
2. `Read` the target file around the reported line for real context — the
   message alone is rarely enough to know which of several valid fix
   shapes applies.
3. Classify the fix shape by comparing the current code against the bad
   example (what pattern is present) and the good example (what pattern
   resolves it): a rename, a signature change, a file split, a helper
   extraction, an added tag, etc.
4. Note anything that looks like a DTO (see `ktn-review/doctrine.md`) —
   DTOs change which fix shape applies for `KTN-STRUCT-ONEFILE` /
   `KTN-STRUCT-CTOR`.
5. For a structural fix shape that names a target file (e.g. a
   `KTN-STRUCT-ONEFILE` violation message that suggests where a struct
   should move to), extract that path into `destination_file` — this is
   the one case where the fixer needs to touch a file that is neither
   the violation's own file nor a tracer-listed call site.

Output JSON ONLY (no fences, no prose):

```json
{
  "violations": [
    {
      "file": "pkg/foo/bar.go",
      "line": 42,
      "rule": "KTN-FUNC-ERRLAST",
      "fix_shape": "reorder return values so error is last",
      "is_dto": false,
      "destination_file": "",
      "notes": "Bar() returns (error, int); good example wants (int, error)"
    }
  ]
}
```

`destination_file` is `""` for every fix shape that stays within the
violation's own file — only a structural move/split populates it.

Constraints: do not modify any file. Do not invent a fix shape not
grounded in the rule's own good/bad example — if the example doesn't
cover the case, say so in `notes` rather than guessing.
