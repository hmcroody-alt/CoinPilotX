"""The composed promise: one answer, recomputed every read, with the cost kept in.

What this file is defending
---------------------------
Every part of the delivery stack is separately tested and separately correct. The
failures below are produced by the joins between them, and none of them is
visible in a part's own suite:

* **A cached window instead of cached evidence.** Dates stored and re-served are
  early by however long they sat, and eventually point at the past. The parts are
  innocent — the estimator computed correctly, the cache stored faithfully.
* **A supplier's freight cost reaching the buyer half.** One object carrying both
  the customer promise and the wholesale economics is one `jsonify` from
  publishing them.
* **"We could not reach the supplier" served as "the supplier does not ship
  there."** The first is ours and transient; the second blocks a checkout. Both
  render as an absent estimate, so the page looks the same either way.
* **A non-supplier product getting a supplier estimate**, or worse, costing a
  supplier call — forty seller-shipped cards, forty calls.

The provider here is a stub that counts its calls, because "does not call" is half
of what this module promises and an assertion about a returned value cannot see
it.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.delivery import breaker, cache, estimate, quote, routing

NOW_TS = 1_800_000_000.0
NOW_DT = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)  # a Wednesday

HANDLING = {"min_days": 1, "max_days": 2, "basis": estimate.BASIS_BUSINESS}
BUFFER = 2


class Provider:
    """A supplier that records how often it was actually asked."""

    name = "stub"

    def __init__(self, options=None, error=None):
        self._options = options if options is not None else [route_option("std")]
        self._error = error
        self.calls = []

    def quote_routes(self, *, variant_ref, destination, quantity):
        self.calls.append({"variant_ref": variant_ref, "destination": destination,
                           "quantity": quantity})
        if self._error is not None:
            raise self._error
        return self._options


class Store:
    def __init__(self):
        self.data = {}
        self.writes = []

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ttl_seconds):
        self.data[key] = value
        self.writes.append({"key": key, "ttl": ttl_seconds})


def route_option(option_id, *, total="8.50", low=6, high=12, available=True):
    return {
        "option_id": option_id, "channel_id": f"ch-{option_id}", "service": f"svc-{option_id}",
        "provider_total": total, "currency": "USD", "available": available,
        "estimated_transit": f"{low}-{high}",
        "transit": {"min_days": low, "max_days": high, "basis": estimate.BASIS_CALENDAR},
    }


def ask(**overrides):
    kwargs = dict(
        fulfillment=quote.FULFILLMENT_SUPPLIER,
        destination={"country": "US", "postal": "90210"},
        now=NOW_DT, handling=HANDLING, buffer_days=BUFFER,
        variant_ref="VID-1", quantity=1, clock=lambda: NOW_TS,
    )
    kwargs.update(overrides)
    return quote.quote_delivery(**kwargs)


@pytest.fixture(autouse=True)
def clean():
    breaker.reset()
    yield
    breaker.reset()


# ---------------------------------------------------------------------------
# Only a supplier-fulfilled product, and decided before anything is spent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fulfillment,reason", [
    (quote.FULFILLMENT_SELLER, quote.REASON_NOT_SUPPLIER_FULFILLED),
    (quote.FULFILLMENT_DIGITAL, quote.REASON_NOT_SUPPLIER_FULFILLED),
    ("something-new", quote.REASON_NOT_SUPPLIER_FULFILLED),
    (None, quote.REASON_FULFILLMENT_UNDECLARED),
    ("", quote.REASON_FULFILLMENT_UNDECLARED),
])
def test_only_a_supplier_fulfilled_product_gets_a_supplier_estimate(fulfillment, reason):
    """§47: an undeclared fulfillment produces no estimate, not a supplier's."""
    provider = Provider()
    result = ask(fulfillment=fulfillment, provider=provider)
    assert result["buyer"]["state"] == estimate.STATE_UNAVAILABLE
    assert result["buyer"]["reason"] == reason
    assert provider.calls == [], "a non-supplier product cost a supplier call"


def test_a_seller_shipped_product_is_refused_before_a_cache_key_exists():
    """§18's real shape: a feed of forty seller-shipped cards must not warm, read
    or write the supplier's cache, let alone call it."""
    store = Store()
    ask(fulfillment=quote.FULFILLMENT_SELLER, provider=Provider(), store=store)
    assert store.data == {} and store.writes == []


