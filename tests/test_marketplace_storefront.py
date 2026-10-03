"""The web storefront's derivation and rendering layers, tested without a server.

`services/marketplace_web.py` (facts) and `services/marketplace_storefront.py`
(markup) import neither Flask nor `bot`, which is what makes this file a
sub-second unit suite instead of a boot-the-monolith integration suite. The
route-level behaviour those two modules feed is covered elsewhere:
`tests/web_parity/test_marketplace_listing_links.py` for visibility and shared
links, `tests/test_marketplace_web_ctas_follow_the_registry.py` for where the
buttons go.

## What this file is mostly about

The storefront was built from a pair of design references showing a mature
commerce catalogue: star ratings, "1.2K sold", "Trusted Seller", "Free
Shipping", "3-7 business days", "20% OFF", "BEST SELLER". PulseSoc's marketplace
has no review table, no order history feeding the catalogue, no compare-at price
column, no shipping-policy column and no sales counter. Every one of those
elements would therefore have been a decoration wearing the costume of a fact,
and the ones that look most like data -- a rating out of five, a sold count --
are the ones a shopper is least able to check.

So the majority of the tests below are *absence* tests, and they are written to
fail loudly rather than quietly: several assert that a function given the most
tempting possible input still declines to invent the number. An absence test is
easy to write in a way that passes forever by accident, so each one is paired
with a positive case proving the same code path does emit the element when a
real source exists.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services import app_links  # noqa: E402
from services import marketplace_storefront as sf  # noqa: E402
from services import marketplace_web as mw  # noqa: E402

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)


def listing(**overrides):
    """A minimal live-shaped listing row. Only `id` and `title` are load-bearing."""
    row = {
        "id": 4271,
        "title": "Ribbed Cotton Crew Sock, 3 Pair",
        "description": "Soft combed cotton. Machine wash cold.",
        "category": "Clothing > Socks",
        "currency": "USD",
        "price_label": "$12.00",
        "quantity": 4,
        "seller_user_id": 1,
        "seller_store_name": "Ada Goods",
        "seller_username": "ada",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# 1. Real data only -- the rule the whole storefront is organised around
# ---------------------------------------------------------------------------

#: Copy from the design references that no PulseSoc column can support.
#:
#: Split by why each one is unsupported, because the fix differs. A rating needs
#: a review table that does not exist; a shipping estimate needs a policy column
#: that does not exist; a sold count needs the order tables to feed this
#: catalogue, which they do not. None is a formatting problem.
FABRICATED_COPY = (
    "Trusted Seller",
    "Free Shipping",
    "BEST SELLER",
    "Best Seller",
    "TRENDING",
    "Top Rated",
    "20% OFF",
    "business days",
    "sold",
    "out of 5",
    "reviews",
)


def _literal_strings(path):
    """Every string literal in a module that is not a docstring or a comment.

    Scanning the raw file text instead would flag this module's own prose: the
    docstrings here *name* the fabricated copy in order to explain why it is
    absent, and several say "BEST SELLER" outright. A text scan would read those
    explanations as the thing they warn about -- the same confusion that makes
    `services/route_auth.py` classify a commented-out gate as a real one.

    Comments need no special handling: `ast` never emits them.
    """
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_module_ships_a_string_the_catalogue_cannot_source():
    """The headline rule, checked against the code that emits the markup.

    This is the one test in the file that would catch a regression nobody
    reviewed: adding `<span>Free Shipping</span>` to a card is a one-line change
    that looks like polish, renders beautifully, and is a lie about a policy
    PulseSoc has never recorded.
    """
    offenders = []
    for module in (mw.__file__, sf.__file__):
        for text in _literal_strings(module):
            for phrase in FABRICATED_COPY:
                if phrase in text:
                    offenders.append(f"{os.path.basename(module)}: {phrase!r} in {text[:80]!r}")
    assert offenders == [], (
        "These string literals claim something no column in this catalogue can "
        "establish:\n  " + "\n  ".join(offenders)
        + "\n\nIf a real source now exists, derive the value in "
        "marketplace_web.py and render it only when the source answers -- do not "
        "hardcode the phrase. If there is still no source, omit the element."
    )


def test_the_fabricated_copy_list_would_actually_catch_something():
    """Anti-vacuity for the scan above.

    A scan whose needle list has drifted out of the codebase's vocabulary passes
    forever. This proves the matcher is live by running it over text that is not
    in the modules.
    """
    sample = '<span class="mkt-badge">BEST SELLER</span><p>Free Shipping</p>'
    hits = [phrase for phrase in FABRICATED_COPY if phrase in sample]
    assert "BEST SELLER" in hits and "Free Shipping" in hits


def test_the_literal_scanner_ignores_prose_but_reads_markup():
    """Guard the scanner's own discrimination.

    If `_literal_strings` returned docstrings too, the test above would fail on
    this file's own explanations and somebody would "fix" it by shortening the
    needle list -- turning a real gate into a decorative one.
    """
    strings = _literal_strings(mw.__file__)
    joined = " ".join(strings)
    assert "BEST SELLER" not in joined, (
        "the scanner is picking up docstrings; classify_badges' docstring names "
        "BEST SELLER in order to explain why it is not emitted")
    assert any("Featured" == s for s in strings), (
        "the scanner found no real emitted literals, so it is reading nothing")


def test_only_the_three_provable_badges_exist():
    """Badge vocabulary is closed, and each member is one column read."""
    assert {mw.BADGE_NEW, mw.BADGE_FEATURED, mw.BADGE_DIGITAL} == {"new", "featured", "digital"}
    badges = mw.classify_badges(listing(featured=1, product_type="digital",
                                        published_at="2026-09-20T00:00:00Z"), now=NOW)
    assert [b.label for b in badges] == ["Featured", "New", "Digital"]


def test_a_badge_is_absent_when_its_column_is():
    """The same call with nothing to prove emits nothing."""
    assert mw.classify_badges(listing(), now=NOW) == []


def test_new_reads_published_at_and_never_falls_back_to_created_at():
    """`created_at` dates the import, not the product.

    29 of 47 live rows have a null `published_at` and a `created_at` from one
    bulk supplier run, so a fallback would have put "New" on most of the
    catalogue on the same day -- a fact about PulseSoc's ingest pipeline
    presented as a fact about the product.
    """
    fresh = mw.classify_badges(listing(published_at="2026-09-25T00:00:00Z"), now=NOW)
    assert [b.key for b in fresh] == [mw.BADGE_NEW]
    imported = mw.classify_badges(
        listing(published_at=None, created_at="2026-09-25T00:00:00Z"), now=NOW)
    assert imported == [], "New was derived from created_at, which dates the import"


def test_new_expires_on_its_own_window():
    stale = NOW - mw.NEW_LISTING_WINDOW - timedelta(days=1)
    assert mw.classify_badges(listing(published_at=stale.isoformat()), now=NOW) == []


def test_a_recency_badge_most_of_the_page_wears_is_suppressed():
    """True and useless is still useless.

    "New" on 13 of 15 cards distinguishes nothing and reads as decoration, which
    is what the real data was supposed to replace.
    """
    new = [mw.Badge(mw.BADGE_NEW, "New")]
    many = {i: list(new) for i in range(9)}
    many.update({i: [] for i in range(9, 12)})
    kept = mw.suppress_uninformative_badges(many)
    assert all(not badges for badges in kept.values()), (
        "New survived on a page where most products carry it")


def test_suppression_leaves_a_genuinely_rare_badge_alone():
    """The positive half, so the suppressor cannot pass by deleting everything."""
    by_id = {0: [mw.Badge(mw.BADGE_NEW, "New")]}
    by_id.update({i: [] for i in range(1, 10)})
    kept = mw.suppress_uninformative_badges(by_id)
    assert [b.key for b in kept[0]] == [mw.BADGE_NEW]


def test_featured_and_digital_are_never_suppressed():
    """Neither is a recency claim: one is a per-row decision, one changes delivery."""
    by_id = {i: [mw.Badge(mw.BADGE_FEATURED, "Featured"), mw.Badge(mw.BADGE_DIGITAL, "Digital")]
             for i in range(10)}
    kept = mw.suppress_uninformative_badges(by_id)
    assert all(len(b) == 2 for b in kept.values())


def test_unknown_stock_renders_no_line_rather_than_either_answer():
    """`stock_state` is three-valued and the third value is the common one."""
    variants = [{"status": "active", "stock_state": "UNKNOWN", "price_cents": 1200}]
    assert mw.stock_line(listing(quantity=None), variants) == ""


def test_stock_says_in_stock_only_when_something_says_so():
    assert mw.stock_line(listing(quantity=3)) == "In stock"
    assert mw.stock_line(listing(quantity=0)) == "Out of stock"
    assert mw.stock_line(listing(quantity="")) == ""
    assert mw.stock_line(listing(quantity="many")) == ""


def test_a_digital_product_has_no_stock_line_at_all():
    """Inventory is a physical-goods concept; "In stock" on a download is noise."""
    assert mw.stock_line(listing(product_type="digital", quantity=1)) == ""


def test_sorting_offers_no_mode_without_a_column_behind_it():
    """No "Most popular" and no "Top rated": no view counter, no reviews."""
    assert set(mw.SORT_KEYS) == {"featured", "newest", "price_asc", "price_desc"}
    labels = " ".join(label for _, label in mw.SORT_OPTIONS).lower()
    for banned in ("popular", "rated", "rating", "review", "best selling", "trending"):
        assert banned not in labels, f"sort offers {banned!r} with nothing to rank by"


# ---------------------------------------------------------------------------
# 2. Price: one authority, never the client
# ---------------------------------------------------------------------------


def test_variants_outrank_the_sellers_typed_label():
    """`price_label` is display text a human typed; variant cents is what charges."""
    view = mw.derive_price(listing(price_label="$99.00"),
                           [{"status": "active", "price_cents": 1200}])
    assert view.source == "variants"
    assert view.display == "$12.00"


def test_the_label_is_used_only_when_there_are_no_variants():
    view = mw.derive_price(listing(price_label="$12.50"))
    assert (view.source, view.display) == ("label", "$12.50")


def test_an_unparseable_label_is_no_price_rather_than_zero():
    """Zero is a price. "Ask me" is not."""
    view = mw.derive_price(listing(price_label="Ask me"))
    assert view.source == "none" and not view.known and view.display == ""


def test_a_paused_variant_does_not_set_the_price():
    view = mw.derive_price(listing(price_label=None), [
        {"status": "paused", "price_cents": 100},
        {"status": "active", "price_cents": 2500},
    ])
    assert view.display == "$25.00"


def test_a_spread_of_variant_prices_renders_as_a_range():
    view = mw.derive_price(listing(), [
        {"status": "active", "price_cents": 1200},
        {"status": "active", "price_cents": 1800},
    ])
    assert view.is_range and view.display == "$12.00 – $18.00"


def test_structured_data_mirrors_the_page_exactly():
    """A wrong number here is invisible to its author and visible to everyone."""
    single = mw.derive_price(listing(price_label="$12.00")).as_schema_offer()
    assert single == {"@type": "Offer", "priceCurrency": "USD", "price": "12.00"}
    ranged = mw.derive_price(listing(), [
        {"status": "active", "price_cents": 1200},
        {"status": "active", "price_cents": 1800},
    ]).as_schema_offer()
    assert ranged["@type"] == "AggregateOffer"
    assert (ranged["lowPrice"], ranged["highPrice"]) == ("12.00", "18.00")


def test_no_price_means_no_offer_node():
    """An Offer with no price is a structured-data error, not an empty field."""
    assert mw.derive_price(listing(price_label="")).as_schema_offer() is None


def test_an_unknown_currency_code_still_renders_a_number():
    """Falls back to the code rather than guessing a symbol."""
    assert mw.format_money(1200, "PLN") == "PLN 12.00"
    assert mw.format_money(1200, "GBP") == "£12.00"
    assert mw.format_money(None) == ""


# ---------------------------------------------------------------------------
# 3. Product image quality
# ---------------------------------------------------------------------------


def test_an_image_is_never_stretched_or_blind_cropped():
    """Geometry is the box's job; the image is contained inside it.

    This catalogue has no focal-point data and no safe-crop region, so
    `object-fit: cover` would silently behead tall products. The contract is
    asserted where it lives -- the stylesheet -- because the markup cannot
    express it.
    """
    with open(os.path.join(ROOT, "static/css/pulse_marketplace.css"), encoding="utf-8") as handle:
        css = handle.read()
    assert "object-fit: contain" in css
    assert "aspect-ratio" in css, "the box must reserve its geometry before bytes arrive"


def test_every_image_declares_its_loading_and_decoding():
    below = sf.media_box(mw.MediaItem(url="https://cdn/x.jpg", kind="image"), alt="Sock")
    assert 'loading="lazy"' in below and 'decoding="async"' in below
    assert "fetchpriority" not in below


def test_the_largest_contentful_image_is_not_lazy():
    """A lazy LCP image is slower than no optimisation at all."""
    hero = sf.media_box(mw.MediaItem(url="https://cdn/x.jpg", kind="image"),
                        alt="Sock", eager=True)
    assert 'loading="eager"' in hero and 'fetchpriority="high"' in hero


CJ_IMAGE = "https://cf.cjdropshipping.com/17007840/1727919878964383744.jpg"


def test_no_srcset_is_emitted_against_an_origin_that_cannot_resize():
    """An unknown origin keeps the markup it had, with or without a `sizes`.

    A host that does not understand the directive answers **400, not the
    original** -- measured against both CJ hosts with a bogus operation. A
    `srcset` candidate that 400s does not fall back to `src`, so a
    try-everything would turn one heavy image into no image. `sizes` without
    `srcset` does nothing, so it is absent too.

    `https://cdn/...` is deliberately not an allowlisted host. That makes this a
    real assertion rather than a restatement of the default -- the companion
    test below drives the same function with a host that *is*.
    """
    for kwargs in ({}, {"sizes": sf.CARD_SIZES}):
        html = sf.media_box(mw.MediaItem(url="https://cdn/x.jpg", kind="image"),
                            alt="Sock", **kwargs)
        assert "srcset" not in html and "sizes=" not in html
        assert "<picture" not in html


def test_a_resizable_origin_gets_variants_and_keeps_the_original_as_src():
    """The whole point, and the two things that must survive it.

    `src` stays the unmodified supplier URL, so the no-`srcset` path and
    anything that reads `src` rather than the candidate list -- Google Images,
    the structured-data image URL -- see exactly what they saw before.
    """
    html = sf.media_box(mw.MediaItem(url=CJ_IMAGE, kind="image"),
                        alt="Sock", sizes=sf.CARD_SIZES)
    assert f'src="{CJ_IMAGE}"' in html, "the original must remain the fallback"
    assert 'type="image/webp"' in html and html.count("<picture") == 1
    assert html.count("<img") == 1, "a <source> is not a second request"
    for width in (200, 400, 600, 800):
        assert f"w_{width} {width}w" in html
        assert f"w_{width}%2Fformat%2Cwebp {width}w" in html
    assert sf.CARD_SIZES in html


def test_a_width_descriptor_is_never_emitted_without_a_sizes():
    """`srcset` with `w` descriptors and no `sizes` means `100vw`.

    For the 64px gallery thumbnail that is a ~6x over-fetch, so the variants are
    opt-in per call site and a caller that has not worked out its box width gets
    the markup it got before rather than a guess.
    """
    html = sf.media_box(mw.MediaItem(url=CJ_IMAGE, kind="image"), alt="Sock")
    assert "srcset" not in html and "sizes=" not in html


def test_variants_are_refused_for_anything_that_would_break_the_url():
    """Each of these would produce a candidate that does not resolve."""
    refused = {
        "http": "http://cf.cjdropshipping.com/a.jpg",
        "foreign host": "https://evil.example/a.jpg",
        "explicit port": "https://cf.cjdropshipping.com:8443/a.jpg",
        # A second `?` is not a query string, and a signed URL must not be
        # rewritten even when the host is right.
        "already has a query": "https://cf.cjdropshipping.com/a.jpg?sig=1",
        "fragment": "https://cf.cjdropshipping.com/a.jpg#x",
        # Animated: a resize flattens it to frame one.
        "gif": "https://cf.cjdropshipping.com/a.gif",
        "no pixels to resize": "https://cf.cjdropshipping.com/a.svg",
    }
    for why, url in refused.items():
        assert sf._variant_srcset(url) == "", why
    # The host comparison is case-insensitive and the extension test is too,
    # because neither is under our control.
    assert sf._variant_srcset("https://CF.CJDropshipping.com/a.JPG")


def test_the_commas_in_the_directive_are_percent_encoded():
    """Both forms are byte-identical at the CDN; one is unambiguous in HTML.

    `srcset` is a comma-separated list, so a reader of `resize,w_400 400w`
    has to know the parsing rule well enough to say whether that is one
    candidate or two. `%2C` removes the question.
    """
    srcset = sf._variant_srcset(CJ_IMAGE)
    assert "," in srcset, "candidates are still comma-separated"
    assert "resize,w_" not in srcset and "format,webp" not in srcset
    # One comma per gap between candidates, and none inside them.
    assert srcset.count(",") == len(sf._RESIZE_WIDTHS) - 1


def test_a_missing_photo_is_absence_and_a_dead_url_is_failure():
    """Two different states, because they are two different facts.

    A listing with no photograph is normal. A listing whose CDN URL is dead is a
    problem, and must not render as the browser's broken-image glyph.
    """
    empty = sf.media_box(None, alt="Sock")
    assert "is-empty" in empty and "No photo yet" in empty
    live = sf.media_box(mw.MediaItem(url="https://cdn/x.jpg", kind="image"), alt="Sock")
    assert "Image unavailable" in live, (
        "the failure plate must be in the markup already; a script cannot insert "
        "it for an image that failed before the listener bound")


def test_alt_text_carries_the_product_and_is_escaped():
    html = sf.media_box(mw.MediaItem(url="https://cdn/x.jpg", kind="image"),
                        alt='Sock "quoted" <b>')
    assert 'alt="Sock &quot;quoted&quot; &lt;b&gt;"' in html


def test_the_gallery_renders_the_media_that_exists_and_does_not_pad():
    """41 of 47 live listings have exactly one image.

    The reference shows a seven-thumbnail rail. Filling it would mean repeating
    the one image or inventing frames.
    """
    items = mw.gallery_items({"cover_image_url": "https://cdn/a.jpg"})
    assert [i.url for i in items] == ["https://cdn/a.jpg"]
    html = sf.gallery_html(items, title="Sock")
    assert html.count("<img") == 1


def test_gallery_sources_are_merged_without_duplicating_a_url():
    payload = {
        "media": [{"media_url": "https://cdn/a.jpg", "media_type": "image"}],
        "cover_image_url": "https://cdn/a.jpg",
        "gallery_json": json.dumps(["https://cdn/b.jpg", "https://cdn/a.jpg"]),
    }
    assert [i.url for i in mw.gallery_items(payload)] == ["https://cdn/a.jpg", "https://cdn/b.jpg"]


def test_a_corrupt_gallery_json_does_not_lose_the_cover():
    """Supplier JSON is not trustworthy; the cover column is still readable."""
    items = mw.gallery_items({"cover_image_url": "https://cdn/a.jpg", "gallery_json": "{not json"})
    assert [i.url for i in items] == ["https://cdn/a.jpg"]


def test_the_gallery_is_capped():
    payload = {"gallery_json": json.dumps([f"https://cdn/{n}.jpg" for n in range(40)])}
    assert len(mw.gallery_items(payload, limit=8)) == 8


def _css_block(css, selector):
    """The body of the first rule whose selector list contains `selector`.

    Brace-counted rather than matched with `[^}]*`, so a nested block inside a
    media query cannot truncate the answer at the wrong `}`.
    """
    for match in re.finditer(re.escape(selector) + r"\s*\{", css):
        depth, index = 1, match.end()
        while depth and index < len(css):
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        return css[match.end():index - 1]
    return None


def test_a_gallery_arrow_is_not_widened_by_the_shells_phone_button_rule():
    """The arrows sit in a row and must not obey a rule written for a stack.

    `pulse_mobile_system.css` gives every `button` on the site `width: 100%`
    below 768px. That is right for an action that stacks and wrong for a
    control with siblings: on a 390px screen the two gallery arrows grew to
    172px each, took the whole row between them, and left the counter 11px in
    which to render "1 / 3" -- so it wrapped onto three lines.

    The shell rule is deliberately not narrowed; pages outside the storefront
    still lean on it. The storefront opts its own arrows out instead, and two
    class names outrank a bare `button` with no need for `!important`.
    """
    with open(os.path.join(ROOT, "static/css/pulse_mobile_system.css"), encoding="utf-8") as handle:
        shell = handle.read()
    phone = _css_block(shell, "@media (max-width: 768px)")
    assert phone, "the shell's phone breakpoint is gone; this override's premise needs rechecking"
    # The premise, asserted rather than assumed. If the shell ever stops
    # stretching bare buttons this override becomes dead weight, and whoever
    # removes the shell rule should be the one told about it.
    stretched = _css_block(phone, "button")
    assert stretched and "width: 100%" in stretched, (
        "the shell rule this override exists to beat is gone")

    with open(os.path.join(ROOT, "static/css/pulse_marketplace.css"), encoding="utf-8") as handle:
        css = handle.read()
    override = _css_block(css, ".mkt-gallery-nav .mkt-ghost")
    assert override, "the arrows are left to the shell's full-width rule"
    assert "width:" in override and "100%" not in override
    counter = _css_block(css, ".mkt-gallery-counter")
    assert "nowrap" in counter, "the counter can still lose its line to a squeeze"


def test_a_thumbnail_is_sized_by_a_rule_that_can_actually_apply_to_it():
    """The rail's flex items are the `<li>`s, not the anchors inside them.

    `.mkt-gallery-thumb` declared `flex: 0 0 auto; width: 64px` on an `<a>` that
    was neither a flex child nor a block box, so both declarations were inert:
    `flex` belonged to the wrapper and `width` does not apply to an inline
    element at all. The thumbs took their size from the image instead, and the
    anchor's border painted on the line box -- a stray vertical rule taller
    than the picture, beside every thumbnail.

    Asserted as the pair it has to be. Sizing the anchor without making it a
    block, or making it a block while leaving the wrapper unsized, each puts
    the rail back where it was.
    """
    with open(os.path.join(ROOT, "static/css/pulse_marketplace.css"), encoding="utf-8") as handle:
        css = handle.read()
    item = _css_block(css, ".mkt-gallery-rail > li")
    assert item and "flex:" in item, "the rail's real flex item carries no sizing"
    thumb = _css_block(css, ".mkt-gallery-thumb")
    assert "width:" in thumb, "the thumbnail has no width of its own"
    assert re.search(r"display:\s*(block|flex|grid|inline-block|inline-flex)", thumb), (
        "an inline anchor ignores the width above it")


def test_a_thumbnail_is_announced_as_a_tab_of_its_rail():
    """A `tablist` has to own its `tab` children directly.

    The `<li>` wrapper keeps the rail a real list in markup, but left unmarked
    it lands between the two as a generic element and breaks the relationship,
    so the thumbs are announced without their position in the set.
    """
    items = mw.gallery_items({"gallery_json": json.dumps(
        ["https://cdn/a.jpg", "https://cdn/b.jpg"])})
    html = sf.gallery_html(items, title="Sock")
    assert html.count('<li role="presentation">') == 2
    # Paired with the positive case, so a rail that stopped emitting tabs
    # altogether could not pass the line above by having nothing to wrap.
    assert html.count('role="tab"') == 2
    assert 'role="tablist"' in html


def test_a_card_emits_no_empty_element_for_a_field_it_has_no_data_for():
    """Omission has to be real omission, not an empty box.

    `.mkt-card-body` is a flex column with a gap, so every element it emits costs
    one gap whether or not it contains anything. An always-present wrapper for an
    optional field therefore makes card geometry depend on which *sibling* fields
    happen to be populated -- the exact non-uniformity `product_card` claims not
    to have. The same rule already governs the price and the stock line; this
    test holds all three to it, so the next optional field added to a card cannot
    reintroduce the empty wrapper.
    """
    bare = sf.product_card(listing(price_label="", quantity=""),
                           price=mw.derive_price(listing(price_label="")))
    for empty in ('<div class="mkt-card-meta"></div>',
                  '<p class="mkt-card-price"></p>',
                  '<p class="mkt-card-stock"></p>'):
        assert empty not in bare, f"rendered {empty} for a field with no data"
    assert not re.search(r'<(div|p|span|ul)\b[^>]*></\1>', bare), (
        "some element rendered with no content at all")

    # And each one appears the moment there is something to put in it, so the
    # assertions above are not passing because the fields were dropped outright.
    full = sf.product_card(listing(seller_store_name="Sock Co"),
                           price=mw.derive_price(listing()), stock="In stock")
    assert '<div class="mkt-card-meta">' in full
    assert '<p class="mkt-card-price' in full
    assert '<p class="mkt-card-stock' in full


def test_a_card_takes_its_product_link_from_the_registry_not_from_a_literal():
    """Where a card points is `app_links`' decision, not this module's.

    `services/app_links.py` is the single authority on Marketplace
    destinations, and each `Destination` carries a `web_equivalent` flag. Today
    `website_href("product", 42, source="web")` returns
    `/pulse/marketplace/42`; if that flag is ever cleared the same call returns
    the `/open/product/42` interstitial instead. An f-string here would keep
    emitting the web path straight through that flip -- and would go on
    *working*, linking members to a page the registry had already decided not
    to send them to, with nothing failing to say so.

    Which is why this asserts by substitution rather than by value. A literal
    produces character-for-character what the registry produces today, so
    `product_path(42) == "/pulse/marketplace/42"` passes just as happily for a
    module that never consults the registry at all. Replacing the function and
    checking both that the sentinel came back *and* that the right arguments
    went in is the only form of this test that can fail for the right reason.
    """
    sentinel = "/open/product/__FROM_THE_REGISTRY__"
    real = app_links.website_href
    calls = []

    def fake(destination, resource_id=None, source="web"):
        calls.append((destination, resource_id, source))
        return sentinel

    app_links.website_href = fake
    try:
        answer = sf.product_path(42)
        card = sf.product_card(listing(), price=mw.derive_price(listing()))
    finally:
        app_links.website_href = real

    assert answer == sentinel, (
        f"product_path built {answer!r} itself instead of asking the registry"
    )
    assert calls and calls[0] == ("product", 42, "web"), (
        f"asked the registry the wrong question: {calls!r}"
    )
    assert f'href="{sentinel}"' in card, (
        "the card's link did not come through product_path, so the assertion "
        "above only covers a function nothing calls"
    )

    # And the real registry still answers with the web path, so a change that
    # routed members to the interstitial would surface here too.
    assert real("product", 42, source="web") == "/pulse/marketplace/42"


def test_an_unpriced_card_asks_the_stylesheet_to_hold_the_price_slot_open():
    """Omitting the price must not move the lines underneath it.

    Found by measuring a rendered grid in a browser, not by reading: with the
    price element simply absent, an unpriced listing showed its seller name on
    the row where every priced card beside it showed a price -- a 42px step, and
    the one geometry difference left after the empty-wrapper fix above. The
    renderer cannot solve it by emitting a placeholder (that is the empty element
    the previous test forbids), so it states the fact -- this card has no price --
    and the stylesheet reserves the height on the title's margin.

    This asserts the flag, not the pixels; the height itself is verified in the
    browser because only a layout engine can measure it.
    """
    unpriced = listing(price_label="")
    card = sf.product_card(unpriced, price=mw.derive_price(unpriced))
    assert 'class="mkt-card-body is-unpriced"' in card
    assert "mkt-card-price" not in card, "the flag is a substitute for the element, not a partner to it"

    priced = sf.product_card(listing(), price=mw.derive_price(listing()))
    assert 'class="mkt-card-body"' in priced
    assert "is-unpriced" not in priced


def test_the_stylesheet_reserves_the_price_line_rather_than_pinning_it_down():
    """The two rules a browser measurement caught, pinned where they live.

    `margin-top:auto` on the price pinned the price *and every line after it* to
    the bottom of the card, which aligns prices only while every card in a row
    carries the same trailing fields -- so a listing with no stock line dropped
    its price 42px. Packing from the top instead makes the price's position a
    function of the title alone, and the title is a fixed two lines. `min-height`
    then keeps a smaller range price ("$24.00 - $26.00") occupying a full line so
    the type size cannot move the metadata either.
    """
    with open(os.path.join(ROOT, "static/css/pulse_marketplace.css"), encoding="utf-8") as handle:
        css = handle.read()
    price_rule = re.search(r"\.mkt-card-price\s*\{(.*?)\}", css, re.S)
    assert price_rule, "the price rule vanished; this test is measuring nothing"
    assert "margin-top: auto" not in price_rule.group(1), (
        "the price is pinned to the bottom again, so its baseline depends on "
        "whether a sibling stock line exists")
    assert "min-height" in price_rule.group(1)
    # And the reserve an unpriced card gets is derived from that same line box
    # rather than typed in as a second copy of the number.
    assert "--mkt-price-line" in css and "--mkt-price-slot" in css
    reserve = re.search(r"\.mkt-card-body\.is-unpriced\s+\.mkt-card-title\s*\{(.*?)\}", css, re.S)
    assert reserve and "var(--mkt-price-slot)" in reserve.group(1)


def test_a_badge_variant_tints_the_scrim_instead_of_replacing_it():
    """Badges sit over seller photography, so the dark plate is load-bearing.

    `.mkt-badges` is absolutely positioned over `.mkt-media`. `is-new` and
    `is-digital` used the `background` shorthand, which replaced the base rule's
    `rgba(6,16,27,.72)` scrim with a ~20% wash -- leaving near-white badge text
    at 1.03:1 and 1.14:1 over a white studio photograph, and a perfectly legible
    13:1 over a dark one. Local QA renders placeholder plates rather than
    photographs, so no screenshot here could have shown it.

    Variants now set only `--mkt-badge-tint`; the scrim lives on
    `background-color`, which a tint cannot reach.

    What this asserts is the *invariant*, not the palette. The scrim's value
    moved from a literal `rgba(6,16,27,.72)` to `--store-badge-scrim` when the
    storefront went light, and pinning the old literal here would have failed
    for a change that kept the badge exactly as legible. The property that
    actually prevents the bug is structural: a variant may be tint-only (and
    keep the scrim) or fully opaque (and supply its own plate), but it may
    never be *translucent over no scrim*, which is the state that put badge
    text at 1.03:1 over a white studio photograph.
    """
    with open(os.path.join(ROOT, "static/css/pulse_marketplace.css"), encoding="utf-8") as handle:
        css = handle.read()
    base = re.search(r"\n\.mkt-badge\s*\{(.*?)\n\}", css, re.S)
    assert base, "the base badge rule vanished; this test is measuring nothing"
    # The scrim is still a `background-color` (a tint sets `background-image`,
    # so it structurally cannot reach this), and it is still near-opaque.
    scrim = re.search(r"background-color:\s*var\((--[a-z-]*scrim)\)", base.group(1))
    assert scrim, (
        "the base badge no longer sets its scrim via `background-color`; a "
        "variant's tint can now replace the plate")
    scrim_value = re.search(
        re.escape(scrim.group(1)) + r"\s*:\s*([^;]+);", css)
    assert scrim_value, f"{scrim.group(1)} is referenced but never defined"
    alpha = re.search(r"rgba\([^)]*,\s*([0-9.]+)\s*\)", scrim_value.group(1))
    assert alpha and float(alpha.group(1)) >= 0.8, (
        "the badge scrim is no longer opaque enough to carry near-white text "
        f"over an arbitrary photograph: {scrim_value.group(1).strip()}")
    assert "--mkt-badge-tint" in base.group(1), (
        "the base rule no longer renders the per-variant tint layer")

    # Every variant, classified. `tint` keeps the scrim; `opaque` replaces it
    # with a solid plate of its own. Nothing is allowed to be neither.
    tint = {"is-digital"}
    opaque = {"is-featured", "is-new"}
    # `is-quiet` is the `_BADGE_CLASS` fallback and only recolours text, so it
    # inherits the scrim untouched -- it sets no background at all.
    text_only = {"is-quiet"}

    found = set(re.findall(r"\.mkt-badge\.(is-[a-z-]+)\s*\{", css))
    assert found == tint | opaque | text_only, (
        "the badge variant set changed; classify the new one as tint-only, "
        f"opaque or text-only before this test can guard it: {found}")

    for variant in sorted(found):
        body = re.search(
            r"\.mkt-badge\.%s\s*\{(.*?)\n\}" % variant, css, re.S).group(1)
        # The shorthand is the exact mechanism of the original bug: it resets
        # `background-image` *and* `background-color` in one go.
        assert not re.search(r"(?<!-)\bbackground\s*:", body), (
            f"{variant} sets the `background` shorthand, which drops the scrim "
            "and makes the badge illegible over a light photograph")

        if variant in tint:
            assert "--mkt-badge-tint" in body, f"{variant} no longer tints"
            assert "background-color" not in body, (
                f"{variant} is tint-only but sets a background-color, so it is "
                "painting over the scrim it is supposed to sit on")
        elif variant in opaque:
            # Opaque variants may drop the scrim, but only by being genuinely
            # opaque -- a hex or a fully-opaque token, never an rgba() wash.
            assert "background-image: none" in body, (
                f"{variant} is opaque but leaves the tint layer live")
            colour = re.search(r"background-color:\s*([^;]+);", body)
            assert colour, f"{variant} is opaque but sets no plate"
            ref = re.match(r"var\((--[a-z-]+)\)", colour.group(1).strip())
            assert ref, (
                f"{variant} should take its plate from a --store-* token, got "
                f"{colour.group(1).strip()}")
            resolved = re.search(re.escape(ref.group(1)) + r"\s*:\s*([^;]+);", css)
            assert resolved, f"{ref.group(1)} is referenced but never defined"
            assert "rgba" not in resolved.group(1), (
                f"{variant} drops the scrim for a translucent plate "
                f"({resolved.group(1).strip()}) -- that is the original bug")
        else:
            assert "background" not in body, (
                f"{variant} is meant to recolour text only")
            # And its text colour must be an on-dark one, because it is sitting
            # on the scrim rather than on the light card.
            assert "on-dark" in body, (
                f"{variant} keeps the dark scrim, so a light-theme text token "
                "here would be near-invisible; use an on-dark token")


def test_a_video_does_not_autoplay_or_preload():
    html = sf.media_box(mw.MediaItem(url="https://cdn/v.mp4", kind="video",
                                     poster="https://cdn/p.jpg"), alt="Clip")
    assert 'preload="none"' in html and "autoplay" not in html
    assert 'poster="https://cdn/p.jpg"' in html


# ---------------------------------------------------------------------------
# 4. Taxonomy and URL-addressable filters
# ---------------------------------------------------------------------------


def test_the_taxonomy_is_built_from_the_catalogue_not_hardcoded():
    """The reference's categories are not PulseSoc's categories."""
    nodes = mw.build_taxonomy(["Clothing > Socks", "Clothing > Shirts", "Electronics"])
    labels = [n.label for n in nodes]
    assert "Clothing" in labels and "Electronics" in labels
    assert not any(n.label == "Sneakers & Shoes" for n in nodes)


def test_a_category_slug_survives_punctuation_and_case():
    assert mw.slugify("Sneakers & Shoes") == "sneakers-shoes"
    assert mw.slugify("Women's Tops") == "womens-tops"
    assert mw.slugify("  ") == ""


def test_a_category_filter_is_a_path_prefix_not_a_bare_name():
    """A department selects its sections; a section does not select itself globally.

    `category_matches` walks the breadcrumb accumulating "clothing",
    "clothing/socks", so the filter is a *prefix path*. That is the property that
    keeps two departments from colliding: a marketplace with "Clothing > Socks"
    and "Car Parts > Socks" (a real hazard in seller-typed taxonomy, where the
    same English word names unrelated things) must not answer one link with both.
    The filter links the page renders come from `category_crumbs`, which emits
    those same full paths, so nothing in the UI ever asks the bare-name question.
    """
    assert mw.category_matches("Clothing > Socks", "clothing")
    assert mw.category_matches("Clothing > Socks", "clothing/socks")
    assert not mw.category_matches("Clothing > Socks", "socks")
    assert not mw.category_matches("Car Parts > Socks", "clothing/socks")
    assert not mw.category_matches("Clothing > Socks", "electronics")
    # An empty filter is "no filter", not "matches nothing" -- the unfiltered
    # grid goes through this same predicate.
    assert mw.category_matches("Clothing > Socks", "")


def test_a_category_path_is_split_on_every_separator_the_data_uses():
    assert mw.parse_category_path("Clothing > Socks") == ["Clothing", "Socks"]
    assert mw.parse_category_path("Clothing / Socks") == ["Clothing", "Socks"]
    assert mw.parse_category_path("Clothing › Socks") == ["Clothing", "Socks"]
    assert mw.parse_category_path(None) == []


def test_filter_state_lives_in_the_url_so_a_view_can_be_shared():
    qs = mw.build_query_string({}, category="shirts", size="M", color="blue")
    assert qs == "?category=shirts&color=blue&size=M"


def test_the_default_sort_and_first_page_stay_out_of_the_url():
    """A canonical URL must have exactly one spelling."""
    assert mw.build_query_string({}, sort=mw.DEFAULT_SORT) == ""
    assert mw.build_query_string({}, page=1) == ""
    assert mw.build_query_string({}, page=3) == "?page=3"


def test_an_unknown_sort_falls_back_instead_of_erroring():
    assert mw.normalize_sort("price_asc") == "price_asc"
    assert mw.normalize_sort("most_popular") == mw.DEFAULT_SORT
    assert mw.normalize_sort(None) == mw.DEFAULT_SORT


def test_a_seller_controlled_value_cannot_escape_its_query_parameter():
    """`safe=""` is load-bearing: a username may contain `/`, `&` or `#`."""
    assert mw.url_quote("a/b&c#d?e") == "a%2Fb%26c%23d%3Fe"


# ---------------------------------------------------------------------------
# 5. Sorting and search
# ---------------------------------------------------------------------------


def _priced(pid, cents, featured=0):
    return {"id": pid, "featured": featured,
            "price": mw.derive_price({"currency": "USD"},
                                     [{"status": "active", "price_cents": cents}])}


def test_price_sort_orders_by_the_derived_price():
    rows = [_priced(1, 3000), _priced(2, 1000), _priced(3, 2000)]
    assert [r["id"] for r in mw.sort_products(rows, "price_asc")] == [2, 3, 1]
    assert [r["id"] for r in mw.sort_products(rows, "price_desc")] == [1, 3, 2]


def test_an_unpriced_product_sorts_last_in_both_directions():
    """It has no place on the number line, and it is not free."""
    unpriced = {"id": 9, "featured": 0, "price": mw.derive_price({"price_label": ""})}
    rows = [unpriced, _priced(1, 1000)]
    for key in ("price_asc", "price_desc"):
        assert [r["id"] for r in mw.sort_products(rows, key)][-1] == 9, key


def test_featured_sort_puts_featured_first_then_newest():
    rows = [{"id": 1, "featured": 0}, {"id": 2, "featured": 1}, {"id": 3, "featured": 0}]
    assert [r["id"] for r in mw.sort_products(rows, "featured")] == [2, 3, 1]


def test_search_matches_the_fields_a_shopper_would_name():
    row = {"title": "Ribbed Cotton Crew Sock", "category": "Clothing > Socks",
           "seller_store_name": "Ada Goods", "short_description": "combed cotton"}
    assert mw.matches_query(row, "sock")
    assert mw.matches_query(row, "ada")
    assert mw.matches_query(row, "cotton sock"), "all tokens must match, in any order"
    assert not mw.matches_query(row, "sock electronics")


def test_search_does_not_read_the_supplier_boilerplate():
    """Descriptions average 568 characters of care instructions.

    Matching them turns "cotton" into a query returning most of the catalogue on
    the strength of a wash label.
    """
    row = {"title": "Steel Watch", "description": "Do not machine wash cold cotton"}
    assert not mw.matches_query(row, "cotton")


def test_an_empty_query_is_not_a_filter():
    assert mw.matches_query({"title": "x"}, "") and mw.matches_query({"title": "x"}, "   ")


# ---------------------------------------------------------------------------
# 6. Pagination
# ---------------------------------------------------------------------------


def test_pagination_clamps_instead_of_serving_an_empty_page():
    """Page 900 of 3 is a URL a crawler will absolutely request."""
    page = mw.paginate(list(range(50)), 900, size=24)
    assert page.page == 3 and page.items and not page.has_next


def test_pagination_survives_a_hostile_page_number():
    for bad in (0, -5, None):
        assert mw.paginate(list(range(10)), bad, size=4).page == 1


def test_an_empty_catalogue_is_one_page_not_zero():
    page = mw.paginate([], 1)
    assert page.pages == 1 and not page.has_prev and not page.has_next


# ---------------------------------------------------------------------------
# 7. The raw-description problem
# ---------------------------------------------------------------------------


def test_a_run_on_attribute_dump_is_parsed_into_a_spec_table():
    """Supplier descriptions arrive as `Key:Value` runs with no delimiters.

    Rendering the raw string is what the reference's "Product Type :Combo Item
    Main Color:Beige" problem looks like in production.
    """
    view = mw.parse_description("Product Type :Combo Item Main Color:Beige Style:Casual")
    keys = [k for k, _ in view.attributes]
    assert "Product Type" in keys and "Main Color" in keys
    assert dict(view.attributes)["Main Color"] == "Beige"


def test_prose_is_kept_as_prose():
    """The parser must not shred a description that was written by a human.

    Under two recoverable attributes the text is left alone, because a one-row
    spec table is a worse rendering of a sentence than the sentence.
    """
    view = mw.parse_description("Soft combed cotton socks. Machine wash cold.")
    assert view.attributes == ()
    assert "combed cotton" in (view.summary or view.body)


def test_a_single_colon_in_a_sentence_does_not_become_a_spec_table():
    """The two-attribute floor, stated as the case that motivates it."""
    view = mw.parse_description("Note: these run small, so size up.")
    assert view.attributes == ()
    assert "run small" in view.body


def test_an_empty_description_yields_an_empty_view_and_not_a_crash():
    for value in (None, "", "   "):
        view = mw.parse_description(value)
        assert view.attributes == () and not view.summary.strip()
        assert view.body == "" and view.notes == ()


def test_the_meta_description_prefers_parsed_content_over_the_raw_run():
    """A `Key:Value` soup in a search snippet is the parser's whole purpose."""
    row = listing(description="Product Type :Combo Item Main Color:Beige", short_description="")
    view = mw.parse_description(row["description"])
    text = mw.meta_description(row, mw.derive_price(row), view)
    assert text and "Type :Combo" not in text


