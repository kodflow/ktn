#!/usr/bin/env python3
"""Judge what the licence origins are actually serving, from outside the signer.

The freshness alarm in license-roster.yml is the LAST step of the job that
signs. It can only fire if everything before it worked, which excludes every
interesting case: a job that fails at the push step, a job that never starts
because GitHub dropped the schedule, a workflow disabled after sixty days of
repository inactivity. The outage this repository already had was invisible for
thirteen hours for exactly that reason — a failing cron on a quiet repository
looks like a quiet repository.

An alarm inside the thing being watched is not monitoring. This runs as its own
workflow, on its own schedule, and looks at the artefacts CLIENTS fetch rather
than at the state the signer holds:

* **Authenticity** — does the served bundle verify against the vendor key? A
  bundle that does not is what the client reports as spoofing, and it is worth
  knowing before a customer tells us.
* **Freshness** — how old is the served roster's `iat`? This is the early
  signal. Publication stops hours before the 24-hour window closes, and every
  one of those hours is a chance to fix it without an outage.
* **Window** — how long until `exp`, with a margin rather than at the cliff.
* **Size** — the client refuses an oversized artefact on the wire and out of
  its cache, so growth past the ceiling is an outage a signature cannot fix.
* **Concordance** — do the origins agree? They are cached independently; one
  serving a payload the other does not is a publication that half-landed.
* **Liveness of the signer** — when did the signing workflow last SUCCEED?
  This is the check the signer cannot make about itself.

Every check degrades to a finding rather than to silence. A dead origin, an
unreadable document, a missing key: each is reported. Nothing here is allowed
to conclude "fine" because it could not look.
"""
import base64
import datetime
import json
import os
import pathlib
import subprocess
import sys

# Same ceiling as promote_roster.py, and for the same reason: it mirrors what
# pkg/license refuses. Declared twice because these scripts are standalone —
# see the duplicated licenses_dir() helpers for the same trade.
CLIENT_MAX_BYTES = 4 * 1024 * 1024
WARN_BYTES = CLIENT_MAX_BYTES // 2
# Signing runs every 20 minutes. A served roster whose signature is older than
# this means at least six consecutive slots produced nothing, which is a
# problem hours before the window closes.
MAX_IAT_AGE_HOURS = 2
# Half the 24-hour window. Past this, publication has been broken for half a
# day and the remaining margin is what a human has to work with.
EXPIRY_MARGIN_HOURS = 12
# The signer's own cadence plus room for GitHub delaying a scheduled run.
SIGNER_MAX_SILENCE_MINUTES = 90


def hours_between(later: datetime.datetime, earlier: datetime.datetime) -> float:
    """Signed hours from earlier to later."""
    return (later - earlier).total_seconds() / 3600


def parse_moment(value: str) -> datetime.datetime:
    """Read one of this chain's timestamps, or raise ValueError."""
    moment = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"timezone-naive timestamp {value!r}")
    return moment


