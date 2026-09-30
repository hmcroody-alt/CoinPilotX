"""Which warehouse country a CJ product would ship from.

The one fact that is not already on this machine
------------------------------------------------
``variant_facts`` reads four of the five things CJ's freight endpoint demands out
of local rows and takes the fifth — the origin country — from an injected lookup,
because nothing local records a warehouse. This module is that lookup, and it is
the only place in the delivery domain that spends a supplier call to answer a
product page.

``fulfillment._stocked_origin`` answers the same question for an order and says in
its own first line that it is "Not a hardcoded 'CN'": quoting freight from a
country that holds none of the stock prices a shipment that will not happen. It
cannot be reused here, for the reason set out in ``providers/cj_logistics.py``: it
reads through ``gateway.read("inventory", ...)``, which demands an authenticated
actor in a merchant scope, and a visitor on a product page is not one. So the
adapter is called directly and the eligibility rule is reimplemented — not
because the rule is different, but because the caller is.

One call per product, not per variant
-------------------------------------
``CJAdapter.get_inventory`` maps onto ``product/stock/getInventoryByPid``, which
takes a pid and returns **every** variant's warehouses in a single reply, at a
cost of ten points. So the cached unit is the product: a variant-keyed tier would
turn one call into one per variant and store the same body under each key, which
is the fan-out §18 forbids, reintroduced one layer below the place it was fixed.
``vid`` is therefore a lookup into a cached mapping and never part of the key.

It is also why ``get_inventory`` is called with ``vid=None`` and filtered here.
Passing a vid makes the adapter raise ``VARIANT_PRODUCT_MISMATCH`` when CJ's reply
does not mention it — turning "we do not know where this variant ships from",
which is a product page that cannot say, into a supplier error that counts against
the circuit and blanks every other variant of the product with it.

Its own circuit, and that is not tidiness
-----------------------------------------
This runs *inside* ``quote.py``'s freight guard: the provider calls ``facts``,
which calls here. Sharing the freight circuit name would therefore nest
``breaker.guard`` inside itself on one key, and ``breaker.claim`` grants exactly
one half-open probe. Both directions corrupt:

* The outer call takes the probe, so the inner claim is denied and raises
  ``ProviderUnreachable`` **inside** the outer producer, where ``guard``'s
  ``except Exception`` counts it as fresh evidence against the provider and
  re-opens the circuit with a bumped trip count. No probe could ever succeed, so
  the circuit would never close again.
* Inverted, an inner success calls ``succeeded``, which returns ``initial()`` — a
  fully closed circuit — declaring the freight path healthy on the strength of an
  inventory read, before any freight call has been attempted.

Two endpoints with different costs and different failure modes get two circuits.
An inventory outage still stops freight quotes, but by withholding the origin they
need, which is the honest mechanism.

What a stale origin is allowed to do
------------------------------------
Served, and the tiers are sized so that it cannot be staler than the route quote
resting on it — see :data:`cache.ORIGIN_FRESH_SECONDS`. There is no background
revalidation here: ``read`` reports ``revalidate`` and this module ignores it,
because a refresh scheduler belongs to the tier that owns the whole domain rather
than to the first module that could use one. Recorded rather than half-built.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from . import breaker, cache

#: The supplier whose inventory shape this module reads.
SUPPLIER_NAME = "cj"

#: Separate from the freight circuit on purpose; see the module docstring.
CIRCUIT = f"delivery:{SUPPLIER_NAME}:inventory"

#: CJ's own word for "somebody counted stock here". ``_warehouse_stock`` reports
#: it for a positive total and ``UNKNOWN`` for a warehouse nobody counted, which
#: is not stock. Deliberately not also requiring ``verified``: that field says
#: whether CJ audited the count, not whether the units ship, and demanding it made
#: every real CJ warehouse ineligible — the incident is recorded in
#: ``cj._warehouse_stock``'s own docstring.
IN_STOCK = "IN_STOCK"


def resolver(*, adapter: Any, store: Any = None,
             clock: Callable[[], float] = time.time,
             cache_only: bool = False) -> Callable[[str, str], Optional[str]]:
    """An ``origin_lookup(pid, vid)`` of the shape ``variant_facts`` expects.

    Bound rather than called directly so ``variant_facts`` keeps taking a plain
    two-argument callable and stays unaware of the cache, the breaker and the
    supplier adapter alike.

    ``cache_only`` is bound here for the same reason: it is a property of the
    *caller* — a server-rendered page that must not put a supplier's latency in
    front of its first byte — and ``variant_facts`` has no business knowing that
    such a caller exists.
    """
    def origin_lookup(pid: str, vid: str) -> Optional[str]:
        return stocked_origin(pid, vid, adapter=adapter, store=store, clock=clock,
                              cache_only=cache_only)
    return origin_lookup


def stocked_origin(pid: Any, vid: Any, *, adapter: Any, store: Any = None,
                   clock: Callable[[], float] = time.time,
                   cache_only: bool = False) -> Optional[str]:
    """The country a stocked warehouse for ``vid`` sits in, or ``None``.

    ``None`` for every way of not knowing — no binding, no stock, no answer from
    CJ, an open circuit, a coalesced call that timed out. The distinction the
    caller needs is already made further down: ``variant_facts`` returns no facts,
    the provider refuses by name with ``variant_origin_unknown``, and the page says
    it cannot say. Returning a country here on a failure is the one outcome that
    cannot be recovered from downstream.

    ``cache_only`` adds one more way of not knowing: nobody asked. It exists
    because ``quote.cache_only`` would otherwise be a false promise — that path
    refuses before the freight call, but freight is the *second* supplier call on
    a cold product and this is the first. A server-rendered page that skipped
    freight and then blocked on ``product/stock/getInventoryByPid`` would have
    moved its worst-case TTFB, not removed it.

    It is checked below the cache read and above the call, so a warm or merely
    stale origin still answers — the point is to spend no supplier call, not to
    refuse to use what is already known.
    """
    if not isinstance(pid, str) or not pid.strip() or not isinstance(vid, str) or not vid.strip():
        return None
    pid, vid = pid.strip(), vid.strip()

    key = cache.origin_key(supplier=SUPPLIER_NAME, product_ref=pid)
    entry = cache.read(_cached(store, key), now=clock())
    if entry["state"] in (cache.STATE_FRESH, cache.STATE_STALE):
        return _country(entry["value"], vid)

    if cache_only:
        # Before `_inventory`, not inside a refusing adapter, and for the reason
        # `quote` gives at its own cache-only branch: `_inventory` is the breaker
        # and `call_once` is the coalescer. A caller that reached them would record
        # a failure against a supplier it never called, open the inventory circuit,
        # and withhold origins from the callers that *are* allowed to ask.
        return None

    try:
        variants = cache.call_once(key, lambda: _inventory(adapter, pid, clock=clock))
    except Exception:  # noqa: BLE001 — see the docstring: not knowing has one answer
        return None

    # Outside the guard and outside the try, both deliberately. A defect in this
    # projection is neither evidence about CJ nor a reason to report "we do not
    # know where this ships from" — blaming our own bug on the supplier would open
    # its circuit and withhold every other product's estimate along with it.
    origins = _origins(variants)

    if store is not None:
        _remember(store, key, cache.entry_for(
            origins, now=clock(),
            fresh_seconds=cache.ORIGIN_FRESH_SECONDS,
            stale_seconds=cache.ORIGIN_STALE_SECONDS))
    return _country(origins, vid)


def _inventory(adapter: Any, pid: str, *, clock: Callable[[], float]) -> List[Any]:
    """CJ's variant inventory rows for one product, through the circuit.

    Only the call and the shape of its answer are in here, because only those two
    are evidence about CJ. An empty list is CJ saying "nothing stocked", which is a
    cacheable negative; a body that is not a body, or one carrying no variant list
    at all, is us not knowing what CJ said and must count as a failure.

    The shape checks are *inside* the guarded producer for that last reason. Run
    after ``guard`` returns, they raise the same exception and read the same in a
    test, but the circuit has already recorded a success — so a supplier answering
    every call with something unreadable would never be backed off from.
    """
    def call():
        answer = adapter.get_inventory(pid)
        if not isinstance(answer, dict):
            raise ValueError("CJ inventory answer was not a body")
        variants = answer.get("variants")
        if not isinstance(variants, list):
            raise ValueError("CJ inventory answer carried no variant list")
        return variants

    return breaker.guard(CIRCUIT, call, clock=clock)


def _origins(variants: List[Any]) -> Dict[str, List[str]]:
    """``{vid: [country, ...]}`` — where each variant has stock, and nothing else.

    The projection is deliberate and narrow rather than tidy. ``get_inventory``
    returns counts per warehouse and per sub-warehouse, and this body is about to
    be written to a cache that an unauthenticated product page reads. Where the
    stock is, is the question this tier exists to answer; how much of it a merchant
    holds is not, and §33–35 forbid the second travelling with the first.
    """
    origins: Dict[str, List[str]] = {}
    for row in variants:
        if not isinstance(row, dict):
            continue
        row_vid = row.get("vid")
        if not isinstance(row_vid, str) or not row_vid.strip():
            continue
        countries = []
        for warehouse in row.get("warehouses") or ():
            if not isinstance(warehouse, dict) or warehouse.get("state") != IN_STOCK:
                continue
            country = warehouse.get("country")
            if isinstance(country, str) and len(country.strip()) == 2:
                code = country.strip().upper()
                if code not in countries:
                    countries.append(code)
        origins[row_vid.strip()] = countries
    return origins


def _country(origins: Any, vid: str) -> Optional[str]:
    """The warehouse to quote from, chosen so that two callers cannot disagree.

    CJ lists warehouses in whatever order it likes, and a product stocked in both
    CN and US would otherwise quote from a different country depending on which
    reply happened to be cached. The origin travels into the route cache key, so
    an unstable choice here does not merely vary the estimate — it splits one
    product's traffic across two keys and halves the hit rate for as long as both
    warehouses hold stock. Lowest code alphabetically: arbitrary, and therefore
    stable, which is the whole requirement.

    Picking the *nearest* warehouse to the buyer is the tempting refinement and is
    deliberately not done here. It would make origin a function of the
    destination, which is exactly what :func:`cache.origin_key` refuses to admit,
    and it is CJ's freight endpoint — not this module — that knows what shipping
    from a given warehouse actually costs and takes.
    """
    if not isinstance(origins, dict):
        return None
    countries = origins.get(vid)
    if not isinstance(countries, list):
        return None
    usable = sorted({c.strip().upper() for c in countries
                     if isinstance(c, str) and len(c.strip()) == 2})
    return usable[0] if usable else None


def _cached(store: Any, key: str) -> Any:
    """Read the store, treating any fault as a miss."""
    if store is None:
        return None
    try:
        return store.get(key)
    except Exception:  # noqa: BLE001 — an unreadable cache is a miss
        return None


def _remember(store: Any, key: str, written: Dict[str, Any]) -> None:
    """Store an entry, and carry on if it will not go.

    The origin above this call is already known and correct. Letting a cache write
    throw it away would mean a broken cache costs a product page its delivery
    estimate, which inverts the reason the cache is here.
    """
    try:
        store.set(key, written["envelope"], written["ttl_seconds"])
    except Exception:  # noqa: BLE001 — an unwritable cache costs the next caller a call
        pass
