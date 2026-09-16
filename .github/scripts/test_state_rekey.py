#!/usr/bin/env python3
"""Tests for licence state keyed by numeric account id rather than by login.

A GitHub login is released when an account is renamed or deleted, and can then
be registered by somebody else. Every state file keyed on one therefore handed
its contents to whoever picked the handle up: the remaining term, the enrolled
devices, the negotiated seat count. The numeric id is never reissued.

These run the REAL approval chain — parse_request, record_owner, record_expiry,
build_roster, in the order the workflow runs them — because the properties that
matter are lifecycle properties. A rename must not start a new year; a released
handle must not inherit one; and the migration that converts live state must be
safe to run twice.

The dates here are deliberately far-future and specific. A term the code could
compute on its own — "now plus a year" — would pass whether or not the rule it
claims to pin exists, which is how two tests in this repository once compared
two timestamps taken in the same second and asserted nothing.
"""
import base64
import importlib.util
import json
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).parent
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
WIN = "11111111-2222-4333-8444-555555555555"
BOX = "22222222-3333-4444-8555-666666666666"
FOURTH = "33333333-4444-4555-8666-999999999999"
# The customer, and the stranger who later registers their released login.
HOLDER_ID = "70000001"
STRANGER_ID = "70000002"
# A term no clock in this chain could arrive at by itself.
PLANTED_TERM = "2033-09-09T00:00:00Z"


def fixture_key(material: bytes = b"ktn test fixture key, not real!!") -> str:
    """A structurally valid ssh-ed25519 line; see test_parse_request.py."""
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", len(material)) + material
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


KEY = fixture_key()
ROTATED_KEY = fixture_key(b"a different 32 bytes of material")


def load(name: str):
    """Import one of the scripts by path; they are scripts, not package modules."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChainTestCase(unittest.TestCase):
    """Runs the approval chain against a temporary state directory."""

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
        self.state = load("state")

    # -- driving the chain ------------------------------------------------

    def approve(self, uuid, login, account_id, labels=(), key=KEY):
        """Publish one device exactly as the `publish` job does, in its order.

        The order is load-bearing: record_expiry reads the binding
        record_owner just wrote to find the account whose term to apply.
        """
        os.environ["BODY"] = f"### Subject\n\n{uuid}\n\n### Public key\n\n```\n{key}\n```\n"
        os.environ["AUTHOR"] = login
        os.environ["AUTHOR_ID"] = account_id
        os.environ["LABELS_JSON"] = json.dumps([{"name": name} for name in labels])

        load("parse_request").main()
        shutil.move("/tmp/subject.pub", self.licenses / f"{uuid}.pub")
        # One issue enrols one device for good, so a rotation reuses the
        # device's own issue number and a new device gets its own. Deriving it
        # from the uuid keeps that true without the tests having to track it.
        issue = str(int(uuid[:8], 16) % 100000)
        self.run_with_argv("record_owner", [uuid, login, account_id, issue, ""])
        self.run_with_argv("record_expiry", [uuid])

    def run_with_argv(self, name, arguments):
        """Invoke a script's main() with the argv the workflow gives it."""
        saved = sys.argv
        sys.argv = [f"{name}.py", *arguments]
        try:
            load(name).main()
        finally:
            sys.argv = saved

    def revoke(self, uuid):
        """Withdraw a device the way the revoke job does.

        The BINDING survives on purpose: the uuid stays bound to its original
        account so the identity cannot be squatted after its key is gone.
        """
        (self.licenses / f"{uuid}.pub").unlink(missing_ok=True)
        (self.licenses / f"{uuid}.meta.json").unlink(missing_ok=True)

    # -- reading the result -----------------------------------------------

    def term_of(self, account_key):
        """The term recorded for an account, or None."""
        recorded = self.state.account_term(self.licenses, account_key)
        return None if recorded is self.state.ABSENT else recorded

    def devices_of(self, account_key):
        """Every published device of an account."""
        return sorted(self.state.active_devices(self.licenses, account_key))

    def write(self, name, payload):
        """Write one of the state files verbatim."""
        (self.licenses / name).write_text(json.dumps(payload) + "\n")

    def read(self, name):
        """Read one of the state files, or {} when it does not exist."""
        path = self.licenses / name
        return json.loads(path.read_text()) if path.exists() else {}


