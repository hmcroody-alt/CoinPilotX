"""``/pulse/marketplace`` and ``/pulse/marketplace/<id>`` for a reader with no session.

Until this change every web marketplace URL answered ``302 -> /login``, for
anonymous humans and for Googlebot alike, so the entire catalogue was invisible
to search. Both routes now branch on **authentication** — never on user-agent,
because serving a crawler a page a visitor cannot get is cloaking — and this file
pins both sides of that branch plus the three things that were easy to get wrong.

The two pages are tested together, in one file, because they are one change:
the product pages are what search needs to reach and the grid is the only
internal path to them. Testing the grid without the product page would leave
"the links work" unasserted, which is the single thing the grid exists for.

## The three

1. **The redirect was in two places.** ``pulse_social_shell`` calls
   ``require_account()`` itself, so removing the route's own guard would have
   changed nothing. That is why the anonymous reader gets a different template
   rather than the same one without a gate, and why "anonymous gets a page" and
   "a member still gets the app shell" are separate cases here.

2. **One URL, two frames.** A second public product path would split one
   product's ranking signal across two URLs and make the canonical a coin toss,
   so the canonical is asserted to be the *same* path that was requested.

3. **``Cache-Control``.** ``add_pwa_headers`` stamps ``no-store`` on every
   ``/pulse/`` response, which is right for the signed-in app and wrong for this
   page: Googlebot and Merchant Center both fetch it, and ``no-store`` makes
   every crawl a full re-download. The view opts out through a request-scoped
   flag, and the member path must keep ``no-store``. Both are asserted, because
   the first failed silently the first time — the view set the header and the
   hook overwrote it on the way out.

Visibility is *not* relaxed by any of this. The route applies the same two
predicates it always did, so the 404 cases are here to prove that being logged
out widened nothing.

Runs against a temp sqlite file, so nothing here touches coinpilotx.db.

Run: python3 -m pytest tests/test_marketplace_public_pages.py
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="mkt_public_page_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import marketplace_seo  # noqa: E402


def pin_database():
    """Point DATABASE_URL at *this* file's temp sqlite, again, per test.

    Setting it once above the import is not enough, and the reason is a property
    of the app rather than of pytest: on SQLite, ``services.db.connect()`` re-reads
    DATABASE_URL on **every call** (services/db.py:1072), so the database a request
    is answered from is chosen at request time by whatever the environment says
    then -- not by what this module said when it was imported.

    Several other suites also point DATABASE_URL at a temp file of their own
    above their own ``import bot``, and pytest imports every selected module
    during collection, before running a single test. So the last module imported
    silently owns the database for every test in the process.

    Measured: ``pytest tests/protection/test_route_auth.py
    tests/test_marketplace_seo.py tests/test_marketplace_public_pages.py``
    failed all 24 tests in this file while each file passed alone. The failures
    read as missing prices, missing CTAs and missing cache headers -- i.e. as
    twenty-four unrelated regressions in the page -- because every listing this
    file seeded was invisible to the app and every request 404ed. Nothing in that
    output pointed at a database.

    Re-pinning here rather than making the other suite defer: that file points
    DATABASE_URL somewhere harmless for the same reason this one does, and
    neither can know whether it was imported last. A test that states its own
    preconditions in ``setUp`` does not depend on collection order at all.

    This is half the fix; ``setUpClass`` holds the other half, because pinning
    the database only moves the problem to which database has tables.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

SELLER = 95101
MEMBER = 95102
NOW = "2026-09-01T00:00:00"

DESCRIPTION = ("A washed European linen duvet cover with two pillowcases, prewashed "
               "so it arrives soft and does not shrink in the first wash.")


