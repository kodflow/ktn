#!/usr/bin/env python3
"""Tests for revoke_subject.py — what a revocation is actually aimed at.

The uuid used to come straight out of the issue body. Applying the label
requires maintainer access, so *whether* a revocation happens was gated; *what
it hits* was not. A body stays editable by whoever opened it, indefinitely, and
the maintainer applying the label sees the label, not a diff of the body.
"""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("revoke_subject.py")
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
WIN = "11111111-2222-4333-8444-555555555555"


def load_script():
    """Import revoke_subject.py by path; it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("revoke_subject", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body(subject: str) -> str:
    """An enrolment issue body, in the shape the request form produces."""
    return f"### Subject\n\n{subject}\n\n### Public key\n\n```\nssh-ed25519 AAAAshape\n```\n"


class RevokeSubjectTest(unittest.TestCase):
    """Resolution order: what we wrote down first, the body only as a fallback."""

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
        for key in ("LICENSES_DIR", "GITHUB_OUTPUT", "ISSUE", "AUTHOR", "BODY"):
            self.addCleanup(os.environ.pop, key, None)
        self.module = load_script()

    def enrol(self, uuid, issue, login="kodflow"):
        """Record an approval the way record_owner.py does."""
        path = self.licenses / "enrolments.json"
        enrolments = json.loads(path.read_text()) if path.exists() else {}
        enrolments[str(issue)] = {"subject": uuid, "login": login, "account": "1", "at": "x"}
        path.write_text(json.dumps(enrolments))
        self.own(uuid, login)

    def own(self, uuid, login="kodflow"):
        """Bind a device to an account, the way the approval chain does."""
        path = self.licenses / "owners.json"
        owners = json.loads(path.read_text()) if path.exists() else {}
        owners[uuid] = login
        path.write_text(json.dumps(owners))

    def run_script(self, issue="", author="kodflow", claimed=MAC):
        """Invoke main() the way the revoke job does."""
        os.environ["ISSUE"] = str(issue)
        os.environ["AUTHOR"] = author
        os.environ["BODY"] = body(claimed)
        self.module.main()

    def resolved(self):
        """The uuid the job will delete."""
        return self.output.read_text().strip().removeprefix("uuid=")

    def test_the_recorded_enrolment_wins_over_the_body(self):
        """THE defect: the aim of a revocation was editable by its subject.

        A device is recorded against its issue at approval time, so the body is
        not consulted at all — an edit cannot point the revocation elsewhere.
        """
        self.enrol(MAC, issue=7)
        self.own(WIN, "someone-else")

        self.run_script(issue=7, claimed=WIN)

        self.assertEqual(self.resolved(), MAC)

    def test_an_unrecorded_issue_falls_back_to_the_body(self):
        """Devices enrolled before enrolments.json existed have no record.

        The fallback is what keeps them revocable at all; the ownership check
        below is what makes it safe.
        """
        self.own(MAC, "kodflow")

        self.run_script(issue=7, claimed=MAC)

        self.assertEqual(self.resolved(), MAC)

    def test_the_body_cannot_name_another_accounts_device(self):
        """An edited body must not become a way to revoke somebody else's key."""
        self.own(WIN, "someone-else")

        with self.assertRaises(SystemExit) as raised:
            self.run_script(issue=7, author="kodflow", claimed=WIN)

        self.assertNotEqual(raised.exception.code, 0)
        self.assertEqual(self.output.read_text(), "")

    def test_a_subject_nobody_owns_is_refused(self):
        """Nothing was ever published for it, so there is nothing to revoke."""
        with self.assertRaises(SystemExit):
            self.run_script(issue=7, claimed=MAC)

    def test_an_authorless_fallback_is_refused(self):
        """Without an author the ownership check cannot be made at all."""
        self.own(MAC, "kodflow")

        with self.assertRaises(SystemExit):
            self.run_script(issue=7, author="", claimed=MAC)

    def test_a_non_uuid_body_is_refused(self):
        """The subject names a file path; anything but a uuid is a lever."""
        self.own(MAC, "kodflow")

        with self.assertRaises(SystemExit):
            self.run_script(issue=7, claimed="../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
