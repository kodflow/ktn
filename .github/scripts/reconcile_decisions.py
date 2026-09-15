#!/usr/bin/env python3
"""Make the published state agree with every decision a maintainer has taken.

The concurrency group serialises this workflow but does not queue it: GitHub
keeps ONE pending run per group, so a third event arriving while one runs and
one waits replaces the waiting one. The existing note on that group reasons
about an approval — "there is no silent partial state, only a missing one" —
and for an approval that holds: the customer is waiting and says so.

A REVOCATION is the opposite shape. Nothing is written, nobody is waiting, and
the schedule then rebuilds the roster from a branch that never heard about it —
signing a fresh, valid roster that keeps authorising the revoked device for as
long as the licence's own term runs. The product's headline promise is that a
revocation takes effect within the roster's 24-hour window. A dropped
revocation breaks it silently and indefinitely, and the only person who would
notice is the one who benefits.

So every signing run reconciles before it builds:

* **Identity** is the GitHub label event id. Immutable, one per decision, and
  only a maintainer can produce one — the label IS the approval gate.
* **Order** is the event's own timestamp, and the ledger is appended in that
  order.
* **Processing** is the outcome recorded against that id in
  ``decisions.jsonl``. An id already in the ledger is never acted on twice,
  which is what makes re-running this harmless.
* **Resolution is from durable state only.** A decision names an issue; the
  subject it published is read from ``enrolments.json``, written at approval
  time. It is never read back from the issue body, which is editable by
  whoever opened it: a revocation resolved that way could be aimed at a
  different device than the one approved.

What it will not do is replay an approval. Publishing needs the key from the
body, and a body can have changed since the label landed — replaying one is
how an edited request would get published without ever being reviewed in that
form. An approval with no enrolment record is REPORTED, with the issue number,
for a maintainer to re-apply the label.
"""
import datetime
import json
import os
import pathlib
import sys

APPROVED = "license:approved"
REVOKED = "license:revoked"
LEDGER = "decisions.jsonl"


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see build_roster.py for the same helper."""
    return pathlib.Path(os.environ.get("LICENSES_DIR", "licenses"))


def emit(name: str, value: str) -> None:
    """Publish a step output when running under Actions, else print it."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        print(f"{name}={value}")
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def read_events(source: str) -> list:
    """Parse the label events, as a JSON array or as one object per line.

    `gh api --paginate` emits either depending on how it is asked, and a
    reconciliation that silently reads zero events would report a clean bill of
    health for a branch nobody checked.
    """
    text = sys.stdin.read() if source in ("", "-") else pathlib.Path(source).read_text()
    text = text.strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except ValueError:
        loaded = [json.loads(line) for line in text.splitlines() if line.strip()]
    events = []
    # A JSON array of pages is what --paginate --slurp produces; flatten it.
    for item in loaded if isinstance(loaded, list) else [loaded]:
        events.extend(item if isinstance(item, list) else [item])
    return events


def decisions(events: list) -> list:
    """The licence decisions among those events, oldest first.

    Sorted by the decision's own timestamp rather than by the order the API
    happened to return: the ledger is a record of when decisions were taken,
    and a burst is exactly the case where those two differ.
    """
    kept = []
    for event in events:
        label = (event.get("label") or {}).get("name") if isinstance(event.get("label"), dict) else event.get("label")
        if label not in (APPROVED, REVOKED):
            continue
        if event.get("event") not in (None, "labeled"):
            continue
        identity = event.get("id") or event.get("event_id")
        issue = event.get("issue")
        if isinstance(issue, dict):
            issue = issue.get("number")
        if identity is None or issue is None:
            continue
        # The actor is deliberately NOT recorded. Who applied a label is
        # already on the issue, in public, with a timestamp; the ledger names
        # the decision, and this branch is one clients fetch.
        kept.append(
            {
                "id": str(identity),
                "at": str(event.get("created_at") or event.get("at") or ""),
                "label": label,
                "issue": str(issue),
            }
        )
    return sorted(kept, key=lambda d: (d["at"], d["id"]))


def load_ledger(path: pathlib.Path) -> dict:
    """Every decision already processed, by event id.

    A line that will not parse is fatal: continuing would treat a recorded
    decision as new and, for a revocation already applied, look like a fresh
    one to apply — harmless — but for an approval, re-report it every 20
    minutes until someone muted the alarm.
    """
    if not path.exists():
        return {}
    processed = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            print(f"::error::{path}:{number} is not readable JSON; refusing to reconcile")
            sys.exit(1)
        processed[str(entry.get("id"))] = entry
    return processed


