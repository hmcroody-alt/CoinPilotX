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
                     product_type="physical", title="Linen Duvet Cover Set",
                     category="Home", updated_at=NOW):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listings "
            "(seller_user_id, title, description, short_description, category, price_label, currency,"
            " quantity, product_type, listing_type, status, approval_status, cover_image_url,"
            " safety_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SELLER, title, description, "Washed linen duvet set", category,
             price_label, currency, quantity, product_type, product_type, status, approval_status,
             cover, 7, NOW, updated_at),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def make_variant(self, listing_id, price_cents, *, status="active", currency="USD",
                     variant_key="default"):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listing_variants "
            "(listing_id, seller_user_id, variant_key, price_cents, currency, status,"
            " position, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (listing_id, SELLER, variant_key, price_cents, currency, status, 0, NOW, NOW),
        )
        variant_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return variant_id

    def get(self, listing_id):
        return self.client.get(f"/pulse/marketplace/{listing_id}")

    def ld_json(self, response):
        """The page's *first* JSON-LD block, parsed."""
        body = response.get_data(as_text=True)
        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
        self.assertIsNotNone(match, "the page carries no ld+json block")
        return json.loads(match.group(1))

    def ld_nodes(self, response):
        """Every structured-data node the page declares, however it packaged them.

        The two pages package identically-valid structured data differently:
        the grid emits one block wrapping an ``@graph`` array, while the product
        page emits one ``<script>`` per node. Google reads both the same way, so
        pinning either shape would be asserting the wrapper rather than the
        claim -- and it is the claim (one Product, carrying an Offer, and no
        app or service node riding alongside it) that these tests exist for.
        """
        body = response.get_data(as_text=True)
        blocks = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
        self.assertTrue(blocks, "the page carries no ld+json block")
        nodes = []
        for block in blocks:
            parsed = json.loads(block)
            nodes.extend(parsed["@graph"] if "@graph" in parsed else [parsed])
        return nodes

    def product_node(self, response):
        nodes = [node for node in self.ld_nodes(response)
                 if node.get("@type") == "Product"]
        self.assertEqual(len(nodes), 1, "expected exactly one Product node")
        return nodes[0]

    def assertPricePill(self, response, amount):
        """The visible price, asserted through the element that carries it.

        A bare substring search for the amount would also match the ``Offer``
        in the page's structured data, so it would pass on a page that told
        Google a price and showed the buyer nothing -- which is one of the two
        halves this file exists to keep in step. ``data-mkt-price`` is the hook
        the storefront's own script reads, so matching it means the pill is
        both present and the one the page treats as the price.
        """
        self.assertRegex(
            response.get_data(as_text=True),
            r"data-mkt-price[^>]*>[^<]*%s" % re.escape(amount),
            "the price pill does not show %s" % amount,
        )


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
        """Standing product requirement: every public web surface routes to the app.

        Pinned on the two links rather than their wording. The wording is
        ``services/app_links.py``'s to choose and it changed when this page
        moved onto the shared storefront renderer; what must not change is that
        a reader already looking at this product can open *this product* in the
        app, and that someone without the app can get it.
        """
        body = self.get(self.make_listing()).get_data(as_text=True)
        self.assertRegex(body, r'data-app-link="product"',
                         "no deep link into this listing in the app")
        self.assertRegex(body, r'data-app-link="app-store"',
                         "no way to install the app from this page")

    def test_the_page_offers_no_control_the_next_click_would_refuse(self):
        """Contact Seller / Save / Report are each a POST needing a session.

        Rendering them would either fail on click or bounce to /login after the
        reader had already committed to an action, so the requirement is stated
        before the click instead.

        Add to cart used to be in that company and no longer is: a visitor has a
        cart of their own, so the button works where it stands. What is left
        here is the set of verbs that genuinely need two named parties or a
        place to save to -- and messaging is offered as an honest sign-in link
        rather than as a control that fails.
        """
        listing_id = self.make_listing()
        body = self.get(listing_id).get_data(as_text=True)
        self.assertNotIn("Contact Seller", body)
        self.assertNotIn("Sign in to add to cart", body)
        self.assertIn("Sign in to message seller", body)
        self.assertRegex(body, r'<button\b[^>]*\bdata-mkt-add="%d"' % listing_id)

    def test_the_buy_control_is_the_same_one_on_both_renderings(self):
        """This test has now contradicted itself twice, and both reversals were
        the same bug receding.

        It began as "Sign in to buy", which led nowhere near buying: signing in
        landed on a member page whose only verbs were Contact Seller, Save and
        Report. A promise broken *after* the reader had created an account,
        which is the most expensive place to break one. The repair was to make
        the promise true -- a member got a cart -- and the assertion became
        "Sign in to add to cart" present on the public page, pinned against the
        member page's button so the wording could not outlive the capability.

        What is left is the last of it. The promise has not been made truer, it
        has been made unnecessary: the visitor gets the button, not a coupon for
        one. So the pairing survives and the direction flips -- both renderings
        must carry the same control, and asserting it on either side alone would
        be the mismatch this test exists to catch.
        """
        listing_id = self.make_listing()
        anonymous = self.get(listing_id).get_data(as_text=True)
        self.assertNotIn("Sign in to add to cart", anonymous)
        self.assertRegex(anonymous, r'<button\b[^>]*\bdata-mkt-add="%d"' % listing_id,
                         "a visitor is shown a product page with no way to buy")
        self.assertIn("/static/js/pulse_marketplace.js", anonymous,
                      "the visitor's add-to-cart control is wired to nothing")

        self.login()
        member = self.get(listing_id).get_data(as_text=True)
        # The member rendering is `marketplace_storefront.render_product` now,
        # so the control is `data-mkt-add`. Matched as an opening tag for the
        # reason the old literal carried its value:
        # `static/js/pulse_marketplace.js` selects on `[data-mkt-add]`, so a
        # name-only search matches the handler on a page with no button.
        self.assertRegex(member, r'<button\b[^>]*\bdata-mkt-add="%d"' % listing_id,
                         "the public page promises a cart the member page does not offer")
        self.assertIn("/static/js/pulse_marketplace.js", member,
                      "the add-to-cart control is not wired to the cart endpoint")

    def test_the_first_buyer_on_a_deployment_is_offered_the_button_too(self):
        """A cart nobody has used yet must not be a cart nobody can start.

        `marketplace_cart_items` is created by `marketplace_cart_routes`'
        `_ensure_schema` and by nothing else -- not by `init_db()` -- so on a
        fresh deployment the table is absent until the first `POST` to the cart
        API. The product page reads a cart count to render its header badge,
        and a failed read answers `None`, which `render_product` documents as
        the caller withholding the entire cart UI, Add to cart included.

        Those two facts met in a deadlock: no button, therefore no POST,
        therefore no table, therefore no button. Every other test in this class
        hides it, because the fixture's own cart traffic creates the table
        before they look -- so the absence is arranged explicitly here.

        The button is the only thing asserted. Whether the *badge* appears with
        no table is a judgement about an empty cart, not about this deadlock.
        """
        listing_id = self.make_listing()
        self.login()

        conn = bot.db()
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS marketplace_cart_items")
        conn.commit()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='marketplace_cart_items'")
        # The drop is the premise. A fixture that silently kept the table would
        # make this test a duplicate of the one above.
        self.assertIsNone(cur.fetchone(), "the cart table survived the drop")
        conn.close()

        member = self.get(listing_id).get_data(as_text=True)
        self.assertRegex(
            member, r'<button\b[^>]*\bdata-mkt-add="%d"' % listing_id,
            "a deployment whose cart table does not exist yet offers no way to "
            "create it: the page withholds Add to cart, and only the POST that "
            "button makes would have created the table")

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


