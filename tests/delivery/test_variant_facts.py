"""A listing's parcel is described from what the import already wrote down.

What this file is defending
---------------------------
Four failures, each of which produces a delivery date rather than an error:

* **The wrong variant's parcel.** One listing can hold a 200-gram shirt and a
  2-kilo pair of boots. Quoting the listing-level binding, or the first variant in
  the snapshot's list, gives a confident estimate for a different object.
* **A weight matched by position.** The supplier does not promise variant
  ordering, so pairing weights to variants by index silently reassigns every one
  of them the first time CJ reorders its own list.
* **A fabricated origin.** Nothing local records a warehouse country. Defaulting
  to ``"CN"`` would put an invented origin underneath every estimate in the
  domain, and it would look completely normal.
* **Supplier economics reaching an unauthenticated page.** The snapshot body this
  module reads holds CJ's wholesale prices next to the weights, and it is read
  with no actor because a visitor has no merchant identity. Only five keys may
  leave.

The tests drive real SQL against a temp SQLite file built from the production
DDL constants, so a renamed column fails here rather than in production.
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_DB = tempfile.NamedTemporaryFile(prefix="pulsesoc-variant-facts-", suffix=".db", delete=False)
_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.name}"

from services import db, marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.delivery import variant_facts  # noqa: E402

LISTING = 4242
PID = "PID-1"
VID = "VID-SHIRT"
SNAPSHOT = "snap-1"

SHIRT = {"pid": PID, "vid": VID, "sku": "SKU-SHIRT", "weight_grams": 220,
         "price": "4.10", "currency": "USD"}
BOOTS = {"pid": PID, "vid": "VID-BOOTS", "sku": "SKU-BOOTS", "weight_grams": 2050,
         "price": "31.00", "currency": "USD"}

PAYLOAD = {"pid": PID, "logistics_properties": ["ORDINARY"],
           "variants": [BOOTS, SHIRT], "supplier_wholesale_note": "18.40"}


def origin_is(country="CN"):
    calls = []

    def lookup(pid, vid):
        calls.append((pid, vid))
        return country

    lookup.calls = calls
    return lookup


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


def seed(*, provider="cj", provider_variant_id=VID, external_sku=None,
         snapshot_id=SNAPSHOT, payload=None, kind="product", variants=()):
    """One bound listing, its snapshot, and any per-variant rows."""
    conn = db.connect()
    try:
        conn.execute(
            f"INSERT INTO {supplier_schema.SOURCE_TABLE} "
            f"(listing_id, seller_user_id, provider, provider_product_id, "
            f" provider_variant_id, external_sku, source_snapshot_id) "
            f"VALUES (?,?,?,?,?,?,?)",
            (LISTING, 1, provider, PID, provider_variant_id, external_sku, snapshot_id))
        if snapshot_id is not None:
            conn.execute("INSERT INTO supplier_snapshots (snapshot_id, kind, payload_json) "
                         "VALUES (?,?,?)",
                         (snapshot_id, kind,
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


def describe(ref=str(LISTING), *, lookup=None):
    return variant_facts.describe(ref, origin_lookup=lookup or origin_is())


# ---------------------------------------------------------------------------
# The happy path, and exactly what leaves
# ---------------------------------------------------------------------------

def test_a_bound_listing_is_described_from_local_rows_alone():
    seed()
    assert describe() == {"vid": VID, "sku": "SKU-SHIRT", "origin": "CN",
                          "weight_grams": 220, "properties": ["ORDINARY"]}


def test_the_facts_are_exactly_what_the_cj_provider_demands():
    """The provider refuses on any absent key, so the two sets have to match."""
    seed()
    assert set(describe()) == variant_facts.FACT_KEYS


def test_no_supplier_economics_leave_with_the_facts():
    """The snapshot body holds CJ's wholesale price beside the weight, and this
    read has no actor. A key that is not one of the five is a leak."""
    seed()
    facts = describe()
    assert "18.40" not in repr(facts), "a wholesale figure travelled with the parcel"
    assert "4.10" not in repr(facts)
    assert "price" not in facts and "currency" not in facts


def test_an_origin_is_asked_for_by_variant_and_not_by_buyer():
    """Origin is a function of (pid, vid) and stock. If it were asked per buyer or
    per destination it could not be cached across them, which is the whole reason
    it is a separate tier."""
    seed()
    lookup = origin_is()
    describe(lookup=lookup)
    assert lookup.calls == [(PID, VID)]


def test_a_lowercase_origin_is_normalized_rather_than_refused():
    seed()
    assert describe(lookup=origin_is("cn"))["origin"] == "CN"


# ---------------------------------------------------------------------------
# The right variant's parcel
# ---------------------------------------------------------------------------

def test_a_chosen_variant_is_described_and_not_the_listings_default_binding():
    """The failure this exists for: one listing, a shirt and a pair of boots, and
    an estimate for whichever the importer happened to bind."""
    seed(variants=[("color=red|size=m", VID, "SKU-SHIRT"),
                   ("color=black|size=9", "VID-BOOTS", "SKU-BOOTS")])
    boots = describe(f"{LISTING}:color=black|size=9")
    assert boots["vid"] == "VID-BOOTS"
    assert boots["weight_grams"] == 2050
    assert boots["sku"] == "SKU-BOOTS"


def test_a_weight_is_matched_to_its_variant_and_never_to_a_position():
    """``PAYLOAD`` lists the boots first on purpose. Anything reading the head of
    the list, or zipping by index, hands the shirt the boots' 2 kilos."""
    seed(variants=[("color=red|size=m", VID, "SKU-SHIRT")])
    assert describe(f"{LISTING}:color=red|size=m")["weight_grams"] == 220
    assert PAYLOAD["variants"][0]["vid"] == "VID-BOOTS", "the fixture stopped testing order"


