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
buy. A claim that was never released *and* never filled — the request died
between the two — is taken over once it is older than :data:`CLAIM_TTL_SECONDS`,
for the same reason: a lock with no expiry is a lockout.

Both of those let a retry reach the provider again, so something has to stop the
retry creating a *second* payable session. This module used to claim the answer
was lane-specific — that the offers lane derived its provider key from the
attempt, so "a retry presents the same key and Stripe returns the original
session. Nothing second exists." That was false, and the reason it was false is
the whole content of the October 2026 incident.

Stripe binds an idempotency key to the *parameters* of the first request that
used it, for 24 hours. A later request presenting the same key with different
parameters is answered with HTTP 400 ``idempotency_error``, and is answered that
way for the rest of the window. So a stable key is only a replay if the
parameters are stable too.

No marketplace lane has stable parameters. All three embed the
``seller_transactions`` row the attempt created — ``success_url``,
``cancel_url``, ``metadata.seller_transaction_id(s)`` and
``payment_intent_data.transfer_group`` all name it, and the success page and the
settlement webhook read those back — and that row is a fresh ``lastrowid`` on
every attempt. The cart and buy-now lanes failed this by keeping a
content-addressed key over row-addressed parameters; the offers lane failed it
identically, mirror-imaged, and escaped production only because it has no UI
callers. Nine cart attempts, one burned key, a buyer who could not pay.

So in every lane the provider key must co-vary with the parameters, and the
consequence — in every lane — is that a retry can mint a second session.

What makes that acceptable is reachability, not luck. The orphan's URL is never
written to the database, never logged and never returned in any response — the
one code path that could have returned it is the path that failed. It exists
only inside Stripe, where it expires on its own, and all three checkout error
paths expire it eagerly whenever the failure was observed. One buyer, one
deliverable URL, at all times.

Which also relocates the deduplication guarantee. With a co-varying key the
provider no longer collapses a double tap — and in the cart lane the collision
had been doing exactly that by accident, so fixing the key alone turns one tap
into two charged surfaces and two decremented shelves. The claim in this module
is now the only thing standing between a double tap and a double order, which is
why it has to be the claim and not the key: it runs before the
``seller_transactions`` INSERT rather than at the last step, so it also
deduplicates the row and the stock, not just the payment.

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

import datetime as _dt
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
#: The cart lane does not derive its attempt key here — a cart's identity is
#: only knowable to the client that assembled it, so the key arrives on the
#: request and is namespaced below rather than hashed above. The constant exists
#: so the namespace is declared in one place alongside the others.
LANE_CART = "cart"
#: Buy-now, same arrangement as the cart: a client-supplied token, namespaced
#: here. Its own namespace rather than the cart's because the two are different
#: attempts even when a buyer's cart holds exactly the one item they then bought
#: directly — the transactions, the fulfilment choice and the response payloads
#: all differ, so replaying one as the other would answer the wrong purchase.
#:
#: Both lanes previously stored the client token raw. A deploy therefore
#: re-namespaces any in-flight attempt, so a buyer who is mid-checkout across
#: the deploy boundary gets a fresh claim instead of a replay. That is one
#: additional Stripe surface for a buyer who was already going to be told to
#: retry, and it is bounded to the deploy itself.
LANE_BUY_NOW = "buy_now"

#: How long a blank claim is believed to belong to a live request.
#:
#: Without this, the claim is a second poison of exactly the shape the cart
#: incident was: a request that dies *between* taking the claim and either
#: filling it or releasing it leaves a blank row that no later request can
#: insert over and no failure path will ever delete, so the buyer is refused
#: ``CHECKOUT_IN_PROGRESS`` forever for that cart. The release path only runs
#: when a failure is *observed*; a worker killed mid-flight, an OOM, a Railway
#: redeploy landing between two statements and a request that exceeded the
#: gunicorn timeout all skip it.
#:
#: 90 seconds is chosen against the client, not against the server: the mobile
#: and web callers abandon a checkout POST at ``PULSE_API_READ_TIMEOUT_MS``
#: (15s), so any request still legitimately holding a claim after 90s has no
#: caller left listening to the answer. Long enough that a genuinely slow
#: Stripe round trip is never stolen from, short enough that a buyer who taps
#: again after reading an error is not told to wait.
CLAIM_TTL_SECONDS = 90

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