# ---------------------------------------------------------------------------
# 8. Variants
# ---------------------------------------------------------------------------


def opts(*pairs):
    """`options_json` exactly as `services/marketplace_variants.py` writes it.

    An ordered list of `{"name", "value"}` objects, not a dict. The ordering is
    the supplier's and is load-bearing -- `build_option_groups` presents the
    controls in first-seen order -- which a dict literal would not preserve
    faithfully across the values a supplier actually sends.
    """
    return json.dumps([{"name": name, "value": value} for name, value in pairs])


def test_option_groups_are_derived_from_the_variant_rows():
    """Nothing in the option UI is configured; it is read back out of inventory.

    Both the group set and the value order come from the rows, in first-seen
    order, so a seller who stocks M before L gets M first -- not an alphabetical
    order this page invented, and not a size list hard-coded from the mockup.
    """
    variants = [
        {"id": 1, "status": "active", "price_cents": 1200, "variant_key": "a",
         "options_json": opts(("Size", "M"), ("Color", "Blue"))},
        {"id": 2, "status": "active", "price_cents": 1300, "variant_key": "b",
         "options_json": opts(("Size", "L"), ("Color", "Blue"))},
    ]
    groups = mw.build_option_groups(variants)
    assert [g.label for g in groups] == ["Size", "Color"]
    by_label = {g.label: [o.value for o in g.options] for g in groups}
    assert by_label["Size"] == ["M", "L"], "first-seen order, not sorted"
    assert by_label["Color"] == ["Blue"], "one value appearing twice is one control"


