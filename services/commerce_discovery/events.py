"""Recording what happened to a placement, idempotently and without PII.

Three write paths — impression, engagement, feedback — and one rule they all
obey: **the client supplies the verb, the server supplies every noun.** A
request says "this placement became visible" or "this placement was clicked",
and carries a placement id plus its HMAC. Listing, seller, promotion class,
reason and ranking version are read back from the stored placement row. A
client cannot report an impression against a product it was never served, and it
cannot reclassify a placement's promotion class — which is what keeps the
organic/paid wall from being a client-side honour system.

Idempotency
-----------

Every row carries a ``dedup_key`` with a UNIQUE index, and a collision is
answered with ``{"ok": True, "duplicate": True}`` rather than an error. Mobile
clients retry: a backgrounded app replays its impression queue, a flaky network
turns one visible impression into three POSTs. Without dedup those become three
rows, and — because nobody is being billed — no reconciliation anywhere would
ever catch it. The numbers would simply be too high, forever, in the direction
that flatters the feature.

The dedup key is derived from the facts that make an event *the same event*,
never from a client-supplied nonce. ``impr:<placement>`` and
``vis:<placement>`` are one-per-placement by construction. Engagement keys
include the action, so a click and a later purchase on the same placement are
distinct events while two reports of the same click are not.

Self-view
---------

A seller viewing their own listing is recorded with ``self_view=1`` rather than
dropped. Dropping it would make a seller's own testing invisible and their
reach numbers quietly inconsistent with their impression log; flagging it lets
every downstream reader exclude it explicitly. There is no billing consequence
here — that is the entire difference between this and the advertising path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

from . import config, promotion, ranking, schema, subject

LOGGER = logging.getLogger(__name__)


#: Post-click funnel steps, in order. The client names one; the server does not
#: infer ordering or backfill missing steps — a purchase without a recorded
#: click is a real thing (the user saved the product and bought it days later)
#: and inventing the click would make attribution a fiction.
ENGAGEMENT_ACTIONS = (
    "click",
    "product_view",
    "save",
    "add_to_cart",
    "checkout_started",
    "purchase",
)

#: Event names as the client emits them, mapped to the write path that owns
#: them. Exposed so the mobile analytics layer and this module cannot drift
#: into two different vocabularies.
CLIENT_EVENT_NAMES = {
    "commerce_impression": "impression",
    "commerce_visible_impression": "impression",
    "commerce_click": "engagement",
    "commerce_product_view": "engagement",
    "commerce_save": "engagement",
    "commerce_add_to_cart": "engagement",
    "commerce_checkout_started": "engagement",
    "commerce_purchase": "engagement",
    "commerce_hide": "feedback",
    "commerce_not_interested": "feedback",
    "commerce_hide_seller": "feedback",
    "commerce_see_fewer": "feedback",
    "commerce_view_duration": "impression",
}


class DiscoveryEventError(ValueError):
    """A write was refused. Carries a stable client-facing code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _rows(cur) -> list[dict]:
    try:
        fetched = cur.fetchall() or []
    except Exception:
        return []
    return [row if isinstance(row, dict) else dict(row) for row in fetched]


def _one(cur) -> dict:
    rows = _rows(cur)
    return rows[0] if rows else {}


def _meta(request_meta: Any) -> Optional[str]:
    """Serialize non-sensitive request metadata, or ``None``.

    Truncated and best-effort. Nothing is validated as non-PII here because
    nothing *can* be — the caller is the only party that knows what it put in
    the dict, and the route pack is the one place that builds it.
    """
    if request_meta in (None, ""):
        return None
    try:
        return json.dumps(request_meta, sort_keys=True, separators=(",", ":"))[:2000]
    except Exception:
        return None


