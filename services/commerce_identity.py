"""Who owns a cart -- one key, whether or not there is an account behind it.

WHAT WAS ACTUALLY WRONG
-----------------------
``marketplace_cart_items`` is keyed ``UNIQUE (user_id, listing_id)`` and
``user_id`` is nullable. Verified against production rather than assumed:
PostgreSQL 18.6, one plain b-tree ``marketplace_cart_items_user_id_listing_id_key``,
``user_id integer YES``, four rows, all four with a user. The routes write
``int(user["user_id"])`` at every site, so that nullability is currently
unreachable -- today a cart belongs to an account or it does not exist.

Open Commerce needs a guest to have a cart, and the obvious move is to let
``user_id`` stay NULL for one. That silently deletes the constraint. In SQL NULL
is not equal to NULL, including inside a unique index, so two guest rows for the
same listing never conflict -- and the entire add-to-cart path is built on the
assumption that they do. Its own comment says so, at
``marketplace_cart_routes.py``:

    # A duplicate tap must not duplicate the line: the UNIQUE constraint
    # turns the second add into a quantity update.

With a NULL owner, that ``ON CONFLICT(user_id, listing_id)`` matches nothing. A
double-tap inserts a second line instead of incrementing the first, and the
guest's cart shows one product twice at a total they never chose. The clamp to
``MAX_QTY_PER_LINE`` goes with it, since the thing being clamped is the quantity
on the row that was supposed to be found.

This is *not* one of the engine asymmetries that fill this repo. SQLite and
PostgreSQL agree that NULLs are distinct, so a local test can prove the bug and
prove the fix -- which is why there is one. It does rule out the fix only
production can express: PostgreSQL 15 added ``UNIQUE NULLS NOT DISTINCT``,
SQLite has nothing like it, and with no migration framework here one DDL string
has to be correct on both engines. Reaching for it would be the ``btrim``
mistake again.

SO THE OWNER IS A VALUE, NOT AN ABSENCE
---------------------------------------
``owner_key`` is a non-null string naming whoever the cart belongs to::

    signed in   u:<user_id>
    guest       g:<sha256 of the guest token>

Uniqueness becomes ``(owner_key, listing_id)``: an ordinary constraint over
ordinary values, no NULL semantics involved, identical on both engines, and one
code path for both kinds of shopper. That last part is the point of §3 -- a
guest cart is not ``if (!user)`` bolted to the side of a real one, it is the
same cart with a different owner.

THE GUEST HANDLE IS THE FLASK SESSION, ON PURPOSE
-------------------------------------------------
A guest needs somewhere to keep an identifier across requests, and this app
already has exactly one such place that an anonymous visitor gets: the Flask
session cookie. It is server-signed and tamper-evident, it already carries the
CSRF token for anonymous visitors (``services/csrf.issue_token`` writes into
``session``, which is what makes a guest POST possible at all), and its key
already has a documented rotation cost in ``services/signing_keys.py``.

A second signed cookie carrying a second opaque id would be a parallel
implementation of that -- its own signature to verify, its own key to derive and
rotate, its own ``httponly``/``secure``/``samesite`` flags to get wrong -- for
nothing the session does not already do. So the token lives in the session.

WHAT THE DATABASE STORES IS NOT WHAT THE COOKIE CARRIES
-------------------------------------------------------
The session holds a 256-bit random token. The cart row holds ``sha256`` of it.
One-way, so a dump of ``marketplace_cart_items``, or a log line, or a BI export,
yields nothing replayable as a cookie to read somebody's cart back. It is the
same shape as ``commerce_discovery``'s ``subject_ref`` and for the same stated
reason: the key only ever needs to be *stable*, never reversible.

A plain SHA-256 rather than an HMAC is deliberate, and it is the reasoning this
repo already applies to refresh tokens in ``mobile_security_sessions``: the
input is ``secrets.token_urlsafe(32)``, so there is no low-entropy guess for a
key to protect and nothing to brute-force. Adding one would mean a sixth signing
purpose whose rotation silently empties every in-flight guest cart -- a cost
paid for no threat.

The prefix is the other half, and the more important one. ``owner_key`` is only
ever built in this module, from either an integer user id or a hex digest, so a
guest's key cannot be spelled ``u:1`` whatever the session contains -- and the
session is signed, so it contains nothing the server did not put there. Two
independent reasons a guest cannot address an account's cart; both are tested,
because either one holding alone is a single point of failure.
"""
from __future__ import annotations

