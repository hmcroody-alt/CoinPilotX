"""The provider boundary refuses to invent facts it was not given.

What this file is defending
---------------------------
``normalize`` is the only module allowed to know a provider's vocabulary, and
every layer above it treats what comes out as true. That makes its three
refusals load-bearing:

* an unparseable price becomes ``None``, never ``0``,
* an absent stock signal becomes ``UNKNOWN``, never ``OUT_OF_STOCK``,
* a rejected media URL is dropped, never replaced with a placeholder.

Each of those is one ``or 0`` / ``or "OUT_OF_STOCK"`` / ``or PLACEHOLDER`` away
from being wrong, and each wrong version is invisible: a 100%-margin product, a
storefront that empties itself during a provider outage, and an import that
reports success while shipping black cards. None of the three raises, so nothing
downstream can notice. This file is where they are noticed.

The tests are pure — no database, no network, no fixtures — because the boundary
is pure. If a test here needs a connection, the boundary has leaked.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.business_os.suppliers import normalize as n


# ---------------------------------------------------------------------------
# Unknown cost is not zero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    None, "", "   ", "call for pricing", "N/A", "-", [], {}, True, False,
    float("nan"), float("inf"), float("-inf"), -1, -0.01, "-5.00",
])
def test_unreadable_cost_is_none_never_zero(value):
    result = n.cents(value)
    # `is None`, not falsiness: 0 is falsy and would pass a sloppier assert,
    # and 0 is precisely the wrong answer this whole module exists to avoid.
    assert result is None, f"{value!r} produced {result!r}"


@pytest.mark.parametrize("value,expected", [
    (0, 0), ("0", 0), ("0.00", 0), (8.2, 820), ("8.20", 820), ("$8.20", 820),
    ("USD 8.20", 820), (12, 1200), ("8.20-12.50", 820), ("8.2 - 12.5", 820),
])
def test_readable_cost_parses(value, expected):
    assert n.cents(value) == expected


def test_zero_cost_is_distinguishable_from_unknown_cost():
    # A provider genuinely saying "free" and a provider saying nothing must not
    # arrive at the same value; the entire margin story rests on this.
    assert n.cents("0.00") == 0
    assert n.cents(None) is None
    assert n.cents("0.00") != n.cents(None)


def test_cost_rounds_half_up_on_the_cent():
    assert n.cents("8.205") == 821
    assert n.cents("0.005") == 1


def test_absurd_cost_is_rejected_rather_than_stored():
    assert n.cents(10_000_000_000) is None


# ---------------------------------------------------------------------------
# Unknown stock is not out of stock
# ---------------------------------------------------------------------------

def test_absent_quantity_is_unknown_not_out_of_stock():
    state, quantity = n.stock_state()
    assert state == n.STOCK_UNKNOWN
    assert state != n.STOCK_OUT_OF_STOCK
    assert quantity is None


def test_provider_failure_is_distinct_from_zero_stock():
    failed, _ = n.stock_state(provider_failed=True)
    zero, zero_quantity = n.stock_state(quantity=0)
    assert failed == n.STOCK_PROVIDER_UNAVAILABLE
    assert zero == n.STOCK_OUT_OF_STOCK
    assert zero_quantity == 0
    assert failed != zero


def test_provider_failure_wins_over_a_stale_quantity():
    # A quantity from a previous read is not evidence about this read.
    state, quantity = n.stock_state(quantity=99, provider_failed=True)
    assert state == n.STOCK_PROVIDER_UNAVAILABLE
    assert quantity is None


@pytest.mark.parametrize("quantity,expected", [
    (0, n.STOCK_OUT_OF_STOCK), (1, n.STOCK_LOW_STOCK), (4, n.STOCK_LOW_STOCK),
    (5, n.STOCK_IN_STOCK), (900, n.STOCK_IN_STOCK), ("12", n.STOCK_IN_STOCK),
])
def test_quantity_maps_to_state(quantity, expected):
    assert n.stock_state(quantity=quantity)[0] == expected


def test_unrecognised_declared_state_is_unknown_not_passed_through():
    # A provider inventing a word must not make a variant read as purchasable to
    # code written before the word existed.
    state, _ = n.stock_state(declared="FLYING_OFF_THE_SHELVES")
    assert state == n.STOCK_UNKNOWN


def test_unparseable_quantity_is_unknown():
    assert n.stock_state(quantity="lots")[0] == n.STOCK_UNKNOWN
    assert n.stock_state(quantity=True)[0] == n.STOCK_UNKNOWN


# ---------------------------------------------------------------------------
# The storage collapse
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", sorted(n.INDETERMINATE_STOCK | {n.STOCK_DISCONTINUED}))
def test_indeterminate_states_collapse_to_unknown_never_out_of_stock(state):
    # marketplace_listing_variants stores three states. Every state that means
    # "we do not know" must land on UNKNOWN; readers gate purchasability on
    # OUT_OF_STOCK and treat it as a confirmed negative.
    assert n.storage_stock_state(state) == n.STOCK_UNKNOWN


def test_low_stock_collapses_upward_to_in_stock():
    assert n.storage_stock_state(n.STOCK_LOW_STOCK) == n.STOCK_IN_STOCK


def test_confirmed_states_survive_the_collapse():
    assert n.storage_stock_state(n.STOCK_IN_STOCK) == n.STOCK_IN_STOCK
    assert n.storage_stock_state(n.STOCK_OUT_OF_STOCK) == n.STOCK_OUT_OF_STOCK


def test_every_normalized_state_collapses_to_a_storable_one():
    storable = {n.STOCK_IN_STOCK, n.STOCK_OUT_OF_STOCK, n.STOCK_UNKNOWN}
    for state in n.STOCK_STATES:
        assert n.storage_stock_state(state) in storable


def test_only_a_real_out_of_stock_produces_out_of_stock():
    # The inverse direction: nothing other than a confirmed zero may reach the
    # one state that stops a buyer from purchasing.
    produced = {state for state in n.STOCK_STATES
                if n.storage_stock_state(state) == n.STOCK_OUT_OF_STOCK}
    assert produced == {n.STOCK_OUT_OF_STOCK}


# ---------------------------------------------------------------------------
# Media is an SSRF boundary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://cdn.example.com/a.jpg",              # plaintext
    "file:///etc/passwd",
    "gopher://cdn.example.com/a.jpg",
    "ftp://cdn.example.com/a.jpg",
    "javascript:alert(1)",
    "https://user:pass@cdn.example.com/a.jpg",   # credential phishing primitive
    "https://cdn.example.com@evil.example/a.jpg",
    "https://169.254.169.254/latest/meta-data/", # cloud metadata
    "https://127.0.0.1/a.jpg",
    "https://10.0.0.5/a.jpg",
    "https://192.168.1.1/a.jpg",
    "https://172.16.0.1/a.jpg",
    "https://[::1]/a.jpg",
    "https://[::ffff:127.0.0.1]/a.jpg",          # IPv4-mapped loopback
    "https://[::ffff:169.254.169.254]/a.jpg",
    "https://[fd00::1]/a.jpg",                   # unique-local
    "https://0.0.0.0/a.jpg",
    "https://255.255.255.255/a.jpg",
    "https://224.0.0.1/a.jpg",                   # multicast
    "https://metadata/a.jpg",                    # dotless container hostname
    "https://printer.local/a.jpg",
    "https://vault.internal/a.jpg",
    "https://cdn.example.com/a\n.jpg",           # control character smuggling
    "https://cdn.example.com/a .jpg",
    "",
    "   ",
    None,
    12345,
    ["https://cdn.example.com/a.jpg"],
])
def test_unsafe_media_url_is_rejected(url):
    assert n.safe_media_url(url) is None


@pytest.mark.parametrize("url", [
    "https://cdn.example.com/a.jpg",
    "https://cf.cjdropshipping.com/product/1.jpg?v=2",
    "https://sub.domain.example.co.uk/path/to/image.png",
    "https://8.8.8.8/a.jpg",  # a public IP literal is odd but not an SSRF pivot
])
def test_safe_media_url_is_accepted_unchanged(url):
    assert n.safe_media_url(url) == url


def test_overlong_url_is_rejected():
    assert n.safe_media_url("https://cdn.example.com/" + "a" * 4000) is None


def test_rejected_media_is_dropped_not_substituted():
    # The failure this prevents: a placeholder makes a broken import look like a
    # successful one, and the merchant finds out from a customer.
    media = n.media_list([
        "http://cdn.example.com/1.jpg",
        "https://127.0.0.1/2.jpg",
        "https://cdn.example.com/3.jpg",
    ])
    assert media == ["https://cdn.example.com/3.jpg"]
    assert not any("placeholder" in url or "default" in url for url in media)


def test_media_list_preserves_order_and_deduplicates():
    media = n.media_list([
        "https://cdn.example.com/b.jpg",
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.jpg",
    ])
    assert media == ["https://cdn.example.com/b.jpg", "https://cdn.example.com/a.jpg"]


def test_media_list_of_entirely_bad_urls_is_empty_not_faked():
    assert n.media_list(["http://a.example/1.jpg", "https://10.0.0.1/2.jpg"]) == []
    assert n.media_list(None) == []


def test_media_list_is_capped():
    urls = [f"https://cdn.example.com/{i}.jpg" for i in range(n.MAX_MEDIA + 25)]
    assert len(n.media_list(urls)) == n.MAX_MEDIA


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "  ", "has space", "tab\tsep", True, "a" * 500])
def test_unusable_external_id_is_none(value):
    assert n.external_id(value) is None


def test_numeric_external_id_becomes_a_string():
    assert n.external_id(12345) == "12345"


@pytest.mark.parametrize("value,expected", [
    ("usd", "USD"), ("USD", "USD"), ("Usd", "USD"),
    ("US", None), ("USDD", None), ("US1", None), (None, None), (840, None),
])
def test_currency_is_a_code_or_nothing(value, expected):
    assert n.currency(value) == expected


# ---------------------------------------------------------------------------
# Variant identity comes from the provider id
# ---------------------------------------------------------------------------

def _cj_product_payload(variants):
    return {
        "pid": "PID-1",
        "productNameEn": "Cotton Tee",
        "productImage": "https://cdn.example.com/cover.jpg",
        "categoryName": "Apparel",
        "sellPrice": "8.20",
        "variants": variants,
    }


def test_variants_are_identified_by_provider_id_not_by_title():
    product = n.product("cj", _cj_product_payload([
        {"vid": "V1", "variantKey": "Black-XL", "variantSellPrice": "8.20"},
        {"vid": "V2", "variantKey": "Black-XL", "variantSellPrice": "9.20"},
    ]))
    ids = [v["external_variant_id"] for v in product["variants"]]
    assert ids == ["V1", "V2"], "two provider ids must remain two variants"


def test_variant_without_a_provider_id_is_dropped():
    product = n.product("cj", _cj_product_payload([
        {"variantKey": "Black-XL", "variantSellPrice": "8.20"},
        {"vid": "V2", "variantKey": "Black-L", "variantSellPrice": "9.20"},
    ]))
    assert [v["external_variant_id"] for v in product["variants"]] == ["V2"]


def test_variant_key_fallback_produces_distinct_option_sets():
    # Without the "Black-XL" split every optionless variant hashes to the same
    # marketplace variant_key and a 12-variant product imports as one.
    product = n.product("cj", _cj_product_payload([
        {"vid": f"V{i}", "variantKey": f"Black-{size}", "variantSellPrice": "8.20"}
        for i, size in enumerate(["S", "M", "L", "XL", "XXL"])
    ]))
    option_sets = [tuple(sorted((o["name"], o["value"]) for o in v["options"]))
                   for v in product["variants"]]
    assert len(set(option_sets)) == 5, option_sets


def test_structured_options_are_preferred_over_the_key_split():
    product = n.product("cj", _cj_product_payload([{
        "vid": "V1", "variantKey": "Black-XL",
        "variantOptions": [{"name": "Colour", "value": "Black"}, {"name": "Size", "value": "XL"}],
    }]))
    assert product["variants"][0]["options"] == [
        {"name": "Colour", "value": "Black"}, {"name": "Size", "value": "XL"}]


def test_variant_count_is_capped():
    product = n.product("cj", _cj_product_payload([
        {"vid": f"V{i}", "variantSellPrice": "1.00"} for i in range(n.MAX_VARIANTS + 40)]))
    assert len(product["variants"]) == n.MAX_VARIANTS


# ---------------------------------------------------------------------------
# Product normalization
# ---------------------------------------------------------------------------

def test_cj_product_normalizes_to_the_neutral_shape():
    product = n.product("cj", _cj_product_payload([
        {"vid": "V1", "variantKey": "Black-XL", "variantSellPrice": "8.20",
         "variantQuantity": 40, "variantSku": "SKU-1"}]))
    assert product["provider"] == "cj"
    assert product["external_product_id"] == "PID-1"
    assert product["title"] == "Cotton Tee"
    assert product["cover_image_url"] == "https://cdn.example.com/cover.jpg"
    assert product["from_cost_cents"] == 820
    variant = product["variants"][0]
    assert variant["cost_cents"] == 820
    assert variant["stock_state"] == n.STOCK_IN_STOCK
    assert variant["stock_quantity"] == 40


def test_product_html_description_is_stripped_not_escaped():
    product = n.product("cj", {**_cj_product_payload([]),
                               "description": "<script>alert(1)</script>Soft <b>cotton</b>"})
    assert "<" not in product["description"]
    assert "script" not in product["description"].lower()
    assert "cotton" in product["description"]


def test_unknown_provider_is_refused():
    assert n.supported("cj") is True
    assert n.supported("printful") is False
    with pytest.raises(n.NormalizationError):
        n.product("printful", {"pid": "1"})


def test_non_mapping_payload_raises_rather_than_inventing_a_product():
    for payload in ["a string", 12, None, ["list"]]:
        with pytest.raises(n.NormalizationError):
            n.product("cj", payload)


def test_missing_fields_are_none_not_an_error():
    # A missing weight must not fail the import of an otherwise fine product.
    product = n.product("cj", {"pid": "PID-1", "productNameEn": "Tee"})
    assert product["external_product_id"] == "PID-1"
    assert product["weight_grams"] is None
    assert product["description"] is None
    assert product["media"] == []


# ---------------------------------------------------------------------------
# Inventory overlay
# ---------------------------------------------------------------------------

def test_inventory_overlay_updates_only_mentioned_variants():
    variants = [
        {"external_variant_id": "V1", "stock_state": n.STOCK_UNKNOWN, "stock_quantity": None},
        {"external_variant_id": "V2", "stock_state": n.STOCK_IN_STOCK, "stock_quantity": 7},
    ]
    result = n.apply_inventory(variants, {"V1": (n.STOCK_IN_STOCK, 12)})
    assert result[0]["stock_state"] == n.STOCK_IN_STOCK
    assert result[0]["stock_quantity"] == 12
    # V2 was not mentioned. A partial inventory read is normal provider
    # behaviour and must not empty the shelf.
    assert result[1]["stock_state"] == n.STOCK_IN_STOCK
    assert result[1]["stock_quantity"] == 7


def test_empty_inventory_read_changes_nothing():
    variants = [{"external_variant_id": "V1", "stock_state": n.STOCK_IN_STOCK, "stock_quantity": 7}]
    assert n.apply_inventory(variants, {}) == variants
    assert n.apply_inventory(variants, None) == variants


def test_cj_inventory_sums_warehouse_rows():
    readings = n.inventory("cj", {"data": [
        {"vid": "V1", "inventoryList": [{"storageNum": 3}, {"storageNum": 4}]},
        {"vid": "V2", "totalInventoryNum": 0},
        {"vid": "V3"},
    ]})
    assert readings["V1"] == (n.STOCK_IN_STOCK, 7)
    assert readings["V2"] == (n.STOCK_OUT_OF_STOCK, 0)
    # No count at all is unknown, not zero.
    assert readings["V3"] == (n.STOCK_UNKNOWN, None)


# ---------------------------------------------------------------------------
# Cost range
# ---------------------------------------------------------------------------

def test_cost_range_excludes_unknown_costs():
    low, high = n.cost_range([
        {"cost_cents": 820}, {"cost_cents": None}, {"cost_cents": 1250}])
    assert (low, high) == (820, 1250)


def test_all_unknown_cost_range_is_none_not_zero():
    low, high = n.cost_range([{"cost_cents": None}, {"cost_cents": None}])
    assert low is None and high is None
    low, high = n.cost_range([])
    assert low is None and high is None


def test_a_genuinely_free_variant_is_included():
    assert n.cost_range([{"cost_cents": 0}, {"cost_cents": 500}]) == (0, 500)