class PublicMarketplaceFixture(unittest.TestCase):
    """Seeding and session control shared by both pages. Holds no tests itself.

    A base class rather than a second file: the database pinning below is subtle
    enough that a copy of it in another file would be a copy that drifts, and the
    two pages have to be able to assert things about each other -- that a card's
    link resolves to a real product page is the grid's entire purpose.
    """

    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        pin_database()
        # `init_db()` returns early on a process global (`INIT_DB_COMPLETED`,
        # bot.py:117235), which is right in production -- it runs once per worker
        # -- and wrong for the second suite in a pytest process: the first one
        # already built the schema, in a temp database of its own, so this call
        # is a no-op and this file's database has no tables at all. The only sign
        # is one line on stdout, `DB_INIT_SKIPPED_ALREADY_DONE`, captured and
        # hidden unless a test fails.
        #
        # Clearing the flag rather than exporting FORCE_INIT_DB, which is the
        # other supported way in: the environment variable is read on every
        # `init_db()` call, including the ones a request triggers, so leaving it
        # set changes behaviour well outside this class.
        bot.INIT_DB_COMPLETED = False
        bot.init_db()
        cls._real_require_account = bot.require_account
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.require_account = cls._real_require_account

    def logout(self):
        """The case this whole file is about: no session at all."""
        bot.require_account = lambda *args, **kwargs: None

    def login(self, user_id=MEMBER, username="public_page_member"):
        bot.require_account = lambda *args, **kwargs: {
            "user_id": user_id, "username": username, "email": f"{username}@example.com"}

    def setUp(self):
        pin_database()
        self.logout()
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id=?", (SELLER,))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id=?", (SELLER,))
        for user_id, username in ((SELLER, "public_page_seller"), (MEMBER, "public_page_member")):
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                        (user_id, username, username))
        cur.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (SELLER, "M&W Store", "approved", NOW, NOW),
        )
        conn.commit()
        conn.close()

    # -- helpers --------------------------------------------------------------

    def make_listing(self, *, status="published", approval_status="approved",
                     description=DESCRIPTION, price_label="$465.74", currency="USD",
                     cover="https://cdn.example/bed.jpg", quantity=12,
                     product_type="physical"):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listings "
            "(seller_user_id, title, description, short_description, category, price_label, currency,"
            " quantity, product_type, listing_type, status, approval_status, cover_image_url,"
            " safety_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SELLER, "Linen Duvet Cover Set", description, "Washed linen duvet set", "Home",
             price_label, currency, quantity, product_type, product_type, status, approval_status,
             cover, 7, NOW, NOW),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def get(self, listing_id):
        return self.client.get(f"/pulse/marketplace/{listing_id}")

    def ld_json(self, response):
        """The page's one JSON-LD block, parsed."""
        body = response.get_data(as_text=True)
        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
        self.assertIsNotNone(match, "the page carries no ld+json block")
        return json.loads(match.group(1))

    def product_node(self, response):
        graph = self.ld_json(response)["@graph"]
        nodes = [node for node in graph if node.get("@type") == "Product"]
        self.assertEqual(len(nodes), 1, "expected exactly one Product node")
        return nodes[0]


