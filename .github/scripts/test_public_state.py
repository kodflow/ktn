#!/usr/bin/env python3
"""Tests for the boundary between public artefacts and commercial state.

This repository is public and licence state lives on a branch of it, so every
file written there is published. The signed roster HAS to be — a client fetches
it unauthenticated on every cold start — but which accounts are customers, when
each term ends and who negotiated extra seats do not.

Three properties are pinned here:

* the seam works — commercial state follows ``PRIVATE_STATE_DIR`` and the
  public artefacts do not;
* pruning the login-keyed files is safe — it refuses while anything is
  unmigrated, and after it runs the roster is unchanged;
* the audit tells the truth in both directions — it reports exposure when no
  private store is configured, and FAILS when one is configured and commercial
  files are still public.

What is not pinned, because it is not true: that any of this un-publishes
anything. The history of a public branch keeps every file it ever carried.
"""
import base64
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import struct
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).parent
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
WIN = "11111111-2222-4333-8444-555555555555"
HOLDER_ID = "70000001"
PLANTED_TERM = "2033-09-09T00:00:00Z"


def fixture_key(material: bytes = b"ktn test fixture key, not real!!") -> str:
    """A structurally valid ssh-ed25519 line; see test_parse_request.py."""
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", len(material)) + material
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


KEY = fixture_key()


def load(name: str):
    """Import one of the scripts by path; they are scripts, not package modules."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicStateTestCase(unittest.TestCase):
    """A public state directory, and a separate private one to point at."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.public = root / "licenses"
        self.private = root / "private"
        self.public.mkdir()
        self.private.mkdir()
        os.environ["LICENSES_DIR"] = str(self.public)
        for key in ("LICENSES_DIR", "PRIVATE_STATE_DIR"):
            self.addCleanup(os.environ.pop, key, None)
        os.environ.pop("PRIVATE_STATE_DIR", None)
        self.state = load("state")

    def use_private_store(self):
        """Make the cutover, the way a configured workflow would."""
        os.environ["PRIVATE_STATE_DIR"] = str(self.private)

    def write(self, directory, name, payload):
        """Write one state file into whichever half it belongs to."""
        (directory / name).write_text(json.dumps(payload) + "\n")

    def publish_roster_trio(self):
        """The three files a client actually fetches."""
        for name in ("roster.json", "roster.json.sig", "roster.signed.json"):
            (self.public / name).write_text("{}")

    def run_with_argv(self, name, arguments):
        """Invoke a script's main() with the argv a maintainer would give it.

        A zero exit is success — these scripts report through their status —
        so only a non-zero one is allowed to surface as a failure here.
        """
        saved = sys.argv
        sys.argv = [f"{name}.py", *arguments]
        try:
            load(name).main()
        except SystemExit as raised:
            if raised.code:
                raise
        finally:
            sys.argv = saved


