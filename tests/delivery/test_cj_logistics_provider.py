"""The CJ side of the boundary: what gets sent, and what is refused instead.

What this file is defending
---------------------------
* **A guessed input produces a real-looking answer.** This is the whole risk of
  the module. Substitute a weight, a SKU or an "ORDINARY" shipping property and CJ
  returns a perfectly well-formed freight quote for a parcel that does not exist.
  Nothing downstream can tell that answer from a good one, so the refusals are
  asserted one fact at a time.
* **A bad catalogue row taking the supplier down.** An undescribed variant raised
  as an ordinary exception is counted by the breaker as a CJ failure. Three of them
  and the circuit opens for every product CJ fulfils. So the refusals must arrive
  as ``NotProviderEvidence``, and that is asserted rather than assumed.
* **An address reaching a shared cache entry.** The cache key holds a country and
  at most a postal prefix. A request carrying a full postal code, city or street
  computes one buyer's surcharge and serves it to everyone sharing their prefix, so
  the sent payload's key set is pinned.
* **Per-unit weight sent for a multi-unit order.** CJ prices it without complaint.
* **A malformed answer read as "no routes".** An empty list is a cacheable
  negative that blocks checkout; an unreadable body is not knowledge and has to
  reach the breaker as a failure.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.delivery import breaker
from services.delivery.providers import cj_logistics as p

FACTS = {
    "vid": "VID-1",
    "sku": "SKU-1",
    "origin": "CN",
    "weight_grams": 250,
    "properties": ["ORDINARY"],
}


NO_ROUTES = {"quotes": [], "state": "UNSUPPORTED_ROUTE"}
DEFAULT = object()


class Adapter:
    """Records the payload, because what is *sent* is most of this module."""

    def __init__(self, answer=DEFAULT, error=None):
        # A sentinel rather than ``None``, because ``None`` is one of the malformed
        # answers under test and must not be read as "give me the default".
        self.answer = NO_ROUTES if answer is DEFAULT else answer
        self.error = error
        self.payloads = []

    def estimate_shipping(self, payload):
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.answer


def provider(adapter=None, facts=FACTS):
    return p.CJLogisticsProvider(adapter=adapter or Adapter(),
                                 facts=lambda ref: facts)


def line_of(adapter):
    return adapter.payloads[-1]["reqDTOS"][0]


# --- the request ----------------------------------------------------------


def test_the_request_carries_the_five_facts_cj_needs_to_price_freight():
    adapter = Adapter()
    provider(adapter).quote_routes(variant_ref="cj:P1:VID-1",
                                   destination={"country": "us"}, quantity=1)
    line = line_of(adapter)
    assert line["srcAreaCode"] == "CN"
    assert line["destAreaCode"] == "US", "the destination country is upper-cased for CJ"
    assert line["weight"] == 250.0
    assert line["productProp"] == ["ORDINARY"]
    assert line["skuList"] == ["SKU-1"]
    assert line["freightTrialSkuList"] == [
        {"vid": "VID-1", "sku": "SKU-1", "skuQuantity": 1}]


def test_the_weight_is_the_parcel_not_one_unit_of_it():
    """A five-unit order of a 250g item is a 1.25kg parcel. Sending 250 quotes a
    cheaper, faster service than the one that will actually carry the goods, and
    CJ answers it without complaint because the request is well formed."""
    adapter = Adapter()
    provider(adapter).quote_routes(variant_ref="r", destination={"country": "US"},
                                   quantity=5)
    line = line_of(adapter)
    assert line["weight"] == 1250.0
    assert line["freightTrialSkuList"][0]["skuQuantity"] == 5


def test_no_part_of_an_address_is_ever_sent():
    """The cached answer has to be a function of its cache key, and that key holds
    a country and at most a postal prefix. A full postal code in the request means
    one buyer's remote-area surcharge is served to every buyer sharing the prefix."""
    adapter = Adapter()
    provider(adapter).quote_routes(
        variant_ref="r",
        destination={"country": "US", "postal": "94107", "city": "San Francisco",
                     "province": "CA", "address": "1 Market St"},
        quantity=1)
    sent = set(line_of(adapter))
    forbidden = {"zip", "city", "province", "recipientAddress"}
    assert sent & forbidden == set(), f"an address field reached CJ: {sent & forbidden}"


def test_a_declared_shipping_mode_is_passed_through():
    adapter = Adapter()
    provider(adapter).quote_routes(variant_ref="r", quantity=1,
                                   destination={"country": "US", "shipping_mode": "Packet"})
    assert line_of(adapter)["shippingMode"] == "Packet"


def test_no_shipping_mode_key_appears_when_none_was_declared():
    """Absent, not empty. CJ's allowlist admits the key, so an empty string is a
    request to ship by a mode called "" rather than a request with no preference."""
    adapter = Adapter()
    provider(adapter).quote_routes(variant_ref="r", destination={"country": "US",
                                                                "shipping_mode": "  "})
    assert "shippingMode" not in line_of(adapter)


