#!/usr/bin/env python3
"""Where licence state lives, and how it is KEYED.

One place, because the answer used to be "the GitHub login" in four files and
that was the exposure. A login is released when an account is renamed or
deleted and can then be registered by somebody else
(https://docs.github.com/en/account-and-profile/concepts/username-changes),
so every file keyed on one handed its contents — term, devices, quota, CI
seat — to whoever picked the handle up. The numeric id is never reissued, so
that is the key; the login stays as a LABEL, for messages a human reads.

Three id-keyed files replace three login-keyed ones:

===========================  ===========================================
``device-owners.json``       ``{uuid: account key}`` — replaces ``owners.json``
``account-terms.json``       ``{account key: {expiresAt}}`` — replaces ``licences.json``
``account-quotas.json``      ``{account key: int}`` — replaces ``quotas.json``
===========================  ===========================================

``accounts.json`` stays keyed by login: it IS the login→id directory, and a
directory has to be keyed on the thing being looked up. It is the only file
where a login is a key rather than a label.

**The legacy files are still READ.** A state branch that has not been migrated
must not stop the roster being signed — an unsigned roster blocks every client
within 24 hours, which is a far worse outcome than a stale key scheme. Every
lookup here tries the id-keyed file first and falls back to the login-keyed one
through ``accounts.json``. ``migrate_state_keys.py`` performs the conversion
once, and the fallback can be deleted when no state branch predates it.

**An account with no recorded id is not guessed at.** Its key is the explicit
marker ``login:<login>`` — never confusable with a numeric id, never granted a
CI seat, and refused at the approval gate rather than attributed to whoever
holds the login today. That refusal is the point: an account enrolled before
``accounts.json`` existed has no id to compare, so "is this the same person?"
has no answer here, and the wrong answer gives a stranger a paid licence.
"""
import json
import os
import pathlib

# The marker prefix for an account whose numeric id was never captured. It is
# deliberately not a valid id: `str.isdigit()` is false for it everywhere, so a
# code path that expects an id cannot silently accept one of these.
UNRESOLVED_PREFIX = "login:"

# "Nothing is recorded", as a value that cannot be confused with a recorded
# one. `None` cannot serve here: a recorded `null` is exactly the corruption
# this chain has to refuse, and if absence were also None the two would be the
# same answer — the account would read as brand new and be handed a fresh year,
# which is the free-renewal outcome the term mechanism exists to prevent.
ABSENT = object()


def licenses_dir() -> pathlib.Path:
    """Where PUBLIC licence state lives: the published keys and the roster.

    Defaults to ``licenses/`` so the scripts stay runnable from a plain
    checkout of ``main``. The workflow overrides it with ``LICENSES_DIR``
    because the state lives on its own branch, checked out into a separate
    directory: ``main`` carries a required-status ruleset that refuses a direct
    push, which silently stopped every re-signature for a day and a half until
    the roster's window closed.
    """
    return pathlib.Path(os.environ.get("LICENSES_DIR", "licenses"))


# The commercial half of the state: which named accounts are customers, what
# each one's term is, how many seats each negotiated. None of it is needed by a
# client, and all of it is contract data.
#
# Kept as one list so the boundary is enumerable rather than remembered.
# ``<uuid>.pub``, ``<uuid>.meta.json`` and the roster trio are NOT here: the
# roster has to be public — every client fetches it unauthenticated — and a
# uuid with a fingerprint and a date names nobody once the bindings are gone.
COMMERCIAL_FILES = (
    "accounts.json",
    "account-quotas.json",
    "account-terms.json",
    "ci-owners.json",
    "device-owners.json",
    "licences.json",
    "owners.json",
    "quotas.json",
    "unresolved-accounts.json",
)


def private_for(state_dir: pathlib.Path) -> pathlib.Path:
    """Where the COMMERCIAL half of the state lives, given the public half.

    ``PRIVATE_STATE_DIR`` moves every file in ``COMMERCIAL_FILES`` somewhere
    the public `licenses` branch is not. Unset, it resolves to the public
    directory — which is where all of it lives today, so nothing changes until
    a maintainer points this at a private store.

    **That store is an infrastructure decision and is deliberately not made
    here.** The signer runs on a GitHub runner and would have to read the
    private half with a token; choosing where (another repository, an object
    store, an environment secret) and provisioning it is not something a script
    should invent. What this seam buys is that the choice becomes two lines of
    workflow configuration and no code change, and that the boundary is
    enumerable instead of being rediscovered each time.

    A subdirectory of the public branch is NOT a fix and must not be mistaken
    for one: a different path on a public branch is still public.
    """
    configured = os.environ.get("PRIVATE_STATE_DIR")
    return pathlib.Path(configured) if configured else state_dir


