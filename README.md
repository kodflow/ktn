# ktn-linter — public distribution

Public distribution channel for [ktn-linter](https://github.com/kodflow/ktn-linter),
a strict Go linter. This repository carries no source code: it holds the
universal installer and the prebuilt release binaries.

## Continuous integration

```yaml
- uses: kodflow/ktn/setup@main
  with:
    license-key: ${{ secrets.KTN_LICENSE_KEY }}

- run: ktn-linter run ./...
```

One input, deliberately. The subject UUID lives in the key's comment and the
public half is derivable from it, so passing them separately would only be
repeating what the key already says — and giving you three things to keep in
sync instead of one.

The action installs the linter, activates the licence, and verifies it against
the published roster before your workflow continues. It fails there rather than
several steps later inside a lint run, because a licence problem and a lint
failure read nothing alike in a log.

### Getting a key for CI

Run `ktn-linter license create` and open the issue it prints. **Use a subject
dedicated to CI rather than reusing a developer's**: a key in repository
secrets is readable by every workflow and by everyone with write access, so
you want to be able to revoke it without taking a person offline with it.

Store the private key exactly as written, newlines included:

```bash
gh secret set KTN_LICENSE_KEY < ~/.ssh/<your-uuid>
```

The key must not be passphrase-protected — CI has nobody to type it.

## Installation

```bash
curl -sSL https://raw.githubusercontent.com/kodflow/ktn/main/install.sh | bash
```

The installer resolves the latest release, verifies the archive against
`checksums.txt` before extracting it, and installs `ktn-linter` into
`/usr/local/bin` or `~/.local/bin`.

Supported platforms:

| OS      | amd64 | arm64 |
| ------- | ----- | ----- |
| linux   | ✅    | ✅    |
| darwin  | ✅    | ✅    |
| windows | ✅    | —     |

## Claude Code plugin

This repository also serves as a Claude Code plugin marketplace:

```text
/plugin marketplace add kodflow/ktn
/plugin install ktn
```

The plugin registers the `ktn-linter` MCP server (live diagnostics as you
edit) and installs three things:

- The `ktn:ktn` skill, which gets the binary itself onto `PATH` and, for a
  project that wants its own edit-time enforcement hooks rather than
  relying on the plugin alone, wires it locally. See
  [`install/README.md`](install/README.md) for the exact procedure.
- `ktn:ktn-review [path]` — reviews and fixes violations across all 446
  rules, phase by phase, using the plan `ktn-linter prompt` already
  generates.
- `ktn:ktn-comments [path]` — refactors Go doc-comments to the canonical
  `go.dev/doc/comment` style.

## Releases

This channel is rolling-latest: exactly one release is kept, so
`releases/latest` is always well defined and the repository never accumulates.

A consequence worth knowing: **older versions cannot be installed from here.**
`KTN_VERSION` only resolves while it names the current release — any earlier
tag has already been pruned and the installer fails loudly rather than
installing something else. Release candidates are never published here.

## Usage

```bash
ktn-linter run ./...
```

## Rules

`ktn-linter` runs several hundred analyzers: native KTN rules across naming,
structure, architecture, testing, performance, goroutines and modern Go
idioms, plus wrapped `go vet`, `modernize` and `staticcheck` checks. The
catalogue is generated from the running binary rather than duplicated here,
so it never drifts out of date:

```bash
ktn-linter rules                    # full catalogue, text output
ktn-linter rules --format=json      # machine-readable
ktn-linter rules --format=markdown  # for pasting into docs/PRs
```

## Configuration

```bash
ktn-linter config --generate        # write .ktn-linter.yaml with every rule
```

Per-rule severity, category filters, phase gating (`phases.enabled`) and
diff-vs-full review scope all live in `.ktn-linter.yaml`. `ktn-linter config
--generate` documents every key inline; there is no separate reference to
keep in sync.

## License

MIT