def test_a_group_is_only_called_color_when_every_value_is_one():
    """The real production shape behind "Color: Chuck air outlet accessories".

    Suppliers name every group `option1`, so the label is inferred from the
    values -- and inferring it by majority let eighteen colourways vote a
    workwear description into being a colour. The values here are a verbatim
    slice of listing 174 in production, which rendered "Color: Denim Unlined
    Long Gown" on the live product page.
    """
    values = [
        "Police Blue Tweed", "Rose Red Tweed", "Bright Blue Thickened",
        "Gray Polyester Card", "Purplish Blue Polyester Card",
        "Denim Unlined Long Gown",
    ]
    variants = [
        {"id": i, "status": "active", "price_cents": 1200, "variant_key": str(i),
         "options_json": opts(("option1", value))}
        for i, value in enumerate(values, start=1)
    ]
    group = mw.build_option_groups(variants)[0]
    assert group.kind == "option"
    assert group.label == "Option", "vague beats wrong: a gown is not a colour"
    assert [o.value for o in group.options] == values, "every choice is still offered"
    assert all(o.swatch == "" for o in group.options), (
        "a demoted group must not keep drawing colour chips"
    )


def test_a_run_of_real_colours_still_gets_the_colour_treatment():
    """Strictness must not cost shoppers the label when the values earn it.

    Lilac and caramel are the interesting ones: both are ordinary colour words
    that the vocabulary did not know, and under an all-must-match rule a single
    gap like that silently demotes the whole group.
    """
    values = ["Lilac", "Caramel", "Sapphire Blue", "Charcoal Gray"]
    variants = [
        {"id": i, "status": "active", "price_cents": 1200, "variant_key": str(i),
         "options_json": opts(("option1", value))}
        for i, value in enumerate(values, start=1)
    ]
    group = mw.build_option_groups(variants)[0]
    assert group.label == "Color"
    assert [o.value for o in group.options] == values


