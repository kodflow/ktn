#!/usr/bin/env python3
"""Resolve which subject a revocation issue names.

Revocation reuses the original enrolment issue as its audit trail: the body
still carries the "### Subject" section from the initial request, so this
only re-extracts that uuid — there is no key to validate and no ownership
check to make, since applying a label at all already requires maintainer
access.
"""
import os
import re
import sys

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


def main() -> None:
    body = os.environ.get("BODY", "")
    uuid = section(body, "Subject")
    if not UUID_RE.match(uuid):
        fail(f"subject {uuid!r} is not a canonical v4 uuid")

    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"uuid={uuid}\n")
    print(f"revoking {uuid}")


if __name__ == "__main__":
    main()
