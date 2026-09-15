#!/usr/bin/env python3
"""Extract and validate a licence request from an issue body.

Everything in the body is attacker-controlled: anyone can open an issue. The
approval label gates *whether* we act, this script gates *what* we accept, so
a malformed or hostile payload can never reach the roster.

One account still holds exactly ONE licence — that is the billing unit and it
does not change. What changed is that a licence now authorises several
DEVICES: a laptop, a desktop, a work machine. Each device carries its own
keypair and its own subject uuid, and the private half never travels, which
is the property the whole scheme rests on. Before this, "one licence" and
"one machine" were the same sentence, so a second machine could only be
served by copying a private key — the one thing the design forbids — or by
not being served at all.
"""
import base64
import binascii
import json
import os
import pathlib
import re
import struct
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

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# How many devices one licence authorises. Three covers the shape almost every
# developer actually has — laptop, desktop, and one more — without turning a
# personal licence into a site licence.
DEFAULT_MAX_DEVICES = 3
# Only ed25519 is accepted: it is what `ktn-linter license create` mints, and
# narrowing the accepted algorithms narrows what the verifier must handle.
#
# The shape is the first gate, not the only one: validate_ed25519() below reads
# the actual wire format. A line can match this pattern perfectly and still
# decode to something no SSH implementation would call a key.
#
# The trailing group is the COMMENT, and it is deliberately permissive: this
# used to allow exactly one token, while the comment `ktn-linter license
# create` writes is `ktn-linter licence <uuid>` — three. Anyone pasting the
# line the tool actually prints, which is what the request form asks for, was
# told their key "is not a single ssh-ed25519 authorized-keys line". Nothing
# reads the comment: the fingerprint is taken from the blob alone, and the
# subject uuid comes from its own section.
KEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]+={0,3}(\s+\S.*)?$")
# RFC 8709: the blob is the algorithm name and then the 32-byte key, each
# length-prefixed, and nothing else.
ED25519_ALGORITHM = b"ssh-ed25519"
ED25519_KEY_BYTES = 32


def max_devices(account: str) -> int:
    """How many devices this account's licence authorises.

    A per-account override lives in quotas.json, keyed by GitHub login, so a
    team licence can be widened without touching this file. A quota is a
    property of the ACCOUNT, not of one issue, which is why it is not an
    `expireAt:`-style label: a label applies to the request it sits on and
    would have to be repeated, correctly, on every future device request.
    """
    path = licenses_dir() / "quotas.json"
    # No file is the normal state: every account is on the default until one
    # of them is not.
    if not path.exists():
        return DEFAULT_MAX_DEVICES
    quotas = json.loads(path.read_text() or "{}")
    recorded = quotas.get(account, DEFAULT_MAX_DEVICES)
    # A malformed entry must not silently widen the quota to something
    # unbounded, nor narrow it to zero and lock the account out.
    #
    # bool is excluded explicitly because it is a subclass of int in Python:
    # a quota of `true` would otherwise pass validation and then behave as 1,
    # silently cutting an account down to a single device.
    if isinstance(recorded, bool) or not isinstance(recorded, int) or recorded < 1:
        fail(f"quotas.json holds an invalid quota for @{account}: {recorded!r}")
    return recorded


def fail(message: str) -> None:
    """Abort loudly so the maintainer sees why an approval did not take."""
    print(f"::error::{message}")
    sys.exit(1)


def recorded_account_id(account: str) -> str:
    """The numeric id this login was enrolled under, or "" if none was captured.

    Licences issued before accounts.json existed have no id. They keep working
    as devices and get one on their next approval; see build_roster.py.
    """
    path = licenses_dir() / "accounts.json"
    if not path.exists():
        return ""
    entry = json.loads(path.read_text() or "{}").get(account, {})
    return str(entry.get("id", "") or "")


def ssh_string(blob: bytes, offset: int) -> tuple:
    """Read one length-prefixed SSH string, or raise ValueError.

    The length prefix is attacker-supplied, so it is checked against what is
    actually there before it is used to slice: a declared length of four
    gigabytes must be a refusal, not an allocation.
    """
    if offset + 4 > len(blob):
        raise ValueError("truncated length prefix")
    (length,) = struct.unpack(">I", blob[offset : offset + 4])
    end = offset + 4 + length
    if end > len(blob):
        raise ValueError(f"declared {length} bytes, only {len(blob) - offset - 4} present")
    return blob[offset + 4 : end], end


