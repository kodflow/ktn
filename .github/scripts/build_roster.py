#!/usr/bin/env python3
"""Rebuild the roster from the published public keys.

The roster carries fingerprints rather than keys: it stays small, and the
comparison the binary makes is exact. Its validity window is what bounds both
revocation latency and how long a hostile endpoint can replay a genuine copy.
"""
import base64
import datetime
import hashlib
import json
import pathlib

# Must match RosterLifetime in pkg/license. Widening it here without widening
# it there would publish a roster the binary refuses; the reverse would leave
# clients blocked between signatures.
LIFETIME_HOURS = 24


def fingerprint(line: str) -> str:
    """Render the SHA256 fingerprint exactly as ssh.FingerprintSHA256 does."""
    blob = base64.b64decode(line.split()[1])
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def main() -> None:
    subjects = {}
    for pub in sorted(pathlib.Path("licenses").glob("*.pub")):
        subjects[pub.stem] = fingerprint(pub.read_text().strip())

    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    roster = {
        "iat": now.isoformat().replace("+00:00", "Z"),
        "exp": (now + datetime.timedelta(hours=LIFETIME_HOURS)).isoformat().replace("+00:00", "Z"),
        "subjects": subjects,
    }
    # Separators without spaces keep the signed bytes stable: the signature
    # covers the exact serialisation, so cosmetic formatting changes would
    # invalidate it.
    pathlib.Path("licenses/roster.json").write_text(
        json.dumps(roster, separators=(",", ":"), sort_keys=True)
    )
    print(f"roster: {len(subjects)} subject(s), valid until {roster['exp']}")


if __name__ == "__main__":
    main()
