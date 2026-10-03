"""Which outside identity provider vouches for which PulseSoc member.

One table for every provider, keyed on the pair ``(provider,
provider_subject)``. Apple is provider #1 and Google is provider #2, and a third
would add a row type, not a system. The alternative — ``apple_users`` beside
``google_users`` — is what makes "this member signs in three ways" unanswerable
and makes each new provider a rewrite of the login path.

**The identity is the subject, never the email.** This is the single rule the
rest of the module exists to enforce, so it is worth saying why rather than
asserting it:

* An email address is a *contact attribute* the provider happens to disclose.
  It changes. Google accounts change primary address; Apple's Hide My Email
  issues a relay address that the member can disable, and re-enable as a
  different one. Keyed on email, every one of those events silently orphans an
  account or silently merges two.
* Worse in the other direction: an attacker who can get a provider to assert an
  email they do not own — or who simply registers the address at a provider the
  victim does not use — would inherit the victim's PulseSoc account. The email
  is an *unauthenticated claim about a third system*. ``sub`` is the provider
  stating which of its own accounts this is, which is the only thing it is
  actually in a position to know.

So ``UNIQUE(provider, provider_subject)``, and the email columns here are
descriptive only. Nothing in :func:`resolve` reads them to decide *who* someone
is — they decide only whether an account has to be *created* or whether a human
has to be asked.

**An email collision is never resolved automatically.** When a federated sign-in
presents an address that already belongs to a PulseSoc account, the answer is
``link_required``: the member proves control of the existing account the normal
way, and only then is the identity attached. Auto-linking on email equality is
account takeover with extra steps, and it is the default behaviour of most
hand-rolled social login, which is why :func:`resolve` returns a decision for a
caller to act on rather than a user id.

``UNIQUE(user_id, provider)`` is the second constraint and it is not cosmetic:
without it one member accumulates several Apple subjects, and "unlink Apple"
stops having a single meaning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from services import db
from services.account_email_uniqueness import EMAIL_IDENTITY_EXPRESSION

#: Providers this table accepts. Apple first, Google second, one system.
PROVIDERS = ("apple", "google")

#: Human labels, for audit lines and for the account-connections screen.
PROVIDER_LABELS = {"apple": "Apple", "google": "Google"}

#: What :func:`resolve` can conclude.
#:
#: * ``sign_in``      -- this subject is already on file; `user_id` is the member.
#: * ``create``       -- nobody holds this subject or this email; make an account.
#: * ``link_required``-- the email belongs to an existing account. Ask the human.
#: * ``refused``      -- the provider did not say enough to proceed safely.
DECISIONS = ("sign_in", "create", "link_required", "refused")

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

#: Apple's relay domain. A relay address is deliverable only while the member
#: keeps the app authorised, so it is recorded rather than treated as a durable
#: way to reach someone.
APPLE_PRIVATE_RELAY_DOMAIN = "privaterelay.appleid.com"


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_TIME_FORMAT)


def normalize_email(value) -> str:
    """``lower(trim(...))`` — the same identity the users-table index uses."""

    return str(value or "").strip().lower()


def is_private_relay(email) -> bool:
    return normalize_email(email).endswith("@" + APPLE_PRIVATE_RELAY_DOMAIN)


def ensure_schema(conn=None) -> None:
    """Create the table. Call from `init_db()` only.

    Same reason as `legal_acceptance.ensure_schema`: the sign-in routes write
    here from inside an open transaction on their own connection, so building it
    on demand from a second connection blocks on a lock the caller holds and
    fails the sign-in.
    """

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_external_identities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                provider_subject TEXT NOT NULL,
                provider_email TEXT NOT NULL DEFAULT '',
                provider_email_verified INTEGER NOT NULL DEFAULT 0,
                is_private_relay INTEGER NOT NULL DEFAULT 0,
                display_name TEXT NOT NULL DEFAULT '',
                linked_source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL DEFAULT '',
                UNIQUE(provider, provider_subject),
                UNIQUE(user_id, provider))"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_external_identities_user "
            "ON user_external_identities (user_id)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


_SELECT = (
    "SELECT id, user_id, provider, provider_subject, provider_email, "
    "provider_email_verified, is_private_relay, display_name, linked_source, "
    "created_at, last_login_at FROM user_external_identities "
)


def _row_to_dict(row) -> dict:
    (
        row_id,
        user_id,
        provider,
        subject,
        email,
        email_verified,
        private_relay,
        display_name,
        linked_source,
        created_at,
        last_login_at,
    ) = db.row_values(row)
    return {
        "id": int(row_id),
        "user_id": int(user_id),
        "provider": str(provider or ""),
        "provider_subject": str(subject or ""),
        "provider_email": str(email or ""),
        "provider_email_verified": bool(email_verified),
        "is_private_relay": bool(private_relay),
        "display_name": str(display_name or ""),
        "linked_source": str(linked_source or ""),
        "created_at": str(created_at or ""),
        "last_login_at": str(last_login_at or ""),
    }


def lookup(provider: str, subject: str, conn=None) -> dict | None:
    """The identity row for this provider subject, or None.

    This is the whole of "who is signing in". No email is consulted.
    """

    provider = str(provider or "").strip().lower()
    subject = str(subject or "").strip()
    if provider not in PROVIDERS or not subject:
        return None
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = conn.execute(
            _SELECT + "WHERE provider=? AND provider_subject=?", (provider, subject)
        ).fetchone()
    finally:
        if owned:
            conn.close()
    return _row_to_dict(row) if row is not None else None


def for_user(user_id, conn=None) -> list[dict]:
    """Every provider linked to this member, for the connections screen."""

    if not user_id:
        return []
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            _SELECT + "WHERE user_id=? ORDER BY provider", (int(user_id),)
        ).fetchall()
    finally:
        if owned:
            conn.close()
    return [_row_to_dict(row) for row in rows]


def accounts_matching_email(email, conn=None) -> list[int]:
    """Member ids whose address is this one, by the users index's own identity.

    Returns a *list* rather than one id on purpose. `ensure_email_identity_index`
    is allowed to fail to build when duplicates already exist, so this can
    legitimately return several — and a federated sign-in that found several
    accounts must never pick one.
    """

    normalized = normalize_email(email)
    if not normalized:
        return []
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            f"SELECT user_id FROM users WHERE {EMAIL_IDENTITY_EXPRESSION}=? ORDER BY user_id",
            (normalized,),
        ).fetchall()
    finally:
        if owned:
            conn.close()
    return [int(db.row_values(row)[0]) for row in rows]


def link(
    cur,
    user_id,
    *,
    provider: str,
    subject: str,
    email: str = "",
    email_verified: bool = False,
    display_name: str = "",
    source: str = "",
) -> None:
    """Attach a provider identity to a member, on the caller's cursor.

    On the caller's cursor so the link commits with whatever made it legitimate
    — the account creation, or the password check of the linking ceremony. A
    link that survives a rolled-back authorisation is a standing credential
    nobody authorised.

    Raises on a subject already held by a *different* member. That is not a
    recoverable condition to paper over: it means two PulseSoc accounts are
    claiming one Apple or Google account, and silently repointing the row would
    hand the second member the first member's sign-in.
    """

    provider = str(provider or "").strip().lower()
    subject = str(subject or "").strip()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {PROVIDERS}")
    if not subject:
        raise ValueError("a federated identity needs the provider's subject")
    if not user_id:
        raise ValueError("a federated identity needs the member it belongs to")

    held = cur.execute(
        "SELECT user_id FROM user_external_identities WHERE provider=? AND provider_subject=?",
        (provider, subject),
    ).fetchone()
    if held is not None:
        holder = int(db.row_values(held)[0])
        if holder != int(user_id):
            raise ValueError(
                f"{provider} subject already linked to member {holder}"
            )
        cur.execute(
            "UPDATE user_external_identities SET provider_email=?, provider_email_verified=?, "
            "is_private_relay=?, display_name=?, last_login_at=? "
            "WHERE provider=? AND provider_subject=?",
            (
                normalize_email(email),
                1 if email_verified else 0,
                1 if is_private_relay(email) else 0,
                str(display_name or "")[:120],
                _now(),
                provider,
                subject,
            ),
        )
        return

    cur.execute(
        "INSERT INTO user_external_identities "
        "(user_id, provider, provider_subject, provider_email, provider_email_verified, "
        "is_private_relay, display_name, linked_source, created_at, last_login_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            int(user_id),
            provider,
            subject,
            normalize_email(email),
            1 if email_verified else 0,
            1 if is_private_relay(email) else 0,
            str(display_name or "")[:120],
            str(source or "")[:60],
            _now(),
            _now(),
        ),
    )


def touch_login(provider: str, subject: str, conn=None) -> None:
    """Record that this identity signed in. Best effort; never fails a sign-in."""

    try:
        owned = conn is None
        if owned:
            conn = db.connect()
        try:
            conn.execute(
                "UPDATE user_external_identities SET last_login_at=? "
                "WHERE provider=? AND provider_subject=?",
                (_now(), str(provider or "").lower(), str(subject or "")),
            )
            if owned:
                conn.commit()
        finally:
            if owned:
                conn.close()
    except Exception as exc:
        logging.warning("EXTERNAL_IDENTITY_TOUCH_FAILED provider=%s error=%s", provider, exc)


def can_unlink(user_id, provider: str, *, has_password: bool, conn=None) -> tuple[bool, str]:
    """Whether removing this provider still leaves a way in.

    A member who signed up through Apple has no password. Unlinking Apple would
    leave an account nobody — including its owner — can authenticate to, and
    there is no recovery path because password reset needs a password to set
    *and* the email may be an Apple relay that stops forwarding. So the last
    remaining credential cannot be removed; the member is told to set a password
    first. Refusing is recoverable, locking someone out is not.
    """

    provider = str(provider or "").strip().lower()
    linked = {entry["provider"] for entry in for_user(user_id, conn)}
    if provider not in linked:
        return False, "not_linked"
    if has_password:
        return True, ""
    if len(linked) > 1:
        return True, ""
    return False, "last_credential"


def unlink(cur, user_id, provider: str) -> bool:
    """Detach a provider from a member. Returns whether a row went away.

    Deliberately does *not* re-check :func:`can_unlink` — the caller holds the
    session and knows whether a password exists. Two checks in two places drift;
    the route is where authorisation lives.
    """

    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS or not user_id:
        return False
    # The row count comes off the cursor `execute` returns, not off `cur` --
    # which may be a connection, and a connection has no `rowcount`, so reading
    # it there silently reports "deleted nothing" for a delete that happened.
    result = cur.execute(
        "DELETE FROM user_external_identities WHERE user_id=? AND provider=?",
        (int(user_id), provider),
    )
    return bool(getattr(result, "rowcount", 0) or 0)


def resolve(provider: str, profile: dict, conn=None) -> dict:
    """Decide what a validated provider assertion means. Does not write anything.

    `profile` is what the adapter extracted from a token it has *already*
    verified — this function assumes the signature, issuer, audience, expiry and
    nonce all checked out, and is only about identity. Calling it with
    unverified claims would make every decision below meaningless.

    Separate from the route so the ladder is testable without a request, a
    session, or a provider. The route's job is the parts this cannot know:
    whether the member is banned, and whether they owe a legal acceptance.
    """

    provider = str(provider or "").strip().lower()
    subject = str(profile.get("subject") or "").strip()
    email = normalize_email(profile.get("email"))
    email_verified = bool(profile.get("email_verified"))

    if provider not in PROVIDERS:
        return {"decision": "refused", "reason": "unknown_provider", "user_id": 0}
    if not subject:
        return {"decision": "refused", "reason": "missing_subject", "user_id": 0}

    existing = lookup(provider, subject, conn)
    if existing:
        # The only identity question there is. Whatever the email says now —
        # changed, relayed, withdrawn — this is the same provider account that
        # was linked, so it is the same member.
        return {
            "decision": "sign_in",
            "reason": "known_subject",
            "user_id": existing["user_id"],
            "identity": existing,
        }

    if not email:
        # First sight of this subject and no address to create an account with.
        # Apple discloses the email only on the first authorisation, so this is
        # reachable: a member who authorised, was never recorded here, and came
        # back. Inventing a placeholder address would create an account that
        # cannot receive a password reset or a security notice.
        return {"decision": "refused", "reason": "provider_email_missing", "user_id": 0}

    matches = accounts_matching_email(email, conn)
    if not matches:
        return {
            "decision": "create",
            "reason": "new_member",
            "user_id": 0,
            "email": email,
            "email_verified": email_verified,
        }

    # An address this platform already knows. Email equality is not proof of
    # anything: the provider is asserting an address, not control of a PulseSoc
    # account. Hand it back for the linking ceremony.
    return {
        "decision": "link_required",
        "reason": "email_belongs_to_existing_account",
        "user_id": 0,
        "email": email,
        "email_verified": email_verified,
        "candidate_count": len(matches),
    }