class RenameTest(ChainTestCase):
    """A rename is a new label on the same account, and nothing more."""

    def test_a_rename_keeps_the_term(self):
        """The decisive case: a rename must not look like a brand-new licence.

        Keyed by login, the renamed account had no term on record — which is
        the ONE path that starts a clock — so it was handed a fresh year, and
        a customer could renew indefinitely by renaming.
        """
        self.approve(MAC, "old-name", HOLDER_ID, labels=[f"expireAt:{PLANTED_TERM[:10]}"])

        self.approve(WIN, "new-name", HOLDER_ID)

        self.assertEqual(self.term_of(HOLDER_ID), PLANTED_TERM)

    def test_a_rename_keeps_the_negotiated_quota(self):
        """Seats are sold. A rename must not silently put the account back on 3.

        A fourth device is the assertion: it is refused on the default quota
        and accepted on the negotiated one, so accepting it proves the override
        survived the rename rather than merely that nothing crashed.
        """
        self.approve(MAC, "old-name", HOLDER_ID)
        self.write("account-quotas.json", {HOLDER_ID: 4})
        self.approve(WIN, "new-name", HOLDER_ID)
        self.approve(BOX, "new-name", HOLDER_ID)

        self.approve(FOURTH, "new-name", HOLDER_ID)

        self.assertEqual(len(self.devices_of(HOLDER_ID)), 4)

    def test_a_rename_keeps_a_legacy_login_keyed_quota(self):
        """The same, through the un-migrated fallback.

        quotas.json is keyed by the OLD login, and state.account_quota has to
        reach it across every login the id has carried — otherwise migrating
        becomes a prerequisite for a rename not costing the customer a seat.
        """
        self.approve(MAC, "old-name", HOLDER_ID)
        self.write("quotas.json", {"old-name": 4})
        self.approve(WIN, "new-name", HOLDER_ID)
        self.approve(BOX, "new-name", HOLDER_ID)

        self.approve(FOURTH, "new-name", HOLDER_ID)

        self.assertEqual(len(self.devices_of(HOLDER_ID)), 4)

    def test_a_rename_keeps_the_devices_on_one_licence(self):
        """Two labels, one account, one seat pool."""
        self.approve(MAC, "old-name", HOLDER_ID)

        self.approve(WIN, "new-name", HOLDER_ID)

        self.assertEqual(self.devices_of(HOLDER_ID), sorted([MAC, WIN]))

    def test_a_rename_does_not_widen_the_seat_count(self):
        """Renaming between each request would otherwise be unlimited devices."""
        self.approve(MAC, "name-one", HOLDER_ID)
        self.approve(WIN, "name-two", HOLDER_ID)
        self.approve(BOX, "name-three", HOLDER_ID)

        with self.assertRaises(SystemExit) as raised:
            self.approve(FOURTH, "name-four", HOLDER_ID)

        self.assertNotEqual(raised.exception.code, 0)


