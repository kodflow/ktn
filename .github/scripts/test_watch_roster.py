#!/usr/bin/env python3
"""Tests for watch_roster.py — the monitoring that is not inside the signer.

Every case here is a way the signing workflow's own alarm stays quiet. That
alarm is the last step of the job that signs, conditioned on a step output: a
job that fails at the push, or never starts, emits nothing. The thirteen-hour
outage this repository had was exactly that shape.

So the assertion that matters most is the boring one: a check that could not be
made must produce a finding, never a clean bill of health.
"""
import base64
import datetime
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("watch_roster.py")
NOW = datetime.datetime(2026, 9, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
SIGNATURE = b"stand-in for an ed25519 signature"


def load_script():
    """Import watch_roster.py by path; it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("watch_roster", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def roster(iat_hours_ago=0.1, window_hours=24, subjects=1) -> bytes:
    """A roster payload as build_roster.py serialises one."""
    iat = NOW - datetime.timedelta(hours=iat_hours_ago)
    payload = {
        "iat": iat.isoformat().replace("+00:00", "Z"),
        "exp": (iat + datetime.timedelta(hours=window_hours)).isoformat().replace("+00:00", "Z"),
        "subjects": {f"subject-{i}": {"fp": "SHA256:" + "x" * 43} for i in range(subjects)},
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def bundle(payload=None, signature=SIGNATURE) -> bytes:
    """The single document clients fetch."""
    return json.dumps(
        {
            "payload": base64.b64encode(roster() if payload is None else payload).decode(),
            "sig": base64.b64encode(signature).decode(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def accept(_payload, _signature):
    """A verifier that says the served bundle is genuine."""
    return True


def reject(_payload, _signature):
    """A verifier that says it is not."""
    return False


class WatchTestCase(unittest.TestCase):
    """One healthy origin, and a recent successful signing run."""

    def setUp(self):
        self.module = load_script()
        self.last_success = (NOW - datetime.timedelta(minutes=20)).isoformat().replace("+00:00", "Z")

    def judge(self, served=None, verify=accept, signer=None):
        """Evaluate one or more origins at a fixed instant."""
        if served is None:
            served = {"raw": bundle()}
        sources = {name: self.module.unpack(raw) for name, raw in served.items()}
        return self.module.evaluate(
            sources,
            NOW,
            verify,
            self.last_success if signer is None else signer,
        )

    def assertClean(self, findings):
        self.assertEqual(findings, [], "\n".join(findings))

    def assertMentions(self, findings, fragment):
        self.assertTrue(
            any(fragment in finding for finding in findings),
            f"no finding mentions {fragment!r}:\n" + "\n".join(findings) or "(none)",
        )


class HealthyTest(WatchTestCase):
    """The state that must be quiet, or the alarm is worthless."""

    def test_a_fresh_verified_roster_reports_nothing(self):
        self.assertClean(self.judge())

    def test_two_origins_serving_the_same_bytes_report_nothing(self):
        payload = roster()
        self.assertClean(self.judge({"raw": bundle(payload), "api": bundle(payload)}))


class FreshnessTest(WatchTestCase):
    """Publication stops hours before clients notice. That gap is the warning."""

    def test_a_stale_signature_is_reported_while_the_window_is_still_open(self):
        """The early signal: 20 hours of window left and nothing publishing.

        Waiting for the window to close would turn a fixable morning into an
        outage. The signer's own freshness alarm cannot see this at all — it
        only fires from inside a run that got that far.
        """
        findings = self.judge({"raw": bundle(roster(iat_hours_ago=4))})

        self.assertMentions(findings, "publication has stopped")

    def test_a_closed_window_is_reported_as_clients_refusing(self):
        findings = self.judge({"raw": bundle(roster(iat_hours_ago=30))})

        self.assertMentions(findings, "Clients are refusing to run")

    def test_a_window_inside_the_margin_is_reported(self):
        findings = self.judge({"raw": bundle(roster(iat_hours_ago=18))})

        self.assertMentions(findings, "of window left")


class AuthenticityTest(WatchTestCase):
    """What a client would call spoofing, found before a customer finds it."""

    def test_a_bundle_that_does_not_verify_is_reported(self):
        findings = self.judge(verify=reject)

        self.assertMentions(findings, "does NOT verify")

    def test_an_unconfigured_key_is_a_finding_not_a_pass(self):
        """A check that could not be made must never read as a check that passed."""
        findings = self.judge(verify=None)

        self.assertMentions(findings, "authenticity NOT CHECKED")


class OriginTest(WatchTestCase):
    """A dead or lying origin is the thing being watched for."""

    def test_an_origin_serving_nothing_is_reported(self):
        findings = self.judge({"raw": b""})

        self.assertMentions(findings, "served nothing at all")

    def test_an_unreadable_origin_does_not_stop_the_others(self):
        """One broken origin must not cost the judgement on the rest."""
        findings = self.judge({"broken": b"<html>404</html>", "raw": bundle()})

        self.assertMentions(findings, "is not readable JSON")
        self.assertEqual(len(findings), 1)

    def test_origins_that_disagree_are_reported(self):
        """Independently cached copies; a disagreement is a half-landed push."""
        findings = self.judge(
            {"raw": bundle(roster(iat_hours_ago=0.1)), "api": bundle(roster(iat_hours_ago=1))}
        )

        self.assertMentions(findings, "disagree about what the current roster is")

    def test_a_bundle_with_an_empty_signature_is_reported(self):
        """A well-formed document that authorises nobody."""
        findings = self.judge({"raw": bundle(signature=b"")})

        self.assertMentions(findings, "empty payload or signature")

    def test_a_payload_that_is_not_a_roster_is_reported(self):
        findings = self.judge({"raw": bundle(payload=b"not json")})

        self.assertMentions(findings, "not readable JSON")


class SizeTest(WatchTestCase):
    """The client drops an oversized artefact; a signature cannot fix that."""

    def test_the_ceiling_matches_the_publication_gate(self):
        """Two scripts, one number. Drift between them is a silent outage."""
        self.assertEqual(self.module.CLIENT_MAX_BYTES, 4 * 1024 * 1024)

    def test_an_oversized_artefact_is_reported_as_blocking_everyone(self):
        self.module.CLIENT_MAX_BYTES = 256
        self.module.WARN_BYTES = 128

        findings = self.judge()

        self.assertMentions(findings, "Every client is blocked")

    def test_crossing_half_the_ceiling_is_reported_early(self):
        self.module.CLIENT_MAX_BYTES = 1024
        self.module.WARN_BYTES = 128

        findings = self.judge()

        self.assertMentions(findings, "outgrowing the format")


class SignerLivenessTest(WatchTestCase):
    """The question the signing workflow cannot answer about itself."""

    def test_a_signer_that_has_not_succeeded_recently_is_reported(self):
        stale = (NOW - datetime.timedelta(hours=5)).isoformat().replace("+00:00", "Z")

        findings = self.judge(signer=stale)

        self.assertMentions(findings, "has not succeeded")

    def test_an_unknown_last_success_is_reported(self):
        """Not knowing is a finding. It is how a disabled workflow looks."""
        findings = self.judge(signer="")

        self.assertMentions(findings, "No successful run")

    def test_an_unreadable_last_success_is_reported(self):
        findings = self.judge(signer="whenever")

        self.assertMentions(findings, "unreadable")


class ReportTest(WatchTestCase):
    """What the workflow branches on."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.output = root / "github_output"
        self.output.touch()
        self.served = root / "raw.json"
        os.environ["GITHUB_OUTPUT"] = str(self.output)
        os.environ["RUNNER_TEMP"] = str(root)
        os.environ["SIGNER_LAST_SUCCESS"] = self.last_success
        for key in ("GITHUB_OUTPUT", "RUNNER_TEMP", "SIGNER_LAST_SUCCESS", "VENDOR_PUBLIC_KEY_FILE"):
            self.addCleanup(os.environ.pop, key, None)

    def outputs(self):
        parsed = {}
        for line in self.output.read_text().splitlines():
            name, _, value = line.partition("=")
            parsed[name] = value
        return parsed

    def run_main(self):
        self.module.sys.argv = ["watch_roster.py", f"raw={self.served}"]
        self.module.main()

    def test_findings_produce_an_alarm_and_a_report_file(self):
        self.served.write_bytes(bundle(roster(iat_hours_ago=30)))

        self.run_main()

        outputs = self.outputs()
        self.assertEqual(outputs["alarm"], "true")
        self.assertTrue(pathlib.Path(outputs["report"]).is_file())

    def test_a_missing_file_is_an_alarm_not_a_crash(self):
        """A failed fetch arrives as an absent file. It must still be judged."""
        self.run_main()

        self.assertEqual(self.outputs()["alarm"], "true")

    def test_no_origins_at_all_fails_loudly(self):
        """Watching nothing and reporting health is the failure being removed."""
        self.module.sys.argv = ["watch_roster.py"]

        with self.assertRaises(SystemExit) as raised:
            self.module.main()

        self.assertNotEqual(raised.exception.code, 0)

class RealSignatureTest(unittest.TestCase):
    """openssl_verifier itself, against a key this test mints and signs with.

    Every other authenticity test hands `judge` a boolean stub — `reject`, or
    None — so the verifier's OWN body was never executed: not the
    `-rawin -pubin` flag pair, not the temp-file staging, not the mapping of an
    exit status to a verdict. A typo in any of those would make the watcher
    report "does NOT verify" for every genuine roster it is watching, or — the
    direction that matters — report a pass it never established.

    So this drives the real command with real material. The negative rows are
    what make it worth having: a verifier that returned True unconditionally
    would satisfy the positive row on its own.
    """

    def setUp(self):
        # FAILS rather than skips when openssl is absent. A skipped class reads
        # exactly like a passing one in the summary, and "a check that could
        # not be made must never read as a check that passed" is the rule
        # watch_roster.py itself is built on — it would be a strange rule to
        # keep in the code and drop in the test of that code. openssl is also
        # not optional here in any real sense: openssl_verifier shells out to
        # it, so a machine without it cannot run the watcher at all.
        self.assertIsNotNone(
            shutil.which("openssl"),
            "openssl is required: openssl_verifier shells out to it, so its absence "
            "means the watcher cannot work on this machine, not that this test is moot",
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        os.environ["RUNNER_TEMP"] = str(self.dir)
        self.addCleanup(os.environ.pop, "RUNNER_TEMP", None)
        self.module = load_script()
        self.payload = b'{"iat":"2026-09-16T00:00:00Z","subjects":{}}'

    def mint(self, name):
        """One ed25519 pair, returning the path of its PUBLIC half."""
        private = self.dir / f"{name}.pem"
        public = self.dir / f"{name}.pub.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(private)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
            check=True, capture_output=True,
        )
        return private, public

    def sign(self, private, payload):
        """The detached raw ed25519 signature the signer produces."""
        message = self.dir / "message.bin"
        message.write_bytes(payload)
        signature = self.dir / "signature.bin"
        subprocess.run(
            [
                "openssl", "pkeyutl", "-sign", "-rawin",
                "-inkey", str(private), "-in", str(message), "-out", str(signature),
            ],
            check=True, capture_output=True,
        )
        return signature.read_bytes()

    def test_a_genuine_signature_verifies(self):
        """The flags and the staging are right, or nothing below means anything."""
        private, public = self.mint("vendor")
        verify = self.module.openssl_verifier(public)

        self.assertTrue(verify(self.payload, self.sign(private, self.payload)))

    def test_a_modified_payload_does_not_verify(self):
        """One byte. This is what a CDN serving altered content looks like."""
        private, public = self.mint("vendor")
        signature = self.sign(private, self.payload)
        verify = self.module.openssl_verifier(public)

        self.assertFalse(verify(self.payload + b" ", signature))

    def test_another_key_does_not_verify(self):
        """THE spoofing case: a well-formed signature by the wrong signer.

        A roster signed by somebody else is exactly what the vendor key is
        checked against, and it must fail for that reason rather than by
        happening to be malformed.
        """
        other_private, _ = self.mint("impostor")
        _, public = self.mint("vendor")
        verify = self.module.openssl_verifier(public)

        self.assertFalse(verify(self.payload, self.sign(other_private, self.payload)))

    def test_garbage_in_place_of_a_signature_does_not_verify(self):
        """It must refuse, not raise: a raising verifier kills the watcher
        before it reaches the origins it has not judged yet."""
        _, public = self.mint("vendor")
        verify = self.module.openssl_verifier(public)

        self.assertFalse(verify(self.payload, b"not a signature"))

    def test_no_key_configured_yields_no_verifier(self):
        """None is what makes the caller report "NOT CHECKED" instead of a pass."""
        self.assertIsNone(self.module.openssl_verifier(self.dir / "absent.pem"))


if __name__ == "__main__":
    unittest.main()
