#!/usr/bin/env python3
"""Re-key live licence state from GitHub LOGIN to numeric account id.

A login is released when an account is renamed or deleted, and can then be
registered by somebody else. Every file keyed on one therefore handed its
contents to whoever picked the handle up: the term, the enrolled devices, the
negotiated quota. The numeric id is never reissued, so that is the key from
here on and the login is a label.

Run it on the checked-out ``licenses`` branch:

    LICENSES_DIR=state python3 .github/scripts/migrate_state_keys.py

It is **idempotent**: everything it writes is derived from the legacy files,
which it never deletes, so a second run produces no diff and a half-finished
run is simply finished by the next one. It **merges** rather than replaces, so
a device enrolled after the first run is not dropped by the second.

It **refuses to guess.** An account enrolled before ``accounts.json`` existed
has no id, and "is the current holder of this login the same person?" has no
answer here — only a billing record has one. Such an account is keyed
``login:<login>``, which is not a valid id anywhere in this chain: it keeps its
devices and its term (so no free year appears), gets no CI seat, and is listed
in ``unresolved-accounts.json`` for a human. The approval gate refuses its next
request rather than attributing it.

Resolving one by hand, once the billing record says which account it is:

    python3 .github/scripts/migrate_state_keys.py --resolve some-login=70000001

``--check`` reports what is still pending and exits non-zero if anything is,
without writing.

``--prune`` deletes the login-keyed files once everything in them has been
migrated, which is the step that actually reduces what the public branch says.
It refuses while anything is unmigrated, and it does not pretend to un-publish
what those files already disclosed — see its docstring.
"""
import importlib.util
import json
import pathlib
import sys

_SPEC = importlib.util.spec_from_file_location(
    "state", pathlib.Path(__file__).with_name("state.py")
)
state = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(state)


def fail(message: str) -> None:
    """Abort loudly. A half-applied migration is worse than an unstarted one."""
    print(f"::error::{message}")
    sys.exit(1)


def resolve(state_dir: pathlib.Path, assignments: list) -> None:
    """Record a numeric id for a login that never had one, by hand.

    This is the ONLY way an unresolved account becomes resolved, and it is
    deliberately a separate, explicit act: the information needed — which
    account the licence was actually sold to — does not exist in this
    repository. Write-once, like the approval chain's own guard: a login that
    already carries a different id is a conflict, not an update.
    """
    path = state.private_for(state_dir) / "accounts.json"
    accounts = state.load(path)
    for assignment in assignments:
        login, _, account_id = assignment.partition("=")
        if not (login and account_id.isdigit()):
            fail(f"--resolve wants login=numeric-id, got {assignment!r}")
        recorded = str(accounts.get(login, {}).get("id", "") or "")
        if recorded and recorded != account_id:
            fail(
                f"@{login} is already on record as account {recorded}, not {account_id}. "
                "GitHub does not reissue an id, so this is a different account; moving the "
                "record is a billing decision and not one this script takes."
            )
        accounts.setdefault(login, {})["id"] = account_id
        print(f"resolved @{login} to account {account_id}")
    state.save(path, accounts)


def promote_bindings(state_dir: pathlib.Path) -> int:
    """Re-point device bindings whose account has since been resolved.

    Keyed by uuid, so the marker being replaced is the VALUE.
    """
    path = state.private_for(state_dir) / "device-owners.json"
    bindings = state.load(path)
    moved = 0
    for uuid, key in sorted(bindings.items()):
        login = state.key_login(key)
        if not login:
            continue
        resolved = state.account_key_for_login(state_dir, login)
        if state.is_resolved(resolved):
            bindings[uuid] = resolved
            moved += 1
    if moved:
        state.save(path, bindings)
    return moved


def promote_account_file(state_dir: pathlib.Path, name: str, merge) -> int:
    """Re-key one account-keyed file whose accounts have since been resolved.

    ``merge`` decides what happens when both the marker and the resolved id
    already carry a value, which is possible when an account was partly
    resolved: two logins of the same id, one recorded before and one after.
    """
    path = state.private_for(state_dir) / name
    entries = state.load(path)
    moved = 0
    for key in sorted(entries):
        login = state.key_login(key)
        if not login:
            continue
        resolved = state.account_key_for_login(state_dir, login)
        if not state.is_resolved(resolved):
            continue
        candidate = entries.pop(key)
        existing = entries.get(resolved)
        entries[resolved] = candidate if existing is None else merge(existing, candidate)
        moved += 1
    if moved:
        state.save(path, entries)
    return moved


