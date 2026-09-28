"""Cache policy for delivery quotes: what is keyed, how long it lives, who calls.

Why this is policy and not a cache
----------------------------------
This module stores nothing. It computes cache keys, wraps values in an envelope
that records their own freshness, and classifies an envelope on the way back out.
The store underneath is ``services.cache_engine`` (Redis when configured, an
in-process TTL dict otherwise), bound by the composition layer.

Keeping the two apart is what makes the policy testable at all. The decisions
that matter here — what a key is allowed to contain, how stale is too stale, who
is permitted to trigger a provider call — are exactly the decisions that become
untestable once they are entangled with a network client.

The load-bearing constraint: forty cards is not forty calls
----------------------------------------------------------
A feed of forty products must not become forty freight quotes. Nothing in a cache
fixes that by itself, because forty products are forty *different* keys and every
one of them misses on a cold cache. So the answer is in the key design, not the
hit rate:

* **A corridor key** is coarse — supplier, origin country, destination country,
  shipping mode. It carries a transit range and no cost. Most CJ products leaving
  the same warehouse for the same country share a transit profile, so a whole feed
  collapses onto a handful of keys, and those keys can be warmed in advance.
* **A route key** is precise — variant, destination, quantity, warehouse — and
  carries the full option set a real quote needs.

List surfaces read corridor keys and are **forbidden from triggering a fetch**.
On a miss a card shows nothing, which is a product decision this module enforces
structurally: a card that may fan out to the provider is one traffic spike away
from being the reason CJ disables the account.

A corridor estimate is a weaker claim than a route estimate, and the scope
travels in the key so no surface can mistake one for the other.

Stale-while-revalidate needs the store to outlive freshness
-----------------------------------------------------------
This is the part that is easy to get silently wrong. If the entry is stored with
a TTL equal to its freshness deadline, then at the instant it goes stale it is
also *gone*, and there is nothing left to serve while revalidating. The hard TTL
handed to the store must therefore be freshness **plus** the stale window, and the
freshness deadline has to live inside the envelope where this module can read it.
A cache whose TTL is its freshness has no stale tier at all — it just has misses,
and it will look like it is working right up until the provider goes down.

A stale positive is servable at lowered confidence. A stale negative is not
-------------------------------------------------------------------------
"We do not ship there" blocks a checkout. Serving that from a stale cache keeps a
route closed on the strength of an old answer, and if the answer was really a
credential problem or an outage the route stays closed until someone notices. So
negative answers get a short freshness and **no** stale window: when they expire
the question gets asked again.

The reverse asymmetry applies to failures: a failure to *reach* the provider is
never cached as an answer at all. "CJ timed out" is not a fact about a shipping
corridor.
"""

from __future__ import annotations

import re
import threading

#: Envelope schema version, carried in the key. A change here makes old entries
#: unreadable rather than reinterpreted — deserializing a v1 body as v2 is how a
#: schema change turns into a wrong delivery date instead of a cache miss.
VERSION = "v1"

SCOPE_CORRIDOR = "corridor"
SCOPE_ORIGIN = "origin"
SCOPE_ROUTE = "route"
SCOPES = (SCOPE_CORRIDOR, SCOPE_ORIGIN, SCOPE_ROUTE)

#: Envelope classifications on read.
STATE_FRESH = "FRESH"
STATE_STALE = "STALE"
STATE_MISS = "MISS"

#: A corridor's transit profile is a property of carriers and customs lanes, not
#: of anything that moves minute to minute. Six hours fresh, a day servable while
#: revalidating — long enough that a feed almost always hits, short enough that a
#: lane change works its way out within a day.
CORRIDOR_FRESH_SECONDS = 6 * 3600
CORRIDOR_STALE_SECONDS = 24 * 3600

#: A route quote prices a specific variant to a specific place. Fifteen minutes
#: fresh because the thing that actually invalidates it is a warehouse change —
#: the item ships from somewhere else and both the cost and the lane change with
#: it. Six hours stale so a provider outage degrades confidence rather than
#: emptying every product page at once.
ROUTE_FRESH_SECONDS = 15 * 60
ROUTE_STALE_SECONDS = 6 * 3600

