#!/usr/bin/env python3
"""Tests for parse_request.py — what the approval chain will and will not accept.

This is the script that decides whether a key reaches the roster. Everything it
reads comes from an issue body, which anyone can write, so the negative cases
matter more than the positive one.
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("parse_request.py")
# Deliberately far too short to be a key. parse_request only checks the SHAPE
# of the line — algorithm prefix plus base64 — so a fixture does not need real
# key material, and it should not have any: a full-length ed25519 line trips
# secret scanners on FORM alone, whatever it decodes to, and a test is not
# worth sending someone to check whether the thing that was flagged mattered.
KEY = "ssh-ed25519 AAAAtestfixturenotarealkey"
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
WIN = "11111111-2222-4333-8444-555555555555"
BOX = "22222222-3333-4444-8555-666666666666"
FOURTH = "33333333-4444-4555-8666-777777777777"


def load_script():
    """Import parse_request.py by path; it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("parse_request", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body(subject: str, key: str = KEY) -> str:
    """Render an issue body in the shape the request form produces."""
    return f"### Subject\n\n{subject}\n\n### Public key\n\n```\n{key}\n```\n"


class ParseRequestTest(unittest.TestCase):
    """Covers ownership, the device quota, and the shapes that must be refused."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.licenses = root / "licenses"
        self.licenses.mkdir()
        self.output = root / "github_output"
        self.output.touch()
        os.environ["LICENSES_DIR"] = str(self.licenses)
        os.environ["GITHUB_OUTPUT"] = str(self.output)
        self.addCleanup(os.environ.pop, "LICENSES_DIR", None)
        self.addCleanup(os.environ.pop, "GITHUB_OUTPUT", None)
        self.module = load_script()

    def enrol(self, uuid: str, account: str, published: bool = True):
        """Record a device as owned by account, and optionally publish its key."""
        owners_path = self.licenses / "owners.json"
        owners = json.loads(owners_path.read_text()) if owners_path.exists() else {}
        owners[uuid] = account
        owners_path.write_text(json.dumps(owners))
        if published:
            (self.licenses / f"{uuid}.pub").write_text(KEY + "\n")

    def run_script(self, subject: str, author: str, key: str = KEY):
        """Invoke main() the way the workflow does."""
        os.environ["BODY"] = body(subject, key)
        os.environ["AUTHOR"] = author
        self.module.main()

    def test_a_first_device_is_accepted(self):
        """The ordinary first enrolment, with nothing on record yet."""
        self.run_script(MAC, "kodflow")

        self.assertIn(f"uuid={MAC}", self.output.read_text())

    def test_a_second_and_third_device_are_accepted(self):
        """The whole point: one licence, several machines, no key copying."""
        self.enrol(MAC, "kodflow")

        self.run_script(WIN, "kodflow")
        self.enrol(WIN, "kodflow")
        self.run_script(BOX, "kodflow")

        self.assertIn(f"uuid={BOX}", self.output.read_text())

    def test_a_fourth_device_is_refused(self):
        """The quota has to actually bind, or it is decoration."""
        for uuid in (MAC, WIN, BOX):
            self.enrol(uuid, "kodflow")

        with self.assertRaises(SystemExit) as raised:
            self.run_script(FOURTH, "kodflow")

        self.assertNotEqual(raised.exception.code, 0)
        # Asserting only "refused" would also pass under the old
        # one-subject-per-account rule, which refused the SECOND device. The
        # count is what distinguishes a quota from a ban.
        self.assertNotIn(f"uuid={FOURTH}", self.output.read_text())

    def test_rotating_a_published_device_does_not_consume_a_seat(self):
        """A rotation replaces a key in a seat the device already occupies.

        Counting it as new would make the third device unrotatable — the one
        state where rotation matters most, since a compromised key is exactly
        when you cannot afford to be told the licence is full.
        """
        for uuid in (MAC, WIN, BOX):
            self.enrol(uuid, "kodflow")

        self.run_script(BOX, "kodflow")

        self.assertIn(f"uuid={BOX}", self.output.read_text())

    def test_a_revoked_device_frees_its_seat(self):
        """Revocation removes the key but keeps the owner binding, on purpose.

        Counting owner bindings rather than published keys would mean three
        revocations killed the licence permanently, with no message saying so.
        """
        for uuid in (MAC, WIN, BOX):
            self.enrol(uuid, "kodflow")
        # Revocation deletes the key; owners.json keeps the binding so the
        # identity cannot be squatted later.
        (self.licenses / f"{BOX}.pub").unlink()

        self.run_script(FOURTH, "kodflow")

        self.assertIn(f"uuid={FOURTH}", self.output.read_text())

    def test_another_accounts_device_cannot_be_taken_over(self):
        """Ownership is what stops a takeover; the quota must not weaken it."""
        self.enrol(MAC, "someone-else")

        with self.assertRaises(SystemExit) as raised:
            self.run_script(MAC, "kodflow")

        self.assertNotEqual(raised.exception.code, 0)

    def test_quotas_json_widens_the_limit(self):
        """A team licence must be widenable without editing the script."""
        for uuid in (MAC, WIN, BOX):
            self.enrol(uuid, "kodflow")
        (self.licenses / "quotas.json").write_text(json.dumps({"kodflow": 5}))

        self.run_script(FOURTH, "kodflow")

        self.assertIn(f"uuid={FOURTH}", self.output.read_text())

    def test_quotas_json_only_widens_the_named_account(self):
        """An override for one account must not raise everybody's quota."""
        for uuid in (MAC, WIN, BOX):
            self.enrol(uuid, "other")
        (self.licenses / "quotas.json").write_text(json.dumps({"kodflow": 5}))

        with self.assertRaises(SystemExit):
            self.run_script(FOURTH, "other")

    def test_an_invalid_quota_fails_loudly(self):
        """A typo must not silently widen a licence to something unbounded."""
        (self.licenses / "quotas.json").write_text(json.dumps({"kodflow": 0}))

        with self.assertRaises(SystemExit) as raised:
            self.run_script(MAC, "kodflow")

        self.assertNotEqual(raised.exception.code, 0)

    def test_a_non_uuid_subject_is_refused(self):
        """The subject names a file path; anything but a canonical uuid is a lever."""
        with self.assertRaises(SystemExit):
            self.run_script("../../etc/passwd", "kodflow")

    def test_a_non_ed25519_key_is_refused(self):
        """Narrowing the accepted algorithm narrows what the verifier handles."""
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "kodflow", key="ssh-rsa AAAAB3NzaC1yc2EAAAA")

    def test_an_anonymous_request_is_refused(self):
        """The author IS the identity a subject gets bound to."""
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "")


if __name__ == "__main__":
    unittest.main()
