"""Business OS — SELLER METRICS: one definition of "live" and one of "order".

Every number a seller sees about their own business is computed here, once, on
the server. Business OS and the Store screen read the same fields of the same
payload, so the two cannot disagree — which, until this module existed, they
loudly did.

What was wrong
--------------
Both screens were fed the same two raw lists (``GET
/api/pulse/marketplace/seller/listings`` and ``GET
/api/pulse/payments/seller/orders``) and each applied its own arithmetic to
them. The Store screen applied predicates; Business OS applied ``.length``. So
for seller 1 in production on 2026-09-22:

* Business OS said **43 Live listings**. That is every listing row the seller
  owns that has not been deleted — 28 of them drafts that no buyer can see.
  The true figure was 13.
* Business OS said **32 Orders**. Not one of those 32 rows was a paid order.
  Thirteen were ``checkout_created`` (a PaymentIntent exists, nobody paid),
  nine ``checkout_expired``, seven ``checkout_failed``, two bare ``created``
  cart rows and one Stripe-reported ``failed``. The true figure was 0.

Neither number was a rendering bug and neither could be fixed on the phone.
``.length`` is a faithful count of the list it was given; the list was simply
never the answer to the question the label was asking.

The two predicates
------------------
``is_live_listing`` and ``confirmed_order_state`` are the whole point of this
module. They are the only place either question is answered, and every count
below is built from them.

**Live** means a buyer can see it and buy it. A draft is not live. A listing
awaiting review is not live — the seller submitted it, nothing is wrong, and it
still is not on sale. A paused, rejected, blocked or deleted listing is not
live. Only ``published``/``live``/``active`` with an approval that has not been
withdrawn counts, which is exactly the rung the Store screen's Active tab has
always drawn and exactly the rung ``listing_readiness`` calls checkout-ready.

**Confirmed** means money is owed to this seller for this row, as decided by
something other than the buyer's phone. Two lanes reach it and they are kept
apart on purpose:

* **Card.** Stripe said the payment succeeded and a server-side webhook or
  reconciliation pass wrote that down. ``checkout_created`` means a
  PaymentIntent was minted and nothing more; the seller is owed nothing and it
  must never be counted. This is not a hypothetical — see the orphan note
  below.
* **Cash / in person.** ``cash_pending`` IS a confirmed order the moment it is
  created, before any money changes hands, because that is what PulseSoc's
  cash lane already means: ``bot.py`` routes it through ``status_group ==
  "pending"``, the seller has committed stock to it, and
  ``POST /api/pulse/orders/<id>/cash-collected`` is the seller's own attestation
  that they were paid. Requiring Stripe success of a cash order would erase the
  lane. It is reported separately from paid card revenue so that no money
  figure is inflated by cash that has not been collected.

Statuses are matched against explicit sets, never by substring. Substring
matching is how ``"unavailable"`` gets read as ``"available"``, and the order
vocabulary here contains ``failed`` and ``checkout_failed``, ``created`` and
``checkout_created`` — four words where two contain the other two.

Refunds stay
------------
A refunded order was a real sale and remains in ``confirmed_orders`` and in the
historical record. It is excluded from ``open_orders`` (nothing left to ship)
and from ``net_sales_minor`` (the money went back). Deleting it, or not
counting it, would make the seller's own history disagree with their bank.

The orphan this module surfaces but does not fix
------------------------------------------------
Transaction 27 (seller 1, $0.50, 2026-08-23) carries
``pi_3U7QYgFP8qvvGWBI0MGB8bL0``, which Stripe reports as **succeeded** in
livemode with charge ``ch_3U7QYgFP8qvvGWBI09HrxN7r``. The row still says
``checkout_created``, and ``stripe_events`` holds no ``payment_intent.succeeded``
or ``charge.succeeded`` event — the success notification never arrived. Real
money moved and PulseSoc never learned about it.

``unmatched_payment_candidates`` below finds rows in exactly that shape so the
condition is visible instead of silent. This module does NOT promote them: a
metrics reader must not write payment state, and turning a row into a paid order
starts fulfilment and payout. That is a decision with money attached and it
belongs to reconciliation, under an owner who approved it.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional

# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

#: Publication states in which a buyer can see and order the listing.
LIVE_PUBLICATION_STATES = frozenset({"published", "live", "active"})

#: Submitted, no decision yet. Not live, and not a draft either — the seller
#: has done their part, so filing these under Drafts would tell them their
#: publish did not work.
AWAITING_REVIEW_STATES = frozenset({"pending_review", "review_ready"})

#: Never live, never counted anywhere, and excluded from the total as well:
#: the seller deleted these and does not consider them part of their store.
REMOVED_STATES = frozenset({"seller_deleted", "deleted", "removed"})

#: Not published, and the reason is a decision that went against the listing
#: (or the seller paused it themselves).
SUPPRESSED_STATES = frozenset(
    {"paused", "rejected", "blocked", "blocked_review", "suspended", "hidden"}
)

#: Approval verdicts that revoke a listing even when ``status`` still reads
#: published. Publication and approval are two columns and they can disagree;
#: when they do, the restrictive one wins, because showing a rejected product
#: to buyers is the failure that matters.
NON_APPROVED_STATES = REMOVED_STATES | frozenset({"rejected", "blocked", "suspended"})


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip().lower()
        if text:
            return text
    return ""


def listing_state(row: Mapping[str, Any]) -> str:
    """The one canonical word for what a listing is.

    Returns ``live``, ``draft``, ``pending_review``, ``suppressed`` or
    ``removed``. Every listing count in PulseSoc is a tally of these, and no
    caller may re-derive them from the raw columns.
    """
    status = _text(row, "status")
    approval = _text(row, "approval_status")

    if status in REMOVED_STATES or approval in REMOVED_STATES:
        return "removed"
    # Publication is read from `status`; `publication_state` is accepted only
    # because the serialized payload carries it under that name. There is no
    # such column in the database.
    publication = _text(row, "publication_state", "status")
    if publication in LIVE_PUBLICATION_STATES:
        if approval and approval in NON_APPROVED_STATES:
            return "suppressed"
        return "live"
    if publication in AWAITING_REVIEW_STATES:
        # Only `status` can put a listing in review. `approval_status` cannot:
        # in this schema an untouched draft is written with
        # `approval_status='pending_review'` at creation, so reading approval as
        # "the seller submitted it" would file all 28 of seller 1's drafts under
        # Awaiting review and empty their Drafts tab. The real submission signal
        # is `status='review_ready'`.
        return "pending_review"
    if publication == "draft" or not publication:
        return "draft"
    if publication in SUPPRESSED_STATES:
        return "suppressed"
    # An unrecognised state is deliberately NOT live. A vocabulary this module
    # has not been taught is a reason to under-report, never to advertise.
    return "suppressed"


def is_live_listing(row: Mapping[str, Any]) -> bool:
    """Can a buyer see this listing and order it right now?

    The single predicate behind Business OS "Live listings", the Store screen's
    Active tab, seller analytics and any store-level count. Screen-specific
    counting logic is what this function exists to end.
    """
    return listing_state(row) == "live"


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

#: A card payment that a server-side authority confirmed.
PAID_STATES = frozenset({"paid", "succeeded", "checkout_completed", "completed"})

#: Paid, and moving through fulfilment.
FULFILMENT_STATES = frozenset(
    {"processing", "preparing", "shipped", "in_transit", "out_for_delivery"}
)

#: Fulfilment finished.
DELIVERED_STATES = frozenset({"delivered", "fulfilled", "collected"})

#: Paid and given back. Still an order, still history, no longer revenue.
REFUNDED_STATES = frozenset({"refunded", "partially_refunded", "chargeback"})

#: The cash lane. A real order before any money moves — see the module docstring.
CASH_PENDING_STATES = frozenset({"cash_pending"})

#: Everything before a payment exists. None of these is an order, and the two
#: pairs below are why this is a set rather than a substring test:
#: ``created``/``checkout_created`` and ``failed``/``checkout_failed``.
PRE_PAYMENT_STATES = frozenset(
    {
        "created",
        "pending",
        "checkout_created",
        "checkout_pending",
        "requires_payment_method",
        "requires_action",
        "processing_payment",
    }
)

#: Payment was attempted and did not succeed, or was never attempted.
FAILED_STATES = frozenset(
    {
        "failed",
        "checkout_failed",
        "payment_failed",
        "blocked_stripe_not_configured",
        "out_of_stock",
        "declined",
    }
)

#: The buyer walked away, or the reservation timed out.
ABANDONED_STATES = frozenset({"checkout_expired", "expired", "abandoned"})

#: Cancelled with no payment recorded. A cancellation *after* payment is
#: represented by a refund, which stays in `confirmed_orders`.
CANCELLED_STATES = frozenset({"cancelled", "canceled", "checkout_canceled", "voided"})

#: Confirmed = the seller is owed something, or has already been paid.
CONFIRMED_ORDER_STATES = (
    PAID_STATES | FULFILMENT_STATES | DELIVERED_STATES | REFUNDED_STATES | CASH_PENDING_STATES
)

#: Confirmed and still needing the seller to do something.
OPEN_ORDER_STATES = PAID_STATES | FULFILMENT_STATES | CASH_PENDING_STATES


def order_state(row: Mapping[str, Any]) -> str:
    """Classify one ``seller_transactions`` row into the audit vocabulary.

    One of: ``paid``, ``fulfilling``, ``delivered``, ``refunded``,
    ``cash_pending``, ``pre_payment``, ``failed_payment``, ``abandoned``,
    ``cancelled``, ``unknown``.

    ``unknown`` is returned rather than guessed. A status this module has not
    been taught must not be silently counted as a sale, and must not be quietly
    dropped either — ``seller_metrics`` reports the tally so an unrecognised
    lifecycle word shows up as a number somebody can go and look at.
    """
    status = _text(row, "status")
    if status in PAID_STATES:
        return "paid"
    if status in FULFILMENT_STATES:
        return "fulfilling"
    if status in DELIVERED_STATES:
        return "delivered"
    if status in REFUNDED_STATES:
        return "refunded"
    if status in CASH_PENDING_STATES:
        return "cash_pending"
    if status in PRE_PAYMENT_STATES:
        return "pre_payment"
    if status in FAILED_STATES:
        return "failed_payment"
    if status in ABANDONED_STATES:
        return "abandoned"
    if status in CANCELLED_STATES:
        return "cancelled"
    if status.startswith("blocked"):
        return "failed_payment"
    return "unknown"


def is_confirmed_order(row: Mapping[str, Any]) -> bool:
    """Is this row a legitimate seller order?

    A checkout session, a PaymentIntent, a failed charge and an expired
    reservation are all rows in ``seller_transactions`` and none of them is an
    order. Only a server-confirmed payment, or a cash order on its own
    lifecycle, is.
    """
    return order_state(row) in {
        "paid",
        "fulfilling",
        "delivered",
        "refunded",
        "cash_pending",
    }


def is_open_order(row: Mapping[str, Any]) -> bool:
    """Confirmed, and the seller still owes the buyer something."""
    return order_state(row) in {"paid", "fulfilling", "cash_pending"}


def is_card_revenue(row: Mapping[str, Any]) -> bool:
    """Counts toward money figures.

    Cash is excluded until collected: ``cash_pending`` is a real order but the
    seller does not have the money yet, and a sales total that includes it is a
    total that lies. Refunds are excluded because the money went back.
    """
    return order_state(row) in {"paid", "fulfilling", "delivered"}


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def _minor(row: Mapping[str, Any]) -> int:
    for key in ("gross_amount_cents", "amount_cents"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _day(value: Any) -> str:
    """The calendar day of a timestamp, as ``YYYY-MM-DD``.

    String slicing rather than parsing: every timestamp in this table is
    ISO-8601 written by the application, in two shapes that differ only in
    whether they carry an offset (``...T01:50:50`` and
    ``...T22:00:30+00:00``). Both start with the date, and a parse that has to
    accept both would be more code with more ways to raise.
    """
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def unmatched_payment_candidates(orders: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Rows that hold a PaymentIntent but were never confirmed.

    A report, not a repair. Each of these is either a buyer who opened checkout
    and left — the ordinary case — or a payment that succeeded at Stripe while
    PulseSoc never heard about it, which is what happened to transaction 27.
    The two are indistinguishable from the database alone; separating them takes
    a Stripe call, which belongs to reconciliation and not to a metrics read.
    """
    candidates = []
    for row in orders:
        if is_confirmed_order(row):
            continue
        intent = str(row.get("stripe_payment_intent_id") or "").strip()
        if not intent:
            continue
        candidates.append(
            {
                "transaction_id": row.get("id"),
                "status": _text(row, "status"),
                "payment_intent_id": intent,
                "amount_minor": _minor(row),
                "currency": str(row.get("currency") or "USD").upper(),
                "created_at": row.get("created_at"),
            }
        )
    return candidates


