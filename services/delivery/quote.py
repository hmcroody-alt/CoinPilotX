"""The one place a delivery promise is produced. Every surface reads this.

Why composition is its own module
---------------------------------
The pieces are each independently correct and independently tested: the provider
boundary normalizes a supplier's aging string, ``routing`` picks a route
deterministically, ``estimate`` turns a transit range into a window, ``cache``
decides what may be reused, ``breaker`` decides whether to call at all. None of
them can produce a wrong promise on its own.

The wrong promise is produced by joining them badly, and the three ways to do
that are all invisible:

* **Per-surface composition.** A product card, a product page and a checkout each
  calling the parts in their own order eventually disagree, and no single
  surface's tests fail — each one is self-consistently wrong. That is the failure
  this module exists to make impossible, so it is the only public entry point and
  the surfaces get no parts to assemble.
* **Caching the rendered window instead of the evidence.** A window is a pair of
  dates, so a cached window is *yesterday's* dates when read today. Six hours
  later it is subtly early; a day later it promises the past. The store therefore
  holds the route and its transit range — the provider's evidence, which does not
  age that way — and the dates are recomputed against ``now`` on every single
  read, cache hit included.
* **Letting a supplier's economics reach the buyer.** Freight cost is needed
  internally for margin and is forbidden externally. One result object carrying
  both is one accidental serialization away from publishing wholesale costs, so
  the result is split and the buyer half is asserted to have no cost field.

Shipping is free to the buyer; it is not free to the platform
-------------------------------------------------------------
Those are different statements and both are true. The buyer half says ``FREE``
unconditionally. The internal half carries the real freight the platform will pay
and the identifiers fulfillment must book, because a route quoted and a route
shipped being different is how an estimate becomes a lie after the fact.

What this module refuses to do
------------------------------
It will not estimate for a product that is not supplier-fulfilled, and it decides
that *before* anything else, so a seller-shipped item never costs a supplier
call. It will not invent a handling time or a buffer — both are required
arguments with no defaults, and their absence is a reason, not a zero. It will
not treat "we could not reach the supplier" as "the supplier does not ship
there", because the first is ours to fix and the second blocks a checkout.

It knows no supplier. There is no CJ in this file, by test.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Protocol, Sequence

from . import breaker, cache, estimate, routing

# §47-48: what fulfils this product is a declared fact, never inferred. An
# unknown fulfillment produces no estimate rather than a supplier's estimate.
FULFILLMENT_SUPPLIER = "SUPPLIER"
FULFILLMENT_SELLER = "SELLER"
FULFILLMENT_DIGITAL = "DIGITAL"
FULFILLMENT_TYPES = (FULFILLMENT_SUPPLIER, FULFILLMENT_SELLER, FULFILLMENT_DIGITAL)

REASON_FULFILLMENT_UNDECLARED = "fulfillment_undeclared"
REASON_NOT_SUPPLIER_FULFILLED = "not_supplier_fulfilled"
REASON_NO_DESTINATION = "destination_unresolved"
REASON_PROVIDER_UNREACHABLE = "supplier_unreachable"
REASON_PROVIDER_FAILED = "supplier_error"
#: The request was never formed — a variant with no stated weight, SKU or shipping
#: properties. Kept apart from ``supplier_error`` because it is our data that is
#: incomplete, not the supplier that is down, and apart from an unserviceable route
#: because the goods may well ship there: nobody has said.
REASON_VARIANT_INCOMPLETE = "variant_data_incomplete"
#: Nothing in the cache and the caller said not to ask. See ``cache_only`` on
#: :func:`quote_delivery`.
#:
#: Its own reason rather than one of the supplier ones, and the distinction is the
#: point of it: every other refusal in this module is a statement that an answer
#: could not be had, and this one says an answer was not *sought*. A surface that
#: rendered "not available right now" for it would be reporting a healthy cache
#: miss as a delivery problem, and an accuracy or alerting query counting it
#: alongside ``supplier_unreachable`` would show an outage that is not happening.
REASON_NOT_CACHED = "estimate_not_cached"

# The single customer-facing shipping price. A string, not a zero, because a
# surface that receives 0 will eventually format it as "$0.00" next to a total.
SHIPPING_FREE = "FREE"

# The buyer half's exact key set. Pinned by a test so a cost, a supplier name or a
# warehouse cannot be added to it without that test going red — the leak this
# guards is a field appended in good faith by someone who did not know the half
# is serialized to an unauthenticated surface.
BUYER_FIELDS = frozenset({
    "state", "reason", "earliest", "latest", "confidence", "guaranteed",
    "shipping_price", "is_estimate",
})


class QuoteRejected(ValueError):
    """The caller asked for something that cannot be quoted as asked."""


class SupplierLogisticsProvider(Protocol):
    """What a supplier must offer to participate in delivery intelligence.

    Deliberately narrow. Everything provider-specific — endpoints, credentials,
    field names, the shape of an aging string — lives behind this and is
    normalized before it returns. A second supplier implements this; nothing in
    this package changes.
    """

    name: str

    def quote_routes(self, *, variant_ref: str, destination: Dict[str, Any],
                     quantity: int) -> Sequence[Dict[str, Any]]:
        """Return normalized route options, or raise if the supplier is unreachable.

        An empty sequence is a real answer: the supplier ships nothing to that
        destination. It is not the same as raising, and the two must not be
        conflated — see ``REASON_PROVIDER_UNREACHABLE``.
        """


class Store(Protocol):
    """The minimum a cache store must do. ``services/cache_engine.py`` satisfies it."""

    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...


def quote_delivery(
    *,
    fulfillment: Optional[str],
    destination: Optional[Dict[str, Any]],
    now,
    handling,
    buffer_days,
    provider: Optional[SupplierLogisticsProvider] = None,
    variant_ref: Optional[str] = None,
    quantity: int = 1,
    store: Optional[Store] = None,
    origin: Optional[str] = None,
    shipping_mode: Optional[str] = None,
    warehouses: Optional[Sequence[str]] = None,
    policy: str = routing.POLICY_CHEAPEST_ACCEPTABLE,
    ceiling_days: Optional[int] = None,
    dispatch_cutoff_hour: Optional[int] = None,
    unspecified_basis: str = estimate.BASIS_BUSINESS,
    holidays: Sequence[Any] = (),
    clock: Optional[Callable[[], float]] = None,
    allow_stale: bool = True,
    cache_only: bool = False,
) -> Dict[str, Any]:
    """Produce the canonical delivery answer for one variant to one destination.

    Returns ``{"buyer", "internal"}``. ``buyer`` is the only half a surface may
    render or serialize outward. ``internal`` carries freight cost, the route
    identifiers fulfillment must book, and how the answer was obtained.

    Never raises for a supplier problem. A caller that has to catch exceptions to
    render a page will eventually forget to, and the page it renders then is
    worse than one saying the estimate is unavailable.

    ``cache_only`` answers from the cache or refuses with
    :data:`REASON_NOT_CACHED`, and never spends a supplier call. It exists for
    one caller shape: a **server-rendered HTML page**. The app asks this question
    from a screen that already has a spinner and a retry, so a supplier call
    costing a second is a second the buyer spends looking at a loading line. A
    Jinja product page has nowhere to put that second except in front of the
    first byte, which turns the platform's slowest supplier response into the
    platform's Time To First Byte — on the page Google measures. So the page
    renders whatever the corridor's cache already holds, which is free, and lets
    the browser fetch the rest from the endpoint the app uses.

    It is deliberately *not* the same thing as ``allow_stale=False``. Stale-ness
    is about how old an answer may be; this is about whether asking is permitted
    at all. Both tiers of the cache count as answers here, because a stale
    provider quote is still the provider's own number and withholding it in
    favour of "checking…" would be showing the buyer less than is known.
    """
    tick = clock or time.time
    moment = tick()

    if fulfillment is None or not isinstance(fulfillment, str) or not fulfillment.strip():
        return _compose(estimate.unavailable(REASON_FULFILLMENT_UNDECLARED), source="REFUSED")
    if fulfillment.strip().upper() != FULFILLMENT_SUPPLIER:
        # Checked before the destination and before any key is built: a
        # seller-shipped or digital product must not cost a supplier call, and a
        # feed full of them must not warm the supplier's cache.
        return _compose(estimate.unavailable(REASON_NOT_SUPPLIER_FULFILLED), source="REFUSED")

    country = (destination or {}).get("country") if isinstance(destination, dict) else None
    if not isinstance(country, str) or not country.strip():
        return _compose(estimate.unavailable(REASON_NO_DESTINATION), source="REFUSED")
    if not isinstance(variant_ref, str) or not variant_ref.strip():
        raise QuoteRejected("a supplier-fulfilled quote needs the supplier's variant reference")
    if provider is None:
        raise QuoteRejected("a supplier-fulfilled quote needs a logistics provider")

    supplier = getattr(provider, "name", None)
    if not isinstance(supplier, str) or not supplier.strip():
        raise QuoteRejected("a logistics provider must name itself")

    key = cache.route_key(
        supplier=supplier, variant_ref=variant_ref, destination=country,
        postal=(destination or {}).get("postal"), quantity=quantity,
        origin=origin, shipping_mode=shipping_mode, warehouses=warehouses,
    )

    stored = cache.read(_cached(store, key), now=moment)
    evidence = stored["value"] if stored["state"] == cache.STATE_FRESH else None
    source = "CACHE_FRESH"

    if evidence is None and cache_only:
        # Checked before `_fetch` rather than by passing a refusing provider into
        # it, because `_fetch` is the breaker and the coalescer: a caller that got
        # there would record a failure against a supplier it never called, trip
        # the breaker for the callers who *are* allowed to call, and coalesce onto
        # an in-flight request whose result it then discards.
        if stored["state"] == cache.STATE_STALE:
            # A stale answer is still an answer, and this path does not have the
            # option `_fetch` has of trying to beat it with a fresh one.
            evidence, source = stored["value"], "CACHE_STALE"
        else:
            return _compose(estimate.unavailable(REASON_NOT_CACHED),
                            source="CACHE_MISS", route=None)

    if evidence is None:
        fetched = _fetch(
            key=key, supplier=supplier, provider=provider, variant_ref=variant_ref,
            destination=destination, country=country, quantity=quantity, policy=policy,
            ceiling_days=ceiling_days, store=store, clock=tick,
        )
        if fetched["evidence"] is not None:
            evidence, source = fetched["evidence"], fetched["source"]
        elif fetched["refusal"] is not None and fetched["negative"]:
            # A real negative answer from a reachable supplier. It outranks a
            # stale positive: the stale one says "ten days", this one says "we do
            # not ship there", and shipping nothing is the newer truth.
            return _compose(fetched["refusal"], source=fetched["source"], route=None)
        elif allow_stale and stored["state"] == cache.STATE_STALE:
            # The supplier is unreachable and there is a stored answer. This is
            # the moment the stale tier exists for.
            evidence, source = stored["value"], "CACHE_STALE"
        else:
            return _compose(fetched["refusal"], source=fetched["source"], route=None)

    if (evidence or {}).get("supported") is False:
        # A cached negative, still inside its short freshness. It has to come back
        # as UNSUPPORTED_ROUTE and not as "transit unknown": a checkout gate reads
        # that distinction to decide whether to block, and the two reasons send an
        # investigation to different places.
        return _compose(
            estimate.unavailable(estimate.REASON_UNSUPPORTED,
                                 detail=(evidence or {}).get("detail")),
            source=source, route=None)

    route = (evidence or {}).get("route")
    window = estimate.arrival_window(
        transit=(evidence or {}).get("transit"),
        handling=handling,
        buffer_days=buffer_days,
        now=now,
        dispatch_cutoff_hour=dispatch_cutoff_hour,
        unspecified_basis=unspecified_basis,
        holidays=holidays,
        confidence=(estimate.CONFIDENCE_PROVIDER_CACHED if source == "CACHE_STALE"
                    else estimate.CONFIDENCE_PROVIDER_QUOTED),
    )
    return _compose(window, source=source, route=route)


def _fetch(*, key, supplier, provider, variant_ref, destination, country, quantity,
           policy, ceiling_days, store, clock) -> Dict[str, Any]:
    """Call the supplier once, through the breaker, coalescing concurrent callers."""

    def produce():
        options = provider.quote_routes(
            variant_ref=variant_ref, destination=destination, quantity=quantity)
        return routing.select_route(options, policy=policy, ceiling_days=ceiling_days)

    try:
        selection = cache.call_once(
            key, lambda: breaker.guard(f"delivery:{supplier}", produce, clock=clock))
    except breaker.ProviderUnreachable:
        # Nothing was attempted. Emphatically not cached: caching it would make
        # our own outage look like the supplier's answer for as long as the TTL.
        return _fetch_failed(REASON_PROVIDER_UNREACHABLE, "PROVIDER_UNREACHABLE")
    except cache.CoalesceTimeout:
        return _fetch_failed(REASON_PROVIDER_UNREACHABLE, "COALESCE_TIMEOUT")
    except breaker.NotProviderEvidence as incomplete:
        # Not stored, and deliberately not as a negative. A negative says "we do
        # not ship there" and blocks a checkout; an undescribed variant may ship
        # there perfectly well. Not cached either, so repairing the catalogue row
        # takes effect on the next read instead of after a TTL.
        return _fetch_failed(REASON_VARIANT_INCOMPLETE, "VARIANT_INCOMPLETE",
                             detail=incomplete.reason)
    except Exception:
        return _fetch_failed(REASON_PROVIDER_FAILED, "PROVIDER_ERROR")

    selected = selection.get("selected")
    if selected is None:
        # The supplier answered and the answer is that there is no usable route.
        # Cached, briefly and without a stale window, because it blocks a
        # checkout and may yet turn out to have been a credential problem.
        refusal = estimate.unavailable(
            estimate.REASON_UNSUPPORTED,
            detail=selection.get("reason") or routing.NO_ELIGIBLE,
        )
        if store is not None:
            written = cache.entry_for(
                {"supported": False, "route": None, "transit": None,
                 "detail": refusal["detail"]},
                now=clock(),
                fresh_seconds=cache.UNSUPPORTED_FRESH_SECONDS,
                stale_seconds=cache.UNSUPPORTED_STALE_SECONDS,
            )
            _remember(store, key, written)
        return {"evidence": None, "refusal": refusal, "negative": True,
                "source": "PROVIDER_NO_ROUTE"}

    # What gets stored is the evidence, never the computed window. A window is a
    # pair of dates; stored and re-served an hour later it is an hour early, and
    # a day later it promises a date in the past.
    evidence = {
        "supported": True,
        "route": {
            "option_id": selected.get("option_id"),
            "channel_id": selected.get("channel_id"),
            "service": selected.get("service"),
            "provider_total": selected.get("provider_total"),
            "currency": selected.get("currency"),
            "provider_aging_text": selected.get("estimated_transit"),
        },
        "transit": selected.get("transit"),
    }
    if store is not None:
        written = cache.entry_for(
            evidence, now=clock(),
            fresh_seconds=cache.ROUTE_FRESH_SECONDS,
            stale_seconds=cache.ROUTE_STALE_SECONDS,
        )
        _remember(store, key, written)
    return {"evidence": evidence, "refusal": None, "negative": False, "source": "PROVIDER"}


def _cached(store: Optional[Store], key: str) -> Any:
    """Read the store, treating any fault as a miss.

    The cache is an accelerator. A store that is down must cost a provider call,
    not a product page, so nothing it raises is allowed past this line.
    """
    if store is None:
        return None
    try:
        return store.get(key)
    except Exception:  # noqa: BLE001 — an unreadable cache is a miss
        return None


def _remember(store: Store, key: str, written: Dict[str, Any]) -> None:
    """Store an entry, and carry on if it will not go.

    The answer above this call is already computed and correct. Letting a cache
    write throw it away would mean a broken cache serves *worse* than no cache
    at all, which inverts the reason it is here.
    """
    try:
        store.set(key, written["envelope"], written["ttl_seconds"])
    except Exception:  # noqa: BLE001 — an unwritable cache costs the next caller a call
        pass


def _fetch_failed(reason: str, source: str,
                  detail: Optional[str] = None) -> Dict[str, Any]:
    return {"evidence": None, "negative": False, "source": source,
            "refusal": estimate.unavailable(reason, detail=detail)}


def _compose(window: Dict[str, Any], *, source: str,
             route: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Split one estimate into what a buyer may see and what the platform keeps."""
    buyer = {
        "state": window["state"],
        "reason": window["reason"],
        "earliest": window["earliest"],
        "latest": window["latest"],
        "confidence": window["confidence"],
        # Never True. There is no carrier guarantee behind any of this, and §58
        # and §125 are the same instruction said twice for a reason.
        "guaranteed": False,
        "is_estimate": True,
        # Free to the buyer regardless of what the platform pays. The two facts
        # live in different halves of this result on purpose.
        "shipping_price": SHIPPING_FREE,
    }
    return {
        "buyer": buyer,
        "internal": {
            "source": source,
            "route": route,
            "detail": window["detail"],
            "components": window["components"],
            # The freight the platform actually pays, surfaced for the pricing
            # engine's landed-cost basis. Free shipping is a customer promise,
            # not an accounting fact.
            "freight_total": (route or {}).get("provider_total"),
            "freight_currency": (route or {}).get("currency"),
        },
    }
