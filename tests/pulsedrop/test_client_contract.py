"""The commerce overlay's wire keys, checked against the clients that read them.

Why this file exists
--------------------
``hydration.overlay()`` is the only producer of the commerce attachment, and it
has three consumers: the native app (``api/pulseCommerceOverlay.ts``), the web
feed renderer (``pulse_commerce_card.js``), and the server-rendered card
(``pulse_commerce_card_html`` in ``bot.py``). Each of those was tested, each was
green, and the payload still shipped a blank product thumbnail to every client
for the whole life of the feature — the server emitted
``product.cover_image_url`` while both clients read ``product.image_url``.

Nothing caught it because nothing compared the two sides. ``test_hydration.py``
asserts the overlay against hand-written expectations, and the TypeScript tests
assert the client against hand-written fixtures. Two hand-written descriptions
of one contract agree with themselves and not with each other, and the failure
mode is silent: a missing key reads as an empty string, and an empty image URL
renders as an empty box rather than an error.

Which side moved
----------------
The clients did. ``cover_image_url`` is what the deployed server has always
emitted, so renaming it would have broken every already-corrected client and
every stored payload; instead ``pulseCommerceOverlay.ts`` now declares
``cover_image_url`` required and keeps ``image_url`` only as an optional legacy
alias, and the renderers read the real name first. That is the direction every
assertion below is written in.

This is not a historical note. The same spelling bug was fixed in the TypeScript
type and in ``pulse_commerce_card.js`` and **missed** in ``bot.py``'s
server-rendered card, which went on rendering a blank square — the copy a
crawler indexes and the copy a reader sees before any JavaScript runs. This
file's mechanism is what surfaced it. So it is a live defect detector, not a
record of a settled migration.

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

#: Fields the contract declares for reading only -- the pre-rename spellings,
#: kept optional so a payload stored before the clients moved still types. They
#: are deliberately absent from the wire, so they are exempt from "everything
#: declared is emitted" and are separately required to stay absent.
#:
#: Not every optional field belongs here. ``published_at?`` and ``evidence?``
#: are also optional and *are* emitted; exempting optionality in general would
#: silently stop checking them, which is the opposite of what this file is for.
LEGACY_ALIASES = {
    "PulseCommerceProduct": ("image_url",),
    "PulseCommerceSeller": ("seller_user_id",),
    "PulseCommerceCta": (),
    "PulseCommerceAvailability": (),
    "PulseCommerceLabel": (),
    "PulseCommerceAttribution": (),
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
        declared = _declared_fields(contract_source, alias) - set(LEGACY_ALIASES[alias])
        assert declared, f"{alias} parsed to no fields -- the regex has rotted"
        emitted = set(hydration.overlay(_row())[block])
        assert declared <= emitted, (
            f"{alias} declares {sorted(declared - emitted)} which "
            f"overlay()['{block}'] does not emit"
        )

    @pytest.mark.parametrize("alias,block", sorted(DECLARED_BLOCKS.items()))
    def test_no_legacy_alias_is_emitted(self, alias, block):
        """The aliases are a read-side courtesy, not a second wire key.

        Subtracting them above would be enough to make this file green, and
        would also let the server quietly start emitting both spellings of the
        same value -- which is how the original bug became survivable for as
        long as it did. One name on the wire; the other exists only so an
        already-stored payload still types.
        """
        emitted = set(hydration.overlay(_row())[block])
        leaked = sorted(set(LEGACY_ALIASES[alias]) & emitted)
        assert not leaked, (
            f"overlay()['{block}'] emits {leaked}, declared in {alias} as a "
            "legacy alias that this server does not send"
        )

    @pytest.mark.parametrize("alias", sorted(LEGACY_ALIASES))
    def test_each_declared_legacy_alias_is_still_declared_optional(
        self, contract_source, alias
    ):
        """Keep the hand-written exemption list honest.

        ``LEGACY_ALIASES`` subtracts from the required set, so a name left in it
        after the TS type drops the field silently exempts nothing and hides the
        next real omission behind a stale entry. Requiring each one to still be
        declared -- and declared *optional*, since a required alias would be a
        contradiction the clients could not satisfy -- makes removing the field
        from the contract turn this red instead of going unnoticed.
        """
        match = re.search(
            r"export type " + re.escape(alias) + r"\s*=\s*\{(.*?)\n\};",
            contract_source,
            re.DOTALL,
        )
        assert match is not None, f"{alias} is not declared in {CONTRACT.name}"
        for field in LEGACY_ALIASES[alias]:
            assert re.search(
                r"^\s{2}" + re.escape(field) + r"\?\s*:", match.group(1), re.MULTILINE
            ), (
                f"{alias}.{field} is exempted as a legacy alias but is no longer "
                f"an optional field of {alias} -- drop it from LEGACY_ALIASES"
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
        """The mismatch was settled by moving the clients, not the server.

        Both repairs close the gap this file exists to detect, and the choice
        between them was never about which name is nicer. `cover_image_url` is
        emitted by a server that is already deployed and already being read, so
        renaming it would have broken every client that had meanwhile been
        corrected to it -- including the two below -- while renaming the clients
        broke nothing. The column's name winning is a side effect of that, not
        the argument for it.

        So the assertion is inverted from the way this test was first written,
        and the direction is pinned in both directions: the legacy alias must
        stay absent, or a client could read it, pass, and quietly depend on a
        key the server does not send.
        """
        product = hydration.overlay(_row())["product"]
        assert product["cover_image_url"] == "https://cdn.test/lamp.jpg"
        assert "image_url" not in product

    def test_a_withdrawn_listing_still_carries_the_key(self):
        """Shape is constant across states, so a client never reads undefined."""
        product = hydration.overlay(_row(listing_status="archived"))["product"]
        assert product["cover_image_url"] == ""

    def test_the_seller_id_is_namespaced_by_its_own_block(self):
        """`seller.user_id`, and `attribution.seller_user_id`, deliberately.

        The two blocks name the same number differently because they are read
        for different reasons. Inside `seller` the owner is already established
        by the key path, so `user_id` cannot be mistaken for anyone else's. In
        `attribution` the whole point is that two ids travel together -- the
        publisher's and the merchant's -- and there the qualifier is what stops
        an analytics consumer reading one as the other.

        Pinned as inequality of *names* rather than of values: the values are
        equal and must stay equal, which is exactly why a reader who assumed the
        spellings also matched would never see their mistake in the data.
        """
        overlay = hydration.overlay(_row())
        assert overlay["seller"]["user_id"] == 10
        assert overlay["seller"]["user_id"] == overlay["attribution"]["seller_user_id"]
        assert "seller_user_id" not in overlay["seller"]


#: (overlay block, field) pairs a web card actually draws.
RENDERED = [
    ("product", "listing_id"),
    ("product", "title"),
    ("product", "price_label"),
    ("product", "cover_image_url"),
    ("seller", "store_name"),
    ("seller", "route"),
    # Not ``seller.user_id``. Both cards draw the store from ``store_name`` and
    # ``route``; only the server-rendered one needs the merchant's id, for the
    # ``data-commerce-seller`` tracking attribute. Listing it here would require
    # the JS card to read a field it has no use for -- an invented requirement,
    # and one satisfiable by naming the field in dead code. It is pinned against
    # the renderer that really does read it, in ``TestTheSellerIdAttribute``.
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

    ``field in source`` cannot see the rename this file exists for: the clients
    moved ``image_url`` -> ``cover_image_url``, and the abandoned name survives
    as a substring of the one that replaced it. A renderer still reading only
    the legacy alias would therefore satisfy a plain ``in`` check for the new
    name, which is precisely the renderer this file has to fail. So the match
    has to be bounded by something that is not an identifier character. ``\\b``
    is no help -- ``_`` is a word character, so ``\\bimage_url\\b`` matches
    inside ``cover_image_url`` too.
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


class TestTheSellerIdAttribute:
    """The second instance of the rename, in the same function as the first.

    ``pulse_commerce_card_html`` read ``seller.seller_user_id`` -- the spelling
    ``_seller`` has never emitted -- so ``safe_int(None, 0)`` stamped
    ``data-commerce-seller='0'`` onto every server-rendered commerce card for
    the life of the feature. Nothing failed: the attribute has exactly one
    writer and no reader in this repo, so a wrong value is invisible until
    something downstream tries to attribute a click.

    Asserted against the rendered markup rather than the source text, because
    the defect was not a missing read -- the function did read a seller field.
    It read the wrong one, and only the output shows that.
    """

    def _rendered(self, monolith, overlay):
        """Through ``monolith``, never a bare ``import bot``.

        Importing it here would run ``init_db()`` at whatever point this file
        happens to be collected, which is the ordering hazard the conftest
        fixture exists to take away -- see its docstring.
        """
        return monolith.pulse_commerce_card_html({"commerce": overlay})

    def _attribute(self, markup):
        match = re.search(r"data-commerce-seller='([^']*)'", markup)
        assert match, "the card no longer carries data-commerce-seller"
        return match.group(1)

    def test_the_card_carries_the_real_merchant_id(self, monolith):
        overlay = hydration.overlay(_row())
        assert overlay["seller"]["user_id"] == 10
        assert self._attribute(self._rendered(monolith, overlay)) == "10"

    def test_a_payload_predating_the_rename_does_not_regress_to_zero(self, monolith):
        """The fallback is the point: 0 is a value, not an absence.

        An attribute that is merely missing reads as a bug downstream. One that
        says ``0`` reads as a real seller, and 0 is a plausible id, so the
        failure arrives as mis-attributed clicks rather than as an error.
        """
        overlay = hydration.overlay(_row())
        overlay["seller"] = {
            key: value
            for key, value in overlay["seller"].items()
            if key != "user_id"
        }
        overlay["seller"]["seller_user_id"] = 10
        assert self._attribute(self._rendered(monolith, overlay)) == "10"

    def test_the_attribute_is_zero_when_there_is_genuinely_no_seller(self, monolith):
        """Guard the guard: a fix that hardcodes a non-zero id passes the above."""
        overlay = hydration.overlay(_row())
        overlay["seller"] = {}
        assert self._attribute(self._rendered(monolith, overlay)) == "0"


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
