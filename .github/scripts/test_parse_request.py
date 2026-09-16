#!/usr/bin/env python3
"""Tests for parse_request.py — what the approval chain will and will not accept.

This is the script that decides whether a key reaches the roster. Everything it
reads comes from an issue body, which anyone can write, so the negative cases
matter more than the positive one.
"""
import base64
import importlib.util
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("parse_request.py")
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
WIN = "11111111-2222-4333-8444-555555555555"
BOX = "22222222-3333-4444-8555-666666666666"
FOURTH = "33333333-4444-4555-8666-777777777777"
# The numeric id is the identity; the login is a label. Distinct per account so
# a test cannot pass by accident when the two are confused.
IDS = {"kodflow": "133899878", "someone-else": "424242", "other": "777777"}


def fixture_key(material: bytes = b"ktn test fixture key, not real!!", algorithm: bytes = b"ssh-ed25519") -> str:
    """A structurally valid ssh-ed25519 line, assembled rather than pasted.

    parse_request.py reads the wire format now, so a blob that is merely
    SHAPED like a key is refused — and a full-length `ssh-ed25519 AAAA…`
    literal trips secret scanners on FORM alone, whatever it decodes to.
    Building the bytes here keeps both properties at once: real RFC 8709
    structure, and no line anywhere in this repository shaped like a key.
    """
    blob = (
        struct.pack(">I", len(algorithm))
        + algorithm
        + struct.pack(">I", len(material))
        + material
    )
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


