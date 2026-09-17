#!/usr/bin/env python3
"""Publish a candidate roster, or publish nothing at all.

This is the gate the publication path did not have. Everything before it works
in a scratch directory; this script is the only thing that writes into the
state tree, and it writes the complete trio or it exits non-zero having
touched nothing — leaving the last artefact that clients accepted exactly
where it was.

Four properties are checked, and all four exist because their absence was
observed rather than imagined:

* **Signed and verified in THIS run.** The old path tested that a file named
  roster.json.sig existed. A run with no signing key rebuilt the payload, left
  it unsigned, and inherited the previous run's signature from the checkout —
  so the test passed and a new payload was published under an old signature.
  attest_candidate.py records what `openssl pkeyutl -verify` actually
  verified, and a receipt naming another run is not a signature.
* **The bundle matches the pair.** Clients read roster.signed.json; humans and
  `openssl pkeyutl -verify` read the loose pair. Publishing a bundle built
  from anything but the attested bytes would make the two disagree, which is
  the exact condition the bundle was introduced to remove.
* **Small enough for the client to accept.** The client refuses an oversized
  artefact on the network and out of its own cache, so a roster that grows
  past the ceiling is an outage that no signature can fix. Refusing to publish
  costs the remaining hours of the current window and says so; publishing it
  costs every client immediately.
* **Still inside its window.** A roster whose window has already closed, or
  nearly, authorises nobody. That only happens with a broken clock or a wedged
  build, and either way the last good artefact is the better thing to serve.
"""
import base64
import datetime
import hashlib
import json
import os
import pathlib
import sys

# What the client refuses outright — on the network and out of its own cache.
# This mirrors the ceiling compiled into pkg/license: raising it here alone
# would publish a bundle every client drops, which looks like an outage with
# no cause. The bundle is base64 inside a JSON envelope, so the payload's own
# budget is roughly three quarters of this.
#
# Measured on this format, a subject with a term costs 128 B of payload and
# 170.7 B of bundle, on 208 B of envelope and window. That puts the cliff at
# ~24,574 subjects, the ceiling below at ~22,117, and the warning at ~12,286 —
# numbers this authority is nowhere near, which is exactly when to install the
# check rather than when it is urgent.
CLIENT_MAX_BYTES = 4 * 1024 * 1024
# Refuse at nine tenths rather than at the cliff. The gap is what turns "the
# roster is too big" from an outage into a warning with hours on it.
PUBLISH_CEILING_BYTES = CLIENT_MAX_BYTES * 9 // 10
# Say something at half: ~10,000 subjects of notice before publication stops.
# Nobody should discover this ceiling from a client error.
WARN_BYTES = CLIENT_MAX_BYTES // 2
# A window with less than this left is not worth publishing over a working
# artefact; it means the clock or the build is wrong.
MIN_WINDOW_HOURS = 1