class RecycledLoginTest(ChainTestCase):
    """Whoever registers a released handle inherits nothing."""

    def test_the_new_holder_of_a_freed_login_is_refused(self):
        """Already closed by the write-once id guard; pinned so it stays closed."""
        self.approve(MAC, "old-name", HOLDER_ID)

        with self.assertRaises(SystemExit) as raised:
            self.approve(WIN, "old-name", STRANGER_ID)

        self.assertNotEqual(raised.exception.code, 0)

    def test_the_new_holder_inherits_no_term(self):
        """Asserted on the STATE, not on the refusal.

        The gate's refusal is one layer. This is the layer beneath it: even
        reading the state directly as the stranger's account, the previous
        holder's remaining year is not reachable — so a second way into the
        chain could not pick it up either.
        """
        self.approve(MAC, "old-name", HOLDER_ID, labels=[f"expireAt:{PLANTED_TERM[:10]}"])

        self.assertEqual(self.term_of(HOLDER_ID), PLANTED_TERM)
        self.assertIsNone(self.term_of(STRANGER_ID))

    def test_the_new_holder_inherits_no_quota(self):
        """A negotiated seat count is not a property of the handle."""
        self.approve(MAC, "old-name", HOLDER_ID)
        self.write("quotas.json", {"old-name": 9})

        self.assertEqual(self.state.account_quota(self.licenses, STRANGER_ID, 3), 3)

    def test_the_new_holder_inherits_no_devices(self):
        """Seats are counted per account id, so a freed handle starts empty."""
        self.approve(MAC, "old-name", HOLDER_ID)

        self.assertEqual(self.devices_of(STRANGER_ID), [])

    def test_the_new_holder_cannot_rotate_the_previous_holders_device(self):
        """Ownership is compared by id; a login match would have let this pass."""
        self.approve(MAC, "old-name", HOLDER_ID)
        # The directory is what the write-once guard reads, so remove the entry
        # to isolate the OWNERSHIP comparison from it: without this the request
        # is refused one check earlier and the test would prove nothing about
        # which key the binding is compared on.
        self.write("accounts.json", {})

        with self.assertRaises(SystemExit) as raised:
            self.approve(MAC, "old-name", STRANGER_ID)

        self.assertNotEqual(raised.exception.code, 0)


class LifecycleTest(ChainTestCase):
    """Rotation, a second machine, and re-enrolment after revoking everything."""

    def test_a_rotation_does_not_renew(self):
        """`license update` once a year was an indefinite free subscription."""
        self.approve(MAC, "holder", HOLDER_ID, labels=[f"expireAt:{PLANTED_TERM[:10]}"])

        self.approve(MAC, "holder", HOLDER_ID, key=ROTATED_KEY)

        self.assertEqual(self.term_of(HOLDER_ID), PLANTED_TERM)

    def test_a_rotation_does_not_consume_a_seat(self):
        """A compromised key is exactly when you cannot be told the licence is full."""
        self.approve(MAC, "holder", HOLDER_ID)
        self.approve(WIN, "holder", HOLDER_ID)
        self.approve(BOX, "holder", HOLDER_ID)

        self.approve(BOX, "holder", HOLDER_ID, key=ROTATED_KEY)

        self.assertEqual(len(self.devices_of(HOLDER_ID)), 3)

    def test_a_second_machine_inherits_the_licences_term(self):
        """One licence, one date — a late device must not outlive it."""
        self.approve(MAC, "holder", HOLDER_ID, labels=[f"expireAt:{PLANTED_TERM[:10]}"])

        self.approve(WIN, "holder", HOLDER_ID)

        self.assertEqual(
            json.loads((self.licenses / f"{WIN}.meta.json").read_text())["expiresAt"],
            PLANTED_TERM,
        )

    def test_revoking_everything_then_re_enrolling_does_not_restart_the_clock(self):
        """Revoke your only device, enrol another, and the year used to restart.

        The term record survives revocation precisely so that cannot happen,
        and re-keying it by id must not have reintroduced the path.
        """
        self.approve(MAC, "holder", HOLDER_ID, labels=[f"expireAt:{PLANTED_TERM[:10]}"])
        self.revoke(MAC)

        self.approve(WIN, "holder", HOLDER_ID)

        self.assertEqual(self.term_of(HOLDER_ID), PLANTED_TERM)

    def test_revocation_frees_a_seat_but_keeps_the_binding(self):
        """Counting bindings would retire a seat permanently on each revocation."""
        for uuid in (MAC, WIN, BOX):
            self.approve(uuid, "holder", HOLDER_ID)
        self.revoke(BOX)

        self.approve(FOURTH, "holder", HOLDER_ID)

        self.assertEqual(self.state.device_owner(self.licenses, BOX), HOLDER_ID)
        self.assertEqual(self.devices_of(HOLDER_ID), sorted([MAC, WIN, FOURTH]))

    def test_a_revoked_device_cannot_be_claimed_by_another_account(self):
        """The reason the binding outlives the key."""
        self.approve(MAC, "holder", HOLDER_ID)
        self.revoke(MAC)

        with self.assertRaises(SystemExit) as raised:
            self.approve(MAC, "stranger", STRANGER_ID)

        self.assertNotEqual(raised.exception.code, 0)