def test_a_variant_key_this_listing_does_not_have_is_not_quoted_as_the_default():
    """Falling back to the listing binding would answer a question about a variant
    that does not exist with a confident estimate for one that does."""
    seed(variants=[("color=red|size=m", VID, "SKU-SHIRT")])
    assert describe(f"{LISTING}:color=green|size=xl") is None


def test_a_listing_with_no_variant_rows_falls_back_to_its_sole_orderable_binding():
    """``importer._sole_orderable`` writes that column only when there is one
    orderable variant, so it is a safe fallback and not a guess."""
    seed()
    assert describe()["vid"] == VID


def test_a_variant_row_without_a_provider_id_does_not_erase_the_binding():
    seed(variants=[("color=red|size=m", None, None)])
    facts = describe(f"{LISTING}:color=red|size=m")
    assert facts["vid"] == VID and facts["sku"] == "SKU-SHIRT"


# ---------------------------------------------------------------------------
# Nothing is defaulted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("country", [None, "", "C", "CHN", 86, True, "  "])
def test_an_origin_that_is_not_a_country_yields_no_facts(country):
    """No local column records a warehouse. A default of "CN" would sit under
    every estimate the domain makes and look entirely normal."""
    seed()
    assert describe(lookup=origin_is(country)) is None


@pytest.mark.parametrize("grams", [None, 0, -5, "220", True, float("nan"), float("inf")])
def test_a_variant_without_a_usable_weight_yields_no_facts(grams):
    """``normalize._cj_variant`` leaves ``weight_grams`` null for a variant CJ
    never weighed. ``True`` is here because ``isinstance(True, int)`` would pass
    it as a one-gram parcel."""
    seed(payload={"pid": PID, "logistics_properties": ["ORDINARY"],
                  "variants": [dict(SHIRT, weight_grams=grams)]})
    assert describe() is None


@pytest.mark.parametrize("properties", [None, [], [""], [None], "ORDINARY"])
def test_absent_logistics_properties_do_not_become_ordinary(properties):
    """CJ routes batteries, liquids and magnets down different channels. A
    substituted "ORDINARY" quotes the wrong service for exactly the goods where
    the difference matters."""
    seed(payload={"pid": PID, "logistics_properties": properties, "variants": [SHIRT]})
    assert describe() is None


def test_a_variant_the_snapshot_does_not_describe_yields_no_facts():
    seed(payload={"pid": PID, "logistics_properties": ["ORDINARY"], "variants": [BOOTS]})
    assert describe() is None


def test_a_snapshot_whose_pid_does_not_match_the_binding_yields_no_facts():
    """A snapshot is matched on both ids. Matching on the variant alone would let
    a re-bound listing keep quoting the product it used to be."""
    seed(payload={"pid": PID, "logistics_properties": ["ORDINARY"],
                  "variants": [dict(SHIRT, pid="PID-OTHER")]})
    assert describe() is None


# ---------------------------------------------------------------------------
# Rows that are not an answer
# ---------------------------------------------------------------------------

def test_an_unbound_listing_yields_no_facts():
    assert describe() is None


