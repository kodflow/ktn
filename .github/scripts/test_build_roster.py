#!/usr/bin/env python3
"""Tests for build_roster.py — the document every client authenticates against.

The roster is the only statement ktn-linter trusts. What it says about CI
entitlement decides who gets a free seat, so the cases that matter here are the
ones where an account should NOT appear.
"""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("build_roster.py")
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
WIN = "11111111-2222-4333-8444-555555555555"
PUBLIC_KEY = "ssh-ed25519 AAAAtestfixturenotarealkeyAA\n"


def load_script():
    """Import build_roster.py by path; it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("build_roster", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RosterTestCase(unittest.TestCase):
    """Shared fixture: a licences directory in the shape the workflow builds."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.licenses = pathlib.Path(self.tmp.name) / "licenses"
        self.licenses.mkdir()
        os.environ["LICENSES_DIR"] = str(self.licenses)
        self.addCleanup(os.environ.pop, "LICENSES_DIR", None)
        self.module = load_script()

    def write(self, name, payload):
        """Write one of the state files."""
        (self.licenses / name).write_text(json.dumps(payload) + "\n")

    def publish(self, uuid):
        """Publish a device's key, which is what makes it active."""
        (self.licenses / f"{uuid}.pub").write_text(PUBLIC_KEY)

    def enrol(self, uuid, login="kodflow", account_id="133899878", term=None, published=True):
        """Record a device the way the approval chain does."""
        owners_path = self.licenses / "owners.json"
        owners = json.loads(owners_path.read_text()) if owners_path.exists() else {}
        owners[uuid] = login
        owners_path.write_text(json.dumps(owners) + "\n")

        if account_id:
            accounts_path = self.licenses / "accounts.json"
            accounts = json.loads(accounts_path.read_text()) if accounts_path.exists() else {}
            accounts[login] = {"id": account_id}
            accounts_path.write_text(json.dumps(accounts) + "\n")

        if term:
            licences_path = self.licenses / "licences.json"
            licences = json.loads(licences_path.read_text()) if licences_path.exists() else {}
            licences[login] = {"expiresAt": term}
            licences_path.write_text(json.dumps(licences) + "\n")

        if published:
            self.publish(uuid)

    def entitlements(self):
        """Run the entitlement computation the roster publishes."""
        return self.module.ci_entitlements(self.licenses)

    def roster(self):
        """Build the roster and return it decoded."""
        self.module.main()
        return json.loads((self.licenses / "roster.json").read_text())


class CIEntitlementTest(RosterTestCase):
    """Who may claim a free CI seat, and — mostly — who may not."""

    def test_an_active_licence_is_entitled(self):
        """The ordinary case: a licence with a published device covers its CI."""
        self.enrol(MAC, term="2027-03-01T00:00:00Z")

        self.assertEqual(self.entitlements(), {"133899878": {"exp": "2027-03-01T00:00:00Z"}})

    def test_the_key_is_the_numeric_id_not_the_login(self):
        """A login can be renamed and a released one reclaimed by someone else.

        Matching a CI run on the name would turn a freed handle into a way in;
        GitHub does not reissue an account id.
        """
        self.enrol(MAC, login="kodflow", account_id="42")

        self.assertIn("42", self.entitlements())
        self.assertNotIn("kodflow", self.entitlements())

    def test_an_account_with_no_active_device_is_not_entitled(self):
        """A licence nobody uses should not keep handing out free CI.

        Revocation removes the published key, so this needs no separate
        revocation path — CI stops with the last device.
        """
        self.enrol(MAC, term="2027-03-01T00:00:00Z")
        (self.licenses / f"{MAC}.pub").unlink()

        self.assertEqual(self.entitlements(), {})

    def test_an_account_with_no_recorded_id_is_not_entitled(self):
        """Every licence issued before accounts.json existed looks like this.

        They keep working as devices; they simply get no CI seat until their
        next approval records an id. Inventing one is not an option.
        """
        self.enrol(MAC, account_id="", term="2027-03-01T00:00:00Z")

        self.assertEqual(self.entitlements(), {})

    def test_one_account_does_not_entitle_another(self):
        """Entitlement follows the licence, and a licence is one account."""
        self.enrol(MAC, login="kodflow", account_id="1")
        self.enrol(WIN, login="someone-else", account_id="2", published=False)

        self.assertEqual(list(self.entitlements()), ["1"])

    def test_the_term_is_the_licence_term(self):
        """CI expires exactly when the devices do — one licence, one date."""
        self.enrol(MAC, term="2028-12-31T00:00:00Z")

        self.assertEqual(self.entitlements()["133899878"]["exp"], "2028-12-31T00:00:00Z")

    def test_an_account_with_no_term_is_entitled_without_one(self):
        """A missing term reads as "none recorded", never as "already expired".

        The client treats an absent expiry the same way, so a licence that
        predates licences.json must not lose its CI over the gap.
        """
        self.enrol(MAC, term=None)

        self.assertEqual(self.entitlements(), {"133899878": {}})


class RosterDocumentTest(RosterTestCase):
    """What ends up in the signed bytes."""

    def test_the_ci_block_is_omitted_when_empty(self):
        """An absent "ci" and an empty one mean the same thing to the client.

        Leaving the key out keeps the signed bytes identical to what older
        rosters looked like.
        """
        self.enrol(MAC, account_id="")

        self.assertNotIn("ci", self.roster())

    def test_the_ci_block_is_published_when_present(self):
        self.enrol(MAC, term="2027-03-01T00:00:00Z")

        roster = self.roster()

        self.assertEqual(roster["ci"], {"133899878": {"exp": "2027-03-01T00:00:00Z"}})

    def test_subjects_are_unaffected_by_the_ci_block(self):
        """The device path must not change shape because CI gained one."""
        self.enrol(MAC, term="2027-03-01T00:00:00Z")

        roster = self.roster()

        self.assertIn(MAC, roster["subjects"])
        self.assertIn("fp", roster["subjects"][MAC])

    def test_an_empty_state_directory_still_builds(self):
        """The schedule must succeed with zero subjects, not crash."""
        roster = self.roster()

        self.assertEqual(roster["subjects"], {})
        self.assertNotIn("ci", roster)


if __name__ == "__main__":
    unittest.main()
