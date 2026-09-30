"""Cache keys separate what must be separate, and stale is a real tier.

What this file is defending
---------------------------
Three failures, none of which shows up as a red build:

* **A key that collides.** Two destinations, one key, and a buyer in one country
  is shown the other country's transit time. The estimate is internally perfect;
  it is simply about somewhere else.
* **A stale tier that does not exist.** If the store's TTL equals the entry's
  freshness, the entry is gone the instant it goes stale, so there is nothing to
  serve while revalidating. Every "stale" read is really a miss, and the cache
  looks fine until the provider goes down and every product page empties at once.
* **A cache that leaks a buyer's address.** A full postal code in a cache key is a
  personal identifier in a shared Redis instance with a day-long TTL.

The single-flight tests use real threads, because the property under test is what
happens when two callers arrive at once and nothing else reproduces that.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.delivery import cache as c

NOW = 1_800_000_000.0


def await_leader():
    """Block until a flight is registered, then give up.

    The spin is the point — the follower has to arrive while the leader is still
    running or the test proves nothing. The deadline is there because an
    unbounded spin in CI is a hung job rather than a failed one.
    """
    deadline = time.monotonic() + 5
    while c.in_flight() == 0:
        if time.monotonic() > deadline:
            pytest.fail("no flight was ever registered")
        time.sleep(0.001)


# ---------------------------------------------------------------------------
# What a key must separate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    (dict(destination="US"), dict(destination="CA")),
    (dict(destination="US", origin="CN"), dict(destination="US", origin="US")),
    (dict(destination="US", origin=None), dict(destination="US", origin="CN")),
    (dict(destination="US", shipping_mode="air"), dict(destination="US", shipping_mode="sea")),
    (dict(destination="US", shipping_mode=None), dict(destination="US", shipping_mode="air")),
])
def test_a_corridor_key_separates_anything_that_changes_the_answer(a, b):
    assert c.corridor_key(supplier="cj", **a) != c.corridor_key(supplier="cj", **b)


@pytest.mark.parametrize("a, b", [
    (dict(variant_ref="V1"), dict(variant_ref="V2")),
    (dict(variant_ref="V1", destination="US"), dict(variant_ref="V1", destination="GB")),
    (dict(variant_ref="V1", postal="90210"), dict(variant_ref="V1", postal="10001")),
    (dict(variant_ref="V1", postal=None), dict(variant_ref="V1", postal="90210")),
    (dict(variant_ref="V1", quantity=1), dict(variant_ref="V1", quantity=3)),
    (dict(variant_ref="V1", warehouses=["CN-1"]), dict(variant_ref="V1", warehouses=["US-1"])),
    (dict(variant_ref="V1", warehouses=None), dict(variant_ref="V1", warehouses=["CN-1"])),
])
def test_a_route_key_separates_anything_that_changes_the_answer(a, b):
    base = dict(supplier="cj", destination="US")
    assert c.route_key(**{**base, **a}) != c.route_key(**{**base, **b})


def test_a_corridor_key_and_a_route_key_never_collide():
    """The two carry different claims. A corridor transit range presented as a
    route quote is a coarse answer wearing a precise answer's confidence."""
    assert c.corridor_key(supplier="cj", destination="US") != c.route_key(
        supplier="cj", variant_ref="V1", destination="US")
    assert c.SCOPE_CORRIDOR in c.corridor_key(supplier="cj", destination="US")
    assert c.SCOPE_ROUTE in c.route_key(supplier="cj", variant_ref="V1", destination="US")


def test_the_key_is_versioned_so_a_schema_change_misses_rather_than_reinterprets():
    assert c.VERSION in c.corridor_key(supplier="cj", destination="US")


# ---------------------------------------------------------------------------
# What a key must NOT separate
# ---------------------------------------------------------------------------

def test_equivalent_inputs_produce_one_key_rather_than_halving_the_hit_rate():
    """Case, surrounding space and warehouse ordering are not differences. Keying
    on them is a quietly halved hit rate with nothing visibly wrong."""
    assert c.route_key(supplier="cj", variant_ref="V1", destination="us",
                       postal="90210", warehouses=["CN-1", "US-2"]) == \
           c.route_key(supplier="CJ", variant_ref="v1", destination=" US ",
                       postal="90-210", warehouses=["US-2", "CN-1", "US-2"])