def test_a_listing_bound_to_another_supplier_is_not_read_with_cj_field_names():
    """Every fact above is read out of a CJ payload shape. Another provider's
    snapshot would be parsed with the wrong vocabulary and mostly succeed."""
    seed(provider="aliexpress")
    assert describe() is None


@pytest.mark.parametrize("kind", ["inventory", "shipping", "product_list"])
def test_a_snapshot_of_the_wrong_kind_is_not_mined_for_variants(kind):
    """An inventory snapshot has no variant weights. Reading one would report
    "this variant has no weight" for a listing whose weight is recorded fine one
    row over — sending the repair to the wrong place."""
    seed(kind=kind)
    assert describe() is None


def test_a_missing_snapshot_row_yields_no_facts():
    seed(snapshot_id=None)
    assert describe() is None


def test_an_unparseable_snapshot_body_yields_no_facts():
    conn = db.connect()
    try:
        conn.execute(
            f"INSERT INTO {supplier_schema.SOURCE_TABLE} "
            f"(listing_id, seller_user_id, provider, provider_product_id, "
            f" provider_variant_id, source_snapshot_id) VALUES (?,?,?,?,?,?)",
            (LISTING, 1, "cj", PID, VID, SNAPSHOT))
        conn.execute("INSERT INTO supplier_snapshots (snapshot_id, kind, payload_json) "
                     "VALUES (?,?,?)", (SNAPSHOT, "product", "{not json"))
        conn.commit()
    finally:
        conn.close()
    assert describe() is None


def test_a_listing_with_no_bound_variant_at_all_yields_no_facts():
    seed(provider_variant_id=None)
    assert describe() is None


# ---------------------------------------------------------------------------
# The reference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref", [None, "", "   ", "abc", "abc:red", "0", "-3", 4242,
                                 ":red"])
def test_a_reference_that_names_no_listing_is_a_bug_and_not_a_quiet_absence(ref):
    """Distinguished from "this listing has no weight on file" on purpose: the
    first is a coding mistake and the second is a page that honestly cannot say.
    Collapsing them hides the bug behind a plausible product state."""
    with pytest.raises(variant_facts.VariantRefInvalid):
        variant_facts.describe(ref, origin_lookup=origin_is())


def test_a_reference_carrying_a_variant_key_with_a_separator_in_it_is_kept_whole():
    """``partition`` splits on the first separator only, so an option value
    containing one survives. ``str.split`` would truncate it and look up a key
    the listing does not have."""
    seed(variants=[("size=10:12", "VID-BOOTS", "SKU-BOOTS")])
    assert describe(f"{LISTING}:size=10:12")["vid"] == "VID-BOOTS"


def test_parse_ref_reports_the_listing_and_the_variant_separately():
    assert variant_facts.parse_ref("77:color=red") == {"listing_id": 77,
                                                       "variant_key": "color=red"}
    assert variant_facts.parse_ref(" 77 ") == {"listing_id": 77, "variant_key": None}


# ---------------------------------------------------------------------------
# The shape the provider is handed
# ---------------------------------------------------------------------------

def test_the_resolver_hands_the_provider_a_one_argument_callable():
    """The provider takes ``facts(variant_ref)`` and must stay unaware of both the
    database and the origin tier."""
    seed()
    facts = variant_facts.resolver(origin_lookup=origin_is())
    assert facts(str(LISTING))["weight_grams"] == 220
    assert facts("999999") is None


def test_the_provider_accepts_what_this_module_produces():
    """The two were written against each other; this is the assertion that keeps
    them that way. The provider validates all five facts and raises by name on any
    it cannot use."""
    from services.delivery.providers import cj_logistics

    seed()
    sent = []

    class Adapter:
        def estimate_shipping(self, payload):
            sent.append(payload)
            return {"quotes": [], "state": "UNSUPPORTED_ROUTE"}

    provider = cj_logistics.CJLogisticsProvider(
        adapter=Adapter(), facts=variant_facts.resolver(origin_lookup=origin_is()))
    provider.quote_routes(variant_ref=str(LISTING),
                          destination={"country": "US"}, quantity=2)

    line = sent[-1]["reqDTOS"][0]
    assert line["srcAreaCode"] == "CN"
    assert line["destAreaCode"] == "US"
    assert line["weight"] == 440.0, "the parcel is two shirts, not one"
    assert line["productProp"] == ["ORDINARY"]
    assert line["freightTrialSkuList"] == [{"vid": VID, "sku": "SKU-SHIRT",
                                            "skuQuantity": 2}]