def load_placement(cur, placement_id: Any, token: Any) -> dict:
    """The authoritative placement row, or raise.

    Four checks in a fixed order, all of which must pass before any write. The
    token check runs against the *derived* HMAC rather than a stored comparison
    so a placement row cannot be forged by anyone with table write access but
    not the secret.
    """
    pid = subject.sid(placement_id or "").strip()
    if not pid:
        raise DiscoveryEventError("INVALID_PLACEMENT", "Missing placement id.")
    if not subject.verify_token(pid, token):
        raise DiscoveryEventError("INVALID_TOKEN", "Placement token did not verify.")

    cur.execute(
        "SELECT * FROM commerce_discovery_placements WHERE placement_id=? LIMIT 1", (pid,)
    )
    row = _one(cur)
    if not row:
        raise DiscoveryEventError("UNKNOWN_PLACEMENT", "No such placement.")
    if subject.is_expired(row.get("expires_at")):
        raise DiscoveryEventError("PLACEMENT_EXPIRED", "This placement is no longer current.")
    # Belt and braces: a paid placement must never have been written to this
    # table in the first place, so finding one means something upstream is
    # wrong and the event must not be recorded as unpaid reach.
    promotion.assert_unpaid(row.get("promotion_class"))
    return row


def _insert_idempotent(cur, table: str, columns: list[str], values: list[Any], dedup_key: str) -> dict:
    """Insert, or report the existing row on a dedup collision.

    The collision is caught rather than pre-checked. A SELECT-then-INSERT has a
    race two concurrent retries can both win, and this table has no other
    guard — the UNIQUE index is the actual mechanism and the exception handler
    is how it is observed.
    """
    marks = ",".join("?" for _ in columns)
    try:
        cur.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})", values
        )
        return {"ok": True, "duplicate": False}
    except Exception as exc:
        text = f"{type(exc).__name__}:{exc}"
        if "UNIQUE" not in text.upper() and "INTEGRITY" not in text.upper() and "DUPLICATE" not in text.upper():
            raise
        cur.execute(f"SELECT event_id FROM {table} WHERE dedup_key=? LIMIT 1", (dedup_key,))
        existing = _one(cur)
        return {"ok": True, "duplicate": True, "event_id": existing.get("event_id")}