def test_one_non_size_in_a_size_run_demotes_the_whole_group():
    values = ["S", "M", "L", "Gift Wrap"]
    variants = [
        {"id": i, "status": "active", "price_cents": 1200, "variant_key": str(i),
         "options_json": opts(("option1", value))}
        for i, value in enumerate(values, start=1)
    ]
    assert mw.build_option_groups(variants)[0].label == "Option"


def test_a_size_run_starting_at_1xl_is_still_a_size():
    """`1XL` is a size every supplier ships and the pattern used to reject it.

    Under a majority vote one unrecognised token was outvoted and invisible.
    Requiring every value to match turns that same gap into a lost label, so
    the vocabulary has to actually be right.
    """
    variants = [
        {"id": i, "status": "active", "price_cents": 1200, "variant_key": str(i),
         "options_json": opts(("option1", value))}
        for i, value in enumerate(["1XL", "2XL", "3XL"], start=1)
    ]
    assert mw.build_option_groups(variants)[0].label == "Size"


def test_a_listing_with_no_variants_has_no_option_controls():
    assert mw.build_option_groups([]) == []
    assert sf.options_html([], {}) == "", "an empty fieldset is still a visible box"


# --- product titles ---------------------------------------------------------
#
# Every title below is a verbatim published production title, because the whole
# design claim is about what supplier titles are actually shaped like. A
# plausible-looking invented title would let a cut that is unsafe on the real
# catalogue pass here.