class UnattributableStateTest(ChainTestCase):
    """An account with no numeric id is marked, not guessed."""

    def legacy(self, uuid="eb56f295-9428-49b1-9dc3-0ebc6e383444", login="legacy-holder"):
        """Write the state a pre-accounts.json enrolment left behind.

        Login-keyed, with no id anywhere — which is exactly why it cannot be
        attributed to whoever holds that login today.
        """
        self.write("owners.json", {uuid: login})
        self.write("licences.json", {login: {"expiresAt": PLANTED_TERM}})
        self.write("quotas.json", {login: 5})
        (self.licenses / f"{uuid}.pub").write_text(KEY + "\n")

    def test_it_is_keyed_with_an_explicit_marker(self):
        """Never a bare login: a marker cannot be mistaken for an id."""
        self.legacy()

        key = self.state.device_owner(self.licenses, MAC)
        self.assertFalse(self.state.is_resolved(key))
        self.assertEqual(self.state.key_login(key), "legacy-holder")

    def test_its_next_request_is_refused_rather_than_attributed(self):
        """The hole: keyed by id, the current holder of that login looks new.

        No devices counted against the quota and no term on record — the one
        path that starts a clock — so the previous customer's seats and their
        remaining year went across silently.
        """
        self.legacy()

        with self.assertRaises(SystemExit) as raised:
            self.approve(WIN, "legacy-holder", STRANGER_ID)

        self.assertNotEqual(raised.exception.code, 0)

    def test_a_term_alone_is_enough_to_refuse(self):
        """An account that revoked every device still holds its remaining year.

        Checking only for devices would leave that year claimable by whoever
        registers the freed login.
        """
        self.write("licences.json", {"legacy-holder": {"expiresAt": PLANTED_TERM}})

        with self.assertRaises(SystemExit) as raised:
            self.approve(WIN, "legacy-holder", STRANGER_ID)

        self.assertNotEqual(raised.exception.code, 0)

    def test_an_unrelated_new_account_is_not_refused(self):
        """The guard must bite on held state, not on being new."""
        self.legacy()

        self.approve(WIN, "somebody-else", STRANGER_ID)

        self.assertEqual(self.devices_of(STRANGER_ID), [WIN])

    def test_it_gets_no_ci_entitlement(self):
        """There is no id to match `repository_owner_id` against.

        Falling back to the login would defeat the entire reason the id is used.
        """
        self.legacy()

        self.assertEqual(load("build_roster").ci_entitlements(self.licenses), {})

    def test_its_published_device_still_reaches_the_roster(self):
        """Refusing an approval must not withdraw a licence already sold."""
        self.legacy()

        roster = load("build_roster")
        roster.main()
        published = json.loads((self.licenses / "roster.json").read_text())

        self.assertIn(MAC, published["subjects"])