import hashlib
import logging
import secrets

from . import db as db_service
from .schema_guard import run_once_per_process

LOGGER = logging.getLogger(__name__)

#: The two owner namespaces. Separate prefixes so the two id spaces -- small
#: integers and 64-character hex digests -- cannot be confused for one another
#: even if one later changes shape.
USER_PREFIX = "u:"
GUEST_PREFIX = "g:"

#: Where the guest's token lives inside the Flask session.
GUEST_SESSION_KEY = "commerce_guest_token"

#: 32 bytes of randomness -> 43 url-safe characters. The minimum length below is
#: checked rather than trusted, because a short token would still hash to a
#: perfectly well-formed 64-hex owner key and the weakness would be invisible.
GUEST_TOKEN_BYTES = 32
MIN_GUEST_TOKEN_LENGTH = 32

TABLE = "marketplace_cart_items"
OWNER_COLUMN = "owner_key"
INDEX_NAME = "ux_cart_owner_line"

CREATE_INDEX_SQL = (
    f"CREATE UNIQUE INDEX {INDEX_NAME} ON {TABLE} ({OWNER_COLUMN}, listing_id)"
)

#: Portable on both engines: ``||`` concatenates and an explicit CAST avoids
#: relying on either dialect's integer-to-text coercion inside it.
BACKFILL_SQL = (
    f"UPDATE {TABLE} SET {OWNER_COLUMN} = '{USER_PREFIX}' || CAST(user_id AS TEXT) "
    f"WHERE {OWNER_COLUMN} IS NULL AND user_id IS NOT NULL"
)


# --- the keys ---------------------------------------------------------------
def owner_key_for_user(user_id) -> str:
    """The cart owner key for a signed-in account.

    Raises on anything that is not a positive integer. A cart keyed to the string
    ``"None"`` -- which is what an f-string does with a missing user id -- would
    be a single shared cart that every failed lookup falls into, and it would
    look like a working feature.
    """
    try:
        value = int(user_id)
    except (TypeError, ValueError):
        raise ValueError(f"refusing to key a cart to a non-numeric user_id {user_id!r}")
    if value <= 0:
        raise ValueError(f"refusing to key a cart to user_id {value!r}")
    return f"{USER_PREFIX}{value}"


def owner_key_for_guest_token(token) -> str:
    """The cart owner key for a guest, from the token in their session."""
    text = str(token or "")
    if len(text) < MIN_GUEST_TOKEN_LENGTH:
        raise ValueError(
            f"refusing to key a cart to a {len(text)}-character guest token; "
            f"a short token hashes to a well-formed owner key and hides its own "
            f"weakness, so the length is checked here rather than assumed"
        )
    return GUEST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_guest_key(owner_key) -> bool:
    return str(owner_key or "").startswith(GUEST_PREFIX)


def is_user_key(owner_key) -> bool:
    return str(owner_key or "").startswith(USER_PREFIX)


def user_id_from_key(owner_key):
    """The account behind a ``u:`` key, or None for a guest key or junk."""
    text = str(owner_key or "")
    if not text.startswith(USER_PREFIX):
        return None
    try:
        return int(text[len(USER_PREFIX):])
    except (TypeError, ValueError):
        return None