def append_ledger(path: pathlib.Path, entries: list) -> None:
    """Append, never rewrite. The ledger is the audit trail of decisions."""
    if not entries:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


def enrolments() -> dict:
    """Which subject each approved issue published, recorded at approval time."""
    path = licenses_dir() / "enrolments.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text() or "{}")


def withdraw(subject: str) -> bool:
    """Remove a subject's key, the way the revoke job does.

    owners.json is deliberately untouched: the uuid stays bound to its original
    owner so the identity cannot be squatted afterwards, and licences.json
    survives so revoking every device cannot restart the term.
    """
    state = licenses_dir()
    key = state / f"{subject}.pub"
    if not key.is_file():
        return False
    key.unlink()
    sidecar = state / f"{subject}.meta.json"
    if sidecar.is_file():
        sidecar.unlink()
    return True


def judge(decision: dict, records: dict) -> tuple:
    """Decide what this decision still needs, and say so in one word.

    Returns the outcome and a note. The outcome is what goes in the ledger; the
    note is what a human reads.
    """
    record = records.get(decision["issue"], {})
    subject = record.get("subject")

    if decision["label"] == APPROVED:
        if subject:
            return "published", f"issue #{decision['issue']} published {subject}"
        return (
            "unpublished",
            f"issue #{decision['issue']} was approved but nothing was published for it. "
            "Either the run was dropped by the concurrency group, or it refused the "
            "request (quota, malformed key). Re-apply the label to publish it; this "
            "will not be reported again.",
        )

    if not subject:
        return (
            "unresolved",
            f"issue #{decision['issue']} was revoked but no enrolment record names the "
            "device it published, so the revocation cannot be applied from durable "
            "state. Confirm by hand that the device's key is gone from the branch.",
        )
    if withdraw(subject):
        return "applied", f"withdrew {subject} for issue #{decision['issue']} (revocation had not landed)"
    return "effective", f"issue #{decision['issue']}: {subject} was already withdrawn"


def main() -> None:
    state = licenses_dir()
    state.mkdir(parents=True, exist_ok=True)
    ledger_path = state / LEDGER
    seeding = not ledger_path.exists()

    observed = decisions(read_events(sys.argv[1] if len(sys.argv) > 1 else "-"))
    processed = load_ledger(ledger_path)
    records = enrolments()

    now = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    run = os.environ.get("GITHUB_RUN_ID", "")

    entries, applied, attention = [], [], []
    for decision in observed:
        if decision["id"] in processed:
            continue
        if seeding:
            # First run: the ledger starts as a baseline of what has already
            # been decided. Judging these would report every historical
            # approval as unpublished — enrolments.json did not exist when they
            # were taken — and no action is available for them either way.
            outcome, note = "seed", ""
        else:
            outcome, note = judge(decision, records)
        entries.append({**decision, "outcome": outcome, "reconciled_at": now, "run": run})
        if outcome == "applied":
            applied.append(note)
        elif outcome in ("unpublished", "unresolved"):
            attention.append(note)
        if note:
            print(f"{outcome}: {note}")

    append_ledger(ledger_path, entries)

    if seeding:
        print(
            f"::warning::decision ledger created with {len(entries)} decision(s) as a "
            "baseline; decisions from here on are reconciled. Revocations taken before "
            "this run are not verifiable from durable state — confirm the branch holds "
            "no key for a device that was revoked."
        )

    emit("applied", str(len(applied)))
    emit("changed", "true" if applied else "false")
    report = ""
    if attention:
        path = pathlib.Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "reconciliation.md"
        path.write_text(
            "A licence decision did not reach the published state.\n\n"
            "Each line below is a decision a maintainer took that the branch does not\n"
            "reflect. The concurrency group serialises this workflow but does not queue\n"
            "it: GitHub keeps one pending run per group, so a burst of events can drop\n"
            "one. Revocations are re-applied automatically; these are the ones that\n"
            "need a person.\n\n"
            + "".join(f"- {note}\n" for note in attention)
            + "\nEach of these is recorded in `decisions.jsonl` on the `licenses` branch\n"
            "and will not be reported again.\n"
        )
        report = str(path)
    emit("report", report)
    emit("attention", "true" if attention else "false")
    print(
        f"reconciled {len(entries)} new decision(s): {len(applied)} applied, "
        f"{len(attention)} needing attention"
    )


if __name__ == "__main__":
    main()
