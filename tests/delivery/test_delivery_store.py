"""The binding to ``cache_engine`` keeps what it was given, or refuses it.

What this file is defending
---------------------------
Four failures, none of which turns a build red:

* **A value that means one thing locally and another in production.**
  ``cache_engine`` keeps values by reference in its in-process dict and as
  ``json.dumps(value, default=str)`` in Redis. ``default=str`` does not raise on
  a type JSON cannot hold — it stringifies it. So a ``Decimal`` freight cost is a
  ``Decimal`` on every developer machine and ``"12.34"`` in the one environment
  with ``REDIS_URL`` set, and a tuple is a tuple here and a list there. The
  divergence is silent at the write, silent at the read, and surfaces as
  arithmetic on a string in production only.
* **A TTL the store rewrites.** ``max(1, int(ttl_seconds or 60))`` turns a zero
  into a full minute of caching and truncates a float, which kills the row before
  the ``stale_until`` its own body advertises and clips the tail off the stale
  tier. Both are silent.
* **A write into another subsystem's key.** One flat ``cache_engine`` namespace
  serves presence and counters too. Reading a foreign key is harmless because
  ``cache.read`` misses on it; overwriting one is not.
* **A cache outage becoming a page outage.** ``store.get`` is called bare inside
  ``quote_delivery``. If it can raise, a flapping Redis is a 500 on every product
  page — and on the write side it would discard an estimate that was already
  computed correctly, making a broken cache worse than no cache at all.

The tests use a fake engine rather than a Redis, because every property here is
about what this module does to a value on the way past, and the one property that
*is* about Redis — that a value survives its serializer — is checked by running
the real serializer over the value instead of by standing up a server.
"""

import json
import os
import sys
import time
from datetime import datetime
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import cache_engine
from services.delivery import cache
from services.delivery import store as delivery_store

NOW = 1_800_000_000.0

ROUTE_KEY = cache.route_key(
    supplier="cj", variant_ref="PID-1:VID-1", destination="US",
    postal="94107", quantity=1, origin="CN",
)


class Engine:
    """A ``cache_engine`` stand-in that records, and fails on demand."""

    def __init__(self, *, fail_get=False, fail_set=False):
        self.rows = {}
        self.sets = []
        self.gets = []
        self._fail_get = fail_get
        self._fail_set = fail_set

    def cache_get(self, key, default=None):
        self.gets.append(key)
        if self._fail_get:
            raise RuntimeError("redis went away mid-read")
        return self.rows.get(key, default)

    def cache_set(self, key, value, ttl_seconds=60):
        self.sets.append((key, value, ttl_seconds))
        if self._fail_set:
            raise RuntimeError("redis went away mid-write")
        self.rows[key] = value
        return True


def bound(**kwargs):
    engine = Engine(**kwargs)
    return delivery_store.CacheEngineStore(engine=engine), engine


# ---------------------------------------------------------------------------
# The round trip the delivery domain actually depends on
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fresh,stale", [
    (cache.ROUTE_FRESH_SECONDS, cache.ROUTE_STALE_SECONDS),
    (cache.CORRIDOR_FRESH_SECONDS, cache.CORRIDOR_STALE_SECONDS),
    (cache.UNSUPPORTED_FRESH_SECONDS, cache.UNSUPPORTED_STALE_SECONDS),
])
def test_an_envelope_comes_back_fresh_through_the_real_store_seam(fresh, stale):
    """``cache.read`` re-reads three timestamps off the body it gets back.

    If the store altered them — or the body they sit in — the entry would come
    back a MISS and the cache would degrade to nothing while looking populated.
    """
    store, _ = bound()
    written = cache.entry_for({"supported": True, "transit": {"min_days": 7, "max_days": 12}},
                              now=NOW, fresh_seconds=fresh, stale_seconds=stale)
    assert store.set(ROUTE_KEY, written["envelope"], written["ttl_seconds"]) is True

    seen = cache.read(store.get(ROUTE_KEY), now=NOW + 1.0)
    assert seen["state"] == cache.STATE_FRESH
    assert seen["value"]["transit"]["max_days"] == 12