# --- the guest handle -------------------------------------------------------
def peek_guest_token(session):
    """The guest's existing token, or None. Never mints one.

    Read paths use this. Minting on a GET would hand a session cookie to every
    crawler, link-preview fetcher and uptime probe that touches a product page,
    which is both a pointless ``Set-Cookie`` on cacheable responses and a
    consent question nobody asked. A guest gets an identity when they first put
    something in a cart, not when they look at one.
    """
    if session is None:
        return None
    try:
        token = session.get(GUEST_SESSION_KEY)
    except Exception:
        LOGGER.exception("COMMERCE_GUEST_SESSION_READ_FAILED")
        return None
    text = str(token or "")
    return text if len(text) >= MIN_GUEST_TOKEN_LENGTH else None


def ensure_guest_token(session) -> str:
    """The guest's token, minting and storing one on first write.

    Replaces a token too short to be trusted rather than using it, so a
    truncated or hand-edited value fails closed into a fresh identity instead of
    into a weak one. The practical effect of a replacement is an abandoned cart,
    not a lost account.
    """
    existing = peek_guest_token(session)
    if existing:
        return existing
    token = secrets.token_urlsafe(GUEST_TOKEN_BYTES)
    session[GUEST_SESSION_KEY] = token
    return token


def forget_guest_token(session) -> None:
    """Drop the guest handle -- called once its cart has been merged into an account.

    Leaving it behind would mean a signed-in shopper still carries a live guest
    identity, so a later sign-out would reveal the merged cart's *source* rows to
    whoever next used that browser. Merging is the end of the guest's life, not a
    copy.
    """
    if session is None:
        return
    try:
        session.pop(GUEST_SESSION_KEY, None)
    except Exception:
        LOGGER.exception("COMMERCE_GUEST_SESSION_CLEAR_FAILED")


def resolve_cart_owner(account_user, session, *, allow_guest: bool, minting: bool):
    """Who this request's cart belongs to.

    Returns ``(owner_key, user_id)``, or ``(None, None)`` when there is nobody to
    attribute a cart to. Deliberately free of Flask response objects: the caller
    owns the wording and the status code, and this stays callable from a test
    with a plain dict for a session.

    ``minting`` separates a write from a read. A read resolves an existing guest
    or answers "nobody"; only a write is allowed to create a guest identity. See
    ``peek_guest_token``.

    A signed-in user always wins over a guest token in the same session. That
    ordering is the one that matters: the inverse would let a stale guest handle
    shadow the account of somebody who has just logged in, and serve them
    somebody else's cart on their own machine.
    """
    if account_user:
        try:
            return owner_key_for_user(account_user.get("user_id")), int(account_user["user_id"])
        except Exception:
            # A signed-in user whose id will not resolve is not a guest. Falling
            # through to the guest branch would silently move a real shopper into
            # an anonymous cart and lose their lines.
            LOGGER.exception("COMMERCE_OWNER_KEY_USER_UNRESOLVABLE")
            return None, None
    if not allow_guest:
        return None, None
    token = ensure_guest_token(session) if minting else peek_guest_token(session)
    if not token:
        return None, None
    return owner_key_for_guest_token(token), None


# --- the constraint ---------------------------------------------------------
def owner_column_present(cur) -> bool:
    try:
        return OWNER_COLUMN in db_service.get_table_columns(cur, TABLE)
    except Exception:
        LOGGER.exception("CART_OWNER_COLUMN_INTROSPECTION_FAILED")
        return False


def index_exists(cur) -> bool:
    """Whether the index is present, asked of each engine's own catalogue.

    Checked rather than relying on ``IF NOT EXISTS`` so the steady state issues no
    DDL and therefore takes no lock. ``CREATE INDEX IF NOT EXISTS`` still takes a
    ShareLock on PostgreSQL when the index already exists, and ShareLock conflicts
    with the RowExclusiveLock an INSERT needs -- ``services/schema_guard.py``
    exists because that pattern once hung half of production, and this function
    is called from a route, which is the worst place to hold it.
    """
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename = ? AND indexname = ? LIMIT 1",
            (TABLE, INDEX_NAME),
        )
    else:
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ? LIMIT 1",
            (INDEX_NAME,),
        )
    return cur.fetchone() is not None