def test_a_long_title_splits_only_where_the_seller_already_broke_it():
    """Listing 14: 160 characters, and the seller's own comma does the work.

    Nothing is reworded or abbreviated. Both halves are substrings of the
    input, and concatenating them with the separator gives the title back --
    which is what makes this a re-division rather than a rewrite.
    """
    title = (
        "Upholstered Bed 135 X 190 Cm With LED Lighting, USB Type-C Charging, "
        "Storage Headboard For Cellphones And Tablets, 4ft6 Hydraulic Storage "
        "Bed With Metal Slatted"
    )
    name, subtitle = mw.display_title(title)
    assert name == "Upholstered Bed 135 X 190 Cm With LED Lighting"
    assert subtitle == (
        "USB Type-C Charging, Storage Headboard For Cellphones And Tablets, "
        "4ft6 Hydraulic Storage Bed With Metal Slatted"
    )
    assert f"{name}, {subtitle}" == title, "a re-division, not a rewrite"


def test_the_split_keeps_the_product_noun_in_the_name():
    """The point of splitting at all: the heading still says what it is.

    Listing 52 and listing 110 are the other two production titles that split.
    Both keep their noun -- "Ring", "Earrings" -- because the seller ended the
    first clause on it. That is the property a word budget cannot promise.
    """
    name, subtitle = mw.display_title(
        "Women's 3D Butterfly Ring, Elegant Fairy Style Index Finger Ring, "
        "Korean INS Trend, Unique Minimalist Fashion Ring"
    )
    assert name == "Women's 3D Butterfly Ring"
    assert subtitle.startswith("Elegant Fairy Style Index Finger Ring")

    name, subtitle = mw.display_title(
        "1 Pair Vintage Boho Style Leaf Dangle Earrings, Elegant & Chic, For "
        "Women's Daily Wear, Banquets & Vacations, Exquisite Female Jewelry Gift"
    )
    assert name == "1 Pair Vintage Boho Style Leaf Dangle Earrings"
    assert subtitle.startswith("Elegant & Chic")


def test_a_title_with_no_seller_separator_is_never_cut():
    """The case that rules out every length-based shortener.

    This production title is 94 characters of keyword run with the product noun
    last. Any budget that shortens it names the product "...Elegant Long" and
    loses the T-shirt, so the only correct answer is to return it whole and let
    CSS deal with the length.
    """
    title = (
        "European And American Rib Slim V-neck Elegant Long Sleeve Spring And "
        "Summer T-shirt"
    )
    assert mw.display_title(title) == (title, "")


def test_a_head_that_opens_on_marketing_is_not_promoted_to_the_name():
    """Listing 50 -- the one production title the lead-in guard actually saves.

    The head parses as a ring and is a sane length, so length and separator
    both pass it; only the lead-in check notices that "New Arrival" is the
    supplier's banner rather than the start of the product's name.
    """
    title = (
        "New Arrival Flower Oil Dripping Open Ring, Alloy Hot Sale Butterfly "
        "Diamond"
    )
    assert mw.display_title(title) == (title, "")


def test_a_title_short_enough_to_read_is_left_alone():
    """Splitting is a remedy for length, so a short title is not a candidate.

    The fixture is constructed rather than taken from production, and that is
    worth saying out loud: exactly one published title is both short and
    comma-broken (listing 161, "Compatible with Apple , Anti-peeping Toughened
    Film ..."), and the lead-in guard would refuse that one anyway. So nothing
    in today's catalogue exercises this check.

    It is still the first thing the function tests, because the cost of
    splitting is paid in the heading -- the tail moves into smaller, quieter
    type -- and a title that already fits has nothing to buy with it. Without
    this check the 151 titles under the threshold become candidates the moment
    a seller types a comma.
    """
    title = "Linen Duvet Cover Set, washed and stonewashed in eight colours"
    assert len(title) < 70
    assert mw._TITLE_SEPARATOR.search(title), "the fixture must be splittable"
    assert mw.display_title(title) == (title, "")


