#!/usr/bin/env python3
"""Tests for the requester / beneficiary split — the silent no.

A CI run does not present the id of the account that asked for the licence. It
presents ``repository_owner_id``, the owner of the repository the run is in. An
issue is opened by a PERSON, so for a customer whose repositories belong to an
organisation those are two different numbers — and the chain used to publish
the requester's id as the CI key. The entitlement existed, matched nothing, and
nothing anywhere said so: a licence that looked issued and a CI job that failed
its licence check with no cause to point at.

These tests pin the three halves of the repair: the claim is read and never
trusted, the state records beneficiary separately from requester, and an
UNRESOLVED claim yields no entitlement rather than an unmatchable one.

They do not pin a POLICY. Whether an organisation's CI seat belongs to the
organisation or to the member who bought the licence is a billing question;
what is pinned is that both answers are expressible and that neither is
guessed.
"""
import base64
import importlib.util
import json
import os
import pathlib
import struct
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).parent
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
WIN = "11111111-2222-4333-8444-555555555555"
# Distinct numbers throughout, so a test cannot pass by confusing the two
# accounts it exists to keep apart. MEMBER opens the issue; ORG owns the
# repositories the CI runs in.
MEMBER_ID = "70000001"
ORG_ID = "70000003"
OTHER_ORG_ID = "70000004"


def fixture_key(material: bytes = b"ktn test fixture key, not real!!") -> str:
    """A structurally valid ssh-ed25519 line; see test_parse_request.py.

    Assembled rather than pasted: parse_request.py reads the RFC 8709 wire
    format, and a full-length literal trips secret scanners on form alone.
    """
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", len(material)) + material
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


KEY = fixture_key()


