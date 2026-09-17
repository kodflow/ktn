#!/usr/bin/env python3
"""Record that THIS run both produced and verified the candidate's signature.

The publication path used to test one thing about the signature — that a file
named roster.json.sig existed. A run with no signing key rebuilt roster.json,
skipped signing, and found the PREVIOUS run's signature sitting in the
checkout: the existence test passed, the pair was bundled, and a new payload
was published under an old signature. Every client read that as forgery, and
the workflow reported success.

Existence is not the property that matters. "Signed by us, over these exact
bytes, in this very run" is, and nothing on disk says so on its own. So the
step that runs `openssl pkeyutl -verify` writes down what it verified, and
promote_roster.py refuses to publish anything else. The attestation is a
receipt, not a security boundary: it is trustworthy exactly because it never
leaves the runner that wrote it.
"""
import datetime
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402  (the path has to be set before this can resolve)


def candidate_dir() -> pathlib.Path:
    """Where the unpublished candidate is staged; see build_roster.py."""
    #: CANDIDATE_DIR wins; the fallback delegates rather than restating the
    #: LICENSES_DIR default, which is state.licenses_dir()'s to decide.
    configured = os.environ.get("CANDIDATE_DIR")
    if configured:
        return pathlib.Path(configured)
    return state.licenses_dir()


def digest(path: pathlib.Path) -> str:
    """The SHA256 of a file, as hex."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    candidate = candidate_dir()
    roster = candidate / "roster.json"
    signature = candidate / "roster.json.sig"

    for path in (roster, signature):
        if not path.is_file() or path.stat().st_size == 0:
            print(f"::error::cannot attest: {path} is missing or empty")
            sys.exit(1)

    attestation = {
        # Which bytes were verified. A later step that rewrites either file
        # invalidates the receipt instead of riding on it.
        "roster_sha256": digest(roster),
        "sig_sha256": digest(signature),
        # Which run verified them. This is the half that a stale signature
        # cannot satisfy: an artefact from an earlier run carries an earlier
        # run id, and a re-run of the same workflow carries a new attempt.
        "run": os.environ.get("GITHUB_RUN_ID", ""),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    (candidate / "attestation.json").write_text(
        json.dumps(attestation, indent=2, sort_keys=True) + "\n"
    )
    print(
        f"attested {attestation['roster_sha256'][:12]}… signed and verified "
        f"in run {attestation['run'] or '(local)'}"
        f" attempt {attestation['attempt'] or '(local)'}"
    )


if __name__ == "__main__":
    main()
