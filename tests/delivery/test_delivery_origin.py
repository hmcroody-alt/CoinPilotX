"""The stocked-origin tier: the one supplier call a product page is allowed.

Four failures this file exists to catch, none of which is visible in a green
build:

* A default ``"CN"`` reaching the freight request. §5 forbids it by name and
  ``fulfillment._stocked_origin`` warns about it in its own first line; the code
  path is one ``or`` away at all times.
* One CJ call per **variant** instead of per product. CJ's endpoint answers a pid,
  so a variant-keyed tier turns one ten-point call into one per variant while
  every test still passes.
* An unstable choice between two stocked warehouses. The origin travels into the
  route cache key, so an order-dependent pick does not merely vary the estimate —
  it splits one product's traffic over two keys.
* Our own parsing defects being counted as evidence against CJ, which would open
  the supplier circuit and withhold every *other* product's estimate too.
"""
from __future__ import annotations

import pytest

from services.delivery import breaker, cache, origin


NOW = 1_760_000_000.0
PID = "PID-SHIRT"
SHIRT = "VID-SHIRT"
BOOTS = "VID-BOOTS"


def warehouse(country, state=origin.IN_STOCK, **extra):
    row = {"country": country, "state": state, "total": 40, "cj": 40,
           "factory": 0, "verified": 2, "area_id": 1, "subwarehouses": []}
    row.update(extra)
    return row


def inventory(*variants):
    """A reply shaped like ``CJAdapter.get_inventory``'s."""
    return {"pid": PID, "variants": list(variants), "product_warehouses": [],
            "state": "UNKNOWN", "snapshot_at": "2026-09-27T00:00:00Z",
            "points_info": {}}


class Adapter:
    """A CJ adapter that records its calls and never reaches a network."""

    def __init__(self, answer=None, error=None):
        self.answer = answer if answer is not None else inventory(
            {"vid": SHIRT, "pid": PID, "warehouses": [warehouse("CN")]})
        self.error = error
        self.calls = []

    def get_inventory(self, pid, vid=None):
        self.calls.append((pid, vid))
        if self.error is not None:
            raise self.error
        return self.answer


class Store:
    def __init__(self, *, fail_get=False, fail_set=False):
        self.rows = {}
        self.gets = []
        self.sets = []
        self.fail_get = fail_get
        self.fail_set = fail_set

    def get(self, key):
        self.gets.append(key)
        if self.fail_get:
            raise RuntimeError("cache unreachable")
        return self.rows.get(key)

    def set(self, key, value, ttl_seconds):
        self.sets.append((key, value, ttl_seconds))
        if self.fail_set:
            raise RuntimeError("cache unreachable")
        self.rows[key] = value
        return True


@pytest.fixture(autouse=True)
def circuit():
    """A closed circuit per test; the breaker's state is process-global."""
    breaker.reset()
    yield
    breaker.reset()


def at(moment):
    return lambda: moment


def ask(pid=PID, vid=SHIRT, *, adapter=None, store=None, now=NOW):
    return origin.stocked_origin(pid, vid, adapter=adapter or Adapter(),
                                 store=store, clock=at(now))


# -- The happy path, and the shape of the thing that is cached ---------------

def test_a_stocked_warehouse_country_is_reported():
    assert ask() == "CN"


def test_the_country_is_normalized_rather_than_passed_through():
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID,
                                 "warehouses": [warehouse(" us ")]}))
    assert ask(adapter=adapter) == "US"


def test_one_supplier_call_answers_every_variant_of_the_product():
    """CJ's endpoint takes a pid and returns every variant. Asking per variant
    would multiply a ten-point call by the variant count for one answer."""
    adapter = Adapter(inventory(
        {"vid": SHIRT, "pid": PID, "warehouses": [warehouse("CN")]},
        {"vid": BOOTS, "pid": PID, "warehouses": [warehouse("US")]}))
    store = Store()
    assert ask(vid=SHIRT, adapter=adapter, store=store) == "CN"
    assert ask(vid=BOOTS, adapter=adapter, store=store) == "US"
    assert adapter.calls == [(PID, None)], "the second variant must be a cache hit"


def test_the_variant_is_never_passed_to_the_adapter():
    """Passing a vid makes the adapter raise VARIANT_PRODUCT_MISMATCH when CJ's
    reply does not mention it, turning "we cannot say" into a supplier error that
    counts against the circuit."""
    adapter = Adapter()
    ask(adapter=adapter)
    assert adapter.calls == [(PID, None)]