class MigrationTest(ChainTestCase):
    """migrate_state_keys.py: idempotent, replayable, and it does not guess."""

    def legacy_parc(self):
        """A login-keyed branch with an active device, a revoked one and a quota."""
        self.write("owners.json", {MAC: "holder", WIN: "holder", BOX: "other"})
        self.write("accounts.json", {"holder": {"id": HOLDER_ID}, "other": {"id": STRANGER_ID}})
        self.write("licences.json", {
            "holder": {"expiresAt": PLANTED_TERM},
            "other": {"expiresAt": "2030-01-01T00:00:00Z"},
        })
        self.write("quotas.json", {"holder": 5})
        # MAC is published, WIN was revoked (key gone, binding kept), BOX is
        # the other account's.
        (self.licenses / f"{MAC}.pub").write_text(KEY + "\n")
        (self.licenses / f"{BOX}.pub").write_text(KEY + "\n")

    def migrate(self, arguments=()):
        """Run the migration the way a maintainer does."""
        self.run_with_argv("migrate_state_keys", list(arguments))

    def test_every_binding_is_re_keyed(self):
        """Including the REVOKED one: dropping it would reopen uuid squatting."""
        self.legacy_parc()

        self.migrate()

        self.assertEqual(
            self.read("device-owners.json"),
            {MAC: HOLDER_ID, WIN: HOLDER_ID, BOX: STRANGER_ID},
        )

    def test_terms_and_quotas_are_carried_across(self):
        """A migration that loses a term hands out a fresh year on the next approval."""
        self.legacy_parc()

        self.migrate()

        self.assertEqual(self.read("account-terms.json")[HOLDER_ID]["expiresAt"], PLANTED_TERM)
        self.assertEqual(self.read("account-quotas.json")[HOLDER_ID], 5)

    def test_it_is_idempotent(self):
        """Run twice, and the second run must produce no change at all."""
        self.legacy_parc()
        self.migrate()
        before = {name: self.read(name) for name in
                  ("device-owners.json", "account-terms.json", "account-quotas.json")}

        self.migrate()

        after = {name: self.read(name) for name in before}
        self.assertEqual(after, before)

    def test_a_device_enrolled_after_the_migration_is_not_dropped(self):
        """Replayability: the second run merges, it does not replace.

        A migration that rebuilt the file from the legacy one would silently
        un-enrol every device approved since the first run.
        """
        self.legacy_parc()
        self.migrate()
        self.approve(FOURTH, "holder", HOLDER_ID)

        self.migrate()

        self.assertEqual(self.state.device_owner(self.licenses, FOURTH), HOLDER_ID)

    def test_an_account_with_no_id_is_listed_for_a_human(self):
        """Refusing to guess only helps if the refusal is actionable."""
        self.write("owners.json", {MAC: "legacy-holder"})

        self.migrate()

        worklist = self.read("unresolved-accounts.json")
        self.assertEqual(list(worklist), ["login:legacy-holder"])
        self.assertEqual(worklist["login:legacy-holder"]["devices"], [MAC])

    def test_resolving_by_hand_keys_it_and_clears_the_worklist(self):
        """One command, and the account is attributed — by a human, on evidence."""
        self.write("owners.json", {MAC: "legacy-holder"})
        self.write("licences.json", {"legacy-holder": {"expiresAt": PLANTED_TERM}})
        self.migrate()

        self.migrate(["--resolve", f"legacy-holder={HOLDER_ID}"])

        self.assertEqual(self.read("device-owners.json"), {MAC: HOLDER_ID})
        self.assertEqual(self.read("account-terms.json")[HOLDER_ID]["expiresAt"], PLANTED_TERM)
        self.assertFalse((self.licenses / "unresolved-accounts.json").exists())

    def test_resolving_cannot_overwrite_a_recorded_id(self):
        """GitHub does not reissue an id, so a differing one is another account."""
        self.legacy_parc()

        with self.assertRaises(SystemExit) as raised:
            self.migrate(["--resolve", f"holder={STRANGER_ID}"])

        self.assertNotEqual(raised.exception.code, 0)
        self.assertEqual(self.read("accounts.json")["holder"]["id"], HOLDER_ID)

    def test_a_corrupt_legacy_term_is_carried_across_verbatim(self):
        """A recorded null is copied as a recorded null — neither dropped nor repaired.

        Dropping it is the dangerous option, and it is the one that looks
        tidier: once the legacy file is gone the account would read as having
        NO term, which is the single path that starts a clock, and the next
        approval would hand it a fresh year. Repairing it would invent a date
        nobody sold. So it is preserved, and the chain that reads it keeps
        refusing it.
        """
        self.write("owners.json", {MAC: "holder"})
        self.write("accounts.json", {"holder": {"id": HOLDER_ID}})
        self.write("licences.json", {"holder": {"expiresAt": None}})

        self.migrate()

        self.assertEqual(self.read("account-terms.json")[HOLDER_ID], {"expiresAt": None})
        with self.assertRaises(SystemExit):
            self.approve(WIN, "holder", HOLDER_ID)

    def test_check_reports_pending_work_without_writing(self):
        """The signing run calls this every 20 minutes; it must not mutate state."""
        self.legacy_parc()

        with self.assertRaises(SystemExit) as raised:
            self.migrate(["--check"])

        self.assertNotEqual(raised.exception.code, 0)
        self.assertFalse((self.licenses / "device-owners.json").exists())

    def test_check_is_quiet_once_everything_is_keyed(self):
        """So the report is a signal rather than permanent noise."""
        self.legacy_parc()
        self.migrate()
        (self.licenses / "owners.json").unlink()

        with self.assertRaises(SystemExit) as raised:
            self.migrate(["--check"])

        self.assertEqual(raised.exception.code, 0)