def test_an_unresolved_destination_produces_no_estimate_and_no_call():
    """§14: no destination is a reason. Quoting a house default would put a
    plausible date in front of a buyer it does not apply to."""
    provider = Provider()
    for destination in (None, {}, {"country": ""}, {"country": None}, {"postal": "90210"}):
        result = ask(destination=destination, provider=provider)
        assert result["buyer"]["reason"] == quote.REASON_NO_DESTINATION
    assert provider.calls == []


def test_a_supplier_quote_without_a_variant_reference_is_a_programming_error():
    """§7: estimating against the wrong variant is worse than not estimating, and
    a caller that forgot the reference must find out loudly rather than get a
    product-level guess."""
    for missing in (None, "", "   "):
        with pytest.raises(quote.QuoteRejected):
            ask(variant_ref=missing, provider=Provider())


# ---------------------------------------------------------------------------
# The promise itself
# ---------------------------------------------------------------------------

def test_a_quoted_route_becomes_a_dated_window_that_is_never_a_guarantee():
    result = ask(provider=Provider())
    buyer = result["buyer"]
    assert buyer["state"] == estimate.STATE_ESTIMATED
    assert buyer["earliest"] < buyer["latest"]
    assert buyer["guaranteed"] is False
    assert buyer["is_estimate"] is True
    assert buyer["confidence"] == estimate.CONFIDENCE_PROVIDER_QUOTED


def test_shipping_is_free_to_the_buyer_and_not_free_to_the_platform():
    """§3 and §33 at the same time. These are different facts, and a result that
    cannot hold both forces one of them to be dropped."""
    result = ask(provider=Provider(options=[route_option("std", total="11.40")]))
    # Asserted against the literal rather than against quote.SHIPPING_FREE: a test
    # that compares the constant to itself passes whatever the constant becomes,
    # including 0 — which a surface will eventually render as "$0.00" in a total
    # column beside a real price.
    assert result["buyer"]["shipping_price"] == "FREE"
    assert isinstance(result["buyer"]["shipping_price"], str)
    assert result["internal"]["freight_total"] == "11.40"
    assert result["internal"]["freight_currency"] == "USD"


def test_the_buyer_half_carries_no_cost_field_at_all():
    """§35. Truncating or rounding the cost is not the defence — not carrying it
    is. The key set is pinned so a field added in good faith goes red here rather
    than shipping wholesale economics to an unauthenticated surface."""
    result = ask(provider=Provider())
    assert set(result["buyer"]) == quote.BUYER_FIELDS
    rendered = repr(result["buyer"])
    for leak in ("8.50", "provider_total", "freight", "wholesale", "USD"):
        assert leak not in rendered


def test_the_result_names_the_route_fulfillment_must_book():
    """§84: quoting one service and shipping another. The promise and the booking
    are only tied together if the answer carries the identifiers."""
    result = ask(provider=Provider(options=[route_option("express")]))
    assert result["internal"]["route"]["option_id"] == "express"
    assert result["internal"]["route"]["channel_id"] == "ch-express"


def test_the_supplier_variant_and_quantity_are_what_is_actually_asked_about():
    provider = Provider()
    ask(provider=provider, variant_ref="VID-77", quantity=3)
    assert provider.calls[0]["variant_ref"] == "VID-77"
    assert provider.calls[0]["quantity"] == 3


# ---------------------------------------------------------------------------
# Evidence is cached; dates are not
# ---------------------------------------------------------------------------

def test_the_dates_are_recomputed_on_a_cache_hit_rather_than_replayed():
    """The join failure that no part's suite can see. A stored window is a pair of
    dates; re-served six hours later it is six hours early, and re-served the next
    day it promises a date that has passed."""
    store, provider = Store(), Provider()
    first = ask(provider=provider, store=store)

    later = NOW_DT + timedelta(days=1)
    second = ask(provider=provider, store=store, now=later,
                 clock=lambda: NOW_TS + 60)  # still inside route freshness

    assert len(provider.calls) == 1, "a fresh cache entry cost a second call"
    assert second["internal"]["source"] == "CACHE_FRESH"
    assert second["buyer"]["earliest"] > first["buyer"]["earliest"], \
        "the cached answer replayed yesterday's dates"


def test_what_is_stored_is_the_suppliers_evidence_and_not_the_rendered_window():
    store = Store()
    ask(provider=Provider(), store=store)
    stored = store.data[store.writes[0]["key"]]["value"]
    assert stored["transit"] == {"min_days": 6, "max_days": 12,
                                 "basis": estimate.BASIS_CALENDAR}
    assert "earliest" not in stored and "latest" not in stored


