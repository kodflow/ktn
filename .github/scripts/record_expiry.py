#!/usr/bin/env python3
"""Record a licence's term, and stamp it onto the device being published.

A term belongs to the LICENCE — the account — not to any one device. It lives
in ``licences.json``, which is written once and never removed, and is copied
onto each device's sidecar so the roster builder can publish it per subject
without knowing anything about accounts.

Three rules, each of which exists because its absence was a way to get paid
service for free:

* **A rotation does not renew.** This script used to run identically for a
  rotation and a first publish, so ``license update`` handed out a fresh 365
  days every time: rotating once a year was an indefinite free subscription.
* **A new device inherits the licence's term.** Giving it its own year would
  leave one licence expiring on several different days — that is not one
  licence, it is several, and a device added late would outlive the licence
  that authorised it.
* **Revoking everything does not reset the clock.** Revocation deletes a
  device's key and sidecar, so a term derived from surviving devices would
  vanish with the last one: revoke your only device, enrol another, and the
  year starts again. ``licences.json`` survives revocation precisely so that
  cannot happen.

Only a maintainer moves a term, by applying an ``expireAt:YYYY-MM-DD`` label
before (or together with) ``license:approved``; labels applied afterwards do
not retroactively change a recorded term. That renews the LICENCE: every
active device of the account is restamped, so the account keeps exactly one
date.
"""
import datetime
import json
import os
import pathlib
import re
import sys


def licenses_dir() -> pathlib.Path:
    """Where licence state lives.

    Defaults to ``licenses/`` so the scripts stay runnable from a plain
    checkout of ``main``. The workflow overrides it with ``LICENSES_DIR``
    because the state now lives on its own branch, checked out into a
    separate directory: ``main`` carries a required-status ruleset that
    refuses a direct push, which silently stopped every re-signature for a
    day and a half until the roster's window closed.
    """
    return pathlib.Path(os.environ.get("LICENSES_DIR", "licenses"))


LABEL_RE = re.compile(r"^expireAt:(\d{4}-\d{2}-\d{2})$")
DEFAULT_TERM = datetime.timedelta(days=365)


def fail(message: str) -> None:
    """Abort loudly so the maintainer sees why an approval did not take."""
    print(f"::error::{message}")
    sys.exit(1)


def parse_term(value, source: str) -> datetime.datetime:
    """Parse a recorded term, refusing anything that is not one.

    Terms are compared and copied, so a malformed one must stop the run rather
    than propagate. Copied verbatim onto a new device it would reach the signed
    roster, where the client reads an unparseable or absent expiry as "no term
    recorded" — a corrupt date would quietly become an unlimited licence.
    """
    if not isinstance(value, str):
        fail(f"{source} holds a non-string expiresAt: {value!r}")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(f"{source} holds an unparseable expiresAt: {value!r}")
    # A naive timestamp cannot be ordered against an aware one, and the whole
    # scheme is UTC.
    if parsed.tzinfo is None:
        fail(f"{source} holds a timezone-naive expiresAt: {value!r}")
    return parsed


def render(moment: datetime.datetime) -> str:
    """Render a term the way every other file in this chain spells one."""
    return moment.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def owners() -> dict:
    """The device → account bindings recorded so far."""
    path = licenses_dir() / "owners.json"
    # No file means nothing is bound yet, which is the first-ever publish.
    if not path.exists():
        return {}
    return json.loads(path.read_text() or "{}")


def active_devices(account: str) -> list:
    """Every device of an account whose key is currently published.

    A revoked device keeps its owner binding — deliberately, so the identity
    cannot be squatted afterwards — but it is no longer part of the licence and
    must not be restamped on renewal.
    """
    return sorted(
        uuid
        for uuid, owner in owners().items()
        if owner == account and (licenses_dir() / f"{uuid}.pub").is_file()
    )