class PublicProductTitleTestCase(PublicMarketplaceFixture):
    """The canonical title and the displayed name are two jobs, one string.

    ``marketplace_web.display_title`` is unit-tested in
    ``tests/test_marketplace_storefront.py``; what is asserted here is the
    wiring, because the risk of this change was never the split itself. It was
    that shortening the heading would also shorten what a search engine reads,
    which would be a silent SEO regression on every long listing -- visible to
    nobody looking at the page.
    """

    #: Listing 14 in production, verbatim. 160 characters, split at the
    #: seller's own first comma.
    LONG = (
        "Upholstered Bed 135 X 190 Cm With LED Lighting, USB Type-C Charging, "
        "Storage Headboard For Cellphones And Tablets, 4ft6 Hydraulic Storage "
        "Bed With Metal Slatted"
    )
    HEAD = "Upholstered Bed 135 X 190 Cm With LED Lighting"

    def test_the_heading_is_shortened_and_the_rest_is_kept_as_a_qualifier(self):
        """Nothing the seller wrote stops being on the page; it is re-divided."""
        body = self.get(self.make_listing(title=self.LONG)).get_data(as_text=True)
        self.assertIn(f'<h1 class="mkt-title">{self.HEAD}</h1>', body)
        self.assertIn('class="mkt-title-qualifier"', body)
        self.assertIn("4ft6 Hydraulic Storage Bed With Metal Slatted", body)

    def test_a_search_engine_still_reads_the_whole_canonical_title(self):
        """The regression this class exists for.

        ``<title>``, ``og:title`` and the ``Product`` node all keep the full
        string. A crawler matching "Hydraulic Storage Bed" must still find this
        page, and the structured-data ``name`` is what a shopping result shows.
        """
        response = self.get(self.make_listing(title=self.LONG))
        body = response.get_data(as_text=True)
        self.assertEqual(self.product_node(response)["name"], self.LONG)
        self.assertRegex(body, r"<title>%s" % re.escape(self.LONG))
        self.assertRegex(
            body, r'<meta property="og:title" content="%s' % re.escape(self.LONG))

    def test_the_breadcrumb_does_not_restate_the_heading_at_full_length(self):
        """The duplication that prompted this: the same sentence twice, the
        first time in 12px grey directly above the ``<h1>``."""
        body = self.get(self.make_listing(title=self.LONG)).get_data(as_text=True)
        self.assertIn(f'<li aria-current="page">{self.HEAD}</li>', body)
        self.assertNotIn(f'<li aria-current="page">{self.LONG}</li>', body)

    def test_a_title_that_does_not_split_renders_no_empty_qualifier(self):
        """An empty ``<p>`` under the heading is a gap with no explanation."""
        body = self.get(self.make_listing()).get_data(as_text=True)
        self.assertIn('<h1 class="mkt-title">Linen Duvet Cover Set</h1>', body)
        self.assertNotIn("mkt-title-qualifier", body)


