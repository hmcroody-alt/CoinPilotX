"""Discovery for the Marketplace storefront: taxonomy, facets, search, sort, paging.

The fixture is seeded with the category strings production actually carries,
verbatim -- both delimiters, the apostrophes, the fullwidth comma. That matters
because every interesting behaviour in ``marketplace_catalog`` is a response to
how dirty those strings are, and a fixture that tidied them up would test a
catalogue PulseSoc does not have.

Run this file in its own pytest process. It builds real schema through
``bot.init_db()`` and writes to the same tables other suites seed differently.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services import marketplace_catalog as catalog  # noqa: E402

SELLER_ID = 90_210
OTHER_SELLER_ID = 90_211

#: Verbatim from production's 15 published listings, plus two spelling variants
#: that only folding can merge. Do not "clean" these.
SEEDS = [
    # (title, category, price_label, price_minor, published_at)
    ("Basic Jacket Navy", "Women's Clothing > Outerwear & Jackets > Basic Jacket", "$38.00", 3800, "2026-09-01T10:00:00Z"),
    ("Basic Jacket Olive", "Women's Clothing > Outerwear & Jackets > Basic Jacket", "$41.50", 4150, "2026-09-02T10:00:00Z"),
    ("Sweater Cable Knit", "Women's Clothing > Tops & Sets > Sweaters", "$29.99", 2999, "2026-09-03T10:00:00Z"),
    ("Woman Jeans Straight", "Womens Clothing > Bottoms > Woman Jeans", "$52.00", 5200, "2026-09-04T10:00:00Z"),
    ("Solid Tee", "Men's Clothing > T-Shirts > Solid", "$18.00", 1800, "2026-09-05T10:00:00Z"),
    ("Print Tee", "MENS  CLOTHING > T-Shirts > Print", "$21.00", 2100, "2026-09-06T10:00:00Z"),
    ("Fashion Ring Silver", "Jewelry & Watches / Fashion Jewelry / Rings", "$12.00", 1200, "2026-09-07T10:00:00Z"),
    ("Fashion Ring Gold", "Jewelry & Watches / Fashion Jewelry / Rings", "$35.00", 3500, "2026-09-08T10:00:00Z"),
    ("Silicone Case", "Phones & Accessories > Cases & Covers > Silicone Cases", "", None, "2026-09-09T10:00:00Z"),
    ("Nail Art Kit", "Health, Beauty & Hair > Nail Art & Tools > Nail Art Kits", "Request access", 0, "2026-09-10T10:00:00Z"),
    ("Storage Bin", "Home, Garden & Furniture / Home Storage / Furniture", "$9.99", 999, "2026-09-11T10:00:00Z"),
    # U+FF0C fullwidth comma: the same category as a plain comma would give.
    ("Toy Blocks", "Toys， Kids & Baby > Blocks > Wooden", "$24.00", 2400, "2026-09-12T10:00:00Z"),
    ("Tote Bag", "Bag & Shoes > Handbags > Totes", "$44.00", 4400, "2026-09-13T10:00:00Z"),
    ("Sneakers", "Bags & Shoes > Sneakers > Low Top", "$68.00", 6800, "2026-09-14T10:00:00Z"),
]


def _seed(cur, bot):
    for user_id, hidden, account_status in (
        (SELLER_ID, 0, "active"),
        (OTHER_SELLER_ID, 0, "suspended"),
    ):
        cur.execute(
            "INSERT OR REPLACE INTO users (user_id, username, hidden_from_discovery, account_status) "
            "VALUES (?,?,?,?)",
            (user_id, f"catalogfixture{user_id}", hidden, account_status),
        )
        cur.execute(
            "INSERT OR REPLACE INTO marketplace_sellers (user_id, status, display_name) "
            "VALUES (?,'approved',?)",
            (user_id, f"Catalog Fixture Store {user_id}"),
        )

    for title, category, label, minor, published in SEEDS:
        cur.execute(
            "INSERT INTO marketplace_listings "
            "(seller_user_id, title, category, price_label, price_minor, status, "
            " approval_status, product_type, quantity, featured, published_at, created_at) "
            "VALUES (?,?,?,?,?, 'published','approved','physical', 5, 0, ?, ?)",
            (SELLER_ID, title, category, label, minor, published, published),
        )

    # Three listings that must never appear, one per exclusion rule. Their
    # absence is the only thing proving base_where() applies both predicates.
    cur.execute(
        "INSERT INTO marketplace_listings "
        "(seller_user_id, title, category, price_label, price_minor, status, "
        " approval_status, product_type, quantity, published_at, created_at) "
        "VALUES (?,?,?,?,?, 'published','pending_review','physical', 5, ?, ?)",
        (SELLER_ID, "Unapproved Jacket", "Women's Clothing > Outerwear & Jackets > Basic Jacket",
         "$99.00", 9900, "2026-09-20T10:00:00Z", "2026-09-20T10:00:00Z"),
    )
    cur.execute(
        "INSERT INTO marketplace_listings "
        "(seller_user_id, title, category, price_label, price_minor, status, "
        " approval_status, product_type, quantity, published_at, created_at) "
        "VALUES (?,?,?,?,?, 'draft','approved','physical', 5, ?, ?)",
        (SELLER_ID, "Draft Jacket", "Women's Clothing > Outerwear & Jackets > Basic Jacket",
         "$98.00", 9800, "2026-09-20T10:00:00Z", "2026-09-20T10:00:00Z"),
    )
    cur.execute(
        "INSERT INTO marketplace_listings "
        "(seller_user_id, title, category, price_label, price_minor, status, "
        " approval_status, product_type, quantity, published_at, created_at) "
        "VALUES (?,?,?,?,?, 'published','approved','physical', 5, ?, ?)",
        (OTHER_SELLER_ID, "Suspended Seller Jacket",
         "Women's Clothing > Outerwear & Jackets > Basic Jacket",
         "$97.00", 9700, "2026-09-20T10:00:00Z", "2026-09-20T10:00:00Z"),
    )


class CatalogTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import bot  # noqa: PLC0415 -- import runs init_db(); keep it out of collection

        cls.bot = bot
        cls.conn = bot.db()
        cur = cls.conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id IN (?,?)",
                    (SELLER_ID, OTHER_SELLER_ID))
        _seed(cur, bot)
        cls.conn.commit()
        cls.cur = cls.conn.cursor()

    @classmethod
    def tearDownClass(cls):
        cur = cls.conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id IN (?,?)",
                    (SELLER_ID, OTHER_SELLER_ID))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)",
                    (SELLER_ID, OTHER_SELLER_ID))
        cur.execute("DELETE FROM users WHERE user_id IN (?,?)", (SELLER_ID, OTHER_SELLER_ID))
        cls.conn.commit()
        cls.conn.close()

    def _titles(self, page):
        return [row["title"] for row in page.rows]


class Folding(CatalogTestCase):
    def test_spelling_variants_fold_to_one_category(self):
        for left, right in (
            ("Men's Clothing", "MENS  CLOTHING"),
            ("Women's Clothing", "Womens Clothing"),
            ("Toys， Kids & Baby", "Toys, Kids & Baby"),
            ("Bag & Shoes", "Bags & Shoes"),
        ):
            with self.subTest(left=left):
                self.assertEqual(catalog.segment_key(left), catalog.segment_key(right))

    def test_the_slug_comes_from_the_fold_not_from_a_spelling(self):
        """A slug is a public URL. It must not move when inventory does.

        If the slug were derived from the most-seen spelling, importing one
        more "Mens Clothing" row than "Men's Clothing" would silently rewrite
        a ranking URL.
        """
        self.assertEqual("mens-clothing", catalog.slugify("Men's Clothing"))
        self.assertEqual("mens-clothing", catalog.slugify("MENS  CLOTHING"))
        self.assertEqual("bags-shoes", catalog.slugify("Bag & Shoes"))

    def test_both_delimiters_split(self):
        self.assertEqual(
            ("Jewelry & Watches", "Fashion Jewelry", "Rings"),
            catalog.category_path("Jewelry & Watches / Fashion Jewelry / Rings"),
        )
        self.assertEqual(
            ("Women's Clothing", "Tops & Sets", "Sweaters"),
            catalog.category_path("Women's Clothing > Tops & Sets > Sweaters"),
        )


class Taxonomy(CatalogTestCase):
    def test_tree_is_derived_from_live_rows_and_counts_them(self):
        facets = catalog.taxonomy(self.cur)
        by_slug = {f.slug: f for f in facets}

        # 4 women's rows seeded; the unapproved / draft / suspended-seller
        # jackets are all in this category and none may be counted.
        self.assertEqual(4, by_slug["womens-clothing"].count)
        self.assertEqual(2, by_slug["mens-clothing"].count)
        self.assertEqual(2, by_slug["jewelry-watches"].count)
        self.assertEqual(2, by_slug["bags-shoes"].count)

    def test_label_prefers_the_apostrophe_spelling(self):
        facets = catalog.taxonomy(self.cur)
        by_slug = {f.slug: f for f in facets}
        self.assertEqual("Women's Clothing", by_slug["womens-clothing"].label)
        self.assertEqual("Men's Clothing", by_slug["mens-clothing"].label)

    def test_no_empty_facet_is_ever_produced(self):
        def walk(facets):
            for facet in facets:
                yield facet
                yield from walk(facet.children)

        for facet in walk(catalog.taxonomy(self.cur)):
            with self.subTest(slug=facet.slug):
                self.assertGreater(facet.count, 0)


class Visibility(CatalogTestCase):
    def test_both_predicates_apply(self):
        """Neither half of base_where() is optional.

        Each of these three rows passes one predicate and fails the other, so
        dropping either half would surface exactly one of them.
        """
        titles = self._titles(catalog.query(self.cur, page_size=96))
        for hidden in ("Unapproved Jacket", "Draft Jacket", "Suspended Seller Jacket"):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, titles)

    def test_every_seeded_listing_is_visible(self):
        page = catalog.query(self.cur, page_size=96)
        self.assertEqual(len(SEEDS), page.total)


class Sorting(CatalogTestCase):
    def test_price_low_to_high_is_numeric_not_lexical(self):
        """$9.99 before $12.00 -- the case a string sort gets wrong."""
        page = catalog.query(self.cur, sort="price_low", page_size=96)
        priced = [r["price_minor"] for r in page.rows if (r["price_minor"] or 0) > 0]
        self.assertEqual(sorted(priced), priced)
        self.assertEqual("Storage Bin", page.rows[0]["title"])

    def test_unpriced_listings_sort_last_in_both_directions(self):
        """"Request access" is not $0.00, and an empty label is not free."""
        for sort in ("price_low", "price_high"):
            with self.subTest(sort=sort):
                rows = catalog.query(self.cur, sort=sort, page_size=96).rows
                unpriced = [i for i, r in enumerate(rows) if not (r["price_minor"] or 0)]
                self.assertEqual(
                    list(range(len(rows) - len(unpriced), len(rows))), unpriced,
                    "an unpriced listing surfaced above a priced one",
                )

    def test_price_high_to_low_is_the_exact_reverse_of_the_priced_run(self):
        low = [r["price_minor"] for r in
               catalog.query(self.cur, sort="price_low", page_size=96).rows
               if (r["price_minor"] or 0) > 0]
        high = [r["price_minor"] for r in
                catalog.query(self.cur, sort="price_high", page_size=96).rows
                if (r["price_minor"] or 0) > 0]
        self.assertEqual(low, list(reversed(high)))

    def test_newest_orders_by_publication(self):
        rows = catalog.query(self.cur, sort="newest", page_size=96).rows
        self.assertEqual("Sneakers", rows[0]["title"])

    def test_an_unknown_sort_falls_back_rather_than_raising(self):
        for junk in ("", None, "price_low; DROP TABLE marketplace_listings", "PRICE_LOW "):
            with self.subTest(junk=junk):
                self.assertIn(catalog.resolve_sort(junk).key, catalog.SORTS)


class Search(CatalogTestCase):
    def test_search_matches_title_and_category(self):
        self.assertEqual(2, catalog.query(self.cur, search="jacket").total)
        self.assertEqual(2, catalog.query(self.cur, search="Rings").total)

    def test_search_is_case_insensitive(self):
        self.assertEqual(
            catalog.query(self.cur, search="SNEAKERS").total,
            catalog.query(self.cur, search="sneakers").total,
        )

    def test_a_typed_wildcard_is_not_a_wildcard(self):
        """A buyer typing % must not match the whole catalogue."""
        self.assertEqual(0, catalog.query(self.cur, search="%").total)
        self.assertEqual(0, catalog.query(self.cur, search="_").total)

    def test_search_cannot_reach_a_hidden_listing(self):
        self.assertEqual(0, catalog.query(self.cur, search="Suspended Seller").total)


class CategoryFilter(CatalogTestCase):
    def test_a_slug_selects_every_spelling_of_its_category(self):
        """The fold has to survive the round trip through SQL.

        "Men's Clothing" and "MENS  CLOTHING" are stored as different strings;
        a prefix match would return one of them.
        """
        page = catalog.query(self.cur, category_slug="mens-clothing", page_size=96)
        self.assertEqual(2, page.total)
        self.assertEqual({"Solid Tee", "Print Tee"}, set(self._titles(page)))

    def test_a_deep_slug_filters_at_its_own_level(self):
        page = catalog.query(self.cur, category_slug="rings", page_size=96)
        self.assertEqual(2, page.total)

    def test_an_unknown_slug_returns_nothing_rather_than_everything(self):
        """Fail closed. A typo'd facet must not render the full catalogue."""
        self.assertEqual(0, catalog.query(self.cur, category_slug="not-a-category").total)