def record_impression(
    cur,
    placement_id: Any,
    token: Any,
    *,
    conn,
    viewer_user_id: Any = None,
    visible: bool = False,
    view_duration_ms: int = 0,
    request_meta: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Record that a placement was rendered, or that it became visible.

    Two distinct rows, keyed by different dedup prefixes, because they answer
    different questions. ``visible=False`` means "this consumed a slot" — it is
    what the cadence and session caps count. ``visible=True`` means "a human
    could actually see it for long enough" (``config.VISIBLE_PERCENT_THRESHOLD``
    / ``VISIBLE_DWELL_MS``) — it is what reach and conversion rates are computed
    against. Collapsing them would make one number wrong whichever definition
    was chosen.
    """
    schema.ensure_schema(conn)
    row = load_placement(cur, placement_id, token)
    pid = row["placement_id"]
    prefix = "vis" if visible else "impr"
    dedup_key = f"{prefix}:{pid}"

    self_view = 0
    if viewer_user_id is not None:
        try:
            self_view = 1 if int(viewer_user_id) == int(row.get("seller_user_id") or 0) else 0
        except (TypeError, ValueError):
            self_view = 0

    now = subject.now_iso()
    return _insert_idempotent(
        cur,
        "commerce_discovery_impression_events",
        [
            "event_id", "placement_id", "subject_ref", "surface", "slot", "listing_id",
            "seller_user_id", "promotion_class", "reason_code", "ranking_version",
            "session_id", "visible", "view_duration_ms", "self_view",
            "request_meta_json", "event_at", "dedup_key", "created_at",
        ],
        [
            subject.new_id("cd_impr"), pid, row["subject_ref"], row["surface"],
            int(row.get("slot") or 0), int(row["listing_id"]),
            int(row.get("seller_user_id") or 0), row["promotion_class"],
            row.get("reason_code") or "", row["ranking_version"],
            row.get("session_id") or "", 1 if visible else 0,
            max(0, int(view_duration_ms or 0)), self_view,
            _meta(request_meta), now, dedup_key, now,
        ],
        dedup_key,
    )


#: Verbs that carry an intent value rather than a settled one. A cart addition
#: and a checkout entry are both worth the line total of the thing being bought,
#: and neither is revenue — nothing has been paid. They are kept because the
#: funnel is the point: knowing that £400 of intent entered checkout and £40 came
#: out is a different and more useful fact than two counts.
_INTENT_VERBS = ("add_to_cart", "checkout_started")

#: Ceiling on a claimed quantity, applied when the listing declares no stock of
#: its own. Not a business rule — a bound, so that one malformed or hostile
#: request cannot contribute a number large enough to dominate an aggregate.
_MAX_CLAIMED_QUANTITY = 99


def _settled_value(cur, *, order_ref: str, listing_id: int, buyer_user_id: Any) -> tuple[int, str]:
    """What a purchase was actually worth, read from the order that proves it.

    The lookup is scoped by buyer *and* listing, which is what makes a guessed or
    borrowed ``order_ref`` worthless: it can only ever resolve to an order this
    viewer really placed for the product this placement really showed. Unpaid
    orders are not revenue, so the status filter is part of the check rather than
    a refinement of it.

    Returns ``(0, "")`` for anything it cannot substantiate — including a database
    failure, because a read that did not run must not be reported as a sale that
    was worth nothing. The two failures log under different codes for exactly that
    reason.
    """
    try:
        cur.execute(
            "SELECT amount_cents, currency FROM marketplace_orders "
            "WHERE buyer_user_id=? AND listing_id=? "
            "AND (CAST(id AS TEXT)=? OR provider_payment_id=?) "
            "AND LOWER(COALESCE(status,'')) IN ('paid','fulfilled','completed') "
            "LIMIT 1",
            (int(buyer_user_id), int(listing_id), str(order_ref), str(order_ref)),
        )
        row = _one(cur)
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_VALUE_LOOKUP_FAILED listing=%s", listing_id, exc_info=True)
        return 0, ""
    if not row:
        # Logged rather than raised: the purchase happened and the event is worth
        # keeping. What is not worth keeping is an unverifiable amount attached to
        # it, and a silent zero would be indistinguishable from a free product.
        LOGGER.warning(
            "COMMERCE_DISCOVERY_VALUE_UNRECONCILED listing=%s order_ref=%s",
            listing_id, str(order_ref)[:24],
        )
        return 0, ""
    return max(0, int(row.get("amount_cents") or 0)), str(row.get("currency") or "").upper()[:8]


def _intent_value(cur, *, listing_id: int, buyer_user_id: Any, claimed_quantity: Any, parse_price) -> tuple[int, str]:
    """The line total a cart addition or checkout entry represents.

    Price is a fact; quantity is a claim. That distinction is the whole design
    here, and it is forced by the product: "Buy now" deliberately bypasses the
    cart — see ``MarketplaceProductScreen.handleBuyNow`` — so at the moment
    ``checkout_started`` fires there is no server-side row stating how many the
    buyer chose. A fully derived value is therefore not available for that verb,
    and pretending otherwise would mean either dropping the funnel signal or
    quietly trusting a total again.

    So the unit price comes from the listing, through the same parser the
    eligibility gate uses, and the quantity is the *server's* cart row when one
    exists — ``add_to_cart`` is emitted after the cart write completes, so it
    usually does — and otherwise the caller's claim clamped to what the listing
    can actually sell. The worst a hostile client can now do is overstate how many
    of a real product it intended to buy, bounded by that product's own stock.
    """
    if not parse_price:
        # Without the parser there is no authoritative price, and a value derived
        # from no price is a made-up number. Silence beats invention.
        return 0, ""
    try:
        cur.execute(
            "SELECT price_label, currency, quantity FROM marketplace_listings WHERE id=? LIMIT 1",
            (int(listing_id),),
        )
        listing = _one(cur)
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_VALUE_LOOKUP_FAILED listing=%s", listing_id, exc_info=True)
        return 0, ""
    if not listing:
        return 0, ""

    from . import eligibility

    priced = eligibility.resolvable_price(listing, parse_price)
    if priced is None:
        # "Request access" and friends. An unpriced listing has no line total, and
        # the eligibility gate already refuses to place it.
        return 0, ""
    unit_minor, currency = priced

    stock = int(listing.get("quantity") or 0)
    ceiling = stock if stock > 0 else _MAX_CLAIMED_QUANTITY
    quantity = 0
    if buyer_user_id:
        try:
            cur.execute(
                "SELECT qty FROM marketplace_cart_items WHERE user_id=? AND listing_id=? LIMIT 1",
                (int(buyer_user_id), int(listing_id)),
            )
            cart = _one(cur)
            quantity = int(cart.get("qty") or 0) if cart else 0
        except Exception:
            LOGGER.debug("COMMERCE_DISCOVERY_CART_QTY_UNAVAILABLE listing=%s", listing_id, exc_info=True)
    if quantity <= 0:
        try:
            quantity = int(claimed_quantity or 1)
        except (TypeError, ValueError):
            quantity = 1
    quantity = max(1, min(quantity, ceiling))
    return unit_minor * quantity, (currency or "").upper()[:8]


def _reconciled_value(
    cur,
    *,
    verb: str,
    order_ref: str,
    listing_id: int,
    buyer_user_id: Any,
    claimed_quantity: Any = None,
    parse_price=None,
) -> tuple[int, str]:
    """The money this event is worth, derived rather than accepted.

    ``value_minor`` used to be whatever number the request body contained, and it
    was reaching production: ``MarketplaceProductScreen`` sends ``unitMinor * qty``
    on every cart addition and checkout entry, both computed on the device. The
    column is named after money, typed like money, and sits in a table an analyst
    will eventually sum — so the amount credited to a placement was an assertion
    by the party that benefits from it, and nothing in the schema or the signature
    said so.

    Three cases, in descending order of how much can be proven:

    * ``purchase`` — settled money, read from the buyer's own paid order.
    * ``add_to_cart`` / ``checkout_started`` — intent, priced from the listing.
    * everything else — nothing. A click is not worth its product's price, and
      storing the number anyway is how a view would later be summed as a sale.
    """
    if verb == "purchase":
        if not order_ref or not buyer_user_id:
            return 0, ""
        return _settled_value(cur, order_ref=order_ref, listing_id=listing_id, buyer_user_id=buyer_user_id)
    if verb in _INTENT_VERBS:
        return _intent_value(
            cur,
            listing_id=listing_id,
            buyer_user_id=buyer_user_id,
            claimed_quantity=claimed_quantity,
            parse_price=parse_price,
        )
    return 0, ""


def record_engagement(
    cur,
    placement_id: Any,
    token: Any,
    action: str,
    *,
    conn,
    buyer_user_id: Any = None,
    order_ref: str = "",
    claimed_quantity: Any = None,
    parse_price=None,
    request_meta: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Record one post-impression outcome.

    Unlike the advertising path, a click here does **not** require a prior
    impression row. It should have one, and almost always will, but an app that
    was killed before flushing its impression queue and relaunched into a tapped
    deep link is a real sequence — and refusing the click would lose the more
    valuable of the two events to protect an integrity property that nobody is
    being billed against.

    There is deliberately no ``value_minor`` or ``currency`` parameter. Both are
    derived — see :func:`_reconciled_value` — because a caller that *can* state a
    revenue figure is a caller whose figure someone will one day trust.
    ``claimed_quantity`` is named for what it is: the one remaining client input,
    used only to multiply a server-owned unit price and clamped to the listing's
    own stock.

    ``buyer_user_id`` is used for those lookups and is never stored. This table is
    keyed by ``subject_ref`` on purpose, and the account identity is here to
    authorise a read, not to be recorded alongside the behaviour.
    """
    schema.ensure_schema(conn)
    verb = str(action or "").strip().lower()
    if verb not in ENGAGEMENT_ACTIONS:
        raise DiscoveryEventError("UNKNOWN_ACTION", f"Unsupported engagement action: {action!r}")

    row = load_placement(cur, placement_id, token)
    pid = row["placement_id"]
    dedup_key = f"eng:{verb}:{pid}"
    if verb == "purchase" and order_ref:
        # One placement can legitimately produce two purchases of the same
        # product (a reorder) and they are distinguished by the order, not by
        # the placement. Without this, the second sale would dedup away and the
        # feature would under-report exactly the outcome it exists to produce.
        dedup_key = f"eng:purchase:{pid}:{order_ref}"

    cur.execute(
        "SELECT event_id FROM commerce_discovery_impression_events "
        "WHERE placement_id=? ORDER BY visible DESC LIMIT 1",
        (pid,),
    )
    impression_event_id = _one(cur).get("event_id")
    value_minor, currency = _reconciled_value(
        cur,
        verb=verb,
        order_ref=order_ref,
        listing_id=int(row["listing_id"]),
        buyer_user_id=buyer_user_id,
        claimed_quantity=claimed_quantity,
        parse_price=parse_price,
    )

    now = subject.now_iso()
    return _insert_idempotent(
        cur,
        "commerce_discovery_engagement_events",
        [
            "event_id", "placement_id", "impression_event_id", "subject_ref", "surface",
            "listing_id", "seller_user_id", "promotion_class", "reason_code",
            "ranking_version", "session_id", "action", "value_minor", "currency",
            "order_ref", "request_meta_json", "event_at", "dedup_key", "created_at",
        ],
        [
            subject.new_id("cd_eng"), pid, impression_event_id, row["subject_ref"],
            row["surface"], int(row["listing_id"]), int(row.get("seller_user_id") or 0),
            row["promotion_class"], row.get("reason_code") or "", row["ranking_version"],
            row.get("session_id") or "", verb, value_minor,
            currency or None, (order_ref or "")[:120] or None,
            _meta(request_meta), now, dedup_key, now,
        ],
        dedup_key,
    )


