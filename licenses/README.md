# Licence state has moved

The roster, the enrolled public keys and the owner bindings now live on the
[`licenses`](https://github.com/kodflow/ktn/tree/licenses) branch. Nothing
here is read by anything any more.

## Why

`main` carries a required-status ruleset, and a required status check cannot
be satisfied by a direct push: the status does not exist yet at the moment
the push is evaluated. The signing bot's hourly push was refused outright —

```
remote: - Required status check "post-commit" is expected.
 ! [remote rejected] main -> main (push declined due to repository rule violations)
```

— for a day and a half. The roster's own 24-hour window closed behind it and
every licensed binary refused to start. The failure direction was right; the
cause was a code rule applied to data.

Licence state is written by a machine every hour and wants no review. It now
lives on a branch under no such rule, which also keeps `main`'s history clear
of hourly `licence: re-sign roster` commits. Revocation history is fully
auditable as that branch's git log.

## Where the binary looks

`ktn-linter` reads the roster from several origins in turn and takes the
first one that both verifies against the compiled-in vendor key and is still
inside its window. The `licenses` branch is the first of them. Authority
comes from the signature, never from the location — which is exactly why the
location was free to change.