class Pagination(CatalogTestCase):
    def test_page_two_is_the_real_second_page(self):
        first = catalog.query(self.cur, sort="title", page=1, page_size=5)
        second = catalog.query(self.cur, sort="title", page=2, page_size=5)
        self.assertEqual(5, len(first.rows))
        self.assertEqual(len(SEEDS), first.total)
        self.assertEqual(first.total, second.total)
        self.assertFalse(set(self._titles(first)) & set(self._titles(second)))

    def test_paging_partitions_the_result_set_exactly(self):
        seen = []
        for number in range(1, 5):
            seen.extend(self._titles(
                catalog.query(self.cur, sort="title", page=number, page_size=5)))
        self.assertEqual(len(SEEDS), len(set(seen)))

    def test_out_of_range_and_junk_pages_clamp(self):
        for requested in (0, -3, 9_999, "abc", None):
            with self.subTest(requested=requested):
                page = catalog.query(self.cur, page=requested, page_size=5)
                self.assertGreaterEqual(page.page, 1)
                self.assertLessEqual(page.page, page.pages)

    def test_deep_pagination_is_capped(self):
        window = catalog.page_window(catalog.MAX_PAGE + 50, 10_000)
        self.assertLessEqual(max(window), catalog.MAX_PAGE)

    def test_the_pager_is_never_empty(self):
        """An empty window is a page with no way off it.

        Clamping `current` after building the range instead of before produced
        `range(88, 41)` for page 90 of a 40-page ceiling: no links at all.
        """
        for current, pages in ((1, 1), (90, 10_000), (0, 5), (-4, 5),
                               ("abc", 5), (None, None), (3, 2)):
            with self.subTest(current=current, pages=pages):
                self.assertTrue(catalog.page_window(current, pages))


class Related(CatalogTestCase):
    def test_related_comes_from_the_nearest_shared_segment(self):
        page = catalog.query(self.cur, search="Basic Jacket Navy")
        listing = page.rows[0]
        titles = [r["title"] for r in catalog.related(self.cur, listing)]
        self.assertIn("Basic Jacket Olive", titles)
        self.assertNotIn("Basic Jacket Navy", titles)

    def test_related_never_pads_with_unrelated_inventory(self):
        """Fewer results, or none, beats a rail of "anything else we have"."""
        page = catalog.query(self.cur, search="Storage Bin")
        related = catalog.related(self.cur, page.rows[0], limit=8)
        self.assertEqual([], related)

    def test_related_excludes_hidden_listings(self):
        page = catalog.query(self.cur, search="Basic Jacket Navy")
        titles = [r["title"] for r in catalog.related(self.cur, page.rows[0], limit=8)]
        for hidden in ("Unapproved Jacket", "Draft Jacket", "Suspended Seller Jacket"):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, titles)


if __name__ == "__main__":
    unittest.main()
