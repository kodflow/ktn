#!/usr/bin/env python3
"""Extract and validate a licence request from an issue body.

Everything in the body is attacker-controlled: anyone can open an issue. The
approval label gates *whether* we act, this script gates *what* we accept, so
a malformed or hostile payload can never reach the roster.
"""
import json
import os
import pathlib
import re
import sys

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# Only ed25519 is accepted: it is what `ktn-linter license create` mints, and
# narrowing the accepted algorithms narrows what the verifier must handle.
KEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]+={0,3}(\s+\S+)?$")


def fail(message: str) -> None:
    """Abort loudly so the maintainer sees why an approval did not take."""
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


def main() -> None:
    body = os.environ.get("BODY", "")
    author = os.environ.get("AUTHOR", "")
    if not author:
        fail("issue has no author")

    uuid = section(body, "Subject")
    if not UUID_RE.match(uuid):
        fail(f"subject {uuid!r} is not a canonical v4 uuid")

    key = section(body, "Public key")
    if not KEY_RE.match(key):
        fail("public key is not a single ssh-ed25519 authorized-keys line")

    # Ownership is what stops a takeover: anyone may request a subject, but a
    # subject already bound to someone else may only be rotated by that
    # account. Without this check an issue claiming a known uuid would swap
    # the key and hijack the licence.
    owners_path = pathlib.Path("licenses/owners.json")
    owners = json.loads(owners_path.read_text() or "{}") if owners_path.exists() else {}

    recorded = owners.get(uuid)
    if recorded is not None and recorded != author:
        fail(f"subject {uuid} belongs to @{recorded}, not @{author}")

    # One licence per account, checked here rather than left to the issue
    # workflow alone: the dedupe job only catches issues opened through the
    # form, this is what makes it true regardless of how the issue got made.
    other = next((existing for existing, owner in owners.items() if owner == author and existing != uuid), None)
    if other is not None:
        fail(f"@{author} already owns subject {other} — one licence per account")

    pathlib.Path("/tmp/subject.pub").write_text(key.rstrip() + "\n")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"uuid={uuid}\n")
    print(f"accepted subject {uuid} for @{author}")


if __name__ == "__main__":
    main()