class SeamTest(PublicStateTestCase):
    """Commercial state follows PRIVATE_STATE_DIR; public artefacts do not."""

    def test_unset_keeps_everything_where_it_is(self):
        """The default must change nothing: this runs against live state."""
        self.assertEqual(self.state.private_for(self.public), self.public)

    def test_set_moves_the_commercial_half(self):
        """One environment variable, and no code change, is the whole seam."""
        self.use_private_store()

        self.assertEqual(self.state.private_for(self.public), self.private)

    def test_a_binding_is_written_to_the_private_half(self):
        """Which account owns which device is contract data, not an artefact."""
        self.use_private_store()

        self.state.bind_device(self.public, MAC, HOLDER_ID)

        self.assertTrue((self.private / "device-owners.json").is_file())
        self.assertFalse((self.public / "device-owners.json").exists())

    def test_a_term_is_written_to_the_private_half(self):
        """A contract end date per named customer is the clearest case of all."""
        self.use_private_store()

        self.state.set_account_term(self.public, HOLDER_ID, PLANTED_TERM)

        self.assertTrue((self.private / "account-terms.json").is_file())
        self.assertFalse((self.public / "account-terms.json").exists())

    def test_the_roster_is_built_from_a_split_state(self):
        """The seam is worthless if the signer cannot read across it.

        Subjects come from the PUBLIC keys and the CI block from the PRIVATE
        bindings and terms, so this is the one operation that has to span both.
        """
        self.use_private_store()
        self.write(self.private, "accounts.json", {"holder": {"id": HOLDER_ID}})
        self.write(self.private, "device-owners.json", {MAC: HOLDER_ID})
        self.write(self.private, "account-terms.json", {HOLDER_ID: {"expiresAt": PLANTED_TERM}})
        (self.public / f"{MAC}.pub").write_text(KEY + "\n")

        load("build_roster").main()
        roster = json.loads((self.public / "roster.json").read_text())

        self.assertIn(MAC, roster["subjects"])
        self.assertEqual(roster["ci"], {HOLDER_ID: {"exp": PLANTED_TERM}})

    def test_the_roster_is_written_to_the_public_half(self):
        """Clients fetch it unauthenticated; it cannot follow the private path."""
        self.use_private_store()

        load("build_roster").main()

        self.assertTrue((self.public / "roster.json").is_file())
        self.assertFalse((self.private / "roster.json").exists())

    def test_an_empty_state_file_is_not_created(self):
        """The FILE LIST of a public branch is itself a disclosure.

        An empty `account-quotas.json` says nothing about any quota and
        everything about the fact that quotas are tracked per account — and the
        audit would report it as exposure with no content behind it.
        """
        self.state.save(self.public / "account-quotas.json", {})

        self.assertFalse((self.public / "account-quotas.json").exists())


class PruneTest(PublicStateTestCase):
    """Deleting the login-keyed files is the one real reduction available."""

    def legacy(self):
        """An un-migrated branch: login-keyed, with a term and a published key."""
        self.write(self.public, "owners.json", {MAC: "holder"})
        self.write(self.public, "accounts.json", {"holder": {"id": HOLDER_ID}})
        self.write(self.public, "licences.json", {"holder": {"expiresAt": PLANTED_TERM}})
        self.write(self.public, "quotas.json", {"holder": 5})
        (self.public / f"{MAC}.pub").write_text(KEY + "\n")

    def roster_payload(self):
        """The signed bytes, minus the two fields that move with the clock."""
        load("build_roster").main()
        payload = json.loads((self.public / "roster.json").read_text())
        payload.pop("iat")
        payload.pop("exp")
        return payload

    def test_pruning_is_refused_before_the_migration(self):
        """These files are the ONLY copy of what has not been converted yet.

        A term deleted rather than migrated reads afterwards as an account with
        no term — the single path that starts a clock, and a free year.
        """
        self.legacy()

        with self.assertRaises(SystemExit) as raised:
            self.run_with_argv("migrate_state_keys", ["--prune"])

        self.assertNotEqual(raised.exception.code, 0)
        self.assertTrue((self.public / "owners.json").is_file())
        self.assertTrue((self.public / "licences.json").is_file())

    def test_pruning_after_the_migration_removes_the_login_links(self):
        """What is left names no customer: a uuid mapped to a number."""
        self.legacy()
        self.run_with_argv("migrate_state_keys", [])

        self.run_with_argv("migrate_state_keys", ["--prune"])

        for name in ("owners.json", "licences.json", "quotas.json"):
            self.assertFalse((self.public / name).exists(), name)
        self.assertNotIn("holder", (self.public / "device-owners.json").read_text())

    def test_pruning_does_not_change_the_signed_roster(self):
        """The whole point: no client is affected by any of this.

        Asserted on the payload rather than on "it still builds" — a roster
        that builds but publishes a different subject set or a different CI
        block would break clients silently.
        """
        self.legacy()
        before = self.roster_payload()
        self.run_with_argv("migrate_state_keys", [])

        self.run_with_argv("migrate_state_keys", ["--prune"])

        self.assertEqual(self.roster_payload(), before)

    def test_pruning_keeps_the_login_to_id_directory(self):
        """accounts.json cannot go: the write-once guard looks up BY LOGIN.

        Nor can it be hashed — a login is a low-entropy enumerable string, the
        same reason hashing a numeric id fails.
        """
        self.legacy()
        self.run_with_argv("migrate_state_keys", [])

        self.run_with_argv("migrate_state_keys", ["--prune"])

        self.assertTrue((self.public / "accounts.json").is_file())

    def test_pruning_twice_is_harmless(self):
        """A maintainer re-running it must not see a failure.

        run_with_argv re-raises a non-zero exit, so reaching the assertion at
        all is the success half of this; the assertion is that nothing came
        back either.
        """
        self.legacy()
        self.run_with_argv("migrate_state_keys", [])
        self.run_with_argv("migrate_state_keys", ["--prune"])

        self.run_with_argv("migrate_state_keys", ["--prune"])

        self.assertFalse((self.public / "owners.json").exists())

    def test_pruning_is_refused_while_an_account_is_unresolved(self):
        """An unattributable term must not be deleted to tidy the branch up."""
        self.write(self.public, "licences.json", {"legacy-holder": {"expiresAt": PLANTED_TERM}})
        self.write(self.public, "accounts.json", {})

        with self.assertRaises(SystemExit) as raised:
            self.run_with_argv("migrate_state_keys", ["--prune"])

        self.assertNotEqual(raised.exception.code, 0)
        self.assertTrue((self.public / "licences.json").is_file())


