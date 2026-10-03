"""The refusals in :mod:`services.catalog_semantics`, asserted rather than documented.

Every fixture in this file is shaped from a row measured in production on
2026-10-03, and the number in each assertion is the measured one. That matters
more than it usually does, because the failure this module exists to prevent is
not a crash: it is a plausible string. ``"Unbranded"``, a 13-digit supplier code
in a ``gtin`` field, ``"color"`` as the name of option 1 -- each of those is
accepted by every validator, renders correctly, and is false. A test that
invents its own fixture values cannot tell the difference between a module that
refuses correctly and one whose gate is inverted, because the fixture would be
chosen to match whichever behaviour was written.

The companion harness ``scripts/protection/mutate_catalog_semantics.py`` mutates
the module and asserts this file goes red, because an assertion that cannot fail
is worse than none.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.catalog_semantics import (  # noqa: E402
    UNKNOWN,
    Confidence,
    Fact,
    IdentifierClass,
    PRIVATE_FIELDS,
    Provenance,
    SemanticState,
    classify_identifier,
    option_axes,
    product_identity,
    public_projection,
    semantic_state,
    supplier_product_key,
    variant_identity,
)

# Real SKU values from marketplace_listing_variants. All 3,721 non-empty SKUs in
# production share this shape and zero are a structurally valid GTIN.
PROD_SKUS = ("CJYD235792608HS", "CJTW244381907GT", "CJJJJSSK00060")

# A real provider_product_id. 19 digits -- long enough that a length-only GTIN
# test would reject it, but a 13-digit supplier code would not be so lucky.
PROD_PROVIDER_PRODUCT_ID = "2408301103301875900"

# GS1's own published example GTIN-13, so the check-digit path is exercised
# against a number that is genuinely valid rather than one computed by the same
# arithmetic the module uses.
VALID_GTIN13 = "4006381333931"


def listing_row(**over):
    """A supplier-backed listing as it actually sits in the table.

    `category` is the supplier breadcrumb verbatim; `subcategory` is empty, as
    it is on all 202 rows. There is no brand, gtin or mpn key because there is
    no such column.
    """
    row = {
        "id": 163,
        "seller_user_id": 7,
        "title": "Women's Summer Casual Short Sleeve Floral Print Midi Dress",
        "description": "Lightweight woven dress with a round neck and a tiered skirt.",
        "category": "Women's Clothing > Tops & Sets > Lady Dresses",
        "subcategory": "",
        "status": "published",
        "approval_status": "approved",
        "quantity": None,
        "price_label": "",
    }
    row.update(over)
    return row


def variant_row(**over):
    """A variant row with the positional option labels production actually has."""
    row = {
        "variant_key": "a3f1c0d2e4b5",
        "provider_variant_id": "2408301103301875901",
        "sku": PROD_SKUS[0],
        "options_json": json.dumps(
            [{"name": "option1", "value": "Blue"}, {"name": "option2", "value": "M"}]
        ),
        "price_cents": 2290,
        "cost_cents": 940,
        "currency": "USD",
        "stock_state": "IN_STOCK",
    }
    row.update(over)
    return row


def source_row(**over):
    row = {
        "provider": "cj",
        "supplier_connection_id": 4,
        "provider_product_id": PROD_PROVIDER_PRODUCT_ID,
        "fulfillment_mode": "DROPSHIP",
        "supplier_cost_cents": 940,
        "source_snapshot_id": 88211,
    }
    row.update(over)
    return row


def build(listing=None, variants=None, source=None, variant_images=None):
    return product_identity(
        listing or listing_row(),
        variants if variants is not None else [variant_row()],
        source if source is not None else source_row(),
        variant_images=variant_images,
    )


def walk(node, keys, values):
    """Collect every key and every scalar value in a projection, at any depth.

    A substring search over `json.dumps` is the obvious check and it is wrong
    here: `"CJ" not in blob` fails on the legitimate SKU `CJYD235792608HS`, and
    `"940" not in blob` would fail on a price of `$9.40`. Keys and values have to
    be compared as whole tokens.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            walk(value, keys, values)
    elif isinstance(node, list):
        for item in node:
            walk(item, keys, values)
    else:
        values.add(node)
    return keys, values