def test_a_stored_answer_outlives_its_freshness_so_an_outage_has_something_to_serve():
    store = Store()
    ask(provider=Provider(), store=store)
    ttl = store.writes[0]["ttl"]
    assert ttl > cache.ROUTE_FRESH_SECONDS, \
        "the entry is evicted the moment it becomes stale, so the stale tier is a miss tier"


# ---------------------------------------------------------------------------
# Unreachable is not the same answer as unserviceable
# ---------------------------------------------------------------------------

def test_a_supplier_outage_falls_back_to_the_stored_answer_at_lower_confidence():
    store = Store()
    ask(provider=Provider(), store=store)

    stale_at = NOW_TS + cache.ROUTE_FRESH_SECONDS + 60
    down = Provider(error=RuntimeError("connection reset"))
    result = ask(provider=down, store=store, clock=lambda: stale_at)

    assert result["buyer"]["state"] == estimate.STATE_ESTIMATED
    assert result["buyer"]["confidence"] == estimate.CONFIDENCE_PROVIDER_CACHED, \
        "a stale answer was presented with the same confidence as a fresh quote"
    assert result["internal"]["source"] == "CACHE_STALE"


def test_a_supplier_outage_with_nothing_stored_says_so_rather_than_inventing_a_date():
    result = ask(provider=Provider(error=RuntimeError("connection reset")), store=Store())
    assert result["buyer"]["state"] == estimate.STATE_UNAVAILABLE
    assert result["buyer"]["earliest"] is None and result["buyer"]["latest"] is None
    assert result["buyer"]["confidence"] == estimate.CONFIDENCE_NONE


def test_a_supplier_error_is_never_stored_as_though_it_were_an_answer():
    """Caching our own failure to reach the supplier makes our outage look like
    the supplier's verdict for as long as the TTL lasts."""
    store = Store()
    ask(provider=Provider(error=RuntimeError("connection reset")), store=store)
    assert store.writes == []


def test_a_variant_we_cannot_describe_is_neither_an_outage_nor_an_unserviceable_route():
    """Three states that look identical on the page and are not interchangeable
    anywhere else. "We do not ship there" blocks a checkout, and blocking one over
    a missing weight column would refuse an order the supplier would have accepted.
    "The supplier is down" invites a retry that cannot help. This is our data, it
    will not heal on its own, and the reason names the column to go and fill in."""
    store = Store()
    incomplete = Provider(error=breaker.NotProviderEvidence("variant_weight_unknown"))
    result = ask(provider=incomplete, store=store)

    assert result["buyer"]["state"] == estimate.STATE_UNAVAILABLE
    assert result["buyer"]["state"] != estimate.STATE_UNSUPPORTED_ROUTE
    assert result["buyer"]["reason"] == quote.REASON_VARIANT_INCOMPLETE
    assert result["buyer"]["reason"] != quote.REASON_PROVIDER_FAILED
    assert result["internal"]["detail"] == "variant_weight_unknown"
    assert store.writes == [], \
        "a catalogue gap was cached, so repairing the row would not take effect"


def test_the_reason_a_variant_could_not_be_described_stays_out_of_the_buyer_half():
    """It names an internal column. Useful in a log, meaningless on a product page."""
    result = ask(provider=Provider(error=breaker.NotProviderEvidence("variant_sku_unknown")),
                 store=Store())
    assert "variant_sku_unknown" not in repr(result["buyer"])
    assert set(result["buyer"]) == quote.BUYER_FIELDS


def test_an_unserviceable_destination_is_unsupported_and_not_merely_unavailable():
    """§55: a destination the supplier does not serve has to block a checkout, and
    a checkout gate reads the state — so "no route" and "we could not ask" must
    not arrive as the same state."""
    result = ask(provider=Provider(options=[]), store=Store())
    assert result["buyer"]["state"] == estimate.STATE_UNSUPPORTED_ROUTE
    unreachable = ask(provider=Provider(error=RuntimeError("down")), store=Store())
    assert unreachable["buyer"]["state"] == estimate.STATE_UNAVAILABLE
    assert result["buyer"]["state"] != unreachable["buyer"]["state"]


def test_a_cached_negative_comes_back_unsupported_rather_than_transit_unknown():
    """The stored negative has no transit range, so a naive re-read reports
    "supplier transit unknown" — a different reason, sending a different
    investigation somewhere else, and no longer a checkout block."""
    store = Store()
    ask(provider=Provider(options=[]), store=store)
    again = ask(provider=Provider(options=[]), store=store, clock=lambda: NOW_TS + 1)
    assert again["buyer"]["state"] == estimate.STATE_UNSUPPORTED_ROUTE
    assert again["buyer"]["reason"] == estimate.REASON_UNSUPPORTED


