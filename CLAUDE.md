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

Files on that branch: `<uuid>.pub` (published devices), `device-owners.json`
(device → numeric account id), `accounts.json` (login → numeric id),
`account-terms.json` (account → term), `<uuid>.meta.json` (per-device copy of
the term), `account-quotas.json` (optional per-account seat override),
`ci-owners.json` (requester → CI beneficiary), `required-version.txt`,
`enrolments.json` (issue → the device it published), `decisions.jsonl` (the
append-only ledger of label decisions and what became of each), and the signed
`roster.json` / `roster.signed.json`.

The three login-keyed predecessors — `owners.json`, `licences.json`,
`quotas.json` — are still READ, and are the subject of "Keyed by id" below.

## What is public, and what should not be

All of that branch is PUBLIC, and some of it is contract data. The signed
roster has to be public — every client fetches it unauthenticated, and a scheme
whose roster needed a credential would need a credential to check a credential.
Which accounts are customers, when each term ends and who negotiated extra
seats do not. `enrolments.json` and `decisions.jsonl` were deliberately written
to carry no login and no actor, since the issues they reference are already
public.

`state.COMMERCIAL_FILES` is the boundary, enumerated in one place rather than
remembered, and `audit_public_state.py` reports it on every signature. Each
entry carries the reason it matters, so the report names the stake rather than
a filename.

**Three steps, two of them delivered here.**

1. **Re-key and prune.** `migrate_state_keys.py --prune` deletes
   `owners.json`, `licences.json` and `quotas.json` once their contents are
   provably migrated. Their id-keyed replacements carry no login at all —
   `device-owners.json` maps a uuid to a number — so this removes every
   "which named account owns which device" and "which named account expires
   when" link from a public branch. It refuses while anything is unmigrated,
   because those files are then the only copy, and a term deleted rather than
   migrated reads afterwards as an account with no term: a free year.
2. **The seam.** `PRIVATE_STATE_DIR` moves every commercial file somewhere the
   public branch is not. `state.private_for()` is the only place that knows,
   and unset it resolves to the public directory — so nothing changes until a
   maintainer configures it. With it set, `audit_public_state.py` FAILS while
   any commercial file is still public: a half-finished cutover otherwise
   reads as a finished one from the configuration alone. Pointing it at the
   public directory counts as unset, since that configures nothing.
3. **The private store itself — NOT decided here.** The signer runs on a
   GitHub runner and would read the private half with a token. Choosing where
   (another repository, an object store, an environment secret) and
   provisioning it is an infrastructure decision, and no script here invents
   one. What the seam buys is that the choice is two lines of workflow
   configuration and no code change.

**Anonymising the account id does not work.** A GitHub numeric id is a small
integer, six to nine digits, so any hash of one falls to hashing the whole
range. A salt does not help while the salt ships in the client — the client is
what checks the roster, so whatever it needs is public by construction. A
random 128-bit handle minted per account WOULD be opaque, and it works for
anything the client does not have to match locally.

It does not work for the `ci` block. A CI run proves itself with an OIDC token
carrying `repository_owner_id`, a number, and the client compares that number
against the roster; there is no opaque value it could compare without being
given the mapping, which would publish the mapping. So **the `ci` block
irreducibly discloses the numeric ids of CI-entitled accounts** for as long as
the check is local against a public document. Moving that check behind an
authenticated endpoint is a product decision, not a naming problem. The full
argument is in `audit_public_state.py`'s module docstring, next to the code it
constrains.

**And none of this un-publishes anything.** Every commit that carried one of
these files is still in the history of a public branch, and every clone and
every cached fetch already taken still has it. Pruning reduces FUTURE exposure
only. What has been on that branch should be treated as disclosed and handled
as such — notification, and rotation of anything rotatable — not as something
a delete commit fixes.

## The model

**One licence per account — the billing unit. Three devices per licence. CI on
top, spending no seat.**

Devices are flat subjects in the signed roster, which is why the multi-device
change needed no client update and no migration: to a client, a device is just
a subject like any other.

## Rules that exist because their absence was a way to get paid service free

**Seats are counted from published `.pub` files, never from the bindings.**
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
  device and enrol another to restart the clock — the recorded term survives
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
GitHub does not reissue an id. `record_owner.py` captures the requester's id at
approval time from `github.event.issue.user.id`.

**The requester is not necessarily the beneficiary, and the two are now
separate fields.** A CI run presents `repository_owner_id` — the owner of the
repository it runs in — while an issue is opened by a PERSON. For a customer
whose repositories belong to an organisation those are two different numbers,
so publishing the requester's id produced an entitlement no run would ever
carry: the licence looked issued, the CI job failed its licence check, and
nothing named the cause. A silent no is worse than a refusal, because nobody
knows to ask about it.