class MarketplacePublicProductPageTestCase(PublicMarketplaceFixture):
    """``GET /pulse/marketplace/<id>`` -- the page a search result lands on."""

    # -- an anonymous reader gets a page, not a redirect ----------------------

    def test_an_anonymous_reader_gets_the_product_page(self):
        """The whole mission in one assertion: this used to be a 302 to /login."""
        response = self.get(self.make_listing())
        self.assertEqual(response.status_code, 200)
        self.assertIn("Linen Duvet Cover Set", response.get_data(as_text=True))

    def test_googlebot_gets_exactly_what_an_anonymous_person_gets(self):
        """The branch is on authentication, never on user-agent.

        Asserted byte-for-byte rather than by spot-checking fields, because
        cloaking is not a bug that shows up as a wrong value -- it shows up as
        two responses, and the only way to see it is to compare them whole.
        """
        listing_id = self.make_listing()
        human = self.get(listing_id).get_data(as_text=True)
        crawler = self.client.get(
            f"/pulse/marketplace/{listing_id}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; "
                                   "+http://www.google.com/bot.html)"},
        ).get_data(as_text=True)
        self.assertEqual(human, crawler)

    def test_the_page_states_the_price_the_row_holds(self):
        response = self.get(self.make_listing())
        self.assertIn("465.74", response.get_data(as_text=True))

    def test_the_page_names_the_store_that_sells_it(self):
        """HTML-escaped, which is why the ampersand is asserted in its escaped form."""
        response = self.get(self.make_listing())
        self.assertIn("M&amp;W Store", response.get_data(as_text=True))

    def test_the_page_promotes_the_ios_app(self):
        """Standing product requirement: every public web surface routes to the app."""
        body = self.get(self.make_listing()).get_data(as_text=True)
        self.assertIn("Open in the PulseSoc app", body)

    def test_the_page_offers_sign_in_rather_than_a_dead_buy_button(self):
        """Contact Seller / Save / Report are each a POST needing a session.

        Rendering them would either fail on click or bounce to /login after the
        reader had already committed to an action, so the requirement is stated
        before the click instead.
        """
        body = self.get(self.make_listing()).get_data(as_text=True)
        self.assertIn("Sign in to add to cart", body)
        self.assertNotIn("Contact Seller", body)

    def test_the_sign_in_promise_is_one_the_next_page_keeps(self):
        """The CTA used to read "Sign in to buy" and lead nowhere near buying.

        Signing in landed on the member product page, whose only verbs were
        Contact Seller, Save and Report -- a promise broken *after* the reader
        had created an account, which is the most expensive place to break one.

        So the wording is pinned against the thing that makes it true rather
        than on its own: the member rendering of the same URL must carry the
        add-to-cart control. Asserting the string alone would go green again the
        moment someone removed the button, which is exactly the state this test
        exists to make impossible.
        """
        listing_id = self.make_listing()
        anonymous = self.get(listing_id).get_data(as_text=True)
        self.assertIn("Sign in to add to cart", anonymous)
        self.assertNotIn("data-add-to-cart", anonymous)

        self.login()
        member = self.get(listing_id).get_data(as_text=True)
        # The attribute *with its value* -- the member page's own click handler
        # contains the bare selector `closest('[data-add-to-cart]')`, so
        # searching for the name alone matches a page carrying no button.
        self.assertIn(f"data-add-to-cart='{listing_id}'", member,
                      "the public page promises a cart the member page does not offer")
        self.assertIn("/api/pulse/marketplace/cart", member,
                      "the add-to-cart control is not wired to the cart endpoint")

    def test_the_cover_image_is_on_the_page(self):
        body = self.get(self.make_listing()).get_data(as_text=True)
        self.assertIn("https://cdn.example/bed.jpg", body)

    def test_the_hero_image_is_not_lazy_loaded(self):
        """``loading="lazy"`` on the LCP element delays the very paint Core Web
        Vitals measures, so the first image is the one image not deferred."""
        body = self.get(self.make_listing()).get_data(as_text=True)
        hero = body.index("https://cdn.example/bed.jpg")
        self.assertNotIn('loading="lazy"', body[hero:body.index(">", hero)])

    # -- one URL, one canonical ----------------------------------------------

    def test_the_canonical_is_the_url_that_was_requested(self):
        """Not a second public path. Two paths for one product would split its
        ranking signal and make this tag a guess."""
        listing_id = self.make_listing()
        body = self.get(listing_id).get_data(as_text=True)
        self.assertIn(f'<link rel="canonical" href="https://pulsesoc.com/pulse/marketplace/{listing_id}"',
                      body)

    # -- structured data ------------------------------------------------------

    def test_the_page_carries_a_product_node_with_an_offer(self):
        offer = self.product_node(self.get(self.make_listing()))["offers"]
        self.assertEqual(offer["price"], "465.74")
        self.assertEqual(offer["priceCurrency"], "USD")
        self.assertEqual(offer["availability"], marketplace_seo.IN_STOCK)

    def test_the_offers_url_is_the_canonical_so_the_feed_and_the_page_agree(self):
        """Merchant Center compares the feed's ``link`` against what it fetches."""
        listing_id = self.make_listing()
        offer = self.product_node(self.get(listing_id))["offers"]
        self.assertEqual(offer["url"], f"https://pulsesoc.com/pulse/marketplace/{listing_id}")

    def test_a_physical_listing_with_no_stock_has_no_page_to_be_out_of_stock_on(self):
        """Measured, not assumed: ``public_sql`` withdraws the row before this page.

        The obvious test to write here is "a zero-quantity listing says
        OutOfStock", and it fails -- ``public_sql``'s last clause requires
        ``quantity>0`` for anything that is not a stockless type, so the row is
        not public at all and the page 404s. ``OUT_OF_STOCK`` is therefore
        unreachable through this route today, and that is recorded in
        ``marketplace_seo.availability`` rather than removed, because the two
        predicates being equal is a fact about today's catalogue policy and not a
        property of this page.
        """
        self.assertEqual(self.get(self.make_listing(quantity=0)).status_code, 404)

    def test_a_stockless_listing_is_in_stock_with_no_quantity_at_all(self):
        """The other side of that clause: a course has nothing to count.

        This is the case that proves ``availability`` is reading the lifecycle
        rule rather than the ``quantity`` column -- a bare quantity check would
        call this row unavailable while the catalogue is selling it.
        """
        listing_id = self.make_listing(quantity=0, product_type="course")
        response = self.get(listing_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.product_node(response)["offers"]["availability"],
                         marketplace_seo.IN_STOCK)

    def test_an_unpriced_listing_renders_no_offer_and_no_price_pill(self):
        """No price, no pill -- the same rule the grid and the member page follow.

        An empty pill reads as a price the seller set to nothing, and an Offer
        with no price is a malformed claim rather than an absent one.
        """
        response = self.get(self.make_listing(price_label="Request access"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("offers", self.product_node(response))
        self.assertNotIn("Request access", response.get_data(as_text=True))

    def test_the_graph_does_not_advertise_the_app_and_a_service_alongside_the_product(self):
        """A product page whose graph also declares ``MobileApplication`` and
        ``Service`` describes three entities and asks Google to pick."""
        types = [node.get("@type") for node in self.ld_json(self.get(self.make_listing()))["@graph"]]
        self.assertEqual(types, ["Organization", "WebSite", "WebPage", "Product", "BreadcrumbList"])

    # -- robots ---------------------------------------------------------------

    def test_a_complete_listing_asks_to_be_indexed(self):
        body = self.get(self.make_listing()).get_data(as_text=True)
        self.assertRegex(body, r'<meta name="robots" content="index,follow')

    def test_a_thin_listing_keeps_its_page_and_stops_asking_to_be_ranked(self):
        """``noindex,follow`` and not ``nofollow``: the outbound links are the
        marketplace index and the help pages, which are real crawl paths, and
        ``robots_disallow_prefixes`` only ever disallows ``noindex,nofollow``, so
        this choice also keeps the section crawlable."""
        response = self.get(self.make_listing(description="ok"))
        self.assertEqual(response.status_code, 200)
        self.assertIn('<meta name="robots" content="noindex,follow"', response.get_data(as_text=True))

    # -- caching --------------------------------------------------------------

    def test_the_public_page_is_cacheable(self):
        """The regression that failed silently once already.

        The view sets this header and ``add_pwa_headers`` used to overwrite it
        with ``no-store`` for everything under ``/pulse/``. A ``no-store``
        product page makes every crawl a full re-download of an unchanged page.
        """
        response = self.get(self.make_listing())
        self.assertEqual(response.headers.get("Cache-Control"), "public, max-age=300")

    def test_the_public_page_is_not_marked_private(self):
        response = self.get(self.make_listing())
        self.assertNotIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertIsNone(response.headers.get("Pragma"))

    def test_a_signed_in_member_on_the_same_url_still_gets_no_store(self):
        """The opt-out is per *response*, not per path.

        The same URL renders the signed-in app shell for a member, and that must
        not enter a shared cache. A path-prefix exemption would have cached it.
        """
        listing_id = self.make_listing()
        self.login()
        response = self.get(listing_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    # -- a member still gets the app -----------------------------------------

    def test_a_signed_in_member_gets_the_app_shell_and_not_the_public_page(self):
        """Both halves of the branch, asserted by what only one frame carries.

        The member page has no canonical tag and no JSON-LD; the public page has
        no left-hand app nav. Checking one marker from each is what proves the
        branch went the other way rather than that the page merely rendered.
        """
        listing_id = self.make_listing()
        self.login()
        body = self.get(listing_id).get_data(as_text=True)
        self.assertNotIn("Sign in to add to cart", body)
        self.assertNotIn('<script type="application/ld+json">', body)

    # -- being logged out widened nothing ------------------------------------

    def test_a_listing_that_is_not_public_is_404_for_an_anonymous_reader(self):
        """The predicates are unchanged: a listing that 404ed for a member 404s here.

        404 and not "this was removed", because naming a withdrawn row would
        confirm to anyone guessing ids that the row exists.
        """
        for status, approval in (("draft", "approved"), ("published", "pending"), ("paused", "approved")):
            with self.subTest(status=status, approval=approval):
                listing_id = self.make_listing(status=status, approval_status=approval)
                self.assertEqual(self.get(listing_id).status_code, 404)

    def test_an_unknown_id_is_a_404_and_not_a_500(self):
        self.assertEqual(self.get(98765432).status_code, 404)

    def test_a_listing_from_an_unapproved_seller_is_404(self):
        """Seller state is half of ``public_sql``; opening the page must not
        bypass the half that is about the seller rather than the row."""
        listing_id = self.make_listing()
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE marketplace_sellers SET status='pending' WHERE user_id=?", (SELLER,))
        conn.commit()
        conn.close()
        self.assertEqual(self.get(listing_id).status_code, 404)


class MarketplacePublicIndexPageTestCase(PublicMarketplaceFixture):
    """``GET /pulse/marketplace`` -- the grid, which exists for the crawler.

    The grid was opened for the product pages rather than for itself: a sitemap
    is a hint a crawler may ignore, an internal link is a path it follows. So the
    assertions that matter most here are about the *links* -- that they are the
    canonical product URLs and that following one lands on a real page -- and not
    about how the cards look.
    """

    def index(self):
        return self.client.get("/pulse/marketplace")

    def test_an_anonymous_reader_gets_the_grid_rather_than_a_redirect(self):
        self.make_listing()
        response = self.index()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Linen Duvet Cover Set", response.get_data(as_text=True))

    def test_googlebot_gets_exactly_what_an_anonymous_person_gets(self):
        """Byte-for-byte, for the same reason as on the product page."""
        self.make_listing()
        human = self.index().get_data(as_text=True)
        crawler = self.client.get(
            "/pulse/marketplace",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; "
                                   "+http://www.google.com/bot.html)"},
        ).get_data(as_text=True)
        self.assertEqual(human, crawler)

    def test_a_card_links_to_the_canonical_product_url(self):
        """Not to the app interstitial the member grid uses.

        This is the assertion the whole page is for. The signed-in grid links
        each card through `app_first_href('product', id)`, and a crawler
        following forty of those learns about forty redirects rather than forty
        products -- the internal link that should strengthen a product page would
        point at a URL that cannot be indexed.
        """
        listing_id = self.make_listing()
        body = self.index().get_data(as_text=True)
        self.assertIn(f'href="/pulse/marketplace/{listing_id}"', body)
        self.assertNotIn("/app/open", body)

    def test_following_a_card_link_reaches_a_real_product_page(self):
        """The grid's purpose, end to end, in the one way a crawler would find out."""
        listing_id = self.make_listing()
        body = self.index().get_data(as_text=True)
        hrefs = set(re.findall(r'href="(/pulse/marketplace/\d+)"', body))
        self.assertIn(f"/pulse/marketplace/{listing_id}", hrefs)
        for href in sorted(hrefs):
            with self.subTest(href=href):
                self.assertEqual(self.client.get(href).status_code, 200)

    def test_the_grid_states_the_price_the_row_holds(self):
        self.make_listing()
        self.assertIn("465.74", self.index().get_data(as_text=True))

    def test_an_unpriced_listing_gets_no_price_and_no_invented_prose(self):
        """`Request access` was the member grid's filler for a missing price.

        Same rule as the product page: no price pill rather than an empty one or
        a sentence standing in for a number.
        """
        self.make_listing(price_label="")
        body = self.index().get_data(as_text=True)
        self.assertIn("Linen Duvet Cover Set", body)
        self.assertNotIn("Request access", body)

    def test_the_grid_promotes_the_ios_app_once_rather_than_per_card(self):
        """Standing product requirement, met without taxing every link."""
        self.make_listing()
        body = self.index().get_data(as_text=True)
        self.assertIn("Open the marketplace in the app", body)
        self.assertIn("Download on the App Store", body)

    def test_the_grid_renders_no_buttons_that_need_a_session(self):
        """Contact Seller, Save, Report and Promote are each a POST.

        And the live search field is the same problem in a different shape:
        `/api/pulse/marketplace/search` requires a session, so a search box here
        would be an input that fails on submit.
        """
        self.make_listing()
        body = self.index().get_data(as_text=True)
        for dead in ("data-contact-seller", "data-save-listing", "data-report-listing",
                     "data-promote-content", "data-marketplace-search"):
            with self.subTest(control=dead):
                self.assertNotIn(dead, body)

    def test_the_grid_is_a_collection_page_pointing_at_an_item_list(self):
        response = self.index()
        graph = self.ld_json(response)["@graph"]
        types = [node.get("@type") for node in graph]
        self.assertEqual(types, ["Organization", "WebSite", "CollectionPage",
                                 "ItemList", "BreadcrumbList"])

    def test_the_item_list_carries_urls_and_positions_and_nothing_else(self):
        """One authority per product.

        Restating name, price or image here would publish a second description of
        every product at a different URL, and the two disagree the moment a seller
        edits a price -- this page is cached for five minutes and rebuilt from a
        forty-row query, the product page is not.
        """
        listing_id = self.make_listing()
        graph = self.ld_json(self.index())["@graph"]
        item_list = next(node for node in graph if node.get("@type") == "ItemList")
        self.assertEqual(item_list["numberOfItems"], 1)
        self.assertEqual(item_list["itemListElement"], [{
            "@type": "ListItem",
            "position": 1,
            "url": f"https://pulsesoc.com/pulse/marketplace/{listing_id}",
        }])

    def test_a_thin_listing_is_linked_but_not_listed(self):
        """The split the ItemList docstring argues for, measured.

        The HTML link is a crawl path and must stay complete, or a thin listing
        becomes unreachable and can never recover when its seller writes a
        description. The ItemList is a claim about what we ask to rank, and
        naming a `noindex` page there contradicts itself.
        """
        thin_id = self.make_listing(description="Nice.")
        body = self.index().get_data(as_text=True)
        self.assertIn(f'href="/pulse/marketplace/{thin_id}"', body)

        graph = self.ld_json(self.index())["@graph"]
        item_list = next(node for node in graph if node.get("@type") == "ItemList")
        self.assertEqual(item_list["itemListElement"], [])
        self.assertEqual(item_list["numberOfItems"], 0)

        # And the page it links to really does carry the directive that makes
        # listing it a contradiction.
        self.assertIn('content="noindex,follow"',
                      self.get(thin_id).get_data(as_text=True))

    def test_the_canonical_is_the_index_path_itself(self):
        self.make_listing()
        self.assertIn('rel="canonical" href="https://pulsesoc.com/pulse/marketplace"',
                      self.index().get_data(as_text=True))

    def test_a_populated_grid_asks_to_be_indexed(self):
        self.make_listing()
        self.assertIn('content="index,follow', self.index().get_data(as_text=True))

    def test_an_empty_catalogue_keeps_its_page_and_stops_asking_to_be_ranked(self):
        """A 200 with nothing on it is the soft-404 pattern.

        This is a real state on a new deployment rather than a hypothetical one,
        which is why the page still renders and explains itself instead of 404ing.
        """
        body = self.index().get_data(as_text=True)
        self.assertIn('content="noindex,follow"', body)
        self.assertIn("No products are published right now", body)

    def test_the_grid_is_cacheable(self):
        self.make_listing()
        self.assertEqual(self.index().headers.get("Cache-Control"), "public, max-age=300")

    def test_a_signed_in_member_on_the_same_url_gets_the_member_grid_and_no_store(self):
        """Both halves of the branch, and the per-response cache flag.

        The member grid is the surface that carries the session-dependent
        Marketplace panel -- "Sell on PulseSoc", whose three states are seller
        dashboard, create-a-listing and apply-to-sell -- so its presence is what
        proves the branch went the other way.

        The marker used to be ``data-contact-seller``, a per-card action button,
        and those moved to the product page when the member grid was rebuilt. The
        panel is a better marker for exactly the reason a card button was a worse
        one: it cannot appear on the anonymous document at all, because it is the
        part of the page whose content depends on who is asking.

        Both halves are now actually checked. Every assertion here could be
        satisfied by a page that had simply stopped serving the anonymous branch,
        so the anonymous response is fetched too and required not to carry the
        marker.
        """
        self.make_listing()

        anonymous = self.index().get_data(as_text=True)
        self.assertNotIn("mkt-merchant", anonymous,
                         "the anonymous document carries the member-only panel, "
                         "so its presence below proves nothing about the branch")

        self.login()
        response = self.index()
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("mkt-merchant", body)
        self.assertNotIn('<script type="application/ld+json">', body)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_a_listing_that_is_not_public_is_absent_from_the_grid(self):
        """The catalogue query is unchanged; being logged out widened nothing."""
        hidden_id = self.make_listing(status="draft")
        body = self.index().get_data(as_text=True)
        self.assertNotIn(f'href="/pulse/marketplace/{hidden_id}"', body)
        self.assertIn("No products are published right now", body)


class MarketplaceProductsSitemapTestCase(PublicMarketplaceFixture):
    """``GET /sitemap-products.xml`` -- the list we hand Google directly.

    Here rather than in ``tests/test_sitemap_integrity.py`` because every
    assertion below is about which *rows* survive the filter, and the rows are
    the part that file has no fixture for. That file owns the invariants each
    child sitemap must satisfy whatever it lists -- no ``noindex`` URL, one host,
    no duplicates, no fabricated ``lastmod`` -- and it now runs them over this
    route too.

    The distinction matters because a sitemap route that filters nothing still
    returns valid XML and passes every structural check in that file.
    """

    def locs(self):
        response = self.client.get("/sitemap-products.xml")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("<urlset", body)
        return re.findall(r"<loc>([^<]+)</loc>", body), body

    # -- what is in it --------------------------------------------------------

    def test_a_published_product_is_submitted(self):
        listing_id = self.make_listing()
        locs, _body = self.locs()
        self.assertIn(f"https://pulsesoc.com/pulse/marketplace/{listing_id}", locs)

    def test_the_collection_page_leads_the_list(self):
        """The grid is in no other child sitemap, and it is the page whose links
        Google follows to reach every product."""
        self.make_listing()
        locs, _body = self.locs()
        self.assertEqual(locs[0], "https://pulsesoc.com/pulse/marketplace")

    def test_the_collection_page_is_submitted_even_with_nothing_published(self):
        """An empty catalogue still has a grid, and the grid still explains itself.

        The page sends ``noindex,follow`` in that state, which is a different
        claim from "do not crawl this" -- the URL is how a crawler finds the
        products that appear tomorrow.
        """
        locs, _body = self.locs()
        self.assertEqual(locs, ["https://pulsesoc.com/pulse/marketplace"])

    def test_a_submitted_product_carries_the_row_s_own_updated_at(self):
        """Never today-for-everything: a sitemap that claims the whole catalogue
        changed this morning teaches Google to ignore the field on the rows that
        really did change."""
        listing_id = self.make_listing()
        _locs, body = self.locs()
        entry = re.search(
            rf"<loc>https://pulsesoc\.com/pulse/marketplace/{listing_id}</loc>\s*<lastmod>([^<]+)</lastmod>",
            body)
        self.assertIsNotNone(entry, "the product entry carries no lastmod")
        self.assertTrue(entry.group(1).startswith(NOW[:10]), entry.group(1))

    def test_the_collection_page_claims_no_lastmod(self):
        """It has no honest one. What changes is the 40 rows it happens to render,
        and an absent ``lastmod`` says exactly that."""
        self.make_listing()
        _locs, body = self.locs()
        self.assertRegex(
            body,
            r"<loc>https://pulsesoc\.com/pulse/marketplace</loc>\s*</url>")

    # -- what is filtered out -------------------------------------------------

    def test_a_thin_listing_is_not_submitted(self):
        """The same row-level verdict the page itself carries.

        Its product page sends ``noindex,follow``, so submitting the URL would be
        us asking Google to rank a page we told it not to index -- a
        contradiction Google resolves in favour of the page, at the cost of the
        credibility that makes the true entries useful.
        """
        thin_id = self.make_listing(description="Nice.")
        locs, _body = self.locs()
        self.assertNotIn(f"https://pulsesoc.com/pulse/marketplace/{thin_id}", locs)
        self.assertIn('content="noindex,follow"', self.get(thin_id).get_data(as_text=True))

    def test_an_imageless_listing_is_not_submitted(self):
        imageless_id = self.make_listing(cover="")
        locs, _body = self.locs()
        self.assertNotIn(f"https://pulsesoc.com/pulse/marketplace/{imageless_id}", locs)

    def test_an_unpriced_listing_is_still_submitted(self):
        """``indexable``, not ``feed_eligible``.

        A product with a real description and image but an unparseable
        ``price_label`` is a perfectly good web page that Merchant Center cannot
        accept as an offer. Filtering the sitemap on the feed's rule would
        withhold those pages from Search to satisfy a rule Search does not have.
        """
        unpriced_id = self.make_listing(price_label="Request access")
        locs, _body = self.locs()
        self.assertIn(f"https://pulsesoc.com/pulse/marketplace/{unpriced_id}", locs)

    def test_a_draft_listing_is_not_submitted(self):
        draft_id = self.make_listing(status="draft")
        locs, _body = self.locs()
        self.assertNotIn(f"https://pulsesoc.com/pulse/marketplace/{draft_id}", locs)

    def test_an_unapproved_listing_is_not_submitted(self):
        pending_id = self.make_listing(approval_status="pending")
        locs, _body = self.locs()
        self.assertNotIn(f"https://pulsesoc.com/pulse/marketplace/{pending_id}", locs)

    def test_a_physical_listing_with_no_stock_is_not_submitted(self):
        """It has no page to submit: the catalogue query excludes it, so the URL
        404s. The sitemap and the page have to agree about that too."""
        sold_out_id = self.make_listing(quantity=0)
        locs, _body = self.locs()
        self.assertNotIn(f"https://pulsesoc.com/pulse/marketplace/{sold_out_id}", locs)
        self.assertEqual(self.get(sold_out_id).status_code, 404)

    # -- the two lists have to agree -----------------------------------------

    def test_every_submitted_url_answers_200_and_asks_to_be_indexed(self):
        """End to end, which is the only version that catches a disagreement
        between the sitemap's filter and the page's.

        Both call ``marketplace_seo.eligibility``; this asserts they still do,
        rather than that the code looks like it does.
        """
        self.make_listing()
        self.make_listing(price_label="Request access")
        self.make_listing(description="Nice.")
        locs, _body = self.locs()
        self.assertGreater(len(locs), 2, "nothing was submitted; this proves nothing")
        for loc in locs:
            path = loc.replace("https://pulsesoc.com", "")
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('content="index,follow', response.get_data(as_text=True), path)

    def test_the_sitemap_lists_what_the_grid_s_item_list_lists(self):
        """Two independent statements of the same claim, built by different code.

        The grid's ``ItemList`` and this sitemap both mean "these are the product
        pages we are asking to rank". They are assembled separately -- one from a
        40-row page query, one from a 500-row sitemap query -- so they can drift,
        and a drift means one of the two is lying about the catalogue.
        """
        self.make_listing()
        self.make_listing(description="Nice.")
        item_list = [node for node in json.loads(re.search(
            r'<script type="application/ld\+json">(.*?)</script>',
            self.client.get("/pulse/marketplace").get_data(as_text=True), re.S,
        ).group(1))["@graph"] if node.get("@type") == "ItemList"][0]
        listed = {element["url"] for element in item_list["itemListElement"]}
        locs, _body = self.locs()
        submitted = set(locs) - {"https://pulsesoc.com/pulse/marketplace"}
        self.assertEqual(listed, submitted)

    # -- failure is logged, not disguised ------------------------------------

    def test_a_failed_query_is_logged_rather_than_passed_off_as_an_empty_catalogue(self):
        """An empty products sitemap has two causes and one appearance.

        In production the silence reads as "Search Console discovered 0 URLs"
        with nothing to point at, which is indistinguishable from a marketplace
        nobody has listed anything in. The collection page still goes out: it
        needs no query, and it is the URL that gets the crawler back here.

        The log line is `MARKETPLACE_PUBLIC_QUERY_FAILED` rather than the
        `SITEMAP_PRODUCTS_QUERY_FAILED` it used to be, because the query moved
        into `marketplace_public_listings` and now has two readers -- the sitemap
        and the Merchant Center feed. A name that says "sitemap" would send
        whoever reads it in production to one of the two surfaces it broke.
        """
        self.make_listing()
        real_db = bot.db
        bot.db = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('relation "marketplace_listings" does not exist'))
        try:
            with self.assertLogs(level="ERROR") as captured:
                entries = bot.marketplace_public_entries()
        finally:
            bot.db = real_db
        self.assertEqual(entries, [("/pulse/marketplace", "")])
        self.assertIn("MARKETPLACE_PUBLIC_QUERY_FAILED", "\n".join(captured.output))

    def test_a_failed_query_empties_the_feed_too_rather_than_500ing(self):
        """The other reader of the same query, asserted at the same time.

        The shared helper is the reason this case is worth a second test: its
        `except` returns `[]`, and the sitemap turns that into "just the
        collection page" while the feed turns it into an empty `<channel>`. Both
        are the right answer to a crawler and neither is a 500 -- but they are
        different code paths reading one failure, so a change that fixed the
        sitemap's handling and broke the feed's would otherwise stay green.
        """
        self.make_listing()
        real_db = bot.db
        bot.db = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('relation "marketplace_listings" does not exist'))
        try:
            with self.assertLogs(level="ERROR"):
                listings = bot.marketplace_feed_listings()
        finally:
            bot.db = real_db
        self.assertEqual(listings, [])


if __name__ == "__main__":
    unittest.main()