KEY = fixture_key()


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
        for key in ("LICENSES_DIR", "GITHUB_OUTPUT", "AUTHOR_ID"):
            self.addCleanup(os.environ.pop, key, None)
        self.module = load_script()

    def enrol(self, uuid: str, account: str, published: bool = True, account_id: str = None):
        """Record a device as owned by account, and optionally publish its key."""
        owners_path = self.licenses / "owners.json"
        owners = json.loads(owners_path.read_text()) if owners_path.exists() else {}
        owners[uuid] = account
        owners_path.write_text(json.dumps(owners))

        # The approval chain records the account's numeric id alongside the
        # binding; a fixture without it is a licence that predates accounts.json.
        recorded = IDS.get(account, "1") if account_id is None else account_id
        if recorded:
            accounts_path = self.licenses / "accounts.json"
            accounts = json.loads(accounts_path.read_text()) if accounts_path.exists() else {}
            accounts[account] = {"id": recorded}
            accounts_path.write_text(json.dumps(accounts))

        if published:
            (self.licenses / f"{uuid}.pub").write_text(KEY + "\n")

    def run_script(self, subject: str, author: str, key: str = KEY, author_id: str = None):
        """Invoke main() the way the workflow does."""
        os.environ["BODY"] = body(subject, key)
        os.environ["AUTHOR"] = author
        os.environ["AUTHOR_ID"] = IDS.get(author, "1") if author_id is None else author_id
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

    def test_a_boolean_quota_is_refused(self):
        """bool is a subclass of int in Python.

        `true` would otherwise pass the isinstance check and then behave as 1,
        silently cutting an account down to a single device with no message
        saying why.
        """
        for value in (True, False):
            with self.subTest(quota=value):
                self.setUp()
                (self.licenses / "quotas.json").write_text(json.dumps({"kodflow": value}))

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

    # The line and the bytes are two different gates. Only the second one
    # decides whether build_roster.py can fingerprint what was committed.

    def test_a_key_that_is_only_shaped_like_one_is_refused(self):
        """THE defect: the shape was the whole check.

        Committed, this blob reaches build_roster.py, which fingerprints
        whatever `base64.b64decode` salvaged from it — a subject no device will
        ever match, published without complaint.
        """
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "kodflow", key="ssh-ed25519 AAAAtestfixturenotarealkeyAA")

    def test_a_key_that_will_not_decode_is_refused(self):
        """The louder half: bad padding raises, and the raise took the roster.

        Not one licence — every signature after it, until someone found the
        file. A refusal here costs one approval and names the file.
        """
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "kodflow", key="ssh-ed25519 AAAAB3NzaC1lZDI1NTE5AAAAIA")

    def test_a_blob_declaring_another_algorithm_is_refused(self):
        """The prefix a human reads and the algorithm a verifier reads must agree."""
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "kodflow", key=fixture_key(algorithm=b"ssh-rsa"))

    def test_a_blob_with_the_wrong_key_length_is_refused(self):
        """ed25519 keys are 32 bytes. Anything else is not one."""
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "kodflow", key=fixture_key(material=b"too short"))

    def test_a_blob_with_trailing_bytes_is_refused(self):
        """Room after the key material is room for something to hide in."""
        padded = base64.b64decode(fixture_key().split()[1]) + b"extra"
        with self.assertRaises(SystemExit):
            self.run_script(
                MAC, "kodflow", key="ssh-ed25519 " + base64.b64encode(padded).decode()
            )

    def test_a_lying_length_prefix_is_refused_not_allocated(self):
        """The length prefix is attacker-supplied; it is checked before it is used."""
        blob = struct.pack(">I", 0xFFFFFFFF) + b"ssh-ed25519"
        with self.assertRaises(SystemExit):
            self.run_script(
                MAC, "kodflow", key="ssh-ed25519 " + base64.b64encode(blob).decode()
            )

    def test_a_key_with_a_comment_is_accepted(self):
        """`ktn-linter license create` prints one; refusing it refuses every request."""
        self.run_script(MAC, "kodflow", key=KEY + " ktn-linter licence " + MAC)

        self.assertIn(f"uuid={MAC}", self.output.read_text())

    def test_an_anonymous_request_is_refused(self):
        """The author IS the identity a subject gets bound to."""
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "")

    # A login is a label. GitHub releases them; it does not reissue ids.

    def test_a_recycled_login_cannot_inherit_the_licence(self):
        """THE defect: everything is keyed on a name its holder can give up.

        Approved under the old holder's login, this request would rotate one of
        that account's devices to a key the new holder generated, inherit its
        term and its quota, and — because the id was written every time — move
        the CI seat to the new account's id as well.
        """
        self.enrol(MAC, "kodflow")

        with self.assertRaises(SystemExit) as raised:
            self.run_script(WIN, "kodflow", author_id="999999")

        self.assertNotEqual(raised.exception.code, 0)
        self.assertNotIn(f"uuid={WIN}", self.output.read_text())

    def test_it_cannot_take_over_a_device_either(self):
        """The ownership check compares logins, so only the id can catch this."""
        self.enrol(MAC, "kodflow")

        with self.assertRaises(SystemExit):
            self.run_script(MAC, "kodflow", author_id="999999")

    def test_the_same_account_under_a_renamed_login_is_accepted(self):
        """A rename keeps the id. Refusing that would lock out a real customer."""
        self.enrol(MAC, "kodflow")

        self.run_script(WIN, "kodflow")

        self.assertIn(f"uuid={WIN}", self.output.read_text())

    def test_a_request_with_no_account_id_is_refused(self):
        """The id is how CI entitlement is matched; an approval without one is blind."""
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "kodflow", author_id="")

    def test_a_non_numeric_account_id_is_refused(self):
        """A login smuggled in as an id would put us back where we started."""
        with self.assertRaises(SystemExit):
            self.run_script(MAC, "kodflow", author_id="kodflow")

    def test_a_licence_that_predates_accounts_json_is_not_attributed(self):
        """The residual the id guard could not close, now closed by refusing.

        This test used to assert the OPPOSITE — that such a request is served —
        and that was the hole. An account with no recorded id has nothing for
        the write-once guard to compare, so whoever registers its released
        login arrives looking brand new: no devices against the quota and no
        term on record, which is the one path that starts a fresh year. The
        previous customer's seats and their remaining year, handed over
        silently.

        A rename of the original customer has exactly the same shape here. The
        fact that separates them is in a billing record, so this stops and says
        so instead of picking one.
        """
        self.enrol(MAC, "kodflow", account_id="")

        with self.assertRaises(SystemExit) as raised:
            self.run_script(WIN, "kodflow")

        self.assertNotEqual(raised.exception.code, 0)
        self.assertNotIn(f"uuid={WIN}", self.output.read_text())

    def test_resolving_the_account_by_hand_lets_the_request_through(self):
        """The refusal has to be a question, not a dead end.

        Recording the id is the whole resolution, and it is what
        `migrate_state_keys.py --resolve` writes. Without this the previous
        test would be satisfied by a permanent lockout.
        """
        self.enrol(MAC, "kodflow", account_id="")
        # What --resolve does: the numeric id of the account that bought it.
        (self.licenses / "accounts.json").write_text(
            json.dumps({"kodflow": {"id": IDS["kodflow"]}})
        )
        # And what the migration then does with the binding it could not key.
        (self.licenses / "device-owners.json").write_text(
            json.dumps({MAC: IDS["kodflow"]})
        )

        self.run_script(WIN, "kodflow")

        self.assertIn(f"uuid={WIN}", self.output.read_text())


if __name__ == "__main__":
    unittest.main()
