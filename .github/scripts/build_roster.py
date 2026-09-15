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
import binascii
import datetime
import hashlib
import json
import pathlib
import struct


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


def candidate_dir() -> pathlib.Path:
    """Where the roster being built is written, which is NOT where state lives.

    The signing job stages its work in a scratch directory, and only a
    candidate whose signature was created AND verified in the same run is
    copied into the published tree — see promote_roster.py. Building in place
    is what let a run with no signing key leave a NEW roster.json beside the
    PREVIOUS roster.json.sig: the steps after it tested only that a .sig
    existed, so they bundled and pushed that mismatched pair, and every client
    then reported the roster as forged.

    Defaults to the state directory so a hand-run from a plain checkout keeps
    behaving as it always did.
    """
    return pathlib.Path(os.environ.get("CANDIDATE_DIR") or licenses_dir())

# Must match RosterLifetime in pkg/license. Widening it here without widening
# it there would publish a roster the binary refuses; the reverse would leave
# clients blocked between signatures.
LIFETIME_HOURS = 24


# RFC 8709 wire format, checked here as well as at the gate. The two exist for
# different reasons: parse_request.py refuses a bad key so it never reaches the
# branch, and this refuses to be stopped by one that did anyway — a hand edit,
# or a key published before that validation existed.
ED25519_ALGORITHM = b"ssh-ed25519"
ED25519_KEY_BYTES = 32


def ed25519_blob(line: str) -> bytes:
    """Decode an authorized-keys line strictly, or raise ValueError.

    `base64.b64decode` without validate=True DISCARDS characters it does not
    recognise, so a corrupt blob used to be fingerprinted as whatever it
    happened to decode to — a subject nothing would ever match, published with
    no complaint. Bad padding was the louder half: it raised, and the exception
    took the whole roster with it. One unreadable key must not be able to stop
    every signature.
    """
    parts = line.split()
    if len(parts) < 2:
        raise ValueError("not an authorized-keys line")
    blob = base64.b64decode(parts[1], validate=True)
    offset = 0
    fields = []
    for _ in range(2):
        if offset + 4 > len(blob):
            raise ValueError("truncated length prefix")
        (length,) = struct.unpack(">I", blob[offset : offset + 4])
        end = offset + 4 + length
        if end > len(blob):
            raise ValueError("length prefix runs past the blob")
        fields.append(blob[offset + 4 : end])
        offset = end
    if fields[0] != ED25519_ALGORITHM:
        raise ValueError(f"blob declares {fields[0]!r}, not {ED25519_ALGORITHM!r}")
    if len(fields[1]) != ED25519_KEY_BYTES:
        raise ValueError(f"{len(fields[1])} bytes of key material, not {ED25519_KEY_BYTES}")
    if offset != len(blob):
        raise ValueError("trailing bytes after the key material")
    return blob


def fingerprint(line: str) -> str:
    """Render the SHA256 fingerprint exactly as ssh.FingerprintSHA256 does."""
    digest = hashlib.sha256(ed25519_blob(line)).digest()
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


def required_version(state_dir: pathlib.Path) -> str:
    """Read the mandatory-update floor, or "" when none is set.

    The file is written by the release pipeline after a build reaches the
    public mirror, which is what keeps the floor and the downloadable release
    in step. Publishing a floor no release satisfies would block every client
    with nothing to upgrade to, so the ORDER matters: mirror first, floor
    second.
    """
    path = state_dir / "required-version.txt"
    # No file is the normal state until the first release publishes one.
    if not path.exists():
        return ""
    return path.read_text().strip()


