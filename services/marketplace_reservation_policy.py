"""Marketplace inventory reservation policy — the single source of truth.

Why this module exists
----------------------
Before this file, a reservation was a bare stock decrement plus a row whose
``status`` was ``held``, with an in-code comment promising that
"expiry/failure restores it". There was no expiry column, no sweeper, and no
``payment_intent.canceled`` handler, so a buyer who dismissed the Apple Pay /
PaymentSheet UI left the row ``held`` forever and the decremented listing
quantity never came back. Stripe fires no webhook for a dismissed sheet, so
nothing downstream could ever notice.

Every TTL, status string and release reason used by the reservation lifecycle
lives here so that the checkout route, the Stripe webhook branches and the
expiry sweeper cannot drift apart. The directive is explicit: one canonical
configuration constant, not scattered literals.

The lifecycle
-------------
::

    AVAILABLE ──reserve──> RESERVED ──capture──> CAPTURED   (terminal, paid)
                              │
                              └──release──────> RELEASED    (terminal, stock returned)

``RESERVED`` is stored as ``status='held'`` — the value already written by the
existing code and already read by ``capture_inventory_reservation`` and
``release_inventory_reservation``. Reusing it keeps this change additive: no
backfill, no dual-write window, and no second status column that could
disagree with the first. The mission's conceptual ``reservation_status`` is
this column; the conceptual ``reserved_at`` / ``expires_at`` / ``released_at``
are new columns beside it.

Both terminal states are absorbing. A capture only fires ``WHERE
status='held'`` and a release only fires ``WHERE status='held'``, which is what
makes both operations idempotent and makes it impossible for a paid
reservation to be released or for stock to be double-incremented — the two
failure modes that would cost real money.

Expiry authority
----------------
``expires_at`` is durable, written at reservation time, and evaluated
server-side. A client-side cancel signal is an *optimization* that releases
sooner; it is never the only path, because an app can crash, lose its network,
or be force-quit before it sends anything. If the client says nothing at all,
the sweeper still collects the row.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------
# Status vocabulary
# --------------------------------------------------------------------------

#: Stock is decremented and held for one specific ``seller_transaction_id``.
STATUS_HELD = "held"
#: Payment settled. Stock is consumed; it must never be returned to the listing.
STATUS_CAPTURED = "captured"
#: Reservation ended without payment. Stock has been returned exactly once.
STATUS_RELEASED = "released"

#: The only status from which a reservation may still change.
ACTIVE_STATUSES = frozenset({STATUS_HELD})
#: Absorbing states. Any transition attempt out of these is a silent no-op.
TERMINAL_STATUSES = frozenset({STATUS_CAPTURED, STATUS_RELEASED})
ALL_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES


# --------------------------------------------------------------------------
# Release reasons
# --------------------------------------------------------------------------
# Both terminal states stay a closed two-value set so the idempotency guard
# remains a single ``status='held'`` predicate. *Why* a reservation was
# released is recorded separately, in ``release_reason``, which keeps the
# audit trail rich without multiplying the states the guard has to reason
# about. An expired reservation and a buyer-cancelled one are the same state
# with different provenance.

#: Buyer dismissed the payment sheet and the app told us so. An optimization.
REASON_BUYER_CANCELLED = "buyer_cancelled"
#: Durable TTL elapsed with no settlement. The authoritative path.
REASON_EXPIRED = "expired"
#: Stripe reported ``payment_intent.payment_failed``.
REASON_PAYMENT_FAILED = "payment_failed"
#: Stripe reported ``payment_intent.canceled``.
REASON_PAYMENT_CANCELED = "payment_canceled"
#: Checkout raised before the buyer ever reached Stripe; roll the hold back.
REASON_CHECKOUT_ERROR = "checkout_error"
#: A sibling line in the same cart sold out, so the whole group is unwound.
REASON_OUT_OF_STOCK_ROLLBACK = "out_of_stock_rollback"
#: An operator released it by hand. Recorded, never silent.
REASON_MANUAL = "manual"

RELEASE_REASONS = frozenset({
    REASON_BUYER_CANCELLED,
    REASON_EXPIRED,
    REASON_PAYMENT_FAILED,
    REASON_PAYMENT_CANCELED,
    REASON_CHECKOUT_ERROR,
    REASON_OUT_OF_STOCK_ROLLBACK,
    REASON_MANUAL,
})


# --------------------------------------------------------------------------
# TTL — the one canonical constant
# --------------------------------------------------------------------------

#: Default hold window. Long enough for a buyer to authorise Apple Pay, read a
#: 3-D Secure SMS and retype a card; short enough that a dismissed sheet does
#: not strand a scarce item for an hour. The directive's suggested band is
#: 10-15 minutes; 15 is chosen because 3-D Secure step-up is the slow path and
#: releasing stock out from under a buyer who is mid-authentication is the
#: worse of the two errors.
DEFAULT_TTL_SECONDS = 15 * 60

#: Hard bounds. An operator may retune the window but cannot configure the
#: system into a state that is unsafe in either direction: a TTL under five
#: minutes would cancel legitimate 3-D Secure authentications, and one over an
#: hour makes an abandoned sheet indistinguishable from a real outage.
MIN_TTL_SECONDS = 5 * 60
MAX_TTL_SECONDS = 60 * 60

#: The environment variable an operator may set. Absent or unparseable falls
#: back to the default rather than failing checkout — a typo in configuration
#: must not take the store offline.
TTL_ENV_VAR = "MARKETPLACE_RESERVATION_TTL_SECONDS"

#: Grace period applied before the sweeper acts on an expired row. Absorbs
#: clock skew between the web dyno and the worker, and gives a
#: ``payment_intent.succeeded`` webhook that is racing the deadline a moment to
#: land. Reconciliation (Stage 5) is still consulted; this only avoids waking
#: the reconciler for rows that are barely past due.
EXPIRY_GRACE_SECONDS = 60


def reservation_ttl_seconds() -> int:
    """The configured hold window, clamped into the safe band.

    Read on each call rather than cached at import: the sweeper is a
    long-lived worker process, and an operator who retunes the window should
    not have to wait for a redeploy for it to take effect.
    """
    raw = (os.environ.get(TTL_ENV_VAR) or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    if value < MIN_TTL_SECONDS:
        return MIN_TTL_SECONDS
    if value > MAX_TTL_SECONDS:
        return MAX_TTL_SECONDS
    return value


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------
# Timestamps are ISO-8601 strings to match every other column in this
# subsystem. ``parse_timestamp`` is deliberately forgiving because rows written
# before this change have no ``expires_at`` at all, and a legacy row must not
# crash the sweeper.

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def parse_timestamp(value) -> datetime | None:
    """Best-effort ISO-8601 parse, always returned timezone-aware in UTC.

    Returns ``None`` for anything unparseable — including the empty
    ``expires_at`` on a pre-migration row — so callers can treat "no known
    deadline" as its own case instead of guessing one.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def expires_at_for(reserved_at=None, *, ttl_seconds: int | None = None) -> str:
    """The deadline to persist alongside a new reservation."""
    base = parse_timestamp(reserved_at) or now_utc()
    ttl = reservation_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    return (base + timedelta(seconds=ttl)).isoformat(timespec="seconds")


