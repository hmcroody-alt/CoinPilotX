"""The identity of a *logical payment attempt* — server-derived, not row-derived.

Why this module exists
----------------------
The offers lane derived its Stripe idempotency key from the ``seller_transactions``
row it had just inserted::

    POST → INSERT → lastrowid → idempotency_key=f"marketplace-offer:{buyer}:{tx_id}"

A new row per attempt means a new key per attempt, so the key could not
deduplicate anything it was supposed to. Five taps produced five rows, five
keys, five *payable* Stripe Checkout Sessions — and, because the stock
decrement sits on the same path, five holds against one listing. The provider
key was carrying a value that changed precisely when it needed to stay the
same.

An idempotency key has to name *what the buyer is trying to do*. A database row
is a record that an attempt happened; it is not the attempt's identity.

What identity means here
------------------------
For an accepted offer the server already knows the whole logical attempt. The
price was fixed at acceptance, the quantity and currency came from the offer
row, the buyer came from the session, and the fulfilment lane was resolved
before any money surface existed. So the identity is derivable *entirely
server-side*::

    (lane, buyer, offer, listing, seller, amount_minor, currency, qty,
     fulfilment, payment_mode)  ──SHA-256──>  attempt key

No client token participates. That is the difference between this and the
cart/buy-now lanes, which must accept a client-supplied ``idempotency_key``
because a cart's contents are only known to be "the same cart" by the client
that assembled it. Those lanes hash the cart contents client-side (FNV-1a) and
bind the result to ``user_id`` server-side. This lane needs no such trust:
re-deriving from the offer is strictly stronger, because a client cannot vary
it at all.

SHA-256 rather than the cart's FNV-1a: FNV is a non-cryptographic hash chosen
there for cheap availability in browser JavaScript. Nothing on this path runs
in a browser, so there is no reason to inherit a weaker digest for a value that
gates a charge.

Replay, claim and the race
--------------------------
Deriving a stable key makes Stripe idempotent, but Stripe is the *last* thing
on the path. Two concurrent requests would still both insert a transaction row
and both decrement stock before either reached the provider. So the key is
claimed first, and the claim is the same DB row that later holds the response::

    claim()  ──INSERT ... ON CONFLICT DO NOTHING──>  CLAIMED    (we own it)
                                                 │
                                                 ├─>  IN_PROGRESS (someone else owns
                                                 │                 it and has not
                                                 │                 finished)
                                                 └─>  REPLAY      (it finished; here
                                                                   is the answer it
                                                                   gave)

The uniqueness is enforced by ``UNIQUE(user_id, idempotency_key)`` on
``marketplace_cart_checkout_keys`` — a database invariant that already existed
for the cart lane, reused rather than reinvented, so two processes racing on the
same logical attempt cannot both win regardless of what either believes about
the other. There is no lock, no in-memory registry and no coordination between
workers: restart-safe and multi-worker-safe because the guarantee lives in the
database, not in a process.

``remember`` only ever fills a *blank* response, so a slow writer cannot
overwrite the answer a faster one already gave the buyer.

Releasing a failed claim
------------------------
A claim that failed before producing a response is deleted, so the buyer can
retry rather than being permanently locked out of an offer they are entitled to
buy. That is only safe *because* the provider key is derived from the attempt
rather than from a row: if the previous try did reach Stripe before dying, the
retry presents the same key and Stripe returns the original session instead of
creating a second one. The two mechanisms are load-bearing together — deleting
claims under a ``lastrowid`` key would have reintroduced the original defect.

Table ownership
---------------
``ensure_schema`` carries its own copy of the ``CREATE TABLE IF NOT EXISTS``
rather than depending on ``marketplace_cart_routes._ensure_schema``. That
dependency is exactly the one that broke the reservation sweep in production:
the columns existed only after some process served a cart request, and the
worker that needed them never served one. A module that gates charges must be
bootable on its own.
"""

from __future__ import annotations

import hashlib
import json
import logging

LOGGER = logging.getLogger(__name__)

#: The replay/claim table. Shared with the cart and buy-now lanes on purpose:
#: one buyer's logical attempts live in one place, and the uniqueness invariant
#: is maintained once.
KEY_TABLE = "marketplace_cart_checkout_keys"

