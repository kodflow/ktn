#!/usr/bin/env python3
"""Resolve which subject a revocation issue names.

Revocation reuses the original enrolment issue as its audit trail, and the
uuid used to be read straight back out of that issue's body. A body is
editable by whoever opened it, indefinitely, and the maintainer applying the
label sees only the label. So the aim of the revocation was the one part of
the decision the subject of it could still change.

Two sources, in order:

* ``enrolments.json`` — written at approval time, keyed by issue number, and
  never rewritten. When the issue is recorded there, that record IS the
  answer and the body is not read at all.
* the body, *cross-checked against ``owners.json``* — the only source for a
  device enrolled before that file existed. The claimed uuid must be bound to
  the issue's own author, so an edited body can at worst revoke a device the
  same account already owns.

Applying the label still requires maintainer access. That gates whether a
revocation happens; this gates what it hits.
"""
import json
import os
import pathlib
import re
import sys


# state.py owns where licence state lives and how it is keyed.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402  (the path has to be set before this can resolve)


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see state.licenses_dir."""
    return state.licenses_dir()

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def section(body: str, heading: str) -> str:
    """Return the first non-empty line under a '### heading' block."""
    lines = body.replace("\r\n", "\n").split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().lower() == f"### {heading}".lower())
    except StopIteration:
        fail(f"missing '### {heading}' section")
    for line in lines[start + 1:]:
        stripped = line.strip().strip("`")
        if stripped and not stripped.startswith("###"):
            return stripped
    fail(f"empty '### {heading}' section")
    return ""


def recorded_subject(issue: str) -> str:
    """What this issue actually published, if it was recorded at approval time.

    Validated against UUID_RE like the body path is, and REFUSED rather than
    ignored when it does not match. Two reasons the check is not redundant
    even though our own approval chain writes this file:

    * falling back to "" on a corrupt record would silently downgrade to the
      EDITABLE source — the weaker of the two paths, chosen by the failure of
      the stronger one, which is the wrong direction for a failure to push a
      decision;
    * this value is written to GITHUB_OUTPUT, where a newline ends the
      assignment and starts another. Constraining the value is what closes
      that, rather than escaping it at the point of use and hoping every
      future point of use remembers.
    """
    if not issue:
        return ""
    path = licenses_dir() / "enrolments.json"
    if not path.exists():
        return ""
    recorded = json.loads(path.read_text() or "{}").get(issue, {}).get("subject", "")
    if not recorded:
        return ""
    if not UUID_RE.match(recorded):
        fail(
            f"enrolments.json records subject {recorded!r} for issue {issue}, which is not a "
            "uuid. A corrupt enrolment record is a state problem to fix, not something to "
            "route around by re-reading an editable issue body."
        )
    return recorded


def claimed_subject(body: str, author: str, author_id: str) -> str:
    """Read the uuid from the body, and refuse one the author does not own.

    For an issue with no enrolment record this is the only source there is. The
    ownership cross-check is what makes it safe to use: the binding is written
    by the approval chain, keeps its record through revocation, and is not
    editable from an issue.

    Compared by NUMERIC account id whenever the binding carries one. A login
    comparison is what a released handle defeats: whoever registers it next
    passes the check for every device the previous holder still has published,
    and revoking someone else's working licence needs no escalation to hurt.
    A binding with no id behind it — enrolled before ids were captured — still
    falls back to the login, and says so: refusing outright would leave that
    customer unable to withdraw a compromised key, which is the moment it
    matters most.
    """
    uuid = section(body, "Subject")
    if not UUID_RE.match(uuid):
        fail(f"subject {uuid!r} is not a canonical v4 uuid")

    owner_key = state.device_owner(licenses_dir(), uuid)
    if not owner_key:
        fail(
            f"{uuid} has no owner on record: nothing was ever published for it, so this "
            "issue's body does not name a device this repository can revoke."
        )
    if not author:
        fail("issue has no author; cannot confirm the body names that account's device")

    if state.is_resolved(owner_key):
        if not author_id.isdigit():
            fail(
                f"{uuid} is bound to account {owner_key}, but this run passed no numeric id "
                f"for @{author} ({author_id!r}). The workflow passes "
                "github.event.issue.user.id; without it the binding cannot be checked "
                "against the account that opened this issue."
            )
        if owner_key != author_id:
            fail(
                f"this issue was opened by @{author} (account {author_id}) but {uuid} belongs "
                f"to account {owner_key}. Refusing to revoke another account's device from an "
                "editable body."
            )
        return uuid

    legacy_login = state.key_login(owner_key)
    if legacy_login != author:
        fail(
            f"this issue was opened by @{author} but {uuid} belongs to @{legacy_login}. "
            "Refusing to revoke another account's device from an editable body."
        )
    print(
        f"::warning::{uuid} has no numeric account id behind it, so ownership was confirmed "
        f"by LOGIN. A released login can be registered by somebody else; resolve this "
        f"account with `migrate_state_keys.py --resolve {author}=<numeric-id>` to close that."
    )
    return uuid


def main() -> None:
    issue = os.environ.get("ISSUE", "")
    uuid = recorded_subject(issue)
    source = "enrolments.json"
    if not uuid:
        uuid = claimed_subject(
            os.environ.get("BODY", ""),
            os.environ.get("AUTHOR", ""),
            os.environ.get("AUTHOR_ID", ""),
        )
        source = "the issue body, cross-checked against owners.json"

    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"uuid={uuid}\n")
    print(f"revoking {uuid} (resolved from {source})")


if __name__ == "__main__":
    main()
