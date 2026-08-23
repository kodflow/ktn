---
name: ktn
description: This skill should be used when the user wants to "install ktn-linter", "set up ktn-linter", "upgrade ktn-linter", asks why the ktn-linter MCP server isn't responding, or wants Go code linted with ktn-linter but the binary isn't on PATH yet.
---

# ktn — install or upgrade ktn-linter

This plugin's `.mcp.json` already registers the `ktn-linter` MCP server — it
just assumes the `ktn-linter` binary is reachable on `PATH`. This skill's only
job is to get it there, and to decide whether that install should be scoped
to the current project or to the whole host. The exact procedure for each
scope is documented once, in `install/README.md` at the root of this
repository (https://github.com/kodflow/ktn) — read it before acting instead
of re-deriving the steps here, since that file is the source of truth this
skill defers to.

## 1. Decide the scope

Check for a `.claude/` directory in the current working tree.

- **Present → project scope.** This working tree already has its own Claude
  Code configuration; ktn-linter should be wired the same way, scoped to
  this project.
- **Absent → host scope.** There is nothing project-specific to attach to;
  install ktn-linter once for the whole host and let this plugin's
  `.mcp.json` pick it up from `PATH`.

## 2. Check whether the binary is already there

```bash
command -v ktn-linter && ktn-linter version
```

This only answers "is a binary present" — it does not mean nothing else is
needed. Keep going: if the user asked to *upgrade*, run `ktn-linter upgrade`
regardless of what's already installed (skip step 3's install command). In
project scope, still verify the MCP/hooks wiring below even when the binary
was already present — a binary on `PATH` says nothing about whether this
particular project has run `ktn-linter mcp install` yet.

## 3. Install (or wire), per scope

Follow `install/README.md` for the exact commands. Summarized:

- **Project scope**: get the binary (build or fetch, see `install/README.md`
  step 1), then run `ktn-linter mcp install` from the project root — it
  writes a project-local `mcp.json` and the Claude Code hooks in
  `.claude/settings.json`, and is idempotent (safe to re-run, so run it even
  if you're not sure it already happened).
- **Host scope**: run the universal installer
  (`curl -sSL https://raw.githubusercontent.com/kodflow/ktn/main/install.sh | bash`).
  It resolves the latest release, verifies the archive against
  `checksums.txt`, and installs into `/usr/local/bin` when writable, falling
  back to `~/.local/bin` otherwise. Nothing else to configure here — that's
  what the plugin's own `.mcp.json` is for, and it only takes effect once
  `/plugin install ktn` has been run.

If a binary is already installed and only needs a newer version, prefer
`ktn-linter upgrade` over rerunning the installer — it does the same
checksum-verified replacement in place, and additionally retries via a
non-interactive `sudo -n` when the existing install directory itself needs
elevated rights to replace (e.g. a root-owned `/usr/local/bin`).

## 4. Verify

```bash
ktn-linter version
```

Then confirm the MCP server actually answers — in Claude Code, run
`/mcp` and look for `ktn-linter` as connected. If it isn't, the binary is
present but not on the `PATH` this Claude Code process sees; report that to
the user rather than reinstalling again.
