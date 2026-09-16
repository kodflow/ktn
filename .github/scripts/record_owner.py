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
import re
import sys

# state.py owns where licence state lives and how it is keyed.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402  (the path has to be set before this can resolve)

# How a maintainer resolves who a CI seat is for. The value is a NUMERIC
# account id because that is what `repository_owner_id` carries in the OIDC
# token a runner presents — a login would have to be resolved through an API
# call these scripts deliberately never make. `ciOwner:self` is the other
# answer, and it has to be sayable too: it resolves the question to "the
# requester", which is a decision, not an absence of one.
CI_OWNER_LABEL_RE = re.compile(r"^ciOwner:(self|\d+)$")


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see state.licenses_dir."""
    return state.licenses_dir()


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

    # private_for, not licenses_dir: accounts.json is in COMMERCIAL_FILES.
    # Writing it publicly survived the cutover seam entirely — migrate_private_
    # state.py would move it off the public branch and the very next approval
    # would put it back, so audit_public_state.py (a gate once a store is
    # configured) would fail the following run and stop every signature.
    path = state.private_for(licenses_dir()) / "accounts.json"
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


def ci_owner_decisions(labels: list) -> list:
    """Every DISTINCT `ciOwner:` claim in the label set, in label order.

    Read from the FULL current label set rather than the one that triggered the
    run, for the same reason `expireAt:` is: the decision has to be sayable in
    the same breath as the approval, and a label applied afterwards did not
    exist when the approval ran.

    Returns a list rather than the first match, because the first match was a
    coin toss. Labels arrive in whatever order the API returns them — an order
    no maintainer chose and none can see — so a maintainer who applied
    `ciOwner:123`, thought better of it, and applied `ciOwner:456` without
    removing the first got whichever came out on top. The CI seat is a billing
    fact; it is not settled by list order.

    The caller refuses to decide when this returns more than one. That is the
    doctrine of this whole file — "both answers are expressible and neither is
    inferred" — and arbitrating here would have been the inference.

    Duplicates of the SAME value collapse: two identical labels are one
    answer, said twice, and there is nothing ambiguous about it.
    """
    claims = []
    for name in labels:
        matched = CI_OWNER_LABEL_RE.match(name)
        if matched and matched.group(1) not in claims:
            claims.append(matched.group(1))
    return claims


def record_ci_beneficiary(account_id: str, claimed: str, labels: list) -> None:
    """Separate WHO ASKED from WHO A CI RUN IS FOR, and never conflate them.

    The requester is the account that opened the issue. The beneficiary is the
    account whose repositories a CI run may be authorised under. For an
    individual buying a licence for their own repositories those are the same
    number and nothing here has to exist. For a customer whose repositories
    belong to an organisation they are DIFFERENT numbers, and until this file
    existed the chain recorded the requester and published it as the CI key —
    an entitlement matched against `repository_owner_id`, which is the org's
    id, so it never matched and nothing reported a problem. A silent no.

    Keyed by the requester's numeric id, so this file needs no migration when
    the rest of the state is re-keyed: it was never keyed on a login.

    Three states, and the middle one is the point:

    * no entry — the beneficiary IS the requester. The default, and correct for
      a personal account.
    * ``claimed`` with no ``beneficiary`` — UNRESOLVED. The request named
      someone else and nothing here can verify the requester speaks for them.
      build_roster.py omits the CI entry and says so on every build.
    * ``beneficiary`` — a maintainer answered, by label. That id is the CI key.

    This script does not choose the policy. Whether an organisation's CI seat
    belongs to the organisation or to the member who bought the licence is a
    billing question; both answers are expressible (`ciOwner:<id>` and
    `ciOwner:self`) and neither is inferred.

    Nor is a third one. Two disagreeing `ciOwner:` labels used to resolve by
    whichever the API listed first — an inference dressed as a reading, in the
    one function whose whole point is that it does not infer. It now refuses
    and stays UNRESOLVED.
    """
    decisions = ci_owner_decisions(labels)
    # Reported BEFORE the early return, so an ambiguous set is never silent —
    # including when nothing was claimed and a maintainer is granting a seat
    # outright. The "moved" warning further down only fires once a value has
    # been decided, so without this a first arbitration was the one nobody saw.
    if len(decisions) > 1:
        print(
            f"::warning::this approval carries {len(decisions)} disagreeing `ciOwner:` "
            f"labels ({', '.join(sorted(decisions))}). Refusing to pick one: they arrive "
            "in whatever order the API returns and the CI seat is a billing fact, not a "
            "list-order one. The entitlement stays UNRESOLVED — devices are published and "
            "working, CI is not covered — until exactly one `ciOwner:` label remains on an "
            "approval. Remove the labels that no longer apply and re-apply `license:approved`."
        )
        # Degrade to the documented middle state rather than failing the
        # approval: a label typo must not hold a paying customer's device
        # enrolment hostage, and build_roster.py already omits the CI entry and
        # says so on every build, so this stays visible until it is fixed.
        decisions = []
    decision = decisions[0] if decisions else ""
    if not (claimed or decision):
        return
    if not account_id:
        print(
            "::warning::a CI owner was named or decided, but no numeric account id was "
            "captured for the requester; there is no key to record it under and the CI "
            "entitlement cannot be expressed."
        )
        return

    # Same: ci-owners.json is COMMERCIAL_FILES. Who negotiated a CI seat, and
    # for which organisation, is contract data — and the numeric ids in it are
    # the one thing the roster's `ci` block already discloses irreducibly, so
    # there is no reason to publish the NAMES beside them as well.
    path = state.private_for(licenses_dir()) / "ci-owners.json"
    owners = json.loads(path.read_text() or "{}") if path.exists() else {}
    entry = dict(owners.get(account_id, {}))
    before = dict(entry)

    if claimed:
        entry["claimed"] = claimed
    if decision:
        resolved = account_id if decision == "self" else decision
        previous = entry.get("beneficiary")
        entry["beneficiary"] = resolved
        entry["decidedBy"] = "maintainer"
        # A maintainer may legitimately move it — an org changes, a licence is
        # reassigned — but never quietly. A CI seat changing owner is exactly
        # the change that should be readable in a run log afterwards.
        if previous and previous != resolved:
            print(
                f"::warning::CI beneficiary for account {account_id} moved from "
                f"{previous} to {resolved} by label. Every CI run under {previous} "
                "stops being covered."
            )
        print(f"account {account_id}: CI beneficiary is account {resolved} (by label)")
    elif "beneficiary" not in entry:
        print(
            f"::warning::account {account_id} claims CI for {claimed!r} and no maintainer has "
            "resolved it. Its devices are published and working; it has NO CI entitlement "
            "until a `ciOwner:<numeric-id>` label (or `ciOwner:self`) lands on an approval. "
            "This is reported rather than guessed because publishing the requester's own id "
            "would be an entitlement that never matches."
        )
    else:
        print(
            f"account {account_id}: CI beneficiary stays account {entry['beneficiary']}; "
            f"this request's claim of {claimed!r} does not move a decided one."
        )

    if entry == before:
        return
    owners[account_id] = entry
    path.write_text(json.dumps(owners, indent=2, sort_keys=True) + "\n")


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
    # The CI owner the request asked for, as parse_request.py read it. A claim,
    # never a grant — see record_ci_beneficiary.
    claimed_ci_owner = sys.argv[5] if len(sys.argv) > 5 else ""
    labels = [label["name"] for label in json.loads(os.environ.get("LABELS_JSON", "[]"))]

    # Bound to the NUMERIC account id, which is the identity; the login is
    # only a label from here on. The predecessor of this file was keyed on the
    # login, so a released handle carried its devices — and everything counted
    # from them — to whoever registered it next.
    #
    # An empty id is the run-it-by-hand case, and it is recorded as the
    # explicit `login:` marker rather than silently as a login: that marker is
    # not a valid id anywhere in this chain, so it gets no CI seat and the
    # approval gate refuses its next request instead of attributing it.
    account_key = account_id if account_id.isdigit() else state.unresolved_key(author)
    state.bind_device(licenses_dir(), uuid, account_key)
    print(f"bound {uuid} -> account {account_key} (@{author})")

    record_account_id(author, account_id)
    record_ci_beneficiary(account_id, claimed_ci_owner, labels)
    record_enrolment(issue, uuid)


if __name__ == "__main__":
    main()
