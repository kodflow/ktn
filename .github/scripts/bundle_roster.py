#!/usr/bin/env python3
"""Wrap the roster and its signature into the single document clients fetch.

Two objects cannot be fetched atomically. Served separately,
raw.githubusercontent.com caches each with max-age=300, so for up to five
minutes after every re-signature a client could receive a NEW signature over
an OLD roster. That mismatch is cryptographically indistinguishable from
forgery, so a correct client refused a correct roster with the most alarming
error the scheme has — and with hourly signing, that is roughly 8% of all
wall-clock time.

roster.json and roster.json.sig are still published beside the bundle: they
are what a human reads and what `openssl pkeyutl -verify` consumes directly.
Only the bundle is authoritative for clients.
"""
import base64
import json
import os
import pathlib


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see build_roster.py for the same helper."""
    return pathlib.Path(os.environ.get("LICENSES_DIR", "licenses"))


def main() -> None:
    state = licenses_dir()
    roster = (state / "roster.json").read_bytes()
    signature = (state / "roster.json.sig").read_bytes()

    # Refuse to publish half a bundle. An empty signature would be served as
    # a well-formed document that authorises nobody, which reads to every
    # client as a forged roster rather than as the publishing failure it is.
    if not roster or not signature:
        raise SystemExit("refusing to bundle: roster or signature is empty")

    bundle = {
        "payload": base64.b64encode(roster).decode(),
        "sig": base64.b64encode(signature).decode(),
    }
    # Separators without spaces for consistency with the roster itself. The
    # bundle is not signed — its payload is — so formatting is free here, but
    # matching keeps diffs on this branch readable.
    (state / "roster.signed.json").write_text(
        json.dumps(bundle, separators=(",", ":"), sort_keys=True)
    )
    print(f"bundle: {len(roster)}B roster + {len(signature)}B signature")


if __name__ == "__main__":
    main()
