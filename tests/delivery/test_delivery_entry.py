"""The whole stack, joined: a listing row in, a delivery promise out.

What this file is defending
---------------------------
Every part below this has its own suite and its own mutations, and all of them
pass. These are the failures that only exist in the seams, and each one produces a
plausible delivery date rather than an error:

* **A cache key that does not name the warehouse the quote was priced from.** The
  origin is resolved two layers down, inside ``variant_facts``; the key is built
  one layer up, inside ``quote``. Nothing fails if they are not connected — every
  estimate is still individually correct. But stock moving from CN to US keeps
  serving the CN quote while the right answer sits in the origin tier, and the
  entry is filed under a warehouse that had nothing to do with it.
* **A shipping mode the provider honours and the key ignores.** The provider reads
  it from the destination; the key takes it as its own argument. Set only in the
  first place, express and standard share one cache entry — and whichever was
  asked first answers for both.
* **A supplier connection opened on a page that needed no supplier.** Hydrating
  one spends a verification call and writes the merchant's connection status. On a
  warm product page, or a seller-shipped one, it must not happen at all.
* **Our own credential state counted against the supplier.** A connection that
  will not open is not CJ failing. Counted as such it opens CJ's circuit and stops
  quoting every other product in the catalogue.
* **One inventory call per variant instead of per product.** ``origin`` proves
  this in isolation against a stub; here it is proved through two real quotes for
  two real variants of one real listing, which is where the fan-out would actually
  reappear.

The database is a real temp SQLite file built from the production DDL, the cache
is a real store, and the breaker is the real one. Only CJ is faked, and it counts
what it was asked — because "was not called" is most of what this module promises,
and no assertion about a returned value can see it.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_DB = tempfile.NamedTemporaryFile(prefix="pulsesoc-delivery-entry-", suffix=".db", delete=False)
_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.name}"

from services import db, marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.delivery import (  # noqa: E402
    breaker, cache, entry, estimate, origin, quote, routing, variant_facts,
)
from services.delivery.providers import cj_logistics  # noqa: E402

LISTING = 7001
PID = "PID-SHIRT"
VID = "VID-SHIRT"
BOOTS_VID = "VID-BOOTS"
SNAPSHOT = "snap-entry"

NOW_TS = 1_800_000_000.0
NOW_DT = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)  # a Wednesday
HANDLING = {"min_days": 1, "max_days": 2, "basis": estimate.BASIS_BUSINESS}
BUFFER = 2

SHIRT = {"pid": PID, "vid": VID, "sku": "SKU-SHIRT", "weight_grams": 220,
         "price": "4.10", "currency": "USD"}
BOOTS = {"pid": PID, "vid": BOOTS_VID, "sku": "SKU-BOOTS", "weight_grams": 2050,
         "price": "31.00", "currency": "USD"}
PAYLOAD = {"pid": PID, "logistics_properties": ["ORDINARY"],
           "variants": [BOOTS, SHIRT], "supplier_wholesale_note": "18.40"}


# ---------------------------------------------------------------------------
# A CJ that answers both questions and remembers being asked
# ---------------------------------------------------------------------------

def warehouse(country, state=origin.IN_STOCK):
    return {"country": country, "state": state, "quantity": 118}


def route_option(option_id, *, total="8.50", low=6, high=12, available=True):
    return {
        "option_id": option_id, "channel_id": f"ch-{option_id}",
        "service": f"svc-{option_id}", "provider_total": total, "currency": "USD",
        "available": available, "estimated_transit": f"{low}-{high}",
        "transit": {"min_days": low, "max_days": high, "basis": estimate.BASIS_CALENDAR},
    }


class Adapter:
    """CJ, as far as this domain can tell. Records every question."""

    def __init__(self, *, stock=("CN",), options=None, inventory_error=None,
                 freight_error=None):
        self._stock = stock
        self._options = [route_option("std")] if options is None else options
        self._inventory_error = inventory_error
        self._freight_error = freight_error
        self.inventory_calls = []
        self.freight_calls = []

    def get_inventory(self, pid, vid=None):
        self.inventory_calls.append((pid, vid))
        if self._inventory_error is not None:
            raise self._inventory_error
        houses = [warehouse(c) for c in self._stock]
        return {"variants": [{"vid": VID, "warehouses": houses},
                             {"vid": BOOTS_VID, "warehouses": houses}]}

    def estimate_shipping(self, request):
        self.freight_calls.append(request)
        if self._freight_error is not None:
            raise self._freight_error
        return {"quotes": self._options, "state": "QUOTED"}


class Source:
    """The adapter source, counting how many times a connection was opened."""

    def __init__(self, adapter=None, *, error=None):
        self.adapter = adapter if adapter is not None else Adapter()
        self._error = error
        self.builds = 0

    def __call__(self):
        self.builds += 1
        if self._error is not None:
            raise self._error
        return self.adapter


class Store:
    def __init__(self):
        self.data = {}
        self.writes = []

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ttl_seconds):
        self.data[key] = value
        self.writes.append({"key": key, "ttl": ttl_seconds})


class Connects:
    """``db.connect``, counting. A read that must not touch the database is a
    promise, and only a counter can hold it."""

    def __init__(self):
        self.opened = 0

    def __call__(self):
        self.opened += 1
        return db.connect()


def boom_connect():
    raise AssertionError("the database must not be read for this quote")


def boom_source():
    raise AssertionError("a supplier connection must not be opened for this quote")


# ---------------------------------------------------------------------------
# Real schema, real rows
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def schema():
    conn = db.connect()
    try:
        conn.execute(f"DROP TABLE IF EXISTS {supplier_schema.SOURCE_TABLE}")
        conn.execute(f"DROP TABLE IF EXISTS {supplier_schema.VARIANT_TABLE}")
        conn.execute("DROP TABLE IF EXISTS supplier_snapshots")
        conn.execute(supplier_schema.SOURCE_TABLE_DDL)
        conn.execute(supplier_schema.VARIANT_TABLE_DDL)
        conn.execute("CREATE TABLE supplier_snapshots (snapshot_id TEXT, kind TEXT, "
                     "payload_json TEXT)")
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture(autouse=True)
def circuit():
    breaker.reset()
    yield
    breaker.reset()


def seed(*, provider="cj", payload=None, variants=()):
    conn = db.connect()
    try:
        conn.execute(
            f"INSERT INTO {supplier_schema.SOURCE_TABLE} "
            f"(listing_id, seller_user_id, provider, provider_product_id, "
            f" provider_variant_id, external_sku, source_snapshot_id) "
            f"VALUES (?,?,?,?,?,?,?)",
            (LISTING, 1, provider, PID, VID, None, SNAPSHOT))
        conn.execute("INSERT INTO supplier_snapshots (snapshot_id, kind, payload_json) "
                     "VALUES (?,?,?)",
                     (SNAPSHOT, "product",
                      json.dumps(PAYLOAD if payload is None else payload)))
        for variant_key, vid, sku in variants:
            conn.execute(
                f"INSERT INTO {supplier_schema.VARIANT_TABLE} "
                f"(listing_id, seller_user_id, variant_key, provider_variant_id, sku) "
                f"VALUES (?,?,?,?,?)",
                (LISTING, 1, variant_key, vid, sku))
        conn.commit()
    finally:
        conn.close()


def ask(**overrides):
    kwargs = dict(
        variant_ref=str(LISTING),
        fulfillment=quote.FULFILLMENT_SUPPLIER,
        destination={"country": "US", "postal": "90210"},
        now=NOW_DT, handling=HANDLING, buffer_days=BUFFER,
        adapter_source=Source(), quantity=1, clock=lambda: NOW_TS,
    )
    kwargs.update(overrides)
    return entry.delivery_for_variant(**kwargs)


def route_key(*, origin_code="CN", quantity=1, postal="90210", shipping_mode=None):
    return cache.route_key(
        supplier=entry.SUPPLIER_NAME, variant_ref=str(LISTING), destination="US",
        postal=postal, quantity=quantity, origin=origin_code,
        shipping_mode=shipping_mode, warehouses=None)


# ---------------------------------------------------------------------------
# The happy path, end to end
# ---------------------------------------------------------------------------

def test_a_bound_listing_becomes_a_dated_window_and_a_bookable_route():
    seed()
    source = Source()
    got = ask(adapter_source=source)

    assert got["buyer"]["state"] == estimate.STATE_ESTIMATED
    assert got["buyer"]["earliest"] and got["buyer"]["latest"]
    assert got["buyer"]["shipping_price"] == quote.SHIPPING_FREE
    assert got["buyer"]["guaranteed"] is False
    assert got["internal"]["route"]["option_id"] == "std"
    assert got["internal"]["source"] == "PROVIDER"
    assert source.adapter.inventory_calls == [(PID, None)]
    assert len(source.adapter.freight_calls) == 1


def test_the_buyer_half_carries_no_supplier_economics():
    """The freight is in the internal half and nowhere else. Asserted on the key
    set rather than on the absence of one name, because the field that leaks will
    be one nobody thought to check for."""
    seed()
    got = ask()
    assert set(got["buyer"]) == quote.BUYER_FIELDS
    assert "8.50" not in repr(got["buyer"])
    assert got["internal"]["route"]["provider_total"] == "8.50"


def test_the_freight_request_describes_the_variant_that_was_asked_for():
    seed(variants=[("boots", BOOTS_VID, "SKU-BOOTS")])
    source = Source()
    ask(variant_ref=f"{LISTING}:boots", adapter_source=source)
    line = source.adapter.freight_calls[0]["reqDTOS"][0]
    assert line["weight"] == 2050.0
    assert line["freightTrialSkuList"][0]["vid"] == BOOTS_VID
    assert line["srcAreaCode"] == "CN"
    assert line["destAreaCode"] == "US"


# ---------------------------------------------------------------------------
# The warehouse reaches the key. This is the join the module exists for.
# ---------------------------------------------------------------------------

def test_the_route_entry_is_filed_under_the_warehouse_it_was_quoted_from():
    """Pinned against both keys, not just the right one. Asserting only that the
    written key equals the CN key would still pass if ``route_key`` had stopped
    reading ``origin`` at all, because then every key would be that key."""
    seed()
    store = Store()
    ask(store=store)

    written = [w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]]
    assert written == [route_key(origin_code="CN")]
    assert route_key(origin_code="CN") != route_key(origin_code=None)


def test_the_warehouse_appears_in_the_key_by_name():
    seed()
    store = Store()
    ask(store=store)
    key = [w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]][0]
    assert "CN" in key.split("|")


def test_stock_moving_to_another_country_does_not_reuse_the_old_quote():
    """The failure this defends is silent: with the origin missing from the key,
    both quotes land on one entry and the first one answers for the second."""
    seed()
    store = Store()
    ask(adapter_source=Source(Adapter(stock=("CN",))), store=store)
    from_cn = [w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]]

    store.data.clear()
    store.writes.clear()
    ask(adapter_source=Source(Adapter(stock=("US",))), store=store)
    from_us = [w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]]

    assert from_cn == [route_key(origin_code="CN")]
    assert from_us == [route_key(origin_code="US")]
    assert from_cn != from_us


def test_the_origin_the_key_names_is_the_origin_the_supplier_was_quoted_from():
    """One resolution, used twice. If the facts were resolved a second time inside
    the provider, an origin tier expiring in between would key the entry on one
    warehouse and price it from another."""
    seed()
    store = Store()
    source = Source(Adapter(stock=("US", "CN")))
    ask(adapter_source=source, store=store)

    quoted_from = source.adapter.freight_calls[0]["reqDTOS"][0]["srcAreaCode"]
    key = [w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]][0]
    assert quoted_from in key.split("|")
    assert key == route_key(origin_code=quoted_from)


# ---------------------------------------------------------------------------
# The shipping mode reaches the key too
# ---------------------------------------------------------------------------

def test_a_shipping_mode_set_on_the_destination_reaches_the_cache_key():
    seed()
    store = Store()
    ask(destination={"country": "US", "postal": "90210", "shipping_mode": "EXPRESS"},
        store=store)
    key = [w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]][0]
    assert key == route_key(shipping_mode="EXPRESS")
    assert key != route_key(shipping_mode=None)


def test_two_shipping_modes_do_not_share_one_entry():
    seed()
    store = Store()
    ask(destination={"country": "US", "postal": "90210", "shipping_mode": "EXPRESS"},
        store=store)
    ask(destination={"country": "US", "postal": "90210", "shipping_mode": "STANDARD"},
        store=store)
    routes = {w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]}
    assert len(routes) == 2


def test_the_mode_the_supplier_is_asked_for_is_the_mode_in_the_key():
    seed()
    store = Store()
    source = Source()
    ask(destination={"country": "US", "shipping_mode": "EXPRESS"},
        adapter_source=source, store=store)
    assert source.adapter.freight_calls[0]["reqDTOS"][0]["shippingMode"] == "EXPRESS"
    key = [w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]][0]
    assert "EXPRESS" in key


def test_an_explicit_shipping_mode_argument_is_what_gets_keyed():
    seed()
    store = Store()
    ask(shipping_mode="ECONOMY", store=store)
    key = [w["key"] for w in store.writes if cache.SCOPE_ROUTE in w["key"]][0]
    assert key == route_key(shipping_mode="ECONOMY")


# ---------------------------------------------------------------------------
# What a page that needs no supplier costs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fulfillment", [f for f in quote.FULFILLMENT_TYPES
                                         if f != quote.FULFILLMENT_SUPPLIER])
def test_a_product_this_supplier_does_not_ship_costs_no_read_at_all(fulfillment):
    """Driven off ``quote.FULFILLMENT_TYPES`` so a fulfillment kind added there is
    covered here without this test being edited."""
    seed()
    got = entry.delivery_for_variant(
        variant_ref=str(LISTING), fulfillment=fulfillment,
        destination={"country": "US"}, now=NOW_DT, handling=HANDLING,
        buffer_days=BUFFER, adapter_source=boom_source, connect=boom_connect,
        clock=lambda: NOW_TS)
    assert got["buyer"]["reason"] == quote.REASON_NOT_SUPPLIER_FULFILLED
    assert got["internal"]["source"] == "REFUSED"


@pytest.mark.parametrize("fulfillment", [None, "", "   ", 7, "supplier-ish"])
def test_an_undeclared_fulfillment_costs_no_read_either(fulfillment):
    seed()
    got = ask(fulfillment=fulfillment, adapter_source=boom_source,
              connect=boom_connect)
    assert got["buyer"]["state"] == estimate.STATE_UNAVAILABLE
    assert got["internal"]["source"] == "REFUSED"


@pytest.mark.parametrize("destination", [None, {}, {"country": ""},
                                         {"country": "  "}, {"country": 44},
                                         "US", []])
def test_a_visitor_with_no_known_country_costs_no_read(destination):
    seed()
    got = ask(destination=destination, adapter_source=boom_source,
              connect=boom_connect)
    assert got["buyer"]["reason"] == quote.REASON_NO_DESTINATION
    assert got["internal"]["source"] == "REFUSED"


def test_the_cheap_gate_refuses_nothing_the_canonical_gate_would_have_quoted():
    """The gate above the reads is an economy, not a second contract. It is allowed
    to be stricter than ``quote_delivery`` — it is not allowed to be looser, and it
    is not allowed to refuse something that would have been answered."""
    seed()
    assert entry._worth_asking(quote.FULFILLMENT_SUPPLIER, {"country": "US"}) is True
    assert entry._worth_asking("supplier", {"country": "us"}) is True
    assert entry._worth_asking(" SUPPLIER ", {"country": "US"}) is True


def test_a_warm_product_page_opens_no_supplier_connection():
    """Both tiers warm: nothing is asked of CJ, so nothing is hydrated. This is the
    case that makes deferring the adapter worth doing — it is the common one."""
    seed()
    store = Store()
    ask(store=store)

    source = Source()
    got = ask(adapter_source=source, store=store)
    assert got["internal"]["source"] == "CACHE_FRESH"
    assert source.builds == 0
    assert source.adapter.inventory_calls == []
    assert source.adapter.freight_calls == []


def test_a_warm_page_still_produces_a_promise_and_recomputes_its_dates():
    seed()
    store = Store()
    first = ask(store=store)
    second = ask(store=store, adapter_source=Source())
    assert second["buyer"]["earliest"] == first["buyer"]["earliest"]
    assert second["buyer"]["state"] == estimate.STATE_ESTIMATED


def test_one_connection_answers_both_questions():
    seed()
    source = Source()
    ask(adapter_source=source)
    assert source.builds == 1
    assert len(source.adapter.inventory_calls) == 1
    assert len(source.adapter.freight_calls) == 1


def test_two_variants_of_one_product_cost_one_inventory_call():
    """§18, proved where it would actually come back: two real quotes for two real
    variants of one real listing. ``origin``'s own suite proves the tier is keyed on
    the product; this proves nothing above it re-keys on the variant."""
    seed(variants=[("boots", BOOTS_VID, "SKU-BOOTS")])
    store = Store()
    source = Source()
    ask(variant_ref=str(LISTING), adapter_source=source, store=store)
    ask(variant_ref=f"{LISTING}:boots", adapter_source=source, store=store)

    assert source.adapter.inventory_calls == [(PID, None)]
    assert len(source.adapter.freight_calls) == 2


def test_an_expired_origin_tier_costs_an_inventory_call_and_not_a_freight_call():
    """The tiers are the same length, so this is the boundary rather than the norm:
    the origin is re-asked, resolves to the same warehouse, and the route key it
    rebuilds is the one already stored."""
    seed()
    store = Store()
    ask(store=store)
    for key in [k for k in store.data if cache.SCOPE_ORIGIN in k]:
        del store.data[key]

    source = Source()
    got = ask(adapter_source=source, store=store)
    assert got["internal"]["source"] == "CACHE_FRESH"
    assert len(source.adapter.inventory_calls) == 1
    assert source.adapter.freight_calls == []


# ---------------------------------------------------------------------------
# A connection that will not open is not the supplier failing
# ---------------------------------------------------------------------------

def test_a_connection_that_will_not_open_is_reported_as_itself():
    seed()
    got = ask(adapter_source=Source(error=RuntimeError("vault unreachable")))
    assert got["buyer"]["reason"] == quote.REASON_VARIANT_INCOMPLETE
    assert got["internal"]["detail"] == entry.REASON_CONNECTION_UNAVAILABLE


def test_a_source_that_produces_no_adapter_is_reported_the_same_way():
    seed()
    got = ask(adapter_source=lambda: None)
    assert got["internal"]["detail"] == entry.REASON_CONNECTION_UNAVAILABLE


def test_a_connection_failure_is_not_counted_against_the_supplier():
    """A handful of listings on a broken connection must not open CJ's circuit and
    stop quoting the thousands on working ones."""
    seed()
    for _ in range(6):
        ask(adapter_source=Source(error=RuntimeError("vault unreachable")))
    for circuit_name in (f"delivery:{entry.SUPPLIER_NAME}", origin.CIRCUIT):
        record = breaker.inspect_circuit(circuit_name, now=NOW_TS)
        assert record["consecutive_failures"] == 0
        assert record["state"] == breaker.STATE_CLOSED
        assert record["may_call"] is True


def test_the_underlying_exception_text_does_not_travel_into_the_refusal():
    """The source is a credential path. Its exception text is not a message this
    domain may relay outward, so the reason is fixed rather than derived."""
    seed()
    got = ask(adapter_source=Source(error=RuntimeError("cj api key sk-live-9911")))
    assert "sk-live-9911" not in repr(got)


def test_a_connection_is_opened_once_even_though_it_fails():
    seed()
    source = Source(error=RuntimeError("vault unreachable"))
    ask(adapter_source=source)
    assert source.builds == 1


# ---------------------------------------------------------------------------
# The other ways of not knowing, kept apart from each other
# ---------------------------------------------------------------------------

def test_a_product_stocked_nowhere_is_refused_without_a_freight_call():
    seed()
    source = Source(Adapter(stock=()))
    got = ask(adapter_source=source)
    assert got["buyer"]["reason"] == quote.REASON_VARIANT_INCOMPLETE
    assert got["internal"]["detail"] == entry.REASON_ORIGIN_UNKNOWN
    assert source.adapter.freight_calls == []


def test_a_listing_bound_to_another_supplier_is_not_quoted_from_cj():
    seed(provider="other")
    source = Source()
    got = ask(adapter_source=source)
    assert got["buyer"]["reason"] == quote.REASON_VARIANT_INCOMPLETE
    assert source.adapter.freight_calls == []


def test_a_listing_with_no_weight_on_file_is_not_blamed_on_the_warehouse():
    """A local gap must not be reported as a stock gap. It stays the provider's
    vaguer ``variant_unknown``, because ``variant_facts`` withholds which column is
    absent on purpose and guessing one would send the repair to the wrong place."""
    seed(payload={"pid": PID, "logistics_properties": ["ORDINARY"],
                  "variants": [{"pid": PID, "vid": VID, "sku": "SKU-SHIRT"}]})
    got = ask()
    assert got["buyer"]["reason"] == quote.REASON_VARIANT_INCOMPLETE
    assert got["internal"]["detail"] == cj_logistics.REASON_UNKNOWN_VARIANT
    assert got["internal"]["detail"] != entry.REASON_ORIGIN_UNKNOWN


def test_an_unreachable_supplier_is_not_an_unserviceable_route():
    seed()
    got = ask(adapter_source=Source(Adapter(freight_error=RuntimeError("502"))))
    assert got["buyer"]["reason"] == quote.REASON_PROVIDER_FAILED


def test_a_supplier_that_ships_nowhere_from_there_blocks_rather_than_estimates():
    seed()
    got = ask(adapter_source=Source(Adapter(options=[])))
    assert got["buyer"]["reason"] == estimate.REASON_UNSUPPORTED
    assert got["buyer"]["state"] == estimate.STATE_UNSUPPORTED_ROUTE


def test_an_inventory_outage_withholds_the_estimate_rather_than_inventing_one():
    seed()
    source = Source(Adapter(inventory_error=RuntimeError("500")))
    got = ask(adapter_source=source)
    assert got["buyer"]["state"] == estimate.STATE_UNAVAILABLE
    assert source.adapter.freight_calls == []


@pytest.mark.parametrize("ref", [None, "", "  ", "abc", "0", "-3", ":boots", 7])
def test_a_reference_that_names_no_listing_is_a_bug_and_not_a_missing_weight(ref):
    seed()
    with pytest.raises(variant_facts.VariantRefInvalid):
        ask(variant_ref=ref)


# ---------------------------------------------------------------------------
# The memo, which must not answer for a variant it was not resolved for
# ---------------------------------------------------------------------------

def test_the_resolved_facts_are_handed_to_the_provider_without_a_second_lookup():
    seed()
    connects = Connects()
    source = Source()
    ask(adapter_source=source, connect=connects)
    assert connects.opened == 1
    assert len(source.adapter.inventory_calls) == 1


def test_the_memo_does_not_answer_for_a_reference_it_was_not_resolved_for():
    """Returning the memo for whatever is asked would quote one variant's parcel
    for another — the precise failure ``variant_facts`` returns ``None`` rather
    than a default to avoid."""
    seed(variants=[("boots", BOOTS_VID, "SKU-BOOTS")])
    facts = {"vid": VID, "sku": "SKU-SHIRT", "origin": "CN",
             "weight_grams": 220, "properties": ["ORDINARY"]}
    memo = entry._memo(str(LISTING), facts, connect=db.connect,
                       witness=entry._Witness(lambda pid, vid: "CN",
                                              entry._Deferred(Source())))

    assert memo(str(LISTING)) is facts
    other = memo(f"{LISTING}:boots")
    assert other is not facts
    assert other["vid"] == BOOTS_VID
    assert other["weight_grams"] == 2050


def test_absent_facts_alone_are_not_blamed_on_the_connection():
    """Both halves of the split matter. A variant that is genuinely incomplete on a
    working connection must keep saying so, or every catalogue gap starts looking
    like a credential problem."""
    seed()
    witness = entry._Witness(lambda pid, vid: None, entry._Deferred(Source()))
    memo = entry._memo(str(LISTING), None, witness=witness, connect=db.connect)
    # Nothing has asked the witness anything, so it has nothing to attribute.
    assert witness.reason is None
    assert memo(str(LISTING)) is None


def test_the_memo_reports_a_broken_connection_rather_than_an_unknown_origin():
    seed()
    deferred = entry._Deferred(Source(error=RuntimeError("vault unreachable")))
    with pytest.raises(breaker.NotProviderEvidence) as raised:
        deferred.get_inventory(PID)
    assert raised.value.reason == entry.REASON_CONNECTION_UNAVAILABLE

    witness = entry._Witness(lambda pid, vid: None, deferred)
    memo = entry._memo(str(LISTING), None, witness=witness, connect=db.connect)
    assert witness.reason == entry.REASON_CONNECTION_UNAVAILABLE
    with pytest.raises(breaker.NotProviderEvidence) as refused:
        memo(str(LISTING))
    assert refused.value.reason == entry.REASON_CONNECTION_UNAVAILABLE


# ---------------------------------------------------------------------------
# The witness: which of three repairs an absent fact calls for
# ---------------------------------------------------------------------------

def test_a_witness_that_was_never_asked_attributes_nothing():
    witness = entry._Witness(lambda pid, vid: "CN", entry._Deferred(Source()))
    assert witness.reason is None


def test_a_lookup_that_answered_attributes_nothing():
    witness = entry._Witness(lambda pid, vid: "CN", entry._Deferred(Source()))
    assert witness(PID, VID) == "CN"
    assert witness.reason is None


@pytest.mark.parametrize("answer", [None, "", "   ", 0, False, []])
def test_a_lookup_that_produced_nothing_is_a_stock_question(answer):
    """An empty country is not an origin. Reading it as answered would attribute a
    stock gap to the catalogue, and the repair would go looking for a column."""
    witness = entry._Witness(lambda pid, vid: answer, entry._Deferred(Source()))
    assert witness(PID, VID) == answer
    assert witness.reason == entry.REASON_ORIGIN_UNKNOWN


def test_a_broken_connection_outranks_the_stock_reading_it_causes():
    """A connection that will not open also makes the lookup answer nothing, so both
    conditions hold at once and only one of them is the repair."""
    deferred = entry._Deferred(Source(error=RuntimeError("vault unreachable")))
    witness = entry._Witness(lambda pid, vid: deferred.get_inventory(pid), deferred)
    with pytest.raises(breaker.NotProviderEvidence):
        witness(PID, VID)
    assert witness.reason == entry.REASON_CONNECTION_UNAVAILABLE


def test_the_witness_forwards_the_product_and_the_variant_unchanged():
    seen = []
    witness = entry._Witness(lambda pid, vid: seen.append((pid, vid)) or "CN",
                             entry._Deferred(Source()))
    witness(PID, VID)
    assert seen == [(PID, VID)]


def test_the_stock_reason_is_the_providers_own_spelling_of_it():
    """Two names for one condition divide it between two counters in whatever
    eventually measures why estimates are missing."""
    assert entry.REASON_ORIGIN_UNKNOWN == cj_logistics.REASON_NO_ORIGIN
    assert entry.REASON_ORIGIN_UNKNOWN != entry.REASON_CONNECTION_UNAVAILABLE


# ---------------------------------------------------------------------------
# The deferred adapter, on its own
# ---------------------------------------------------------------------------

def test_a_deferred_adapter_builds_nothing_until_it_is_asked_something():
    source = Source()
    deferred = entry._Deferred(source)
    assert source.builds == 0
    deferred.get_inventory(PID)
    deferred.estimate_shipping({"reqDTOS": []})
    assert source.builds == 1


def test_a_deferred_adapter_forwards_what_it_was_given():
    source = Source()
    deferred = entry._Deferred(source)
    deferred.get_inventory(PID, None)
    deferred.estimate_shipping({"reqDTOS": ["line"]})
    assert source.adapter.inventory_calls == [(PID, None)]
    assert source.adapter.freight_calls == [{"reqDTOS": ["line"]}]


@pytest.mark.parametrize("source", [None, "adapter", 7, {}])
def test_an_adapter_source_that_is_not_callable_is_a_programming_error(source):
    with pytest.raises(ValueError):
        entry._Deferred(source)


def test_a_failed_build_marks_itself_unavailable():
    deferred = entry._Deferred(Source(error=RuntimeError("vault unreachable")))
    assert deferred.unavailable is False
    with pytest.raises(breaker.NotProviderEvidence):
        deferred.get_inventory(PID)
    assert deferred.unavailable is True


# ---------------------------------------------------------------------------
# What the entry point is, structurally
# ---------------------------------------------------------------------------

def test_the_entry_point_takes_no_actor_and_no_buyer_identity():
    """A delivery estimate on a public page is the platform answering about its own
    listing. An actor argument here would be an invitation to authorize a buyer
    against a merchant's supplier connection, which is what
    ``connections.adapter_for`` is for and what this path must never become."""
    import inspect
    names = set(inspect.signature(entry.delivery_for_variant).parameters)
    for forbidden in ("actor_user_id", "user_id", "actor", "business_id",
                      "store_id", "connection_id", "viewer"):
        assert forbidden not in names


def test_every_argument_is_keyword_only():
    """Positionally, ``variant_ref`` and ``fulfillment`` are interchangeable strings
    and a transposition would quote a listing called ``SUPPLIER``."""
    import inspect
    kinds = {p.kind for p in inspect.signature(entry.delivery_for_variant).parameters.values()}
    assert kinds == {inspect.Parameter.KEYWORD_ONLY}


def test_handling_and_buffer_have_no_defaults():
    """§12: a buffer nobody chose is a buffer of zero presented as a decision."""
    import inspect
    params = inspect.signature(entry.delivery_for_variant).parameters
    for required in ("handling", "buffer_days", "now", "adapter_source"):
        assert params[required].default is inspect.Parameter.empty


def test_the_policy_default_is_the_domains_policy_default():
    import inspect
    params = inspect.signature(entry.delivery_for_variant).parameters
    assert params["policy"].default == routing.POLICY_CHEAPEST_ACCEPTABLE
    assert params["unspecified_basis"].default == estimate.BASIS_BUSINESS