def test_an_absent_key_reads_as_a_miss_rather_than_an_error():
    store, _ = bound()
    assert store.get(ROUTE_KEY) is None
    assert cache.read(store.get(ROUTE_KEY), now=NOW)["state"] == cache.STATE_MISS


def test_the_engine_receives_the_body_itself_and_not_a_serialized_copy():
    """The in-memory tier keeps what it is handed, so handing it a string would
    make every local read fail ``cache.read``'s ``isinstance(entry, dict)`` check."""
    store, engine = bound()
    written = cache.entry_for({"supported": True}, now=NOW,
                              fresh_seconds=cache.ROUTE_FRESH_SECONDS,
                              stale_seconds=cache.ROUTE_STALE_SECONDS)
    store.set(ROUTE_KEY, written["envelope"], written["ttl_seconds"])

    key, value, ttl = engine.sets[-1]
    assert key == ROUTE_KEY
    assert value == written["envelope"]
    assert ttl == written["ttl_seconds"]


# ---------------------------------------------------------------------------
# Values the two backends would disagree about
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hazard,value", [
    ("a Decimal money amount", {"provider_total": Decimal("12.34")}),
    ("a tuple", {"warehouses": ("CN", "US")}),
    ("a set", {"channels": {"CJPacket"}}),
    ("a datetime", {"stored_at": datetime(2026, 9, 27)}),
    ("a non-string dict key", {"by_quantity": {1: "CJPacket"}}),
    ("a bytes payload", {"blob": b"\x00"}),
])
def test_a_value_the_two_backends_would_disagree_about_is_refused(hazard, value):
    """Each of these is kept faithfully by the in-process dict and mangled by
    Redis, so accepting it means the cache holds different things in different
    environments and only production is wrong."""
    store, engine = bound()
    with pytest.raises(delivery_store.StoreRejected):
        store.set(ROUTE_KEY, value, 60)
    assert engine.sets == [], "the value reached the store before it was checked"


@pytest.mark.parametrize("value", [
    {"ratio": float("nan")},
    {"ceiling": float("inf")},
])
def test_a_non_finite_number_is_refused_rather_than_written_as_invalid_json(value):
    """``json.dumps`` emits a bare ``NaN``/``Infinity`` token, which is not JSON.
    Redis would store it and a non-Python reader would choke on it."""
    store, _ = bound()
    with pytest.raises(delivery_store.StoreRejected):
        store.set(ROUTE_KEY, value, 60)


def test_the_hazard_list_is_not_theoretical():
    """Proves the premise the refusals rest on: ``cache_engine``'s serializer
    really does keep these silently, and as something other than what it was.

    Without this the tests above would pass just as happily if ``default=str``
    were removed upstream, and the refusals would be guarding nothing.
    """
    mangled = json.loads(cache_engine._serialize({"provider_total": Decimal("12.34"),
                                                  "warehouses": ("CN", "US")}))
    assert mangled["provider_total"] == "12.34", "a Decimal no longer stringifies"
    assert mangled["warehouses"] == ["CN", "US"], "a tuple no longer becomes a list"


@pytest.mark.parametrize("fresh,stale", [
    (cache.ROUTE_FRESH_SECONDS, cache.ROUTE_STALE_SECONDS),
    (cache.CORRIDOR_FRESH_SECONDS, cache.CORRIDOR_STALE_SECONDS),
    (cache.UNSUPPORTED_FRESH_SECONDS, cache.UNSUPPORTED_STALE_SECONDS),
])
def test_every_envelope_this_domain_really_writes_is_accepted(fresh, stale):
    """The guard has to be narrower than "refuse anything unusual". An evidence
    body carries nested dicts, lists, floats, ints, strings, booleans and None,
    and a check strict enough to reject one of those would disable the cache."""
    evidence = {
        "supported": True,
        "route": {"option_id": "CJPacket", "channel_id": None,
                  "service": "CJPacket Ordinary", "provider_total": 12.34,
                  "currency": "USD", "provider_aging_text": "7-12"},
        "transit": {"min_days": 7, "max_days": 12, "business_days": False},
        "warehouses": ["CN"],
    }
    store, _ = bound()
    written = cache.entry_for(evidence, now=NOW, fresh_seconds=fresh, stale_seconds=stale)
    assert store.set(ROUTE_KEY, written["envelope"], written["ttl_seconds"]) is True


