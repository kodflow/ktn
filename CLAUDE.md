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
`enrolments.json` (issue → the device it published), `decisions.jsonl` (the
append-only ledger of label decisions and what became of each), and the signed
`roster.json` / `roster.signed.json`.

All of it is PUBLIC, and some of it is contract data: `owners.json` names which
GitHub accounts are customers, `licences.json` gives each one's end date, and
`quotas.json` says who negotiated extra seats. The signed roster has to be
public — every client fetches it unauthenticated — but the login↔account
mapping does not, and hashing a numeric GitHub id would not hide it: those ids
are small enumerable integers. Moving the customer-facing files to a private
store the signer reads with a token is the only fix; nothing here does that
yet. `enrolments.json` and `decisions.jsonl` were deliberately written to carry
no login and no actor, since the issues they reference are already public.

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

**An id is written once and a differing one is a conflict, never an update.**
`record_owner.py` used to overwrite: GitHub does not reissue an id, so a
different value looked like a mistake to correct. The premise is right and the
conclusion was backwards — an id does not change for an account, but a LOGIN
can be released and registered by someone else, and everything except the `ci`
block is keyed on the login. Overwriting handed that login's term, devices,
quota and CI seat to whoever picked up the freed handle. `parse_request.py`
refuses such a request at the gate; deciding whether it is a rename or a
recycled handle is a billing question, not one for an approval run.

What remains open: an account enrolled before `accounts.json` existed has no id
to compare, so the guard cannot bind for it. Re-keying `owners.json`,
`licences.json` and `quotas.json` by id is the real repair and is a live-state
migration.

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

`parse_request.py` now reads the SSH wire format, not just the shape of the
line, so a fixture has to be a real RFC 8709 blob — and a full-length
`ssh-ed25519 AAAA…` literal trips secret scanners on **form** alone, whatever
it decodes to. `fixture_key()` in `test_parse_request.py` and
`test_build_roster.py` assembles the bytes at runtime, which keeps both
properties: valid structure, and no line in this repository shaped like a key.
The short literals that remain are negative fixtures — they exist to be
refused.

## Workflow ordering that other code depends on

`license-roster.yml` publishes the key, then runs `record_owner.py`, then
`record_expiry.py`. The last reads `owners.json` to find the account whose term
to apply, so that order is load-bearing.

**Nothing is published until it was signed AND verified in the same run.** The
signing job builds into a scratch `CANDIDATE_DIR`; the step that runs `openssl
pkeyutl -verify` then writes `attestation.json` naming the run and the exact
bytes it verified, and `promote_roster.py` is the only thing that writes into
the state tree — refusing unless that receipt matches this run, the bundle
carries those same bytes, the artefact is under the ceiling the client enforces
and the window is still open. On any refusal the previously published roster is
left exactly as it was.

That structure replaces an `exit 0`. A run with no signing key used to rebuild
`roster.json`, skip signing, and find the PREVIOUS run's `roster.json.sig` in
the checkout; the steps after it tested only that a `.sig` existed, so the
mismatched pair was bundled and pushed. Nobody gained access — every client
refused everything — but each one reported the roster as forged while the
workflow reported success. A missing key now fails the run.

**The `concurrency` group serialises but still does not guarantee delivery.**
`queue: max` lets a hundred runs wait in FIFO order instead of one, which makes
a dropped decision far less likely and remains a mitigation: the hundred and
first is still discarded. The correction is the reconciliation step, which runs
before every signature and compares the decisions a maintainer took against
what was published — identity is the label event id, order is that event's
timestamp, and the outcome of each is appended to `decisions.jsonl` so nothing
is judged twice.

A dropped REVOCATION is why that exists. A dropped approval has a customer
asking about it; a revocation nobody wrote is invisible, and the schedule keeps
signing valid rosters that authorise the withdrawn device for the rest of its
term. Reconciliation re-applies it, resolving the subject from
`enrolments.json` and never from the issue body, which the requester can still
edit after the label lands. An approval is REPORTED, never replayed: publishing
needs the key from that body.

`roster-watch.yml` is separate on purpose. The freshness alarm inside
`license-roster.yml` is the last step of the job that signs, so it cannot fire
for a job that failed earlier or never ran — the shape of the thirteen-hour
outage. The watch has its own schedule, `contents: read`, and judges what the
ORIGINS serve: authenticity, the age of the served signature, the window with a
margin, the size against what the client refuses, and whether the origins agree.
It also answers the question the signer cannot ask about itself — when did
signing last succeed. Its authenticity check needs the `VENDOR_PUBLIC_KEY`
repository variable (the public half only); without it, the watch reports that
the check could not be made rather than reporting health.