def test_the_cached_body_holds_no_stock_levels():
    """This tier is read by an unauthenticated product page. Where the stock is,
    is the question; how much of it a merchant holds is not."""
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID,
                                 "warehouses": [warehouse("CN", total=11830, cj=11830)]}))
    store = Store()
    ask(adapter=adapter, store=store)

    (_, envelope, _), = store.sets
    assert envelope["value"] == {SHIRT: ["CN"]}
    assert "11830" not in repr(envelope), repr(envelope)


def test_the_key_read_is_the_product_and_carries_no_buyer_or_destination():
    """The literal is pinned rather than compared against another call to
    ``origin_key``: a self-comparison moves with the code and cannot see a buyer
    component arrive. The key's own shape is defended in the cache suite; this
    pins that *this tier* reads exactly that key and builds it from the pid."""
    store = Store()
    ask(store=store)
    key, = store.gets
    assert key == f"delivery|{cache.VERSION}|{cache.SCOPE_ORIGIN}|CJ|PID-SHIRT"


def test_the_key_is_one_the_store_adapter_will_accept():
    from services.delivery import store as store_module
    assert cache.origin_key(supplier="cj", product_ref=PID).startswith(
        store_module.KEY_PREFIX)


# -- Nothing is invented when the answer is not there ------------------------

def test_a_variant_cj_does_not_mention_has_no_origin():
    adapter = Adapter(inventory({"vid": BOOTS, "pid": PID,
                                 "warehouses": [warehouse("CN")]}))
    assert ask(vid=SHIRT, adapter=adapter) is None


def test_a_product_with_no_stocked_warehouse_has_no_origin():
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID, "warehouses": [
        warehouse("CN", state="OUT_OF_STOCK"),
        warehouse("US", state="UNKNOWN")]}))
    assert ask(adapter=adapter) is None


def test_a_warehouse_nobody_counted_is_not_stock():
    """`_warehouse_stock` reports UNKNOWN for an absent count. Treating it as
    stock quotes freight from a warehouse that may hold nothing."""
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID,
                                 "warehouses": [warehouse("CN", state="UNKNOWN")]}))
    assert ask(adapter=adapter) is None


def test_an_unaudited_count_is_still_a_count():
    """verifiedWarehouse separates CJ-audited stock (1) from supplier-reported
    stock (2), and CJ sells both. Demanding audit made every real CJ warehouse
    ineligible once already — see cj._warehouse_stock."""
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID,
                                 "warehouses": [warehouse("CN", verified=2)]}))
    assert ask(adapter=adapter) == "CN"


@pytest.mark.parametrize("country", [None, "", "C", "CHN", 86, True, "  ", ["CN"]])
def test_a_warehouse_without_a_country_code_is_not_an_origin(country):
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID,
                                 "warehouses": [warehouse(country)]}))
    assert ask(adapter=adapter) is None


@pytest.mark.parametrize("variants", [[], [None], ["CN"], [{"pid": PID}],
                                      [{"vid": "", "warehouses": []}],
                                      [{"vid": 7, "warehouses": []}]])
def test_an_inventory_reply_naming_no_usable_variant_yields_no_origin(variants):
    assert ask(adapter=Adapter(inventory(*variants))) is None


@pytest.mark.parametrize("warehouses", [None, [], "CN", [None], [["CN"]]])
def test_an_unusable_warehouse_list_yields_no_origin(warehouses):
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID, "warehouses": warehouses}))
    assert ask(adapter=adapter) is None


@pytest.mark.parametrize("pid,vid", [(None, SHIRT), ("", SHIRT), ("  ", SHIRT),
                                     (PID, None), (PID, ""), (PID, "   "),
                                     (42, SHIRT), (PID, 42)])
def test_an_unusable_reference_costs_no_supplier_call(pid, vid):
    adapter = Adapter()
    assert origin.stocked_origin(pid, vid, adapter=adapter, clock=at(NOW)) is None
    assert adapter.calls == []


# -- A stable choice between two stocked warehouses --------------------------

