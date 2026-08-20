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

### Pinning a version

```bash
KTN_VERSION=v1.45.2 curl -sSL https://raw.githubusercontent.com/kodflow/ktn/main/install.sh | bash
```

## Releases

Only one release is kept here at a time: `releases/latest` always points at the
current stable version. Release candidates are not published to this channel.

## Usage

```bash
ktn-linter lint ./...
```

## License

MIT
