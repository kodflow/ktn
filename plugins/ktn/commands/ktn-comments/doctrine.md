---
name: ktn-comments-doctrine
ktn-managed: true
---

# KTN Comment Doctrine (Go-canonical)

Source of truth: <https://go.dev/doc/comment>.

## Mandatory

- First line of a function / type / var / const doc comment starts with
  the declaration name. For booleans, prefer `reports whether ...`.
- First line of a package doc comment starts with `Package <name>`.
- Free-form prose. Sentences end with a period.
- Reference symbols with `[Name]` or `[pkg.Name]` (Go 1.19+).
- Headings use `# Heading` on their own line (Go 1.19+).

## Forbidden

- Javadoc tags: `@param`, `@return`, `@throws`, `@see`.
- Structured blocks: `Params:`, `Returns:`, `Args:`, `Result:`, `Raises:`.
- Filler phrases: `This function does ...`, `Method to ...`.
- Internal implementation details (algorithm, complexity).
- Backticks around identifiers — use `[Name]` doc-links instead.

## Project conventions

- Doc-comment above a declaration: prefix `//`.
- Intention comment inside a control block (if/else/switch/for/return):
  prefix `//:`. The `//:` marker distinguishes audited / AI-generated
  intention comments from free-form human prose. KTN-COMMENT-BLOCK
  enforces this.
- Persistent issue annotation: `// See: <org>/<repo>#<n> — <one-line why>`.
  Such blocks MUST NEVER be rewritten.
- `//ktn:keep` on a comment line freezes the block above it.

## Verbosity

Concision is not a goal. If the why needs eight lines, write eight
lines — Go prose supports it natively.

## Canonical example

```go
// Frob recombines old with the latest delta and returns the resulting
// snapshot. Callers may safely assume the snapshot is immutable;
// mutation would race the readers in [pkg/snapshot.Reader].
//
// See: kodflow/ktn-linter#246 — concurrent readers expect immutable
// snapshots, so we copy-on-write rather than mutate in place.
func Frob(old State, delta Delta) State { ... }
```