def test_quantities_in_one_bucket_share_a_key():
    keys = {c.route_key(supplier="cj", variant_ref="V1", destination="US", quantity=n)
            for n in (3, 4, 5)}
    assert len(keys) == 1


def test_a_bucket_is_quoted_at_its_top_so_the_answer_cannot_be_optimistic():
    """A heavier shipment is the one that may cross a weight threshold onto
    another channel, so the bucket's top is the member whose answer is safe for
    the rest of it."""
    assert c.quantity_bucket(3) == 5
    assert c.quantity_bucket(1) == 1
    assert c.quantity_bucket(11) == 25


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, None, "3", c.MAX_QUANTITY + 1])
def test_an_unusable_quantity_raises_rather_than_bucketing_to_something(bad):
    with pytest.raises(c.DeliveryCacheRejected):
        c.quantity_bucket(bad)


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------

def test_a_full_postal_code_never_reaches_the_key():
    """A shared Redis instance with a day-long TTL is not a place for an address."""
    key = c.route_key(supplier="cj", variant_ref="V1", destination="US", postal="90210-1234")
    assert "90210" not in key and "1234" not in key
    assert "|902|" in key


def test_the_route_key_signature_admits_no_identifying_field():
    """Truncation defends the postal code. This defends everything else: there is
    no argument through which a name, street or contact detail could arrive."""
    import inspect
    accepted = set(inspect.signature(c.route_key).parameters)
    assert accepted == {"supplier", "variant_ref", "destination", "postal", "quantity",
                        "origin", "shipping_mode", "warehouses"}


def test_the_origin_key_is_the_supplier_and_the_product_and_nothing_else():
    """Pinned as a literal rather than compared against another call to the same
    function. Where a supplier's stock sits does not vary by who is asking, so a
    component that varies by buyer would fragment a tier meant to be shared by
    every viewer of the product -- and a self-comparison cannot see one arrive,
    because both sides of it move together."""
    assert c.origin_key(supplier="cj", product_ref="PID-1") == "delivery|v1|origin|CJ|PID-1"


def test_the_origin_key_signature_admits_no_buyer_or_destination():
    import inspect
    assert set(inspect.signature(c.origin_key).parameters) == {"supplier", "product_ref"}


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(supplier="cj", destination=None),
    dict(supplier="cj", destination=""),
    dict(supplier="cj", destination="USA"),
    dict(supplier="cj", destination="U"),
    dict(supplier="cj", destination=840),
    dict(supplier="", destination="US"),
    dict(supplier=None, destination="US"),
])
def test_a_key_that_cannot_be_built_raises_rather_than_dropping_the_component(kwargs):
    """Dropping an unusable component still yields a valid-looking key, and that
    key will serve one country's transit time to another country's buyer."""
    with pytest.raises(c.DeliveryCacheRejected):
        c.corridor_key(**kwargs)


@pytest.mark.parametrize("variant", [None, "", "   ", 7, "---"])
def test_a_route_key_without_a_usable_variant_is_refused(variant):
    with pytest.raises(c.DeliveryCacheRejected):
        c.route_key(supplier="cj", variant_ref=variant, destination="US")


# ---------------------------------------------------------------------------
# The stale tier has to actually exist
# ---------------------------------------------------------------------------

def test_the_store_ttl_outlives_freshness_or_there_is_no_stale_tier():
    """The one that is silently wrong. A TTL equal to freshness means the entry is
    evicted at the moment it becomes servable-but-stale, so the stale tier is
    really a miss tier — and it looks fine until the provider goes down."""
    assert c.store_ttl(900, 21600) == 900 + 21600
    assert c.store_ttl(c.ROUTE_FRESH_SECONDS, c.ROUTE_STALE_SECONDS) > c.ROUTE_FRESH_SECONDS