def validate_ed25519(key: str) -> None:
    """Refuse anything that is not a real ssh-ed25519 public key.

    The shape check above looks at the line; this looks at the bytes. The gap
    between the two was not cosmetic: build_roster.py fingerprints the decoded
    blob, and `base64.b64decode` without validate=True DISCARDS characters it
    does not recognise rather than objecting. A blob accepted here could
    therefore be fingerprinted as something else entirely — or fail to decode
    at all and crash the roster build, which does not block one licence, it
    blocks every signature after it.

    So the structure is checked at the gate, where a refusal costs one approval
    and names the problem, instead of at build time, where it costs the whole
    parc.
    """
    try:
        blob = base64.b64decode(key.split()[1], validate=True)
    except (binascii.Error, ValueError, IndexError):
        fail("public key is not valid base64")
    try:
        algorithm, offset = ssh_string(blob, 0)
        material, offset = ssh_string(blob, offset)
    except ValueError as problem:
        fail(f"public key is not a well-formed SSH key blob: {problem}")
    if algorithm != ED25519_ALGORITHM:
        fail(
            f"public key blob declares {algorithm!r}, not {ED25519_ALGORITHM!r}: the line "
            "and the bytes disagree about the algorithm"
        )
    if len(material) != ED25519_KEY_BYTES:
        fail(
            f"public key carries {len(material)} bytes of key material, not "
            f"{ED25519_KEY_BYTES}: this is not an ed25519 key"
        )
    if offset != len(blob):
        fail(f"public key has {len(blob) - offset} trailing byte(s) after the key material")


def section(body: str, heading: str) -> str:
    """Return the first non-empty line under a '### heading' block."""
    lines = body.replace("\r\n", "\n").split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().lower() == f"### {heading}".lower())
    except StopIteration:
        fail(f"missing '### {heading}' section")
    for line in lines[start + 1:]:
        stripped = line.strip().strip("`")
        if stripped and not stripped.startswith("###"):
            return stripped
    fail(f"empty '### {heading}' section")
    return ""


def main() -> None:
    body = os.environ.get("BODY", "")
    author = os.environ.get("AUTHOR", "")
    if not author:
        fail("issue has no author")

    # The login is a label; the numeric id is the identity. A login can be
    # released and registered by somebody else, so a request arriving under a
    # known login with a DIFFERENT id is not that account — it is whoever holds
    # the handle now, and everything keyed on the login (the term, the quota,
    # the devices already enrolled, the CI seat) would otherwise pass to them.
    #
    # Refused here rather than reconciled: which of the two it is — a renamed
    # account or a recycled handle — has a billing answer, and an approval run
    # is not where it gets guessed.
    author_id = os.environ.get("AUTHOR_ID", "")
    if not author_id.isdigit():
        fail(
            f"no numeric account id for @{author} ({author_id!r}). The approval chain "
            "identifies an account by id, never by login; the workflow passes "
            "github.event.issue.user.id."
        )
    enrolled_as = recorded_account_id(author)
    if enrolled_as and enrolled_as != author_id:
        fail(
            f"@{author} is account {author_id}, but that login is enrolled as account "
            f"{enrolled_as}. GitHub releases logins and does not reissue ids: this request "
            "is either a renamed account or a different person on a freed handle, and the "
            "second must not inherit the first one's licence. A maintainer has to decide "
            "which before this can be approved."
        )

    uuid = section(body, "Subject")
    if not UUID_RE.match(uuid):
        fail(f"subject {uuid!r} is not a canonical v4 uuid")

    key = section(body, "Public key")
    if not KEY_RE.match(key):
        fail("public key is not a single ssh-ed25519 authorized-keys line")
    validate_ed25519(key)

    # Ownership is what stops a takeover: anyone may request a subject, but a
    # subject already bound to someone else may only be rotated by that
    # account. Without this check an issue claiming a known uuid would swap
    # the key and hijack the licence.
    owners_path = licenses_dir() / "owners.json"
    owners = json.loads(owners_path.read_text() or "{}") if owners_path.exists() else {}

    recorded = owners.get(uuid)
    if recorded is not None and recorded != author:
        fail(f"subject {uuid} belongs to @{recorded}, not @{author}")

    # The device quota, checked here rather than left to the issue workflow
    # alone: the dedupe job only sees issues opened through the form, and this
    # is what makes the rule true regardless of how an issue got made.
    #
    # Seats are counted from the PUBLISHED KEYS, never from owners.json.
    # Revocation deliberately leaves the uuid bound to its original owner so
    # the identity cannot be squatted afterwards, so counting owners.json
    # entries would count every device the account ever had — three
    # revocations and the licence would be dead with no way to say why.
    active = sorted(
        existing
        for existing, owner in owners.items()
        if owner == author and (licenses_dir() / f"{existing}.pub").is_file()
    )
    # An already-published device is a rotation, not a new seat: it is
    # replacing its own key, so it must not be counted against the quota it
    # already occupies.
    if uuid not in active and len(active) >= max_devices(author):
        fail(
            f"@{author} already has {len(active)} active device(s) "
            f"({', '.join(active)}) and the licence allows {max_devices(author)}. "
            "Revoke one before enrolling another."
        )

    pathlib.Path("/tmp/subject.pub").write_text(key.rstrip() + "\n")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"uuid={uuid}\n")
    print(f"accepted subject {uuid} for @{author} ({len(active)} device(s) already active)")


if __name__ == "__main__":
    main()
