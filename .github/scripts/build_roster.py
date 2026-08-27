#!/usr/bin/env python3
"""Rebuild the roster from the published public keys.

The roster carries fingerprints rather than keys: it stays small, and the
comparison the binary makes is exact. Its top-level validity window is what
bounds both revocation latency and how long a hostile endpoint can replay a
genuine copy — separate from each subject's own "exp", which is that one
licence's term and does not move on re-signature.
"""
import os
import base64
import datetime
import hashlib
import json
import pathlib


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

# Must match RosterLifetime in pkg/license. Widening it here without widening
# it there would publish a roster the binary refuses; the reverse would leave
# clients blocked between signatures.
LIFETIME_HOURS = 24


def fingerprint(line: str) -> str:
    """Render the SHA256 fingerprint exactly as ssh.FingerprintSHA256 does."""
    blob = base64.b64decode(line.split()[1])
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def subject_value(pub: pathlib.Path, state_dir: pathlib.Path) -> dict:
    """Build one subject's roster entry: fingerprint, plus its term if known.

    A subject with no sidecar (or one missing the field) gets no "exp" at
    all — pkg/license.SubjectValue treats an absent/zero expiry as "no expiry
    recorded", not "already expired", so omitting it here is what lets a
    subject published before this sidecar existed keep working.
    """
    value = {"fp": fingerprint(pub.read_text().strip())}
    meta_path = state_dir / f"{pub.stem}.meta.json"
    if meta_path.exists():
        expires_at = json.loads(meta_path.read_text()).get("expiresAt")
        if expires_at:
            value["exp"] = expires_at
    return value


def main() -> None:
    licenses_path = licenses_dir()
    # A fresh state branch has no keys until the first subject is published;
    # the hourly schedule must still succeed with zero subjects instead of
    # crashing on a missing parent directory.
    licenses_path.mkdir(parents=True, exist_ok=True)

    subjects = {
        pub.stem: subject_value(pub, licenses_path)
        for pub in sorted(licenses_path.glob("*.pub"))
    }

    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    roster = {
        "iat": now.isoformat().replace("+00:00", "Z"),
        "exp": (now + datetime.timedelta(hours=LIFETIME_HOURS)).isoformat().replace("+00:00", "Z"),
        "subjects": subjects,
    }
    # Separators without spaces keep the signed bytes stable: the signature
    # covers the exact serialisation, so cosmetic formatting changes would
    # invalidate it.
    (licenses_path / "roster.json").write_text(
        json.dumps(roster, separators=(",", ":"), sort_keys=True)
    )
    print(f"roster: {len(subjects)} subject(s), valid until {roster['exp']}")


if __name__ == "__main__":
    main()
