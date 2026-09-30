"""The commerce overlay's wire keys, checked against the clients that read them.

Why this file exists
--------------------
``hydration.overlay()`` is the only producer of the commerce attachment, and it
has three consumers: the native app (``api/pulseCommerceOverlay.ts``), the web
feed renderer (``commerceHtml`` in ``bot.py``), and anything downstream of
either. Each of those was tested, each was green, and the payload still shipped
a blank product thumbnail to every client for the whole life of the feature —
the server emitted ``product.cover_image_url`` (the database column's name)
while both clients read ``product.image_url`` (the declared contract's name).

Nothing caught it because nothing compared the two sides. ``test_hydration.py``
asserts the overlay against hand-written expectations, and the TypeScript tests
assert the client against hand-written fixtures. Two hand-written descriptions
of one contract agree with themselves and not with each other, and the failure
mode is silent: a missing key reads as an empty string, and an empty image URL
renders as an empty box rather than an error.

So this file does not re-describe the payload. It reads the client's own
declaration out of the TypeScript source and requires the server to satisfy it.
A field renamed on either side fails here, which is the only place the rename
is visible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.pulsedrop import hydration

REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "mobile-native" / "src" / "api" / "pulseCommerceOverlay.ts"

#: TypeScript type alias -> the overlay block it describes.
DECLARED_BLOCKS = {
    "PulseCommerceProduct": "product",
    "PulseCommerceSeller": "seller",
    "PulseCommerceCta": "cta",
    "PulseCommerceAvailability": "availability",
    "PulseCommerceLabel": "label",
    "PulseCommerceAttribution": "attribution",
}


def _row(**overrides):
    """An available listing, matching ``test_hydration._row``."""
    base = {
        "publication_id": 5,
        "post_id": 900,
        "surface": "signal",
        "ref_listing_id": 77,
        "ref_seller_user_id": 10,
        "editorial_label": "TRENDING",
        "published_at": "2026-03-01T10:00:00",
        "listing_id": 77,
        "listing_seller_user_id": 10,
        "listing_title": "Aurora Desk Lamp",
        "listing_price_label": "$49.00",
        "listing_currency": "USD",
        "listing_quantity": 4,
        "listing_product_type": "physical",
        "listing_listing_type": "physical",
        "listing_status": "published",
        "listing_approval_status": "approved",
        "listing_cover_image_url": "https://cdn.test/lamp.jpg",
        "listing_category": "home",
        "seller_status": "approved",
        "seller_store_name": "Northlight Studio",
        "seller_username": "northlight",
        "seller_account_name": "Ann",
    }
    base.update(overrides)
    return base


def _declared_fields(source: str, alias: str) -> set[str]:
    """The property names of one exported TS type alias.

    Deliberately a regex over the source rather than a parse: the alternative is
    a TypeScript toolchain in a pytest run, and the declarations here are plain
    ``name: type;`` lines. Optional markers are stripped so ``published_at?``
    counts as ``published_at``; comments and blank lines carry no ``:`` at the
    start of a line and drop out.
    """
    match = re.search(
        r"export type " + re.escape(alias) + r"\s*=\s*\{(.*?)\n\};",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"{alias} is not declared in {CONTRACT.name}")
    body = match.group(1)
    # Strip block comments so a field name inside prose is not mistaken for a
    # declaration. The docblocks in this file are long and mention field names.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r"^\s{2}([a-z_][a-z0-9_]*)\??\s*:", body, re.MULTILINE))


@pytest.fixture(scope="module")
def contract_source() -> str:
    assert CONTRACT.exists(), f"client contract missing at {CONTRACT}"
    return CONTRACT.read_text(encoding="utf-8")


class TestTheServerSatisfiesTheClientContract:
    """Every field the app declares must be present on the wire."""

    @pytest.mark.parametrize("alias,block", sorted(DECLARED_BLOCKS.items()))
    def test_every_declared_field_is_emitted(self, contract_source, alias, block):
        declared = _declared_fields(contract_source, alias)
        assert declared, f"{alias} parsed to no fields -- the regex has rotted"
        emitted = set(hydration.overlay(_row())[block])
        assert declared <= emitted, (
            f"{alias} declares {sorted(declared - emitted)} which "
            f"overlay()['{block}'] does not emit"
        )

    def test_the_parser_can_fail(self, contract_source):
        """Guard the guard: a regex that matches nothing passes vacuously."""
        with pytest.raises(AssertionError):
            _declared_fields(contract_source, "PulseCommerceNotAThing")


class TestTheThumbnailRegression:
    """The specific break this file was written for.

    Pinned by name rather than left to the parametrized sweep above, because
    the sweep passes the moment someone deletes the field from the TS type --
    which is the wrong repair and an easy one to reach for.
    """

    def test_the_product_image_is_named_for_the_wire_not_the_column(self):
        product = hydration.overlay(_row())["product"]
        assert product["image_url"] == "https://cdn.test/lamp.jpg"
        assert "cover_image_url" not in product

    def test_a_withdrawn_listing_still_carries_the_key(self):
        """Shape is constant across states, so a client never reads undefined."""
        product = hydration.overlay(_row(listing_status="archived"))["product"]
        assert product["image_url"] == ""

    def test_the_seller_id_is_named_as_the_attribution_block_names_it(self):
        overlay = hydration.overlay(_row())
        assert overlay["seller"]["seller_user_id"] == 10
        assert overlay["seller"]["seller_user_id"] == overlay["attribution"]["seller_user_id"]
        assert "user_id" not in overlay["seller"]


#: (overlay block, field) pairs a web card actually draws.
RENDERED = [
    ("product", "listing_id"),
    ("product", "title"),
    ("product", "price_label"),
    ("product", "image_url"),
    ("seller", "store_name"),
    ("seller", "route"),
    ("seller", "seller_user_id"),
    ("cta", "route"),
    ("cta", "enabled"),
    ("availability", "fallback"),
    # The overlay ships a translation key beside every fallback, and the native
    # card renders through it. A web card that reads only the fallback is a card
    # that is English for every reader, under a post that is not -- so the key is
    # as required a read as the string it replaces.
    ("label", "i18n_key"),
    ("cta", "i18n_key"),
    ("availability", "i18n_key"),
]

#: Every web surface that builds the commerce card's markup, and the slice of
#: source each one lives in. Three, and the reason for each is in its own file:
#: the two feed renderers cannot both be loaded (the inline shell runtime is
#: stripped under the default ``core`` boot profile), and the permalink has to
#: emit HTML server-side because it is the only indexable post surface.
WEB_RENDERERS = {
    "pulse_commerce_card.js": (
        Path("static") / "js" / "pulse_commerce_card.js",
        # The whole module: the payload reads are split across `overlayOf` and
        # `html` on purpose, so slicing to one function would miss half of them.
        r".*",
    ),
    "pulse_commerce_card_html": (
        Path("bot.py"),
        r"def pulse_commerce_card_html\(post\):.*?\n\n\n",
    ),
}


def _without_comments(source: str) -> str:
    """Prose is not a read.

    Both renderers carry docblocks that name the fields they handle and explain
    why. Left in, every assertion below would pass on the comment alone, and the
    test would go green against a renderer that draws nothing.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    source = re.sub(r"^\s*(//|#).*$", "", source, flags=re.MULTILINE)
    return source


