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
import sys

# state.py owns where licence state lives and how it is keyed.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402  (the path has to be set before this can resolve)


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see state.licenses_dir."""
    return state.licenses_dir()


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


def ci_beneficiary(ci_owners: dict, account_id: str) -> str:
    """Whose repositories this licence's CI covers, or "" when nobody decided.

    A CI run does not present the requester's id. It presents
    ``repository_owner_id`` — the owner of the repository the run is in. For an
    individual linting their own repositories that is the same number as the
    account that opened the licence request, and keying CI on the requester was
    right by coincidence. For a customer whose repositories belong to an
    ORGANISATION it is a different number, so the entitlement was published
    against an id no run would ever carry: a licence that looked issued, a CI
    job that failed its licence check, and no error anywhere naming the cause.

    So the beneficiary is now a recorded field and "" is a real answer. An
    unresolved claim yields no entitlement rather than a wrong one, because a
    missing entitlement is a question a customer asks and a never-matching one
    is a question nobody knows to ask.
    """
    entry = ci_owners.get(str(account_id), {})
    beneficiary = str(entry.get("beneficiary", "") or "")
    if beneficiary:
        # A non-numeric beneficiary cannot be what a token carries, so it would
        # be published as an entitlement that never matches — the exact failure
        # this field exists to end.
        if not beneficiary.isdigit():
            print(
                f"::error::ci-owners.json records a non-numeric CI beneficiary "
                f"{beneficiary!r} for account {account_id}; a CI run is matched on a numeric "
                "owner id, so this entitlement is omitted rather than published unmatched."
            )
            return ""
        return beneficiary
    if entry.get("claimed"):
        print(
            f"::warning::account {account_id} asked for CI under {entry['claimed']!r} and no "
            "maintainer has resolved it, so it gets no CI entitlement. Its devices are "
            "unaffected. Apply `ciOwner:<numeric-id>` to grant the organisation, or "
            "`ciOwner:self` to grant the requester, on any approval for this account."
        )
        return ""
    # Nothing claimed and nothing decided: the beneficiary is the requester.
    # Unchanged behaviour, and the correct one for a personal account.
    return str(account_id)


def ci_entitlements(state_dir: pathlib.Path) -> dict:
    """Which accounts a CI run may be authorised for, and until when.

    Keyed by the BENEFICIARY's numeric id, never a login. A login can be
    renamed, and a released one can be claimed by somebody else — matching a CI
    run on the name would turn a freed handle into a way in. GitHub does not
    reissue an id.

    An account appears only while it still has a published device. A licence
    with no active device is not a licence anyone is using, and its CI should
    stop with it; nothing here needs a separate revocation path.

    The term is the licence's own, so CI expires exactly when the devices do.
    """
    ci_owners_path = state_dir / "ci-owners.json"
    # Absent for every account that never named one, which is most of them.
    ci_owners = json.loads(ci_owners_path.read_text() or "{}") if ci_owners_path.exists() else {}

    # Iterated from the BINDINGS rather than from accounts.json, because the
    # binding is what says an account has a device and the directory is only
    # the login→id map. state.device_owners merges the id-keyed file over the
    # login-keyed one, so an un-migrated branch still produces entitlements
    # and the roster stays signable through the migration.
    entitled = {}
    for account_key in sorted(set(state.device_owners(state_dir).values())):
        # An account with no numeric id behind it cannot have a CI seat: there
        # is no id to match `repository_owner_id` against, and inventing one —
        # or falling back to the login — would defeat the reason the id is
        # used. Its devices are unaffected.
        if not state.is_resolved(account_key):
            continue
        # A published key is what makes a device active — the same rule the
        # seat count uses, and for the same reason: the bindings deliberately
        # survive revocation so an identity cannot be squatted afterwards.
        if not state.active_devices(state_dir, account_key):
            continue
        beneficiary = ci_beneficiary(ci_owners, account_key)
        if not beneficiary:
            continue
        entry = {}
        expires_at = state.account_term(state_dir, account_key)
        if expires_at is not state.ABSENT and expires_at:
            entry["exp"] = expires_at
        # Two licences can legitimately name the same beneficiary — two members
        # of one organisation, each with their own devices. The owner is covered
        # while EITHER licence is live, so the later term wins; silently keeping
        # whichever account was iterated last would have made the answer depend
        # on dictionary order. Said out loud because two licences pointing at
        # one owner is also what a duplicate sale looks like.
        existing = entitled.get(beneficiary)
        if existing is not None:
            print(
                f"::warning::more than one licence names account {beneficiary} as its CI "
                "beneficiary; CI is covered while any of them is live, so the latest term "
                "is published. Check this is not a licence sold twice."
            )
            # A missing "exp" is the client's "no term recorded", which it
            # reads as no expiry — the widest entry there is, so it must sort
            # LAST rather than compare as an empty string and lose to a date.
            def reach(value: dict) -> tuple:
                """Order terms so an absent one is the furthest away."""
                return (1, "") if "exp" not in value else (0, value["exp"])

            if reach(existing) >= reach(entry):
                continue
        entitled[beneficiary] = entry
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