class PublicProductStoreIdentityTestCase(PublicMarketplaceFixture):
    """A buyer is shown the store, never the person who owns it.

    ``tests/test_marketplace_store_identity.py`` already guards the store
    *name*, and guards it well — but entirely through unit calls and source-text
    greps, with nothing that renders a page. That is precisely how the picture
    and the handle got through: ``users.avatar_url`` was selected under a
    ``seller_avatar_url`` alias with a comment calling it "the store avatar",
    and ``@roody`` was printed under the shop name. Both read as ordinary
    seller-card code. Neither was covered by a single assertion, in any suite.

    So these render the real page and read what a buyer would see. The
    anonymous rendering is the one asserted hardest: it is what a crawler
    indexes and what a shared link opens, so a personal name or face leaking
    there leaks furthest.
    """

    AVATAR = "https://cdn.example/personal-selfie.jpg"
    LOGO = "https://cdn.example/mw-store-logo.png"

    def set_seller_media(self, *, avatar=None, logo=None):
        conn = sqlite3.connect(self.db_path)
        if avatar is not None:
            conn.execute("UPDATE users SET avatar_url=? WHERE user_id=?", (avatar, SELLER))
        if logo is not None:
            conn.execute("UPDATE marketplace_sellers SET logo_url=? WHERE user_id=?", (logo, SELLER))
        conn.commit()
        conn.close()

    def test_the_personal_profile_picture_never_reaches_a_buyer(self):
        """The seller's selfie is set, and must appear nowhere on either page.

        Asserted for the member rendering too. The member page is not indexed,
        but the leak is a privacy leak rather than an SEO one — the owner never
        agreed to put their face on a shop sign, and which stranger is looking
        does not change that.
        """
        self.set_seller_media(avatar=self.AVATAR)
        listing_id = self.make_listing()
        for label, sign_in in (("anonymous", False), ("member", True)):
            with self.subTest(label):
                self.login() if sign_in else self.logout()
                body = self.get(listing_id).get_data(as_text=True)
                self.assertNotIn(self.AVATAR, body)
                self.assertNotIn("personal-selfie", body)

    def test_the_store_logo_is_what_appears_instead(self):
        """And it is the store's own column that supplies it.

        Both are set, so this distinguishes "renders the logo" from "renders
        whichever picture it finds first" — a fallback chain from logo to avatar
        would pass an assertion that only checked the logo was present.
        """
        self.set_seller_media(avatar=self.AVATAR, logo=self.LOGO)
        listing_id = self.make_listing()
        body = self.get(listing_id).get_data(as_text=True)
        self.assertIn(self.LOGO, body)
        self.assertNotIn(self.AVATAR, body)

    def test_a_seller_with_no_logo_gets_a_monogram_not_a_face(self):
        """The empty state is the one the fallback chain used to fill.

        Every seller has a logo column of NULL today, so this is not an edge
        case — it is the whole catalogue, and it is the state in which reaching
        for ``users.avatar_url`` looked most reasonable.
        """
        self.set_seller_media(avatar=self.AVATAR, logo=None)
        listing_id = self.make_listing()
        body = self.get(listing_id).get_data(as_text=True)
        self.assertNotIn(self.AVATAR, body)
        # "M&W Store" escaped, so the monogram is the "M" inside the figure.
        self.assertRegex(body, r'<figure class="mkt-seller-figure" aria-hidden="true">M</figure>')

    def test_the_handle_is_not_presented_to_an_anonymous_buyer(self):
        """``@public_page_seller`` used to print under the shop name.

        A handle is the person's identity on the social product, not the
        store's identity on the commercial one. The anonymous page is also the
        one where it was most useless: ``/pulse/u/<handle>`` redirects a
        signed-out visitor to /login, so the handle named a destination this
        reader could not reach.
        """
        listing_id = self.make_listing()
        body = self.get(listing_id).get_data(as_text=True)
        self.assertNotIn("@public_page_seller", body)
        self.assertIn("M&amp;W Store", body)

    def test_the_handle_survives_as_a_route_and_not_as_a_label(self):
        """Removing it from the page must not break "Message seller".

        That control is a working anchor to ``/pulse/messages/new?q=<handle>``
        precisely so it functions without JavaScript, so the handle still has
        to be *selected* — the rule is that it is only ever spent as a URL.
        Pinning both halves here stops the next person from deleting the column
        from the projection to make the test above pass.
        """
        self.login()
        listing_id = self.make_listing()
        body = self.get(listing_id).get_data(as_text=True)
        self.assertIn("/pulse/messages/new?q=public_page_seller", body)
        self.assertNotIn("@public_page_seller", body)