class AuditTest(PublicStateTestCase):
    """The audit has to be honest in both directions."""

    def audit(self):
        """Run the audit and return its exit code."""
        try:
            load("audit_public_state").main()
        except SystemExit as raised:
            return raised.code
        return 0

    def test_it_reports_but_does_not_fail_with_no_private_store(self):
        """This is the state of the world today, not a regression.

        Failing every signature over a condition the run cannot fix would stop
        the parc to protest it.
        """
        self.publish_roster_trio()
        self.write(self.public, "accounts.json", {"holder": {"id": HOLDER_ID}})

        self.assertEqual(self.audit(), 0)

    def test_it_fails_when_a_cutover_is_configured_but_incomplete(self):
        """Half-finished reads as finished from the configuration alone."""
        self.publish_roster_trio()
        self.write(self.public, "accounts.json", {"holder": {"id": HOLDER_ID}})
        self.use_private_store()

        self.assertNotEqual(self.audit(), 0)

    def test_it_passes_on_a_completed_cutover(self):
        """So the gate is achievable rather than permanently red."""
        self.publish_roster_trio()
        self.write(self.private, "accounts.json", {"holder": {"id": HOLDER_ID}})
        self.use_private_store()

        self.assertEqual(self.audit(), 0)

    def test_pointing_it_at_the_public_directory_is_not_a_cutover(self):
        """The worst report this script could produce is a false all-clear.

        Setting PRIVATE_STATE_DIR to the directory it already uses configures
        nothing, and treating that as done would silence the warning while
        changing nothing at all.
        """
        self.publish_roster_trio()
        self.write(self.public, "accounts.json", {"holder": {"id": HOLDER_ID}})
        os.environ["PRIVATE_STATE_DIR"] = str(self.public)

        audit = load("audit_public_state")
        self.assertFalse(audit.cutover_configured(self.public))

    def enrol_a_subject(self):
        """One published key, which is what makes a roster something owed."""
        (self.public / "a1b2c3d4-0000-4000-8000-000000000000.pub").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExamplePlaceholderKeyBytes00 x\n"
        )

    def test_a_missing_roster_is_an_error_once_a_subject_is_enrolled(self):
        """Removing contract data must not take the artefacts with it.

        A branch with subjects and no roster stops every one of them within the
        roster's window, which is a far larger failure than the one being
        cleaned up.
        """
        self.enrol_a_subject()
        self.write(self.private, "accounts.json", {"holder": {"id": HOLDER_ID}})
        self.use_private_store()

        self.assertNotEqual(self.audit(), 0)

    def test_a_branch_before_its_first_signature_is_not_an_outage(self):
        """THE ROW THAT MADE BOOTSTRAP IMPOSSIBLE.

        This step runs BEFORE the candidate is staged and published, so exiting
        non-zero over an absent roster killed the job before it could build the
        first one. A fresh `licenses` branch — or one reset after an incident —
        failed identically on every run, for ever, at the moment the scheme is
        needed most.

        A branch with no subjects has nothing to serve, so there is nothing an
        absent roster is denying anyone. state.load already holds this rule one
        file away ("a fresh state branch has none of them, and the schedule has
        to succeed against that") and build_roster.py holds it for zero
        subjects; this is that rule where it was missing.
        """
        self.assertEqual(self.audit(), 0)

    def test_a_half_published_trio_is_an_error_with_no_subjects_at_all(self):
        """Bootstrap tolerance covers absence, never a run that stopped midway.

        Three files, one of them there: no reading of "this branch has not
        published yet" explains that, and the client that fetched the one file
        gets a roster it cannot authenticate.
        """
        (self.public / "roster.json").write_text("{}")

        self.assertNotEqual(self.audit(), 0)

    def test_require_artefacts_drops_the_bootstrap_tolerance(self):
        """What the workflow runs AFTER publishing.

        At that point absence can only mean a publishing run that produced
        nothing, so the tolerance above must not follow it there — otherwise
        the availability check would be unreachable in the one place it is
        certain to be meaningful.
        """
        self.assertNotEqual(self.run_audit_argv(["--require-artefacts"]), 0)

    def test_require_artefacts_passes_once_the_trio_is_published(self):
        """So the post-publication gate is achievable, not permanently red."""
        self.publish_roster_trio()

        self.assertEqual(self.run_audit_argv(["--require-artefacts"]), 0)

    def test_require_artefacts_does_not_repeat_the_disclosure_report(self):
        """It runs in the same job as the full audit, minutes apart.

        Reporting the same exposure paragraphs twice per run trains a
        maintainer to skip both.
        """
        self.publish_roster_trio()
        self.write(self.public, "accounts.json", {"holder": {"id": HOLDER_ID}})

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            code = self.run_audit_argv(["--require-artefacts"])

        self.assertEqual(code, 0)
        self.assertNotIn("accounts.json", captured.getvalue())

    def run_audit_argv(self, arguments):
        """Run the audit with an explicit argv, returning its exit code."""
        saved = sys.argv
        sys.argv = ["audit_public_state.py", *arguments]
        try:
            return self.audit()
        finally:
            sys.argv = saved

    def test_every_commercial_file_has_a_stated_reason(self):
        """A filename in a report tells a maintainer nothing about the stake.

        And a file added to the boundary list without one would be reported as
        exposure with no explanation, or crash the audit.
        """
        audit = load("audit_public_state")

        self.assertEqual(
            sorted(audit.DISCLOSURE), sorted(self.state.COMMERCIAL_FILES)
        )

    def test_the_public_artefacts_are_not_on_the_commercial_list(self):
        """They must stay public; listing one would make the audit demand its removal."""
        audit = load("audit_public_state")

        for name in audit.PUBLIC_ARTEFACTS:
            self.assertNotIn(name, self.state.COMMERCIAL_FILES)