def test_a_fresh_negative_outranks_a_stale_positive():
    """The stale entry says ten days; the supplier now says it ships nothing
    there. Preferring the stale positive because it is an estimate rather than a
    refusal keeps a buy button live on an unshippable route."""
    store = Store()
    ask(provider=Provider(), store=store)
    stale_at = NOW_TS + cache.ROUTE_FRESH_SECONDS + 60
    result = ask(provider=Provider(options=[]), store=store, clock=lambda: stale_at)
    assert result["buyer"]["state"] == estimate.STATE_UNSUPPORTED_ROUTE


def test_a_caller_can_refuse_a_stale_answer_when_it_is_about_to_take_money():
    """§39: checkout may insist on a live quote. A surface that cannot ask for one
    has to either accept a stale promise or reimplement the fetch."""
    store = Store()
    ask(provider=Provider(), store=store)
    stale_at = NOW_TS + cache.ROUTE_FRESH_SECONDS + 60
    result = ask(provider=Provider(error=RuntimeError("down")), store=store,
                 clock=lambda: stale_at, allow_stale=False)
    assert result["buyer"]["state"] == estimate.STATE_UNAVAILABLE


def test_a_repeated_outage_stops_calling_the_supplier():
    """The breaker has to be wired in, not merely present in the package. A
    composition that never consults it is green in both suites."""
    down = Provider(error=RuntimeError("connection reset"))
    for offset in range(breaker.FAILURE_THRESHOLD + 3):
        ask(provider=down, store=Store(), clock=lambda o=offset: NOW_TS + o)
    assert len(down.calls) == breaker.FAILURE_THRESHOLD, \
        "the circuit never opened, so every shopper paid the full timeout"


def test_one_suppliers_outage_does_not_stop_quoting_another():
    """The breaker is keyed per supplier, and this is the join that has to pass the
    key. A single circuit for the whole platform lets one dead supplier blank every
    other supplier's estimates — and both the breaker suite and this one stay green
    while it does, because neither alone exercises two suppliers."""
    class Named(Provider):
        def __init__(self, name, **kwargs):
            super().__init__(**kwargs)
            self.name = name

    down = Named("dead", error=RuntimeError("connection reset"))
    for offset in range(breaker.FAILURE_THRESHOLD + 2):
        ask(provider=down, store=Store(), clock=lambda o=offset: NOW_TS + o)

    # Asked while a shared circuit would still be cooling down. Querying after the
    # cooldown expired would find the shared circuit half-open and admitting a
    # probe, so the healthy supplier would be quoted either way and the assertion
    # would prove nothing.
    still_cooling = NOW_TS + breaker.BASE_COOLDOWN_SECONDS / 2
    assert breaker.inspect_circuit("delivery:dead", now=still_cooling)["state"] \
        == breaker.STATE_OPEN

    healthy = Named("alive")
    result = ask(provider=healthy, store=Store(), clock=lambda: still_cooling)
    assert result["buyer"]["state"] == estimate.STATE_ESTIMATED
    assert len(healthy.calls) == 1


def test_an_unserviceable_destination_is_not_cached_with_a_stale_window():
    """A stale "we do not ship there" blocks a checkout on evidence that may have
    been a credential problem an hour ago. The negative gets a short freshness and
    no stale tier at all, so it expires into a re-ask rather than into a refusal
    that keeps being repeated."""
    store = Store()
    ask(provider=Provider(options=[]), store=store)
    assert store.writes[0]["ttl"] == cache.UNSUPPORTED_FRESH_SECONDS, \
        "the negative answer outlives its freshness and will be served stale"


def test_a_route_that_is_too_slow_to_accept_is_not_quoted():
    """The ceiling belongs to the composed call, because the surface asking is the
    thing that knows what it is willing to promise."""
    result = ask(provider=Provider(options=[route_option("slow", high=60)]),
                 store=Store(), ceiling_days=21)
    assert result["buyer"]["state"] == estimate.STATE_UNSUPPORTED_ROUTE


# ---------------------------------------------------------------------------
# No estimate is invented out of missing policy
# ---------------------------------------------------------------------------

def test_an_undeclared_handling_time_produces_a_reason_rather_than_same_day_dispatch():
    result = ask(provider=Provider(), handling=None)
    assert result["buyer"]["reason"] == estimate.REASON_NO_HANDLING
    assert result["buyer"]["earliest"] is None


