"""The composed entry point: one variant, one destination, one delivery promise.

What this module is for
-----------------------
Six pieces already exist and are each tested on their own: ``origin`` finds which
warehouse holds the stock, ``variant_facts`` turns a listing reference into the
physical description CJ's freight endpoint demands, ``providers.cj_logistics``
asks CJ, ``routing`` picks a route, ``estimate`` turns a transit range into dates,
and ``quote`` decides what may be cached and what a buyer may see.

Nothing assembles them, and the assembly is not mechanical. Three of the joins
carry a decision that is wrong by default:

* **The warehouse belongs in the cache key, and it is not known until a supplier
  has been asked.** ``quote.quote_delivery`` accepts ``origin`` and puts it in
  ``cache.route_key``; the origin is resolved *inside* ``variant_facts``, one
  layer below. Left unbridged, every quote is keyed on ``origin=None`` and a
  product whose stock moves from CN to US keeps serving the CN quote until the
  route tier expires — with the correct answer sitting in the origin tier the
  whole time. So this module resolves the facts *first*, once, and passes the
  origin it found both into the key and into the provider.
* **The shipping mode belongs in the cache key too, and callers put it somewhere
  else.** ``CJLogisticsProvider`` reads ``destination["shipping_mode"]``;
  ``route_key`` takes a separate ``shipping_mode`` argument. A caller who sets
  only the first gets two different services sharing one cache entry, which is a
  wrong promise produced entirely by the joining. Bridged here, by default.
* **A supplier adapter is expensive to build and usually not needed.** See
  :class:`_Deferred`.

One resolution, used twice
--------------------------
``variant_facts.describe`` is called here rather than left to the provider, and
the result is handed to the provider as a memo. Not as an optimization — though it
does save a second database read and a second origin lookup. If the facts were
resolved once for the key and again inside the provider, the two could disagree:
the origin tier can expire between them, and then the key names the warehouse we
had a moment ago while the freight quote priced the one we have now. The entry
would be filed under a warehouse it was not quoted from. Resolving once makes
that impossible rather than unlikely.

The memo is keyed on the reference it was resolved for. Returning it for any
reference asked would quote one variant's parcel for another — the exact failure
``variant_facts`` returns ``None`` rather than a default to avoid — so a different
reference is looked up properly.

Why the buyer's identity never appears
--------------------------------------
Nothing here takes an actor. A delivery estimate on a public product page is the
platform answering a question about its own listing with platform-held
credentials, not a buyer reaching through a merchant's supplier connection. That
is why ``connections.adapter_for`` — which authorizes an actor in a merchant
scope — is the wrong door, and why the adapter arrives as an injected source
instead: whatever already holds the connection supplies it, and this module never
learns whose it is. The same reasoning is set out at length in
``origin.py``'s docstring, and it is why the origin tier reimplements the
eligibility rule rather than calling ``fulfillment._stocked_origin``.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Sequence

from services import db

from . import breaker, estimate, origin, quote, routing, variant_facts
from .providers import cj_logistics
from .providers.cj_logistics import CJLogisticsProvider

#: The supplier this composition binds. One module per supplier; ``quote`` stays
#: supplier-agnostic and has a test saying so.
SUPPLIER_NAME = origin.SUPPLIER_NAME

#: The connection could not be opened, so no supplier was asked anything. Reported
#: as a reason of its own because the alternative is reporting it as
#: ``variant_origin_unknown``, which sends the repair to the catalogue for a
#: problem that is in the credential vault.
REASON_CONNECTION_UNAVAILABLE = "supplier_connection_unavailable"

#: No warehouse holds this variant, so there is nowhere to ship it from. Borrowed
#: from the provider rather than restated: it is the same fact reported from one
#: layer up, and two spellings of it would divide the same condition between two
#: names in whatever eventually counts them.
REASON_ORIGIN_UNKNOWN = cj_logistics.REASON_NO_ORIGIN


class _Deferred:
    """The supplier adapter, built on first use and at most once.

    Hydrating a CJ connection is not free. ``connections._hydrate`` reads the
    credential vault, refreshes a token that is close to expiring, spends a
    verification call against CJ, and writes the connection's status and quota
    back to the database. Doing that per product page would put a supplier call
    and a merchant-scoped write in front of a read that, warm, contains no
    supplier call at all — and would let a buyer's page load mark a merchant's
    connection degraded.

    So it happens when a supplier is genuinely about to be asked something. Both
    questions — inventory for the origin, freight for the route — share the one
    adapter, because both misses happen together: the origin and route tiers are
    the same length by construction (see :data:`cache.ORIGIN_FRESH_SECONDS`), so
    the warm case builds nothing and the cold case builds once.

    A source that cannot produce an adapter raises
    :class:`breaker.NotProviderEvidence`: the provider was never reached, so
    counting it would open CJ's circuit over our own credential state and stop
    quoting every other product. The message is fixed rather than derived from the
    underlying exception — that exception comes from a module that handles secrets,
    and the message is what a log line or a traceback prints. (``quote`` relays the
    *reason* and not the detail, so a leaky message would not reach a buyer today;
    that is a property of the layer above, not a reason to build the leak here.) The
    original is chained rather than discarded, so a developer still sees the cause.
    """

    def __init__(self, source: Callable[[], Any]):
        if not callable(source):
            raise ValueError("a supplier adapter source must be callable")
        self._source = source
        self._built: Any = None
        self.unavailable = False

    def _adapter(self) -> Any:
        if self._built is None:
            try:
                built = self._source()
            except Exception as exc:  # noqa: BLE001 — see the class docstring
                self.unavailable = True
                raise breaker.NotProviderEvidence(
                    REASON_CONNECTION_UNAVAILABLE,
                    "the supplier connection could not be opened",
                ) from exc
            if built is None:
                self.unavailable = True
                raise breaker.NotProviderEvidence(
                    REASON_CONNECTION_UNAVAILABLE,
                    "no supplier connection is available for this listing",
                )
            self._built = built
        return self._built

    def get_inventory(self, *args: Any, **kwargs: Any) -> Any:
        return self._adapter().get_inventory(*args, **kwargs)

    def estimate_shipping(self, *args: Any, **kwargs: Any) -> Any:
        return self._adapter().estimate_shipping(*args, **kwargs)


def delivery_for_variant(
    *,
    variant_ref: Optional[str],
    fulfillment: Optional[str],
    destination: Optional[Dict[str, Any]],
    now_at: Callable[[Any], Any],
    handling,
    buffer_days,
    adapter_source: Callable[[], Any],
    quantity: int = 1,
    store: Optional[Any] = None,
    connect: Callable[[], Any] = db.connect,
    clock: Callable[[], float] = time.time,
    policy: str = routing.POLICY_CHEAPEST_ACCEPTABLE,
    ceiling_days: Optional[int] = None,
    shipping_mode: Optional[str] = None,
    dispatch_cutoff_hour: Optional[int] = None,
    unspecified_basis: str = estimate.BASIS_BUSINESS,
    holidays: Sequence[Any] = (),
    allow_stale: bool = True,
    # Passed straight through. See `quote.quote_delivery`: it is the flag a
    # server-rendered HTML page sets so that a cold corridor costs it nothing.
    cache_only: bool = False,
) -> Dict[str, Any]:
    """The delivery answer for one CJ-fulfilled variant, in ``quote``'s two halves.

    Returns exactly what :func:`quote.quote_delivery` returns, including for every
    refusal — the refusal shapes are not rebuilt here, because a second place that
    composes a delivery answer is a second place that can compose a different one.

    Raises :class:`variant_facts.VariantRefInvalid` for a reference that does not
    name a listing. That is a caller's mistake, not a product that cannot say, and
    swallowing it would present a wrong reference as a listing with no weight on
    file.

    ``now_at`` is a function of the origin country and not a moment, and that is
    the one part of this signature worth explaining. ``arrival_window`` requires
    ``now`` to be timezone-aware *in the origin's zone* — "because it is the
    warehouse that has to pick the item" — but the origin is not known until
    :func:`variant_facts.describe` has run below, which is several database reads
    and possibly a supplier call after this function is entered. A caller therefore
    cannot supply the right moment; it does not yet know where the parcel ships
    from. Accepting a datetime anyway is not a neutral simplification: every
    deployment would quote in the caller's zone, which on Railway is UTC, and UTC is
    behind every warehouse zone this supplier uses. 18:00 UTC is already 02:00
    tomorrow in Shenzhen, so the whole platform would start handling a day early and
    close its window a day early. ``policy.now_at`` is the intended argument. A test
    that wants a fixed instant passes ``lambda origin: fixed``.
    """
    deferred = _Deferred(adapter_source)

    if not _worth_asking(fulfillment, destination):
        # Handed straight back to the canonical refusal path. Nothing below this
        # line is free: the facts read touches the database and the origin lookup
        # can spend a supplier call, and a seller-shipped item or a visitor with no
        # known country must cost neither. The gate is an economy, not a second
        # contract — ``quote_delivery`` re-decides it either way, so the worst this
        # can do is skip work that would have been refused anyway.
        # No origin was resolved and none will be, so the clock is asked about
        # nothing. `policy.now_at` answers that with the most advanced declared
        # zone, which is the conservative reading; what matters here is that the
        # refusal is still composed by `quote_delivery` and still carries a real
        # moment, because a refusal record with no `now` is a second refusal shape.
        return quote.quote_delivery(
            fulfillment=fulfillment, destination=destination, now=now_at(None),
            handling=handling, buffer_days=buffer_days, provider=None,
            variant_ref=variant_ref, quantity=quantity, store=None, clock=clock,
        )

    # `cache_only` reaches the origin lookup as well as the freight quote, and it
    # has to. Freight is the second supplier call on a cold product; this is the
    # first. Passing the flag only to `quote_delivery` produced a page that
    # refused to spend a freight call and then blocked on
    # `product/stock/getInventoryByPid` anyway — the same worst-case TTFB, one
    # endpoint further up, and a flag whose docstring was false.
    witness = _Witness(origin.resolver(adapter=deferred, store=store, clock=clock,
                                       cache_only=cache_only),
                       deferred)
    facts = variant_facts.describe(variant_ref, origin_lookup=witness, connect=connect)
    origin_code = (facts or {}).get("origin")

    return quote.quote_delivery(
        fulfillment=fulfillment,
        destination=destination,
        # Asked here and not earlier, because here is the first line at which the
        # warehouse is known. The same value goes into the cache key below; `now`
        # itself is deliberately not part of that key, which is what makes computing
        # it per-origin safe rather than a cache fragmentation.
        now=now_at(origin_code),
        handling=handling,
        buffer_days=buffer_days,
        provider=CJLogisticsProvider(
            adapter=deferred,
            facts=_memo(variant_ref, facts, witness=witness, connect=connect),
        ),
        variant_ref=variant_ref,
        quantity=quantity,
        store=store,
        # The two bridged joins, and the reason this module exists. Both values
        # were already decided above; passing them is what puts them in the key.
        origin=origin_code,
        shipping_mode=(shipping_mode if shipping_mode is not None
                       else (destination or {}).get("shipping_mode")),
        policy=policy,
        ceiling_days=ceiling_days,
        dispatch_cutoff_hour=dispatch_cutoff_hour,
        unspecified_basis=unspecified_basis,
        holidays=holidays,
        clock=clock,
        allow_stale=allow_stale,
        cache_only=cache_only,
    )


def _worth_asking(fulfillment: Any, destination: Any) -> bool:
    """Whether resolving this variant's facts could lead to a quote at all.

    Deliberately the *weaker* of the two tests — it asks only whether a supplier
    could conceivably be worth calling, and leaves every judgement about what is
    quotable to ``quote_delivery``. Both conditions are read off ``quote``'s own
    constant and ``route_key``'s own requirement rather than restated, so a new
    fulfillment type appearing in ``quote.FULFILLMENT_TYPES`` is refused here
    without this function being edited.
    """
    if not isinstance(fulfillment, str) or fulfillment.strip().upper() != quote.FULFILLMENT_SUPPLIER:
        return False
    country = destination.get("country") if isinstance(destination, dict) else None
    return isinstance(country, str) and bool(country.strip())


class _Witness:
    """The origin lookup, plus the one thing the caller above cannot otherwise see.

    ``variant_facts.describe`` answers ``None`` for every incomplete variant and
    says nothing about which fact was missing — deliberately, because naming the
    first absent key sends the repair to whichever column happened to be checked
    first. The provider then refuses the whole thing as ``variant_unknown``, which
    is true and useless: a listing with no weight on file, a product stocked in no
    warehouse, and a credential vault that will not open are three different
    repairs arriving under one name.

    Exactly one of the five facts is remote, so exactly one axis is worth
    recording here, and it is the one that separates *fix our row* from *check the
    supplier*: whether the origin lookup ran, and whether it produced anything.
    Nothing else is inferred, and no absent local column is named — that would
    reintroduce the misdirection ``describe`` avoids.
    """

    def __init__(self, lookup: Callable[[str, str], Optional[str]], deferred: _Deferred):
        self._lookup = lookup
        self._deferred = deferred
        self._asked = False
        self._answered = False

    def __call__(self, pid: str, vid: str) -> Optional[str]:
        self._asked = True
        found = self._lookup(pid, vid)
        # Blank is not an answer. ``variant_facts`` strips the origin before
        # accepting it, so a whitespace country produces absent facts while looking
        # from here like a warehouse that was named — and the repair would go
        # hunting for a missing column.
        self._answered = bool(found.strip() if isinstance(found, str) else found)
        return found

    @property
    def reason(self) -> Optional[str]:
        """Why the remote fact is absent, or ``None`` if it is not the reason."""
        if self._deferred.unavailable:
            # Checked first: a connection that would not open also makes the origin
            # lookup answer nothing, so the stock reading is a symptom here and
            # reporting it would send the repair to the warehouse.
            return REASON_CONNECTION_UNAVAILABLE
        if self._asked and not self._answered:
            return REASON_ORIGIN_UNKNOWN
        return None


def _memo(variant_ref: Any, facts: Optional[dict], *, witness: _Witness,
          connect: Callable[[], Any]) -> Callable[[str], Optional[dict]]:
    """The already-resolved facts, for the reference they were resolved for.

    A different reference is looked up properly rather than answered from the memo.
    Absent facts are refused by name when :class:`_Witness` can say why, and left
    to the provider's ``variant_unknown`` when it cannot — an invented reason is
    worse than a vague one, because it is actionable and wrong.
    """
    def facts_for(ref: str) -> Optional[dict]:
        resolved = facts if ref == variant_ref else variant_facts.describe(
            ref, origin_lookup=witness, connect=connect)
        if resolved is None:
            reason = witness.reason
            if reason is not None:
                raise breaker.NotProviderEvidence(
                    reason, "the supplier's own answer about this variant is missing")
        return resolved
    return facts_for