class PrivateMigrationTest(PublicStateTestCase):
    """migrate_private_state.py: the half of the seam that was missing.

    PRIVATE_STATE_DIR redirects every NEW write. Nothing moved what was
    already on the public branch, and that gap had the whole estate behind it:
    a maintainer sets the variable believing the cutover done, the old files
    stay public, audit_public_state.py BECOMES a gate and fails, the signing
    job dies before building a roster, and every client refuses to run within
    RosterLifetime — 24 hours.

    The last test in this class is the one that matters: after the move, the
    audit PASSES. That is what makes "set one variable" true rather than
    catastrophic.
    """

    def migrate(self, *arguments):
        """Run the migration the way the workflow would."""
        self.run_with_argv("migrate_private_state", list(arguments))

    def test_it_does_nothing_without_a_private_store(self):
        """Unset is the state of the world today, not a fault."""
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})

        self.migrate()

        self.assertTrue((self.public / "accounts.json").is_file())

    def test_it_refuses_a_store_pointed_at_the_public_directory(self):
        """Moving files within one directory would delete the only copy."""
        os.environ["PRIVATE_STATE_DIR"] = str(self.public)
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})

        with self.assertRaises(SystemExit) as raised:
            self.migrate()

        self.assertNotEqual(raised.exception.code, 0)
        self.assertTrue((self.public / "accounts.json").is_file())

    def test_it_moves_a_commercial_file_off_the_public_branch(self):
        """The ordinary cutover: present publicly, absent afterwards."""
        self.use_private_store()
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})

        self.migrate()

        self.assertFalse((self.public / "accounts.json").exists())
        self.assertEqual(
            json.loads((self.private / "accounts.json").read_text()),
            {"a-holder": {"id": HOLDER_ID}},
        )

    def test_it_merges_rather_than_replaces(self):
        """The redirect may already have written privately BEFORE this runs.

        This script runs after those writes, not before them, so replacing the
        private copy would lose a device or a term recorded in between —
        a customer's machine going dark for a reason nobody could trace.
        """
        self.use_private_store()
        self.write(self.public, "device-owners.json", {"uuid-public": HOLDER_ID})
        self.write(self.private, "device-owners.json", {"uuid-private": HOLDER_ID})

        self.migrate()

        self.assertEqual(
            json.loads((self.private / "device-owners.json").read_text()),
            {"uuid-public": HOLDER_ID, "uuid-private": HOLDER_ID},
        )

    def test_the_same_key_with_the_same_value_is_not_a_conflict(self):
        """A stale duplicate must not block a cutover."""
        self.use_private_store()
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})
        self.write(self.private, "accounts.json", {"a-holder": {"id": HOLDER_ID}})

        self.migrate()

        self.assertFalse((self.public / "accounts.json").exists())

    def test_the_same_key_with_different_values_refuses_everything(self):
        """Two answers to one question. Choosing either discards a contract.

        The conflict is planted on the LAST commercial file and a clean one on
        the FIRST, which is the ordering that measures the claim. The previous
        version of this test did the opposite — the conflict on
        account-terms.json, which precedes quotas.json in COMMERCIAL_FILES —
        so the refusal happened before anything had moved and the row passed
        on iteration order rather than on a property. With the conflict last,
        a script that checks and moves one file at a time has already moved
        and DELETED the clean one by the time it refuses: exactly the
        half-finished cutover the refusal claims to prevent.
        """
        self.use_private_store()
        #: First in COMMERCIAL_FILES, and clean: it is what a one-pass script
        #: moves before it discovers the conflict below.
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})
        #: Last in COMMERCIAL_FILES, and conflicting.
        self.write(self.public, "unresolved-accounts.json", {"a-holder": "one"})
        self.write(self.private, "unresolved-accounts.json", {"a-holder": "another"})

        with self.assertRaises(SystemExit) as raised:
            self.migrate()

        self.assertNotEqual(raised.exception.code, 0)
        #: THE assertion: the clean file is still public, so nothing moved.
        self.assertTrue(
            (self.public / "accounts.json").is_file(),
            "accounts.json was moved before the conflict was found; the refusal is not total",
        )
        self.assertFalse((self.private / "accounts.json").exists())
        #: And the conflicting private value is untouched.
        self.assertEqual(
            json.loads((self.private / "unresolved-accounts.json").read_text()),
            {"a-holder": "another"},
        )

    def test_a_private_store_inside_the_public_directory_is_refused(self):
        """A different path on a public branch is still public.

        Equality alone did not catch this: state/private/ is not state/, and
        is just as published. Without the containment check the migration
        would move every commercial file from one public location to another
        and report a completed cutover — the worst outcome this mechanism can
        produce, because the configuration then reads as done.
        """
        inside = self.public / "private"
        inside.mkdir()
        os.environ["PRIVATE_STATE_DIR"] = str(inside)
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})

        with self.assertRaises(SystemExit) as raised:
            self.migrate()

        self.assertNotEqual(raised.exception.code, 0)
        self.assertTrue((self.public / "accounts.json").is_file())
        self.assertFalse((inside / "accounts.json").exists())

    def test_check_reports_without_moving_anything(self):
        """A report must never be the thing that performs the change."""
        self.use_private_store()
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})

        self.migrate("--check")

        self.assertTrue((self.public / "accounts.json").is_file())
        self.assertFalse((self.private / "accounts.json").exists())

    def test_a_completed_cutover_is_a_no_op(self):
        """Running it twice must be safe; the schedule may call it every run."""
        self.use_private_store()
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})

        self.migrate()
        self.migrate()

        self.assertFalse((self.public / "accounts.json").exists())
        self.assertTrue((self.private / "accounts.json").is_file())

    def test_the_audit_passes_after_the_migration(self):
        """THE POINT. Before this script, configuring a private store made the
        audit fail for ever, which killed the signing job and the estate with
        it. The cutover has to be ACHIEVABLE, not merely describable.
        """
        self.publish_roster_trio()
        self.write(self.public, "accounts.json", {"a-holder": {"id": HOLDER_ID}})
        self.write(self.public, "account-terms.json", {HOLDER_ID: "2027-01-01T00:00:00Z"})
        self.use_private_store()

        #: Red first, or the row below proves nothing about the migration.
        self.assertNotEqual(self.audit(), 0)

        self.migrate()

        self.assertEqual(self.audit(), 0)

    def audit(self):
        """Run the disclosure audit and return its exit code."""
        saved = sys.argv
        sys.argv = ["audit_public_state.py"]
        try:
            load("audit_public_state").main()
        except SystemExit as raised:
            return raised.code
        finally:
            sys.argv = saved
        return 0

