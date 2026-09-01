"""Stage 5 — ask Stripe before returning stock to a listing.

Why a reconciler exists at all
------------------------------
Expiry is a *timer*, and a timer is a guess about a payment it cannot see. The
deadline can elapse while the buyer is genuinely mid-authentication on a 3-D
Secure step-up, or while a `payment_intent.succeeded` webhook is queued behind a
Stripe retry backlog, or while an ACH-style delayed method waits on a bank. In
every one of those cases the naive sweeper — "deadline passed, give the stock
back" — produces the single worst outcome this mission exists to prevent: a
buyer who paid and an item that was resold to somebody else.

So the sweeper never releases on the strength of the clock alone. It asks the
processor what actually happened first, and the processor's answer wins. The
clock only decides *when to ask*.

Why not ask about everything
----------------------------
The directive is explicit that healthy reservations must not generate provider
traffic. They don't: a reservation is only reconciled once it is already past
its deadline plus the grace window, which for a functioning checkout is never.
A store doing a thousand successful sales an hour makes zero calls from this
module, because every one of those reservations is captured by the webhook long
before it expires. The call volume here is proportional to *abandonment*, not
to sales.

The decision table
------------------
====================  ==========  =========================================
PaymentIntent status  Decision    Reasoning
====================  ==========  =========================================
``succeeded``         ``capture`` Money moved. The stock is sold. Releasing
                                  it would oversell a paid order. This is
                                  also a *repair*: reaching this branch means
                                  a webhook was lost, and reconciliation is
                                  the backstop that notices.
``processing``        ``defer``   Asynchronous method still settling. No
                                  information yet; asking again later costs
                                  one API call, guessing wrong costs an order.
``requires_action``   ``defer``   Buyer is mid-authentication — but bounded.
``requires_*``        ``defer``   Past ``MAX_DEFERRALS`` the hold is released
                                  anyway, because an abandoned 3-D Secure
                                  prompt is indistinguishable from an active
                                  one and stock cannot be held forever.
``canceled``          ``release`` Stripe says it will never settle.
``requires_payment_   ``release`` after the deferral bound: the sheet was
method`` (post-bound)             dismissed or the card was refused.
no payment intent     ``release`` The buyer never reached Stripe.
unreachable Stripe    ``defer``   An outage must not become a mass release.
====================  ==========  =========================================

``defer`` is always safe in the direction that matters. Deferring holds stock
slightly too long; releasing wrongly sells an item twice. The asymmetry is not
close, so every ambiguous case defers.
"""

from __future__ import annotations

import logging
import os

from services import marketplace_reservation_policy as reservation_policy

LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------

#: Payment settled. Consume the hold; never return the stock.
DECISION_CAPTURE = "capture"
#: Outcome still unknown. Leave the hold alone and look again next sweep.
DECISION_DEFER = "defer"
#: Stripe is certain this will not settle. Return the stock.
DECISION_RELEASE = "release"

# --------------------------------------------------------------------------
# Stripe PaymentIntent statuses, grouped by what they tell us
# --------------------------------------------------------------------------

STATUS_SUCCEEDED = "succeeded"
STATUS_PROCESSING = "processing"
STATUS_CANCELED = "canceled"

#: Terminal-failure statuses. Stripe will not move money for these.
CONCLUSIVE_FAILURE_STATUSES = frozenset({STATUS_CANCELED})

#: Statuses meaning "the buyer still has something to do". Safe to wait on —
#: but only for a bounded number of sweeps, because a buyer who closed the app
#: mid-3DS leaves the intent sitting in ``requires_action`` indefinitely and
#: Stripe emits no event for it.
AWAITING_BUYER_STATUSES = frozenset({
    "requires_action",
    "requires_confirmation",
    "requires_payment_method",
    "requires_capture",
})

#: How many consecutive sweeps a reservation may defer before it is released
#: regardless. At the default sweep cadence this is a real wait beyond the TTL,
#: which is the point: it must comfortably outlast a slow 3-D Secure round trip
#: and still terminate.
MAX_DEFERRALS = 6
MAX_DEFERRALS_ENV_VAR = "MARKETPLACE_RESERVATION_MAX_DEFERRALS"


def max_deferrals() -> int:
    """Deferral bound, clamped so a typo cannot make the hold immortal."""
    raw = (os.environ.get(MAX_DEFERRALS_ENV_VAR) or "").strip()
    if not raw:
        return MAX_DEFERRALS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return MAX_DEFERRALS
    return max(1, min(value, 50))


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------