#: Which warehouse holds the stock. Deliberately the **same** pair as the route
#: tier, and the equality is the point rather than a coincidence: the route tier's
#: fifteen minutes is justified above by "the thing that actually invalidates it is
#: a warehouse change". A route quote cannot detect a warehouse change any faster
#: than the origin it was keyed on is re-asked, so an origin tier allowed to run
#: longer would make that justification decorative — every route refresh for the
#: next hour would re-ask with the same stale warehouse and arrive at the same
#: answer. The stale window matches for the same reason the route's does: an
#: inventory outage should cost confidence, not empty every product page at once.
ORIGIN_FRESH_SECONDS = ROUTE_FRESH_SECONDS
ORIGIN_STALE_SECONDS = ROUTE_STALE_SECONDS

#: An unsupported route is cached only long enough to stop a page refresh from
#: re-asking, and never served stale. See the module docstring: this one blocks a
#: checkout, so it has to be allowed to expire into a real question.
UNSUPPORTED_FRESH_SECONDS = 5 * 60
UNSUPPORTED_STALE_SECONDS = 0

#: Quantity is bucketed because quoting per exact unit multiplies the key space by
#: the size of a plausible basket for no accuracy gain. Each bucket is quoted at
#: its **top**: a heavier shipment is the one that may cross a weight threshold
#: onto a different channel, so the top of the bucket is the member whose answer
#: cannot be optimistic for the others.
QUANTITY_BUCKETS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 10000)

#: Matches CJ's own ceiling on ``skuQuantity``. A basket past this is not a
#: cache-key problem.
MAX_QUANTITY = 10000

#: Postal codes are truncated to this many characters. Enough to separate a
#: remote-area surcharge lane from a metropolitan one; far short of a household.
POSTAL_PREFIX_LENGTH = 3

_COUNTRY = re.compile(r"[A-Z]{2}")
_TOKEN_STRIP = re.compile(r"[^A-Z0-9]+")


class DeliveryCacheRejected(ValueError):
    """A key could not be built from what the caller supplied.

    Raising is deliberate. The alternative — dropping an unusable component and
    keying on what is left — produces a key that is still a valid key, and it will
    happily serve one country's transit time for another country's buyer.
    """


def corridor_key(*, supplier, origin=None, destination, shipping_mode=None) -> str:
    """The coarse key a list surface may read.

    ``origin`` is optional because the warehouse is often not known until a real
    quote; absent, it is recorded as unknown rather than guessed, and a key built
    without an origin never collides with one built with it.
    """
    return "|".join((
        "delivery", VERSION, SCOPE_CORRIDOR,
        _supplier(supplier),
        _country(origin, "origin", required=False),
        _country(destination, "destination", required=True),
        _token(shipping_mode),
    ))


def origin_key(*, supplier, product_ref) -> str:
    """The key for "which warehouse would this product ship from".

    Keyed on the **product**, not on a variant, because that is the shape of the
    question CJ answers: ``product/stock/getInventoryByPid`` takes a pid and
    returns every variant's warehouses in one reply. A per-variant key would
    multiply one supplier call by the variant count and cache the same body under
    each, which is the fan-out this domain exists to prevent, reintroduced one
    layer down.

    No destination and no buyer component. Where stock sits is a fact about the
    supplier's shelves; it does not vary by who is asking or where they live, and
    admitting either into this key would fragment a tier that is supposed to be
    shared by every buyer of the product.
    """
    return "|".join((
        "delivery", VERSION, SCOPE_ORIGIN,
        _supplier(supplier),
        _variant(product_ref, "product_ref"),
    ))


def route_key(*, supplier, variant_ref, destination, postal=None, quantity=1,
              origin=None, shipping_mode=None, warehouses=None) -> str:
    """The precise key a product page, cart or checkout may read.

    ``postal`` is truncated to a prefix; see :data:`POSTAL_PREFIX_LENGTH`. The
    signature accepts no city, street, recipient or contact field, so there is no
    argument through which one could reach a cache key by accident.
    """
    return "|".join((
        "delivery", VERSION, SCOPE_ROUTE,
        _supplier(supplier),
        _variant(variant_ref),
        _country(origin, "origin", required=False),
        _country(destination, "destination", required=True),
        _postal(postal),
        str(quantity_bucket(quantity)),
        _token(shipping_mode),
        _warehouses(warehouses),
    ))