def test_an_envelope_is_fresh_then_stale_then_gone():
    entry = c.envelope({"q": 1}, now=NOW, fresh_seconds=100, stale_seconds=50)
    assert c.read(entry, now=NOW)["state"] == c.STATE_FRESH
    assert c.read(entry, now=NOW + 99)["state"] == c.STATE_FRESH
    assert c.read(entry, now=NOW + 100)["state"] == c.STATE_STALE
    assert c.read(entry, now=NOW + 149)["state"] == c.STATE_STALE
    assert c.read(entry, now=NOW + 150)["state"] == c.STATE_MISS


def test_a_stale_read_still_carries_its_value_and_asks_to_be_revalidated():
    entry = c.envelope({"q": 1}, now=NOW, fresh_seconds=10, stale_seconds=100)
    stale = c.read(entry, now=NOW + 50)
    assert stale["value"] == {"q": 1}
    assert stale["revalidate"] is True
    assert stale["age_seconds"] == 50


def test_a_fresh_read_does_not_ask_to_be_revalidated():
    entry = c.envelope({"q": 1}, now=NOW, fresh_seconds=100, stale_seconds=100)
    assert c.read(entry, now=NOW)["revalidate"] is False


@pytest.mark.parametrize("fresh,stale", [
    (c.ROUTE_FRESH_SECONDS, c.ROUTE_STALE_SECONDS),
    (c.CORRIDOR_FRESH_SECONDS, c.CORRIDOR_STALE_SECONDS),
    (c.UNSUPPORTED_FRESH_SECONDS, c.UNSUPPORTED_STALE_SECONDS),
])
def test_an_entry_never_advertises_a_stale_window_the_store_has_evicted(fresh, stale):
    """``envelope`` and ``store_ttl`` take the same two numbers, so a call site can
    pass different ones to each and produce an entry whose stale window outlives the
    row holding it. Deriving both together is what makes that unreachable, so the
    pairing is asserted rather than left to each call site's care."""
    paired = c.entry_for({"any": "value"}, now=NOW,
                         fresh_seconds=fresh, stale_seconds=stale)
    advertised = paired["envelope"]["stale_until"] - paired["envelope"]["stored_at"]
    assert paired["ttl_seconds"] >= advertised, \
        "the store evicts the entry before its own stale deadline"


def test_a_negative_answer_has_no_stale_window_because_it_blocks_a_checkout():
    """Serving "we do not ship there" from a stale cache keeps a route closed on
    the strength of an old answer — and if that answer was really a credential
    problem, it stays closed until a person notices."""
    assert c.UNSUPPORTED_STALE_SECONDS == 0
    entry = c.envelope({"state": "UNSUPPORTED_ROUTE"}, now=NOW,
                       fresh_seconds=c.UNSUPPORTED_FRESH_SECONDS,
                       stale_seconds=c.UNSUPPORTED_STALE_SECONDS)
    assert c.read(entry, now=NOW + c.UNSUPPORTED_FRESH_SECONDS)["state"] == c.STATE_MISS


def test_a_positive_answer_does_have_a_stale_window():
    assert c.ROUTE_STALE_SECONDS > 0 and c.CORRIDOR_STALE_SECONDS > 0


@pytest.mark.parametrize("entry", [
    None, "not an envelope", 42, {},
    {"version": "v0", "value": 1, "stored_at": NOW, "fresh_until": NOW + 9, "stale_until": NOW + 9},
    {"version": c.VERSION, "stored_at": NOW, "fresh_until": NOW + 9, "stale_until": NOW + 9},
    {"version": c.VERSION, "value": 1, "stored_at": NOW, "fresh_until": "soon", "stale_until": NOW + 9},
    {"version": c.VERSION, "value": 1, "stored_at": NOW, "fresh_until": NOW + 9, "stale_until": NOW + 1},
])
def test_an_unreadable_envelope_is_a_miss_rather_than_a_salvage_attempt(entry):
    """A cache that reconstructs a body it does not understand will one day render
    a field that has moved."""
    assert c.read(entry, now=NOW)["state"] == c.STATE_MISS