def decide_from_status(payment_intent_status: str | None, *,
                       deferrals: int = 0) -> dict:
    """Map a PaymentIntent status to a decision. Pure; no I/O, no database.

    Kept free of every dependency so the whole decision table is testable
    without Stripe, without a database and without a Flask app — which is what
    lets the twenty Stage 13 cases be deterministic rather than mocked.

    ``deferrals`` is how many times this reservation has already been deferred.
    It is what turns "wait for the buyer" from an unbounded promise into a
    bounded one.
    """
    status = (payment_intent_status or "").strip().lower()
    exhausted = int(deferrals or 0) >= max_deferrals()

    if status == STATUS_SUCCEEDED:
        return {"decision": DECISION_CAPTURE, "payment_intent_status": status,
                "detail": "payment_settled"}

    if status == STATUS_PROCESSING:
        # Never force-released. An asynchronous method that is still settling
        # may yet succeed, and releasing under it recreates the exact
        # paid-but-oversold failure the bound is meant to avoid. The hold stays
        # and is surfaced for an operator instead.
        return {"decision": DECISION_DEFER, "payment_intent_status": status,
                "detail": "deferral_bound_exhausted_while_processing" if exhausted
                          else "settlement_in_progress",
                "needs_attention": exhausted}

    if status in AWAITING_BUYER_STATUSES:
        if exhausted:
            return {"decision": DECISION_RELEASE, "payment_intent_status": status,
                    "release_reason": reservation_policy.REASON_EXPIRED,
                    "detail": "buyer_never_completed"}
        return {"decision": DECISION_DEFER, "payment_intent_status": status,
                "detail": "awaiting_buyer"}

    if status in CONCLUSIVE_FAILURE_STATUSES:
        return {"decision": DECISION_RELEASE, "payment_intent_status": status,
                "release_reason": reservation_policy.REASON_PAYMENT_CANCELED,
                "detail": "provider_canceled"}

    if not status:
        # No intent was ever created — checkout raised before Stripe, or the
        # transaction row never recorded one. Nothing can settle, so the hold
        # is pure loss and is returned.
        return {"decision": DECISION_RELEASE, "payment_intent_status": "",
                "release_reason": reservation_policy.REASON_EXPIRED,
                "detail": "no_payment_intent"}

    # An unrecognised status. Stripe may add one; this module must not
    # release stock on a string it does not understand.
    return {"decision": DECISION_DEFER, "payment_intent_status": status,
            "detail": "unknown_status", "needs_attention": True}


def decide_for_reservation(row, *, fetch_status=None, deferrals: int = 0) -> dict:
    """Decide for one expired reservation, consulting Stripe only if needed.

    ``row`` is the joined reservation + transaction record; only
    ``stripe_payment_intent_id`` and ``status`` are read. ``fetch_status`` is
    injected rather than imported so tests can drive the full table without a
    network, and so a caller that has already loaded the intent can avoid a
    second call.

    A raise from ``fetch_status`` becomes ``defer``, never ``release``. During a
    Stripe outage every reservation in the store would reconcile at once; if
    that produced releases, one provider incident would empty every hold in the
    system and resell paid orders wholesale.
    """
    record = dict(row or {})
    intent_id = str(record.get("stripe_payment_intent_id") or "").strip()

    tx_status = str(record.get("transaction_status") or record.get("status") or "").lower()
    if tx_status in {"paid", "refunded"}:
        # The local record already says settled. Trust it without spending an
        # API call — this is the cheap half of "do not call the provider for
        # every reservation".
        return {"decision": DECISION_CAPTURE, "payment_intent_status": STATUS_SUCCEEDED,
                "detail": "local_transaction_already_settled"}

    if not intent_id:
        return decide_from_status(None, deferrals=deferrals)

    if fetch_status is None:
        fetch_status = _fetch_payment_intent_status

    try:
        status = fetch_status(intent_id)
    except Exception:
        LOGGER.exception("RESERVATION_RECONCILE_PROVIDER_UNREACHABLE intent=%s", intent_id)
        return {"decision": DECISION_DEFER, "payment_intent_status": None,
                "detail": "provider_unreachable", "needs_attention": True}

    decision = decide_from_status(status, deferrals=deferrals)
    decision["stripe_payment_intent_id"] = intent_id
    return decision


def _fetch_payment_intent_status(payment_intent_id: str) -> str | None:
    """Read one PaymentIntent's status. Imported lazily and never at module
    scope, so importing this module — which the sweeper and the tests both do —
    does not require the Stripe SDK or a configured API key."""
    import stripe  # noqa: PLC0415 — deliberate: keep the import off the hot path

    intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    return (intent or {}).get("status")