class StoreLogoWriteRouteTestCase(PublicMarketplaceFixture):
    """Setting a store logo writes store identity and nothing else.

    Here rather than in a seller-side file because this fixture already is the
    shape these need: an approved ``marketplace_sellers`` row, a member with no
    such row, and a Flask client. The tests above prove the buyer never sees the
    personal avatar; these prove the only route that can fill the column it sees
    instead cannot reach across into ``users`` or hand anybody a seller account.
    """

    LOGO = "https://cdn.example/uploaded-store-logo.png"

    def post(self, path, **json_body):
        return self.client.post(path, json=json_body)

    def seller_row(self, user_id=SELLER):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM marketplace_sellers WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def test_a_logo_lands_on_the_column_buyers_read(self):
        self.login(user_id=SELLER, username="public_page_seller")
        response = self.post("/api/pulse/marketplace/store-logo", media_url=self.LOGO)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.seller_row()["logo_url"], self.LOGO)
        # And the buyer page renders it, through the same authority the listing
        # queries use -- a write that no read can see is not a fix.
        listing_id = self.make_listing()
        self.logout()
        self.assertIn(self.LOGO, self.get(listing_id).get_data(as_text=True))

    def test_setting_a_store_logo_does_not_change_the_personal_avatar(self):
        """The two pictures are separate identities and this is the seam.

        A seller choosing a shop logo has not chosen a new profile picture, and
        the reverse is what this whole change undoes. Asserted explicitly
        because the route was written next to the avatar route and shares four
        of its validators -- the destination is the only part that differs.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE users SET avatar_url=? WHERE user_id=?",
                     ("https://cdn.example/selfie.jpg", SELLER))
        conn.commit()
        conn.close()
        self.login(user_id=SELLER, username="public_page_seller")
        self.post("/api/pulse/marketplace/store-logo", media_url=self.LOGO)
        conn = sqlite3.connect(self.db_path)
        avatar = conn.execute(
            "SELECT avatar_url FROM users WHERE user_id=?", (SELLER,)).fetchone()[0]
        conn.close()
        self.assertEqual(avatar, "https://cdn.example/selfie.jpg")

    def test_removing_the_logo_clears_it_and_nothing_else(self):
        self.login(user_id=SELLER, username="public_page_seller")
        self.post("/api/pulse/marketplace/store-logo", media_url=self.LOGO)
        response = self.post("/api/pulse/marketplace/store-logo/remove")
        self.assertEqual(response.status_code, 200)
        row = self.seller_row()
        self.assertFalse(row["logo_url"])
        self.assertEqual(row["display_name"], "M&W Store")
        self.assertEqual(row["status"], "approved")

    def test_uploading_a_logo_cannot_mint_a_seller_account(self):
        """The reason both routes are UPDATE-only.

        ``MEMBER`` has no ``marketplace_sellers`` row because they never applied
        to sell. An INSERT here -- or an UPSERT, which is the natural way to
        write this -- would hand them an approved-shaped seller row as a side
        effect of uploading a picture, bypassing merchant review entirely. The
        answer to "no row" is to apply, so the route refuses.
        """
        self.login(user_id=MEMBER, username="public_page_member")
        for path in ("/api/pulse/marketplace/store-logo",
                     "/api/pulse/marketplace/store-logo/remove"):
            with self.subTest(path):
                response = self.post(path, media_url=self.LOGO)
                self.assertEqual(response.status_code, 403)
                self.assertIsNone(self.seller_row(MEMBER))

    def test_a_signed_out_caller_cannot_set_a_logo(self):
        self.logout()
        response = self.post("/api/pulse/marketplace/store-logo", media_url=self.LOGO)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.seller_row()["logo_url"])


class PublicProductPriceAuthorityTestCase(PublicMarketplaceFixture):
    """The logged-out page may not advertise a price checkout will not charge.

    This page priced from ``price_label`` while the member page and
    ``marketplace_cart_routes._line_price_minor`` priced from
    ``marketplace_listing_variants.price_cents``, and the variants were loaded
    only for signed-in readers -- so the two pages for one product could name
    different numbers and nothing noticed. Against production on 2026-10-01,
    four of the 35 listings in the live Shopping feed did.

    The first fix was a refusal: print no price at all when the label and the
    variants disagreed. That was a concession to the page's own architecture
    rather than the answer anyone wanted. ``Price`` held one amount and two of
    those four rows were ranges, so there was no single number to fall back to;
    rendering a range needed ``marketplace_web.PriceView``, and standing up a
    second price renderer on this page was the thing that caused the defect in
    the first place.

    Unifying this route onto ``marketplace_storefront.render_product`` removed
    the constraint rather than working around it. There is now one price
    renderer for both readers, it is ``PriceView``, and it can state a range.
    So the assertions below are no longer "says nothing" but the stronger
    property the refusal was standing in for: **the page states the price
    checkout will charge, and states the same one in both formats.** A
    contradicted label yields the variant price; two variants yield an
    ``AggregateOffer`` spanning them.

    The pill and the ``Offer`` node are asserted together throughout, because
    they are one claim in two formats -- a buyer reads the first and Merchant
    Center reads the second, and a page that dropped the pill while keeping the
    Offer would still be making the claim to Google.
    """

    def test_a_label_its_variants_agree_with_is_printed_normally(self):
        listing_id = self.make_listing(price_label="$465.74")
        self.make_variant(listing_id, 46574)
        response = self.get(listing_id)
        self.assertEqual(response.status_code, 200)
        self.assertPricePill(response, "465.74")
        self.assertEqual(self.product_node(response)["offers"]["price"], "465.74")

    def test_a_label_its_variants_contradict_is_not_printed(self):
        """Production listing 36's shape: advertised $38.00, charged $2.29."""
        listing_id = self.make_listing(price_label="$38.00")
        self.make_variant(listing_id, 229)
        response = self.get(listing_id)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("38.00", response.get_data(as_text=True))

    def test_the_contradicted_label_yields_the_price_checkout_charges(self):
        """The half a visual check cannot see, and the half Google reads.

        Both halves are asserted here rather than only the structured one.
        Dropping the label is necessary and not sufficient -- a page that
        printed nothing would also satisfy the test above while telling a buyer
        less than it knows, and the row's real price is not a secret: $2.29 is
        what ``marketplace_cart_routes`` will charge for it.
        """
        listing_id = self.make_listing(price_label="$38.00")
        self.make_variant(listing_id, 229)
        response = self.get(listing_id)
        self.assertPricePill(response, "2.29")
        self.assertEqual(self.product_node(response)["offers"]["price"], "2.29")

    def test_two_variants_publish_the_range_rather_than_one_end_of_it(self):
        """Production listing 112, the row that overcharged by $8.41.

        Naming either end as *the* price is the same class of false claim as
        the label was -- ``$27.84`` undersells the large and ``$37.72``
        oversells the small. ``AggregateOffer`` is the construct schema.org
        provides for exactly this, and the pill says the same thing in words.
        """
        listing_id = self.make_listing(price_label="$29.31")
        self.make_variant(listing_id, 2784)
        self.make_variant(listing_id, 3772, variant_key="large")
        response = self.get(listing_id)
        offer = self.product_node(response)["offers"]
        self.assertEqual(offer["@type"], "AggregateOffer")
        self.assertEqual((offer["lowPrice"], offer["highPrice"]), ("27.84", "37.72"))
        self.assertPricePill(response, "27.84")
        self.assertPricePill(response, "37.72")
        # The label was between the two ends, which is why it looked plausible.
        self.assertNotIn("29.31", response.get_data(as_text=True))

    def test_the_page_still_renders_and_stays_indexable(self):
        """Out of the feed, still a real page: the row is otherwise complete,
        and the member page prices it correctly from the same variants."""
        listing_id = self.make_listing(price_label="$38.00")
        self.make_variant(listing_id, 229)
        response = self.get(listing_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Linen Duvet Cover Set", response.get_data(as_text=True))
        self.assertNotIn("noindex", response.get_data(as_text=True))

    def test_a_listing_with_no_variants_prices_from_its_label_as_before(self):
        """Most of the catalogue, and the regression this must not cause."""
        listing_id = self.make_listing(price_label="$465.74")
        self.assertPricePill(self.get(listing_id), "465.74")

    def test_the_anonymous_branch_is_what_loads_the_variants(self):
        """Names the plumbing: the read used to be inside ``if user:``.

        Without it the predicate fails open and this whole class passes while
        the page is still wrong, so the load is asserted through its effect on
        a request that carries no session.
        """

        listing_id = self.make_listing(price_label="$38.00")
        self.make_variant(listing_id, 229)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertNotIn("38.00", self.get(listing_id).get_data(as_text=True))


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
        """Standing product requirement, met without taxing every link.

        Both halves, because they are different promises and the store link is
        only ever allowed beside the contextual one: "Open Marketplace in
        PulseSoc" opens the surface the visitor is already looking at, while
        the badge tells someone who does not have the app where to get it.

        Counted rather than merely found. The requirement is that the page
        promotes the app, not that it nags -- one promotion for a whole grid,
        never one per card -- so three listings are published and the count is
        still expected to be one.
        """
        for _ in range(3):
            self.make_listing()
        body = self.index().get_data(as_text=True)
        self.assertEqual(body.count('data-app-link="marketplace"'), 1)
        self.assertIn("Open Marketplace in PulseSoc", body)
        self.assertEqual(body.count("Download on the App Store"), 1)

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
        self.assertIn("No products are listed yet", body)

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
        self.assertIn("No products are listed yet", body)


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


class MarketplaceCategorySitemapTestCase(PublicMarketplaceFixture):
    """``GET /sitemap-categories.xml`` -- the departments we ask to rank.

    Here rather than in ``tests/test_sitemap_integrity.py`` for the same reason
    the products case is: every assertion below is about which *departments*
    survive the policy, and a department only exists because rows exist. That
    file owns the invariants this child owes whatever it lists.

    These URLs were already indexable and already linked before this sitemap
    existed, so none of these tests is about making a page rankable. They are
    about which of the twelve the live catalogue produces are substantial enough
    to submit, and -- the subtler half -- about the submitted URL being spelled
    the way the grid spells its own canonical.
    """

    def locs(self):
        response = self.client.get("/sitemap-categories.xml")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("<urlset", body)
        return re.findall(r"<loc>([^<]+)</loc>", body), body

    def seed(self, n, category, **kwargs):
        return [self.make_listing(category=category, **kwargs) for _ in range(n)]

    # -- the threshold --------------------------------------------------------

    def test_a_department_below_the_threshold_is_not_submitted(self):
        """Asserted at the boundary, both sides, in one place.

        A one- or two-product department is a page whose title, image and text
        are substantially its single product's, and that product already has a
        page of its own. Submitting both asks Google to choose between two
        descriptions of one thing.

        Seeded with a literal 2, not ``CATEGORY_MIN_INDEXABLE_LISTINGS - 1``.
        Deriving the fixture from the constant moves the fixture whenever the
        constant moves, so this pair of boundary tests would pass for *any*
        value and assert only that the code reads its own setting -- mutations
        dropping the threshold to 1 and raising it to 99 both survived them.
        Spelling the number here means changing the policy requires changing the
        test that states it, which is the right friction for a number that has
        to be explainable at a review.
        """
        self.seed(2, "Mens Clothing")
        locs, _body = self.locs()
        self.assertEqual(locs, [])

    def test_a_department_at_the_threshold_is_submitted(self):
        self.seed(3, "Mens Clothing")
        locs, _body = self.locs()
        self.assertEqual(
            locs, ["https://pulsesoc.com/pulse/marketplace?category=mens-clothing"])

    def test_the_threshold_counts_indexable_products_not_public_ones(self):
        """A department of thin products is a collection of ``noindex`` pages.

        The rows are public -- published, approved, in stock, so the grid shows
        them and the taxonomy counts them -- and every one of their product
        pages sends ``noindex,follow``. Submitting the department would ask
        Google to rank a collection of pages we have asked it to ignore, so the
        count that matters is the eligible one.
        """
        thin = self.seed(5, "Mens Clothing", description="Nice.")
        locs, _body = self.locs()
        self.assertEqual(locs, [])
        self.assertIn('content="noindex,follow"', self.get(thin[0]).get_data(as_text=True))
        # ...and the department page itself is unaffected: it is still a real
        # department and still linked. Withholding it from the sitemap is not
        # the same as asking for it not to be indexed.
        self.assertIn(
            "mens-clothing",
            self.client.get("/pulse/marketplace").get_data(as_text=True))

    def test_only_departments_are_submitted_never_their_sections(self):
        """Depth-2 sections stay crawlable through the department's sub-nav.

        At this catalogue size a section is a near-duplicate of its department,
        and the answer to "index this deeply" is fewer, stronger URLs rather
        than one per level of a supplier's breadcrumb.
        """
        self.seed(4, "Phones & Accessories > Mobile Phone Accessories")
        locs, _body = self.locs()
        self.assertEqual(
            locs, ["https://pulsesoc.com/pulse/marketplace?category=phones-accessories"])

    def test_nothing_is_submitted_from_an_empty_catalogue(self):
        """No soft-404 departments. Unlike the products sitemap, which always
        carries the grid, this child has nothing it owes when there is nothing
        published -- the grid is submitted once, there."""
        locs, _body = self.locs()
        self.assertEqual(locs, [])

    # -- the URL has to be the one the page claims ---------------------------

    def test_every_submitted_department_answers_200_and_asks_to_be_indexed(self):
        """End to end, which is the only version that catches a disagreement.

        A department URL the grid considers unknown renders ``noindex,follow``
        and canonicalises to the bare hub, so submitting it would spend a crawl
        on a page that forwards the crawler straight back to where it came
        from. Nothing about that is visible in the XML.
        """
        self.seed(3, "Mens Clothing")
        self.seed(4, "Womens Clothing")
        locs, _body = self.locs()
        self.assertEqual(len(locs), 2, "nothing was submitted; this proves nothing")
        for loc in locs:
            path = loc.replace("https://pulsesoc.com", "")
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            body = response.get_data(as_text=True)
            self.assertIn('content="index,follow', body, path)
            self.assertIn(f'rel="canonical" href="{loc}"', body, path)

    def test_the_submitted_slug_is_the_spelling_the_grid_answers_on(self):
        """The trap this sitemap is shaped around.

        ``build_taxonomy`` picks a department's slug by majority spelling -- the
        live catalogue carries both "Mens Clothing" and "Men's Clothing" -- and
        the grid tests a requested slug against the taxonomy *it* builds, from
        every public row, with an exact ``==``. So the taxonomy here is built
        from the whole public catalogue and only the *count* looks at
        eligibility. Build both from the eligible subset and the majority can
        flip, at which point the URL we submit is one the grid calls unknown.

        Seeded so the two populations disagree on purpose: the majority spelling
        belongs to four thin rows, the three eligible ones carry the minority.

        The pair is "Bags & Shoes" / "Bag & Shoes" rather than the apostrophe
        pair, and that choice is the whole test. ``slugify`` *drops*
        apostrophes, so "Mens Clothing" and "Men's Clothing" produce one
        identical slug -- they cannot disagree, and a version of this test
        seeded with them survived the mutation that builds the taxonomy from the
        eligible subset. These two fold to one department (``_fold_slug`` strips
        the trailing "s", so both key on ``bag-shoe``) while slugifying
        *differently*, which is exactly the shape where the majority vote
        decides the URL. Both spellings are live in the catalogue.

        Correct: the vote runs over all seven rows and ``bags-shoes`` wins.
        Mutated: it runs over the three eligible rows, ``bag-shoes`` wins, and
        we would submit a URL the grid resolves against its own all-rows
        taxonomy, fails to match with its exact ``==``, and serves
        ``noindex,follow`` with a canonical back to the bare hub.
        """
        self.seed(4, "Bags & Shoes", description="Nice.")
        self.seed(3, "Bag & Shoes")
        locs, _body = self.locs()
        self.assertEqual(
            locs, ["https://pulsesoc.com/pulse/marketplace?category=bags-shoes"])
        body = self.client.get("/pulse/marketplace?category=bags-shoes").get_data(as_text=True)
        self.assertIn('content="index,follow', body)
        # The minority spelling is not a second department and not a second URL.
        self.assertNotIn("category=bag-shoes", _body)

    def test_a_department_is_submitted_by_exactly_one_child_sitemap(self):
        """Two children naming one URL is two ``lastmod`` claims about it, and
        Search Console then reports its coverage twice."""
        self.seed(3, "Mens Clothing")
        category_locs, _body = self.locs()
        product_locs = re.findall(
            r"<loc>([^<]+)</loc>",
            self.client.get("/sitemap-products.xml").get_data(as_text=True))
        self.assertTrue(category_locs)
        self.assertTrue(product_locs)
        self.assertEqual(set(category_locs) & set(product_locs), set())

    # -- lastmod --------------------------------------------------------------

    def test_lastmod_is_the_newest_eligible_product_in_the_department(self):
        """What changes about a department page is the products on it.

        Stamping the crawl date instead is the behaviour that teaches a crawler
        to stop reading the field, and it is what the old sitemap did to all 354
        of its URLs every morning.
        """
        self.seed(3, "Mens Clothing", updated_at="2026-04-01T00:00:00")
        self.make_listing(category="Mens Clothing", updated_at="2026-07-14T00:00:00")
        _locs, body = self.locs()
        entry = re.search(
            r"<loc>https://pulsesoc\.com/pulse/marketplace\?category=mens-clothing</loc>"
            r"\s*<lastmod>([^<]+)</lastmod>", body)
        self.assertIsNotNone(entry, "the department entry carries no lastmod")
        self.assertEqual(entry.group(1), "2026-07-14")

    def test_an_ineligible_product_does_not_date_the_department(self):
        """It is not on the list the department is asking to rank, so its edit
        is not a change to what we submitted."""
        self.seed(3, "Mens Clothing", updated_at="2026-04-01T00:00:00")
        self.make_listing(category="Mens Clothing", description="Nice.",
                          updated_at="2026-07-14T00:00:00")
        _locs, body = self.locs()
        self.assertIn("<lastmod>2026-04-01</lastmod>", body)
        self.assertNotIn("2026-07-14", body)

    # -- failure is logged, not disguised ------------------------------------

    def test_a_failed_query_is_logged_rather_than_passed_off_as_no_departments(self):
        """Shares the failure path with the products sitemap and the feed, and
        an empty ``<urlset>`` is still the right answer to a crawler -- but the
        third reader of one failure is a third chance for the handling to be
        wrong in a way nothing notices."""
        self.seed(3, "Mens Clothing")
        real_db = bot.db
        bot.db = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('relation "marketplace_listings" does not exist'))
        try:
            with self.assertLogs(level="ERROR") as captured:
                entries = bot.marketplace_category_entries()
        finally:
            bot.db = real_db
        self.assertEqual(entries, [])
        self.assertIn("MARKETPLACE_PUBLIC_QUERY_FAILED", "\n".join(captured.output))