def load(path: pathlib.Path, default=None):
    """Read a JSON state file, treating absent and empty as the default.

    Absent is the normal state for most of these: a fresh state branch has
    none of them, and the schedule has to succeed against that rather than
    crash on the first signature of a new deployment.
    """
    if default is None:
        default = {}
    if not path.exists():
        return default
    text = path.read_text()
    if not text.strip():
        return default
    return json.loads(text)


def save(path: pathlib.Path, payload) -> None:
    """Write a JSON state file the way every other file on this branch is written.

    An empty payload writes NOTHING when the file does not already exist. This
    branch is public and its file list is itself a disclosure — an empty
    `account-quotas.json` says nothing about quotas and everything about the
    fact that quotas are tracked, and audit_public_state.py would report it as
    exposure with no content behind it. An existing file is still truncated to
    `{}`, because that is how something gets emptied deliberately.
    """
    if not payload and not path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def unresolved_key(login: str) -> str:
    """The explicit key for an account whose numeric id was never captured."""
    return f"{UNRESOLVED_PREFIX}{login}"


def is_resolved(account_key: str) -> bool:
    """Whether this key is a real account id rather than an unresolved login."""
    return bool(account_key) and account_key.isdigit()


def key_login(account_key: str) -> str:
    """The login carried by an unresolved key, or "" for a resolved one."""
    if account_key.startswith(UNRESOLVED_PREFIX):
        return account_key[len(UNRESOLVED_PREFIX):]
    return ""


def accounts(state_dir: pathlib.Path) -> dict:
    """The login → ``{"id": ...}`` directory, as recorded at approval time."""
    return load(private_for(state_dir) / "accounts.json")


def account_key_for_login(state_dir: pathlib.Path, login: str) -> str:
    """This login's account key: its numeric id, or an unresolved marker.

    The marker is returned rather than "" so a caller cannot mistake "no id on
    record" for "no such account" — those need different handling, and
    conflating them is how a legacy licence would get attributed to the current
    holder of a freed login.
    """
    recorded = str(accounts(state_dir).get(login, {}).get("id", "") or "")
    return recorded if recorded.isdigit() else unresolved_key(login)


def logins_for_key(state_dir: pathlib.Path, account_key: str) -> list:
    """Every login ``accounts.json`` records against this account key.

    More than one is normal and is the reason this function exists: a rename
    records the NEW login against the SAME id, and the old entry is not
    removed — deliberately, since deleting it would make a released handle
    indistinguishable from one that was never used. Legacy lookups therefore
    have to try all of them, or a renamed account loses the term and the quota
    it already had and starts a fresh year.
    """
    login = key_login(account_key)
    if login:
        return [login]
    return sorted(
        name
        for name, record in accounts(state_dir).items()
        if str(record.get("id", "") or "") == account_key
    )


def device_owners(state_dir: pathlib.Path) -> dict:
    """Every device → account key binding, id-keyed file merged over the legacy one.

    Bindings SURVIVE revocation, here as in the file this replaces: the uuid
    stays bound to its original account so the identity cannot be squatted
    after its key is withdrawn. Nothing in this view distinguishes an active
    device from a revoked one; a published ``<uuid>.pub`` does, and every seat
    count uses that.
    """
    private = private_for(state_dir)
    merged = {}
    # Legacy first, so the id-keyed file wins on any uuid present in both.
    for uuid, login in load(private / "owners.json").items():
        merged[uuid] = account_key_for_login(state_dir, login)
    merged.update(load(private / "device-owners.json"))
    return merged


def device_owner(state_dir: pathlib.Path, uuid: str) -> str:
    """This device's account key, or "" when nothing ever published it."""
    return device_owners(state_dir).get(uuid, "")


def bind_device(state_dir: pathlib.Path, uuid: str, account_key: str) -> None:
    """Record which account a device belongs to."""
    path = private_for(state_dir) / "device-owners.json"
    owners = load(path)
    owners[uuid] = account_key
    save(path, owners)


def devices_of(state_dir: pathlib.Path, account_key: str) -> list:
    """Every device bound to this account, published or revoked."""
    return sorted(uuid for uuid, key in device_owners(state_dir).items() if key == account_key)


def active_devices(state_dir: pathlib.Path, account_key: str) -> list:
    """Every device of this account whose key is currently published.

    A published ``.pub`` is what makes a device active, and it is what every
    seat count must use. Counting bindings instead would retire a seat
    permanently on each revocation — three revocations and the licence is dead,
    with no message able to explain why.
    """
    return [
        uuid
        for uuid in devices_of(state_dir, account_key)
        if (state_dir / f"{uuid}.pub").is_file()
    ]