def test_two_stocked_warehouses_resolve_the_same_way_whatever_the_order():
    """The origin travels into the route cache key. An order-dependent pick
    splits one product's traffic across two keys and halves the hit rate."""
    forward = Adapter(inventory({"vid": SHIRT, "pid": PID, "warehouses": [
        warehouse("US"), warehouse("CN")]}))
    reverse = Adapter(inventory({"vid": SHIRT, "pid": PID, "warehouses": [
        warehouse("CN"), warehouse("US")]}))
    assert ask(adapter=forward) == ask(adapter=reverse) == "CN"


def test_a_duplicated_warehouse_country_does_not_change_the_answer():
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID, "warehouses": [
        warehouse("us"), warehouse("US"), warehouse(" Us ")]}))
    assert ask(adapter=adapter) == "US"


def test_an_out_of_stock_warehouse_does_not_outrank_a_stocked_one():
    adapter = Adapter(inventory({"vid": SHIRT, "pid": PID, "warehouses": [
        warehouse("CN", state="OUT_OF_STOCK"), warehouse("US")]}))
    assert ask(adapter=adapter) == "US"


# -- The cache tiers --------------------------------------------------------

def test_a_second_ask_within_the_fresh_window_costs_no_supplier_call():
    adapter, store = Adapter(), Store()
    assert ask(adapter=adapter, store=store) == "CN"
    assert ask(adapter=adapter, store=store, now=NOW + 60) == "CN"
    assert len(adapter.calls) == 1


def test_an_entry_past_its_freshness_is_still_served_while_it_revalidates():
    adapter, store = Adapter(), Store()
    ask(adapter=adapter, store=store)
    stale = NOW + cache.ORIGIN_FRESH_SECONDS + 1
    assert ask(adapter=adapter, store=store, now=stale) == "CN"
    assert len(adapter.calls) == 1, "a stale origin is served, not re-fetched here"


def test_an_entry_past_its_stale_deadline_is_re_asked():
    adapter, store = Adapter(), Store()
    ask(adapter=adapter, store=store)
    dead = NOW + cache.ORIGIN_FRESH_SECONDS + cache.ORIGIN_STALE_SECONDS + 1
    assert ask(adapter=adapter, store=store, now=dead) == "CN"
    assert len(adapter.calls) == 2


def test_the_origin_tier_is_not_allowed_to_outlive_the_route_tier_resting_on_it():
    """The route tier's fifteen minutes is justified by "the thing that actually
    invalidates it is a warehouse change". A route quote cannot detect one faster
    than the origin it was keyed on is re-asked, so an origin tier permitted to
    run longer makes that justification decorative rather than true."""
    assert cache.ORIGIN_FRESH_SECONDS <= cache.ROUTE_FRESH_SECONDS
    assert cache.ORIGIN_STALE_SECONDS <= cache.ROUTE_STALE_SECONDS


def test_the_ttl_stored_covers_the_stale_window_the_body_advertises():
    store = Store()
    ask(store=store)
    (_, envelope, ttl), = store.sets
    assert ttl >= envelope["stale_until"] - envelope["stored_at"]


def test_a_product_with_no_stock_anywhere_is_cached_as_a_negative():
    """An empty variant list is CJ saying "nothing stocked". Re-asking it on every
    page render spends ten points to be told the same thing."""
    adapter, store = Adapter(inventory()), Store()
    assert ask(adapter=adapter, store=store) is None
    assert ask(adapter=adapter, store=store, now=NOW + 60) is None
    assert len(adapter.calls) == 1


def test_the_tier_works_with_no_store_at_all():
    adapter = Adapter()
    assert ask(adapter=adapter, store=None) == "CN"
    assert len(adapter.calls) == 1


# -- A cache fault costs a call, never the page ------------------------------

def test_an_unreadable_cache_costs_a_supplier_call_and_not_the_page():
    adapter, store = Adapter(), Store(fail_get=True)
    assert ask(adapter=adapter, store=store) == "CN"
    assert len(adapter.calls) == 1


def test_an_unwritable_cache_does_not_discard_the_origin_it_just_learned():
    adapter, store = Adapter(), Store(fail_set=True)
    assert ask(adapter=adapter, store=store) == "CN"


def test_a_foreign_body_in_the_cache_is_a_miss_rather_than_an_origin():
    adapter, store = Adapter(), Store()
    store.rows[cache.origin_key(supplier="cj", product_ref=PID)] = {"value": {SHIRT: ["ZZ"]}}
    assert ask(adapter=adapter, store=store) == "CN"
    assert len(adapter.calls) == 1