def ci_entitlements(state_dir: pathlib.Path) -> dict:
    """Which accounts a CI run may be authorised for, and until when.

    Keyed by the account's NUMERIC id, never its login. A login can be renamed,
    and a released one can be claimed by somebody else — matching a CI run on
    the name would turn a freed handle into a way in. GitHub does not reissue
    an id.

    An account appears only while it still has a published device. A licence
    with no active device is not a licence anyone is using, and its CI should
    stop with it; nothing here needs a separate revocation path.

    The term is the licence's own, so CI expires exactly when the devices do.
    """
    accounts_path = state_dir / "accounts.json"
    owners_path = state_dir / "owners.json"
    licences_path = state_dir / "licences.json"
    # No accounts file means no account id was ever captured, which is how
    # every licence issued before this existed looks. They keep working; they
    # simply get no CI seat until their next approval records an id.
    if not (accounts_path.exists() and owners_path.exists()):
        return {}

    accounts = json.loads(accounts_path.read_text() or "{}")
    owners = json.loads(owners_path.read_text() or "{}")
    licences = json.loads(licences_path.read_text() or "{}") if licences_path.exists() else {}

    entitled = {}
    for login, record in accounts.items():
        account_id = record.get("id")
        if not account_id:
            continue
        # A published key is what makes a device active — the same rule the
        # seat count uses, and for the same reason: owners.json deliberately
        # keeps revoked bindings so an identity cannot be squatted afterwards.
        active = any(
            owner == login and (state_dir / f"{uuid}.pub").is_file()
            for uuid, owner in owners.items()
        )
        if not active:
            continue
        entry = {}
        expires_at = licences.get(login, {}).get("expiresAt")
        if expires_at:
            entry["exp"] = expires_at
        entitled[str(account_id)] = entry
    return entitled


def main() -> None:
    licenses_path = licenses_dir()
    # A fresh state branch has no keys until the first subject is published;
    # the hourly schedule must still succeed with zero subjects instead of
    # crashing on a missing parent directory.
    licenses_path.mkdir(parents=True, exist_ok=True)
    out_path = candidate_dir()
    out_path.mkdir(parents=True, exist_ok=True)

    subjects, unreadable = {}, []
    for pub in sorted(licenses_path.glob("*.pub")):
        try:
            subjects[pub.stem] = subject_value(pub, licenses_path)
        except (binascii.Error, ValueError) as problem:
            # Loud, and survivable. Publishing this subject is impossible —
            # there is no fingerprint to publish — but stopping here would
            # withhold the roster from every other subject too, and a roster
            # that is not re-signed blocks the whole parc within 24 hours. One
            # device fails; the rest keep working and the log says which.
            unreadable.append(pub.name)
            print(f"::error::{pub.name} is not a usable ssh-ed25519 key ({problem}); omitted from the roster")

    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    roster = {
        "iat": now.isoformat().replace("+00:00", "Z"),
        "exp": (now + datetime.timedelta(hours=LIFETIME_HOURS)).isoformat().replace("+00:00", "Z"),
        "subjects": subjects,
    }

    # The mandatory-update floor. It is carried by the roster because the
    # roster is the one document every client already fetches on a cold start
    # and already authenticates: a floor published here cannot be skipped by
    # going offline nor forged by redirecting the endpoint.
    #
    # Its value is written by the release pipeline, not by hand — "updates are
    # mandatory as soon as one exists" is only true if nobody has to remember
    # to raise it. required-version.txt is a one-line file on this branch that
    # the mirror sync updates after publishing.
    #
    # Omitted entirely when absent: an empty "minv" and a missing one mean the
    # same thing to the client (no floor), and leaving the key out keeps the
    # signed bytes identical to what older rosters looked like.
    required = required_version(licenses_path)
    if required:
        roster["minv"] = required

    # Which accounts a CI run may be authorised for. Omitted entirely when
    # empty: an absent "ci" and an empty one mean the same thing to the client
    # (no CI entitlement), and leaving the key out keeps the signed bytes
    # identical to what older rosters looked like.
    #
    # It is carried by the roster for the same reason the version floor is:
    # this document is already fetched on every cold start and already
    # authenticated against the compiled-in anchor, so an entitlement
    # published here cannot be forged by redirecting an endpoint.
    entitlements = ci_entitlements(licenses_path)
    if entitlements:
        roster["ci"] = entitlements
    # Separators without spaces keep the signed bytes stable: the signature
    # covers the exact serialisation, so cosmetic formatting changes would
    # invalidate it.
    (out_path / "roster.json").write_text(
        json.dumps(roster, separators=(",", ":"), sort_keys=True)
    )
    print(
        f"roster: {len(subjects)} subject(s), "
        f"{len(entitlements)} CI account(s), valid until {roster['exp']}"
        + (f"; {len(unreadable)} unusable key(s) omitted: {', '.join(unreadable)}" if unreadable else "")
    )


if __name__ == "__main__":
    main()
