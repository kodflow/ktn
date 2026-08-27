#!/usr/bin/env python3
"""Record a subject's licence term at approval time.

Every licence gets a term. The default is one year from approval; a
maintainer can override it by also applying an `expireAt:YYYY-MM-DD` label
before (or together with) `license:approved` — labels applied afterwards
don't retroactively change an already-recorded term. Rotation re-runs this
the same as a first-time publish, so a rotated key gets a fresh term too.
"""
import datetime
import json
import os
import pathlib
import re
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

LABEL_RE = re.compile(r"^expireAt:(\d{4}-\d{2}-\d{2})$")
DEFAULT_TERM = datetime.timedelta(days=365)


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def main() -> None:
    uuid = sys.argv[1]
    labels = [label["name"] for label in json.loads(os.environ.get("LABELS_JSON", "[]"))]

    explicit = next(filter(None, (LABEL_RE.match(name) for name in labels)), None)
    if explicit:
        try:
            expires_at = datetime.datetime.strptime(explicit.group(1), "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            fail(f"expireAt label {explicit.group(0)!r} is not a valid calendar date")
    else:
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        expires_at = now + DEFAULT_TERM

    iso = expires_at.isoformat().replace("+00:00", "Z")
    (licenses_dir() / f"{uuid}.meta.json").write_text(
        json.dumps({"expiresAt": iso}, indent=2, sort_keys=True) + "\n"
    )
    print(f"{uuid} expires {iso}")


if __name__ == "__main__":
    main()