def compute(
    listings: Iterable[Mapping[str, Any]],
    orders: Iterable[Mapping[str, Any]],
    *,
    today: str = "",
    recent_days: Optional[list] = None,
    baseline_day: str = "",
    campaigns: Optional[Iterable[Mapping[str, Any]]] = None,
    ad_spend_minor: int = 0,
) -> dict:
    """The seller's whole numeric picture, from rows, with no I/O.

    Pure so that every branch is testable without a database. ``today`` and
    ``recent_days`` are passed in rather than read from the clock so a caller
    can compute against the day the rows were captured — the same reason
    ``deriveKpis`` on the phone takes a ``now``.
    """
    listing_rows = list(listings)
    order_rows = list(orders)
    recent = set(recent_days or [])

    by_listing_state: dict[str, int] = {}
    for row in listing_rows:
        state = listing_state(row)
        by_listing_state[state] = by_listing_state.get(state, 0) + 1

    by_order_state: dict[str, int] = {}
    for row in order_rows:
        state = order_state(row)
        by_order_state[state] = by_order_state.get(state, 0) + 1

    confirmed = [row for row in order_rows if is_confirmed_order(row)]
    revenue = [row for row in confirmed if is_card_revenue(row)]

    today_sales = sum(_minor(row) for row in revenue if _day(row.get("created_at")) == today)
    # Units, not rows: a line for three of the same product sold three. `qty`
    # is what checkout writes into `metadata_json`, and its absence means one.
    sold_7d = 0
    # Per-listing units and per-day money, over the same seven days and through
    # the same `revenue` filter. These exist server-side because the phone was
    # deriving both of them itself, from every order row, with a filter that
    # excluded only "cancel" and "refund" -- so the three `checkout_created`
    # rows of 2026-09-20 were reported to the seller as three units sold, and
    # an abandoned checkout opened today would have been reported as today's
    # takings. Sending the seller a list of rows and letting the client decide
    # what a sale is IS the defect; it cannot be fixed on the client.
    units_by_listing: dict[str, int] = {}
    money_by_day: dict[str, int] = {}
    for row in revenue:
        day = _day(row.get("created_at"))
        # Money is bucketed for every day, not just the recent seven, because
        # the trend below reaches back to the same weekday a week earlier --
        # which is the eighth day back and deliberately outside the window.
        money_by_day[day] = money_by_day.get(day, 0) + _minor(row)
        if recent and day not in recent:
            continue
        quantity = _quantity(row)
        sold_7d += quantity
        key = str(row.get("item_id") or "").strip()
        if key:
            units_by_listing[key] = units_by_listing.get(key, 0) + quantity

    # Oldest first, so the sparkline reads left to right like a calendar.
    # `recent_days` arrives newest-first, matching how a caller naturally builds
    # it from today backwards.
    sparkline = [money_by_day.get(day, 0) for day in reversed(list(recent_days or []))]

    # Against the same weekday last week, not yesterday: a store's Saturday and
    # its Tuesday are not comparable. `None` when there is no baseline, so a
    # store's first week never reports "+100%".
    baseline = money_by_day.get(baseline_day or "", 0)
    sales_trend = (today_sales - baseline) / baseline if baseline > 0 else None

    campaign_rows = list(campaigns or [])
    active_campaigns = sum(
        1
        for row in campaign_rows
        if str(row.get("status") or "").strip().lower() in {"active", "running", "live"}
    )

    return {
        # Listings. `total_listings` excludes rows the seller deleted, which is
        # what the Store screen's All tab already shows them.
        "total_listings": len(listing_rows) - by_listing_state.get("removed", 0),
        "live_listings": by_listing_state.get("live", 0),
        "draft_listings": by_listing_state.get("draft", 0),
        "pending_review_listings": by_listing_state.get("pending_review", 0),
        "suppressed_listings": by_listing_state.get("suppressed", 0),
        "removed_listings": by_listing_state.get("removed", 0),
        # Orders.
        "confirmed_orders": len(confirmed),
        "open_orders": sum(1 for row in order_rows if is_open_order(row)),
        "fulfilled_orders": by_order_state.get("delivered", 0),
        "refunded_orders": by_order_state.get("refunded", 0),
        "cash_pending_orders": by_order_state.get("cash_pending", 0),
        # Money.
        "today_sales_minor": today_sales,
        "sold_last_7_days": sold_7d,
        "sales_last_7_days_minor": sparkline,
        "sales_trend_ratio": sales_trend,
        "units_sold_last_7_days_by_listing": units_by_listing,
        "net_sales_minor": sum(_minor(row) for row in revenue),
        "currency": next(
            (str(row.get("currency")).upper() for row in order_rows if row.get("currency")), "USD"
        ),
        # Advertising, passed through so one payload answers the whole panel.
        "active_campaigns": active_campaigns,
        "ad_spend_minor": int(ad_spend_minor or 0),
        # The audit trail. Every raw row is accounted for in exactly one bucket,
        # so `raw_order_rows` always equals the sum of `order_breakdown`, and a
        # seller asking "where did my 32 orders go" has an answer on the wire.
        "raw_order_rows": len(order_rows),
        "raw_listing_rows": len(listing_rows),
        "order_breakdown": by_order_state,
        "listing_breakdown": by_listing_state,
        "unmatched_payments": len(unmatched_payment_candidates(order_rows)),
    }


def _quantity(row: Mapping[str, Any]) -> int:
    raw = row.get("metadata_json")
    if isinstance(raw, Mapping):
        metadata = raw
    else:
        try:
            metadata = json.loads(raw or "{}")
        except (TypeError, ValueError):
            metadata = {}
    if not isinstance(metadata, Mapping):
        return 1
    try:
        quantity = int(metadata.get("qty") or metadata.get("quantity") or 1)
    except (TypeError, ValueError):
        return 1
    return quantity if quantity > 0 else 1
