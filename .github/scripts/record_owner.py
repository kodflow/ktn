#!/usr/bin/env python3
"""Bind a subject to the GitHub account that requested it.

First writer wins and is never silently overwritten: parse_request.py refuses
a mismatch before we get here, so reaching this point means the author is
either the original owner or the subject is new.

Two files come out of this. ``owners.json`` maps a device to a login, which is
what the quota and the term are counted against. ``accounts.json`` records that
login's NUMERIC id, which is what a CI run is matched on: a login can be
renamed and a released one can be claimed by somebody else, so matching CI
entitlement on the name would make a freed handle a way in. The id cannot be
reused.
"""
import os
import json
import pathlib
import sys


def licenses_dir() -> pathlib.Path:
    """Where licence state lives.

    Defaults to ``licenses/`` so the scripts stay runnable from a plain
    checkout of ``main``. The workflow overrides it with ``LICENSES_DIR``
    because the state now lives on its own branch, checked out into a
    separate directory: ``main`` carries a required-status ruleset that
    refuses a direct push, which silently stopped every re-signature for a
    day and a half until the roster's window closed.
    """
    return pathlib.Path(os.environ.get("LICENSES_DIR", "licenses"))


def record_account_id(author: str, account_id: str) -> None:
    """Record the account's immutable numeric id.

    Written every time rather than first-writer-wins: GitHub does not reissue
    an account id, so a differing value means the workflow was handed the wrong
    one and the newest is the one to trust. The login is kept alongside it for
    diagnostics only — nothing matches on it.
    """
    # Nothing to record when the id was not supplied. That is the
    # run-it-by-hand case; the workflow always passes one, and a CI seat
    # simply does not exist for an account whose id was never captured.
    if not account_id:
        print(f"::warning::no account id for @{author}; CI entitlement will not cover it")
        return
    if not account_id.isdigit():
        print(f"::error::account id {account_id!r} for @{author} is not numeric")
        sys.exit(1)

    path = licenses_dir() / "accounts.json"
    accounts = json.loads(path.read_text()) if path.exists() else {}
    accounts[author] = {"id": account_id}
    path.write_text(json.dumps(accounts, indent=2, sort_keys=True) + "\n")
    print(f"recorded @{author} as account {account_id}")


def main() -> None:
    uuid, author = sys.argv[1], sys.argv[2]
    # Optional so the script stays runnable from a plain checkout; the
    # workflow passes github.event.issue.user.id.
    account_id = sys.argv[3] if len(sys.argv) > 3 else ""

    path = licenses_dir() / "owners.json"
    owners = json.loads(path.read_text()) if path.exists() else {}
    owners[uuid] = author
    path.write_text(json.dumps(owners, indent=2, sort_keys=True) + "\n")
    print(f"bound {uuid} -> @{author}")

    record_account_id(author, account_id)


if __name__ == "__main__":
    main()
