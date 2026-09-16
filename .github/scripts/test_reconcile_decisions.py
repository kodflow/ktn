#!/usr/bin/env python3
"""Tests for reconcile_decisions.py — the decisions that never reached the branch.

The concurrency group can drop a decision. For an approval that is noisy: the
customer is waiting and asks. For a revocation it is silent, because the only
person who notices is the one still being authorised — and the schedule keeps
signing fresh, valid rosters that say so, for as long as that licence's term
runs.

So the cases that matter are: a revocation nobody applied gets applied, the
same decision is never applied twice, and an approval is reported rather than
replayed from a body that may have changed since.
"""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("reconcile_decisions.py")
MAC = "eb56f295-9428-49b1-9dc3-0ebc6e383444"
# A SECOND device, so a test can show that another subject enrolling later
# says nothing about this one.
OTHER_MAC = "7c1d0a42-5f63-4b8e-9a20-11f3c4d5e6a7"
WIN = "11111111-2222-4333-8444-555555555555"
PUBLIC_KEY = "ssh-ed25519 AAAAtestfixturenotarealkeyAA\n"


def load_script():
    """Import reconcile_decisions.py by path; it is a script, not a module."""
    spec = importlib.util.spec_from_file_location("reconcile_decisions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReconcileTestCase(unittest.TestCase):
    """A branch with devices published and a ledger that has already run once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        self.events = root / "events.jsonl"
        self.output = root / "github_output"
        self.output.touch()
        os.environ["LICENSES_DIR"] = str(self.state)
        os.environ["GITHUB_OUTPUT"] = str(self.output)
        os.environ["RUNNER_TEMP"] = str(root)
        for key in ("LICENSES_DIR", "GITHUB_OUTPUT", "RUNNER_TEMP"):
            self.addCleanup(os.environ.pop, key, None)
        self.module = load_script()

    def publish(self, uuid, issue, login="a-holder"):
        """Enrol a device the way the approval chain does, and record its issue."""
        (self.state / f"{uuid}.pub").write_text(PUBLIC_KEY)
        (self.state / f"{uuid}.meta.json").write_text('{"expiresAt":"2027-03-01T00:00:00Z"}\n')

        owners = self.load("owners.json")
        owners[uuid] = login
        self.save("owners.json", owners)

        licences = self.load("licences.json")
        licences[login] = {"expiresAt": "2027-03-01T00:00:00Z"}
        self.save("licences.json", licences)

        enrolments = self.load("enrolments.json")
        enrolments[str(issue)] = {"subject": uuid, "login": login, "account": "1", "at": "x"}
        self.save("enrolments.json", enrolments)

    def load(self, name):
        path = self.state / name
        return json.loads(path.read_text()) if path.exists() else {}

    def save(self, name, payload):
        (self.state / name).write_text(json.dumps(payload) + "\n")

    def ledger(self):
        """Every ledger line, in file order — which is decision order."""
        path = self.state / self.module.LEDGER
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def seal_ledger(self, *ids):
        """Mark decisions as already processed, so the run is not a first one."""
        path = self.state / self.module.LEDGER
        with open(path, "a", encoding="utf-8") as handle:
            for identity in ids or ("0",):
                handle.write(json.dumps({"id": str(identity), "outcome": "seed"}) + "\n")

    def decide(self, *events):
        """Write the label events the workflow collects, then reconcile."""
        self.events.write_text("".join(json.dumps(event) + "\n" for event in events))
        self.module.sys.argv = ["reconcile_decisions.py", str(self.events)]
        self.module.main()

    def outputs(self):
        """The step outputs the workflow branches on."""
        parsed = {}
        for line in self.output.read_text().splitlines():
            if "=" in line:
                name, _, value = line.partition("=")
                parsed[name] = value
        return parsed


def event(identity, label, issue, at="2026-09-15T10:00:00Z", **extra):
    """One label event in the shape the workflow hands over."""
    return {"id": identity, "label": label, "issue": issue, "created_at": at, "actor": "a-holder", **extra}


class RevocationTest(ReconcileTestCase):
    """The silent loss: a revocation that was decided and never written."""

    def test_a_revocation_that_never_landed_is_applied(self):
        """THE defect. Nothing else in the chain would ever notice this one.

        The roster is rebuilt from the branch, so a revocation missing from the
        branch is a revocation the next signature undoes — with a fresh, valid,
        correctly signed roster still authorising the withdrawn device.
        """
        self.publish(MAC, issue=7)
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7))

        self.assertFalse((self.state / f"{MAC}.pub").exists())
        self.assertFalse((self.state / f"{MAC}.meta.json").exists())
        self.assertEqual(self.outputs()["applied"], "1")
        self.assertEqual(self.outputs()["changed"], "true")

    def test_it_is_applied_once_and_only_once(self):
        """Re-running every 20 minutes must not keep reporting a done decision."""
        self.publish(MAC, issue=7)
        self.seal_ledger()
        self.decide(event(901, "license:revoked", 7))

        self.output.write_text("")
        self.decide(event(901, "license:revoked", 7))

        self.assertEqual(self.outputs()["applied"], "0")
        self.assertEqual([entry["id"] for entry in self.ledger()].count("901"), 1)

    def test_a_revocation_already_effective_is_recorded_not_reapplied(self):
        """The normal path already ran. This must be a no-op with a trace."""
        self.publish(MAC, issue=7)
        (self.state / f"{MAC}.pub").unlink()
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7))

        self.assertEqual(self.outputs()["applied"], "0")
        self.assertEqual(self.ledger()[-1]["outcome"], "effective")

    def test_the_owner_binding_and_the_term_survive_it(self):
        """Reconciliation withdraws a key; it must not free a seat or a clock.

        owners.json keeps the binding so the identity cannot be squatted, and
        licences.json survives so revoking every device cannot restart the
        year. A second code path doing revocation is a second chance to get
        those two wrong.
        """
        self.publish(MAC, issue=7)
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7))

        self.assertEqual(self.load("owners.json")[MAC], "a-holder")
        self.assertIn("expiresAt", self.load("licences.json")["a-holder"])

    def test_the_issue_body_cannot_redirect_a_revocation(self):
        """Resolution is from enrolments.json, written at approval time.

        A body is editable by whoever opened the issue, indefinitely, and the
        maintainer applying the label sees only the label. An event carrying a
        different subject must therefore change nothing.
        """
        self.publish(MAC, issue=7)
        self.publish(WIN, issue=8)
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7, body=f"### Subject\n\n{WIN}\n"))

        self.assertFalse((self.state / f"{MAC}.pub").exists())
        self.assertTrue((self.state / f"{WIN}.pub").exists())

    def test_a_revocation_with_no_enrolment_record_is_reported(self):
        """A device published before the record existed cannot be resolved.

        Reporting it is the honest outcome: the alternative is reading the body,
        which is the thing being avoided.
        """
        (self.state / f"{MAC}.pub").write_text(PUBLIC_KEY)
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7))

        self.assertEqual(self.outputs()["attention"], "true")
        self.assertEqual(self.ledger()[-1]["outcome"], "unresolved")
        self.assertTrue((self.state / f"{MAC}.pub").exists())


class ApprovalTest(ReconcileTestCase):
    """An approval is reported, never replayed."""

    def test_an_approval_that_never_published_is_reported(self):
        """Replaying it would publish a body nobody reviewed in its current form."""
        self.seal_ledger()

        self.decide(event(902, "license:approved", 9))

        self.assertEqual(self.outputs()["attention"], "true")
        self.assertIn("#9", pathlib.Path(self.outputs()["report"]).read_text())
        self.assertEqual(self.ledger()[-1]["outcome"], "unpublished")

    def test_it_is_reported_once(self):
        """A report every 20 minutes is a report nobody reads."""
        self.seal_ledger()
        self.decide(event(902, "license:approved", 9))
        self.output.write_text("")

        self.decide(event(902, "license:approved", 9))

        self.assertEqual(self.outputs()["attention"], "false")

    def test_an_approval_that_published_needs_nothing(self):
        self.publish(MAC, issue=9)
        self.seal_ledger()

        self.decide(event(902, "license:approved", 9))

        self.assertEqual(self.outputs()["attention"], "false")
        self.assertEqual(self.ledger()[-1]["outcome"], "published")


class LedgerTest(ReconcileTestCase):
    """Identity, order, and processing — the three things that must be durable."""

    def test_decisions_are_recorded_in_the_order_they_were_taken(self):
        """The API returns what it likes; the ledger records what happened when."""
        self.publish(MAC, issue=7)
        self.publish(WIN, issue=8)
        self.seal_ledger()

        self.decide(
            event(903, "license:revoked", 8, at="2026-09-15T12:00:00Z"),
            event(901, "license:revoked", 7, at="2026-09-15T10:00:00Z"),
        )

        self.assertEqual([entry["id"] for entry in self.ledger()][1:], ["901", "903"])

    def test_the_label_event_id_is_the_identity(self):
        """Two decisions on the same issue are two decisions, not one."""
        self.publish(MAC, issue=7)
        self.seal_ledger()

        self.decide(
            event(901, "license:approved", 7, at="2026-09-15T09:00:00Z"),
            event(902, "license:revoked", 7, at="2026-09-15T10:00:00Z"),
        )

        self.assertEqual(sorted(entry["id"] for entry in self.ledger())[1:], ["901", "902"])

    def test_the_first_run_seeds_without_acting(self):
        """With no ledger, every historical decision looks new.

        Judging them would report every approval taken before enrolments.json
        existed as unpublished, and no action is available for them anyway. The
        baseline is announced rather than silent.
        """
        self.publish(MAC, issue=7)

        self.decide(event(901, "license:revoked", 7))

        self.assertTrue((self.state / f"{MAC}.pub").exists())
        self.assertEqual(self.ledger()[0]["outcome"], "seed")
        self.assertEqual(self.outputs()["attention"], "false")

    def test_a_decision_after_the_baseline_is_acted_on(self):
        """The seeding is one run, not a mode."""
        self.publish(MAC, issue=7)
        self.decide(event(900, "license:approved", 7, at="2026-09-01T00:00:00Z"))
        self.output.write_text("")

        self.decide(
            event(900, "license:approved", 7, at="2026-09-01T00:00:00Z"),
            event(901, "license:revoked", 7, at="2026-09-15T10:00:00Z"),
        )

        self.assertFalse((self.state / f"{MAC}.pub").exists())

    def test_a_corrupt_ledger_stops_the_run(self):
        """Mis-reading the ledger re-decides settled decisions. Refuse instead."""
        self.publish(MAC, issue=7)
        (self.state / self.module.LEDGER).write_text("{not json\n")

        with self.assertRaises(SystemExit) as raised:
            self.decide(event(901, "license:revoked", 7))

        self.assertNotEqual(raised.exception.code, 0)
        self.assertTrue((self.state / f"{MAC}.pub").exists())

    def test_labels_that_are_not_decisions_are_ignored(self):
        """Everything else a maintainer labels is noise to this script."""
        self.publish(MAC, issue=7)
        self.seal_ledger()

        self.decide(
            event(901, "license:request", 7),
            event(902, "expireAt:2027-01-01", 7),
        )

        self.assertTrue((self.state / f"{MAC}.pub").exists())
        self.assertEqual(len(self.ledger()), 1)

    def test_the_api_shapes_are_both_understood(self):
        """`gh api` hands over nested objects; the workflow flattens them.

        Reading zero events from a shape change would report a clean bill of
        health for a branch nobody checked, which is the failure mode this
        whole script exists to remove.
        """
        self.publish(MAC, issue=7)
        self.seal_ledger()

        self.events.write_text(
            json.dumps(
                [
                    {
                        "id": 901,
                        "event": "labeled",
                        "created_at": "2026-09-15T10:00:00Z",
                        "label": {"name": "license:revoked"},
                        "issue": {"number": 7},
                        "actor": {"login": "a-holder"},
                    }
                ]
            )
        )
        self.module.sys.argv = ["reconcile_decisions.py", str(self.events)]
        self.module.main()

        self.assertFalse((self.state / f"{MAC}.pub").exists())


class SupersededRevocationTest(ReconcileTestCase):
    """A stale revocation must not silently undo a newer approval.

    A revocation is replayed on the strength of its event id alone: the id is
    not in the ledger, so the decision was never reconciled. That is the right
    identity for "has this been processed" and says nothing about whether the
    answer is still current.

    The gap: the ledger is committed and pushed by the same step, so a failed
    push loses the record of a withdrawal that DID happen. If the device
    re-enrols in the meantime — a new issue, a new approval, the same uuid —
    the next run reads the old revocation as outstanding and withdraws a key a
    maintainer has since republished. Nothing reported it, because from the
    ledger's point of view the revocation simply took effect.
    """

    def republish(self, uuid, issue, at):
        """Enrol under a NEW issue with a real timestamp, as an approval does."""
        self.publish(uuid, issue=issue)
        enrolments = self.load("enrolments.json")
        enrolments[str(issue)] = {"subject": uuid, "login": "a-holder", "account": "1", "at": at}
        self.save("enrolments.json", enrolments)

    def test_a_revocation_is_not_replayed_over_a_later_republication(self):
        """THE ROW. Before this, the key was withdrawn and nothing said so."""
        self.republish(MAC, issue=7, at="2026-09-15T09:00:00Z")
        self.republish(MAC, issue=8, at="2026-09-15T11:00:00Z")
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7, at="2026-09-15T10:00:00Z"))

        self.assertTrue((self.state / f"{MAC}.pub").is_file())
        self.assertEqual(self.ledger()[-1]["outcome"], "superseded")

    def test_it_needs_a_person_rather_than_passing_quietly(self):
        """Whether the revocation still stands is a decision, not a default.

        So it goes in the attention bucket: `applied` stays at zero and the
        reconciliation report names it. Publishing a "superseded" line nobody
        reads would be the same silence with extra steps.
        """
        self.republish(MAC, issue=7, at="2026-09-15T09:00:00Z")
        self.republish(MAC, issue=8, at="2026-09-15T11:00:00Z")
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7, at="2026-09-15T10:00:00Z"))

        self.assertEqual(self.outputs()["applied"], "0")
        self.assertEqual(self.outputs()["changed"], "false")

    def test_an_earlier_republication_does_not_excuse_the_revocation(self):
        """Order is the whole rule. A key published BEFORE the revocation is
        exactly what the revocation was about, and must still be withdrawn."""
        self.republish(MAC, issue=7, at="2026-09-15T09:00:00Z")
        self.republish(MAC, issue=8, at="2026-09-15T09:30:00Z")
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7, at="2026-09-15T10:00:00Z"))

        self.assertFalse((self.state / f"{MAC}.pub").is_file())
        self.assertEqual(self.ledger()[-1]["outcome"], "applied")

    def test_a_different_subject_is_not_a_republication(self):
        """Another device enrolling later says nothing about this one."""
        self.republish(MAC, issue=7, at="2026-09-15T09:00:00Z")
        self.republish(OTHER_MAC, issue=8, at="2026-09-15T11:00:00Z")
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7, at="2026-09-15T10:00:00Z"))

        self.assertFalse((self.state / f"{MAC}.pub").is_file())
        self.assertEqual(self.ledger()[-1]["outcome"], "applied")

    def test_an_unreadable_timestamp_replays_as_before(self):
        """Refusing on a timestamp it could not read would be the opposite
        failure: a revocation dropped because a record was malformed."""
        self.publish(MAC, issue=7)
        self.publish(MAC, issue=8)
        self.seal_ledger()

        self.decide(event(901, "license:revoked", 7, at="2026-09-15T10:00:00Z"))

        self.assertFalse((self.state / f"{MAC}.pub").is_file())
        self.assertEqual(self.ledger()[-1]["outcome"], "applied")


if __name__ == "__main__":
    unittest.main()