def test_the_current_crumb_is_clamped_like_the_links_beside_it():
    """Splitting the heading is only half of the de-duplication.

    The breadcrumb carries the same shortened heading, and on a 320px screen
    even 45 characters wraps the trail onto three lines directly above the
    `<h1>` -- the duplication, smaller. Its sibling `.mkt-crumbs a` has been
    clamped since the trail was built; the current-page crumb is not an anchor
    and so was never covered by that rule.
    """
    with open(os.path.join(ROOT, "static/css/pulse_marketplace.css"), encoding="utf-8") as handle:
        css = handle.read()
    current = _css_block(css, '.mkt-crumbs [aria-current="page"]')
    assert current, "the current-page crumb has no rule of its own"
    for declaration in ("overflow: hidden", "text-overflow: ellipsis",
                        "white-space: nowrap", "max-width:"):
        assert declaration in current, (
            f"the current crumb is missing {declaration!r}; without the whole "
            "set it still wraps or still overflows")
    # The same trap `test_a_thumbnail_is_sized_by_a_rule_that_can_actually_apply_to_it`
    # guards one rule over: `.mkt-crumbs li` is `inline-flex`, and
    # `text-overflow` applies only to a block container, so inheriting that
    # display makes the ellipsis above inert -- the crumb then clips mid-word,
    # which looks like a rendering fault rather than shortened text.
    assert re.search(r"display:\s*(inline-)?block", current), (
        "a flex container ignores the text-overflow above it")


def test_the_qualifier_is_quieter_than_the_heading_it_follows():
    """It must not read as a second headline -- that is the defect it fixes.

    Asserted as a contrast against `.mkt-title` rather than as an absolute
    size, because what matters is the relationship: a qualifier set in the
    heading's own weight and colour restages the duplication in one line.
    """
    with open(os.path.join(ROOT, "static/css/pulse_marketplace.css"), encoding="utf-8") as handle:
        css = handle.read()
    qualifier = _css_block(css, ".mkt-title-qualifier")
    assert qualifier, "the qualifier has no styling and inherits the body's"
    assert "--mkt-muted" in qualifier, "the qualifier is as loud as the heading"
    heading = _css_block(css, ".mkt-title")
    assert "--mkt-muted" not in heading, "the heading went quiet instead"


def test_a_two_word_head_is_a_category_not_a_product_name():
    """The lower bound on the name, which no production title trips today.

    It is here because the upstream feed changes without warning: a supplier
    writing "Dress, long-sleeve ..." would otherwise have the whole listing
    reduced to the word "Dress" in the heading.
    """
    title = "Dress, long sleeve autumn winter knitted midi with belt and pockets"
    assert len(title) < 70  # the length guard is not what refuses this
    long_title = title + " in eight colourways for everyday and occasion wear"
    name, subtitle = mw.display_title(long_title)
    assert (name, subtitle) == (long_title, ""), "a bare category is not a name"


def test_a_variant_a_seller_retired_is_not_offered():
    """Status is the seller's answer to "may this still be bought".

    A non-active row must not reach either the controls or the price range, or
    the page advertises a combination checkout will refuse.
    """
    rows = [
        {"id": 1, "status": "active", "price_cents": 1200, "variant_key": "a",
         "options_json": opts(("Size", "M"))},
        {"id": 2, "status": "archived", "price_cents": 100, "variant_key": "b",
         "options_json": opts(("Size", "XXL"))},
    ]
    assert [o.value for o in mw.build_option_groups(rows)[0].options] == ["M"]
    assert [v.key for v in mw.build_variant_views(rows)] == ["a"]
    assert mw.derive_price(listing(), rows).min_cents == 1200


def test_the_browser_selects_between_server_authored_prices():
    """No amount ever travels from the client.

    Each variant carries the string this server formatted plus an opaque
    `variant_key`; the page swaps between them. A client that posts a price is
    posting something checkout never reads.
    """
    views = mw.build_variant_views(
        [{"id": 7, "status": "active", "price_cents": 1200, "variant_key": "sock-m",
          "options_json": opts(("Size", "M"))}], currency="USD")
    assert len(views) == 1
    payload = views[0].as_client_dict()
    assert payload["price"] == "$12.00", "the client gets a formatted string, not cents"
    assert payload["key"] == "sock-m"
    assert "price_cents" not in payload, (
        "shipping raw cents invites a client to compute a total and post it")


def test_an_unconfirmed_variant_is_neither_sold_out_nor_in_stock():
    """UNKNOWN is a third answer, and collapsing it either way is a lie.

    A supplier that has not reported stock must not be rendered sold out (that
    loses a real sale) and must not be rendered available (that promises one this
    platform cannot keep). It stays selectable and says nothing.
    """
    def view(state, quantity=None):
        return mw.build_variant_views([{
            "id": 1, "status": "active", "price_cents": 1200, "variant_key": "k",
            "options_json": opts(("Size", "M")),
            "stock_state": state, "stock_quantity": quantity}])[0]

    assert view("UNKNOWN").stock_label == ""
    assert view("UNKNOWN").available is True
    assert view("OUT_OF_STOCK").stock_label == "Out of stock"
    assert view("OUT_OF_STOCK").available is False
    assert view("IN_STOCK").stock_label == "In stock"
    # A count is only shown when it is low enough to change a decision.
    assert view("IN_STOCK", 2).stock_label == "Only 2 left"
    assert view("IN_STOCK", 1).stock_label == "Last one"
    assert view("IN_STOCK", 1284).stock_label == "In stock", "no fake scarcity"


def test_variant_option_values_are_escaped_where_they_are_rendered():
    """Option values are supplier-controlled strings inside HTML attributes."""
    variants = [{"id": 1, "status": "active", "price_cents": 1200, "variant_key": 'x"y',
                 "options_json": opts(("Size", '<script>alert(1)</script>'))}]
    groups = mw.build_option_groups(variants)
    html = sf.options_html(groups, {})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- the vocabulary the picker and the payload have to share ----------------
#
# The server speaks two names for one option group and the page needs both.
# `name` is the query-string parameter, slugified so it can survive a URL:
# "Color" becomes `opt_color`. The variant payload keys `options` by the raw
# group name the seller typed, because that is what a variant row contains.
#
# For a while the script compared one against the other, so `options["opt_color"]`
# was undefined on every row, no combination was ever reachable, and every option
# rendered disabled and struck through -- a product whose entire catalogue looked
# sold out, on the one control the page exists to offer. The markup was right,
# the payload was right, and the two never met.
#
# The fix carries the raw key on the element rather than re-implementing the
# slug in JavaScript, so these tests pin the carrier and pin that the second
# slugifier stayed unwritten.

JS_PATH = os.path.join(ROOT, "static", "js", "pulse_marketplace.js")


def _js():
    """The script with its comments removed.

    The same care `_literal_strings` takes with this module's Python. The fix
    is documented in a block comment that *quotes* the parameter name it exists
    to explain -- "Color" becomes `opt_color` -- so a raw text scan reads the
    explanation as the thing it warns about and fails on the corrected file.

    Strings are left alone: this file's JavaScript has no regex literal and no
    `//` inside a string, so the crude pass below is safe here and would be
    worth replacing the moment either appears.
    """
    with open(JS_PATH, encoding="utf-8") as fh:
        source = fh.read()
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def test_every_option_input_carries_the_key_the_payload_is_keyed_by():
    """Each radio names its group twice: slugified for the URL, raw for the
    script. Both are asserted, because dropping either one is silent."""
    variants = [
        {"id": 1, "status": "active", "price_cents": 1200, "variant_key": "a",
         "options_json": opts(("Color", "Snowflake Blue"), ("Size", "M"))},
        {"id": 2, "status": "active", "price_cents": 1300, "variant_key": "b",
         "options_json": opts(("Color", "Snowflake Blue"), ("Size", "L"))},
    ]
    groups = mw.build_option_groups(variants)
    html = sf.options_html(groups, {})
    inputs = re.findall(r"<input[^>]*>", html)
    assert len(inputs) == 3, "one control per value, not per variant row"
    for tag in inputs:
        raw = re.search(r'data-mkt-option="([^"]*)"', tag)
        slug = re.search(r'name="([^"]*)"', tag)
        assert raw and slug, tag
        assert raw.group(1) in ("Color", "Size")
        assert slug.group(1) == f"opt_{raw.group(1).lower()}"


def test_the_raw_key_matches_the_one_the_variant_payload_uses():
    """The two are produced by different functions from the same rows. This is
    the join that was broken, so it is asserted against both sides rather than
    against a literal either side could drift from."""
    variants = [{"id": 1, "status": "active", "price_cents": 1200, "variant_key": "a",
                 "options_json": opts(("Shade / Colour", "Rose Gold"))}]
    groups = mw.build_option_groups(variants)
    payload_keys = set(mw.build_variant_views(variants)[0].as_client_dict()["options"])
    rendered = set(re.findall(r'data-mkt-option="([^"]*)"', sf.options_html(groups, {})))
    assert rendered == payload_keys == {"Shade / Colour"}


def test_the_key_survives_a_name_that_does_not_survive_slugification():
    """A group whose name slugifies to nothing still has to be matchable.

    `option_name` falls back to `opt_option` for it, and two such groups would
    share that parameter -- which is a URL problem, not a picker problem, and
    only stays a URL problem while the script reads the raw key instead.
    """
    variants = [
        {"id": 1, "status": "active", "price_cents": 1200, "variant_key": "a",
         "options_json": opts(("///", "One"))},
    ]
    group = mw.build_option_groups(variants)[0]
    assert sf.option_name(group) == "opt_option", "the slug collapsed, as expected"
    assert f'data-mkt-option="{group.key}"' in sf.options_html([group], {})


def test_the_script_reads_the_raw_key_and_does_not_slugify_a_second_copy():
    """A slugifier in JavaScript would have to keep agreeing with
    `marketplace_web.slugify` forever, and the first time it disagreed the
    symptom would be this same silent everything-disabled page."""
    js = _js()
    assert 'getAttribute("data-mkt-option")' in js
    assert "opt_" not in js, "the client must never construct the parameter name"


def test_the_stylesheet_still_strikes_a_disabled_option_through():
    """The half of this defect that was never wrong.

    Showing an unreachable combination struck through beats hiding it -- but
    only while `disabled` means unreachable. The rule is pinned here so that a
    future reader who finds a page full of struck-through options looks at the
    script, which was where the fault was, rather than deleting this.
    """
    assert any("line-through" in block
               for block in _declarations_for(_css(), ".mkt-option input:disabled + label"))


# ---------------------------------------------------------------------------
# 9. Structured data and SEO
# ---------------------------------------------------------------------------


def _jsonld(row=None, variants=(), media=()):
    row = row or listing()
    price = mw.derive_price(row, variants)
    description = mw.parse_description(row.get("description"))
    return mw.product_jsonld(row, price, description, media,
                             mw.PUBLIC_ORIGIN + sf.product_path(row["id"]))