def quantity_bucket(quantity) -> int:
    """The top of the bucket ``quantity`` falls in."""
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise DeliveryCacheRejected("quantity must be a whole number of units, at least one")
    if quantity > MAX_QUANTITY:
        raise DeliveryCacheRejected(f"quantity {quantity} exceeds the provider maximum")
    for top in QUANTITY_BUCKETS:
        if quantity <= top:
            return top
    raise DeliveryCacheRejected(f"quantity {quantity} falls outside every bucket")


def store_ttl(fresh_seconds: int, stale_seconds: int) -> int:
    """The hard TTL to hand the store.

    Freshness plus the stale window, because an entry the store evicts at its
    freshness deadline cannot be served while it revalidates. Getting this wrong
    does not break a test — it silently removes the stale tier.
    """
    fresh, stale = _seconds(fresh_seconds, "fresh_seconds"), _seconds(stale_seconds, "stale_seconds")
    if fresh < 1:
        raise DeliveryCacheRejected("fresh_seconds must be at least a second")
    return fresh + stale


def envelope(value, *, now: float, fresh_seconds: int, stale_seconds: int) -> dict:
    """Wrap a value with its own freshness deadlines.

    The deadlines are inside the body rather than delegated to the store's TTL,
    because the store's expiry is the *hard* one and this module needs to
    distinguish two live states beneath it.
    """
    fresh, stale = _seconds(fresh_seconds, "fresh_seconds"), _seconds(stale_seconds, "stale_seconds")
    if fresh < 1:
        raise DeliveryCacheRejected("fresh_seconds must be at least a second")
    moment = _now(now)
    return {
        "version": VERSION, "value": value, "stored_at": moment,
        "fresh_until": moment + fresh, "stale_until": moment + fresh + stale,
    }


def entry_for(value, *, now: float, fresh_seconds: int, stale_seconds: int) -> dict:
    """The envelope and the TTL it must be stored under, from one pair of numbers.

    ``envelope`` and ``store_ttl`` each take the same two tier lengths, and a call
    site that passes them separately can pass different ones — producing an
    envelope that advertises a stale window the store has already evicted. That is
    the exact silent failure ``store_ttl`` warns about, reached through a typo at a
    call site rather than through a wrong formula here. So the two are only ever
    derived together.

    Returns ``{"envelope", "ttl_seconds"}``.
    """
    return {
        "envelope": envelope(value, now=now, fresh_seconds=fresh_seconds,
                             stale_seconds=stale_seconds),
        "ttl_seconds": store_ttl(fresh_seconds, stale_seconds),
    }


def read(entry, *, now: float) -> dict:
    """Classify a stored envelope as FRESH, STALE or MISS.

    Anything unrecognizable is a MISS, including an envelope written by a
    different version. A cache that tries to salvage a body it does not
    understand is a cache that will one day render a field that has moved.
    """
    moment = _now(now)
    if not isinstance(entry, dict) or entry.get("version") != VERSION:
        return _miss()
    try:
        fresh_until, stale_until = float(entry["fresh_until"]), float(entry["stale_until"])
        stored_at = float(entry["stored_at"])
    except (KeyError, TypeError, ValueError):
        return _miss()
    if "value" not in entry or stale_until < fresh_until:
        return _miss()
    if moment < fresh_until:
        state = STATE_FRESH
    elif moment < stale_until:
        state = STATE_STALE
    else:
        return _miss()
    return {"state": state, "value": entry["value"], "age_seconds": max(0.0, moment - stored_at),
            "revalidate": state == STATE_STALE}


# ---------------------------------------------------------------------------
# Single-flight
# ---------------------------------------------------------------------------

#: How long a follower waits for the in-flight leader before giving up. Bounded
#: well below the provider's own timeout on purpose: a follower that waits out a
#: wedged upstream has converted one slow request into many, which is the problem
#: coalescing exists to solve, inverted.
FLIGHT_WAIT_SECONDS = 3.0

_FLIGHTS: dict[str, dict] = {}
_FLIGHT_LOCK = threading.Lock()


class CoalesceTimeout(Exception):
    """A request for the same key was already running and did not finish in time.

    Deliberately not a signal to call the provider. The caller should serve a
    stale entry or report that it cannot say — piling on a second call is what
    this whole mechanism exists to prevent.
    """


