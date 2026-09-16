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
import importlib.util
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

    def test_a_missing_roster_is_an_error_even_with_nothing_exposed(self):
        """Removing contract data must not take the artefacts with it.

        A branch with no roster stops every client within the roster's window,
        which is a far larger failure than the one being cleaned up.
        """
        self.write(self.private, "accounts.json", {"holder": {"id": HOLDER_ID}})
        self.use_private_store()

        self.assertNotEqual(self.audit(), 0)

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


if __name__ == "__main__":
    unittest.main()