def projected_keys_and_values(identity):
    return walk(public_projection(identity), set(), set())


# --------------------------------------------------------------------------
# Identifier truth
# --------------------------------------------------------------------------


@pytest.mark.parametrize("sku", PROD_SKUS)
def test_a_production_sku_is_a_supplier_code_and_not_a_gtin(sku):
    assert classify_identifier(sku) == IdentifierClass.SUPPLIER_INTERNAL


def test_a_genuinely_valid_gtin_is_recognised():
    """The refusal has to be a judgement, not a blanket no.

    If `classify_identifier` returned SUPPLIER_INTERNAL unconditionally every
    other assertion here would still pass, and the module would be refusing out
    of inability rather than out of evidence.
    """
    assert classify_identifier(VALID_GTIN13) == IdentifierClass.GTIN


def test_a_gtin_shaped_value_with_a_bad_check_digit_is_unverified():
    """Neither a GTIN nor assertably a supplier code.

    This is the case a length-and-digits test gets wrong, and it is the one that
    would actually occur: a supplier sending a numeric catalogue code of exactly
    13 digits.
    """
    broken = VALID_GTIN13[:-1] + str((int(VALID_GTIN13[-1]) + 1) % 10)
    assert classify_identifier(broken) == IdentifierClass.UNVERIFIED


def test_a_19_digit_provider_product_id_is_not_a_gtin():
    assert classify_identifier(PROD_PROVIDER_PRODUCT_ID) != IdentifierClass.GTIN


@pytest.mark.parametrize("empty", [None, "", "   ", UNKNOWN])
def test_an_absent_identifier_is_missing_not_guessed(empty):
    assert classify_identifier(empty) == IdentifierClass.MISSING


def test_the_sku_is_never_promoted_into_gtin_or_mpn():
    """The specific mutation the briefing names: SUPPLIER ID -> GTIN."""
    v = variant_identity(variant_row(), listing_id=163)
    assert v.sku.value == PROD_SKUS[0]
    assert v.sku_class == IdentifierClass.SUPPLIER_INTERNAL
    assert v.gtin.value is UNKNOWN
    assert v.gtin.provenance == Provenance.UNAVAILABLE_FROM_SOURCE
    assert v.mpn.value is UNKNOWN
    assert v.mpn.provenance == Provenance.UNAVAILABLE_FROM_SOURCE


def test_mpn_stays_unknown_even_when_the_sku_really_is_a_gtin():
    """A real GTIN is published; it does not drag a fabricated MPN along."""
    v = variant_identity(variant_row(sku=VALID_GTIN13), listing_id=163)
    assert v.gtin.value == VALID_GTIN13
    assert v.gtin.confidence == Confidence.VERIFIED
    assert v.mpn.value is UNKNOWN


# --------------------------------------------------------------------------
# Brand
# --------------------------------------------------------------------------


def test_brand_is_unknown_and_unavailable_not_merely_unasked():
    """The mutation the briefing names: UNKNOWN BRAND -> FAKE BRAND.

    A full recursive key census over all 20,247 product snapshots found 20
    distinct keys and no brand among them, so the provenance has to say
    UNAVAILABLE_FROM_SOURCE. UNKNOWN would imply a backfill could fix it and
    send somebody looking for a source that does not exist.
    """
    identity = build()
    assert identity.brand.value is UNKNOWN
    assert identity.brand.provenance == Provenance.UNAVAILABLE_FROM_SOURCE
    assert identity.brand.confidence == Confidence.NONE


@pytest.mark.parametrize("filler", ["PulseSoc", "Unbranded", "Generic", "CJ", "N/A"])
def test_no_filler_string_can_reach_the_brand_field(filler):
    """Even when the listing row itself carries one.

    `marketplace_listings` has no brand column today, so a key arriving here is
    either a future column or a caller's own invention. Neither is a basis for a
    claim about who manufactured the product.
    """
    identity = build(listing=listing_row(brand=filler, brand_name=filler))
    assert identity.brand.value is UNKNOWN
    keys, values = projected_keys_and_values(identity)
    assert not [k for k in keys if "brand" in k]
    assert filler not in values