# ---------------------------------------------------------------------------
# TTLs cache_engine would rewrite without saying so
# ---------------------------------------------------------------------------

def test_a_zero_ttl_is_refused_because_the_store_turns_it_into_a_minute():
    """``int(ttl_seconds or 60)``: asking for no caching gets sixty seconds of it."""
    store, engine = bound()
    with pytest.raises(delivery_store.StoreRejected):
        store.set(ROUTE_KEY, {"any": "value"}, 0)
    assert engine.sets == []


def test_the_zero_ttl_rewrite_is_not_theoretical():
    """The premise the refusal above rests on, taken from the real module.

    Asked to hold a value for zero seconds, ``cache_engine`` holds it for sixty.
    Without this the refusal would keep passing after the rewrite was removed
    upstream, guarding a hazard that no longer exists.
    """
    if cache_engine.redis_client() is not None:
        pytest.skip("reads the in-process tier's expiry; Redis holds it server-side")
    probe = "delivery|probe|zero-ttl"
    cache_engine.cache_set(probe, {"any": "value"}, 0)
    try:
        expires_at, _ = cache_engine._MEMORY[probe]
        assert expires_at - time.time() > 30, "a zero TTL is no longer widened to 60s"
    finally:
        cache_engine.cache_delete(probe)


@pytest.mark.parametrize("ttl", [900.9, 0.5, -1, True, False, None, "900"])
def test_a_ttl_that_is_not_a_positive_whole_number_of_seconds_is_refused(ttl):
    """A float is truncated by ``int()``, so the row expires *before* the
    ``stale_until`` recorded in its own body and the stale tier loses its tail.
    ``True`` is included because ``isinstance(True, int)`` — a boolean would pass
    as a one-second TTL."""
    store, engine = bound()
    with pytest.raises(delivery_store.StoreRejected):
        store.set(ROUTE_KEY, {"any": "value"}, ttl)
    assert engine.sets == []


@pytest.mark.parametrize("fresh,stale", [
    (cache.ROUTE_FRESH_SECONDS, cache.ROUTE_STALE_SECONDS),
    (cache.CORRIDOR_FRESH_SECONDS, cache.CORRIDOR_STALE_SECONDS),
    (cache.UNSUPPORTED_FRESH_SECONDS, cache.UNSUPPORTED_STALE_SECONDS),
])
def test_the_ttl_reaching_the_store_still_outlives_the_body_it_carries(fresh, stale):
    """The property ``cache.store_ttl`` exists for, asserted at the far end of the
    seam: whatever the store is finally told must not expire the row before the
    stale deadline the envelope advertises."""
    store, engine = bound()
    written = cache.entry_for({"any": "value"}, now=NOW,
                              fresh_seconds=fresh, stale_seconds=stale)
    store.set(ROUTE_KEY, written["envelope"], written["ttl_seconds"])

    _, body, ttl = engine.sets[-1]
    assert ttl >= body["stale_until"] - body["stored_at"], \
        "the store evicts the entry before its own stale deadline"


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "presence:live:1",
    "delivery",
    "delivery|v0|route|CJ|PID-1|CN|US|941|1|*|*",
    "",
    None,
    42,
    b"delivery|v1|route",
])
def test_a_key_this_domain_did_not_mint_is_refused(key):
    """A write under a foreign key evicts whatever was there. The stale version
    is in the list because an envelope written under a ``v0`` key is exactly what
    a schema bump is supposed to strand rather than reinterpret."""
    store, engine = bound()
    with pytest.raises(delivery_store.StoreRejected):
        store.set(key, {"any": "value"}, 60)
    assert engine.sets == []