def load(name: str):
    """Import one of the scripts by path; they are scripts, not package modules."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body(subject: str, ci_owner: str = None) -> str:
    """Render an issue body in the shape the request form produces.

    ``ci_owner=None`` is a form where the optional input was never shown;
    ``ci_owner=""`` is one where it was shown and left blank, which GitHub
    renders as `_No response_`. Both mean "no claim", and both have to.
    """
    text = f"### Subject\n\n{subject}\n\n### Public key\n\n```\n{KEY}\n```\n"
    if ci_owner is not None:
        text += f"\n### CI owner\n\n{ci_owner or '_No response_'}\n"
    return text


class CIBeneficiaryTestCase(unittest.TestCase):
    """Shared fixture: a licence state directory and the three scripts."""

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
        for key in ("LICENSES_DIR", "GITHUB_OUTPUT", "AUTHOR", "AUTHOR_ID", "BODY", "LABELS_JSON"):
            self.addCleanup(os.environ.pop, key, None)

    def ci_owners(self) -> dict:
        """The recorded requester → beneficiary decisions."""
        path = self.licenses / "ci-owners.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def outputs(self) -> dict:
        """The step outputs a script wrote, parsed."""
        pairs = (line.split("=", 1) for line in self.output.read_text().splitlines() if "=" in line)
        return dict(pairs)


class ClaimReadingTest(CIBeneficiaryTestCase):
    """parse_request.py: the claim is read, shape-checked, and never trusted."""

    def request(self, ci_owner=None, author="a-member", author_id=MEMBER_ID):
        """Run the gate the way the workflow does."""
        os.environ["BODY"] = body(MAC, ci_owner)
        os.environ["AUTHOR"] = author
        os.environ["AUTHOR_ID"] = author_id
        load("parse_request").main()

    def test_no_ci_owner_section_makes_no_claim(self):
        """The overwhelmingly common request, which must stay unchanged."""
        self.request(ci_owner=None)

        self.assertEqual(self.outputs()["ci_owner"], "")

    def test_a_blank_ci_owner_makes_no_claim(self):
        """`_No response_` is GitHub's rendering of an untouched optional input.

        Recording that literal as a claimed owner would leave every request
        that merely SAW the field waiting on a maintainer decision.
        """
        self.request(ci_owner="")

        self.assertEqual(self.outputs()["ci_owner"], "")

    def test_naming_yourself_makes_no_claim(self):
        """Asking for what you get by default is not an ambiguity to resolve."""
        self.request(ci_owner="A-Member", author="a-member")

        # Case-insensitively: GitHub logins are, and a capitalised paste of
        # one's own name must not create a decision for a human to take.
        self.assertEqual(self.outputs()["ci_owner"], "")

    def test_naming_an_organisation_is_recorded_as_a_claim(self):
        """The case that used to be invisible is now carried in the output."""
        self.request(ci_owner="some-org", author="a-member")

        self.assertEqual(self.outputs()["ci_owner"], "some-org")

    def test_a_claim_alone_never_becomes_an_entitlement(self):
        """The privilege-escalation shape: claim a big org, be covered by it.

        parse_request.py runs on an attacker-controlled body, so the claim it
        emits has to be inert on its own. This asserts on the STATE, which is
        what build_roster.py reads — an output string that nothing acted on
        would pass a weaker test.
        """
        self.request(ci_owner="some-org", author="a-member")
        load("record_owner").record_ci_beneficiary(MEMBER_ID, "some-org", [])

        self.assertNotIn("beneficiary", self.ci_owners()[MEMBER_ID])

    def test_a_malformed_ci_owner_is_refused_not_ignored(self):
        """Silently dropping it would leave the customer with no CI and no reason."""
        with self.assertRaises(SystemExit) as raised:
            self.request(ci_owner="not a login!!")

        self.assertNotEqual(raised.exception.code, 0)


class BeneficiaryRecordingTest(CIBeneficiaryTestCase):
    """record_owner.py: requester and beneficiary are separate fields."""

    def record(self, account_id=MEMBER_ID, claimed="", labels=()):
        """Record a CI decision the way the approval step does."""
        load("record_owner").record_ci_beneficiary(account_id, claimed, list(labels))

    def test_nothing_is_written_when_nothing_is_claimed_or_decided(self):
        """An individual licence must not grow a file it has no use for."""
        self.record()

        self.assertFalse((self.licenses / "ci-owners.json").exists())

    def test_a_claim_is_recorded_unresolved(self):
        """The middle state is the whole point: recorded, and not yet answered."""
        self.record(claimed="some-org")

        entry = self.ci_owners()[MEMBER_ID]
        self.assertEqual(entry["claimed"], "some-org")
        self.assertNotIn("beneficiary", entry)

    def test_a_maintainer_label_names_the_beneficiary(self):
        """One of the two answers, expressible."""
        self.record(claimed="some-org", labels=[f"ciOwner:{ORG_ID}"])

        entry = self.ci_owners()[MEMBER_ID]
        self.assertEqual(entry["beneficiary"], ORG_ID)
        self.assertEqual(entry["decidedBy"], "maintainer")

    def test_ciowner_self_is_a_decision_not_an_absence(self):
        """The other answer, and it has to be sayable.

        Without it, a maintainer who decides the member keeps the seat has no
        way to record that: the account would sit unresolved forever and lose
        CI it was meant to have.
        """
        self.record(claimed="some-org", labels=["ciOwner:self"])

        self.assertEqual(self.ci_owners()[MEMBER_ID]["beneficiary"], MEMBER_ID)

    def test_a_later_claim_does_not_move_a_decided_beneficiary(self):
        """An editable issue body must not be able to redirect a granted seat.

        This is the takeover shape: get `ciOwner:` decided once, then open a
        second device request claiming a different owner and inherit its CI.
        """
        self.record(claimed="some-org", labels=[f"ciOwner:{ORG_ID}"])

        self.record(claimed="another-org")

        self.assertEqual(self.ci_owners()[MEMBER_ID]["beneficiary"], ORG_ID)

    def test_a_maintainer_can_move_it(self):
        """A licence does get reassigned; a label is how that is said."""
        self.record(claimed="some-org", labels=[f"ciOwner:{ORG_ID}"])

        self.record(labels=[f"ciOwner:{OTHER_ORG_ID}"])

        self.assertEqual(self.ci_owners()[MEMBER_ID]["beneficiary"], OTHER_ORG_ID)

    def test_a_claim_with_no_requester_id_is_reported_not_stored(self):
        """A licence predating accounts.json has no key to record this under."""
        self.record(account_id="", claimed="some-org")

        self.assertFalse((self.licenses / "ci-owners.json").exists())

    def test_the_file_is_keyed_on_the_numeric_requester_never_a_login(self):
        """So this file survives the re-keying of the rest of the state.

        A login-keyed file would have had to be migrated with the others, and
        would carry the same recycled-handle exposure that migration exists to
        close.
        """
        self.record(claimed="some-org")

        self.assertEqual(list(self.ci_owners()), [MEMBER_ID])


class EntitlementKeyingTest(CIBeneficiaryTestCase):
    """build_roster.py: the published key is the beneficiary, or nothing."""

    def setUp(self):
        super().setUp()
        self.module = load("build_roster")

    def enrol(self, uuid=MAC, login="a-member", account_id=MEMBER_ID, term="2027-03-01T00:00:00Z"):
        """Record and publish one device, the way the approval chain does."""
        self.write("owners.json", {uuid: login})
        self.write("accounts.json", {login: {"id": account_id}})
        self.write("licences.json", {login: {"expiresAt": term}})
        (self.licenses / f"{uuid}.pub").write_text(KEY + "\n")

    def write(self, name, payload):
        """Merge a payload into one of the state files."""
        path = self.licenses / name
        current = json.loads(path.read_text()) if path.exists() else {}
        current.update(payload)
        path.write_text(json.dumps(current) + "\n")

    def entitlements(self):
        """Run the entitlement computation the roster publishes."""
        return self.module.ci_entitlements(self.licenses)

    def test_with_no_claim_the_requester_is_the_beneficiary(self):
        """Unchanged for an individual, which is what the id already matched."""
        self.enrol()

        self.assertEqual(list(self.entitlements()), [MEMBER_ID])

    def test_a_decided_organisation_is_the_published_key(self):
        """The repair: the id a runner will actually present.

        Before this, the roster carried MEMBER_ID here while every run in the
        organisation's repositories presented ORG_ID. Asserting the key IS
        ORG_ID is what distinguishes the fix from the bug — asserting merely
        that some entitlement exists passed before it.
        """
        self.enrol()
        self.write("ci-owners.json", {MEMBER_ID: {"beneficiary": ORG_ID, "decidedBy": "maintainer"}})

        self.assertEqual(list(self.entitlements()), [ORG_ID])

    def test_an_unresolved_claim_publishes_no_entitlement(self):
        """A missing entitlement is a question a customer asks.

        A never-matching one is a question nobody knows to ask, which is the
        failure being removed — so the unresolved state must yield nothing at
        all, and in particular must not fall back to the requester.
        """
        self.enrol()
        self.write("ci-owners.json", {MEMBER_ID: {"claimed": "some-org"}})

        self.assertEqual(self.entitlements(), {})

    def test_a_decided_beneficiary_carries_the_licences_own_term(self):
        """CI must expire with the licence that paid for it, not outlive it."""
        self.enrol(term="2027-03-01T00:00:00Z")
        self.write("ci-owners.json", {MEMBER_ID: {"beneficiary": ORG_ID}})

        self.assertEqual(self.entitlements()[ORG_ID], {"exp": "2027-03-01T00:00:00Z"})

    def test_a_non_numeric_beneficiary_is_omitted(self):
        """A hand edit naming a login would republish the original defect.

        A login is not what `repository_owner_id` carries, so publishing it
        would be an entitlement that never matches — exactly what this field
        exists to stop.
        """
        self.enrol()
        self.write("ci-owners.json", {MEMBER_ID: {"beneficiary": "some-org"}})

        self.assertEqual(self.entitlements(), {})

    def test_revoking_the_last_device_withdraws_the_organisations_ci(self):
        """The beneficiary indirection must not outlive the licence behind it."""
        self.enrol()
        self.write("ci-owners.json", {MEMBER_ID: {"beneficiary": ORG_ID}})
        (self.licenses / f"{MAC}.pub").unlink()

        self.assertEqual(self.entitlements(), {})

    def test_two_licences_on_one_organisation_publish_the_later_term(self):
        """Two members of one org, each with their own devices and dates.

        The owner is covered while either licence is live, so the answer must
        not depend on which account the dictionary happened to yield last.
        """
        self.enrol(uuid=MAC, login="a-member", account_id=MEMBER_ID, term="2027-03-01T00:00:00Z")
        self.enrol(uuid=WIN, login="b-member", account_id="70000006", term="2028-06-01T00:00:00Z")
        self.write("ci-owners.json", {
            MEMBER_ID: {"beneficiary": ORG_ID},
            "70000006": {"beneficiary": ORG_ID},
        })

        self.assertEqual(self.entitlements(), {ORG_ID: {"exp": "2028-06-01T00:00:00Z"}})

    def test_an_absent_term_outranks_a_dated_one(self):
        """A missing "exp" is the client's "no expiry", so it is the WIDEST entry.

        Compared as a bare string it would lose to any date and silently narrow
        a licence the client currently treats as unbounded.
        """
        self.enrol(uuid=MAC, login="a-member", account_id=MEMBER_ID, term=None)
        self.enrol(uuid=WIN, login="b-member", account_id="70000006", term="2028-06-01T00:00:00Z")
        self.write("ci-owners.json", {
            MEMBER_ID: {"beneficiary": ORG_ID},
            "70000006": {"beneficiary": ORG_ID},
        })

        self.assertEqual(self.entitlements(), {ORG_ID: {}})


if __name__ == "__main__":
    unittest.main()