def unkeyed_rows(cur) -> int:
    """Cart lines the backfill could not attribute: no owner key and no user id.

    Production has none. Counted and reported rather than deleted, because a
    cart line is somebody's intent to buy and a boot path may not throw it away
    to make a constraint fit.
    """
    cur.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE {OWNER_COLUMN} IS NULL AND user_id IS NULL"
    )
    row = cur.fetchone()
    return int(db_service.row_values(row)[0]) if row else 0


def colliding_lines(cur) -> list[tuple[str, int, int]]:
    """``(owner_key, listing_id, count)`` for any owner holding one listing twice.

    Impossible today by construction -- the pre-existing ``UNIQUE (user_id,
    listing_id)`` forbids it for account rows, and there are no guest rows yet.
    Checked anyway, because "impossible by construction" is a statement about
    today's constraints and this function's whole job is to not issue DDL the
    data would reject. A failed ``CREATE UNIQUE INDEX`` inside a request's open
    transaction poisons that transaction on PostgreSQL, so the add-to-cart it was
    called from would fail for a reason having nothing to do with the cart.
    """
    cur.execute(
        f"SELECT {OWNER_COLUMN}, listing_id, COUNT(*) FROM {TABLE} "
        f"WHERE {OWNER_COLUMN} IS NOT NULL "
        "GROUP BY 1, 2 HAVING COUNT(*) > 1 ORDER BY 3 DESC"
    )
    out = []
    for row in cur.fetchall():
        values = db_service.row_values(row)
        out.append((str(values[0]), int(values[1]), int(values[2])))
    return out


@run_once_per_process
def ensure_cart_owner_key(cur) -> bool:
    """Add ``owner_key``, backfill it, and constrain it. Returns whether it is ready.

    Never raises. This is reached from ``_ensure_schema`` on the cart routes and
    from ``init_db``, and in ``init_db`` a raise does not fail loudly -- it
    truncates the schema at that line. ``bot.py:119868`` records the occasion
    that left 49 tables of 586.

    Returning ``False`` is a real answer, not a swallowed error: the caller keeps
    writing ``user_id`` as it always has, so a database where this could not run
    behaves exactly as it did yesterday rather than losing carts.
    ``run_once_per_process`` deliberately does not cache a ``False``, so the next
    boot retries.
    """
    try:
        if not owner_column_present(cur):
            cur.execute(f"ALTER TABLE {TABLE} ADD COLUMN {OWNER_COLUMN} TEXT")
            LOGGER.warning("CART_OWNER_COLUMN_ADDED table=%s", TABLE)
        # Idempotent, and cheap once done: after the first pass the WHERE clause
        # matches nothing. Not conditional on the column having just been added,
        # because a previous attempt may have added the column and then failed.
        cur.execute(BACKFILL_SQL)
        if index_exists(cur):
            return True
        orphans = unkeyed_rows(cur)
        if orphans:
            # These rows would sit outside the index rather than break it -- NULLs
            # are exempt from uniqueness. That is precisely why they are worth a
            # log line: an unconstrained cart line is the bug this module exists
            # to remove, and it would otherwise be invisible.
            LOGGER.error(
                "CART_OWNER_KEY_UNATTRIBUTED rows=%s -- these lines have no owner and "
                "will not be covered by %s; they predate the owner key and need a "
                "decision, not a delete", orphans, INDEX_NAME,
            )
        collisions = colliding_lines(cur)
        if collisions:
            LOGGER.error(
                "CART_OWNER_INDEX_BLOCKED groups=%s worst=%s owner=%s listing=%s "
                "-- %s not created", len(collisions), collisions[0][2],
                collisions[0][0], collisions[0][1], INDEX_NAME,
            )
            return False
        cur.execute(CREATE_INDEX_SQL)
        LOGGER.warning("CART_OWNER_INDEX_CREATED name=%s engine=%s",
                       INDEX_NAME, db_service.ENGINE_NAME)
        return True
    except Exception as exc:
        LOGGER.warning("CART_OWNER_KEY_FAILED name=%s error=%s", INDEX_NAME, exc)
        return False
