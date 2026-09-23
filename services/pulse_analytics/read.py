"""Seller-scoped reads over the canonical commerce event tables.

Two rules hold everywhere in this module, and both are enforced here rather
than left to callers.

**The seller identity is an argument, never a filter the caller can widen.**
Every query below pins ``seller_user_id`` in its ``WHERE`` clause from the value
passed in, and :func:`_seller_id` rejects anything that is not a positive
integer. A route obtains that value from the session and nowhere else. There is
deliberately no "all sellers" mode and no ``seller_user_id`` parameter read off
a request: an operator view would be a different function with a different
authorisation check, and the cheapest way to never ship a horizontal escalation
is to not write the code path that could become one.

**``subject_ref`` never leaves this module.** It is a salted hash and not
reversible, but it is *stable*, which is enough to count how many times one
person came back and to join a viewer across every listing a seller owns.
``services/commerce_discovery/subject.py`` is explicit that these tables hold "a
behavioural profile … the kind of table that gets exported to a BI tool by
someone who never read this file." Projecting the column out at the read layer
means a seller-facing payload cannot carry it even by accident, so the
``SELECT`` lists below name their columns and never use ``*``.

Reads fail soft. An unreadable event log is a missing measurement, not a 500 on
a page whose other half is fine — the same posture the rest of
``commerce_discovery`` takes.

Why the queries lead with ``listing_id``
----------------------------------------

There is no index on these tables leading with ``seller_user_id``. The only one
that mentions it is ``idx_cd_impr_seller_freq (subject_ref, seller_user_id,
event_at)``, which leads with the viewer because every question the frequency
cap asks is about one viewer — so it cannot serve "everything for this seller".
Scoping on ``seller_user_id`` alone would table-scan the impression log on every
dashboard load, and would get slower precisely as the platform succeeded.

So the scope is resolved to the seller's listing ids first and the event query
runs against ``idx_cd_impr_listing (listing_id, event_at)``, which exists. No
index is added and no shared schema is touched.

The event row's own ``seller_user_id`` is still required to match. It is a
denormalised snapshot taken when the impression was written, so the two can
disagree — if a listing ever changes hands, the historical rows still name the
previous owner. Requiring both means a new owner cannot read the old owner's
history, and an old owner cannot read rows for a listing they no longer hold.
Either alone would leak in one of those two directions.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from services.commerce_discovery import subject

LOGGER = logging.getLogger(__name__)

#: Default window. Seven days matches the seller dashboard's sparkline, so the
#: funnel and the money beside it describe the same stretch of time.
DEFAULT_WINDOW_SECONDS = 7 * 24 * 60 * 60

#: A seller with more impressions than this in the window gets a truncated
#: funnel rather than a slow one. Reported via ``truncated`` so the caller can
#: say so instead of quietly showing a smaller number.
MAX_ROWS = 20000


class SellerScopeError(ValueError):
    """Raised when a read is attempted without a usable seller identity."""


def _seller_id(value: Any) -> int:
    """The seller identity, or a refusal. Never a coercion.

    ``int()`` is not used as the gate, because it is lossy in a direction that
    matters here: ``int(1.5)`` is ``1``, so a float arriving where an id was
    expected would not fail, it would silently address *a different seller*.
    ``True`` is likewise ``1``. Both are caller bugs, and the safe response to a
    caller bug on an authorisation path is to stop.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        if not (isinstance(value, str) and value.strip().lstrip("-").isdigit()):
            raise SellerScopeError(f"seller_user_id must be an integer, got {type(value).__name__}")
        value = int(value)
    if value <= 0:
        raise SellerScopeError("seller_user_id must be positive")
    return value


def _rows(cur) -> list[dict]:
    fetched = cur.fetchall() or []
    return [row if isinstance(row, dict) else dict(row) for row in fetched]


def seller_listing_ids(cur, seller_user_id: Any, *, limit: int = 5000) -> list[int]:
    """The listing ids this seller owns *now*, which is what defines the scope.

    Ownership is read from ``marketplace_listings`` rather than trusted from the
    event rows, so the authorisation decision is made against the current state
    of the world and not against a copy taken months ago.

    Deleted listings are included. A seller is entitled to the history of a
    listing they have since removed — withholding it would make a store's
    numbers shrink retroactively whenever they tidied up.
    """
    seller_id = _seller_id(seller_user_id)
    try:
        cur.execute(
            "SELECT id FROM marketplace_listings WHERE seller_user_id=? LIMIT ?",
            (seller_id, max(1, int(limit))),
        )
    except Exception:
        LOGGER.warning("PULSE_ANALYTICS_LISTINGS_UNREADABLE", exc_info=True)
        return []
    ids = []
    for row in _rows(cur):
        try:
            listing_id = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if listing_id > 0:
            ids.append(listing_id)
    return ids


