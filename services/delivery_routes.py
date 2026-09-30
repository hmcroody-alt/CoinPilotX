"""The only way a client learns when a product will arrive.

Why the estimate is computed here and not in the client
------------------------------------------------------
§88 says the server is authoritative, and the reason is not a preference about
layering. Every input to a delivery promise is either a platform decision (the
handling allowance, the buffer, the route policy, the ceiling) or a supplier fact
(transit, freight, the warehouse the stock sits in). A client that assembled
those itself would need the declared policy, the CJ credential, and the cache —
and two clients doing it would produce two promises for one parcel, which is the
exact failure §91-93 names. So this pack computes and the client renders.

What the caller is allowed to decide
------------------------------------
Almost nothing, and the list is short on purpose:

* ``variant_ref`` — which product. A public listing id, already enumerable.
* ``quantity`` — how many. Bounded; see :data:`MAX_QUANTITY`.
* ``country`` — where to. An assertion, ranked as ``destination.TIER_STATED``.

Everything else is read from the server's own state. In particular
**``fulfillment`` is never accepted from a request.** It is the declared fact
§47-48 insists on, it lives in ``marketplace_product_sources``, and
:func:`listing.declaration` is what reads it. A request parameter here would let
anyone turn a seller-posted item into a supplier-fulfilled one and receive a
warehouse-to-door window for a parcel the seller is about to hand to the post
office — and it would cost a CJ call per request to do it. The same goes for the
buyer's identity, which comes from the session; a ``buyer_user_id`` parameter
would let one visitor resolve a destination from another's order history.

Why no postal code is accepted
------------------------------
``destination.POSTAL_TIERS`` permits a postal code from the checkout tier only,
so one supplied here would be dropped by the resolver whatever this route did
with it. An API field that is always discarded is worse than an absent one: it
reads as precision the answer does not have. Country is also the whole of what a
pre-purchase estimate needs — the international leg dominates every window this
domain produces — and it keeps a fragment of an address out of the request body
of a page the buyer is merely browsing. Postal precision arrives at checkout,
where the buyer is typing it anyway and where the checkout tier carries it.

Why it is a POST
----------------
A ``GET`` would put the destination in a query string, and a query string is
logged by the edge, kept in browser history and forwarded in referrers. The
country alone is not sensitive enough to agonise over, but the shape is: the
checkout surface that comes later has a postal code to pass, and the endpoint it
passes it to should not be one whose habit is to take inputs in the URL. Nothing
here is cacheable by an intermediary either — see :func:`_json`.

Why there is no batch form
--------------------------
§18 is explicit that forty product cards must not become forty supplier calls,
and a batch endpoint is the tempting way to answer it: one request, forty
variants. It is the wrong shape. It moves the fan-out server-side without
removing it, and it makes the slowest supplier response the latency of the whole
list. The list surfaces get a corridor-level answer — one estimate per
(country, warehouse, mode) corridor, shared across every card in it — which is a
different question with a different cache key. This route answers the
variant-level question, which is a product page's question.

Shadow mode without a flag
--------------------------
§101-103 asks for the estimate to be computed before it is shown. That is
already true of this pack the moment it deploys, and not because a flag is off:
``policy.handling()`` and ``policy.buffer_days()`` return ``None`` until an
operator sets a variable, and ``None`` composes to a refusal naming the
undeclared input. A deployment with no delivery variables set therefore serves
this endpoint, exercises the whole path including the supplier call, and returns
``handling_time_undeclared`` — never a date. There is no value of any flag that
would make it invent one, which is a stronger property than a flag, because a
flag can be switched on by someone who has not set the policy.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from flask import Blueprint, jsonify, request, session

from services.marketplace_cart_routes import MAX_QTY_PER_LINE
from services.business_os.suppliers import connections
from services.delivery import copy as delivery_copy
from services.delivery import destination as destinations
from services.delivery import entry, listing, policy, store as store_module
from services.delivery import variant_facts
from services.route_auth import admin_required, public_route

LOGGER = logging.getLogger(__name__)

delivery_blueprint = Blueprint("pulse_delivery", __name__)

API_PREFIX = "/api/pulse/delivery"

#: Borrowed from the cart rather than chosen here. The cart is the only thing on
#: this platform that can turn a quantity into an order, so a quote above its own
#: per-line ceiling is a quote for a parcel nobody can buy — and it is its own
#: cache key, which makes an unbounded quantity a way to fragment the cache and
#: spend the supplier's rate limit at the same time.
MAX_QUANTITY = MAX_QTY_PER_LINE

#: A listing the caller may not see. Deliberately *not* one of
#: ``quote``'s reasons: every reason in that module is a statement about
#: delivery, and this is a statement about the listing. Folding it in would mean
#: a surface rendering "we cannot estimate delivery" for a product that does not
#: exist, and an accuracy measurement later counting it as an estimator failure.
REASON_LISTING_UNAVAILABLE = "listing_unavailable"

#: A malformed ``variant_ref``. The caller's mistake, and answered as one — see
#: :func:`variant_facts.parse_ref`, which raises rather than returning no facts
#: precisely so a coding error stays distinguishable from a product that cannot
#: say.
REASON_REF_INVALID = "variant_ref_invalid"

#: No ``variant_ref`` at all, or a body that is not an object.
REASON_BODY_INVALID = "request_invalid"


def _json(payload: Dict[str, Any], status: int = 200):
    response = jsonify(payload)
    # `no-store`, and not a short `max-age`. Two reasons, and the second is the
    # one that matters. The obvious one is that the answer depends on the
    # session's destination resolution, so a shared cache would hand one
    # visitor's country to another. The subtle one is that this domain already
    # has a cache, with a deliberate freshness policy, stale-while-revalidate and
    # a negative tier -- and an intermediary caching the composed response would
    # silently add a second, dumber layer on top of it whose TTL nobody chose.
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _bot():
    import bot

    return bot


def _buyer_user_id() -> Optional[int]:
    """The signed-in buyer, or ``None``.

    Read from the session and from nowhere else. The value's only use is
    ``destination._from_history``, which reads this buyer's past shipping
    addresses; accepting it as a parameter would make one visitor's order history
    readable-by-effect to any other, one country at a time.

    Absent is normal, not an error. A product page is public and most of its
    traffic has no session, which is the whole reason ``destination`` has an edge
    tier and a ``TIER_NONE`` answer.
    """
    try:
        user = _bot().api_account_user()
    except Exception:  # noqa: BLE001 — an unreadable session is an absent buyer
        return None
    if isinstance(user, dict):
        found = user.get("id") or user.get("user_id")
    else:
        found = getattr(user, "id", None)
    try:
        return int(found) if found is not None else None
    except (TypeError, ValueError):
        return None


def _body() -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _quantity(raw: Any) -> Optional[int]:
    """The requested count, or ``None`` if it is not one this route will quote.

    Refused rather than clamped. ``max(1, min(n, cap))`` is the cart's idiom and
    is right there, because the cart is *setting* a line quantity and the nearest
    legal value is what the merchant wants. Here the number is an input to a
    freight quote, and silently quoting 20 when 500 was asked returns a window
    for a parcel the caller did not describe, labelled as though it were theirs.
    """
    if raw is None:
        return 1
    if isinstance(raw, bool) or not isinstance(raw, int):
        # `bool` first: `True` is an `int` in Python and would quote a quantity of
        # one from a caller who sent a flag, which is a body this route should be
        # telling them about rather than interpreting.
        return None
    if raw < 1 or raw > MAX_QUANTITY:
        return None
    return raw


def _stated(raw: Any) -> Optional[Dict[str, Any]]:
    """The caller's asserted country, in the shape ``destination.resolve`` takes.

    No validation of the code itself happens here. ``destination._code`` already
    discards anything that is not two letters, and a second opinion about country
    codes in this module would be a second place to disagree with the one that
    ends up in the cache key.
    """
    if isinstance(raw, dict):
        # A `{"country": ...}` object is accepted as well as a bare string,
        # because that is the shape `destination.resolve` documents and a client
        # holding a destination record will send it back rather than unpick it.
        return {"country": raw.get("country")}
    if isinstance(raw, str):
        return {"country": raw}
    return None


def _adapter_source(supplier: Optional[Dict[str, Any]]) -> Callable[[], Any]:
    """A callable that yields the supplier adapter, or raises.

    Raising is the contract ``entry._Deferred`` is built around, and a listing
    with an incomplete connection mapping is exactly the case
    :func:`listing._supplier` returns ``None`` for while still reporting
    ``SUPPLIER``. The two together are not a contradiction: the merchant really
    did declare that CJ ships this, and we really cannot reach CJ for it. Made to
    raise here so the answer comes back as ``connection_unavailable`` — which
    sends an operator to the credential mapping — rather than as a missing weight,
    which would send them to the catalogue.

    ``worker_adapter`` and not ``adapter_for``: there is no actor to authorize.
    The buyer is not acting on the merchant's connection, the platform is reading
    a shipping rate on their behalf, and routing that through a user-authorization
    path would mean either inventing an actor id or widening one.
    """

    def source() -> Any:
        if not supplier:
            raise RuntimeError("this listing has no complete supplier connection")
        return connections.worker_adapter(
            supplier["connection_id"], supplier["business_id"], supplier["store_id"])

    return source


@delivery_blueprint.route(f"{API_PREFIX}/estimate", methods=["POST"])
@public_route(
    reason="Delivery estimates are part of a public product page. The listing "
           "must already be publicly visible for an estimate to be produced at "
           "all (listing.declaration applies the platform's own public_sql "
           "predicate), and the answer contains no seller economics: the buyer "
           "half of a quote carries a date range, a confidence and FREE "
           "shipping, never the freight PulseSoc pays. Requiring a login would "
           "mean no shopper sees a delivery date before registering, which is "
           "the question they are asking before they decide to register."
)
def delivery_estimate():
    """When one variant, in one quantity, would arrive at one destination."""
    body = _body()
    variant_ref = body.get("variant_ref")
    if not isinstance(variant_ref, str) or not variant_ref.strip():
        return _json({"ok": False, "reason": REASON_BODY_INVALID,
                      "message": "variant_ref is required."}, 400)

    quantity = _quantity(body.get("quantity"))
    if quantity is None:
        return _json({"ok": False, "reason": REASON_BODY_INVALID,
                      "message": f"quantity must be a whole number from 1 to "
                                 f"{MAX_QUANTITY}."}, 400)

    try:
        declared = listing.declaration(variant_ref)
    except variant_facts.VariantRefInvalid as exc:
        return _json({"ok": False, "reason": REASON_REF_INVALID,
                      "message": str(exc)}, 400)

    if not declared["visible"]:
        # 404 and not 403, and the same answer for a listing that does not exist
        # as for a draft. `listing.declaration` conflates those two on purpose and
        # this route must not un-conflate them: a different status for "exists but
        # you cannot see it" is an oracle for enumerating other sellers' drafts.
        return _json({"ok": False, "reason": REASON_LISTING_UNAVAILABLE,
                      "message": "This listing is not available."}, 404)

    where = destinations.resolve(
        stated=_stated(body.get("destination") or body.get("country")),
        # No `checkout=`. This route serves a product page, where by definition
        # no checkout address exists yet; passing the caller's assertion as the
        # checkout tier would promote it into `POSTAL_TIERS` and let a request
        # body claim postal precision the buyer never confirmed for this purchase.
        buyer_user_id=_buyer_user_id(),
        headers=request.headers,
    )

    result = entry.delivery_for_variant(
        variant_ref=variant_ref,
        # Server-side, from the listing's own source row. See the module
        # docstring: this is the one input a request must never influence.
        fulfillment=declared["fulfillment"],
        destination=where["destination"],
        quantity=quantity,
        adapter_source=_adapter_source(declared["supplier"]),
        # `policy.now_at` itself, not a moment. The origin is not known until the
        # facts read inside `entry` has happened, and this process runs in UTC,
        # which is behind every warehouse zone CJ uses -- a caller-supplied
        # instant would start handling a day early. See
        # `entry.delivery_for_variant`'s docstring.
        now_at=policy.now_at,
        handling=policy.handling(),
        buffer_days=policy.buffer_days(),
        dispatch_cutoff_hour=policy.dispatch_cutoff_hour(),
        holidays=policy.closures(),
        ceiling_days=policy.ceiling_days(),
        policy=policy.route_policy(),
        unspecified_basis=policy.unspecified_basis(),
        store=store_module.CacheEngineStore(),
    )

    return _json({
        "ok": True,
        # The buyer half, whole and unedited. Re-listing its keys here would be a
        # second copy of the buyer/internal split that drifts the day `quote`
        # adds a field; handing over `result` itself would ship `freight_total`
        # to a shopper, which §33-35 forbids in as many words.
        "delivery": result["buyer"],
        # The composed sentence, **for the web surfaces only**, and the
        # restriction is the whole reason this key needs a comment.
        #
        # `services/delivery/web.py` renders a product page from the delivery
        # cache alone, so a cold corridor reaches the browser as "Checking
        # delivery options…" and is filled in by a fetch to this endpoint. The
        # script that does the filling in deliberately holds no delivery copy:
        # the vocabulary exists in exactly two implementations -- `delivery/copy.py`
        # and the app's `deliveryCopy.ts` -- pinned to each other by
        # `tests/delivery/test_delivery_copy.py`, and a third copy inside a
        # JavaScript file would sit outside that contract. So the server composes
        # it, from the same table the Jinja page used, and the sentence the
        # visitor ends up reading is the same one whichever path produced it.
        #
        # **The app must not read this key**, and does not: `parseDeliveryResponse`
        # takes `delivery` and composes through its own bundled copy. That is not
        # an oversight to tidy up later. A shipped binary rendering whatever
        # sentence a future deployment sends it would display copy no reviewer saw,
        # and it would have nothing to say at all when the request fails -- which
        # is the one state the copy exists for. Additive here, ignored there.
        "line": delivery_copy.delivery_copy(delivery=result["buyer"], destination=where),
        "destination": {
            # Echoed back because the presentation layer needs to say "to
            # Germany -- change" rather than guess, and because the tier is what
            # separates "we know where you are" from "we assumed". The postal
            # code is not echoed: a stated destination never carries one, and
            # returning an address fragment the client already has buys nothing.
            "country": where["destination"]["country"],
            "precision": where["precision"],
            "tier": where["tier"],
            "known": where["known"],
        },
    })


@delivery_blueprint.route("/health/delivery", methods=["GET"])
@admin_required
def delivery_health():
    """Whether this deployment can produce a date, and what is missing if not.

    Admin-gated, unlike the other ``/health`` surfaces in this repository, and
    the reason is what it reports rather than caution. ``policy.declared``
    returns the deployment's own configuration and, in ``errors``, the raw text
    of a variable someone typed wrong. None of that is a credential, but it is
    also nobody's business but the operator's, and an endpoint that echoes
    environment values to the internet is a habit rather than a decision.

    Reports and does not judge: ``can_estimate`` false with an empty ``errors``
    list is a deployment that has simply not been configured yet, which is the
    correct state for one that has not started its rollout.
    """
    _admin, denied = _bot().require_admin_api("system.view")
    if denied:
        return denied
    report = dict(policy.declared())
    # Reported beside the policy because the two produce the same buyer-visible
    # silence and call for different work: a missing handling allowance is a
    # variable to set, an unconfigured edge tier is a population of visitors
    # whose country nothing resolved. `destination.edge_configured` exists for
    # exactly this distinction.
    report["edge_tier_configured"] = destinations.edge_configured()
    return _json({"ok": True, "delivery": report})


def register(app) -> None:
    app.register_blueprint(delivery_blueprint)