def test_an_unknown_fact_is_falsy_but_is_not_none():
    """So `fact.value or "Unbranded"` is wrong at review, not plausible.

    Falsy because a consumer will write `if fact.value:`; not None because a
    consumer must not be able to write `fact.value is None` and conclude nobody
    asked.
    """
    fact = Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE)
    assert not fact.value
    assert fact.value is not None
    assert fact.known is False


# --------------------------------------------------------------------------
# Variant axes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["option1", "option2", "opt_1", "attr 3", "ATTRIBUTE_2"])
def test_a_positional_label_is_a_position_not_a_name(label):
    """The mutation the briefing names: UNKNOWN ATTRIBUTE -> FABRICATED VALUE.

    All 3,797 production variant rows carry `option1`/`option2`, zero carry a
    semantic key. CJ sends one string, `"Blue-10.5g"`, and the importer splits
    it on `-`; what it cannot know is what the parts mean.
    """
    axes = option_axes(variant_row(options_json=json.dumps([{"name": label, "value": "Blue"}])))
    assert len(axes) == 1
    assert axes[0].position == 1
    assert axes[0].name.value is UNKNOWN
    assert axes[0].name.provenance == Provenance.UNAVAILABLE_FROM_SOURCE
    assert axes[0].value.value == "Blue"


def test_a_genuinely_semantic_axis_name_is_kept():
    """So the refusal survives a supplier that one day sends real names."""
    axes = option_axes(
        variant_row(options_json=json.dumps([{"name": "Colour", "value": "Blue"}]))
    )
    assert axes[0].name.value == "Colour"
    assert axes[0].name.provenance == Provenance.SUPPLIER_ASSERTED


def test_the_three_axis_product_is_not_relabelled_as_a_colour():
    """Listing 209, the 12 rows with three axes, is why axis names stay unknown.

    Its `option1` value is the boilerplate string "Picture Color" and its
    `option3` is a plug region. Calling position 1 the colour would assert a
    colour named "Picture Color"; dropping position 3 would lose which plug the
    buyer receives.
    """
    axes = option_axes(
        variant_row(
            options_json=json.dumps(
                [
                    {"name": "option1", "value": "Picture Color"},
                    {"name": "option2", "value": "1 Set"},
                    {"name": "option3", "value": "EU"},
                ]
            )
        )
    )
    assert [a.position for a in axes] == [1, 2, 3]
    assert all(a.name.value is UNKNOWN for a in axes)
    assert [a.value.value for a in axes] == ["Picture Color", "1 Set", "EU"]


def test_the_projection_omits_an_unknown_axis_name_rather_than_nulling_it():
    row = public_projection(build())["variants"][0]["axes"][0]
    assert row["position"] == 1
    assert "name" not in row
    assert row["value"] == "Blue"


@pytest.mark.parametrize("junk", ["", None, "[]", "{}", "not json", "[1, 2]"])
def test_unparseable_options_yield_no_axes_rather_than_an_invented_one(junk):
    assert option_axes(variant_row(options_json=junk)) == ()


# --------------------------------------------------------------------------
# Identity and grouping
# --------------------------------------------------------------------------


def test_the_supplier_product_key_is_namespaced_by_provider_and_connection():
    """A bare provider_product_id looks unique and is not.

    196 distinct ids over 196 rows today, but the enforced uniqueness is
    (seller, provider, connection, provider_product_id) -- deliberately, so one
    seller can import the same CJ product through two connections. Keying a
    feed's item_group_id on the bare id works now and silently merges two
    offers the day that happens.
    """
    key = supplier_product_key("cj", 4, PROD_PROVIDER_PRODUCT_ID)
    assert key.value == f"cj:4:{PROD_PROVIDER_PRODUCT_ID}"
    other = supplier_product_key("cj", 9, PROD_PROVIDER_PRODUCT_ID)
    assert other.value != key.value


def test_a_missing_provider_product_id_yields_no_key_rather_than_a_colliding_one():
    """`cj::` would be equal for every id-less row.

    A collision in an identity function is the one failure mode that merges two
    products without anything looking wrong.
    """
    for pid in (None, "", "   "):
        assert supplier_product_key("cj", 4, pid).value is UNKNOWN


def test_an_unbound_listing_still_gets_a_distinct_group_id():
    identity = build(source={})
    assert identity.supplier_product_key.value is UNKNOWN
    assert identity.group_id == "pulsesoc-listing-163"