def openssl_verifier(public_key: pathlib.Path):
    """A verifier for the served signature, or None when no key is configured.

    The PUBLIC half only. A monitor has no business holding the signing key:
    widening that secret to a second workflow would cost more than this check
    is worth. When no key is configured the authenticity check reports that it
    could not be made — it never reports success.
    """
    if not public_key or not public_key.is_file():
        return None

    def verify(payload: bytes, signature: bytes) -> bool:
        """True when this signature covers these bytes under the vendor key."""
        temp = pathlib.Path(os.environ.get("RUNNER_TEMP", "/tmp"))
        payload_path = temp / "watch-payload.bin"
        signature_path = temp / "watch-signature.bin"
        payload_path.write_bytes(payload)
        signature_path.write_bytes(signature)
        # -rawin is Ed25519 signing the message directly rather than a digest,
        # which is what the signer produces and what the binary verifies.
        completed = subprocess.run(
            [
                "openssl", "pkeyutl", "-verify", "-rawin", "-pubin",
                "-inkey", str(public_key),
                "-in", str(payload_path),
                "-sigfile", str(signature_path),
            ],
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0

    return verify


def unpack(raw: bytes) -> dict:
    """Open one served bundle as far as it will open.

    Returns what was readable and a reason when something was not. A bundle
    that will not open is a finding about that origin, not an exception that
    ends the watch and leaves the other origins unchecked.
    """
    result = {"bytes": len(raw)}
    if not raw:
        result["error"] = "served nothing at all"
        return result
    try:
        bundle = json.loads(raw)
    except ValueError:
        result["error"] = "is not readable JSON"
        return result
    # A JSON array or scalar is readable JSON and has no .get, so it crashed
    # the watcher one line below — and a watcher that dies reports nothing
    # about the origins it had not reached yet. Served content is exactly what
    # this file is not allowed to trust the shape of.
    if not isinstance(bundle, dict):
        result["error"] = "is JSON but not an object, so it cannot be a signed bundle"
        return result
    try:
        result["payload"] = base64.b64decode(bundle.get("payload", ""), validate=True)
        result["signature"] = base64.b64decode(bundle.get("sig", ""), validate=True)
    except (ValueError, TypeError):
        result["error"] = "does not carry a base64 payload and signature"
        return result
    if not result["payload"] or not result["signature"]:
        result["error"] = "carries an empty payload or signature"
        return result
    try:
        roster = json.loads(result["payload"])
    except ValueError:
        result["error"] = "carries a payload that is not readable JSON"
        return result
    result["roster"] = roster
    for field in ("iat", "exp"):
        if field not in roster:
            result["error"] = f"carries a roster with no {field!r}"
            return result
    try:
        result["iat"] = parse_moment(roster["iat"])
        result["exp"] = parse_moment(roster["exp"])
    except ValueError as problem:
        result["error"] = f"carries an unusable window ({problem})"
    return result


def check_origin(name: str, served: dict, now: datetime.datetime, verify) -> list:
    """Everything worth saying about one origin's copy."""
    findings = []
    if "error" in served:
        return [f"**{name}** {served['error']}."]

    if served["bytes"] > CLIENT_MAX_BYTES:
        findings.append(
            f"**{name}** serves {served['bytes']}B, over the {CLIENT_MAX_BYTES}B the "
            "client refuses on the wire and out of its cache. Every client is blocked."
        )
    elif served["bytes"] > WARN_BYTES:
        findings.append(
            f"**{name}** serves {served['bytes']}B, over half the client's "
            f"{CLIENT_MAX_BYTES}B ceiling. The roster is outgrowing the format."
        )

    age = hours_between(now, served["iat"])
    if age > MAX_IAT_AGE_HOURS:
        findings.append(
            f"**{name}** serves a roster signed {age:.1f}h ago; signing runs every 20 "
            f"minutes, so anything over {MAX_IAT_AGE_HOURS}h means publication has "
            "stopped. Nothing is broken for clients yet — that is the point of saying it now."
        )

    left = hours_between(served["exp"], now)
    if left <= 0:
        findings.append(
            f"**{name}** serves a roster whose window closed {abs(left):.1f}h ago. "
            "Clients are refusing to run."
        )
    elif left < EXPIRY_MARGIN_HOURS:
        findings.append(
            f"**{name}** serves a roster with {left:.1f}h of window left, under the "
            f"{EXPIRY_MARGIN_HOURS}h margin. Publication has been broken for most of a day."
        )

    if verify is None:
        findings.append(
            f"**{name}** authenticity NOT CHECKED: no vendor public key is configured, so "
            "this watch cannot tell a genuine roster from a substituted one. Set the "
            "VENDOR_PUBLIC_KEY variable to the public half of the signing key."
        )
    elif not verify(served["payload"], served["signature"]):
        findings.append(
            f"**{name}** serves a bundle that does NOT verify against the vendor key. "
            "This is what a client reports as spoofing; either publication paired the "
            "wrong halves or the origin is serving something we did not sign."
        )
    return findings


def check_concordance(sources: dict) -> list:
    """Origins are cached independently; disagreement means a half-landed push."""
    readable = {name: served for name, served in sources.items() if "payload" in served}
    if len(readable) < 2:
        return []
    payloads = {name: served["payload"] for name, served in readable.items()}
    distinct = set(payloads.values())
    if len(distinct) == 1:
        return []
    described = ", ".join(
        f"{name} signed {readable[name].get('iat')}" for name in sorted(readable)
    )
    return [
        "The origins disagree about what the current roster is: "
        f"{described}. Past the five-minute cache window this is a publication that "
        "only half landed, and which copy a client gets is luck."
    ]


def check_signer(last_success: str, now: datetime.datetime) -> list:
    """When did the workflow that signs last SUCCEED?

    This is the check the signer cannot make about itself: a run that fails
    before its own alarm step, or never starts, says nothing from the inside.
    """
    if not last_success:
        return [
            "No successful run of the signing workflow could be found. Either it has "
            "never succeeded, or its history is not readable from here — both are worth "
            "a look right now."
        ]
    try:
        moment = parse_moment(last_success)
    except ValueError:
        return [f"The signing workflow's last success is unreadable: {last_success!r}."]
    silent = hours_between(now, moment) * 60
    if silent > SIGNER_MAX_SILENCE_MINUTES:
        return [
            f"The signing workflow has not succeeded for {silent / 60:.1f}h. It runs every "
            "20 minutes: it is failing, or it is not being started at all, and those have "
            "different causes."
        ]
    return []


def evaluate(sources: dict, now: datetime.datetime, verify, signer_last_success: str) -> list:
    """Every finding across every origin. An empty list means healthy."""
    findings = []
    for name in sorted(sources):
        findings.extend(check_origin(name, sources[name], now, verify))
    findings.extend(check_concordance(sources))
    findings.extend(check_signer(signer_last_success, now))
    return findings


def emit(name: str, value: str) -> None:
    """Publish a step output when running under Actions, else print it."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        print(f"{name}={value}")
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> None:
    # name=path pairs: the workflow fetches, this judges. Keeping the network
    # out of here is what makes the judgement testable, and it is also what
    # lets a failed fetch arrive as an empty file — a finding — rather than as
    # an exception that ends the watch.
    sources = {}
    for argument in sys.argv[1:]:
        name, _, path = argument.partition("=")
        candidate = pathlib.Path(path)
        sources[name] = unpack(candidate.read_bytes() if candidate.is_file() else b"")
    if not sources:
        print("::error::no origins to watch; the workflow passed nothing")
        sys.exit(1)

    key = os.environ.get("VENDOR_PUBLIC_KEY_FILE", "")
    findings = evaluate(
        sources,
        datetime.datetime.now(datetime.timezone.utc),
        openssl_verifier(pathlib.Path(key) if key else None),
        os.environ.get("SIGNER_LAST_SUCCESS", ""),
    )

    for finding in findings:
        print(f"::warning::{finding}")
    for name in sorted(sources):
        served = sources[name]
        print(
            f"{name}: {served['bytes']}B"
            + (f", signed {served['iat']}, valid until {served['exp']}" if "exp" in served else "")
        )

    report = pathlib.Path(
        os.environ.get("REPORT_PATH")
        or pathlib.Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "roster-watch.md"
    )
    if findings:
        report.write_text(
            "What the licence origins are serving does not look right.\n\n"
            + "".join(f"- {finding}\n" for finding in findings)
            + "\nThis check runs as its own workflow, on its own schedule, and reads the\n"
            "artefacts clients fetch. It exists because the signing workflow's own alarm\n"
            "is the last step of the job that signs: a job that fails earlier, or never\n"
            "starts, cannot raise it. That is how the last outage stayed invisible for\n"
            "thirteen hours.\n\n"
            "## What to check\n\n"
            "- Recent runs of `Licence roster` — failing, or not starting? Different causes.\n"
            "- `gh workflow run license-roster.yml --repo kodflow/ktn` signs immediately.\n"
            "- If only one origin disagrees, it is a cache; if all agree and are stale,\n"
            "  nothing is publishing.\n"
        )
    emit("alarm", "true" if findings else "false")
    emit("findings", str(len(findings)))
    emit("report", str(report) if findings else "")
    print(f"{len(findings)} finding(s) across {len(sources)} origin(s)")


if __name__ == "__main__":
    main()