# -- The supplier failing, and whose circuit pays for it ---------------------

def test_a_supplier_error_yields_no_origin_rather_than_an_exception():
    adapter = Adapter(error=RuntimeError("CJ 503"))
    assert ask(adapter=adapter) is None


def test_a_malformed_body_is_counted_against_the_supplier():
    adapter = Adapter({"pid": PID, "variants": "CN"})
    assert ask(adapter=adapter) is None
    assert breaker.inspect_circuit(origin.CIRCUIT, now=NOW)["consecutive_failures"] == 1


def test_an_answer_that_is_not_a_body_is_counted_against_the_supplier():
    adapter = Adapter(["CN"])
    assert ask(adapter=adapter) is None
    assert breaker.inspect_circuit(origin.CIRCUIT, now=NOW)["consecutive_failures"] == 1


def test_an_empty_variant_list_is_not_counted_against_the_supplier():
    """CJ answered. "Nothing stocked" is an answer, and holding it against the
    provider would open a circuit over a catalogue gap."""
    assert ask(adapter=Adapter(inventory())) is None
    assert breaker.inspect_circuit(origin.CIRCUIT, now=NOW)["consecutive_failures"] == 0


def test_an_open_circuit_yields_no_origin_without_calling_the_supplier():
    adapter = Adapter()
    for _ in range(10):
        breaker.report_failure(origin.CIRCUIT, now=NOW)
    assert breaker.inspect_circuit(origin.CIRCUIT, now=NOW)["may_call"] is False
    assert ask(adapter=adapter) is None
    assert adapter.calls == []


def test_the_inventory_circuit_is_not_the_freight_circuit():
    """This runs inside quote.py's freight guard, and breaker.claim grants exactly
    one half-open probe per key. Sharing the name would mean the inner claim is
    denied inside the outer producer, where guard counts it as fresh evidence
    against the provider — so no probe could ever succeed and the circuit could
    never close. Inverted, an inner success calls succeeded(), which returns a
    fully closed circuit, declaring freight healthy on an inventory read."""
    assert origin.CIRCUIT != "delivery:cj"

    adapter = Adapter(error=RuntimeError("CJ 503"))
    ask(adapter=adapter)
    assert breaker.inspect_circuit(origin.CIRCUIT, now=NOW)["consecutive_failures"] == 1
    assert breaker.inspect_circuit("delivery:cj", now=NOW)["consecutive_failures"] == 0


def test_nesting_the_inventory_guard_inside_the_freight_guard_is_survivable():
    """The composition this module is actually used in: the freight guard is
    already open on the stack when the origin lookup runs."""
    adapter = Adapter()
    seen = {}

    def freight():
        seen["origin"] = ask(adapter=adapter)
        return "quoted"

    assert breaker.guard("delivery:cj", freight, clock=at(NOW)) == "quoted"
    assert seen["origin"] == "CN"


def test_a_projection_defect_is_not_blamed_on_the_supplier(monkeypatch):
    """A bug in our own parsing must not open CJ's circuit — that would withhold
    every other product's estimate on the strength of our mistake."""
    def boom(variants):
        raise AttributeError("a defect in the projection")

    monkeypatch.setattr(origin, "_origins", boom)
    with pytest.raises(AttributeError):
        ask(adapter=Adapter())
    assert breaker.inspect_circuit(origin.CIRCUIT, now=NOW)["consecutive_failures"] == 0


# -- The shape the rest of the domain consumes ------------------------------

def test_the_resolver_hands_variant_facts_a_two_argument_callable():
    lookup = origin.resolver(adapter=Adapter(), clock=at(NOW))
    assert lookup(PID, SHIRT) == "CN"


def test_the_resolver_takes_the_two_positional_arguments_variant_facts_passes():
    """``variant_facts.describe`` calls ``origin_lookup(pid, vid)`` positionally.
    A keyword-only signature here passes every test in this file — they all call it
    positionally too — and raises TypeError the first time a real product page asks.
    So the arity is pinned structurally rather than by one successful call."""
    import inspect

    lookup = origin.resolver(adapter=Adapter(), clock=at(NOW))
    parameters = list(inspect.signature(lookup).parameters.values())
    assert [p.name for p in parameters] == ["pid", "vid"]
    assert all(p.kind is p.POSITIONAL_OR_KEYWORD for p in parameters)