class ProductPageScriptBreakoutTestCase(PublicMarketplaceFixture):
    """A seller's title cannot close the ``ld+json`` block on the page as served.

    These pass before the escaping they describe was added anywhere, and that is
    the point of having them here: the renderer this route actually uses is
    ``marketplace_storefront``, which escapes ``<`` already. Nothing asserted it
    end-to-end, so the property held by inspection of one module rather than by a
    test of the response. ``tests/test_marketplace_seo.py`` covers the serialiser
    in the orphaned rollback renderer; this covers whatever renderer the route is
    wired to, which is the thing that can be swapped.

    Why a title is the field to attack rather than a hypothetical one: the write
    path runs ``bot.clean_html``, which deletes matched ``<...>`` pairs. A payload
    with no ``>`` in it never matches that regex and is stored verbatim. And a
    bare ``</script`` followed by whitespace closes a raw-text element on its
    own -- the ``>`` that completes the injected tag comes from the page's own
    markup further down. So the stored value never has to look like a tag.
    """

    BREAKOUT = "Nice Lamp </script <svg onload=alert(1)"

    def test_the_rendered_page_holds_exactly_one_script_element(self):
        """Counting the closing tags is the assertion that would have caught this.
        A breakout does not corrupt the JSON -- it ends the element early and the
        remainder becomes markup, so the parsed block can still look valid.

        The second payload is the textbook one. It is the weaker of the two here
        because ``clean_html`` would strip its matched pairs before storage; it
        is pinned anyway so the test names the case the fix was asked for.
        """
        for title in (self.BREAKOUT, "</script><script>alert(1)</script>"):
            with self.subTest(title=title):
                listing_id = self.make_listing(title=title)
                body = self.get(listing_id).get_data(as_text=True)
                self.assertEqual(body.count('<script type="application/ld+json">'), 1)
                block = re.search(
                    r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
                self.assertNotIn("<", block.group(1))

    def test_the_title_still_reaches_a_consumer_as_the_seller_wrote_it(self):
        """Escaping that changed the name would trade one defect for a quieter
        one: a structured title that disagrees with the visible ``h1``."""
        listing_id = self.make_listing(title=self.BREAKOUT)
        response = self.get(listing_id)
        product = [node for node in self.ld_nodes(response)
                   if node.get("@type") == "Product"][0]
        self.assertEqual(product["name"], self.BREAKOUT)

    def test_the_grid_survives_the_same_title(self):
        """The grid renders its own graph from the same rows."""
        self.make_listing(title=self.BREAKOUT)
        body = self.client.get("/pulse/marketplace").get_data(as_text=True)
        block = re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
        self.assertIsNotNone(block, "the grid carries no ld+json block")
        self.assertNotIn("<", block.group(1))


if __name__ == "__main__":
    unittest.main()