KEY_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {KEY_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    idempotency_key TEXT,
    response_json TEXT,
    created_at TEXT,
    UNIQUE(user_id, idempotency_key)
)
"""

#: Claim outcomes.
CLAIMED = "claimed"
IN_PROGRESS = "in_progress"
REPLAY = "replay"

#: Returned to a caller that lost the claim race. A distinct code so the client
#: can retry quietly instead of showing the buyer a failure for something that
#: is merely still in flight.
IN_PROGRESS_CODE = "CHECKOUT_IN_PROGRESS"
IN_PROGRESS_MESSAGE = (
    "This checkout is already being started. You have not been charged twice — "
    "give it a moment and try again."
)

#: Keys are namespaced by lane so that a future lane cannot collide with this
#: one inside a shared table.
LANE_OFFER = "offer"

#: How much of the digest to keep. 32 hex characters is 128 bits, which is far
#: beyond collision range for one buyer's attempts, and leaves the composed key
#: comfortably inside the 120-character column the cart lane already truncates
#: client keys to.
_DIGEST_CHARS = 32


def ensure_schema(cur) -> bool:
    """Create the claim/replay table if absent. Idempotent; never raises.

    Returns whether the table is believed usable. A failure is logged and
    reported rather than thrown because the caller is on a checkout path: it
    must be able to decide to refuse the charge, which is a different and much
    better outcome than a 500 with a half-built attempt behind it.
    """
    try:
        cur.execute(KEY_TABLE_DDL)
        return True
    except Exception as exc:
        # The overwhelmingly common reason to land here is a concurrent creator,
        # in which case the table does exist and the caller may proceed. A real
        # permissions problem shows up as a failure on the claim below, where
        # refusing is correct.
        LOGGER.warning("CHECKOUT_IDENTITY_SCHEMA_DDL_FAILED error=%s", exc)
        return False


def attempt_key(*, lane: str, buyer_user_id, offer_id, listing_id,
                seller_user_id, amount_minor, currency, quantity,
                fulfillment, payment_mode) -> str:
    """The stable identity of one logical payment attempt.

    Every component is server-side truth at the moment of the charge. Changing
    any of them is a genuinely different attempt and *must* produce a different
    key: a buyer who switches from pickup to delivery, or whose offer is
    re-accepted at another price, is not retrying — they are buying something
    else, and silently replaying the first answer would charge them for the
    wrong thing.

    Normalisation is explicit (ints as ints, currency upper-cased, the rest
    lower-cased and stripped) so that two requests which differ only in the
    casing or spacing of a string do not read as two attempts and create two
    sessions.
    """
    parts = [
        str(lane or "").strip().lower(),
        str(int(buyer_user_id or 0)),
        str(int(offer_id or 0)),
        str(int(listing_id or 0)),
        str(int(seller_user_id or 0)),
        str(int(amount_minor or 0)),
        str(currency or "").strip().upper(),
        str(int(quantity or 0)),
        str(fulfillment or "").strip().lower(),
        str(payment_mode or "").strip().lower(),
    ]
    # "\x1f" (unit separator) cannot occur in any normalised component, so the
    # join is unambiguous. A plain ":" would let two different tuples flatten to
    # the same string if a component ever contained one.
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{parts[0]}:{digest[:_DIGEST_CHARS]}"


def stripe_idempotency_key(key: str) -> str:
    """The provider-facing key for an attempt.

    Derived from the logical attempt and nothing else — in particular not from
    a row id, which is the defect this module exists to remove. Prefixed so that
    a key seen in the Stripe dashboard is attributable to this lane.
    """
    return f"marketplace-{key}"


def claim(cur, *, user_id, key: str, now: str) -> dict:
    """Take ownership of a logical attempt, or report who already has it.

    ``{"state": CLAIMED}``      caller owns it and must proceed to create the
                                charge surface, then call ``remember``.
    ``{"state": REPLAY, ...}``  it already completed; ``payload`` is the exact
                                answer the buyer was given the first time.
    ``{"state": IN_PROGRESS}``  another request owns it and has not finished.

    The ``INSERT ... ON CONFLICT DO NOTHING`` is the whole concurrency control.
    Exactly one of N simultaneous callers can insert the row, so exactly one
    gets ``CLAIMED``; the losers read it back. No advisory lock, no transaction
    escalation, and nothing held across the provider call.
    """
    buyer = int(user_id or 0)
    cur.execute(
        f"INSERT INTO {KEY_TABLE} (user_id, idempotency_key, response_json, created_at) "
        "VALUES (?, ?, '', ?) ON CONFLICT(user_id, idempotency_key) DO NOTHING",
        (buyer, key, now),
    )
    # `rowcount` is the only signal that distinguishes "we inserted" from "the
    # conflict clause absorbed it", and it is reliable for this statement shape
    # on both engines. A SELECT-then-INSERT would be a race; this is not.
    if int(cur.rowcount or 0) == 1:
        return {"state": CLAIMED}

    stored = _stored_response(cur, buyer, key)
    if stored is not None:
        return {"state": REPLAY, "payload": stored}
    return {"state": IN_PROGRESS}


def remember(cur, *, user_id, key: str, payload: dict) -> bool:
    """Record the answer this attempt produced. First writer wins.

    The ``response_json IS NULL OR = ''`` guard means a late or duplicated
    writer cannot replace an answer the buyer has already been shown. Whether
    this wrote is returned rather than logged-and-swallowed, because "we created
    a session but failed to record it" is a state worth asserting about in tests.
    """
    try:
        body = json.dumps(payload, default=str)
    except Exception:
        LOGGER.exception("CHECKOUT_IDENTITY_PAYLOAD_UNSERIALISABLE key=%s", key)
        return False
    cur.execute(
        f"UPDATE {KEY_TABLE} SET response_json=? "
        "WHERE user_id=? AND idempotency_key=? "
        "  AND (response_json IS NULL OR response_json='')",
        (body, int(user_id or 0), key),
    )
    return int(cur.rowcount or 0) == 1


def release(cur, *, user_id, key: str) -> bool:
    """Drop a claim that produced no answer, so the buyer may retry.

    Scoped by ``response_json`` being blank: a completed attempt is never
    deleted by a failure path, because deleting it would let the next request
    build a second charge surface for an attempt that already has one.
    """
    cur.execute(
        f"DELETE FROM {KEY_TABLE} "
        "WHERE user_id=? AND idempotency_key=? "
        "  AND (response_json IS NULL OR response_json='')",
        (int(user_id or 0), key),
    )
    return int(cur.rowcount or 0) == 1


def _stored_response(cur, buyer: int, key: str):
    cur.execute(
        f"SELECT response_json FROM {KEY_TABLE} "
        "WHERE user_id=? AND idempotency_key=? LIMIT 1",
        (buyer, key),
    )
    row = dict(cur.fetchone() or {})
    body = row.get("response_json")
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        # A corrupt stored response must not be served as if it were the
        # buyer's answer, and must not be treated as "no attempt" either — that
        # would build a second charge surface. Reported as in-progress by the
        # caller, which is the safe reading: something happened here, and this
        # request is not the one that gets to decide what.
        LOGGER.error("CHECKOUT_IDENTITY_RESPONSE_CORRUPT key=%s", key)
        return None
