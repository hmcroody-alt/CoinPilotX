"""Who owns a cart that belongs to nobody with an account.

The problem
-----------
``marketplace_cart_items`` is owned by one column, ``user_id``, and every read
in the lane is ``WHERE c.user_id = ?``. ``_serialize_lines`` does not join
``users`` at all -- it says so in its own comment -- so the owner is a bare
integer and nothing downstream asks whether a row exists behind it. That is the
whole reason a guest cart is reachable without reshaping the cart.

The two tempting designs are both worse than this one:

* **A ``guest_token`` column on the cart.** It needs the unique key widened
  again, and ``marketplace_cart_schema``'s docstring is a long account of how
  careful the last swap of that constraint had to be, on two engines, with a
  table rebuild on SQLite. Doing it a second time to support people who are not
  yet customers trades real risk to paying members for a feature they do not
  use. It also reintroduces NULL-distinctness: a nullable owner column lets one
  buyer accumulate unlimited duplicate lines, which is the exact bug that
  constraint exists to prevent.
* **A bare negative-``user_id`` convention.** Mechanically fine -- ``users``
  ids come from ``INTEGER PRIMARY KEY AUTOINCREMENT`` and so are always ``>= 1``
  -- but a sign convention is not written down anywhere a query can see it. The
  failure it invites is a guest reading a member's cart, which is the worst
  outcome this module could possibly have.

So the id is still negative, and it is still disjoint by construction, but it is
*allocated and recorded* rather than assumed. A guest customer is a row here
with a token, an owner id and a creation time. That makes the convention an
auditable fact -- the ids in use can be listed, and an abandoned one can be
expired -- instead of a rule someone has to remember.

What a guest customer is not
----------------------------
Not a ``users`` row, and nothing here ever creates one. A cart is an intention,
not an account, and minting an account for someone who has only clicked "Add to
cart" is the forced-signup this work exists to remove -- it would also leave a
dormant account behind for every visitor who changed their mind. The token is a
cart key and nothing else: it carries no email, no name, and no way to sign in.

The token is also not a capability anyone else can guess. It is 32 bytes from
``secrets``, it is the only thing that resolves to the owner id, and it is
matched exactly -- so the cart it opens is only reachable by the browser holding
it.

Price is still the server's
---------------------------
Nothing here touches money. The cart rows a guest owns are the same rows a
member owns, read by the same query and priced by the same authority, so a guest
cannot name a price any more than a member can. This module answers exactly one
question -- which integer goes in ``user_id`` -- and declines to be involved in
anything else.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

TABLE = "marketplace_guest_customers"

#: The cookie the browser carries. Named for what it keys, not for what it is
#: worth: it is a cart key, and a reader of a request log should not mistake it
#: for a session.
COOKIE_NAME = "psoc_guest_cart"

#: Rotated well inside a cookie's life so an abandoned cart cannot be resumed
#: months later at prices that have moved. Lines are re-priced on every read
#: regardless; this bounds how long the *token* is worth holding.
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

#: 32 bytes of ``secrets`` entropy, hex-encoded. Long enough that guessing is
#: not a threat model, short enough to sit in a cookie without comment.
TOKEN_BYTES = 32

#: Guest owner ids start here and count *down*. Below any id ``users`` can
#: produce -- that table's ids are ``INTEGER PRIMARY KEY AUTOINCREMENT`` and so
#: always ``>= 1`` -- and far enough from zero that a value seen in a log is
#: recognisably not a member.
FIRST_OWNER_ID = -1_000_000


def is_guest_owner(owner_id: Any) -> bool:
    """Whether this id belongs to a guest rather than a member.

    The one place the sign convention is allowed to be read, so that a caller
    needing to tell the two apart does not re-derive it and get it backwards.
    """
    try:
        return int(owner_id or 0) <= FIRST_OWNER_ID
    except (TypeError, ValueError):
        return False


def new_token() -> str:
    return secrets.token_hex(TOKEN_BYTES)


def ensure_schema(cur) -> bool:
    """Create the table if it is absent. Idempotent; never raises.

    Returns whether the table is usable, so a caller in a request path can
    degrade to "no guest cart" rather than 500 a product page. A guest who
    cannot be given a cart should still be able to read the catalogue.
    """
    try:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guest_token TEXT UNIQUE,
                owner_id INTEGER UNIQUE,
                created_at TEXT,
                last_seen_at TEXT,
                claimed_by_user_id INTEGER
            )
            """
        )
        return True
    except Exception:
        LOGGER.exception("GUEST_CUSTOMER_SCHEMA_FAILED")
        return False


