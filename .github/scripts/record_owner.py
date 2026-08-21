#!/usr/bin/env python3
"""Bind a subject to the GitHub account that requested it.

First writer wins and is never silently overwritten: parse_request.py refuses
a mismatch before we get here, so reaching this point means the author is
either the original owner or the subject is new.
"""
import json
import pathlib
import sys


def main() -> None:
    uuid, author = sys.argv[1], sys.argv[2]
    path = pathlib.Path("licenses/owners.json")
    owners = json.loads(path.read_text()) if path.exists() else {}
    owners[uuid] = author
    path.write_text(json.dumps(owners, indent=2, sort_keys=True) + "\n")
    print(f"bound {uuid} -> @{author}")


if __name__ == "__main__":
    main()
