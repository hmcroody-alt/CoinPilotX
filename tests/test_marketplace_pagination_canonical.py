"""Pagination must not lie about which page it is.

The defect this file pins: `/pulse/marketplace?page=2` used to emit
`<link rel="canonical" href="https://pulsesoc.com/pulse/marketplace">` — page 2
declaring itself to be page 1 — while *simultaneously* serving
`noindex,follow`. Two contradictory signals about the same URL. Google's current
guidance on paginated sequences is explicit that the first page of a sequence
must not be the canonical for the rest, and that each page gets its own
canonical URL.

The fix is deliberately asymmetric, and the asymmetry is the whole design:

* the **canonical** is built from the *clamped* page (`Page.page`), so it names
  the page that was actually served; and
* **indexability** stays keyed on the *requested* page (`Filters.page`), so no
  URL that is `noindex` today becomes indexable.

That second half is what makes this change safe to ship against a measured
index baseline: it cannot add a single indexable URL. It only stops page 2
claiming to be page 1. `noindex,follow` on page 2+ is retained on purpose —
a self-referential canonical does not oblige the page to be indexable, and
`follow` is load-bearing because most indexable products on this catalogue are
reachable from page 2 only.

The parameter taxonomy these tests encode:

| param      | content-bearing | in canonical                  | indexable |
|------------|-----------------|-------------------------------|-----------|
| `category` | yes             | yes, when it names a real node| yes       |
| `page`     | yes             | yes, clamped, default order   | no (p>=2) |
| `sort`     | no (reorders)   | never                         | no        |
| `q`        | yes (a search)  | never                         | no        |
| tracking   | no              | never                         | n/a       |
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import marketplace_storefront as sf  # noqa: E402
from services import marketplace_web as mw  # noqa: E402

HUB = sf.BASE_PATH
ANON = sf.Viewer()

#: Enough rows that the hub genuinely spans two pages, so "page 2" in these
#: tests is a real page rather than a clamped alias for page 1.
WIDE = 30
NARROW = 3


def _row(index: int, category: str) -> dict:
    return {
        "id": index,
        "listing_id": index,
        "seller_user_id": 9,
        "title": f"Product {index}",
        "price_label": "$24.00",
        "currency": "USD",
        "quantity": 4,
        "category": category,
        "seller_store_name": "Atlas Goods",
        "buyer_visible": True,
        "inventory_state": "available",
    }


def _catalogue() -> list[dict]:
    rows = [_row(i, "Women's Clothing > Tops") for i in range(1, WIDE + 1)]
    rows += [
        _row(i, "Pet Supplies")
        for i in range(WIDE + 1, WIDE + NARROW + 1)
    ]
    return rows


def _slugs() -> tuple[str, str]:
    """The real slugs, read off the taxonomy rather than guessed.

    `build_taxonomy` folds plurals and picks a winner by majority spelling, so a
    hardcoded "pet-supplies" here could silently stop matching any node and turn
    the category assertions into vacuous ones about an unknown category.
    """
    taxonomy = mw.build_taxonomy([r.get("category") for r in _catalogue()])
    by_size = {node.slug: node for node in taxonomy}
    wide = next(s for s, n in by_size.items() if n.count >= WIDE)
    narrow = next(s for s, n in by_size.items() if n.count == NARROW)
    return wide, narrow


WIDE_SLUG, NARROW_SLUG = _slugs()


def render(**filter_kw) -> sf.RenderedPage:
    return sf.render_discovery(
        listings=_catalogue(),
        variants_by_listing={},
        filters=sf.Filters(**filter_kw),
        viewer=ANON,
    )


def canonical_of(**filter_kw) -> str:
    return render(**filter_kw).canonical_path


# ---------------------------------------------------------------------------
# The fixture has to be able to produce a page 2 at all
# ---------------------------------------------------------------------------

def test_the_fixture_really_spans_two_pages():
    """Guards every assertion below from passing vacuously.

    If `WIDE` ever drops under `PAGE_SIZE`, `paginate` clamps every page to 1
    and the "page 2" tests would assert the canonical of page 1 while still
    being green. That is the exact failure mode where a fixture's chosen values
    validate an inverted gate, so it is checked rather than assumed.
    """
    assert WIDE + NARROW > mw.PAGE_SIZE
    assert WIDE > mw.PAGE_SIZE
    assert NARROW < mw.PAGE_SIZE
    assert len(render(page=2).listed_ids) > 0


# ---------------------------------------------------------------------------
# §5 — page 2 must not lie that it is page 1
# ---------------------------------------------------------------------------

def test_page_two_is_its_own_canonical_and_not_page_one():
    assert canonical_of(page=2) == f"{HUB}?page=2"


def test_page_two_serves_a_different_product_set_than_page_one():
    """The reason the canonical mattered: these are genuinely different docs."""
    first = set(render(page=1).listed_ids)
    second = set(render(page=2).listed_ids)
    assert first and second
    assert not (first & second)


def test_page_one_canonicalises_to_the_clean_parent():
    """§11: `page=1` is not a distinct URL, so it leaves no trace."""
    assert canonical_of(page=1) == HUB
    assert canonical_of() == HUB


def test_no_canonical_ever_spells_page_one_explicitly():
    for kw in ({}, {"page": 1}, {"page": 0}, {"category": WIDE_SLUG}):
        assert "page=1" not in canonical_of(**kw)


# ---------------------------------------------------------------------------
# §6 — a self-canonical does not imply index,follow
# ---------------------------------------------------------------------------

def test_page_one_is_indexable_and_page_two_is_not():
    assert render(page=1).indexable is True
    assert render(page=2).indexable is False


def test_page_two_stays_noindex_but_keeps_follow():
    """`follow` is not incidental: most products are page-2-only.

    Dropping to `nofollow` here would strand the tail of the catalogue behind a
    link a crawler is told not to traverse.
    """
    assert render(page=2).robots_extra == "noindex,follow"


def test_the_fix_adds_no_indexable_url():
    """The safety property that lets this ship against a measured baseline.

    Every page>=2 request — in range, out of range, odd spelling — stays
    non-indexable. The change moves canonicals, never indexability.
    """
    for page in (2, 3, 99, 999999):
        assert render(page=page).indexable is False


def test_a_clamped_page_two_is_still_not_indexable():
    """A request for page 2 of a one-page department asked for page 2.

    It is served page 1's products and canonicalises to the department root,
    but it must not *also* become a second indexable URL for that root. Keyed
    on the requested page, which is exactly why indexability is not clamped.
    """
    page = render(category=NARROW_SLUG, page=2)
    assert page.canonical_path == f"{HUB}?category={NARROW_SLUG}"
    assert page.indexable is False
    assert page.robots_extra == "noindex,follow"


# ---------------------------------------------------------------------------
# §10 — invalid page values normalise instead of minting URLs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["0", "-1", "abc", "", "1e5", " 1 ", None])
def test_an_unusable_page_value_becomes_page_one(raw):
    filters = sf.Filters.from_args({"page": raw} if raw is not None else {})
    assert filters.page == 1
    page = sf.render_discovery(
        listings=_catalogue(),
        variants_by_listing={},
        filters=filters,
        viewer=ANON,
    )
    assert page.canonical_path == HUB
    assert page.indexable is True


def test_a_zero_padded_page_normalises_to_one_spelling():
    """`?page=0002` and `?page=2` are the same document, spelled once."""
    assert sf.Filters.from_args({"page": "0002"}).page == 2
    filters = sf.Filters.from_args({"page": "0002"})
    page = sf.render_discovery(
        listings=_catalogue(),
        variants_by_listing={},
        filters=filters,
        viewer=ANON,
    )
    assert page.canonical_path == f"{HUB}?page=2"


# ---------------------------------------------------------------------------
# §12 — an out-of-range page must not become an indexable empty page
# ---------------------------------------------------------------------------

def test_an_out_of_range_page_canonicalises_to_the_real_last_page():
    """`paginate` clamps, so this URL *served* the last page.

    A canonical built from the request would advertise `?page=999999` as a
    distinct document and invite an unbounded set of soft-404s into the crawl.
    """
    last = mw.paginate(_catalogue(), 1).pages
    assert canonical_of(page=999999) == f"{HUB}?page={last}"
    assert canonical_of(page=999999) == canonical_of(page=last)


def test_an_out_of_range_page_is_never_empty():
    assert len(render(page=999999).listed_ids) > 0


# ---------------------------------------------------------------------------
# §7 — category and page must compose, not collapse
# ---------------------------------------------------------------------------

def test_a_category_page_two_keeps_both_facets():
    assert canonical_of(category=WIDE_SLUG, page=2) == (
        f"{HUB}?category={WIDE_SLUG}&page=2"
    )


def test_a_category_page_two_does_not_collapse_into_anything_else():
    """The four wrong answers, named so a regression says which one it picked."""
    got = canonical_of(category=WIDE_SLUG, page=2)
    assert got != HUB, "collapsed to the hub"
    assert got != f"{HUB}?page=2", "lost the category"
    assert got != f"{HUB}?category={WIDE_SLUG}", "lost the page"


def test_a_single_page_category_clamps_rather_than_inventing_page_two():
    """A department with one page has no page 2, so it canonicalises to itself."""
    assert canonical_of(category=NARROW_SLUG, page=2) == (
        f"{HUB}?category={NARROW_SLUG}"
    )


def test_an_unknown_category_is_dropped_from_the_canonical_with_its_page():
    """A junk slug is not a document; it must not mint one at any depth."""
    assert canonical_of(category="not-a-real-department") == HUB
    assert render(category="not-a-real-department").indexable is False


# ---------------------------------------------------------------------------
# §9 — one deterministic spelling per document
# ---------------------------------------------------------------------------

def test_canonical_parameters_are_in_a_fixed_order():
    got = canonical_of(category=WIDE_SLUG, page=2)
    assert got.index("category=") < got.index("page=")


def test_a_canonical_is_a_fixed_point():
    """§30: feeding a canonical back through the parser reproduces it exactly.

    This is the property that makes the canonical graph acyclic — a canonical
    that parsed into a *different* canonical would be a redirect chain spelled
    in link tags.
    """
    for kw in (
        {},
        {"page": 2},
        {"page": 999999},
        {"category": WIDE_SLUG},
        {"category": WIDE_SLUG, "page": 2},
        {"category": NARROW_SLUG, "page": 2},
    ):
        first = canonical_of(**kw)
        query = first.partition("?")[2]
        args = {
            k: v
            for k, _, v in (part.partition("=") for part in query.split("&") if part)
        }
        reparsed = sf.render_discovery(
            listings=_catalogue(),
            variants_by_listing={},
            filters=sf.Filters.from_args(args),
            viewer=ANON,
        ).canonical_path
        assert reparsed == first, f"{kw} -> {first} -> {reparsed}"


# ---------------------------------------------------------------------------
# §8 / §19 — sort, search and tracking stay out of the canonical
# ---------------------------------------------------------------------------

def test_a_sorted_view_is_not_a_distinct_document():
    """And specifically does not claim to be page 2 of the default order.

    `?page=2&sort=newest` holds different products than `?page=2`. Carrying the
    page across would be a cross-URL canonical between two different sets, so
    the clean parent stays the honest answer.
    """
    assert canonical_of(sort="newest", page=2) == HUB
    assert render(sort="newest", page=2).indexable is False


def test_a_search_result_is_never_canonical_and_never_indexable():
    assert canonical_of(q="shirt", page=2) == HUB
    assert render(q="shirt").indexable is False


def test_tracking_parameters_cannot_reach_the_canonical():
    filters = sf.Filters.from_args(
        {"page": "2", "utm_source": "newsletter", "gclid": "x", "fbclid": "y"}
    )
    page = sf.render_discovery(
        listings=_catalogue(),
        variants_by_listing={},
        filters=filters,
        viewer=ANON,
    )
    assert page.canonical_path == f"{HUB}?page=2"
    for junk in ("utm_source", "gclid", "fbclid", "newsletter"):
        assert junk not in page.canonical_path


def test_the_canonical_carries_only_known_parameters():
    """§8: the canonical is an allowlist, not a query-string passthrough."""
    got = canonical_of(category=WIDE_SLUG, page=2)
    query = got.partition("?")[2]
    keys = {part.partition("=")[0] for part in query.split("&") if part}
    assert keys <= {"category", "page"}


# ---------------------------------------------------------------------------
# §13 — the sequence has to be crawlable as links
# ---------------------------------------------------------------------------

def test_the_pager_links_to_the_next_page_as_a_real_anchor():
    body = render(page=1).body_html
    assert f'<a href="{HUB}?page=2" rel="next">' in body


def test_the_pager_uses_the_clamped_page_so_an_overshoot_still_navigates():
    body = render(page=999999).body_html
    assert 'rel="prev"' in body
    assert f'<a href="{HUB}" rel="prev">' in body


def test_a_category_pager_keeps_the_category_in_every_link():
    body = render(category=WIDE_SLUG, page=1).body_html
    assert f'<a href="{HUB}?category={WIDE_SLUG}&amp;page=2" rel="next">' in body


# ---------------------------------------------------------------------------
# §23 — the head and the structured data must agree with the canonical
# ---------------------------------------------------------------------------

def test_the_rendered_head_canonical_matches_the_pages_own_path():
    head = sf.head_html(render(page=2))
    assert f'<link rel="canonical" href="{mw.PUBLIC_ORIGIN}{HUB}?page=2">' in head
    assert '<meta name="robots" content="noindex,follow">' in head


def test_open_graph_url_never_disagrees_with_the_canonical():
    for kw in ({}, {"page": 2}, {"category": WIDE_SLUG, "page": 2}):
        page = render(**kw)
        head = sf.head_html(page)
        # Escaped, because this is an HTML attribute: `&` between two query
        # parameters must reach the markup as `&amp;`.
        url = sf.esc(f"{mw.PUBLIC_ORIGIN}{page.canonical_path}")
        assert f'<link rel="canonical" href="{url}">' in head
        assert f'<meta property="og:url" content="{url}">' in head


def test_only_the_unfiltered_first_page_can_carry_the_index_graph():
    """`bot.py` gates `index_schema_graph` on `canonical_path == BASE_PATH`.

    `index_schema_graph` hardcodes the unfiltered URL as its `@id`, so any page
    that is *not* the unfiltered first page must fail that gate. Before the
    canonical carried the page, page 2 passed it — and published an `ItemList`
    `@id`-ed as page 1 while listing page 2's products.
    """
    assert render(page=1).canonical_path == sf.BASE_PATH
    for kw in ({"page": 2}, {"page": 999999}, {"category": WIDE_SLUG, "page": 2}):
        assert render(**kw).canonical_path != sf.BASE_PATH


# ---------------------------------------------------------------------------
# §18 — none of this may reach a sitemap
# ---------------------------------------------------------------------------

def test_a_paginated_path_is_not_sitemap_eligible():
    from services import search_visibility

    assert search_visibility.sitemap_eligible(HUB) is True
    for path in (f"{HUB}?page=2", f"{HUB}?category={WIDE_SLUG}&page=2"):
        assert search_visibility.sitemap_eligible(path) is False, path