def _next_owner_id(cur) -> int:
    """One below the lowest owner id on record, or the first one.

    The high-water mark is ``MIN(owner_id)`` over every row ever allocated,
    which is why :func:`revoke` blanks a token instead of deleting the row. A
    hard ``DELETE`` of the lowest row would move this mark back up and reissue
    that id to the next visitor -- whose first page load would then show
    whatever cart lines the previous holder left behind. Rows here are
    tombstones by design: a few dozen bytes each, allocated only when somebody
    actually adds something to a cart.
    """
    cur.execute(f"SELECT MIN(owner_id) AS lowest FROM {TABLE}")
    row = cur.fetchone()
    lowest = None
    if row is not None:
        try:
            lowest = row["lowest"]
        except (TypeError, KeyError, IndexError):
            lowest = row[0]
    if lowest is None:
        return FIRST_OWNER_ID
    return min(int(lowest), FIRST_OWNER_ID) - 1


def owner_id_for_token(cur, token: Optional[str]) -> int:
    """The owner id this token opens, or ``0`` for no token and no match.

    ``0`` and not ``None`` so a caller can pass the result straight into the
    cart's ``WHERE c.user_id = ?`` and get an empty cart, which is the right
    answer for a stale or forged token. No row is created here: reading a cart
    must not be what allocates one, or every crawler hit would mint a guest.
    """
    token = str(token or "").strip()
    if not token:
        return 0
    try:
        cur.execute(
            f"SELECT owner_id FROM {TABLE} WHERE guest_token=? AND claimed_by_user_id IS NULL LIMIT 1",
            (token,),
        )
        row = cur.fetchone()
    except Exception:
        LOGGER.exception("GUEST_CUSTOMER_LOOKUP_FAILED")
        return 0
    if not row:
        return 0
    try:
        return int(row["owner_id"] or 0)
    except (TypeError, KeyError, IndexError):
        return int(row[0] or 0)


def claim_for_member(cur, token: Optional[str], user_id: int) -> int:
    """Mark a guest identity as absorbed into an account. Returns the owner id.

    Called when someone signs in holding a guest token. It does not move the
    cart lines -- that is ``marketplace_guest_cart_merge``'s job, and it has to
    reconcile quantities against whatever the member already had. This only
    records that the guest identity is spent, which is what stops the same token
    being replayed afterwards to read a cart that now belongs to a member.
    """
    owner_id = owner_id_for_token(cur, token)
    if not owner_id:
        return 0
    try:
        cur.execute(
            f"UPDATE {TABLE} SET claimed_by_user_id=? WHERE owner_id=? AND claimed_by_user_id IS NULL",
            (int(user_id), owner_id),
        )
    except Exception:
        LOGGER.exception("GUEST_CUSTOMER_CLAIM_FAILED")
        return 0
    return owner_id


def revoke(cur, token: Optional[str]) -> int:
    """Retire a guest identity without releasing its id. Returns the owner id.

    How an abandoned cart is expired, and the reason it is not a ``DELETE``:
    the id must stay on record or :func:`_next_owner_id` will hand it out again.
    The token is cleared, so the cookie still in some browser stops resolving;
    the sweep that calls this is free to delete the cart *lines* afterwards.
    """
    owner_id = owner_id_for_token(cur, token)
    if not owner_id:
        return 0
    try:
        cur.execute(f"UPDATE {TABLE} SET guest_token=NULL WHERE owner_id=?", (owner_id,))
    except Exception:
        LOGGER.exception("GUEST_CUSTOMER_REVOKE_FAILED")
        return 0
    return owner_id


def allocate(cur, now: str) -> tuple[str, int]:
    """Mint a guest identity. Returns ``(token, owner_id)``, or ``("", 0)``.

    Separate from :func:`owner_id_for_token` because allocation is a write and
    the read happens on every page a guest loads. Only an *action* -- adding a
    line -- should bring a guest customer into existence.
    """
    if not ensure_schema(cur):
        return "", 0
    token = new_token()
    try:
        owner_id = _next_owner_id(cur)
        cur.execute(
            f"INSERT INTO {TABLE} (guest_token, owner_id, created_at, last_seen_at) VALUES (?,?,?,?)",
            (token, owner_id, now, now),
        )
    except Exception:
        LOGGER.exception("GUEST_CUSTOMER_ALLOCATE_FAILED")
        return "", 0
    return token, owner_id
