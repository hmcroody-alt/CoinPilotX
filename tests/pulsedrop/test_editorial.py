"""The curator's copy: what PulseDrop writes on a post, and what it refuses to.

What this suite is defending
----------------------------
Every word here is published to a public feed by a machine, under a verified
badge, with nobody reading it first. There is no reviewer between
:func:`editorial.caption` and a member's screen, so a formatting bug is not
caught in staging — it is caught by members, on every post, until somebody
notices.

That is not hypothetical. The first thing PulseDrop ever published carried
``#womensclothingtopssw``: ``marketplace_listings.category`` holds a *breadcrumb*
("Women's Clothing > Tops & Sets > Sweaters"), the old tag builder flattened the
whole path into one run of characters and clipped that run to a length, and the
clip landed in the middle of "sweaters". Two separate defects in one tag —
unreadable, and cut mid-word — on a post that is still live.

It was worse than cosmetic. Flattening also *collapsed* the catalogue: "Outerwear
& Jackets > Basic Jacket" and "Outerwear & Jackets > Blazers" both truncated to
the identical ``#womensclothingouterwearj``, so the one field that says what a
product actually is could not distinguish two products. Tags are a discovery
surface; a tag shared by everything discovers nothing.

So the classes below assert the three properties that failure violated:

**A tag is a word a member could have typed.** Never a mid-word cut, never a
concatenated path, never longer than something a human writes on purpose.

**A tag says what the product is.** The leaf of the path is the product; the root
is its department. Both are useful, so both are published, and two products with
different leaves never end up with the same tag.

**The signing tags cannot be squeezed out.** ``#pulsedrop`` is how a member finds
the curator's other posts and ``#pulsesocmarketplace`` is how the marketplace is
credited. A category rich enough to fill the budget must not cost either one.

Why the fixtures are production strings
---------------------------------------
The category values in :data:`REAL_CATEGORIES` are copied verbatim out of
``marketplace_listings`` in production, including the inconsistencies — two
separator styles in the same column, a full-width comma from a dropship feed, an
apostrophe in "Women's". A fixture that invented tidy categories would have
passed against the old builder too, because the old builder only failed on paths,
and every real row is a path.
"""

from __future__ import annotations

import pytest

from services.pulsedrop import editorial

#: Verbatim from ``SELECT DISTINCT category FROM marketplace_listings`` in
#: production, with the tags each one must produce. Chosen for their awkwardness:
#: the two separator styles, an apostrophe, a full-width comma, a department long
#: enough to test the ceiling, and two rows that differ only in their leaf.
REAL_CATEGORIES = (
    ("Women's Clothing > Tops & Sets > Sweaters", ["sweaters", "womensclothing"]),
    ("Jewelry & Watches / Fashion Jewelry / Rings", ["rings", "jewelrywatches"]),
    ("Home, Garden & Furniture / Home Storage / Furniture", ["furniture", "homegardenfurniture"]),
    (
        "Automobiles & Motorcycles > Tools, Maintenance & Care > Paint Care",
        ["paintcare", "automobilesmotorcycles"],
    ),
    ("Toys， Kids & Baby > Boys Clothing > Boy Accessories", ["boyaccessories", "toyskidsbaby"]),
    ("Men's Clothing > Outerwear & Jackets > Men's Shirts", ["mensshirts", "mensclothing"]),
    ("Women's Clothing > Outerwear & Jackets > Basic Jacket", ["basicjacket", "womensclothing"]),
    ("Women's Clothing > Outerwear & Jackets > Blazers", ["blazers", "womensclothing"]),
    ("Phones & Accessories > Cases & Covers > Silicone Cases", ["siliconecases", "phonesaccessories"]),
    ("Sports", ["sports"]),
)


def _listing(**overrides):
    listing = {"title": "A Product", "category": None, "subcategory": None}
    listing.update(overrides)
    return listing


def _derived(listing):
    """The tags that came from the listing, without the two signing tags."""
    return [t for t in editorial.hashtags(listing) if t not in editorial._HASHTAG_CONSTANTS]


