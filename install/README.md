# Installing ktn-linter

This is the procedure the `/ktn` plugin skill follows. It exists as a
standalone document, not inline in the skill, so the steps stay correct even
if the skill's own wording drifts — read this first if the two ever disagree.

There are two things to set up, and they're independent:

1. **The `ktn-linter` binary** — same install regardless of scope.
2. **Where it's wired into Claude Code** — host-wide (this plugin's own
   `.mcp.json`) or project-scoped (`mcp.json` + edit-time hooks local to one
   working tree). This is the actual "project vs host" decision.

## 1. Get the binary

```bash
curl -sSL https://raw.githubusercontent.com/kodflow/ktn/main/install.sh | bash
```

Resolves the latest release, verifies the archive against `checksums.txt`
before extracting, and installs into `/usr/local/bin` when writable, falling
back to `~/.local/bin` otherwise (see `install.sh` at the root of this repo
for the exact platform/checksum logic).

If you're working inside the `ktn-linter` source tree itself (private repo,
not this one), `make build` produces `builds/ktn-linter` instead — put that
on `PATH` and skip the installer.

Already installed? Use `ktn-linter upgrade` rather than rerunning the
installer — it's the same checksum-verified replacement in place, and it
additionally retries via a non-interactive `sudo -n` when the install
directory itself needs elevated rights to replace (a root-owned
`/usr/local/bin` being the common case). `-n` never prompts: with no cached
credential or passwordless rule, it fails fast with a clear error instead of
hanging.

## 2. Decide the scope

Check for a `.claude/` directory in the current working tree.

- **No `.claude/` → host scope. Nothing left to do.** This plugin's
  `plugins/ktn/.mcp.json` already registers `ktn-linter serve` globally, the
  moment the plugin is enabled and the binary is on `PATH`.
- **`.claude/` present → project scope.** Run this from the project root:

  ```bash
  ktn-linter mcp install
  ```

  This is what host scope does *not* give you: a project-local `mcp.json`
  entry (idempotent — it only adds the entry if missing, existing config is
  left alone) **and** the Pre/PostToolUse HTTP hooks in
  `.claude/settings.json` that enforce KTN rules as files are edited, not
  just when something later runs `ktn-linter lint`. The plugin's global MCP
  registration has no way to install hooks into a specific project's
  `.claude/settings.json`, which is why project scope is a separate step
  instead of something the plugin could do on its own.

  Flags: `--port <n>` (default 7717) to avoid a collision with another
  daemon; `--no-hooks` to wire the MCP server without the edit-time hooks.

## 3. Verify

```bash
ktn-linter version
```

In Claude Code, run `/mcp` and confirm `ktn-linter` shows as connected. If
the binary is installed but `/mcp` doesn't see it, the install landed
somewhere not on the `PATH` this Claude Code process resolves — re-check
step 1's install directory against `echo "$PATH"` rather than reinstalling.
