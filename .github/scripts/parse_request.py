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
import os
import pathlib
import re
import struct
import sys

# state.py owns where licence state lives and how it is keyed. Imported by
# directory rather than as a package because these are scripts, not a module
# tree, and they are loaded by path both by the workflow and by the tests.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import state  # noqa: E402  (the path has to be set before this can resolve)


def licenses_dir() -> pathlib.Path:
    """Where licence state lives; see state.licenses_dir."""
    return state.licenses_dir()

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
# A GitHub login: alphanumerics and single interior hyphens, 39 characters max.
# Used only to check the SHAPE of a claimed CI owner — this script cannot ask
# GitHub whether that account exists, let alone whether the requester belongs
# to it, which is exactly why a claim is never an entitlement.
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def max_devices(account_key: str) -> int:
    """How many devices this account's licence authorises.

    A per-account override lives in account-quotas.json, keyed by NUMERIC
    account id, so a team licence can be widened without touching this file. A
    quota is a property of the ACCOUNT, not of one issue, which is why it is
    not an `expireAt:`-style label: a label applies to the request it sits on
    and would have to be repeated, correctly, on every future device request.

    Keyed by id because the login-keyed predecessor handed a negotiated seat
    count to whoever registered a released handle. The legacy file is still
    read through state.account_quota so an un-migrated branch keeps working.
    """
    recorded = state.account_quota(licenses_dir(), account_key, DEFAULT_MAX_DEVICES)
    # A malformed entry must not silently widen the quota to something
    # unbounded, nor narrow it to zero and lock the account out.
    #
    # bool is excluded explicitly because it is a subclass of int in Python:
    # a quota of `true` would otherwise pass validation and then behave as 1,
    # silently cutting an account down to a single device.
    if isinstance(recorded, bool) or not isinstance(recorded, int) or recorded < 1:
        fail(f"an invalid quota is recorded for account {account_key}: {recorded!r}")
    return recorded


def fail(message: str) -> None:
    """Abort loudly so the maintainer sees why an approval did not take."""
    print(f"::error::{message}")
    sys.exit(1)


def recorded_account_id(account: str) -> str:
    """The numeric id this login was enrolled under, or "" if none was captured.

    Licences issued before accounts.json existed have no id. Their devices keep
    working; what they cannot do is have a NEW request attributed, which
    refuse_unattributable_state below is what enforces.
    """
    entry = state.accounts(licenses_dir()).get(account, {})
    return str(entry.get("id", "") or "")


def refuse_unattributable_state(author: str, author_id: str) -> None:
    """Stop when this login holds state that no numeric id stands behind.

    The hole this closes, in order: a login is released; somebody else
    registers it; their request carries a real numeric id, so the write-once id
    guard has nothing to compare and passes. Keyed by id their account then
    looks BRAND NEW — no devices counted against the quota, and no term on
    record, which is the one path that starts a clock. The previous customer's
    seats and their remaining year, handed over with no error anywhere.

    The same shape is also an ordinary rename of the original customer, and
    nothing in this repository can tell the two apart: the distinguishing fact
    is in a billing record. So the request stops here and names the exact
    command that resolves it. The account's published devices are untouched and
    keep being signed into every roster meanwhile — this refuses one approval,
    not a licence.
    """
    holdings = state.unresolved_holdings(licenses_dir(), author)
    if not holdings:
        return
    held = []
    if holdings.get("devices"):
        held.append(f"{len(holdings['devices'])} device binding(s)")
    if holdings.get("term"):
        held.append("a recorded term")
    if holdings.get("quota"):
        held.append("a negotiated quota")
    fail(
        f"@{author} holds {', '.join(held)} that were recorded before this chain captured "
        f"numeric account ids, so nothing on record says whether account {author_id} is the "
        "account that bought them. A GitHub login can be released and registered by someone "
        "else, so attributing them would be a guess that hands a paid licence to a stranger. "
        "Its published devices are unaffected and keep working. Resolve it with: "
        f"migrate_state_keys.py --resolve {author}=<the numeric id of the buying account>"
    )


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


def optional_section(body: str, heading: str) -> str:
    """The first non-empty line under a heading, or "" when there is no heading.

    section() fails on a missing heading, which is right for the two fields a
    request cannot omit. This is for a field whose ABSENCE is the common case
    and carries meaning of its own: no claim was made, so there is nothing to
    resolve. An empty section is the same statement as a missing one — the
    issue form emits the heading with `_No response_` under it when the input
    is left blank.
    """
    lines = body.replace("\r\n", "\n").split("\n")
    start = next(
        (i for i, l in enumerate(lines) if l.strip().lower() == f"### {heading}".lower()),
        None,
    )
    if start is None:
        return ""
    for line in lines[start + 1:]:
        stripped = line.strip().strip("`")
        if not stripped or stripped.startswith("###"):
            continue
        # What GitHub's issue forms write for an untouched optional input.
        if stripped.lower() in ("_no response_", "*no response*", "no response"):
            return ""
        return stripped
    return ""