@pytest.mark.parametrize("key", [
    cache.route_key(supplier="cj", variant_ref="PID-1:VID-1", destination="US",
                    postal="94107", quantity=1, origin="CN"),
    cache.route_key(supplier="cj", variant_ref="PID-2:VID-9", destination="GB", quantity=25),
    cache.corridor_key(supplier="cj", destination="DE"),
    cache.corridor_key(supplier="cj", origin="CN", destination="US",
                       shipping_mode="CJPacket"),
])
def test_every_key_the_cache_module_mints_is_one_the_store_accepts(key):
    """The guard is derived from ``cache.VERSION`` rather than written out, so
    this is what keeps the two from drifting: a version bump that moved the key
    builders and not the prefix would lock the domain out of its own cache, and
    every write would raise instead of quietly missing."""
    store, _ = bound()
    assert store.set(key, {"any": "value"}, 60) is True
    assert store.get(key) == {"any": "value"}


def test_a_foreign_key_is_refused_on_read_too_rather_than_read_through():
    """Reading one is harmless — ``cache.read`` misses on a body without our
    version — but a read that reaches a foreign key at all is a bug worth a
    stack trace, and it is the same mistake as writing one."""
    store, engine = bound()
    with pytest.raises(delivery_store.StoreRejected):
        store.get("presence:live:1")
    assert engine.gets == []


# ---------------------------------------------------------------------------
# A cache fault is not a delivery fault
# ---------------------------------------------------------------------------

def test_a_store_that_cannot_be_read_is_a_miss_and_not_an_exception():
    store, _ = bound(fail_get=True)
    assert store.get(ROUTE_KEY) is None


def test_a_store_that_cannot_be_written_reports_it_without_raising():
    store, _ = bound(fail_set=True)
    assert store.set(ROUTE_KEY, {"any": "value"}, 60) is False


def test_a_caller_bug_still_raises_even_though_a_store_fault_does_not():
    """The two live next to each other, so ordering is the whole thing: if the
    validation ran inside the fault handler, a bad key or an unstorable value
    would come back as a quiet ``False`` and never be fixed."""
    store, _ = bound(fail_set=True)
    with pytest.raises(delivery_store.StoreRejected):
        store.set("presence:live:1", {"any": "value"}, 60)
    with pytest.raises(delivery_store.StoreRejected):
        store.set(ROUTE_KEY, {"total": Decimal("1.00")}, 60)


@pytest.mark.parametrize("engine", [None, object(), "cache_engine"])
def test_an_engine_that_cannot_serve_as_a_store_is_refused_at_construction(engine):
    """Rather than at the first cache miss under load."""
    with pytest.raises(delivery_store.StoreRejected):
        delivery_store.CacheEngineStore(engine=engine)


def test_the_default_engine_is_the_real_cache_engine():
    """Bound by default so a call site cannot get a silently inert store."""
    assert delivery_store.CacheEngineStore()._engine is cache_engine


def test_a_real_round_trip_through_cache_engine_itself_comes_back_fresh():
    """Everything above uses a fake engine. This one uses the module that will be
    bound in production, so the seam is proven against the real store's own
    keying, TTL handling and value retention rather than against a stand-in.
    """
    store = delivery_store.CacheEngineStore()
    key = cache.route_key(supplier="cj", variant_ref="ROUNDTRIP-PROBE",
                          destination="US", quantity=1)
    written = cache.entry_for({"supported": True}, now=NOW,
                              fresh_seconds=cache.ROUTE_FRESH_SECONDS,
                              stale_seconds=cache.ROUTE_STALE_SECONDS)
    try:
        assert store.set(key, written["envelope"], written["ttl_seconds"]) is True
        assert cache.read(store.get(key), now=NOW)["state"] == cache.STATE_FRESH
    finally:
        cache_engine.cache_delete(key)