def test_an_undeclared_buffer_produces_a_reason_rather_than_no_buffer():
    result = ask(provider=Provider(), buffer_days=None)
    assert result["buyer"]["reason"] == estimate.REASON_NO_BUFFER


def test_the_policy_inputs_have_no_defaults_so_they_cannot_be_forgotten_silently():
    """A default handling time is a fabricated promise with a plausible shape. The
    signature is the enforcement: omitting one is a TypeError, not a zero."""
    import inspect
    parameters = inspect.signature(quote.quote_delivery).parameters
    for required in ("fulfillment", "destination", "now", "handling", "buffer_days"):
        assert parameters[required].default is inspect.Parameter.empty, \
            f"{required} acquired a default and can now be forgotten silently"


# ---------------------------------------------------------------------------
# The abstraction the mission asked for
# ---------------------------------------------------------------------------

def test_the_composition_layer_names_no_supplier():
    """§10: the delivery domain must not become permanently CJ-specific. A second
    supplier should be a new adapter, and the way that stops being true is one
    `if supplier == "cj"` added here under deadline."""
    import ast
    tree = ast.parse(open(quote.__file__, encoding="utf-8").read())
    body = [node for node in tree.body if not (
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    source = "\n".join(ast.unparse(node) for node in body).lower()
    for supplier in ("cj", "cjdropshipping", "dropshipping"):
        assert supplier not in source.replace("_", " ").split(), \
            f"the composition layer names {supplier!r}"


def test_a_provider_that_does_not_name_itself_cannot_be_cached_under_a_name():
    """An unnamed provider keys every supplier's answers together."""
    class Anonymous:
        name = ""

        def quote_routes(self, **kwargs):
            return [route_option("std")]

    with pytest.raises(quote.QuoteRejected):
        ask(provider=Anonymous())


def test_the_stub_provider_in_this_file_satisfies_the_declared_protocol():
    """Otherwise every test here passes against a shape no real adapter has."""
    import inspect
    expected = inspect.signature(quote.SupplierLogisticsProvider.quote_routes)
    actual = inspect.signature(Provider.quote_routes)
    assert list(actual.parameters) == list(expected.parameters)


# ---------------------------------------------------------------------------
# A broken cache must cost a provider call, never a product page
# ---------------------------------------------------------------------------

class BrokenStore:
    """A store that fails the way a flapping Redis fails: at the call."""

    def __init__(self, *, on_get=False, on_set=False):
        self.data = {}
        self.writes = []
        self._on_get = on_get
        self._on_set = on_set

    def get(self, key):
        if self._on_get:
            raise RuntimeError("cache unreachable")
        return self.data.get(key)

    def set(self, key, value, ttl_seconds):
        if self._on_set:
            raise RuntimeError("cache unreachable")
        self.data[key] = value
        self.writes.append({"key": key, "ttl": ttl_seconds})


def test_a_cache_that_cannot_be_read_costs_a_provider_call_and_not_the_page():
    """``store.get`` sits on the path of every product page render. If a store
    fault could propagate, one flapping Redis is a 500 on every PDP at once —
    and the provider was reachable the whole time.
    """
    provider = Provider()
    result = ask(provider=provider, store=BrokenStore(on_get=True))
    assert result["buyer"]["state"] == estimate.STATE_ESTIMATED
    assert len(provider.calls) == 1, "an unreadable cache has to be a miss, not an error"


def test_a_cache_that_cannot_be_written_does_not_discard_the_answer():
    """The estimate is already computed and correct by the time it is stored.
    Throwing it away because the cache refused it would make a broken cache serve
    worse than no cache at all, which inverts the reason it is there.
    """
    provider = Provider()
    result = ask(provider=provider, store=BrokenStore(on_set=True))
    assert result["buyer"]["state"] == estimate.STATE_ESTIMATED
    assert result["internal"]["source"] == "PROVIDER"


def test_a_cache_that_cannot_be_written_does_not_discard_a_negative_either():
    """The unserviceable-route branch has its own write, so it needs its own
    proof: a cache fault there would turn "we do not ship there" — which a
    checkout gate reads — into an unhandled exception.
    """
    unserviceable = Provider(options=[route_option("std", available=False)])
    result = ask(provider=unserviceable, store=BrokenStore(on_set=True))
    assert result["buyer"]["state"] == estimate.STATE_UNSUPPORTED_ROUTE