def claim(cur, *, user_id, key: str, now: str,
          stale_after_seconds: int = CLAIM_TTL_SECONDS) -> dict:
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

    A blank claim older than ``stale_after_seconds`` is taken over rather than
    honoured — see :data:`CLAIM_TTL_SECONDS` for why that is not optional.
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

    # A blank row. Somebody claimed this attempt and has not finished — or
    # died without finishing, which is indistinguishable from here except by
    # age. An abandoned blank claim must not outlive the request that took it,
    # or it becomes a permanent refusal for this cart.
    if _take_over_if_stale(cur, buyer, key, now=now,
                           stale_after_seconds=stale_after_seconds):
        return {"state": CLAIMED, "detail": "stale_claim_taken_over"}
    return {"state": IN_PROGRESS}


def _take_over_if_stale(cur, buyer: int, key: str, *, now: str,
                        stale_after_seconds: int) -> bool:
    """Steal a blank claim that is older than the TTL. Returns whether we won.

    The UPDATE carries the observed ``created_at`` in its WHERE clause, so it
    is a compare-and-swap: if two requests both decide the same claim is stale,
    exactly one UPDATE matches and the other sees ``rowcount == 0`` and reports
    in-progress. Re-stamping ``created_at`` rather than deleting-then-inserting
    keeps it a single statement, which is what makes that true.
    """
    cur.execute(
        f"SELECT created_at FROM {KEY_TABLE} "
        "WHERE user_id=? AND idempotency_key=? "
        "  AND (response_json IS NULL OR response_json='') LIMIT 1",
        (buyer, key),
    )
    row = dict(cur.fetchone() or {})
    if not row:
        # It filled in or was released between the two statements. Not ours.
        return False
    observed = row.get("created_at")

    age = _age_seconds(observed, now)
    if age is None:
        # A row we cannot date is a row we cannot justify holding. Holding it
        # means refusing this buyer forever on the strength of a timestamp we
        # failed to read; taking it over costs, at worst, one Stripe session
        # that is never handed to any client. Logged loudly because it should
        # not happen and means a writer stored a format this cannot parse.
        LOGGER.error("CHECKOUT_CLAIM_TIMESTAMP_UNREADABLE key=%s created_at=%r "
                     "now=%r", key, observed, now)
    elif age < int(stale_after_seconds or 0):
        return False

    cur.execute(
        f"UPDATE {KEY_TABLE} SET created_at=? "
        "WHERE user_id=? AND idempotency_key=? "
        "  AND (response_json IS NULL OR response_json='') "
        "  AND created_at IS ?",
        (now, buyer, key, observed),
    )
    won = int(cur.rowcount or 0) == 1
    if won:
        LOGGER.warning("CHECKOUT_STALE_CLAIM_TAKEN_OVER key=%s age_seconds=%s "
                       "ttl=%s", key, age, stale_after_seconds)
    return won


def _age_seconds(created_at, now) -> float | None:
    """Seconds between two stored timestamps, or ``None`` if either is unreadable.

    Both values come from the same column family and are written by callers as
    ISO-8601 strings, but not uniformly: some writers include a timezone and
    some do not, and the two are not comparable in Python. Mixed-awareness pairs
    are normalised to naive UTC rather than refused, because refusing is the
    branch that locks a buyer out.
    """
    first = _parse_timestamp(created_at)
    second = _parse_timestamp(now)
    if first is None or second is None:
        return None
    return (second - first).total_seconds()


def _parse_timestamp(value):
    if isinstance(value, _dt.datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = _dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return parsed


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