def is_expired(expires_at, *, now=None, grace_seconds: int | None = None) -> bool:
    """Whether a deadline has passed, including the sweeper's grace period.

    A row with no parseable ``expires_at`` returns ``False``. Rows written
    before this migration are in exactly that position, and inventing a
    retroactive deadline for them would release stock for orders that may be
    legitimately mid-flight. They are surfaced by ``legacy_backfill_expiry``
    instead, which gives them a real deadline going forward.
    """
    deadline = parse_timestamp(expires_at)
    if deadline is None:
        return False
    grace = EXPIRY_GRACE_SECONDS if grace_seconds is None else int(grace_seconds)
    return (parse_timestamp(now) or now_utc()) >= deadline + timedelta(seconds=grace)


def legacy_backfill_expiry(created_at, *, now=None) -> str:
    """A deadline for a row that predates ``expires_at``.

    Derived from the row's own ``created_at`` where that is readable, so an
    already-stale legacy hold becomes immediately collectable rather than
    winning a fresh full TTL. Where ``created_at`` is unreadable the deadline
    is measured from now, which errs toward holding stock slightly too long
    rather than releasing an order that might still settle.
    """
    anchor = parse_timestamp(created_at) or parse_timestamp(now) or now_utc()
    return expires_at_for(anchor)