def earlier_term(existing, candidate):
    """The earlier of two terms: a device must never outlive its licence."""
    return min((existing, candidate), key=lambda entry: entry.get("expiresAt") or "")


def wider_quota(existing, candidate):
    """The wider of two quotas: seats are negotiated and paid for."""
    usable = [
        value
        for value in (existing, candidate)
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    # A malformed value is carried through rather than compared, so the gate
    # refuses it with a message naming the account instead of max() raising here.
    return max(usable) if len(usable) == 2 else candidate


def promote_resolved(state_dir: pathlib.Path) -> int:
    """Move state written under a ``login:`` marker onto the id it now has.

    A first run keys what it cannot attribute as ``login:<login>``. When a
    maintainer later supplies the id, that state has to FOLLOW — otherwise the
    account is resolved in the directory and still stranded in three files,
    which reads as a brand-new licence and grants it a fresh year on the next
    approval.
    """
    moved = (
        promote_bindings(state_dir)
        + promote_account_file(state_dir, "account-terms.json", earlier_term)
        + promote_account_file(state_dir, "account-quotas.json", wider_quota)
    )
    if moved:
        print(f"promoted {moved} entr(ies) from a login marker to a numeric account id")
    return moved


def migrate_devices(state_dir: pathlib.Path) -> dict:
    """Copy every device → login binding across as device → account key.

    Every binding, including those of REVOKED devices: the file this reads
    keeps them on purpose, so that a uuid cannot be squatted after its key is
    withdrawn, and dropping them here would reopen exactly that.
    """
    private = state.private_for(state_dir)
    legacy = state.load(private / "owners.json")
    path = private / "device-owners.json"
    migrated = state.load(path)
    for uuid, login in sorted(legacy.items()):
        account_key = state.account_key_for_login(state_dir, login)
        recorded = migrated.get(uuid)
        # Only a disagreement between two REAL ids is a conflict. An
        # unresolved marker being replaced by an id is the whole point of
        # --resolve, and refusing it made resolving an account impossible: the
        # first run wrote `login:x`, the resolution recorded the id, and the
        # next run called its own earlier output a re-binding.
        if recorded and recorded != account_key and state.is_resolved(recorded):
            fail(
                f"{uuid} is already migrated to account {recorded}, but owners.json resolves "
                f"it to {account_key}. Something re-bound a device; refusing to overwrite it."
            )
        migrated[uuid] = account_key
    state.save(path, migrated)
    return migrated


def migrate_terms(state_dir: pathlib.Path) -> None:
    """Copy every account → term across, and never let one MOVE.

    When a rename left two logins on the same id and both carry a term, the
    EARLIEST wins. A device must never outlive its licence, and taking the
    latest would let one stale record extend the whole account — the same
    reasoning record_expiry.py applies to sibling device sidecars.
    """
    private = state.private_for(state_dir)
    legacy = state.load(private / "licences.json")
    path = private / "account-terms.json"
    terms = state.load(path)
    for login, entry in sorted(legacy.items()):
        # Membership, not truthiness: a recorded null/0/false/"" is corrupt
        # state, and skipping it as "absent" would send the account down the
        # brand-new-licence path on its next approval and grant a fresh year.
        if "expiresAt" not in entry:
            continue
        account_key = state.account_key_for_login(state_dir, login)
        candidate = entry["expiresAt"]
        #: MEMBERSHIP, not `.get()`. A present `null` and an absent key both
        #: read as None, so a recorded null was treated as "nothing here yet"
        #: and silently overwritten by the legacy candidate — skipping the
        #: conflict path entirely. state.account_term uses a sentinel for
        #: exactly this reason; this call site was the layer that undid it.
        recorded_entry = terms.get(account_key, {})
        recorded_present = "expiresAt" in recorded_entry
        recorded = recorded_entry.get("expiresAt")
        if recorded_present and recorded != candidate:
            #: Parsed, not merely typed. "Both are strings" is not a
            #: comparability test: min() orders them LEXICALLY, and two ISO
            #: strings with different offsets do not sort chronologically —
            #: measured, min("...T00:00:00-05:00", "...T00:00:00Z") returns
            #: the one that is four hours LATER, which EXTENDS the licence.
            recorded_at = state.instant(recorded)
            candidate_at = state.instant(candidate)
            comparable = recorded_at is not None and candidate_at is not None
            print(
                f"::warning::account {account_key} carries two terms across its logins "
                f"({recorded} and {candidate}); keeping "
                + (
                    "the earlier one, so no device outlives the licence that authorised it."
                    if comparable
                    else "the one already recorded: one of the two is not a date string, "
                    "so they cannot be ordered and guessing which is earlier could EXTEND "
                    "a licence rather than shorten it."
                )
            )
            # min() across a str and anything else raises TypeError, which
            # aborted the whole migration over one malformed entry — and a
            # migration that cannot finish leaves the state half re-keyed,
            # which is the condition it exists to remove.
            #: The earlier INSTANT, so no device outlives the licence that
            #: authorised it — and the recorded value kept untouched when the
            #: two cannot be ordered, because guessing could only move a term
            #: outwards.
            if comparable:
                candidate = recorded if recorded_at <= candidate_at else candidate
            else:
                candidate = recorded
        terms.setdefault(account_key, {})["expiresAt"] = candidate
    state.save(path, terms)


def migrate_quotas(state_dir: pathlib.Path) -> None:
    """Copy every account → seat override across.

    The WIDEST wins across a renamed account's logins: seats are negotiated and
    paid for, and a rename must not quietly take back what was sold. A
    malformed value is carried over untouched rather than repaired — the limit
    is applied at the gate, and that is where it must be refused, with a
    message naming the account.
    """
    private = state.private_for(state_dir)
    legacy = state.load(private / "quotas.json")
    path = private / "account-quotas.json"
    quotas = state.load(path)
    for login, value in sorted(legacy.items()):
        account_key = state.account_key_for_login(state_dir, login)
        if account_key in quotas and quotas[account_key] != value:
            usable = [
                candidate
                for candidate in (quotas[account_key], value)
                if isinstance(candidate, int) and not isinstance(candidate, bool)
            ]
            print(
                f"::warning::account {account_key} carries two quotas across its logins "
                f"({quotas[account_key]!r} and {value!r}); keeping the wider one."
            )
            quotas[account_key] = max(usable) if len(usable) == 2 else value
            continue
        quotas[account_key] = value
    state.save(path, quotas)


def report_unresolved(state_dir: pathlib.Path, bindings: dict) -> list:
    """List every account that could not be keyed, and say what it still holds.

    Written to a file rather than only logged, because the thing a maintainer
    has to do about it does not fit in a run that has already finished: they
    need the billing record, and then one ``--resolve``. The file is the
    worklist, and it is rewritten each run so a resolved account leaves it.
    """
    unresolved = sorted({key for key in bindings.values() if not state.is_resolved(key)})
    private = state.private_for(state_dir)
    terms = state.load(private / "account-terms.json")
    quotas = state.load(private / "account-quotas.json")
    path = private / "unresolved-accounts.json"

    if not unresolved:
        # Leaving a stale worklist behind would read as "still broken" forever.
        if path.exists():
            path.unlink()
            print("every account is keyed by id; removed the unresolved worklist")
        return []

    worklist = {}
    for account_key in unresolved:
        login = state.key_login(account_key)
        devices = sorted(uuid for uuid, key in bindings.items() if key == account_key)
        worklist[account_key] = {
            "login": login,
            "devices": devices,
            "active": [uuid for uuid in devices if (state_dir / f"{uuid}.pub").is_file()],
            "hasTerm": account_key in terms,
            "hasQuota": account_key in quotas,
            "note": (
                "Enrolled before accounts.json existed, so no numeric id was ever "
                "captured and this state cannot be attributed to an account. Its devices "
                "and its term are intact and it gets no CI seat. The approval gate refuses "
                "its next request. Resolve with --resolve "
                f"{login}=<numeric-id> once the billing record says which account it is."
            ),
        }
        print(
            f"::error::@{login} holds {len(devices)} device binding(s) and no numeric account "
            f"id. Keyed as {account_key!r} and NOT attributed to whoever holds @{login} today: "
            "a released login can be registered by someone else, and guessing here would hand "
            "them this licence. Resolve it by hand."
        )
    state.save(path, worklist)
    return unresolved


def prune(state_dir: pathlib.Path) -> int:
    """Delete the login-keyed files, once everything in them has been migrated.

    This is the one step that actually REDUCES what the public branch says. The
    id-keyed replacements carry no login at all — ``device-owners.json`` maps a
    uuid to a number — so removing these three takes every
    "which named account owns which device" and "which named account expires
    when" link off a public branch, leaving ``accounts.json`` as the only
    login-bearing file.

    It refuses unless every entry has a migrated counterpart, because these
    files are the ONLY copy of state the migration has not converted yet, and
    a term that is deleted rather than migrated reads afterwards as an account
    with no term — the one path that starts a clock, and a free year.

    It does NOT delete ``accounts.json``: the write-once guard looks an account
    up BY LOGIN, so that mapping is still needed. It cannot be anonymised
    either — see audit_public_state.py.

    **Deleting a file does not un-publish it.** Every commit that carried it is
    still in the history of a public branch, and every clone and every cached
    fetch already taken keeps it. This reduces future exposure only.
    """
    private = state.private_for(state_dir)
    bindings = state.load(private / "device-owners.json")
    terms = state.load(private / "account-terms.json")
    quotas = state.load(private / "account-quotas.json")

    blocked = []
    for uuid in state.load(private / "owners.json"):
        if uuid not in bindings:
            blocked.append(f"{uuid} has no migrated binding")
    for login, entry in state.load(private / "licences.json").items():
        if "expiresAt" not in entry:
            continue
        if state.account_key_for_login(state_dir, login) not in terms:
            blocked.append(f"the term recorded for @{login} has no migrated counterpart")
    for login in state.load(private / "quotas.json"):
        if state.account_key_for_login(state_dir, login) not in quotas:
            blocked.append(f"the quota recorded for @{login} has no migrated counterpart")

    if blocked:
        for problem in blocked:
            print(f"::error::refusing to prune: {problem}")
        print("::error::run the migration without --prune first; these files are the only copy")
        return 1

    removed = []
    for name in ("owners.json", "licences.json", "quotas.json"):
        path = private / name
        if path.exists():
            path.unlink()
            removed.append(name)
    if not removed:
        print("nothing to prune; the login-keyed files are already gone")
        return 0
    print(f"pruned {', '.join(removed)}: no login↔device or login↔term link is written here now")
    print(
        "::warning::this reduces FUTURE exposure only. Every commit that carried these files "
        "is still in the history of a public branch, and every clone and cached fetch already "
        "taken still has them. Treat the data in them as disclosed."
    )
    return 0


def check(state_dir: pathlib.Path) -> int:
    """Report what is pending without writing anything."""
    pending = state.migration_pending(state_dir)
    unresolved = sorted(
        {
            key
            for key in state.device_owners(state_dir).values()
            if not state.is_resolved(key)
        }
    )
    if pending:
        print("::warning::some devices are still bound only through owners.json")
    for account_key in unresolved:
        print(f"::warning::{account_key} has no numeric id and needs resolving by hand")
    if not (pending or unresolved):
        print("state is fully keyed by numeric account id")
        return 0
    return 1


def main() -> None:
    arguments = sys.argv[1:]
    state_dir = state.licenses_dir()
    if "--check" in arguments:
        sys.exit(check(state_dir))
    if "--prune" in arguments:
        sys.exit(prune(state_dir))

    assignments = [
        value
        for index, value in enumerate(arguments)
        if index and arguments[index - 1] == "--resolve"
    ]
    if assignments:
        resolve(state_dir, assignments)
    # Before the conversion, not after: state already written under a login
    # marker has to follow the id it just acquired, or the account is resolved
    # in the directory and stranded everywhere else.
    promote_resolved(state_dir)

    # Order matters: the devices are re-keyed first because their resolution is
    # what tells report_unresolved which accounts hold live state, and the
    # terms and quotas are converted before that report so it can say whether
    # an unresolved account still carries one.
    bindings = migrate_devices(state_dir)
    migrate_terms(state_dir)
    migrate_quotas(state_dir)
    unresolved = report_unresolved(state_dir, bindings)

    resolved = sum(1 for key in bindings.values() if state.is_resolved(key))
    print(
        f"migrated {len(bindings)} device binding(s): {resolved} keyed by account id, "
        f"{len(bindings) - resolved} awaiting a maintainer across "
        f"{len(unresolved)} account(s)"
    )
    print(
        "legacy owners.json / licences.json / quotas.json are LEFT IN PLACE and still read "
        "as a fallback, so an un-migrated branch cannot stop the roster being signed."
    )


if __name__ == "__main__":
    main()