def _scoped(
    cur,
    table: str,
    columns: str,
    seller_id: int,
    listing_ids: list[int],
    *,
    since_seconds: Optional[int],
    surface: str,
    limit: int,
    extra: tuple[str, ...] = (),
) -> list[dict]:
    if not listing_ids:
        return []
    window = DEFAULT_WINDOW_SECONDS if since_seconds is None else int(since_seconds)
    placeholders = ",".join("?" for _ in listing_ids)
    clauses = [
        f"listing_id IN ({placeholders})",
        "seller_user_id=?",
        "event_at>?",
        *extra,
    ]
    params: list[Any] = [*listing_ids, seller_id, subject.window_start_iso(max(60, window))]
    if surface:
        clauses.append("surface=?")
        params.append(str(surface).strip().lower())
    params.append(max(1, int(limit)))
    try:
        cur.execute(
            f"SELECT {columns} FROM {table} WHERE {' AND '.join(clauses)} "
            "ORDER BY event_at DESC LIMIT ?",
            tuple(params),
        )
        return _rows(cur)
    except Exception:
        LOGGER.warning("PULSE_ANALYTICS_EVENTS_UNREADABLE", exc_info=True, extra={"table": table})
        return []


def impressions(
    cur,
    seller_user_id: Any,
    *,
    listing_ids: Optional[list[int]] = None,
    since_seconds: Optional[int] = None,
    surface: str = "",
    limit: int = MAX_ROWS,
) -> list[dict]:
    """This seller's impression rows, newest first.

    ``self_view`` rows are excluded in SQL rather than in the aggregate. A
    seller previewing their own listing generates real impression rows, and
    leaving them in would let a seller inflate their own exposure count simply
    by scrolling their own store — which also silently deflates every rate
    computed against it.
    """
    seller_id = _seller_id(seller_user_id)
    ids = seller_listing_ids(cur, seller_id) if listing_ids is None else listing_ids
    return _scoped(
        cur,
        "commerce_discovery_impression_events",
        "event_id, listing_id, seller_user_id, surface, slot, promotion_class, "
        "reason_code, visible, view_duration_ms, self_view, event_at",
        seller_id,
        ids,
        since_seconds=since_seconds,
        surface=surface,
        limit=limit,
        extra=("self_view=0",),
    )


def orders(
    cur,
    seller_user_id: Any,
    *,
    since_seconds: Optional[int] = None,
    limit: int = MAX_ROWS,
) -> Optional[list[dict]]:
    """This seller's order rows over the same window as the events.

    Returns ``None`` when the table cannot be read — the one read in this module
    that does not fail soft to an empty list.

    The event reads may: an unreadable impression log means no impressions,
    every rate goes ``None``, and the dashboard says so. An unreadable *order*
    table resolving to ``[]`` would instead flow through
    ``seller_metrics.compute`` and come back as ``confirmed_orders: 0`` — a
    definite number, indistinguishable on the wire from a seller who genuinely
    sold nothing. That is this codebase's signature defect wearing a new hat, so
    the ambiguity is pushed onto the caller, where ``None`` cannot be iterated
    by accident.

    Windowed here, and in Python rather than in SQL. ``seller_transactions``
    holds ISO timestamps in two shapes — with and without an offset — so a
    ``created_at>?`` comparison is a string sort over two formats. It happens to
    order correctly for the values written today, which is exactly the kind of
    accident that stops being true later.

    The window matters more than it looks. ``confirmed_orders`` from
    ``seller_metrics`` is a lifetime figure when it is handed every row, and the
    funnel's impressions are seven days: subtracting one from the other to get
    ``client_server_purchase_gap`` would compare a week of clicks against a
    year of sales and report the store's whole history as a discrepancy. The
    caller feeds these same rows to both, so both describe one window.

    No status filter. Deciding which of these is a sale is
    ``seller_metrics.is_confirmed_order``'s job and is not repeated here — the
    unconfirmed rows have to arrive so they can be excluded by the one predicate
    that owns the question.
    """
    seller_id = _seller_id(seller_user_id)
    window = DEFAULT_WINDOW_SECONDS if since_seconds is None else int(since_seconds)
    cutoff = subject.parse_iso(subject.window_start_iso(max(60, window)))
    try:
        cur.execute(
            "SELECT id, status, amount_cents, currency, item_id, item_type, "
            "stripe_payment_intent_id, metadata_json, created_at "
            "FROM seller_transactions WHERE seller_user_id=? ORDER BY created_at DESC LIMIT ?",
            (seller_id, max(1, int(limit))),
        )
        rows = _rows(cur)
    except Exception:
        LOGGER.warning("PULSE_ANALYTICS_ORDERS_UNREADABLE", exc_info=True)
        return None
    kept = []
    for row in rows:
        at = subject.parse_iso(row.get("created_at"))
        # An unparseable timestamp is kept. Dropping it would silently shrink a
        # seller's order count on the strength of a formatting fault, and these
        # rows are money.
        if at is None or cutoff is None or at > cutoff:
            kept.append(row)
    return kept


def engagements(
    cur,
    seller_user_id: Any,
    *,
    listing_ids: Optional[list[int]] = None,
    since_seconds: Optional[int] = None,
    surface: str = "",
    limit: int = MAX_ROWS,
) -> list[dict]:
    """This seller's engagement rows, newest first."""
    seller_id = _seller_id(seller_user_id)
    ids = seller_listing_ids(cur, seller_id) if listing_ids is None else listing_ids
    return _scoped(
        cur,
        "commerce_discovery_engagement_events",
        "event_id, listing_id, seller_user_id, surface, action, promotion_class, event_at",
        seller_id,
        ids,
        since_seconds=since_seconds,
        surface=surface,
        limit=limit,
    )
