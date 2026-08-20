# ktn-linter — public distribution

Public distribution channel for [ktn-linter](https://github.com/kodflow/ktn-linter),
a strict Go linter. This repository carries no source code: it holds the
universal installer and the prebuilt release binaries.

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

## Releases

This channel is rolling-latest: exactly one release is kept, so
`releases/latest` is always well defined and the repository never accumulates.

A consequence worth knowing: **older versions cannot be installed from here.**
`KTN_VERSION` only resolves while it names the current release — any earlier
tag has already been pruned and the installer fails loudly rather than
installing something else. Release candidates are never published here.

## Usage

```bash
ktn-linter lint ./...
```

## License

MIT