def account_term(state_dir: pathlib.Path, account_key: str):
    """This account's recorded term VERBATIM, or ``ABSENT`` when none is recorded.

    Membership, never truthiness — and that is why the absent answer is a
    sentinel rather than ``None``. A recorded ``null``, ``0``, ``false`` or
    ``""`` is a CORRUPT entry, and reading one as absent would send the account
    down the brand-new-licence path and hand it a fresh year: the free-renewal
    outcome the whole term mechanism exists to prevent, reachable by nothing
    more than a bad edit. Returning ``None`` for absence would make a recorded
    ``null`` indistinguishable from an unrecorded term and reintroduce it here,
    one layer below where it was fixed.
    """
    private = private_for(state_dir)
    entry = load(private / "account-terms.json").get(account_key, {})
    if "expiresAt" in entry:
        return entry["expiresAt"]
    # Legacy: the term was recorded against a login. Every login this id has
    # ever carried is tried, so a rename does not lose the term — which would
    # otherwise look like a brand-new licence and start a fresh year.
    legacy = load(private / "licences.json")
    for login in logins_for_key(state_dir, account_key):
        if "expiresAt" in legacy.get(login, {}):
            return legacy[login]["expiresAt"]
    return ABSENT


def set_account_term(state_dir: pathlib.Path, account_key: str, iso: str) -> None:
    """Record this account's term. This file outlives every device it has."""
    path = private_for(state_dir) / "account-terms.json"
    terms = load(path)
    terms.setdefault(account_key, {})["expiresAt"] = iso
    save(path, terms)


def account_quota(state_dir: pathlib.Path, account_key: str, default: int):
    """This account's seat override, or the default when it has none.

    Returned unvalidated — the caller refuses a malformed one, because the
    refusal message belongs where the limit is applied. ``bool`` is not
    filtered here for the same reason, and it must be: it is a subclass of
    ``int``, so a quota of ``true`` passes ``isinstance(x, int)`` and then
    behaves as 1, cutting an account to a single device.
    """
    private = private_for(state_dir)
    quotas = load(private / "account-quotas.json")
    if account_key in quotas:
        return quotas[account_key]
    # Legacy, across every login this id has carried. The widest wins: seats
    # are negotiated, and a rename must not quietly take back what was sold.
    legacy = load(private / "quotas.json")
    candidates = [
        legacy[login]
        for login in logins_for_key(state_dir, account_key)
        if login in legacy
    ]
    if not candidates:
        return default
    # A malformed entry is passed through as-is for the caller to refuse; max()
    # over mixed types would raise here instead, losing the message.
    if any(isinstance(value, bool) or not isinstance(value, int) for value in candidates):
        return candidates[0]
    return max(candidates)


def unresolved_holdings(state_dir: pathlib.Path, login: str) -> dict:
    """What state is held under this login with no numeric id behind it.

    The gate calls this before accepting a request, and a non-empty answer is a
    REFUSAL rather than a fallback. The shape of the hole it closes: a login is
    released, somebody else registers it, and their request carries a real
    numeric id — so the write-once id guard has nothing to compare and passes.
    Keyed by id, their account then looks brand new: zero devices used against
    the quota, and no term on record, which is the one path that starts a
    clock. The previous customer's seats and their remaining year, handed over
    silently.

    Nothing here can tell that apart from an ordinary rename. Only a billing
    record can, so the answer is to stop and say so.
    """
    # A login that HAS an id on record is not unresolved, whatever else it
    # holds: its legacy term, devices and quota all belong to that id, and the
    # approval gate's write-once comparison already covers it. Without this
    # early return the guard fired for every ordinary account with a legacy
    # quota entry — a refusal with nothing behind it.
    if is_resolved(account_key_for_login(state_dir, login)):
        return {}
    account_key = unresolved_key(login)
    holdings = {}
    devices = devices_of(state_dir, account_key)
    if devices:
        holdings["devices"] = devices
    if account_term(state_dir, account_key) is not ABSENT:
        holdings["term"] = True
    private = private_for(state_dir)
    if account_key in load(private / "account-quotas.json") or login in load(
        private / "quotas.json"
    ):
        holdings["quota"] = True
    return holdings


def migration_pending(state_dir: pathlib.Path) -> bool:
    """Whether any device is still bound only through the legacy file.

    Used to report, never to block: the roster has to be signable against
    un-migrated state.
    """
    private = private_for(state_dir)
    legacy = load(private / "owners.json")
    if not legacy:
        return False
    migrated = load(private / "device-owners.json")
    return any(uuid not in migrated for uuid in legacy)