class UnmigratedBranchTest(ChainTestCase):
    """An un-migrated branch must not be able to stop a signature."""

    def test_the_roster_is_still_built_from_legacy_files_alone(self):
        """A roster that is not re-signed blocks every client within 24 hours.

        That is far worse than a stale key scheme, so the legacy read path is
        load-bearing for as long as any branch predates the migration.
        """
        self.write("owners.json", {MAC: "holder"})
        self.write("accounts.json", {"holder": {"id": HOLDER_ID}})
        self.write("licences.json", {"holder": {"expiresAt": PLANTED_TERM}})
        (self.licenses / f"{MAC}.pub").write_text(KEY + "\n")

        roster = load("build_roster")
        roster.main()
        published = json.loads((self.licenses / "roster.json").read_text())

        self.assertIn(MAC, published["subjects"])
        self.assertEqual(published["ci"], {HOLDER_ID: {"exp": PLANTED_TERM}})

    def test_the_id_keyed_file_wins_where_both_exist(self):
        """During the transition both are present; one of them has to be authoritative.

        The id-keyed file is the one being written now, so a binding moved by
        the migration must not be read back from the file it was moved out of.
        """
        self.write("owners.json", {MAC: "holder"})
        self.write("accounts.json", {"holder": {"id": HOLDER_ID}})
        self.write("device-owners.json", {MAC: STRANGER_ID})

        self.assertEqual(self.state.device_owner(self.licenses, MAC), STRANGER_ID)

