#!/usr/bin/env python3
"""Prove ``tests/catalog/test_catalog_semantics.py`` would notice a lie.

The suite this guards asserts absences. That is an unusually weak thing to
assert, because the green state and the vacuous state look identical: a test
saying "brand is UNKNOWN" passes just as readily against a module that computes
the refusal from evidence as against one that returns UNKNOWN because somebody
hardcoded it and forgot the other half. And the failure mode being guarded is not
a crash -- it is a plausible string. ``"Unbranded"`` renders fine. A 13-digit
supplier code in a ``gtin`` field validates. ``"color"`` as the name of option 1
reads like a feature.

So each entry below replaces a refusal with the plausible thing somebody would
actually write, and the matrix passes only if the suite goes red for every one.

Five of the entries are the mutations PulseSoc's own search-OS brief names as
mandatory: UNKNOWN BRAND -> FAKE BRAND, SUPPLIER ID -> GTIN, VARIANT A IMAGE ->
VARIANT B, HELD PRODUCT -> SEARCH READY, UNKNOWN ATTRIBUTE -> FABRICATED VALUE.
They are marked in ``control``.

ONE CONTROL IS NOT MUTATED HERE, DELIBERATELY
---------------------------------------------
``public_projection``'s refusal to emit supplier cost is enforced twice: the
``VariantIdentity`` dataclass never captures ``cost_cents`` in the first place,
and the projection is an allowlist. There is no single-line edit that leaks the
cost, because the value is not in scope by the time the projection runs. The
nearest honest mutation -- ``PROJECTION_GROWS_A_COST_FIELD`` below -- adds a
literal, which proves the test reads the output but not that the two-layer
structure holds. The structure is argued in the module docstring instead; a
matrix entry claiming more than that would be the harness lying about itself.

WHAT IT HAS ALREADY CAUGHT
--------------------------
``STABLE_ID_PUBLISHES_THE_RAW_KEY`` survived the first full run. The suite had a
test asserting the published variant id is URL-safe and free of the ``option1``
label, and it passed against a module that emitted the raw key -- because the
fixture supplied a ``provider_variant_id``, so the id was built from the numeric
supplier id and the unsafe fallback branch was never entered. The test was
parametrised over all three cases and the mutation now dies. That is the
mechanism working, and it only works for a control somebody thought to name.

    python3 scripts/protection/catalog_semantics_mutation_matrix.py --verbose
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mutation_harness import run_matrix  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE = "services/catalog_semantics.py"
SUITE = ["tests/catalog/test_catalog_semantics.py"]


MUTATIONS = [
    dict(
        name="BRAND_BECOMES_UNBRANDED",
        control=(
            "BRIEF-MANDATED (UNKNOWN BRAND -> FAKE BRAND). brand is UNKNOWN because a "
            "recursive key census over all 20,247 supplier snapshots found no brand key. "
            "'Unbranded' is itself a claim about the manufacturer and we have no basis "
            "for it."
        ),
        path=MODULE,
        old="brand=Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE),",
        new='brand=Fact("Unbranded", Provenance.PULSESOC_NORMALIZED, Confidence.DERIVED),',
        suites=SUITE,
    ),
    dict(
        name="SKU_PROMOTED_TO_GTIN",
        control=(
            "BRIEF-MANDATED (SUPPLIER ID -> GTIN). All 3,721 production SKUs are CJ "
            "catalogue codes and zero are a valid GTIN. Publishing one as a GTIN asserts "
            "a registered identifier that belongs to somebody else."
        ),
        path=MODULE,
        old="if sku_class == IdentifierClass.GTIN",
        new="if sku_class != IdentifierClass.MISSING",
        suites=SUITE,
    ),
    dict(
        name="GTIN_CHECK_DIGIT_NOT_VERIFIED",
        control=(
            "A length-and-digits test passes a 13-digit supplier code. The check digit is "
            "what separates a GTIN from a number of the same shape."
        ),
        path=MODULE,
        old="            if _gtin_check_digit_ok(text)",
        new="            if True",
        suites=SUITE,
    ),
    dict(
        name="MPN_FILLED_FROM_THE_SKU",
        control=(
            "mpn has no source at all -- not a column, not a snapshot key. A SKU is not a "
            "manufacturer part number even when the manufacturer is unknown."
        ),
        path=MODULE,
        old="mpn=Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE),",
        new="mpn=_text_fact(sku_raw, Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED),",
        suites=SUITE,
    ),
    dict(
        name="AXIS_POSITION_RELABELLED_AS_COLOUR",
        control=(
            "BRIEF-MANDATED (UNKNOWN ATTRIBUTE -> FABRICATED VALUE). All 3,797 variant "
            "rows label axes option1/option2. Listing 209's option1 value is the "
            "boilerplate 'Picture Color' and its option3 is a plug region, so naming "
            "position 1 'color' asserts a colour named 'Picture Color'."
        ),
        path=MODULE,
        old="            name = Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE)",
        new='            name = Fact("color", Provenance.PULSESOC_NORMALIZED, Confidence.DERIVED)',
        suites=SUITE,
    ),
    dict(
        name="UNKNOWN_AXIS_NAME_NULLED_NOT_OMITTED",
        control=(
            "An omitted name says nothing. '\"name\": null' invites a consumer to decide "
            "position 1 is obviously the colour and fill it in."
        ),
        path=MODULE,
        old='for key, fact in (("name", axis.name), ("value", axis.value)):',
        new='row["name"] = emit(axis.name)\n        for key, fact in (("value", axis.value),):',
        suites=SUITE,
    ),
    dict(
        name="VARIANT_IMAGE_TAKEN_FROM_ANY_VARIANT",
        control=(
            "BRIEF-MANDATED (VARIANT A IMAGE -> VARIANT B). The supplier sends one image "
            "per variant for all 3,801, and on 191 of 197 products the colour maps to "
            "exactly one image. Showing the buyer the wrong colour is a product claim, "
            "not a layout bug."
        ),
        path=MODULE,
        old='snapshot_image=images.get(str(v.get("variant_key") or "")),',
        new="snapshot_image=next(iter(images.values()), None),",
        suites=SUITE,
    ),
    dict(
        name="PROJECTION_GROWS_A_COST_FIELD",
        control=(
            "cost_cents is populated on all 3,797 variants and must not cross a public "
            "boundary. The projection is an allowlist so a new column is private by "
            "default; this checks the test reads the output rather than the schema."
        ),
        path=MODULE,
        old='            "sku_class": v.sku_class,',
        new='            "sku_class": v.sku_class,\n            "cost_cents": 940,',
        suites=SUITE,
    ),
    dict(
        name="SEMANTIC_STATE_GAINS_AN_ELIGIBILITY_WORD",
        control=(
            "BRIEF-MANDATED (HELD PRODUCT -> SEARCH READY). 152 of 196 published, approved "
            "listings are held out of search by quantity alone. marketplace_listing_"
            "lifecycle owns eligibility; a second vocabulary here can disagree with it."
        ),
        path=MODULE,
        old='    READY = "READY"',
        new='    READY = "READY"\n    SEARCH_ELIGIBLE = "SEARCH_ELIGIBLE"',
        suites=SUITE,
    ),
    dict(
        name="UNKNOWN_SENTINEL_BECOMES_NONE",
        control=(
            "UNKNOWN is a distinct object so a consumer cannot read 'nobody asked' out of "
            "'there is nothing to ask'. As None, `fact.value or \"Unbranded\"` becomes "
            "indistinguishable from an unset optional."
        ),
        path=MODULE,
        old="UNKNOWN = _Unknown()",
        new="UNKNOWN = None",
        suites=SUITE,
    ),
    dict(
        name="SUPPLIER_KEY_LOSES_ITS_NAMESPACE",
        control=(
            "Uniqueness is enforced on (seller, provider, connection, provider_product_id) "
            "so one seller can import the same CJ product through two connections. A bare "
            "id looks unique over today's 196 rows and silently merges two offers the day "
            "that happens."
        ),
        path=MODULE,
        old='f"{prov}:{conn}:{pid}",',
        new='f"{pid}",',
        suites=SUITE,
    ),
    dict(
        name="IDLESS_SUPPLIER_KEY_COLLIDES",
        control=(
            "'cj::' is equal for every row with no provider_product_id. A collision in an "
            "identity function merges two products with nothing looking wrong."
        ),
        path=MODULE,
        old="    if not pid:\n        return Fact.unknown(Provenance.UNKNOWN)",
        new="    if False:\n        return Fact.unknown(Provenance.UNKNOWN)",
        suites=SUITE,
    ),
    dict(
        name="SUPPLIER_BREADCRUMB_BECOMES_A_PULSESOC_CATEGORY",
        control=(
            "There is no PulseSoc taxonomy: 108 distinct raw CJ breadcrumbs over 202 rows, "
            "subcategory empty on all 202. Relabelling makes a supplier taxonomy change "
            "look like a PulseSoc decision."
        ),
        path=MODULE,
        old="pulsesoc_category=Fact.unknown(Provenance.UNAVAILABLE_FROM_SOURCE),",
        new=(
            "pulsesoc_category=_text_fact(\n"
            '            listing.get("category"), Provenance.PULSESOC_NORMALIZED, Confidence.DERIVED\n'
            "        ),"
        ),
        suites=SUITE,
    ),
    dict(
        name="SUPPLIER_PROSE_RELABELLED_AS_OURS",
        control=(
            "Supplier descriptions arrive tagged untrusted_content in the snapshot. The "
            "provenance is what tells a renderer the string still needs escaping; marking "
            "it PulseSoc-derived removes the only signal that it is third-party text."
        ),
        path=MODULE,
        old='listing.get("description"), Provenance.SUPPLIER_ASSERTED, Confidence.ASSERTED',
        new='listing.get("description"), Provenance.PULSESOC_NORMALIZED, Confidence.VERIFIED',
        suites=SUITE,
    ),
    dict(
        name="AN_AI_PROVENANCE_IS_ADDED",
        control=(
            "BRIEF-ADJACENT (AI SUGGESTION -> AUTHORITATIVE FACT). There is deliberately "
            "no provenance for generated content, so a generator has nowhere to put its "
            "output that would make it read as truth."
        ),
        path=MODULE,
        old='    UNKNOWN = "UNKNOWN"',
        new='    UNKNOWN = "UNKNOWN"\n    AI_GENERATED = "AI_GENERATED"',
        suites=SUITE,
    ),
    dict(
        name="STABLE_ID_KEYS_ON_THE_OPTION_VALUES",
        control=(
            "variant_key is derived from the option values, so a supplier renaming "
            "'Sapphire Blue' to 'Blue' rewrites it. A published id that moves when a "
            "colour is renamed is not an id. The supplier variant id is present and "
            "distinct on all 3,797 rows and reused across listings on none."
        ),
        path=MODULE,
        old="            if self.provider_variant_id.known",
        new="            if False",
        suites=SUITE,
    ),
    dict(
        name="STABLE_ID_PUBLISHES_THE_RAW_KEY",
        control=(
            "Every production variant_key carries '|', '=' and often a space, and all "
            "3,797 embed the literal 'option1='. Raw, it is neither URL-safe nor free of "
            "the positional label this module refuses to name."
        ),
        path=MODULE,
        old='digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]',
        new="digest = basis",
        suites=SUITE,
    ),
    dict(
        name="VARIANT_KEY_REPUBLISHED_ALONGSIDE_THE_STABLE_ID",
        control=(
            "variant_key is listing-scoped rather than unique -- 2,571 distinct over "
            "3,797 rows -- so a consumer correlating on it merges variants across "
            "listings, and it spells out 'option1' on the way."
        ),
        path=MODULE,
        old='            "stable_id": v.stable_id,',
        new='            "stable_id": v.stable_id,\n            "variant_key": v.variant_key,',
        suites=SUITE,
    ),
    dict(
        name="A_VARIANTLESS_LISTING_IS_CALLED_READY",
        control=(
            "Six of 202 rows have no variants at all. Without this clause the model "
            "returned READY_WITH_UNKNOWNS for all 202 -- a state vocabulary that "
            "partitions nothing, which is the shape a vacuous one takes."
        ),
        path=MODULE,
        old="    if not identity.variants:\n        return SemanticState.NEEDS_VARIANT_DECISION",
        new="    if False:\n        return SemanticState.NEEDS_VARIANT_DECISION",
        suites=SUITE,
    ),
    dict(
        name="UNKNOWN_FACTS_EMIT_A_PLAUSIBLE_DEFAULT",
        control=(
            "The projection omits what it does not know. Defaulting turns every absence "
            "into an assertion at the one boundary where a crawler reads it as one."
        ),
        path=MODULE,
        old="return fact.value if fact.known else None",
        new='return fact.value if fact.known else "Unbranded"',
        suites=SUITE,
    ),
]


if __name__ == "__main__":
    sys.exit(run_matrix(ROOT, MUTATIONS))