def test_product_structured_data_carries_no_rating_and_no_review():
    """There is no review table. Inventing one gets the whole domain ignored."""
    data = _jsonld()
    assert "aggregateRating" not in data
    assert "review" not in data
    assert "brand" not in data, "there is no brand column"


def test_product_structured_data_carries_what_it_can_prove():
    """The positive half: absence tests must not pass by emitting nothing."""
    data = _jsonld(media=[mw.MediaItem(url="https://cdn/a.jpg", kind="image")])
    assert data["@type"] == "Product"
    assert data["name"] == "Ribbed Cotton Crew Sock, 3 Pair"
    assert data["url"].endswith("/pulse/marketplace/4271")
    assert data["sku"] == "pulsesoc-listing-4271"
    assert data["image"] == ["https://cdn/a.jpg"]
    assert data["offers"]["price"] == "12.00"
    assert data["category"] == "Clothing > Socks"


def test_a_video_is_not_offered_to_schema_as_a_product_image():
    data = _jsonld(media=[mw.MediaItem(url="https://cdn/v.mp4", kind="video")])
    assert "image" not in data


def test_availability_is_stated_only_when_it_is_known():
    row = listing()
    price = mw.derive_price(row)
    desc = mw.parse_description(row["description"])
    url = mw.PUBLIC_ORIGIN + sf.product_path(row["id"])
    unknown = mw.product_jsonld(row, price, desc, (), url, in_stock=None)
    assert "availability" not in unknown["offers"]
    known = mw.product_jsonld(row, price, desc, (), url, in_stock=True)
    assert known["offers"]["availability"] == "https://schema.org/InStock"


def test_the_canonical_product_url_is_stable_and_id_addressed():
    """These URLs are already published as universal links; they cannot drift."""
    assert sf.product_path(4271) == "/pulse/marketplace/4271"
    assert sf.product_url(4271) == "https://pulsesoc.com/pulse/marketplace/4271"
    assert sf.BASE_PATH == "/pulse/marketplace"


def test_the_breadcrumb_list_starts_at_the_marketplace_root():
    """Every crumb must be a URL that actually selects what the crumb names.

    The deep crumb carries the full path (`clothing/socks`), matching what
    `category_matches` consumes. A breadcrumb linking to `?category=socks` would
    render as a plausible trail and land on an empty grid -- the failure mode
    Google reports as a structured-data URL that disagrees with the page.
    """
    crumbs = mw.category_crumbs("Clothing > Socks")
    data = mw.breadcrumb_jsonld(crumbs)
    names = [item["name"] for item in data["itemListElement"]]
    assert names == ["Marketplace", "Clothing", "Socks"]
    assert [item["position"] for item in data["itemListElement"]] == [1, 2, 3]
    items = [item["item"] for item in data["itemListElement"]]
    assert items[0] == mw.PUBLIC_ORIGIN + sf.BASE_PATH
    assert items[1].endswith("?category=clothing")
    assert items[2].endswith("?category=clothing/socks")


def test_no_breadcrumb_is_emitted_for_an_uncategorised_product():
    assert mw.breadcrumb_jsonld(mw.category_crumbs(None)) is None


def test_the_structured_data_serialises_to_json():
    """A dict that cannot be dumped is a script tag that breaks the page."""
    json.dumps(_jsonld(media=[mw.MediaItem(url="https://cdn/a.jpg", kind="image")]))


# ---------------------------------------------------------------------------
# 10. Escaping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    '<script>alert(1)</script>',
    '" onmouseover="alert(1)',
    "</title><script>x</script>",
])
def test_a_seller_controlled_title_cannot_break_out_of_the_markup(payload):
    """Titles, descriptions and store names are all seller-typed.

    The title reaches a card twice -- as link text and as the image's `alt` -- so
    one of the two escaping the payload is not enough. Asserting the payload is
    absent from the whole card covers both, including the attribute position
    where `"` alone is the entire exploit.
    """
    row = listing(title=payload, store_name=payload)
    card = sf.product_card(row, price=mw.derive_price(row), stock="In stock",
                           seller_href="/pulse/store/9")
    assert payload not in card
    assert "&lt;" in card or "&quot;" in card
    # The escaped form must actually be present: a card that dropped the title
    # entirely would also pass the assertion above.
    assert mw.esc(payload) in card


def test_esc_covers_the_attribute_delimiters():
    assert mw.esc('a"b<c>&d') == "a&quot;b&lt;c&gt;&amp;d"
    assert mw.esc(None) == ""


# ---------------------------------------------------------------------------
# 11. Living inside someone else's stylesheet
#
# The storefront does not own the page it renders into. `pulse_social_shell`
# brings its own sheets, two of which make declarations this storefront's
# markup is subject to whether it likes it or not. Both defects below shipped
# in a version of this file that passed every other test here, because both
# are invisible to anything that reads markup alone: the markup was correct
# and the cascade overruled it. They were found by measuring a rendered page
# in a browser, and these tests exist so the next one is found by pytest.
# ---------------------------------------------------------------------------

CSS_PATH = os.path.join(ROOT, "static", "css", "pulse_marketplace.css")
SHELL_MOBILE_CSS = os.path.join(ROOT, "static", "css", "pulse_mobile_system.css")


def _css():
    with open(CSS_PATH, encoding="utf-8") as fh:
        return fh.read()


def _declarations_for(css, selector):
    """Every declaration block whose selector list contains `selector` exactly.

    Deliberately crude -- this splits on braces rather than parsing CSS -- but
    it is reading a file this repository writes by hand, and a real parser is
    not a dependency worth adding to learn whether one rule exists.
    """
    out = []
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [s.strip() for s in block.group(1).split("\n")[-1].split(",")]
        if selector in [s.strip() for s in block.group(1).replace("\n", " ").split(",")]:
            out.append(block.group(2))
        elif selector in selectors:
            out.append(block.group(2))
    return out


#: `hidden` as a real attribute, in the two forms the renderer writes it.
#:
#: The lookbehind is the whole point. `aria-hidden="true"` contains the word
#: and says nothing about `display` -- the renderer uses it nine times, on
#: decorative avatars, the `×` on a filter chip and the pagination ellipsis,
#: none of which are progressively enhanced. A first version of this scanner
#: matched all nine and none of the controls that matter, so the test it fed
#: passed entirely on false positives. The brace alternative catches the
#: attribute arriving through an f-string hole (`data-mkt-stock{stock_hidden}`,
#: the stock line, which is the element whose defect prompted all of this);
#: the lookbehind alone rejects that one because `_` is a word character.
HIDDEN_ATTR = re.compile(r"(?<![-\w])hidden(?![-\w])|\{[a-z_]*hidden\}")


def _hidden_classes_in_the_renderer():
    """Classes the renderer emits on an element that also carries `hidden`.

    Reads the source rather than a rendered page because most of these are
    conditional on data a fixture would have to invent -- the gallery arrows
    need a second image, the stock line needs variants, Save and Report need a
    signed-in viewer -- and the contract holds for all of them or for none.

    Scans raw source rather than parsed string literals, which is the opposite
    of what the rest of this file does and is deliberate: an f-string's holes
    are not part of any literal, so an AST-based read of
    `f'<p class="mkt-stock{stock_out}" data-mkt-stock{stock_hidden}>'` sees
    neither the class nor the attribute. Raw text can in principle match
    markup quoted inside a comment; the tag pattern is narrow enough that it
    does not today, and the caller only asks whether *some* hidden-rendered
    class is given a `display`, not which.
    """
    with open(os.path.join(ROOT, "services", "marketplace_storefront.py"),
              encoding="utf-8") as fh:
        source = fh.read()
    found = set()
    for tag in re.finditer(r"<(?:p|button|div|span|li|figure)\b[^<>]*>", source):
        markup = tag.group(0)
        if not HIDDEN_ATTR.search(markup):
            continue
        cls = re.search(r'class="([^"]*)"', markup)
        if cls:
            # An interpolated modifier (`mkt-stock{stock_out}`) is trimmed back
            # to the literal stem, which is the name the stylesheet uses.
            found.update(c.split("{")[0] for c in cls.group(1).split()
                         if c.startswith("mkt-") and c.split("{")[0])
    return found


def test_hidden_survives_the_components_own_display_rules():
    """`hidden` is the no-JS contract and the UA stylesheet cannot enforce it.

    The server renders Save, Report listing, Message seller, both gallery
    arrows, the description toggle and the variant stock line already in the
    DOM but `hidden`; the page script removes the attribute as it binds each
    one. `[hidden] { display: none }` is a *user-agent* rule, and a user-agent
    declaration loses to every author declaration -- so each component rule
    that sets `display` silently revived the control it was meant to suppress.

    Two of those were visible on the rendered page: a glowing green
    availability dot with no words beside it (`.mkt-stock::before` drawing
    inside an empty `<p>`), and the sort form's Apply button outliving the
    script that had already made it redundant. A green dot is a claim about
    stock, so the first was the catalogue asserting something no row said.

    Nothing about that is specific to the six controls involved. Any future
    component rendered `hidden` inherits the same trap, which is why the fix
    and this test are both stated over the whole subtree.
    """
    blocks = _declarations_for(_css(), ".mkt [hidden]")
    assert blocks, "the storefront must override the UA's [hidden] rule"
    assert any(
        re.search(r"display\s*:\s*none\s*!important", b) for b in blocks
    ), "the override has to be !important -- it is outranking author rules"


def test_the_hidden_override_is_load_bearing():
    """Anti-vacuity for the test above: prove the trap is real, here, today.

    If no class the renderer hides ever set `display`, the rule above would be
    a decoration and the test guarding it would pass forever without guarding
    anything. This asserts the opposite -- that at least one hidden-rendered
    class is given a `display` by this very stylesheet, which is exactly the
    collision the override exists to lose.
    """
    css = _css()
    hidden_classes = _hidden_classes_in_the_renderer()
    assert hidden_classes, "the renderer stopped emitting `hidden` at all"
    revived = {
        cls for cls in hidden_classes
        if any(re.search(r"display\s*:", b) for b in _declarations_for(css, "." + cls))
    }
    assert revived, (
        "no hidden-rendered class sets `display` any more, so the [hidden] "
        "override no longer has anything to override -- check whether the "
        "progressive-enhancement contract moved before deleting it"
    )