`ci-owners.json` holds the answer, keyed by the requester's numeric id — so it
needs no migration when the rest of the state is re-keyed, and carries no
recycled-handle exposure of its own. Three states, and the middle one is the
point:

- **no entry** — the beneficiary IS the requester. The default, and right for a
  personal account;
- **`claimed` with no `beneficiary`** — UNRESOLVED. The request named someone
  else and nothing here can verify the requester speaks for them, so
  `build_roster.py` omits the CI entry and says so on every build. The devices
  are unaffected;
- **`beneficiary`** — a maintainer answered, with a `ciOwner:<numeric-id>` or
  `ciOwner:self` label on an approval. That id is the published key.

The policy is deliberately NOT chosen here. Whether an organisation's CI seat
belongs to the organisation or to the member who bought the licence is a billing
question; both answers are expressible and neither is inferred. The request form
may NAME a CI owner, and that name is a claim, never a grant — `ci_owner:
some-big-org` from an issue body would otherwise cover every repository that org
owns. A claim never moves a beneficiary a maintainer already decided.

Two licences may legitimately name one beneficiary (two members of one org). The
owner is covered while either is live, so the later term is published and the
collision is reported — it is also what a licence sold twice looks like. An
ABSENT term sorts last in that comparison: the client reads a missing `exp` as
no expiry, so it is the widest entry, not an empty string that loses to a date.

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

## Keyed by id — the state, not just the CI block

`device-owners.json`, `account-terms.json` and `account-quotas.json` replace
`owners.json`, `licences.json` and `quotas.json`, keyed by **numeric account
id**. The login survives as a label, and `accounts.json` is the only file where
it is still a key — it IS the login→id directory, and a directory has to be
keyed on the thing being looked up.

`state.py` is the single place that knows this. Every lookup reads the id-keyed
file and falls back to the login-keyed one through `accounts.json`, across
**every** login an id has carried: a rename records the new login against the
same id and the old entry is kept, so a legacy term or quota is still reachable
afterwards. Without that traversal a rename looked like an account with no term
on record — the one path that starts a clock — and handed out a fresh year.
Renaming once a year was an indefinite subscription, and renaming between
requests was unlimited devices.

**The legacy read path is load-bearing, not vestigial.** An un-migrated branch
must still produce a signable roster: a roster that is not re-signed blocks
every client within 24 hours, which is far worse than a stale key scheme.

`migrate_state_keys.py` converts live state. It never deletes the legacy files,
merges rather than replaces, and is idempotent — a second run produces no diff,
and a device approved between two runs is not dropped by the later one. The
signing job calls it with `--check` every 20 minutes, reporting and never
blocking (`|| true` is deliberate).

**Absence is a sentinel, not `None`.** `state.ABSENT` exists because a recorded
`null` is exactly the corruption this chain refuses, and if absence were also
`None` the two would be one answer: the account reads as brand new and gets a
fresh year. The migration copies a corrupt term across VERBATIM for the same
reason — dropping it is the tidier-looking option and the dangerous one, since
once the legacy file is gone the account would read as having no term at all.

**An account with no recorded id is marked, never attributed.** Its key is the
explicit marker `login:<login>`, which `isdigit()` rejects everywhere, so no
code path expecting an id can accept one. It keeps its devices and its term, it
gets no CI seat, and it is listed in `unresolved-accounts.json`.

Its next approval is **refused**. This reverses what this file used to say, and
the old behaviour was the hole: with no id on record the write-once guard has
nothing to compare and passes, so whoever registers the released login arrives
looking brand new — no devices against the quota, no term on record — and
collects the previous customer's seats and their remaining year with no error
anywhere. An ordinary rename has the identical shape here; the fact that
separates them is in a billing record. So the run stops and prints the command
that resolves it:

```
migrate_state_keys.py --resolve <login>=<numeric-id>
```

That records the id and promotes every entry already written under the marker.
Resolution is write-once: a login already carrying a different id is a
conflict, because GitHub does not reissue one.

A refusal costs one approval and names its cause. Its published devices keep
being signed into every roster meanwhile — this refuses an approval, not a
licence.

**Re-keying changed nothing a client sees.** The roster built from migrated
state is byte-identical to the one built from the same state before migration:
subjects are keyed by uuid and the `ci` block was already keyed by id. No client
migration, no format version bump.

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
`record_expiry.py`. The last reads the binding the first wrote to find the
account whose term to apply, so that order is load-bearing.

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