class CommercialWriteRoutingTest(PublicStateTestCase):
    """Every COMMERCIAL_FILES write must go through private_for.

    Two did not. record_owner.py wrote accounts.json and ci-owners.json via
    licenses_dir() — the PUBLIC directory — so they survived the cutover
    entirely: migrate_private_state.py moves them off the public branch, and
    the very next approval puts them back. audit_public_state.py is a gate once
    a store is configured, so it would fail the FOLLOWING run and stop every
    signature. The migration would have appeared to work and then undone itself
    on the next customer.

    These rows assert the DESTINATION rather than the content, because the
    destination is the whole property.
    """

    def test_an_account_id_is_recorded_privately(self):
        """Which login holds which numeric id is the link the prune removes."""
        self.use_private_store()

        load("record_owner").record_account_id("a-holder", HOLDER_ID)

        self.assertTrue((self.private / "accounts.json").is_file())
        self.assertFalse((self.public / "accounts.json").exists())

    def test_a_ci_beneficiary_is_recorded_privately(self):
        """Who negotiated a CI seat, and for whom, is contract data.

        The numeric ids in it are what the roster's `ci` block already
        discloses irreducibly — which is no reason to publish the NAMES beside
        them as well.
        """
        self.use_private_store()

        load("record_owner").record_ci_beneficiary(HOLDER_ID, "some-org", [])

        self.assertTrue((self.private / "ci-owners.json").is_file())
        self.assertFalse((self.public / "ci-owners.json").exists())

    def test_unset_still_writes_where_it_always_did(self):
        """The control, and the compatibility guarantee: with no store
        configured private_for resolves to the public directory, so today's
        behaviour is unchanged and no migration is implied."""
        load("record_owner").record_account_id("a-holder", HOLDER_ID)

        self.assertTrue((self.public / "accounts.json").is_file())

    def test_every_commercial_file_written_here_lands_privately(self):
        """The conservation check, so a NEW commercial write cannot be added
        through the public path without this failing.

        It drives the recorder the way an approval does and then asserts that
        nothing in COMMERCIAL_FILES was left behind publicly — rather than
        listing the two names known to have been wrong, which would say nothing
        about the third.
        """
        self.use_private_store()
        owner = load("record_owner")

        owner.record_account_id("a-holder", HOLDER_ID)
        owner.record_ci_beneficiary(HOLDER_ID, "some-org", [f"ciOwner:{HOLDER_ID}"])
        self.state.bind_device(self.public, MAC, HOLDER_ID)
        self.state.set_account_term(self.public, HOLDER_ID, PLANTED_TERM)

        stranded = [
            name for name in self.state.COMMERCIAL_FILES if (self.public / name).is_file()
        ]
        self.assertEqual(
            stranded, [], f"{stranded} were written to the PUBLIC half despite a configured store"
        )


if __name__ == "__main__":
    unittest.main()
