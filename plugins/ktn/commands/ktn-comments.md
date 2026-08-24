---
name: ktn-comments
description: |
  Refactor Go doc-comments to the canonical go.dev/doc/comment style.
  Drops Javadoc-style Params:/Returns: blocks. Preserves `//:` intention
  markers and `See: <repo>#<n>` issue annotations. Run via `ktn:ktn-comments
  [path]`.
ktn-managed: true
---

# ktn:ktn-comments — Go Doc Comment Refactor (KTN doctrine)

Read the doctrine in `ktn-comments/doctrine.md` then walk the target
path. For every Go declaration, ensure the doc comment follows the
canonical go.dev/doc/comment style (free prose, first line starts with
the declaration name). For every `//:` block-intention comment, ensure
it begins with an action verb. Preserve any block annotated with
`See: <repo>#<n>` or `//ktn:keep`.

After edits, run `gofmt -w` and `ktn-linter run --only-rule
KTN-COMMENT-FUNC` on the touched files — and, for any file with `//:`
block-intention rewrites, a second pass with `--only-rule
KTN-COMMENT-BLOCK`. Fix any remaining diagnostic before moving on.

Output a JSON summary:

```json
{
  "files": <int>,
  "edits": [{"path": "...", "decl": "...", "kind": "doc|block"}],
  "preserved": [{"path": "...", "reason": "issue-link|ktn:keep"}],
  "unresolved": [{"path": "...", "reason": "..."}],
  "out_of_scope_skipped": [{"path": "...", "reason": "..."}]
}
```