def test_a_stable_id_survives_a_supplier_renaming_an_option_value():
    """The reason it keys on the supplier variant id and not on variant_key.

    variant_key is built from the option values, so "Sapphire Blue" becoming
    "Blue" rewrites it. A published id that moves when a colour is renamed is
    not an id.
    """
    before = variant_identity(variant_row(variant_key="option1=sapphire blue"), listing_id=163)
    after = variant_identity(variant_row(variant_key="option1=blue"), listing_id=163)
    assert before.stable_id == after.stable_id


UNSAFE_VARIANT_KEY = "option1=sapphire blue|option2=iphone11/pro (x)"


@pytest.mark.parametrize("provider_variant_id", ["2504191138021621100", "", None])
def test_a_stable_id_is_url_safe_and_does_not_spell_out_the_option_label(provider_variant_id):
    """Both branches, because only one of them is exercised in production.

    Every production variant_key carries `|`, `=` and often a space, `/`, a
    bracket or an apostrophe, and all 3,797 embed the literal `option1=`. All of
    that would travel into a JSON-LD @id or a feed id. The id is built from the
    supplier variant id when there is one -- true for all 3,797 rows today -- and
    falls back to the variant_key when there is not, so the fallback is the
    branch with no production coverage and the one a test has to pin.

    An earlier version of this test passed `provider_variant_id` only, so the
    fallback went unasserted and a mutation that published the raw key survived.
    """
    stable = variant_identity(
        variant_row(variant_key=UNSAFE_VARIANT_KEY, provider_variant_id=provider_variant_id),
        listing_id=107,
    ).stable_id
    assert re.fullmatch(r"[A-Za-z0-9._~-]+", stable), stable
    assert "option" not in stable
    assert "sapphire" not in stable


def test_a_variant_with_no_supplier_id_still_gets_a_distinct_stable_id():
    """The fallback has to stay an identity, not just a safe string."""
    a = variant_identity(variant_row(variant_key="option1=blue", provider_variant_id=""), listing_id=107)
    b = variant_identity(variant_row(variant_key="option1=red", provider_variant_id=""), listing_id=107)
    assert a.stable_id != b.stable_id


def test_two_variants_of_the_same_listing_do_not_share_a_stable_id():
    a = variant_identity(variant_row(provider_variant_id="2504191138021621100"), listing_id=163)
    b = variant_identity(variant_row(provider_variant_id="2507300922201614400"), listing_id=163)
    assert a.stable_id != b.stable_id


def test_two_listings_do_not_share_a_stable_id_for_the_same_supplier_variant():
    """variant_key is scoped to its listing -- 3,797 rows hold 2,571 distinct
    keys -- so the listing has to be part of the published id too.
    """
    a = variant_identity(variant_row(), listing_id=163)
    b = variant_identity(variant_row(), listing_id=164)
    assert a.stable_id != b.stable_id


def test_the_projection_does_not_publish_the_variant_key():
    row = public_projection(build())["variants"][0]
    assert "variant_key" not in row
    keys, values = projected_keys_and_values(build())
    assert not [v for v in values if isinstance(v, str) and "option1=" in v]


# --------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------


def test_the_supplier_breadcrumb_is_never_reported_as_a_pulsesoc_category():
    """There is no PulseSoc taxonomy: 108 distinct raw CJ breadcrumbs over 202
    rows, `subcategory` empty on all 202. Passing the supplier path off as a
    PulseSoc decision makes a supplier taxonomy change look like ours.
    """
    identity = build()
    assert identity.supplier_category_path.value.startswith("Women's Clothing >")
    assert identity.supplier_category_path.provenance == Provenance.SUPPLIER_ASSERTED
    assert identity.pulsesoc_category.value is UNKNOWN
    assert identity.pulsesoc_category.provenance == Provenance.UNAVAILABLE_FROM_SOURCE
    projected = public_projection(identity)
    assert "pulsesoc_category" not in projected
    assert projected["supplier_category_path_provenance"] == Provenance.SUPPLIER_ASSERTED


# --------------------------------------------------------------------------
# Media
# --------------------------------------------------------------------------


