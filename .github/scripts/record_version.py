#!/usr/bin/env python3
"""Write the mandatory-update floor, and never lower it.

The floor is carried by the signed roster, so whatever lands here is signed and
served as the minimum version allowed to run. Two things used to be missing.

**It was not really validated.** A shell regex accepted `v01.02.03` and
`v1.0.0-`, neither of which a SemVer comparison reads the way the tag looks.
The workflow's own comment said why that matters — "a malformed value fails
open on every client" — and then checked shape rather than grammar.

**It could go DOWN.** `repository_dispatch` is a fire-and-forget event. Two
releases in quick succession, a retried delivery, a replayed payload: the
order of arrival is not the order of release, and the last writer won
unconditionally. A floor that moves backwards silently un-mandates an update
that was already mandatory, which is the failure nobody notices — the fleet
keeps running the version we decided to stop supporting.

So a floor only ever moves forward. An older version arriving late is reported
and dropped, which is the correct outcome for a message that is merely late.
"""
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402  (the path has to be set before this can resolve)

# SemVer 2.0.0, with the leading `v` this chain publishes and without build
# metadata. Leading zeros are rejected because SemVer says a numeric identifier
# must not have them, and a comparison that disagrees with the tag is worse
# than a refused release.
#
# Build metadata (`+sha`) is refused as well: it does not participate in
# precedence, so accepting it would publish a floor two clients could order
# differently depending on how carefully they parse.
SEMVER_RE = re.compile(
    r"^v(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?$"
)
FLOOR = "required-version.txt"


def licenses_dir() -> pathlib.Path:
    """Where licence state lives — delegated, never reimplemented.

    This was a second copy of the same two lines. state.licenses_dir() is the
    one place that decides, and a duplicate is how the two drift: the moment
    the default or the variable name changes in one, this file keeps reading
    somewhere else and writes the version floor into a directory nothing
    signs.
    """
    return state.licenses_dir()


def fail(message: str) -> None:
    """Abort loudly: a floor nobody can satisfy blocks every client."""
    print(f"::error::{message}")
    sys.exit(1)


def precedence(version: str) -> tuple:
    """Order two versions the way SemVer says, or raise ValueError.

    A release outranks every prerelease of the same core version, a numeric
    prerelease identifier outranks nothing alphanumeric, and a shorter run of
    identifiers loses to a longer one that starts the same way. Comparing the
    strings instead would put v1.10.0 below v1.9.0 and v1.0.0 below
    v1.0.0-rc.1 — the second of which would un-mandate a real release.
    """
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"{version!r} is not a SemVer tag")
    core = (int(match.group("major")), int(match.group("minor")), int(match.group("patch")))
    prerelease = match.group("prerelease")
    if prerelease is None:
        # 1 beats the 0 every prerelease carries: 1.0.0 > 1.0.0-rc.1.
        return (core, 1, [])
    identifiers = []
    for identifier in prerelease.split("."):
        if identifier.isdigit():
            identifiers.append((0, int(identifier), ""))
        else:
            identifiers.append((1, 0, identifier))
    return (core, 0, identifiers)


def current(path: pathlib.Path) -> str:
    """The floor on record, or "" until the first release publishes one."""
    if not path.exists():
        return ""
    return path.read_text().strip()


def main() -> None:
    version = os.environ.get("VERSION", "").strip()
    # A dispatch with no version would blank the floor and silently disable
    # mandatory updates, which is the failure nobody notices.
    if not version:
        fail("release-published carried no version — refusing to clear the floor.")
    try:
        arriving = precedence(version)
    except ValueError as problem:
        fail(
            f"{problem}. The floor is compared with SemVer on every client and a malformed "
            "value fails open there; refusing to publish an unusable floor."
        )

    path = licenses_dir() / FLOOR
    recorded = current(path)
    if recorded:
        try:
            standing = precedence(recorded)
        except ValueError:
            # Corrupt state already on the branch. Replacing it with something
            # valid is strictly better than preserving it: every client is
            # currently reading a floor it cannot parse.
            print(
                f"::warning::the recorded floor {recorded!r} is not a SemVer tag; replacing "
                f"it with {version}."
            )
        else:
            if arriving < standing:
                # Not an error: a late or replayed dispatch is a normal thing
                # for an at-least-once delivery to do. Dropping it is the
                # correct handling, and saying so is how a genuine mistake —
                # a rolled-back release — gets noticed.
                print(
                    f"::warning::{version} is older than the recorded floor {recorded}; the "
                    "floor does not move backwards. Dispatch order is not release order, so "
                    "this is most likely a late delivery. Nothing written."
                )
                print(json.dumps({"floor": recorded, "changed": False}))
                return
            if arriving == standing:
                print(f"{recorded} is already the floor; nothing written")
                print(json.dumps({"floor": recorded, "changed": False}))
                return

    path.write_text(version + "\n")
    print(f"floor moved from {recorded or '(none)'} to {version}")
    print(json.dumps({"floor": version, "changed": True}))


if __name__ == "__main__":
    main()
