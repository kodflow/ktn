#!/usr/bin/env python3
"""Report what the PUBLIC licence branch discloses, and verify the cutover.

This repository is public and the `licenses` branch is where licence state
lives, so every file written there is published to anyone who asks. Some of it
has to be: the signed roster is fetched unauthenticated by every client on
every cold start, and a licence scheme whose roster needed a credential would
need a credential to check a credential.

The rest is contract data. Which GitHub accounts are customers, when each one's
term ends, who negotiated extra seats, and which login belongs to which numeric
id. A client needs none of it.

Two modes, both non-destructive:

* ``PRIVATE_STATE_DIR`` unset — reports exactly what is public today, and says
  what a removal would and would not fix. Exit 0: this is the state of the
  world, not a regression.
* ``PRIVATE_STATE_DIR`` set — the cutover has been made, so any commercial file
  still on the public branch is a FAILURE. Exit non-zero, because a
  half-finished cutover looks finished from the configuration alone.

Either way it checks the roster trio is present. Removing contract data must
never be allowed to take the thing clients actually fetch with it.

**On anonymising the account id: it does not work, and this is where to read
why.** A GitHub numeric account id is a small integer — six to nine digits in
practice — so any hash of one is recovered by hashing the whole range, which is
seconds of work. A salt does not help while the salt has to ship in the client:
the client is what checks the roster, so whatever it needs to compute the
comparison is public by construction.

An opaque identifier CAN be made non-recomputable — 128 random bits minted once
per account and stored only in the private half — and it would work for
anything the client does not have to match locally. It does NOT work for the
``ci`` block: a CI run proves itself with an OIDC token carrying
``repository_owner_id``, a number, and the client compares that number against
the roster. There is no opaque value the client could compare it to without
being given the mapping, which would publish the mapping.

So the ``ci`` block irreducibly discloses the numeric ids of CI-entitled
accounts, for as long as the check is made locally against a public document.
Moving it behind an authenticated endpoint is a product decision, not a naming
problem. What CAN be removed from the public branch is everything that turns
those numbers into names and dates — which is what the rest of this does.
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

# What a client fetches. These MUST stay public and MUST stay present.
PUBLIC_ARTEFACTS = ("roster.json", "roster.json.sig", "roster.signed.json")

# Why each commercial file should not be public, in the terms a reader of the
# branch would care about. Kept beside the audit rather than in a comment so
# the report says what is at stake instead of only naming a filename.
DISCLOSURE = {
    "accounts.json": "maps a GitHub login to its numeric account id",
    "owners.json": "maps each device to the LOGIN that owns it — names customers and counts their machines",
    "device-owners.json": "maps each device to an account id — counts machines per customer",
    "licences.json": "records the contract end date, per named login",
    "account-terms.json": "records the contract end date, per account id",
    "quotas.json": "records which named logins negotiated extra seats",
    "account-quotas.json": "records which accounts negotiated extra seats",
    "ci-owners.json": "records which organisations a licence covers for CI",
    "unresolved-accounts.json": "lists logins holding licences that could not be attributed",
}


def cutover_configured(state_dir: pathlib.Path) -> bool:
    """Whether a private store has actually been pointed somewhere else.

    Set-but-equal counts as unset: pointing PRIVATE_STATE_DIR at the public
    directory configures nothing, and reporting it as a completed cutover
    would be the worst outcome this script can produce.
    """
    configured = os.environ.get("PRIVATE_STATE_DIR")
    if not configured:
        return False
    return pathlib.Path(configured).resolve() != state_dir.resolve()


def missing_public_artefacts(state_dir: pathlib.Path) -> list:
    """Any client-facing file that is absent, which is an outage."""
    return [name for name in PUBLIC_ARTEFACTS if not (state_dir / name).is_file()]


def exposed(state_dir: pathlib.Path) -> list:
    """Every commercial file currently sitting on the public branch."""
    return [name for name in state.COMMERCIAL_FILES if (state_dir / name).is_file()]


def main() -> None:
    state_dir = state.licenses_dir()
    problems = 0

    absent = missing_public_artefacts(state_dir)
    if absent:
        # Reported first: a branch with no roster is an outage, and it must not
        # be buried under a privacy report.
        print(
            f"::error::the public branch is missing {', '.join(absent)}. Every client fetches "
            "these unauthenticated; without them the parc stops within the roster's window."
        )
        problems += 1

    public = exposed(state_dir)
    if not public:
        print("no commercial state is on the public branch")
    elif cutover_configured(state_dir):
        for name in public:
            print(
                f"::error::{name} is still on the PUBLIC branch although PRIVATE_STATE_DIR is "
                f"configured: it {DISCLOSURE[name]}. A half-finished cutover reads as a "
                "finished one from the configuration alone."
            )
        problems += 1
    else:
        for name in public:
            print(f"::warning::{name} is PUBLIC and it {DISCLOSURE[name]}")
        print(
            "::warning::no private store is configured (PRIVATE_STATE_DIR is unset), so the "
            "above is published to anyone who fetches this branch. Running the migration and "
            "`migrate_state_keys.py --prune` removes the login↔device and login↔term links; "
            "the remaining files need a private store the signer reads with a token, which is "
            "an infrastructure decision and is not made by any script here."
        )
        print(
            "::warning::and removing a file does not un-publish it. Every commit that carried "
            "one is still in the history of a public branch, and every clone and cached fetch "
            "already taken still has it. Anything listed above should be treated as disclosed "
            "and handled as such — rotation and notification, not deletion."
        )

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
