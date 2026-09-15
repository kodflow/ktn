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


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see build_roster.py for the same helper."""
    return pathlib.Path(os.environ.get("LICENSES_DIR", "licenses"))

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
    """What this issue actually published, if it was recorded at approval time."""
    if not issue:
        return ""
    path = licenses_dir() / "enrolments.json"
    if not path.exists():
        return ""
    return json.loads(path.read_text() or "{}").get(issue, {}).get("subject", "")


def claimed_subject(body: str, author: str) -> str:
    """Read the uuid from the body, and refuse one the author does not own.

    For an issue with no enrolment record this is the only source there is. The
    ownership cross-check is what makes it safe to use: owners.json is written
    by the approval chain, keeps its bindings through revocation, and is not
    editable from an issue.
    """
    uuid = section(body, "Subject")
    if not UUID_RE.match(uuid):
        fail(f"subject {uuid!r} is not a canonical v4 uuid")

    path = licenses_dir() / "owners.json"
    owners = json.loads(path.read_text() or "{}") if path.exists() else {}
    owner = owners.get(uuid)
    if owner is None:
        fail(
            f"{uuid} has no owner on record: nothing was ever published for it, so this "
            "issue's body does not name a device this repository can revoke."
        )
    if not author:
        fail("issue has no author; cannot confirm the body names that account's device")
    if owner != author:
        fail(
            f"this issue was opened by @{author} but {uuid} belongs to @{owner}. "
            "Refusing to revoke another account's device from an editable body."
        )
    return uuid


def main() -> None:
    issue = os.environ.get("ISSUE", "")
    uuid = recorded_subject(issue)
    source = "enrolments.json"
    if not uuid:
        uuid = claimed_subject(os.environ.get("BODY", ""), os.environ.get("AUTHOR", ""))
        source = "the issue body, cross-checked against owners.json"

    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"uuid={uuid}\n")
    print(f"revoking {uuid} (resolved from {source})")


if __name__ == "__main__":
    main()