#: The shell's phone display scale, and the one storefront heading that wants
#: it. `.mkt-title` is the page's `<h1>`; rendering it at the shell's size is
#: how this page looks like the rest of PulseSoc on a phone.
SHELL_DISPLAY_SCALE_IS_WANTED_BY = {"mkt-title"}


def test_storefront_headings_restate_their_size_against_the_shells_scale():
    """`pulse_mobile_system.css` resizes bare headings with `!important`.

    Under `max-width: 768px` it sets `h1`, `h2` and `h3` to viewport-relative
    clamps, `!important`, on every PulseSoc page. That is the shell's display
    scale for page and section headings and it is right for them. It is wrong
    for headings that are components: measured on a 390px viewport, the
    product card's 14px title rendered at 21.84px -- bold, and louder than the
    16px price directly beneath it, inverting the single hierarchy the card
    exists to state. Four other headings flattened to 27.3px.

    So every heading this storefront renders must either restate its own size
    `!important` inside the same query, or be named above as wanting the
    shell's treatment. A new `<h3>` component added without either is the same
    bug again, and it will not look like a bug in the markup.

    The premise is checked, not assumed: if the shell ever stops setting these
    sizes the assertion stops being meaningful, and this fails loudly rather
    than passing vacuously.
    """
    with open(SHELL_MOBILE_CSS, encoding="utf-8") as fh:
        shell = fh.read()
    contested = set(re.findall(
        r"\n\s*(h[1-6])\s*\{[^}]*font-size[^}]*!important", shell
    ))
    assert contested >= {"h1", "h2", "h3"}, (
        "the shell no longer resizes bare headings !important on phones; this "
        "test's premise is gone and the overrides it guards may be dead weight"
    )

    css = _css()
    # The block that does the restating. Taking it by slice rather than by
    # selector because what matters is that the declarations sit *inside* a
    # query that matches the shell's.
    blocks = re.findall(r"@media\s*\(max-width:\s*768px\)\s*\{(.*?)\n\}", css, re.S)
    restated = set()
    for block in blocks:
        for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
            if not re.search(r"font-size[^;]*!important", rule.group(2)):
                continue
            restated.update(re.findall(r"\.(mkt-[a-z-]+)", rule.group(1)))

    rendered = set()
    for text in _literal_strings(os.path.join(ROOT, "services", "marketplace_storefront.py")):
        for tag in re.finditer(r'<h[1-6][^>]*class="([^"]*)"', text):
            rendered.update(c for c in tag.group(1).split() if c.startswith("mkt-"))
        # Bare headings inside a classed section -- `.mkt-panel > h2` and the
        # like -- are addressed through their container, so credit the
        # container the same way.
        for tag in re.finditer(r'<(?:section|div|figure)[^>]*class="(mkt-[^"]*)"[^>]*>\s*<h[1-6]>', text):
            rendered.update(c for c in tag.group(1).split() if c.startswith("mkt-"))

    unguarded = rendered - restated - SHELL_DISPLAY_SCALE_IS_WANTED_BY
    assert not unguarded, (
        "these storefront headings take the shell's phone display scale "
        f"instead of their own size: {sorted(unguarded)}. Either restate the "
        "size !important under (max-width: 768px) in pulse_marketplace.css, "
        "or add it to SHELL_DISPLAY_SCALE_IS_WANTED_BY and say why."
    )


# ---------------------------------------------------------------------------
# 12. A column default is not a seller's answer
#
# `bot.py` declares `delivery_type TEXT DEFAULT 'digital'`. Almost every row in
# the catalogue carries that default because nobody was ever asked, so the
# column reads "digital" for aerosol tyre spray, denim jackets and duvet covers
# alike -- and `fulfilment_html` printed it verbatim, directly under a "Product
# type: physical" line the same table had just produced.
#
# That is the most dangerous class of fabrication this storefront can commit,
# and the one hardest to see: unlike a star rating, it is a real column being
# read faithfully. The value was true to the database and false about the
# product. Section 1 above catches copy that no column can source; nothing
# there could catch a column sourcing the wrong answer.
#
# So the rule is narrow. `delivery_type` is not corrected, inferred or
# defaulted -- it is *omitted* where it contradicts the kind of thing being
# sold, which is the honest rendering of a question the seller never answered.
# Where the two agree, or where no kind was recorded at all, it prints
# unchanged. The precedence -- `listing_type or product_type` first,
# `delivery_type` last -- is the one `bot.py` already applies to this column.
# ---------------------------------------------------------------------------

DELIVERY_ROW = "<dt>Delivery</dt>"
TYPE_ROW = "<dt>Product type</dt>"


def test_a_physical_product_is_not_advertised_as_a_download():
    """The defect, stated as its symptom. Both rows were rendered together."""
    html = sf.fulfilment_html({"product_type": "physical", "delivery_type": "digital"})
    assert TYPE_ROW in html, "the type is a real answer and still prints"
    assert DELIVERY_ROW not in html


@pytest.mark.parametrize("kind,delivery", [
    ("Physical", "digital"),
    ("shipped", "digital"),
    ("pickup", "download"),
    ("PHYSICAL", "DIGITAL"),
])
def test_the_contradiction_is_caught_however_either_side_is_spelled(kind, delivery):
    """Both columns are free text written by importers, not an enum."""
    assert DELIVERY_ROW not in sf.fulfilment_html(
        {"product_type": kind, "delivery_type": delivery})


@pytest.mark.parametrize("label,row", [
    ("they agree", {"product_type": "digital", "delivery_type": "digital"}),
    ("no kind was recorded", {"delivery_type": "digital"}),
    ("a physical kind delivered physically", {"product_type": "physical",
                                              "delivery_type": "shipped"}),
    ("a real answer the column default cannot explain",
     {"product_type": "physical", "delivery_type": "Ships in 3-5 days"}),
    ("a digital kind delivered digitally", {"product_type": "course",
                                            "delivery_type": "download"}),
])
def test_everything_that_does_not_contradict_still_prints(label, row):
    """The paired positive cases.

    An omission rule is easy to write in a way that omits everything and passes
    the test above forever. The last case matters most: an answer the seller
    really did type is not one the column default could have produced, and
    suppressing it would lose the only delivery fact this catalogue has.
    """
    assert DELIVERY_ROW in sf.fulfilment_html(row)


def test_listing_type_outranks_product_type_the_way_the_rest_of_the_code_says():
    """`bot.py:20985` resolves a listing's kind as
    `listing_type or product_type or delivery_type`. A second precedence here
    would make one page disagree with the lifecycle about what a product is."""
    contradicted = {"listing_type": "physical", "product_type": "digital",
                    "delivery_type": "digital"}
    assert DELIVERY_ROW not in sf.fulfilment_html(contradicted)
    assert DELIVERY_ROW in sf.fulfilment_html({**contradicted, "listing_type": "digital"})


def test_no_delivery_window_is_ever_invented():
    """`estimated_delivery` is empty on every production row, and "3-7 business
    days" is the reference mockup's single most quotable fabrication."""
    assert "Estimated delivery" not in sf.fulfilment_html(
        {"product_type": "physical", "delivery_type": "shipped"})
    assert "Estimated delivery" in sf.fulfilment_html(
        {"estimated_delivery": "Arrives by 12 October"}), (
        "the row is suppressed by absence, not removed from the renderer")


def test_the_panel_survives_a_row_that_answers_nothing():
    """The platform's own payment policy is not a seller fact and still
    prints, so the section is never an empty box."""
    html = sf.fulfilment_html({})
    assert "Delivery and payment" in html
    assert "mkt-facts" not in html, "an empty definition list is still a visible gap"


# --- asset cache tokens -----------------------------------------------------
#
# These two live here rather than in a protection suite because the thing they
# guard is a constant in `marketplace_storefront`, and the cost of getting it
# wrong is silent: the fix deploys, the origin serves the new bytes, and the
# browsers that needed it never ask for them.

#: sha256 prefixes of the two assets whose URLs carry a hand-bumped `?v=`.
#: Update these *and* the token in the same commit. See the docstring on
#: `CSS_HREF` for why the pair has to move together.
ASSET_DIGESTS = {
    "static/css/pulse_marketplace.css": "862bf4222797",
    "static/js/pulse_marketplace.js": "d2d20c58cd87",
}

#: The token those digests were recorded against.
ASSET_TOKEN = "storefront-20261002b"


def test_editing_a_storefront_asset_forces_its_cache_token_to_move():
    """The gate for a bug this branch actually shipped.

    `pulse_marketplace.js` gained the variant resolver and `pulse_marketplace.css`
    the option-group rules, and the token stayed at `storefront-20260927a` -- which
    #84 had already deployed. Both files are served `max-age=31536000, immutable`,
    so a returning visitor keeps the old script for a year and never revalidates.
    The page then renders a picker whose radios do nothing and an add button that
    nothing re-enables, which looks like a broken feature rather than a stale
    cache.

    A digest is the only honest trigger here. Comparing mtimes or asking git
    would pass on a fresh clone, and asserting the token merely *exists* is what
    let the reuse through. When this fails, do both halves: bump `CSS_HREF` and
    `JS_SRC`, then record the new digests and token below.
    """
    import hashlib

    stale = []
    for relative, expected in ASSET_DIGESTS.items():
        path = os.path.join(ROOT, relative)
        actual = hashlib.sha256(
            open(path, "rb").read()).hexdigest()[:len(expected)]
        if actual != expected:
            stale.append(f"{relative}: recorded {expected}, on disk {actual}")
    assert not stale, (
        "a versioned storefront asset changed without its cache token being "
        "bumped:\n  " + "\n  ".join(stale) + "\n"
        f"Bump CSS_HREF/JS_SRC off {ASSET_TOKEN!r} in "
        "services/marketplace_storefront.py, then update ASSET_DIGESTS and "
        "ASSET_TOKEN here to match."
    )


def test_both_asset_urls_carry_the_token_the_digests_were_recorded_against():
    """Keeps the two halves of the test above from drifting apart.

    Without this, someone could bump the token, forget the digests, and the
    digest test would go on guarding a token that is no longer served -- or
    update the digests without bumping the token, which is the original bug
    wearing a green suite.
    """
    assert f"?v={ASSET_TOKEN}" in sf.CSS_HREF, sf.CSS_HREF
    assert f"?v={ASSET_TOKEN}" in sf.JS_SRC, sf.JS_SRC
    # Same token on both, because one page loads both and a half-bumped pair
    # gives the new CSS to a browser still running the old JS.
    assert sf.CSS_HREF.split("?v=")[1] == sf.JS_SRC.split("?v=")[1]