def _reads(source: str, field: str) -> bool:
    """Whether ``source`` reads exactly ``field``, not a name containing it.

    ``field in source`` cannot see the rename this file exists for: the break
    was ``image_url`` -> ``cover_image_url``, and the old name survives as a
    substring of the new one. So the match has to be bounded by something that
    is not an identifier character. ``\\b`` is no help -- ``_`` is a word
    character, so ``\\bimage_url\\b`` matches inside ``cover_image_url`` too.
    """
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(field) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, source) is not None


class TestEveryWebRendererReadsTheSameKeys:
    """The web cards are checked as text, against the payload they are given.

    Not a substitute for rendering them -- text cannot catch a logic error -- but
    it does catch the failure this file is about: one surface drifting onto a
    different key name while each remains internally consistent. There is more
    than one web renderer and they are in two languages, so "they agree today" is
    not something any single-surface test can observe.
    """

    @pytest.fixture(scope="module", params=sorted(WEB_RENDERERS))
    def renderer(self, request) -> str:
        relative, pattern = WEB_RENDERERS[request.param]
        source = (REPO / relative).read_text(encoding="utf-8", errors="replace")
        match = re.search(pattern, source, re.DOTALL)
        assert match, f"{request.param} is gone from {relative} -- that surface is unrendered"
        rendered = _without_comments(match.group(0))
        assert "pulse-commerce-cta" in rendered, f"{request.param} draws no card"
        return rendered

    @pytest.mark.parametrize("block,field", RENDERED)
    def test_the_field_this_card_reads_is_emitted(self, renderer, block, field):
        assert _reads(renderer, field), f"this web card does not read {block}.{field}"
        assert field in hydration.overlay(_row())[block]

    def test_the_reader_can_fail(self, renderer):
        """Guard the guard, as above: the break was a prefix rename."""
        assert not _reads(renderer, "image_ur")
        assert not _reads("var x = product.cover_image_url;", "image_url")


class TestTheInlineRuntimeDelegates:
    """`postHtml` must not grow its own card back.

    The inline shell runtime and `pulse_home_core.js` render the same feed on two
    boot profiles and never coexist, so a card written into one is a card the
    other silently lacks -- with both surfaces' tests still green. The only way
    that stays true is if neither builds the markup itself.
    """

    @pytest.fixture(scope="class")
    def bot_source(self) -> str:
        return (REPO / "bot.py").read_text(encoding="utf-8", errors="replace")

    def test_the_shell_runtime_calls_the_shared_module(self, bot_source):
        match = re.search(r"function commerceHtml\(p\)\{.*?\n", bot_source)
        assert match, "commerceHtml is gone from bot.py -- the shell feed card is unrendered"
        assert "PulseCommerceCard" in match.group(0), (
            "the inline runtime builds its own card again; pulse_home_core.js "
            "renders the default boot profile and would not get the change"
        )

    def test_the_post_card_places_commerce_between_media_and_actions(self, bot_source):
        match = re.search(r"function postHtml\(p\)\{.*?\n", bot_source)
        assert match, "postHtml is gone from bot.py"
        body = match.group(0)
        assert "${commerceHtml(p)}" in body, "postHtml does not render the commerce attachment"
        assert body.index("mediaHtml") < body.index("${commerceHtml(p)}") < body.index("quick-actions")

    def test_the_default_boot_profile_renderer_places_it_too(self):
        source = (REPO / "static" / "js" / "pulse_home_core.js").read_text(encoding="utf-8")
        match = re.search(r"function renderPost\(post\) \{.*?\n  \}", source, re.DOTALL)
        assert match, "renderPost is gone from pulse_home_core.js"
        body = match.group(0)
        assert "PulseCommerceCard" in body, "the default web feed renders no commerce attachment"
        assert body.index("appendChild(media)") < body.index("PulseCommerceCard") < body.index("renderEngagement")
