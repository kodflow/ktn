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

## Getting a licence

Anyone with a GitHub account can ask for one; a maintainer decides.

1. **Generate a key.** `ktn-linter license create` mints a subject UUID and an
   ed25519 pair in `~/.ssh/`. The private half never leaves your machine —
   what travels is the public half.
2. **Open the issue it prints.** The command hands you a prefilled link. The
   account that opens the issue is the identity the licence is bound to;
   there is nothing else to prove.
3. **Wait for approval.** Opening the issue does nothing by itself. A
   maintainer applies the `license:approved` label, which is what publishes
   your key. Until then `ktn-linter` reports that your licence is not listed.
4. **It activates within about 20 minutes.** The roster is re-signed three
   times an hour; your licence starts working at the next signature.

### One licence per account, three devices per licence

The licence is the thing you hold: one per account, always. What it
authorises is up to **three devices** — your laptop, your desktop, one more
machine.

Each device carries its own subject UUID and its own keypair, generated on
that machine, and the private half never travels. That is not bookkeeping: a
key copied between machines cannot be revoked on one without breaking the
other, and it turns a stolen laptop into a problem for every machine you own.

**Adding a device** is the same four steps as the first one, run on the new
machine. Open a separate issue for it — one request at a time, so wait for a
decision before opening the next.

**A device inherits the licence's term.** A machine enrolled six months in
expires with the rest, not a year later: one licence, one date, one renewal.

**Out of seats?** Revoke a device you no longer use and its seat comes back
immediately. Seats are counted from published keys, so a revoked device stops
counting the moment the roster is re-signed.

**Rotating a key** reuses that device's UUID: run `ktn-linter license update`
on the machine and post the new key. Only the account the device is bound to
can rotate it, which is what stops someone claiming a UUID that is not
theirs. A rotation never extends your term — rotating a key and paying for
another year are different acts.

### Getting a key for CI

CI needs a private key in a repository secret, and a key in a secret is
readable by every workflow and by everyone with write access.

Use one of your three device seats for CI rather than reusing a machine's
key: a secret that leaks must be revocable without taking one of your real
machines offline with it. Name it accordingly on the request, so the device
you revoke later is the one you meant.

Store the private key exactly as written, newlines included:

```bash
gh secret set KTN_LICENSE_KEY < ~/.ssh/<your-uuid>
```

The key must not be passphrase-protected — CI has nobody to type it.

### Updates are mandatory

The signed roster carries the minimum version allowed to run. When a release
raises it, an older binary refuses to start, upgrades itself, and re-runs the
command you typed — in the CLI and in the MCP daemon alike. There is no opt
out: the floor travels inside the same signed document the licence check
already reads, so it cannot be skipped by going offline.

Only one release is downloadable at a time. Older ones are removed from this
repository as each new one lands.

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