def test_a_value_of_none_is_still_a_cached_answer():
    """``cache_engine.cache_get`` cannot distinguish a stored None from a miss.
    The envelope can, and must: "no options on this route" is an answer worth
    keeping, and re-asking for it on every page view is the fan-out this cache
    exists to prevent."""
    entry = c.envelope(None, now=NOW, fresh_seconds=100, stale_seconds=0)
    assert c.read(entry, now=NOW)["state"] == c.STATE_FRESH
    assert c.read(entry, now=NOW)["value"] is None


@pytest.mark.parametrize("kwargs", [
    {"fresh_seconds": 0}, {"fresh_seconds": -1}, {"fresh_seconds": 1.5},
    {"fresh_seconds": True}, {"stale_seconds": -1}, {"stale_seconds": 1.5},
])
def test_a_malformed_lifetime_raises_rather_than_defaulting(kwargs):
    with pytest.raises(c.DeliveryCacheRejected):
        c.envelope({}, now=NOW, **{"fresh_seconds": 100, "stale_seconds": 100, **kwargs})


@pytest.mark.parametrize("now", [None, "now", True, float("nan")])
def test_a_clock_that_is_not_a_timestamp_is_refused(now):
    with pytest.raises(c.DeliveryCacheRejected):
        c.envelope({}, now=now, fresh_seconds=10, stale_seconds=10)


# ---------------------------------------------------------------------------
# Single-flight
# ---------------------------------------------------------------------------

def test_concurrent_callers_for_one_key_make_one_provider_call():
    """The property §18 asks for: one render fanning out must not multiply."""
    calls = []
    release = threading.Event()

    def producer():
        calls.append(1)
        release.wait(timeout=5)
        return "quoted"

    results = {}

    def caller(name):
        results[name] = c.call_once("k", producer, wait_seconds=5)

    threads = [threading.Thread(target=caller, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
        # Let the leader register its flight before the followers look for it.
        if thread is threads[0]:
            await_leader()
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert len(calls) == 1, "the producer ran once per caller"
    assert set(results.values()) == {"quoted"}
    assert len(results) == 4


def test_a_flight_is_deregistered_so_the_next_caller_asks_again():
    """A flight that outlives its producer pins one answer forever."""
    calls = []
    for _ in range(3):
        c.call_once("k", lambda: calls.append(1))
    assert len(calls) == 3
    assert c.in_flight() == 0


def test_separate_keys_do_not_coalesce_into_one_answer():
    assert c.call_once("a", lambda: "A") == "A"
    assert c.call_once("b", lambda: "B") == "B"


def test_a_follower_receives_the_leaders_failure_rather_than_retrying_it():
    """A stampede of retries against a provider that is already failing is how a
    rate limit becomes a suspension."""
    calls = []
    release = threading.Event()

    def producer():
        calls.append(1)
        release.wait(timeout=5)
        raise RuntimeError("cj down")

    errors = []

    def caller():
        try:
            c.call_once("k", producer, wait_seconds=5)
        except RuntimeError as exc:
            errors.append(str(exc))

    leader = threading.Thread(target=caller)
    leader.start()
    await_leader()
    follower = threading.Thread(target=caller)
    follower.start()
    release.set()
    leader.join(timeout=10)
    follower.join(timeout=10)

    assert len(calls) == 1
    assert errors == ["cj down", "cj down"]
    assert c.in_flight() == 0, "a failed flight must still be deregistered"


def test_a_follower_that_waits_too_long_gives_up_instead_of_calling_the_provider():
    """The inversion worth naming: a follower that waits out a wedged upstream has
    turned one slow request into several. It degrades instead."""
    release = threading.Event()

    def producer():
        release.wait(timeout=5)
        return "late"

    leader = threading.Thread(target=lambda: c.call_once("k", producer, wait_seconds=5))
    leader.start()
    await_leader()
    try:
        with pytest.raises(c.CoalesceTimeout):
            c.call_once("k", lambda: pytest.fail("a follower must not call the producer"),
                        wait_seconds=0.05)
    finally:
        release.set()
        leader.join(timeout=10)


@pytest.mark.parametrize("key", [None, "", 42])
def test_a_flight_without_a_key_is_refused(key):
    with pytest.raises(c.DeliveryCacheRejected):
        c.call_once(key, lambda: None)