PUBLISHED = ("roster.json", "roster.json.sig", "roster.signed.json")


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see build_roster.py for the same helper."""
    return pathlib.Path(os.environ.get("LICENSES_DIR", "licenses"))


def candidate_dir() -> pathlib.Path:
    """Where the unpublished candidate is staged; see build_roster.py."""
    return pathlib.Path(os.environ.get("CANDIDATE_DIR") or licenses_dir())


def fail(message: str) -> None:
    """Refuse the publication loudly, leaving the published artefact intact."""
    print(f"::error::{message}")
    sys.exit(1)


def read(path: pathlib.Path) -> bytes:
    """Read a candidate file, refusing an absent or empty one."""
    if not path.is_file():
        fail(f"candidate is incomplete: {path} is missing")
    payload = path.read_bytes()
    if not payload:
        fail(f"candidate is incomplete: {path} is empty")
    return payload


def check_attested_in_this_run(candidate: pathlib.Path, roster: bytes, signature: bytes) -> dict:
    """Refuse anything but a signature created and verified by this run.

    The run id is what a stale artefact cannot forge: it was written by a
    different run and carries that run's number. The digests are what a later
    step cannot slip past: they pin the exact bytes openssl verified.
    """
    path = candidate / "attestation.json"
    if not path.is_file():
        fail(
            "no signing attestation: the signature was not created and verified "
            "in this run. Refusing to publish."
        )
    try:
        attestation = json.loads(path.read_text())
    except ValueError:
        fail(f"{path} is not readable JSON; refusing to publish")

    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    # An empty run id is not an identity, and comparing two of them proves
    # nothing: `"" == ""` passed, so the whole property this function asserts —
    # that the signature was produced in THIS run — degenerated to "signed by a
    # run with no identifier" wherever these variables are unset. That is
    # outside Actions today, which is exactly where somebody would be holding
    # the signing key by hand.
    if not run or not attempt:
        fail(
            "GITHUB_RUN_ID/GITHUB_RUN_ATTEMPT are unset, so there is no run identity to "
            "bind the signature to and 'signed in this run' cannot be checked. Refusing "
            "to publish. This script publishes what a workflow signed; it is not a "
            "manual tool."
        )
    if attestation.get("run", "") != run or attestation.get("attempt", "") != attempt:
        fail(
            "the signing attestation names run "
            f"{attestation.get('run') or '(none)'}/{attestation.get('attempt') or '(none)'}, "
            f"not this one ({run or '(none)'}/{attempt or '(none)'}): "
            "the signature was not produced here. Refusing to publish."
        )
    if attestation.get("roster_sha256") != hashlib.sha256(roster).hexdigest():
        fail("the payload changed after it was signed and verified; refusing to publish")
    if attestation.get("sig_sha256") != hashlib.sha256(signature).hexdigest():
        fail("the signature changed after it was verified; refusing to publish")
    return attestation


def check_bundle_matches(bundle_bytes: bytes, roster: bytes, signature: bytes) -> None:
    """The one document clients read must carry the pair humans can check."""
    try:
        bundle = json.loads(bundle_bytes)
    except ValueError:
        fail("roster.signed.json is not readable JSON; refusing to publish")
    try:
        payload = base64.b64decode(bundle.get("payload", ""), validate=True)
        sig = base64.b64decode(bundle.get("sig", ""), validate=True)
    except (ValueError, TypeError):
        fail("roster.signed.json does not carry valid base64; refusing to publish")
    if payload != roster:
        fail("the bundle's payload is not the roster that was signed; refusing to publish")
    if sig != signature:
        fail("the bundle's signature is not the one that was verified; refusing to publish")


def check_size(bundle_bytes: bytes, roster: bytes) -> None:
    """Keep the artefact inside what the client will accept, with room to spare."""
    size = len(bundle_bytes)
    subjects = 0
    try:
        subjects = len(json.loads(roster).get("subjects", {}))
    except ValueError:
        # The window check below reports an unreadable payload properly; here
        # it only costs the per-subject arithmetic in the message.
        pass

    if size > PUBLISH_CEILING_BYTES:
        fail(
            f"the bundle is {size}B, over the {PUBLISH_CEILING_BYTES}B publication "
            f"ceiling ({CLIENT_MAX_BYTES}B is what the client refuses). Refusing to "
            "publish: the previous roster still verifies, and this needs a format "
            "change — paged rosters, or subjects fetched per key — not a bigger number."
        )
    if size > WARN_BYTES:
        headroom = "unknown"
        if subjects:
            headroom = str(max(0, (PUBLISH_CEILING_BYTES - size) * subjects // size))
        print(
            f"::warning::the bundle is {size}B, over half the client's "
            f"{CLIENT_MAX_BYTES}B ceiling ({subjects} subject(s)); roughly "
            f"{headroom} more subject(s) fit before publication is refused."
        )


def instant(payload: dict, field: str) -> datetime.datetime:
    """One timestamp field, parsed and timezone-aware, or a refusal.

    `str(payload[field])` is what the previous exp check did, and it turns a
    JSON `null` into the string "None" — which then fails to parse and is
    caught, by luck rather than by design. The type is checked here so a null,
    a number or a list is refused as what it is.
    """
    raw = payload[field]
    if not isinstance(raw, str):
        fail(f"the candidate roster has a non-string {field} {raw!r}; refusing to publish")
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        fail(f"the candidate roster has an unparseable {field} {raw!r}")
    if parsed.tzinfo is None:
        fail(f"the candidate roster has a timezone-naive {field} {raw!r}")
    return parsed


def check_window(roster: bytes) -> dict:
    """A roster that authorises nobody is worse than the one already served."""
    try:
        payload = json.loads(roster)
    except ValueError:
        fail("the candidate roster is not readable JSON; refusing to publish")
    for field in ("iat", "exp"):
        if field not in payload:
            fail(f"the candidate roster has no {field!r}; refusing to publish")
    issued = instant(payload, "iat")
    expires = instant(payload, "exp")

    now = datetime.datetime.now(datetime.timezone.utc)
    # iat was PRESENT-only checked and never parsed, so a future issuance
    # published cleanly — and that is the poisoning vector seen from this side.
    # A client's anti-rollback ratchet takes its mark from IssuedAt, so a
    # roster stamped in the future pins every machine's floor above any later
    # legitimate publication: the estate stops, and the publisher did it.
    #
    # No skew tolerance, deliberately. This runs in the SAME job on the SAME
    # runner as the build that stamped iat seconds earlier, so there is no
    # second clock to be lenient about — a future iat means the runner's clock
    # is wrong or the payload changed after it was built, and both are refusals.
    if issued > now:
        fail(
            f"the candidate roster is issued at {payload['iat']!r}, in the FUTURE "
            f"(now {now.isoformat()}). A client's ratchet takes its mark from iat, so "
            "publishing this would pin every machine above any later roster and stop "
            "the estate. Check the runner's clock. Refusing to publish."
        )
    if expires <= issued:
        fail(
            f"the candidate roster expires at {payload['exp']!r}, at or before its "
            f"issuance {payload['iat']!r}. An inverted window authorises nobody, and "
            "the client refuses it outright. Refusing to publish."
        )
    hours_left = (expires - now).total_seconds() / 3600
    if hours_left < MIN_WINDOW_HOURS:
        fail(
            f"the candidate roster has {hours_left:.1f}h of window left, under the "
            f"{MIN_WINDOW_HOURS}h minimum — check the runner's clock. Refusing to publish."
        )
    return payload


def place(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Put one file in the published tree without ever leaving it half-written.

    The candidate can be on another filesystem, so the bytes are written beside
    the destination and renamed over it: a reader — including a git command
    racing this one — sees either the old file or the new one.
    """
    staged = destination.with_name(destination.name + ".incoming")
    try:
        staged.write_bytes(source.read_bytes())
        os.replace(staged, destination)
    except BaseException:
        # A failed write used to leave `<name>.incoming` behind, and the NEXT
        # run's "Commit the reconciliation" step stages the whole directory
        # with `git add -A` — so a residue of a half-written publication could
        # reach a PUBLIC branch. Cleaned up on the way out rather than left for
        # a sweep that does not know what it is committing.
        #
        # BaseException, not Exception: a cancelled workflow arrives as
        # KeyboardInterrupt, and a cancelled run is precisely when a partial
        # write is most likely.
        staged.unlink(missing_ok=True)
        raise


def main() -> None:
    candidate = candidate_dir()
    state = licenses_dir()
    if candidate.resolve() == state.resolve():
        fail(
            "CANDIDATE_DIR is the state directory: there is nothing to promote, and "
            "building in place is what published a new payload under an old signature."
        )
    state.mkdir(parents=True, exist_ok=True)

    roster = read(candidate / "roster.json")
    signature = read(candidate / "roster.json.sig")
    bundle = read(candidate / "roster.signed.json")

    check_attested_in_this_run(candidate, roster, signature)
    check_bundle_matches(bundle, roster, signature)
    check_size(bundle, roster)
    payload = check_window(roster)

    # The loose pair first, the document clients actually read last: if this
    # process dies mid-way the published bundle is still the previous, coherent
    # one rather than a new one nothing else matches.
    for name in PUBLISHED:
        place(candidate / name, state / name)

    print(
        f"published {len(payload.get('subjects', {}))} subject(s), "
        f"{len(bundle)}B bundle, valid until {payload['exp']}"
    )


if __name__ == "__main__":
    main()
