#!/usr/bin/env python3
"""Move the COMMERCIAL half of the licence state off the public branch.

`PRIVATE_STATE_DIR` redirects every NEW write to a private store. It does not
move what is already there, and that gap was a trap with the whole estate
behind it:

1. a maintainer sets `PRIVATE_STATE_DIR`, believing the cutover done;
2. new writes land privately, and the files already on the public branch stay;
3. `audit_public_state.py` BECOMES a gate the moment a private store is
   configured — deliberately, because a half-finished cutover reads as a
   finished one from the configuration alone — and it fails;
4. the signing job dies before it builds a roster;
5. every client refuses to run within `RosterLifetime`, 24 hours.

So the seam needed the other half: a move, run before the audit judges, that
makes "set one variable" true instead of catastrophic.

WHAT IT DOES

For every name in `state.COMMERCIAL_FILES` still sitting in the public
directory, it merges the payload into the private copy and deletes the public
one. Merging rather than replacing, because the redirect may already have
written entries privately that the public copy does not have — this runs AFTER
those writes, not before them, and losing a binding recorded in between would
lose a customer's device.

WHAT IT REFUSES

* the two directories being the same path — that configures nothing, and
  "migrating" in place would delete the only copy;
* a key present on both sides with DIFFERENT values. That is not a stale
  duplicate, it is two answers to one question, and picking either would
  silently discard a contract fact. It names the key and refuses the whole
  run: a partial move is the state this script exists to prevent.

WHAT IT CANNOT DO, AND SAYS SO

Removing a file does not un-publish it. Every commit that carried one is still
in the history of a public branch, and every clone and cached fetch already
taken still has it. Anything this moves must be treated as disclosed —
rotation and notification, not deletion. `audit_public_state.py` says the same
thing at the end of every run; it is repeated here because this is the script
somebody runs believing it fixes the disclosure.

USAGE
    LICENSES_DIR=state PRIVATE_STATE_DIR=private \\
      python3 .github/scripts/migrate_private_state.py [--check]

``--check`` reports what would move and exits 0 whatever it finds: it is a
report, and the schedule must not die over one. Without it, the move happens
and the exit status is 0 unless something was refused.
"""
import importlib.util
import os
import pathlib
import sys

_SPEC = importlib.util.spec_from_file_location(
    "state", pathlib.Path(__file__).with_name("state.py")
)
state = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(state)


def fail(message: str) -> None:
    """Report a refusal and stop, leaving both sides as they were."""
    print(f"::error::{message}")
    sys.exit(1)


def pending(public: pathlib.Path) -> list:
    """Every commercial file still on the public side."""
    return [name for name in state.COMMERCIAL_FILES if (public / name).is_file()]


def conflicts(public_payload, private_payload) -> list:
    """Keys both sides hold with different values.

    Only top-level keys are compared, because every commercial file is a flat
    map keyed by account id or by uuid. A nested difference under the same key
    still counts — the values are compared whole.
    """
    if not isinstance(public_payload, dict) or not isinstance(private_payload, dict):
        # A file that is not a map cannot be merged key-by-key, so any private
        # copy at all is a conflict unless the two are identical.
        return [] if public_payload == private_payload else ["<whole file>"]
    return sorted(
        key
        for key, value in public_payload.items()
        if key in private_payload and private_payload[key] != value
    )


def preflight(public: pathlib.Path, private: pathlib.Path, names: list) -> None:
    """Refuse the whole run if ANY file conflicts, before moving a single one.

    The refusal was claimed to be total — "a partial move is the state this
    mechanism exists to prevent" — and it was not: move() checked and moved one
    file at a time, so a conflict on a LATE file exited after the earlier ones
    had already been moved and deleted. Exactly the half-finished cutover the
    docstring promised to avoid.

    The test that covered it passed for the wrong reason: it planted the
    conflict on account-terms.json, which precedes quotas.json in
    COMMERCIAL_FILES, so the refusal happened before anything moved. Ordering
    luck, not a property.

    Two passes. This one decides; move() then only moves.
    """
    clashes = []
    for name in names:
        clash = conflicts(state.load(public / name), state.load(private / name))
        if clash:
            clashes.append(f"{name} ({', '.join(clash)})")
    if not clashes:
        return

    fail(
        f"{len(clashes)} file(s) hold different values on the public and private sides: "
        f"{'; '.join(clashes)}. That is two answers to one question, not a stale "
        "duplicate, and choosing either would discard a contract fact. NOTHING was "
        "moved — reconcile them by hand and run this again."
    )


def move(public: pathlib.Path, private: pathlib.Path, name: str) -> int:
    """Merge one file into the private store and drop the public copy.

    Conflicts are already ruled out by preflight, so this only moves. Returns
    how many top-level entries the private side gained.
    """
    public_payload = state.load(public / name)
    private_payload = state.load(private / name)

    if isinstance(public_payload, dict) and isinstance(private_payload, dict):
        gained = len([key for key in public_payload if key not in private_payload])
        merged = {**private_payload, **public_payload}
    else:
        gained = 0 if private_payload else 1
        merged = public_payload

    state.save(private / name, merged)
    (public / name).unlink()
    return gained


def main() -> None:
    """Move what is public and should not be, or report what would move."""
    public = state.licenses_dir()
    private = state.private_for(public)
    checking = "--check" in sys.argv

    if not os.environ.get("PRIVATE_STATE_DIR"):
        print(
            "PRIVATE_STATE_DIR is unset, so there is no private store to move anything "
            "into. Nothing to do — this is the state of the world today, not a fault."
        )
        return

    resolved_private, resolved_public = private.resolve(), public.resolve()
    if resolved_private == resolved_public or resolved_public in resolved_private.parents:
        fail(
            "PRIVATE_STATE_DIR is inside the public directory "
            f"({resolved_private} under {resolved_public}). state.py says it in one line — "
            "'a different path on a public branch is still public' — so this would move "
            "every commercial file from one published location to another and report a "
            "completed cutover. Equality alone did not catch it: state/private/ is not "
            "state/, and is just as public. Point it at a store the `licenses` branch "
            "is not."
        )

    outstanding = pending(public)
    if not outstanding:
        print("no commercial state is on the public branch; the cutover is complete")
        return

    if checking:
        print(
            f"::warning::{len(outstanding)} commercial file(s) would move off the public "
            f"branch: {', '.join(outstanding)}. Run this without --check to move them. "
            "Until then audit_public_state.py fails, because a configured private store "
            "with public commercial files is a half-finished cutover."
        )
        return

    #: Every conflict decided BEFORE the first move, so a refusal leaves both
    #: sides exactly as it found them.
    preflight(public, private, outstanding)

    private.mkdir(parents=True, exist_ok=True)
    for name in outstanding:
        gained = move(public, private, name)
        print(f"moved {name} off the public branch (+{gained} private entr(y/ies))")

    print(
        f"::warning::{len(outstanding)} file(s) moved. This does NOT un-publish them: "
        "every commit that carried one is still in the history of a public branch, and "
        "every clone and cached fetch already taken still has it. Treat their contents "
        "as disclosed — rotation and notification, not deletion."
    )


if __name__ == "__main__":
    main()