def adopt_existing_term(account: str) -> str:
    """Recover an account's term from device sidecars, for migration only.

    Licences enrolled before licences.json existed carry their term on the
    device sidecar and nowhere else. Reading it back once, and persisting it,
    keeps those accounts on the date they were sold rather than silently
    granting them a fresh year on their next device.

    The earliest wins when siblings disagree: a device must never outlive its
    licence, and taking the latest would let one stale sidecar extend the whole
    account.
    """
    terms = []
    for uuid in active_devices(account):
        sidecar = licenses_dir() / f"{uuid}.meta.json"
        if not sidecar.exists():
            continue
        recorded = json.loads(sidecar.read_text()).get("expiresAt")
        if recorded:
            terms.append(parse_term(recorded, str(sidecar)))
    return render(min(terms)) if terms else ""


def licence_term(account: str) -> str:
    """The account's term, or "" when it has never had one."""
    path = licenses_dir() / "licences.json"
    if path.exists():
        recorded = json.loads(path.read_text() or "{}").get(account, {}).get("expiresAt")
        if recorded:
            # Validate on the way out: a hand-edited file must fail here rather
            # than reach the roster.
            return render(parse_term(recorded, str(path)))
    # Nothing at the account level: either a brand-new licence, or one that
    # predates this file.
    return adopt_existing_term(account)


def set_licence_term(account: str, iso: str) -> None:
    """Record the account's term. This file outlives every device it has."""
    path = licenses_dir() / "licences.json"
    licences = json.loads(path.read_text() or "{}") if path.exists() else {}
    licences.setdefault(account, {})["expiresAt"] = iso
    path.write_text(json.dumps(licences, indent=2, sort_keys=True) + "\n")


def stamp(uuid: str, iso: str) -> None:
    """Copy the licence's term onto one device's sidecar."""
    (licenses_dir() / f"{uuid}.meta.json").write_text(
        json.dumps({"expiresAt": iso}, indent=2, sort_keys=True) + "\n"
    )


def renew(account: str, uuid: str, explicit) -> None:
    """Apply a maintainer's expireAt: label to the whole licence.

    Restamping every active device is what keeps "one licence, one date" true.
    Renewing only the device that happened to carry the label would leave the
    others on their old dates, and the renewed one outliving the licence it
    belongs to.
    """
    try:
        moment = datetime.datetime.strptime(explicit.group(1), "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        fail(f"expireAt label {explicit.group(0)!r} is not a valid calendar date")
    iso = render(moment)
    set_licence_term(account, iso)
    # The subject being approved may not be published yet, so it is added
    # explicitly rather than left to active_devices().
    devices = {*active_devices(account), uuid}
    for device in sorted(devices):
        stamp(device, iso)
    print(f"@{account} renewed to {iso}; {len(devices)} device(s) restamped")


def main() -> None:
    uuid = sys.argv[1]
    labels = [label["name"] for label in json.loads(os.environ.get("LABELS_JSON", "[]"))]

    account = owners().get(uuid)
    # record_owner.py runs immediately before this script, so an unbound
    # subject here means the workflow changed and the binding is gone. Guessing
    # an account would stamp a term onto the wrong licence.
    if not account:
        fail(f"{uuid} has no recorded owner; record_owner.py must run first")

    explicit = next(filter(None, (LABEL_RE.match(name) for name in labels)), None)
    if explicit:
        renew(account, uuid, explicit)
        return

    iso = licence_term(account)
    # A licence with no term on record is a new one. This is the ONLY path that
    # starts a clock, which is what makes every other path — a rotation, a new
    # device, a re-enrolment after revoking everything — unable to grant free
    # time.
    if not iso:
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        iso = render(now + DEFAULT_TERM)
        print(f"@{account} starts a new term, {iso}")
    set_licence_term(account, iso)
    stamp(uuid, iso)
    print(f"{uuid} carries its licence's term, {iso}")


if __name__ == "__main__":
    main()
