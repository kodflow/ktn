# kodflow/ktn

Public distribution channel for ktn-linter, and the **licence authority**: the
scripts under `.github/scripts` decide who is published, for how long, and what
the signed roster says. `README.md` is the customer-facing document; this file
is what a maintainer needs before changing any of it.

## Where the state lives

Licence state is on the **`licenses` branch**, not `main`. `main` carries a
required-status ruleset, and a required check cannot be satisfied by a direct
push — the signing bot's push was refused outright for a day and a half, the
roster's 24-hour window closed behind it, and every licensed binary stopped.
Data written by a machine every twenty minutes does not belong under a rule
written for code.

Files on that branch: `<uuid>.pub` (published devices), `owners.json`
(device → account), `accounts.json` (account → numeric id), `licences.json`
(account → term), `<uuid>.meta.json` (per-device copy of the term),
`quotas.json` (optional per-account seat override), `required-version.txt`,
and the signed `roster.json` / `roster.signed.json`.

## The model

**One licence per account — the billing unit. Three devices per licence. CI on
top, spending no seat.**

Devices are flat subjects in the signed roster, which is why the multi-device
change needed no client update and no migration: to a client, a device is just
a subject like any other.

## Rules that exist because their absence was a way to get paid service free

**Seats are counted from published `.pub` files, never from `owners.json`.**
Revocation deliberately leaves the uuid bound to its original owner so the
identity cannot be squatted afterwards. Counting bindings would therefore
retire a seat permanently on every revocation — three revocations and the
licence is dead, with no message able to explain why.

**A term is set once, and only a maintainer moves it.** Four separate paths
each handed out a fresh year before this was closed:

- a rotation re-ran the term logic, so `license update` once a year was an
  indefinite subscription;
- a second device started its own year, so one licence expired on as many dates
  as it had machines, and a late device outlived the licence authorising it;
- deriving the term from surviving devices let an account revoke its last
  device and enrol another to restart the clock — `licences.json` survives
  revocation precisely so that cannot happen;
- a recorded `expiresAt` that was `null`, `0`, `false` or `""` read as *absent*
  rather than as *corrupt*, sending the account down the brand-new-licence
  path. Test membership (`"expiresAt" in entry`), never truthiness.

An `expireAt:` label renews the **licence**: every active device is restamped,
so the account keeps exactly one date.

**A recorded term is parsed before it is compared or copied.** An unparseable
one used to be publishable, and the client reads an unparseable expiry as *no*
expiry — an unlimited licence.

**`isinstance(x, int)` accepts `True` in Python.** `bool` is a subclass of
`int`, so a quota of `true` passed validation and behaved as 1, silently
cutting an account to a single device. Exclude `bool` explicitly.

## CI entitlement

The roster's `ci` block maps a **numeric account id** to a term. Keyed by id and
never by login: a login can be renamed, and a released one can be claimed by
somebody else, so matching on the name would turn a freed handle into a way in.
GitHub does not reissue an id. `record_owner.py` captures it at approval time
from `github.event.issue.user.id`.

An account appears only while it has an active device, so revoking the last one
removes CI with it — there is no separate revocation path to forget. The term is
the licence's own, so CI expires exactly when the devices do.

Licences approved before `accounts.json` existed carry no id. They keep working
as devices and get no CI seat until their next approval records one; inventing
an id, or falling back to the login, would defeat the reason the id is used.

`setup/action.yml` therefore needs **no key material**. `license-key` is kept,
deprecated and warning, because nothing outside GitHub Actions can mint a token
the linter knows how to check.

## Commit identity — non-negotiable

Every commit here must carry a `users.noreply.github.com` address. The
`post-commit` gate scans **full history**, so one bad commit anywhere blocks
until history is rewritten — not until the next commit is clean.

That is not hypothetical: `cb4fee4`, authored as a personal address on
2026-08-27, made this gate fail for nine days and cost a rewrite of all 65
commits on the `licenses` branch — a branch the signing bot writes to three
times an hour.

Do **not** reach for the escape hatches (`history: range`, or widening
`authors`). Both weaken the gate, and the gate's own documentation states that
`full` means "one tainted commit anywhere blocks until history is rewritten".

## Testing the scripts

`python3 -m unittest discover -s .github/scripts -p "test_*.py"` — stdlib only,
no requirements file, so the lane cannot rot behind a dependency nobody updates.
Scripts are loaded by path with `importlib` so the real code is exercised rather
than a copy that can drift from it.

**A test must be seen failing without its fix.** Two tests here compared two
dates computed within the same second and passed whether or not the rule they
claimed to pin existed. Plant a value the code could not have produced.

Fixture keys are deliberately far too short to be real: a full-length
`ssh-ed25519` line trips secret scanners on **form** alone, whatever it decodes
to, and `parse_request.py` only checks the shape of the line.

## Workflow ordering that other code depends on

`license-roster.yml` publishes the key, then runs `record_owner.py`, then
`record_expiry.py`. The last reads `owners.json` to find the account whose term
to apply, so that order is load-bearing.

The `concurrency` group serialises approvals but does **not** queue them: GitHub
keeps one pending run per group, so a burst can drop an approval. The schedule
recovers, since the next signing run rebuilds the roster from whatever is on the
branch; an approval that never landed needs its label re-applied.