def test_a_variant_image_is_marked_recoverable_not_asserted():
    """The supplier sends one per variant for all 3,801; no column holds it.

    `marketplace_listing_variants` has no image column, so an image that reaches
    this layer came out of the `supplier_snapshots` audit trail. A consumer has
    to be able to tell, because a value read from an immutable snapshot is as old
    as the snapshot.
    """
    url = "https://cbu01.alicdn.com/img/ibank/blue.jpg"
    v = variant_identity(variant_row(), listing_id=163, snapshot_image=url)
    assert v.image.value == url
    assert v.image.provenance == Provenance.RECOVERABLE_FROM_SNAPSHOT


def test_an_image_is_attached_to_the_variant_it_belongs_to():
    """The mutation the briefing names: VARIANT A IMAGE -> VARIANT B.

    Mapping is by variant_key, so a swapped mapping produces a swapped output
    rather than a silently plausible one.
    """
    blue = variant_row(variant_key="blue-m")
    red = variant_row(variant_key="red-m", sku=PROD_SKUS[1])
    images = {"blue-m": "https://cdn/blue.jpg", "red-m": "https://cdn/red.jpg"}
    identity = build(variants=[blue, red], variant_images=images)
    by_key = {v.variant_key: v.image.value for v in identity.variants}
    assert by_key == {"blue-m": "https://cdn/blue.jpg", "red-m": "https://cdn/red.jpg"}


def test_a_variant_with_no_mapped_image_gets_unknown_not_the_other_variants():
    identity = build(
        variants=[variant_row(variant_key="blue-m"), variant_row(variant_key="red-m")],
        variant_images={"blue-m": "https://cdn/blue.jpg"},
    )
    images = {v.variant_key: v.image.value for v in identity.variants}
    assert images["blue-m"] == "https://cdn/blue.jpg"
    assert images["red-m"] is UNKNOWN


# --------------------------------------------------------------------------
# The public boundary
# --------------------------------------------------------------------------


def test_supplier_cost_never_crosses_the_public_boundary():
    """cost_cents is populated on all 3,797 variants and supplier_cost_cents on
    all 196 sources, so this is live data and not a hypothetical column.
    """
    keys, values = projected_keys_and_values(build())
    assert not keys & PRIVATE_FIELDS
    assert not [k for k in keys if "cost" in k or "margin" in k]
    assert 940 not in values and "940" not in values
    assert 88211 not in values and "88211" not in values


def test_the_projection_is_an_allowlist_so_a_new_column_is_private_by_default():
    """The column most likely to be added next to a dropship variants table is a
    cost or a margin. Under a denylist it would be public the day it lands.
    """
    identity = build(
        listing=listing_row(margin_pct=61.0),
        variants=[variant_row(supplier_margin_cents=1350)],
    )
    keys, values = projected_keys_and_values(identity)
    assert not [k for k in keys if "margin" in k]
    assert 1350 not in values
    assert 61.0 not in values


def test_no_unknown_is_serialised_as_null():
    """`"brand": null` passes some validators and reads as an assertion."""
    keys, values = projected_keys_and_values(build())
    assert None not in values
    assert not keys & {"brand", "gtin", "mpn", "pulsesoc_category"}
    assert "null" not in json.dumps(public_projection(build()))


def test_the_unknown_sentinel_itself_cannot_be_json_serialised():
    """A last-resort guard. If a future field leaks UNKNOWN into the projection,
    the consumer raises rather than emitting a repr of the sentinel.
    """
    with pytest.raises(TypeError):
        json.dumps({"brand": UNKNOWN})


def test_a_real_gtin_does_appear_in_the_projection():
    """The boundary withholds what is unknown, not everything."""
    identity = build(variants=[variant_row(sku=VALID_GTIN13)])
    assert public_projection(identity)["variants"][0]["gtin"] == VALID_GTIN13


# --------------------------------------------------------------------------
# Semantic state
# --------------------------------------------------------------------------


def test_a_complete_supplier_product_is_ready_with_unknowns_not_ready():
    """Nothing in this catalogue is fully described, and the state says so.

    READY would claim the axis names and the brand are known. They are not, and
    they are not going to be.
    """
    assert semantic_state(build()) == SemanticState.READY_WITH_UNKNOWNS


def test_a_missing_title_is_reported_as_needing_one():
    assert semantic_state(build(listing=listing_row(title=""))) == SemanticState.NEEDS_TITLE