def call_once(key: str, producer, *, wait_seconds: float = FLIGHT_WAIT_SECONDS):
    """Run ``producer`` once for ``key``, sharing the result with concurrent callers.

    Scope, stated plainly: this coalesces **within one process**. Under gunicorn
    with N workers, N simultaneous misses on the same key make up to N calls, not
    one. Cross-process coalescing needs a distributed lock, and the usual
    implementation — hold a Redis lock across a slow HTTP call — trades a
    thundering herd for a wedged key and a lock nobody releases when the worker
    dies. Per-process coalescing collapses the common case, which is one page
    render fanning out, and it cannot deadlock.

    A follower receives the leader's exception rather than retrying. They would
    have failed the same way a moment later, and a stampede of retries against a
    provider that is already failing is how a rate limit becomes a suspension.
    """
    if not isinstance(key, str) or not key:
        raise DeliveryCacheRejected("a flight needs a key")
    with _FLIGHT_LOCK:
        flight = _FLIGHTS.get(key)
        leader = flight is None
        if leader:
            flight = {"done": threading.Event(), "value": None, "error": None}
            _FLIGHTS[key] = flight

    if leader:
        try:
            flight["value"] = producer()
        except BaseException as error:  # noqa: BLE001 — recorded, then re-raised below
            flight["error"] = error
        finally:
            # Deregister before waking the followers, so a caller arriving after
            # this point starts a new flight instead of joining a finished one.
            with _FLIGHT_LOCK:
                if _FLIGHTS.get(key) is flight:
                    del _FLIGHTS[key]
            flight["done"].set()
        if flight["error"] is not None:
            raise flight["error"]
        return flight["value"]

    if not flight["done"].wait(timeout=max(0.0, float(wait_seconds))):
        raise CoalesceTimeout(key)
    if flight["error"] is not None:
        raise flight["error"]
    return flight["value"]


def in_flight() -> int:
    """How many keys are mid-flight. For diagnostics and for tests."""
    with _FLIGHT_LOCK:
        return len(_FLIGHTS)


# ---------------------------------------------------------------------------
# Key component normalization
# ---------------------------------------------------------------------------

def _supplier(value) -> str:
    token = _token(value)
    if token == "*":
        raise DeliveryCacheRejected("supplier is required")
    return token


def _variant(value, what: str = "variant_ref") -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeliveryCacheRejected(f"{what} is required to build this key")
    token = _TOKEN_STRIP.sub("-", value.strip().upper())[:190].strip("-")
    if not token:
        raise DeliveryCacheRejected(f"{what} carries no usable characters")
    return token


def _country(value, what: str, *, required: bool) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise DeliveryCacheRejected(
                f"{what} country is required; keying without it would serve one "
                f"country's transit time to another country's buyer")
        return "*"
    if not isinstance(value, str):
        raise DeliveryCacheRejected(f"{what} country must be an ISO-3166 alpha-2 code")
    code = value.strip().upper()
    if not _COUNTRY.fullmatch(code):
        raise DeliveryCacheRejected(f"{what} country {value!r} is not an ISO-3166 alpha-2 code")
    return code


def _postal(value) -> str:
    if value is None:
        return "*"
    if not isinstance(value, str):
        raise DeliveryCacheRejected("postal must be text or None")
    prefix = _TOKEN_STRIP.sub("", value.upper())[:POSTAL_PREFIX_LENGTH]
    return prefix or "*"


def _token(value) -> str:
    if value is None:
        return "*"
    if not isinstance(value, str):
        raise DeliveryCacheRejected("key components must be text or None")
    token = _TOKEN_STRIP.sub("-", value.strip().upper()).strip("-")
    return token[:40] or "*"


def _warehouses(values) -> str:
    """Sorted and deduplicated, because a set of warehouses has no order.

    Two callers listing the same warehouses differently must not produce two keys
    for one answer — that is a halved hit rate with nothing visibly wrong.
    """
    if values is None:
        return "*"
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        raise DeliveryCacheRejected("warehouses must be a collection or None")
    tokens = sorted({_token(value) for value in values} - {"*"})
    return ",".join(tokens[:20]) or "*"


def _seconds(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DeliveryCacheRejected(f"{what} must be a non-negative whole number of seconds")
    return value


def _now(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise DeliveryCacheRejected("now must be a real epoch timestamp")
    return float(value)


def _miss() -> dict:
    return {"state": STATE_MISS, "value": None, "age_seconds": None, "revalidate": True}