class TermOrderingTest(ChainTestCase):
    """Two terms across one account's logins are ordered as INSTANTS.

    They were ordered with min() over the raw strings, which is LEXICAL.
    Measured on python3.13:

        min("2027-01-01T00:00:00-05:00", "2027-01-01T00:00:00Z")
          -> "2027-01-01T00:00:00-05:00"

    and that value is four hours LATER. Ordering a licence term that way
    EXTENDS it — the one direction a term must never move by accident, and the
    direction the code's own comment warned about while doing it.

    "Both are strings" was never a comparability test either:
    min("soon", "2027-01-01T00:00:00Z") returns the date.
    """

    def terms_after_migration_sorted(self, recorded, candidate):
        """Plant the terms so `recorded` is the one migrate_terms sees FIRST.

        migrate_terms walks `sorted(legacy.items())`, so the login NAMES decide
        which value becomes "recorded" and which becomes the candidate. The
        helper below names them older/newer, and `newer` sorts first — so every
        row through it exercises corrupt-recorded-with-valid-candidate and
        cannot reach the opposite direction, which is the one where a naive
        implementation overwrites a good term with garbage.

        `aaa`/`zzz` make the ordering explicit instead of incidental.
        """
        self.write("accounts.json", {"aaa": {"id": HOLDER_ID}, "zzz": {"id": HOLDER_ID}})
        self.write("licences.json", {"aaa": {"expiresAt": recorded}, "zzz": {"expiresAt": candidate}})

        self.run_with_argv("migrate_state_keys", [])

        return self.read("account-terms.json").get(HOLDER_ID, {}).get("expiresAt")

    def test_a_corrupt_candidate_does_not_replace_a_valid_recorded_term(self):
        """The direction the helper below cannot reach.

        A valid term is recorded first and a corrupt one arrives as the
        candidate. Keeping the candidate would replace a usable bound with a
        value account_term's sentinel then refuses downstream — an account
        with no bound at all, from a migration that reported success.
        """
        got = self.terms_after_migration_sorted("2027-01-01T00:00:00Z", "soon")

        self.assertEqual(
            got,
            "2027-01-01T00:00:00Z",
            "a corrupt candidate replaced a valid recorded term",
        )

    def test_a_non_object_term_entry_refuses_instead_of_raising(self):
        """account-terms.json is JSON nobody validated.

        `"expiresAt" in x` means three different things across null, a number,
        a string and a list, and `.get()` raises on all of them. The device
        bindings are already written by then, so an exception left the re-key
        half applied. It must refuse with a sentence instead.
        """
        self.write("accounts.json", {"aaa": {"id": HOLDER_ID}})
        self.write("licences.json", {"aaa": {"expiresAt": "2027-01-01T00:00:00Z"}})
        #: Written verbatim: a hand-edited file is how this shape arrives.
        (self.licenses / "account-terms.json").write_text(json.dumps({HOLDER_ID: "2030-01-01T00:00:00Z"}) + "\n")

        with self.assertRaises(SystemExit) as raised:
            self.run_with_argv("migrate_state_keys", [])

        self.assertNotEqual(raised.exception.code, 0)

    def terms_after_migration(self, first, second):
        """Two logins on ONE account, each carrying a term, then migrate.

        Two logins mapping to one numeric id is exactly the case a rename
        produces, and it is the only way two terms ever meet.
        """
        self.write("accounts.json", {"older": {"id": HOLDER_ID}, "newer": {"id": HOLDER_ID}})
        self.write("licences.json", {"older": {"expiresAt": first}, "newer": {"expiresAt": second}})

        self.run_with_argv("migrate_state_keys", [])

        return self.read("account-terms.json").get(HOLDER_ID, {}).get("expiresAt")

    def test_the_earlier_instant_wins_across_offsets(self):
        """THE ROW. Lexically the -05:00 value sorts first and is LATER."""
        got = self.terms_after_migration("2027-01-01T00:00:00-05:00", "2027-01-01T00:00:00Z")

        self.assertEqual(
            got,
            "2027-01-01T00:00:00Z",
            "the later instant was kept, which extends the licence",
        )

    def test_the_earlier_instant_wins_in_the_ordinary_case(self):
        """The control: same offset, so lexical and chronological agree, and
        the fix must not have broken the case that already worked."""
        got = self.terms_after_migration("2028-01-01T00:00:00Z", "2027-01-01T00:00:00Z")

        self.assertEqual(got, "2027-01-01T00:00:00Z")

    def test_an_unorderable_pair_keeps_the_readable_term(self):
        """A value that is not an instant cannot be ordered against one, so the
        READABLE term wins whichever side it arrives on.

        This row is named for what it measures now. It used to say "keeps what
        was recorded", and that was wrong twice over: the rule is parseability
        rather than provenance, and which value is even "recorded" here is
        decided by `sorted(legacy.items())` — `newer` sorts before `older`, so
        the corrupt value is recorded first and the valid one arrives as the
        candidate. The row therefore exercises corrupt-recorded-with-valid-
        candidate, and says so.
        `test_a_corrupt_candidate_does_not_replace_a_valid_recorded_term`
        covers the opposite direction with logins chosen so the ordering is
        explicit.
        """
        got = self.terms_after_migration("2027-01-01T00:00:00Z", "soon")

        #: One value, exactly. `assertIn(got, (recorded, candidate))` stood
        #: here and accepted both, so it passed against a migration that kept
        #: either — which is the whole question.
        self.assertEqual(
            got,
            "2027-01-01T00:00:00Z",
            "the unreadable value won; a term that cannot be read is no bound at all",
        )


if __name__ == "__main__":
    unittest.main()
