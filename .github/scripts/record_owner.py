#!/usr/bin/env python3
"""Bind a subject to the GitHub account that requested it.

First writer wins and is never silently overwritten: parse_request.py refuses
a mismatch before we get here, so reaching this point means the author is
either the original owner or the subject is new.

Three files come out of this. ``owners.json`` maps a device to a login, which is
what the quota and the term are counted against. ``accounts.json`` records that
login's NUMERIC id, which is what a CI run is matched on: a login can be
renamed and a released one can be claimed by somebody else, so matching CI
entitlement on the name would make a freed handle a way in. The id cannot be
reused.

``enrolments.json`` maps the ISSUE that was approved to the subject it
published. It exists because the issue body is the only other place that
mapping lives, and a body is editable by whoever opened it: a revocation
resolved from the body could be pointed at a different device between approval
and revocation. Like ``owners.json`` it survives revocation, so a decision can
still be resolved long after the key is gone — which is what lets
reconcile_decisions.py apply a revocation that was dropped rather than wait for
a complaint that a revoked device will never produce.
"""
import datetime
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
    """Record the account's immutable numeric id, and never replace it.

    This used to write every time, reasoning that GitHub does not reissue an id
    so a differing value must be a mistake and the newest must be right. The
    premise is sound and the conclusion is backwards. An id does not change for
    an account — but a LOGIN can be released and registered by somebody else,
    and then the same login legitimately carries a different id. Overwriting
    handed that login's licence — its term, its devices, its quota and its CI
    seat — to whoever picked up the freed handle.

    So a differing id is a CONFLICT, and a conflict stops the run. Which of the
    two it is — a renamed account whose record should move, or a recycled handle
    that needs its own licence — is a question with a billing answer, and this
    script is not where it gets guessed.

    The login stays as a label. It is what the term and the quota are keyed on
    today, which is the wider problem this guard fences rather than solves.
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
    recorded = str(accounts.get(author, {}).get("id", "") or "")
    if recorded and recorded != account_id:
        print(
            f"::error::@{author} is account {account_id}, but this login is on record as "
            f"account {recorded}. GitHub releases logins and does not reissue ids, so this "
            "is either a renamed account or a different person holding a freed handle — and "
            "the second one must not inherit the first one's licence. Refusing to rebind it; "
            "decide which it is and move the record by hand."
        )
        sys.exit(1)
    if recorded:
        return
    accounts[author] = {"id": account_id}
    path.write_text(json.dumps(accounts, indent=2, sort_keys=True) + "\n")
    print(f"recorded @{author} as account {account_id}")


def record_enrolment(issue: str, uuid: str) -> None:
    """Record which subject an approved issue published.

    First writer wins, and a second writer naming a DIFFERENT subject is a
    conflict rather than an update: one issue enrols one device for good. A
    disagreement means the body changed between two approvals of the same
    issue, which is precisely the edit this file exists to stop mattering.
    """
    # Nothing to record when the issue number was not supplied — the
    # run-it-by-hand case. A decision on an issue with no enrolment record is
    # reported for a human instead of being resolved from the body.
    if not issue:
        print("::warning::no issue number supplied; this enrolment cannot be reconciled later")
        return
    if not issue.isdigit():
        print(f"::error::issue number {issue!r} is not numeric")
        sys.exit(1)

    path = licenses_dir() / "enrolments.json"
    enrolments = json.loads(path.read_text() or "{}") if path.exists() else {}
    recorded = enrolments.get(issue, {}).get("subject")
    if recorded and recorded != uuid:
        print(
            f"::error::issue #{issue} already enrolled {recorded}, not {uuid}. "
            "One issue enrols one device; refusing to rebind it."
        )
        sys.exit(1)
    if recorded:
        return
    # The subject and the date, and nothing else. This branch is PUBLIC, and
    # the account behind a subject is already recorded — once — in owners.json;
    # copying it here would put the same customer's name in one more public
    # file for no gain, since resolution is by subject.
    enrolments[issue] = {
        "subject": uuid,
        "at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(enrolments, indent=2, sort_keys=True) + "\n")
    print(f"recorded issue #{issue} as the enrolment of {uuid}")


def main() -> None:
    uuid, author = sys.argv[1], sys.argv[2]
    # Optional so the script stays runnable from a plain checkout; the
    # workflow passes github.event.issue.user.id.
    account_id = sys.argv[3] if len(sys.argv) > 3 else ""
    # Likewise optional: the workflow passes github.event.issue.number.
    issue = sys.argv[4] if len(sys.argv) > 4 else ""

    path = licenses_dir() / "owners.json"
    owners = json.loads(path.read_text()) if path.exists() else {}
    owners[uuid] = author
    path.write_text(json.dumps(owners, indent=2, sort_keys=True) + "\n")
    print(f"bound {uuid} -> @{author}")

    record_account_id(author, account_id)
    record_enrolment(issue, uuid)


if __name__ == "__main__":
    main()
