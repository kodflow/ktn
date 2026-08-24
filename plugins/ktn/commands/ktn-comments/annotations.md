---
name: ktn-comments-annotations
ktn-managed: true
---

# Persistent annotations — preservation contract

Two annotation forms freeze a comment block. Any tool refactoring
comments MUST detect these markers and skip the block.

## `See: <org>/<repo>#<n> — <why>`

Issue-link annotation. Encodes the reason behind a non-obvious design
choice and ties it to a repository issue or pull request. Format:

- Starts with `See:` (capital `S`, no leading whitespace).
- Followed by `<org>/<repo>#<n>` (GitHub / GitLab style).
- Optional em-dash + one-line summary of the why.

Example:

```go
// See: kodflow/ktn-linter#246 — concurrent readers expect immutable
// snapshots, so we copy-on-write rather than mutate in place.
```

A doc-comment containing `See: <repo>#<n>` is preserved verbatim.

## `//ktn:keep`

Pragma. When a comment line literally equals `//ktn:keep`, the entire
comment group attached to it is frozen.

```go
//ktn:keep
// This wording was negotiated with legal — do not paraphrase.
type Disclaimer struct{}
```

## Detection rules

- Issue-link match: regular expression `(?m)^\s*//\s*See:\s+\S+/\S+#\d+`.
- Pragma match: literal line `//ktn:keep` (no leading whitespace, no
  trailing characters except newline).
- A match anywhere inside the comment group freezes the whole group.

## Auditor behavior

- `ktn-linter skill comments` reports preserved blocks in the
  `preserved` array of its JSON output.
- Manual review is welcome: humans may always edit these blocks; only
  AI tooling treats them as immutable.