def test_the_request_cj_receives_is_one_the_adapter_will_accept():
    """Cross-checked against `CJAdapter.estimate_shipping`'s own validation rather
    than against this module's idea of it, so a change to the allowlist upstream
    fails here instead of in production."""
    import ast

    from services.business_os.suppliers import cj as cj_adapter
    tree = ast.parse(open(cj_adapter.__file__, encoding="utf-8").read())
    permitted = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "estimate_shipping"):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Assign) and len(inner.targets) == 1
                    and getattr(inner.targets[0], "id", None) == "allowed"):
                permitted = {c.value for c in inner.value.elts}
    assert permitted, "could not find estimate_shipping's field allowlist to check against"

    adapter = Adapter()
    provider(adapter).quote_routes(variant_ref="r", quantity=3,
                                   destination={"country": "DE", "shipping_mode": "X"})
    unknown = set(line_of(adapter)) - permitted
    assert unknown == set(), f"the adapter would reject these keys: {unknown}"


# --- the refusals ---------------------------------------------------------


@pytest.mark.parametrize("missing,reason", [
    ("vid", p.REASON_NO_VID),
    ("origin", p.REASON_NO_ORIGIN),
    ("sku", p.REASON_NO_SKU),
    ("weight_grams", p.REASON_NO_WEIGHT),
    ("properties", p.REASON_NO_PROPERTIES),
])
def test_each_missing_fact_is_refused_by_name_rather_than_defaulted(missing, reason):
    """Named, because "no delivery estimate available" gives whoever has to repair
    the catalogue nothing to act on."""
    adapter = Adapter()
    facts = {k: v for k, v in FACTS.items() if k != missing}
    with pytest.raises(breaker.NotProviderEvidence) as raised:
        provider(adapter, facts).quote_routes(variant_ref="r",
                                              destination={"country": "US"})
    assert raised.value.reason == reason
    assert adapter.payloads == [], "a request was sent for a parcel we cannot describe"


def test_an_unbound_variant_is_refused_rather_than_quoted_as_a_default_parcel():
    adapter = Adapter()
    with pytest.raises(breaker.NotProviderEvidence) as raised:
        p.CJLogisticsProvider(adapter=adapter, facts=lambda ref: None).quote_routes(
            variant_ref="cj:nothing", destination={"country": "US"})
    assert raised.value.reason == p.REASON_UNKNOWN_VARIANT
    assert adapter.payloads == []


@pytest.mark.parametrize("weight", [0, -1, 0.0, float("nan"), float("inf"), True, "250", None])
def test_a_weight_that_is_not_a_positive_finite_number_is_not_a_weight(weight):
    """``True`` is in here because ``isinstance(True, int)`` — a boolean weight
    would pass a naive numeric check and quote a one-gram parcel."""
    with pytest.raises(breaker.NotProviderEvidence) as raised:
        provider(None, dict(FACTS, weight_grams=weight)).quote_routes(
            variant_ref="r", destination={"country": "US"})
    assert raised.value.reason == p.REASON_NO_WEIGHT


@pytest.mark.parametrize("properties", [[], None, ["", "  "], "ORDINARY", [None]])
def test_an_empty_property_list_is_refused_instead_of_becoming_ordinary(properties):
    """CJ routes batteries, liquids and magnets differently. "ORDINARY" is the
    plausible substitution and it is wrong exactly where it matters most."""
    with pytest.raises(breaker.NotProviderEvidence) as raised:
        provider(None, dict(FACTS, properties=properties)).quote_routes(
            variant_ref="r", destination={"country": "US"})
    assert raised.value.reason == p.REASON_NO_PROPERTIES


def test_a_blank_property_is_dropped_without_discarding_the_real_ones():
    adapter = Adapter()
    provider(adapter, dict(FACTS, properties=["", "BATTERY", "  ", "LIQUID"])).quote_routes(
        variant_ref="r", destination={"country": "US"})
    assert line_of(adapter)["productProp"] == ["BATTERY", "LIQUID"]


@pytest.mark.parametrize("country", [None, "", "U", "USA", "1S", 12, {"country": "US"}])
def test_an_unusable_destination_country_is_refused_before_the_call(country):
    adapter = Adapter()
    with pytest.raises(breaker.NotProviderEvidence):
        provider(adapter).quote_routes(variant_ref="r", destination={"country": country})
    assert adapter.payloads == []


@pytest.mark.parametrize("quantity", [0, -3, True, 1.0, "2", p.MAX_QUANTITY + 1])
def test_a_quantity_cj_will_not_price_is_refused_rather_than_sent(quantity):
    adapter = Adapter()
    with pytest.raises(breaker.NotProviderEvidence):
        provider(adapter).quote_routes(variant_ref="r", destination={"country": "US"},
                                       quantity=quantity)
    assert adapter.payloads == []


