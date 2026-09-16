#!/usr/bin/env python3
"""Tests for promote_roster.py — the only step allowed to publish.

The case that matters is the one that used to succeed: a run that rebuilt the
roster, did not sign it, and found the previous run's signature in the
checkout. The old path tested that a file named roster.json.sig existed, so it
bundled a NEW payload under an OLD signature and pushed it. Nobody gained
access — every client refused everything — and the workflow reported success.

So each refusal here asserts two things: the run fails, and what was already
published is still byte-for-byte what it was.
"""
import base64
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("promote_roster.py")
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
# Not a real signature: promote_roster never verifies crypto — it verifies that
# the step which DID verify wrote down these exact bytes and this exact run.
SIGNATURE = b"a 64-byte-ish blob standing in for an ed25519 signature........."


def load_script():
    """Import promote_roster.py by path; it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("promote_roster", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def roster_bytes(hours_left: int = 24, subjects: int = 1) -> bytes:
    """A roster in the exact serialisation build_roster.py emits."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    expires = now + datetime.timedelta(hours=hours_left)
    payload = {
        "iat": now.isoformat().replace("+00:00", "Z"),
        "exp": expires.isoformat().replace("+00:00", "Z"),
        "subjects": {f"{MAC[:-1]}{i}": {"fp": "SHA256:" + "x" * 43} for i in range(subjects)},
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


class PromoteTestCase(unittest.TestCase):
    """A published tree with something valuable in it, and a staged candidate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.state = root / "state"
        self.candidate = root / "candidate"
        self.state.mkdir()
        self.candidate.mkdir()

        # What is already serving. Any refusal must leave this alone.
        self.published = {
            "roster.json": b'{"exp":"already","iat":"published","subjects":{}}',
            "roster.json.sig": b"the signature clients accept today",
            "roster.signed.json": b'{"payload":"already","sig":"published"}',
        }
        for name, payload in self.published.items():
            (self.state / name).write_bytes(payload)

        os.environ["LICENSES_DIR"] = str(self.state)
        os.environ["CANDIDATE_DIR"] = str(self.candidate)
        os.environ["GITHUB_RUN_ID"] = "4242"
        os.environ["GITHUB_RUN_ATTEMPT"] = "1"
        for key in ("LICENSES_DIR", "CANDIDATE_DIR", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
            self.addCleanup(os.environ.pop, key, None)
        self.module = load_script()

    def stage(self, roster=None, signature=SIGNATURE, run="4242", attempt="1", bundle=None):
        """Stage a candidate the way the signing job does, then let tests spoil it."""
        roster = roster_bytes() if roster is None else roster
        (self.candidate / "roster.json").write_bytes(roster)
        (self.candidate / "roster.json.sig").write_bytes(signature)
        (self.candidate / "attestation.json").write_text(
            json.dumps(
                {
                    "roster_sha256": hashlib.sha256(roster).hexdigest(),
                    "sig_sha256": hashlib.sha256(signature).hexdigest(),
                    "run": run,
                    "attempt": attempt,
                    "at": "2026-09-15T00:00:00Z",
                }
            )
        )
        if bundle is None:
            bundle = json.dumps(
                {
                    "payload": base64.b64encode(roster).decode(),
                    "sig": base64.b64encode(signature).decode(),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        (self.candidate / "roster.signed.json").write_bytes(bundle)
        return roster

    def assertNothingPublished(self):
        """The previous artefact is what clients keep getting. Byte for byte."""
        for name, payload in self.published.items():
            self.assertEqual((self.state / name).read_bytes(), payload, name)
        # A half-written file left in the tree would be committed by the next
        # step that runs `git add -A` anywhere near it.
        self.assertEqual(sorted(p.name for p in self.state.glob("*.incoming")), [])

    def assertRefused(self):
        """Run main() expecting a refusal, and no change to what is served."""
        with self.assertRaises(SystemExit) as raised:
            self.module.main()
        self.assertNotEqual(raised.exception.code, 0)
        self.assertNothingPublished()


class PublicationTest(PromoteTestCase):
    """What reaches the branch, and — mostly — what does not."""

    def test_a_signed_and_verified_candidate_is_published(self):
        """The ordinary run: all three files land, and they are the candidate's."""
        roster = self.stage()

        self.module.main()

        self.assertEqual((self.state / "roster.json").read_bytes(), roster)
        self.assertEqual((self.state / "roster.json.sig").read_bytes(), SIGNATURE)
        bundle = json.loads((self.state / "roster.signed.json").read_text())
        self.assertEqual(base64.b64decode(bundle["payload"]), roster)

    def test_a_signature_from_another_run_is_refused(self):
        """THE defect: a keyless run rebuilt the payload and kept the old .sig.

        The old condition was `hashFiles('state/roster.json.sig') != ''`, which
        the inherited file satisfied. Every client then read the published pair
        as forged while the workflow reported success.
        """
        self.stage(run="4241")

        self.assertRefused()

    def test_a_retry_of_the_same_run_is_still_another_run(self):
        """Re-running a failed attempt re-signs; it does not inherit.

        A receipt from attempt 1 would otherwise let attempt 2 publish a
        payload attempt 2 never signed.
        """
        self.stage(attempt="7")

        self.assertRefused()

    def test_a_candidate_with_no_receipt_is_refused(self):
        """No receipt means nothing verified these bytes. Existence is not proof."""
        self.stage()
        (self.candidate / "attestation.json").unlink()

        self.assertRefused()

    def test_a_payload_rewritten_after_verification_is_refused(self):
        """Signing covers bytes, not filenames.

        This is the same defect one step later: rebuild the roster after the
        verification step and the signature no longer covers what is published.
        """
        self.stage()
        (self.candidate / "roster.json").write_bytes(roster_bytes(hours_left=23))

        self.assertRefused()

    def test_a_signature_swapped_after_verification_is_refused(self):
        self.stage()
        (self.candidate / "roster.json.sig").write_bytes(b"some other signature blob")

        self.assertRefused()

    def test_a_bundle_that_is_not_the_signed_pair_is_refused(self):
        """Clients read the bundle; humans read the loose pair. They must agree."""
        self.stage(
            bundle=json.dumps(
                {
                    "payload": base64.b64encode(roster_bytes(hours_left=12)).decode(),
                    "sig": base64.b64encode(SIGNATURE).decode(),
                }
            ).encode()
        )

        self.assertRefused()

    def test_an_incomplete_candidate_is_refused(self):
        """Publishing two thirds of a trio is publishing a mismatch."""
        for missing in ("roster.json", "roster.json.sig", "roster.signed.json"):
            with self.subTest(missing=missing):
                self.setUp()
                self.stage()
                (self.candidate / missing).unlink()

                self.assertRefused()

    def test_an_empty_file_is_refused(self):
        """An empty signature is a well-formed document that authorises nobody."""
        self.stage()
        (self.candidate / "roster.json.sig").write_bytes(b"")

        self.assertRefused()

    def test_building_in_place_is_refused(self):
        """With one directory there is no candidate — only the live artefact.

        Staging is the structural half of the fix: a clean directory has no
        previous signature to inherit, so the failure this file is about cannot
        be reached at all.
        """
        os.environ["CANDIDATE_DIR"] = str(self.state)
        self.stage()

        self.assertRefused()


class WindowTest(PromoteTestCase):
    """A roster is only worth publishing while it authorises somebody."""

    def test_an_expired_candidate_is_refused(self):
        """Publishing this replaces a working artefact with a dead one."""
        self.stage(roster=roster_bytes(hours_left=-1))

        self.assertRefused()

    def test_a_candidate_with_minutes_left_is_refused(self):
        """A clock that is a day out builds a roster that is already over."""
        self.stage(roster=roster_bytes(hours_left=0))

        self.assertRefused()

    def test_a_roster_with_no_window_is_refused(self):
        self.stage(roster=b'{"subjects":{}}')

        self.assertRefused()


class SizeTest(PromoteTestCase):
    """The client drops an oversized artefact, on the wire and out of its cache."""

    def test_the_ceiling_is_the_one_the_client_enforces(self):
        """Pinned deliberately: raising it here alone publishes an unusable bundle.

        4 MiB is what pkg/license refuses. The publication ceiling sits below
        it so the roster crossing this line is a warning with hours on it
        rather than an outage nobody predicted.
        """
        self.assertEqual(self.module.CLIENT_MAX_BYTES, 4 * 1024 * 1024)
        self.assertLess(self.module.PUBLISH_CEILING_BYTES, self.module.CLIENT_MAX_BYTES)
        self.assertLess(self.module.WARN_BYTES, self.module.PUBLISH_CEILING_BYTES)

    def test_an_oversized_bundle_is_refused(self):
        """Refusing costs the rest of the current window; publishing costs everyone.

        The ceiling is lowered rather than the roster inflated to megabytes: the
        arithmetic being tested is the comparison, not base64.
        """
        self.module.CLIENT_MAX_BYTES = 512
        self.module.PUBLISH_CEILING_BYTES = 460
        self.module.WARN_BYTES = 256
        self.stage(roster=roster_bytes(subjects=12))

        self.assertRefused()

    def test_a_bundle_under_the_ceiling_is_published(self):
        """The same shape, below the line, must still go out."""
        self.module.CLIENT_MAX_BYTES = 1 << 20
        self.module.PUBLISH_CEILING_BYTES = 1 << 19
        self.module.WARN_BYTES = 1 << 18
        roster = self.stage(roster=roster_bytes(subjects=12))

        self.module.main()

        self.assertEqual((self.state / "roster.json").read_bytes(), roster)


class RunIdentityTest(PromoteTestCase):
    """"Signed in THIS run" needs a run to be named.

    The attestation's run/attempt are compared against the environment's. Both
    unset compared EQUAL — `"" == ""` — so the property degenerated to "signed
    by a run with no identifier" exactly where somebody would be holding the
    signing key by hand, which is the one place it needed to hold.
    """

    def test_an_unset_run_id_refuses_rather_than_matching_itself(self):
        """THE ROW. Two empty strings are not an identity."""
        self.stage(run="", attempt="")
        os.environ.pop("GITHUB_RUN_ID", None)
        os.environ.pop("GITHUB_RUN_ATTEMPT", None)

        with self.assertRaises(SystemExit) as raised:
            self.module.main()

        self.assertNotEqual(raised.exception.code, 0)
        self.assertNothingPublished()

    def test_an_unset_attempt_refuses_too(self):
        """Half an identity is not one: a re-run is a different publication."""
        self.stage(run="4242", attempt="")
        os.environ["GITHUB_RUN_ID"] = "4242"
        os.environ.pop("GITHUB_RUN_ATTEMPT", None)

        with self.assertRaises(SystemExit) as raised:
            self.module.main()

        self.assertNotEqual(raised.exception.code, 0)
        self.assertNothingPublished()


class PlaceResidueTest(PromoteTestCase):
    """A half-written publication must not survive its own failure.

    `place` writes `<name>.incoming` beside the destination and renames it
    over, which is what makes a reader see either the old file or the new one.
    A write that failed left the staged file behind — and the NEXT run's
    "Commit the reconciliation" step stages the whole directory with
    `git add -A`, so the residue of a failed publication could reach a PUBLIC
    branch.
    """

    def test_a_failed_write_leaves_no_incoming_file(self):
        """A control, not the discriminating row — and worth saying so.

        An unreadable SOURCE raises inside `source.read_bytes()`, which is
        evaluated before `staged.write_bytes` is entered, so no staged file
        ever existed and this passed against the unfixed code too. The row
        that discriminates is the cancelled write below, where the staged file
        is already on disk when the failure arrives.
        """
        destination = self.state / "roster.json"
        missing = self.candidate / "does-not-exist.json"

        with self.assertRaises(OSError):
            self.module.place(missing, destination)

        self.assertFalse(
            destination.with_name("roster.json.incoming").exists(),
            "a residue here is committed by the next run's `git add -A`",
        )

    def test_a_cancelled_write_leaves_no_incoming_file(self):
        """THE ROW. The staged file exists and the rename never happens.

        Caught as BaseException rather than Exception because a cancelled
        workflow arrives as KeyboardInterrupt, and a cancelled run is exactly
        when a partial write is most likely. Measured red against the unfixed
        code: `roster.json.incoming` survived.
        """
        destination = self.state / "roster.json"
        source = self.candidate / "roster.json"
        source.write_bytes(b"{}")

        original = self.module.os.replace

        def interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt

        self.module.os.replace = interrupt
        self.addCleanup(setattr, self.module.os, "replace", original)

        with self.assertRaises(KeyboardInterrupt):
            self.module.place(source, destination)

        self.assertFalse(destination.with_name("roster.json.incoming").exists())

    def test_a_successful_write_leaves_no_incoming_file_either(self):
        """The ordinary path: renamed away, so nothing is left to sweep up."""
        destination = self.state / "roster.json"
        source = self.candidate / "roster.json"
        source.write_bytes(b'{"ok":true}')

        self.module.place(source, destination)

        self.assertEqual(destination.read_bytes(), b'{"ok":true}')
        self.assertFalse(destination.with_name("roster.json.incoming").exists())


if __name__ == "__main__":
    unittest.main()
