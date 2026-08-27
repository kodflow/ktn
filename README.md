# Licence state

This branch is the source of truth for ktn-linter entitlement. It carries no
code and is never merged anywhere.

| File | What it is |
|------|------------|
| `roster.json` | the signed statement of who may run the linter |
| `roster.json.sig` | the detached vendor signature over those exact bytes |
| `owners.json` | `uuid -> github login`, the binding that stops a subject being claimed twice |
| `<uuid>.pub` | one enrolled subject's public half |
| `<uuid>.meta.json` | that subject's term, when one was set |

Everything here is public by construction: fingerprints, dates and public
keys. Publishing it exposes nothing — authority comes from the signature,
never from the file's location.

## Why not `main`

`main` carries a required-status ruleset, and a required status check cannot
be satisfied by a direct push: the status does not exist yet at the moment
the push is evaluated. The signing bot's hourly push was therefore refused
outright —

```
remote: - Required status check "post-commit" is expected.
 ! [remote rejected] main -> main (push declined due to repository rule violations)
```

— for a day and a half. The roster's 24-hour window closed behind it and
every licensed binary refused to start. Correct failure direction, wrong
reason.

Licence state is data, not code. It does not want review, it wants to be
written every hour by a machine; putting it on a branch governed by a code
ruleset was the mistake. This branch is under no such rule, so the same
outage cannot recur, and `main`'s history is no longer buried under hourly
`licence: re-sign roster` commits.

Revocation history stays fully auditable — it is this branch's git log.