def test_a_refusal_is_not_evidence_about_the_supplier():
    """The reason this exception type exists. Counted as failures, three variants
    with no stated weight would open the circuit for every CJ product in the
    catalogue — a handful of bad rows taking the feature down for everything."""
    assert issubclass(breaker.NotProviderEvidence, Exception)
    assert not issubclass(breaker.NotProviderEvidence, breaker.ProviderUnreachable)

    breaker.reset("cj-probe")
    for _ in range(breaker.FAILURE_THRESHOLD + 2):
        with pytest.raises(breaker.NotProviderEvidence):
            breaker.guard("cj-probe",
                          lambda: provider(None, {}).quote_routes(
                              variant_ref="r", destination={"country": "US"}),
                          clock=lambda: 1_800_000_000.0)
    assert breaker.inspect_circuit("cj-probe", now=1_800_000_000.0)["state"] \
        == breaker.STATE_CLOSED
    breaker.reset("cj-probe")


# --- the answer -----------------------------------------------------------


def test_the_quotes_are_handed_on_exactly_as_the_adapter_normalized_them():
    """Not re-mapped. The router's contract is already this shape, and a second
    mapping layer is where one field silently stops arriving."""
    rows = [{"option_id": "o1", "channel_id": "c1", "provider_total": "4.20",
             "transit": {"min_days": 7, "max_days": 12}, "available": True,
             "estimated_transit": "7-12", "currency": "USD"}]
    got = provider(Adapter({"quotes": rows, "state": "QUOTED"})).quote_routes(
        variant_ref="r", destination={"country": "US"})
    assert got == rows
    assert got[0] is rows[0], "the rows were copied, and a copy can lose a field"


def test_no_routes_is_an_empty_list_rather_than_an_error():
    """CJ saying "nowhere from here" is an answer. It becomes a cacheable negative
    upstream; raising here would make an unserviceable corridor look like an outage
    and retry it forever."""
    assert provider(Adapter({"quotes": [], "state": "UNSUPPORTED_ROUTE"})).quote_routes(
        variant_ref="r", destination={"country": "US"}) == []


@pytest.mark.parametrize("answer", [None, {}, {"quotes": None}, {"quotes": "none"},
                                    {"state": "QUOTED"}])
def test_an_unreadable_answer_is_a_failure_and_not_an_empty_route_list(answer):
    """The difference decides whether a checkout is blocked. An empty list is CJ's
    statement that the corridor is unserviceable; an unreadable body is us not
    knowing what CJ said, and it has to reach the breaker as a failure."""
    with pytest.raises(Exception) as raised:
        provider(Adapter(answer)).quote_routes(variant_ref="r",
                                               destination={"country": "US"})
    assert not isinstance(raised.value, breaker.NotProviderEvidence), \
        "a malformed supplier answer was excused as our own data problem"


def test_a_supplier_error_propagates_so_the_breaker_can_see_it():
    boom = RuntimeError("CJ 503")
    with pytest.raises(RuntimeError):
        provider(Adapter(error=boom)).quote_routes(variant_ref="r",
                                                   destination={"country": "US"})


# --- the boundary ---------------------------------------------------------


def test_the_provider_reaches_the_supplier_without_the_order_gateway():
    """`gateway.read("shipping", ...)` persists a `supplier_snapshots` row per call
    and re-checks a merchant scope, both of which are right for pricing a paid
    order and wrong for a public product page: anonymous traffic would mint order
    evidence at the rate it arrives."""
    import ast

    tree = ast.parse(open(p.__file__, encoding="utf-8").read())
    imported, attributes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)
    assert not any("gateway" in name or "business_os" in name for name in imported), \
        f"the provider imports the order layer: {imported}"
    # The docstring above names `gateway.read` in order to explain why it is not
    # used, so this asks the syntax tree what is *called* rather than searching the
    # source text — which would match the explanation and pass on any code at all.
    for forbidden in ("read", "connect", "get_snapshot", "create_intent"):
        assert forbidden not in attributes, f"the provider reached for .{forbidden}()"
    assert "estimate_shipping" in attributes


def test_the_provider_names_itself_so_the_cache_key_can_be_scoped_to_it():
    assert p.CJLogisticsProvider(adapter=Adapter(), facts=lambda r: FACTS).name == "cj"
    assert p.SUPPLIER_NAME == "cj"


@pytest.mark.parametrize("kwargs", [{"adapter": None, "facts": lambda r: FACTS},
                                    {"adapter": Adapter(), "facts": None},
                                    {"adapter": Adapter(), "facts": "not callable"}])
def test_an_incompletely_wired_provider_fails_at_construction_not_at_a_quote(kwargs):
    with pytest.raises(ValueError):
        p.CJLogisticsProvider(**kwargs)
