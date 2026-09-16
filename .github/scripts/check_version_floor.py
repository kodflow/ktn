#!/usr/bin/env python3
"""Refuse to sign a roster whose version floor no published release satisfies.

WHY THIS EXISTS

`required_version`'s own docstring states the invariant: the mirror is synced
first and the floor is recorded second, "so a floor never demands a version
nobody can download". That ordering is a convention of the CALLER, and nothing
checked it. `record_version.py` accepts a `repository_dispatch` carrying any
well-formed SemVer tag, so a manual dispatch, a replayed delivery, or a mirror
sync that failed after dispatching all produce a floor for a version that was
never published.

What that costs is the whole estate at once. Every client fetches the roster,
reads a floor it cannot meet, and is told to upgrade. The upgrade finds nothing
higher, the anti-loop guard stops it from re-executing for ever — and turns the
outcome into a refusal. The guard prevents the loop, not the unavailability.

So this makes the documented ordering VERIFIABLE instead of assumed.

WHAT IT REFUSES, AND WHAT IT DOES NOT

An unreachable release list is a WARNING and exit 0. A GitHub API outage must
not stop the signing run: the floor was already validated as SemVer when it was
recorded, the roster it would block is the one that keeps every client alive,
and refusing here would turn somebody else's outage into ours.

A list that IS obtained and holds nothing at or above the floor is exit 1. That
is not an outage, it is a floor no client can satisfy, and publishing it is the
fleet-wide refusal described above.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from record_version import precedence  # noqa: E402  (path set above)

# DEFAULT_MIRROR is where clients download from, which is the only repository
# whose releases answer "can a client satisfy this floor". The linter's own
# repository may hold tags the mirror has not published yet, so checking it
# would assert the wrong thing.
DEFAULT_MIRROR = "kodflow/ktn"

# FLOOR_FILE is written by the release pipeline and read by build_roster.
FLOOR_FILE = "required-version.txt"


def licenses_dir() -> pathlib.Path:
    """Where the published state lives."""
    return pathlib.Path(os.environ.get("LICENSES_DIR", "state"))


def recorded_floor(state_dir: pathlib.Path) -> str:
    """The floor on record, or "" when none is set."""
    path = state_dir / FLOOR_FILE
    if not path.exists():
        return ""
    return path.read_text().strip()


def published_tags(repo: str) -> list[str] | None:
    """Every release tag on the mirror, or None when the list is unreachable.

    None and an empty list are deliberately different answers: the first means
    "we do not know" and the second means "there are none", and only the second
    is grounds to refuse.
    """
    try:
        out = subprocess.run(
            ["gh", "release", "list", "--repo", repo, "--limit", "200", "--json", "tagName"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    try:
        return [entry["tagName"] for entry in json.loads(out)]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def satisfied_by(floor: str, tags: list[str]) -> bool:
    """Whether any tag is at or above the floor.

    A tag that is not well-formed SemVer is skipped rather than fatal: the
    mirror's tag namespace is not this script's to police, and one hand-made
    tag must not make a real release invisible.
    """
    try:
        want = precedence(floor)
    except ValueError:
        # A malformed floor is record_version.py's to refuse, and it does. It
        # cannot be judged here, and guessing would contradict that gate.
        return True
    for tag in tags:
        try:
            if precedence(tag) >= want:
                return True
        except ValueError:
            continue
    return False


def main() -> None:
    """Check the floor against the mirror, or say why it could not be."""
    floor = recorded_floor(licenses_dir())
    if not floor:
        print("no version floor recorded; nothing to check")
        return

    repo = os.environ.get("MIRROR_REPO", DEFAULT_MIRROR)
    tags = published_tags(repo)
    if tags is None:
        print(
            f"::warning::cannot list releases of {repo}, so the floor {floor} is unverified. "
            "Signing continues: the roster this would block is the one keeping every client "
            "alive, and the floor was validated as SemVer when it was recorded."
        )
        return

    if not satisfied_by(floor, tags):
        highest = max(tags, key=lambda tag: precedence(tag), default="none") if tags else "none"
        print(
            f"::error::the recorded floor {floor} is satisfied by no release on {repo} "
            f"(highest published: {highest}). Publishing it would tell every client to "
            "upgrade to a version that does not exist, and the anti-loop guard turns that "
            "into a refusal rather than a loop. Sync the mirror first, then record the floor."
        )
        sys.exit(1)

    print(f"floor {floor} is satisfied by a published release on {repo}")


if __name__ == "__main__":
    main()