class TestATagIsAWordAMemberCouldHaveTyped:
    """No mid-word cuts, no flattened paths, nothing longer than a word."""

    @pytest.mark.parametrize("category,expected", REAL_CATEGORIES)
    def test_every_category_in_production_reads_as_words(self, category, expected):
        assert _derived(_listing(category=category)) == expected

    def test_the_tag_that_shipped_broken_is_the_tag_that_is_fixed(self):
        """The literal regression: listing 26, live on the feed as post 2500."""
        tags = editorial.hashtags(_listing(category="Women's Clothing > Tops & Sets > Sweaters"))
        assert "womensclothingtopssetssw" not in tags
        assert tags[0] == "sweaters"

    @pytest.mark.parametrize("category,_expected", REAL_CATEGORIES)
    def test_no_tag_exceeds_the_ceiling(self, category, _expected):
        for token in editorial.hashtags(_listing(category=category)):
            assert len(token) <= editorial.HASHTAG_TOKEN_MAX

    def test_a_segment_over_the_ceiling_is_clipped_between_words(self):
        """Nine words, forty-odd characters: the clip must land on a boundary."""
        token = editorial._hashtag_token("Outdoor Recreation And Camping Equipment Supplies")
        assert len(token) <= editorial.HASHTAG_TOKEN_MAX
        # Each surviving word is whole — reassembling them reproduces the token.
        words = editorial._hashtag_words("Outdoor Recreation And Camping Equipment Supplies")
        rebuilt = ""
        for word in words:
            if not token.startswith(rebuilt + word):
                break
            rebuilt += word
        assert rebuilt == token

    def test_one_long_word_with_no_boundary_falls_back_to_a_hard_cut(self):
        """A hard cut is ugly. Dropping the only tag the listing has is worse."""
        token = editorial._hashtag_token("Antidisestablishmentarianism")
        assert token == "antidisestablishmentaria"
        assert len(token) == editorial.HASHTAG_TOKEN_MAX

    def test_both_separator_styles_in_the_column_agree(self):
        slash = _derived(_listing(category="Jewelry & Watches / Fashion Jewelry / Rings"))
        arrow = _derived(_listing(category="Jewelry & Watches > Fashion Jewelry > Rings"))
        assert slash == arrow


class TestATagSaysWhatTheProductIs:
    """The leaf is the product, the root is its department, and leaves differ."""

    def test_two_products_under_one_department_do_not_share_a_tag(self):
        """The collapse bug: these two were identical before, at 24 characters."""
        jacket = _derived(_listing(category="Women's Clothing > Outerwear & Jackets > Basic Jacket"))
        blazer = _derived(_listing(category="Women's Clothing > Outerwear & Jackets > Blazers"))
        assert jacket[0] != blazer[0]
        assert jacket[1] == blazer[1] == "womensclothing"

    def test_the_specific_tag_comes_first(self):
        tags = _derived(_listing(category="Bags & Shoes > Women's Shoes > Woman Slippers"))
        assert tags == ["womanslippers", "bagsshoes"]

    def test_a_subcategory_spends_the_second_slot_on_specificity(self):
        """Two fields means two leaves — the department is the thing to drop."""
        tags = _derived(_listing(category="Electronics > Audio > Headphones", subcategory="Wireless"))
        assert tags == ["headphones", "wireless"]

    def test_a_one_word_category_yields_one_tag_not_two(self):
        assert _derived(_listing(category="Beauty")) == ["beauty"]

    def test_a_meaningless_leaf_falls_back_to_the_segment_above_it(self):
        """``#other`` describes nothing, but the department it sits in does."""
        assert _derived(_listing(category="Home & Garden > Other")) == ["homegarden"]

    def test_a_listing_with_no_category_is_tagged_only_by_its_publisher(self):
        assert editorial.hashtags(_listing()) == list(editorial._HASHTAG_CONSTANTS)

    def test_seller_authored_tags_are_never_republished(self):
        """``tags_json`` is the seller's words, not the platform's."""
        listing = _listing(category="Beauty", tags_json='["luxury", "bestprice", "sale"]')
        assert _derived(listing) == ["beauty"]


class TestTheSigningTagsKeepTheirSlots:
    """However much a category has to say, these two are still published."""

    @pytest.mark.parametrize("category,_expected", REAL_CATEGORIES)
    def test_both_survive_every_real_category(self, category, _expected):
        tags = editorial.hashtags(_listing(category=category))
        for constant in editorial._HASHTAG_CONSTANTS:
            assert constant in tags

    def test_the_budget_is_never_exceeded(self):
        listing = _listing(
            category="Women's Clothing > Tops & Sets > Sweaters",
            subcategory="Cashmere > Crew Neck",
        )
        tags = editorial.hashtags(listing)
        assert len(tags) == editorial.HASHTAG_MAX
        assert len(set(tags)) == len(tags)

    def test_a_category_that_is_already_a_signing_tag_is_not_published_twice(self):
        tags = editorial.hashtags(_listing(category="PulseDrop"))
        assert tags.count("pulsedrop") == 1


class TestTheCaptionCarriesThem:
    """The tags are only worth anything if they reach the post body."""

    def test_the_caption_is_the_title_then_the_tags(self):
        listing = _listing(title="Rib Slim V-neck Sweater", category="Women's Clothing > Tops & Sets > Sweaters")
        body = editorial.caption(listing, editorial.LABELS[editorial.DISCOVERY])
        assert body == (
            "Rib Slim V-neck Sweater\n"
            "#sweaters #womensclothing #pulsesocmarketplace #pulsedrop"
        )

    @pytest.mark.parametrize("category,_expected", REAL_CATEGORIES)
    def test_no_real_listing_can_push_the_caption_into_its_own_truncation(self, category, _expected):
        """``caption`` hard-cuts at 220, which would cut a tag in half again."""
        listing = _listing(title="X" * editorial.TITLE_MAX, category=category)
        body = editorial.caption(listing, editorial.LABELS[editorial.DISCOVERY])
        assert len(body) < editorial.CAPTION_MAX
        for token in editorial.hashtags(listing):
            assert f"#{token}" in body
