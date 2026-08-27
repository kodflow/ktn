#!/usr/bin/env python3
"""Bind a subject to the GitHub account that requested it.

First writer wins and is never silently overwritten: parse_request.py refuses
a mismatch before we get here, so reaching this point means the author is
either the original owner or the subject is new.
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


def main() -> None:
    uuid, author = sys.argv[1], sys.argv[2]
    path = licenses_dir() / "owners.json"
    owners = json.loads(path.read_text()) if path.exists() else {}
    owners[uuid] = author
    path.write_text(json.dumps(owners, indent=2, sort_keys=True) + "\n")
    print(f"bound {uuid} -> @{author}")


if __name__ == "__main__":
    main()