def ci_owner_claim(body: str, author: str) -> str:
    """The CI owner the request asks for, or "" when it asks for nothing.

    This is the ONE place the requester≠beneficiary case becomes visible before
    a roster is built. A CI run identifies itself by the owner of the
    repository it runs in — `repository_owner_id` in the OIDC token — and an
    issue is opened by a PERSON. For a customer whose repositories belong to an
    organisation those two ids are different numbers, so an entitlement keyed
    on the requester authorises nothing and no error says so: the run simply
    never matches.

    So the request may NAME the owner it means, and that name is recorded as a
    CLAIM. It is never turned into an entitlement here, because nothing in this
    repository can check it: "I speak for this organisation" is not verifiable
    from an issue body, and a claim that granted CI would let anyone write
    `ci_owner: some-big-org` and be covered for every repository that org owns.
    A maintainer resolves it, or it stays unresolved and is reported as such.

    Naming yourself is not a claim: it is what happens by default, so it is
    dropped rather than left to be resolved by hand for no reason.
    """
    claimed = optional_section(body, "CI owner")
    if not claimed:
        return ""
    # A numeric id is accepted too — it is literally what the token carries,
    # and a maintainer who already knows it should not have to round-trip
    # through a login.
    if not (claimed.isdigit() or LOGIN_RE.match(claimed)):
        fail(
            f"CI owner {claimed!r} is neither a GitHub login nor a numeric account id. "
            "Leave it blank if your CI runs under your own account."
        )
    if claimed.lower() == author.lower():
        return ""
    print(
        f"::warning::this request asks that CI be covered for {claimed!r}, which is not "
        f"@{author}. Recorded as a CLAIM, not a grant: a CI run is matched on the numeric "
        "owner of the repository it runs in, and nothing here can verify that @"
        f"{author} speaks for {claimed!r}. Until a maintainer applies a "
        "`ciOwner:<numeric-id>` label this account gets NO CI entitlement — publishing the "
        "requester's own id instead would be an entitlement that silently never matches."
    )
    return claimed


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
    # The guard above only bites for a login that HAS an id on record. This one
    # covers the accounts it cannot see: those enrolled before ids were
    # captured, whose state has no id behind it to compare at all.
    refuse_unattributable_state(author, author_id)

    uuid = section(body, "Subject")
    if not UUID_RE.match(uuid):
        fail(f"subject {uuid!r} is not a canonical v4 uuid")

    key = section(body, "Public key")
    if not KEY_RE.match(key):
        fail("public key is not a single ssh-ed25519 authorized-keys line")
    validate_ed25519(key)

    # From here on the account is identified by its numeric id, which the two
    # guards above have established IS this requester's. The login is only used
    # to address the human in a message.
    account_key = author_id

    # Ownership is what stops a takeover: anyone may request a subject, but a
    # subject already bound to someone else may only be rotated by that
    # account. Without this check an issue claiming a known uuid would swap
    # the key and hijack the licence.
    #
    # Compared by account id, not by login. Comparing logins meant that after a
    # rename the original owner was refused their own device, and — the way
    # that mattered — whoever registered the released handle was accepted for
    # it.
    recorded = state.device_owner(licenses_dir(), uuid)
    if recorded and recorded != account_key:
        holder = state.logins_for_key(licenses_dir(), recorded)
        named = f"@{holder[-1]}" if holder else f"account {recorded}"
        fail(f"subject {uuid} belongs to {named}, not @{author} (account {account_key})")

    # The device quota, checked here rather than left to the issue workflow
    # alone: the dedupe job only sees issues opened through the form, and this
    # is what makes the rule true regardless of how an issue got made.
    #
    # Seats are counted from the PUBLISHED KEYS, never from the bindings.
    # Revocation deliberately leaves the uuid bound to its original owner so
    # the identity cannot be squatted afterwards, so counting bindings would
    # count every device the account ever had — three revocations and the
    # licence would be dead with no way to say why.
    active = sorted(state.active_devices(licenses_dir(), account_key))
    # An already-published device is a rotation, not a new seat: it is
    # replacing its own key, so it must not be counted against the quota it
    # already occupies.
    allowed = max_devices(account_key)
    if uuid not in active and len(active) >= allowed:
        fail(
            f"@{author} already has {len(active)} active device(s) "
            f"({', '.join(active)}) and the licence allows {allowed}. "
            "Revoke one before enrolling another."
        )

    # Read AFTER the device checks so a malformed claim cannot be used to
    # probe ownership or the quota: by here the request is already one this
    # account is allowed to make.
    claimed_ci_owner = ci_owner_claim(body, author)

    pathlib.Path("/tmp/subject.pub").write_text(key.rstrip() + "\n")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"uuid={uuid}\n")
        out.write(f"ci_owner={claimed_ci_owner}\n")
    print(f"accepted subject {uuid} for @{author} ({len(active)} device(s) already active)")


if __name__ == "__main__":
    main()
