#!/usr/bin/env python3
"""Tests for record_expiry.py — the script that decides how long a licence runs.

This chain carries the billing decision and had no tests at all, which is how
`license update` came to hand out a fresh 365-day term on every rotation:
rotating once a year was an indefinite free subscription, and no lane could
have noticed.

Stdlib unittest and importlib on purpose: these scripts run on a bare GitHub
runner with no requirements file, and a test suite that needs installing is a
test suite that gets skipped.
"""
import datetime
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("record_expiry.py")
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
WIN = "11111111-2222-4333-8444-555555555555"
BOX = "22222222-3333-4444-8555-666666666666"


def load_script():
    """Import record_expiry.py by path.

    It is a script rather than a package module, so there is no import path to
    name it by; loading it explicitly is what lets the real code be exercised
    instead of a copy that can drift from it.
    """
    spec = importlib.util.spec_from_file_location("record_expiry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TermTestCase(unittest.TestCase):
    """Shared fixture: a licences/ directory and the bindings the workflow writes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.licenses = pathlib.Path(self.tmp.name) / "licenses"
        self.licenses.mkdir()
        os.environ["LICENSES_DIR"] = str(self.licenses)
        self.addCleanup(os.environ.pop, "LICENSES_DIR", None)
        self.module = load_script()

    def bind(self, uuid, account="kodflow", published=True, term=None):
        """Record a device the way record_owner.py does, optionally published."""
        path = self.licenses / "owners.json"
        owners = json.loads(path.read_text()) if path.exists() else {}
        owners[uuid] = account
        path.write_text(json.dumps(owners))
        if published:
            (self.licenses / f"{uuid}.pub").write_text("ssh-ed25519 AAAA test\n")
        if term:
            (self.licenses / f"{uuid}.meta.json").write_text(
                json.dumps({"expiresAt": term}) + "\n"
            )

    def revoke(self, uuid):
        """Remove a device the way the revoke job does: key and sidecar go.

        owners.json is deliberately left alone — that is the anti-squat rule.
        """
        (self.licenses / f"{uuid}.pub").unlink(missing_ok=True)
        (self.licenses / f"{uuid}.meta.json").unlink(missing_ok=True)

    def run_script(self, uuid, labels=()):
        """Invoke main() the way the workflow does: argv plus LABELS_JSON."""
        os.environ["LABELS_JSON"] = json.dumps(list(labels))
        argv = sys.argv
        sys.argv = ["record_expiry.py", uuid]
        try:
            self.module.main()
        finally:
            sys.argv = argv

    def recorded(self, uuid):
        """The term currently stamped on a device, or None."""
        path = self.licenses / f"{uuid}.meta.json"
        return json.loads(path.read_text())["expiresAt"] if path.exists() else None

    def licence_of(self, account="kodflow"):
        """The term recorded for the account itself, or None."""
        path = self.licenses / "licences.json"
        if not path.exists():
            return None
        return json.loads(path.read_text()).get(account, {}).get("expiresAt")


class FirstPublishTest(TermTestCase):
    """Starting a clock is the one thing only a first publish may do."""

    def test_a_first_device_starts_a_one_year_term(self):
        self.bind(MAC)

        self.run_script(MAC)

        recorded = datetime.datetime.fromisoformat(self.recorded(MAC).replace("Z", "+00:00"))
        expected = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
        # A minute of slack: the script reads its own clock.
        self.assertLess(abs((recorded - expected).total_seconds()), 60)

    def test_the_term_is_recorded_against_the_account(self):
        """The device sidecar is a copy; licences.json is the record."""
        self.bind(MAC)

        self.run_script(MAC)

        self.assertEqual(self.licence_of(), self.recorded(MAC))

    def test_an_unbound_subject_is_refused(self):
        """Guessing an account would stamp a term onto the wrong licence."""
        with self.assertRaises(SystemExit):
            self.run_script(MAC)


class RotationTest(TermTestCase):
    """Rotating a key and paying for another year are different acts."""

    def test_rotation_does_not_renew_the_term(self):
        """The defect this suite exists for.

        The term is planted rather than produced by a first run: two runs a few
        milliseconds apart compute the SAME default date, so comparing them
        would pass whether or not the rule exists. A date that could not have
        been computed now is what makes this test discriminate.
        """
        self.bind(MAC, term="2026-11-30T00:00:00Z")

        self.run_script(MAC)

        self.assertEqual(self.recorded(MAC), "2026-11-30T00:00:00Z")


class MultiDeviceTest(TermTestCase):
    """One licence, several machines, exactly one expiry date."""

    def test_a_second_device_inherits_the_licence_term(self):
        """A machine enrolled six months in expires with the rest.

        Its own year would make renewal several acts at several dates, and the
        late device would outlive the licence that authorised it.
        """
        self.bind(MAC, term="2027-03-01T00:00:00Z")
        self.bind(WIN)

        self.run_script(WIN)

        self.assertEqual(self.recorded(WIN), "2027-03-01T00:00:00Z")

    def test_a_device_does_not_inherit_from_another_account(self):
        """Terms follow the licence, and a licence is one account."""
        self.bind(MAC, account="someone-else", term="2099-01-01T00:00:00Z")
        self.bind(WIN)

        self.run_script(WIN)

        self.assertNotEqual(self.recorded(WIN), "2099-01-01T00:00:00Z")

    def test_the_earliest_sibling_term_wins_on_migration(self):
        """A device must never outlive its licence, so disagreement resolves down."""
        self.bind(MAC, term="2028-01-01T00:00:00Z")
        self.bind(BOX, term="2027-01-01T00:00:00Z")
        self.bind(WIN)

        self.run_script(WIN)

        self.assertEqual(self.recorded(WIN), "2027-01-01T00:00:00Z")

    def test_a_revoked_sibling_does_not_dictate_the_term(self):
        """A revoked device keeps its binding but leaves the licence."""
        self.bind(MAC, published=False, term="2027-03-01T00:00:00Z")
        self.bind(WIN)

        self.run_script(WIN)

        self.assertNotEqual(self.recorded(WIN), "2027-03-01T00:00:00Z")


class RevocationTest(TermTestCase):
    """Revoking devices must not hand back a fresh year."""

    def test_revoking_every_device_does_not_reset_the_clock(self):
        """The free-renewal path a device-derived term left open.

        Revocation deletes the key AND the sidecar, so a term recovered from
        surviving devices disappears with the last one: revoke your only
        device, enrol another, and the year starts again. licences.json is what
        closes that.
        """
        # A planted term, not one this run could have computed: a fresh
        # default and an inherited one land in the same second, so comparing
        # two computed dates would pass with or without the rule.
        self.bind(MAC, term="2027-02-02T00:00:00Z")
        self.run_script(MAC)
        self.revoke(MAC)

        self.bind(WIN)
        self.run_script(WIN)

        self.assertEqual(self.recorded(WIN), "2027-02-02T00:00:00Z")

    def test_re_enrolling_the_same_device_does_not_reset_the_clock(self):
        """Revoke-then-re-add is the same trick with one machine."""
        self.bind(MAC, term="2027-02-02T00:00:00Z")
        self.run_script(MAC)
        self.revoke(MAC)
        (self.licenses / f"{MAC}.pub").write_text("ssh-ed25519 AAAA test\n")

        self.run_script(MAC)

        self.assertEqual(self.recorded(MAC), "2027-02-02T00:00:00Z")


class RenewalTest(TermTestCase):
    """Only a maintainer moves a term, and it moves for the whole licence."""

    def test_an_expireat_label_sets_the_term_on_a_first_publish(self):
        self.bind(MAC)

        self.run_script(MAC, [{"name": "expireAt:2028-06-30"}])

        self.assertEqual(self.recorded(MAC), "2028-06-30T00:00:00Z")

    def test_a_renewal_restamps_every_active_device(self):
        """One licence, one date. Renewing one device would split the licence."""
        self.bind(MAC, term="2027-01-01T00:00:00Z")
        self.bind(WIN, term="2027-01-01T00:00:00Z")

        self.run_script(WIN, [{"name": "expireAt:2031-05-05"}])

        self.assertEqual(self.recorded(MAC), "2031-05-05T00:00:00Z")
        self.assertEqual(self.recorded(WIN), "2031-05-05T00:00:00Z")
        self.assertEqual(self.licence_of(), "2031-05-05T00:00:00Z")

    def test_a_renewal_does_not_restamp_a_revoked_device(self):
        """A revoked machine is not part of the licence any more."""
        self.bind(MAC, published=False, term="2027-01-01T00:00:00Z")
        self.bind(WIN, term="2027-01-01T00:00:00Z")

        self.run_script(WIN, [{"name": "expireAt:2031-05-05"}])

        self.assertEqual(self.recorded(MAC), "2027-01-01T00:00:00Z")

    def test_a_renewal_does_not_touch_another_account(self):
        self.bind(MAC, account="someone-else", term="2027-01-01T00:00:00Z")
        self.bind(WIN)

        self.run_script(WIN, [{"name": "expireAt:2031-05-05"}])

        self.assertEqual(self.recorded(MAC), "2027-01-01T00:00:00Z")

    def test_a_malformed_expireat_label_fails_loudly(self):
        """Falling back to the default would grant a year nobody asked for."""
        self.bind(MAC)

        with self.assertRaises(SystemExit) as raised:
            self.run_script(MAC, [{"name": "expireAt:2026-13-45"}])

        self.assertNotEqual(raised.exception.code, 0)

    def test_unrelated_labels_are_ignored(self):
        self.bind(MAC)

        self.run_script(MAC, [{"name": "license:approved"}, {"name": "bug"}])

        self.assertIsNotNone(self.recorded(MAC))


class MalformedStateTest(TermTestCase):
    """A corrupt term must stop the run, never propagate to the roster."""

    def test_an_unparseable_recorded_term_is_refused(self):
        """Copied verbatim it would read as 'no expiry' — an unlimited licence."""
        self.bind(MAC, term="not-a-date")
        self.bind(WIN)

        with self.assertRaises(SystemExit):
            self.run_script(WIN)

    def test_a_non_string_recorded_term_is_refused(self):
        """min() over mixed types raises; failing on purpose beats crashing."""
        self.bind(MAC)
        (self.licenses / f"{MAC}.meta.json").write_text(json.dumps({"expiresAt": 1234}))
        self.bind(WIN)

        with self.assertRaises(SystemExit):
            self.run_script(WIN)

    def test_a_timezone_naive_recorded_term_is_refused(self):
        """An offset-less date cannot be ordered against the aware ones."""
        self.bind(MAC, term="2027-01-01T00:00:00")
        self.bind(WIN)

        with self.assertRaises(SystemExit):
            self.run_script(WIN)

    def test_a_falsey_recorded_term_is_corruption_not_absence(self):
        """A recorded null/0/"" must stop the run, never read as "no term".

        Reading it as absence sends the account down the brand-new-licence
        path and hands it a fresh year — the free-renewal outcome licences.json
        exists to prevent, reachable by nothing more than a bad edit.
        """
        for falsey in (None, 0, "", False):
            with self.subTest(recorded=falsey):
                self.setUp()
                self.bind(MAC)
                (self.licenses / "licences.json").write_text(
                    json.dumps({"kodflow": {"expiresAt": falsey}}) + "\n"
                )

                with self.assertRaises(SystemExit):
                    self.run_script(MAC)

    def test_a_falsey_sidecar_term_is_corruption_not_absence(self):
        """Same reasoning on the device sidecar, which migration reads."""
        self.bind(MAC)
        (self.licenses / f"{MAC}.meta.json").write_text(json.dumps({"expiresAt": None}) + "\n")
        self.bind(WIN)

        with self.assertRaises(SystemExit):
            self.run_script(WIN)

    def test_a_corrupt_sidecar_does_not_silently_renew(self):
        """Treating unreadable state as absent would turn corruption into a renewal."""
        self.bind(MAC)
        (self.licenses / f"{MAC}.meta.json").write_text("{not json")

        with self.assertRaises(Exception) as raised:
            self.run_script(MAC)

        self.assertNotIsInstance(raised.exception, AssertionError)


if __name__ == "__main__":
    unittest.main()