def test_a_missing_category_is_reported_as_needing_one():
    assert semantic_state(build(listing=listing_row(category=None))) == SemanticState.NEEDS_CATEGORY


def test_a_variant_without_a_stable_key_needs_a_decision_not_a_guess():
    identity = build(variants=[variant_row(variant_key="")])
    assert semantic_state(identity) == SemanticState.NEEDS_VARIANT_DECISION


def test_a_listing_with_no_variants_is_not_ready_however_good_its_prose_is():
    """Six of 202 production rows have no variants, and all six are also unbound
    from any supplier. There is nothing for a buyer to choose, so the product's
    meaning is not complete -- and before this clause existed the model returned
    READY_WITH_UNKNOWNS for all 202 rows, partitioning nothing.
    """
    assert semantic_state(build(variants=[])) == SemanticState.NEEDS_VARIANT_DECISION


def test_a_short_but_real_product_name_is_not_called_a_missing_title():
    """`Lip Medex` is 9 characters and a real product. `T2` is 2 and a
    placeholder. A length threshold cannot tell them apart, so there is none.
    """
    short = build(listing=listing_row(title="Lip Medex"))
    assert semantic_state(short) == semantic_state(build())
    assert public_projection(short)["title"] == "Lip Medex"


def test_a_listing_with_no_id_is_invalid_data():
    assert semantic_state(build(listing=listing_row(id=0))) == SemanticState.INVALID_DATA


def test_semantic_state_does_not_claim_a_listing_is_publishable():
    """The mutation the briefing names: HELD PRODUCT -> SEARCH READY.

    This module reports whether a product's *meaning* is complete.
    `marketplace_listing_lifecycle.public_sql()` owns whether a row may be
    published, and in production 152 of 196 published, approved listings are
    held back by `quantity` alone. A semantic state must therefore not be
    readable as an eligibility verdict -- so a held row and a buyable row with
    the same text produce the same state, and the state vocabulary contains no
    word meaning "publish this".
    """
    held = build(listing=listing_row(quantity=None, status="draft", approval_status="pending"))
    live = build(listing=listing_row(quantity=12, status="published"))
    assert semantic_state(held) == semantic_state(live)
    assert not {s for s in vars(SemanticState) if not s.startswith("_")} & {
        "PUBLIC",
        "BUYABLE",
        "SEARCH_ELIGIBLE",
        "MERCHANT_ELIGIBLE",
        "ELIGIBLE",
    }
    projected = public_projection(live)
    assert "eligible" not in json.dumps(projected).lower()


# --------------------------------------------------------------------------
# Untrusted supplier text
# --------------------------------------------------------------------------


def test_supplier_text_is_carried_verbatim_and_never_marked_pulsesoc_derived():
    """Supplier descriptions arrive tagged `untrusted_content` in the snapshot.

    This layer does not sanitise -- escaping belongs to whichever surface
    renders it, and silently stripping here would make the audit trail and the
    stored row disagree. What it must not do is relabel supplier prose as
    PulseSoc's own, because the provenance is what tells a renderer the string
    needs escaping.
    """
    payload = '<img src=x onerror="alert(1)">Lightweight woven dress'
    identity = build(listing=listing_row(description=payload))
    assert identity.description.value == payload
    assert identity.description.provenance == Provenance.SUPPLIER_ASSERTED
    assert identity.description.confidence == Confidence.ASSERTED
    projected = public_projection(identity)
    assert projected["description"] == payload
    assert projected["description_provenance"] == Provenance.SUPPLIER_ASSERTED


def test_there_is_no_provenance_meaning_an_unreviewed_ai_suggestion_is_fact():
    """The mutation the briefing names: AI SUGGESTION -> AUTHORITATIVE FACT.

    PULSESOC_NORMALIZED covers a deterministic transform of a value we already
    hold. There is deliberately no provenance for generated content, so a
    generator has nowhere to put its output that would make it read as truth.
    """
    allowed = {v for k, v in vars(Provenance).items() if not k.startswith("_")}
    assert not allowed & {"AI_GENERATED", "AI_SUGGESTED", "MODEL_DERIVED", "INFERRED"}
    assert Confidence.VERIFIED not in {Confidence.DERIVED, Confidence.NONE}