def record_feedback(
    cur,
    placement_id: Any,
    token: Any,
    action: str,
    *,
    conn,
    request_meta: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Record a negative signal **and** apply the suppression it implies.

    Both, in one call and one transaction, because a user who taps "Don't
    recommend this seller" has made a request, not filed a report. Logging the
    event without writing the suppression would satisfy analytics and fail the
    person — and the failure would be invisible until the seller reappeared.

    Suppression durations, and why they differ:

    * ``hide`` — this card, this session. 24h on the product, because the user
      dismissed *this* impression, not the product forever.
    * ``not_interested`` — the product, permanently.
    * ``hide_seller`` — the seller, permanently. Never expires. See
      ``preferences.record_suppression``.
    * ``see_fewer`` — a soft bias on the category, for 30 days. Stored as a
      suppression row with ``source_action='see_fewer'`` so there is one place
      to look for "what has this user told us", but read as a ranking penalty
      rather than a filter.
    * ``snooze`` — everything, for ``COMMERCE_DISCOVERY_HIDE_DAYS``.
    """
    from . import preferences as prefs

    schema.ensure_schema(conn)
    verb = str(action or "").strip().lower()
    if verb not in schema.FEEDBACK_ACTIONS:
        raise DiscoveryEventError("UNKNOWN_ACTION", f"Unsupported feedback action: {action!r}")

    row = load_placement(cur, placement_id, token)
    pid = row["placement_id"]
    ref = row["subject_ref"]
    listing_id = int(row["listing_id"])
    seller_id = int(row.get("seller_user_id") or 0)
    category = str((request_meta or {}).get("category") or "").strip().lower()

    if verb == "hide":
        prefs.record_suppression(cur, ref, scope="product", value=listing_id, action=verb, ttl_seconds=86400)
    elif verb == "not_interested":
        prefs.record_suppression(cur, ref, scope="product", value=listing_id, action=verb, ttl_seconds=None)
    elif verb == "hide_seller":
        prefs.record_suppression(cur, ref, scope="seller", value=seller_id, action=verb, ttl_seconds=None)
    elif verb == "see_fewer":
        # Falls back to the seller token when the card carried no category —
        # "see fewer like this" has to soften *something* or the control is a
        # no-op the user cannot tell from a working one.
        target = category or f"seller:{seller_id}"
        prefs.record_suppression(cur, ref, scope="product", value=target, action=verb, ttl_seconds=2592000)
    elif verb == "snooze":
        prefs.record_suppression(
            cur, ref, scope="all", value="all", action=verb,
            ttl_seconds=config.hide_days() * 86400,
        )

    now = subject.now_iso()
    # Not deduped on the placement alone: a user may hide a card, see another
    # from the same seller, and hide that one too. Both are real signals.
    dedup_key = f"fb:{verb}:{pid}"
    return _insert_idempotent(
        cur,
        "commerce_discovery_feedback_events",
        [
            "event_id", "placement_id", "subject_ref", "surface", "listing_id",
            "seller_user_id", "promotion_class", "reason_code", "action",
            "session_id", "event_at", "dedup_key", "created_at",
        ],
        [
            subject.new_id("cd_fb"), pid, ref, row["surface"], listing_id,
            seller_id, row["promotion_class"], row.get("reason_code") or "", verb,
            row.get("session_id") or "", now, dedup_key, now,
        ],
        dedup_key,
    )


def explain(cur, placement_id: Any, token: Any) -> dict:
    """"Why am I seeing this?", as structured data the client renders.

    Returns the reason code and the *names* of the top positive contributors —
    never the numbers. A user asking why a product appeared is owed a truthful
    answer in their own terms; they are not owed the weight vector, and
    publishing it would let anyone reverse-engineer the ranker by creating
    listings and reading their own scores back.
    """
    row = load_placement(cur, placement_id, token)
    # Two separate ways this column disappoints, and only one of them raises.
    # `"{not json"` raises and is caught; `"[]"` and `"null"` parse *successfully*
    # into a list and a None, so a try/except around the parse alone leaves the
    # next attribute access to fail — an AttributeError on a curiosity tap, on a
    # row nobody is looking at. Both layers are checked for shape, not just for
    # truthiness, because `contributions` is iterated with `.items()`.
    try:
        breakdown = json.loads(row.get("score_breakdown_json") or "{}")
    except Exception:
        breakdown = {}
    if not isinstance(breakdown, dict):
        breakdown = {}
    contributions = breakdown.get("contributions")
    if not isinstance(contributions, dict):
        contributions = {}
    # Two filters, doing two different jobs. The allowlist decides what may be
    # *said* — a term with no translation and no business being shown to a
    # shopper is excluded whatever its value. The sign and sort then pick which
    # of the sayable terms actually lifted this product. Dropping the allowlist
    # and keeping `v > 0` would make a sign typo in the operator weight override
    # enough to publish `seller_risk` to a shopper.
    top = sorted(
        (
            (k, v)
            for k, v in contributions.items()
            if k in ranking.EXPLAINABLE_FACTORS and isinstance(v, (int, float)) and v > 0
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )[:3]
    return {
        "ok": True,
        "reason": row.get("reason_code") or "",
        "promotion_class": row.get("promotion_class"),
        "label_key": promotion.label_key(row.get("promotion_class")),
        "factors": [name for name, _ in top],
        "ranking_version": row.get("ranking_version"),
    }
