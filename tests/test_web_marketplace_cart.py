"""The web Marketplace cart, and the promise it exists to keep.

## What was wrong

``/pulse/marketplace/<id>`` renders two pages off one URL. The anonymous one
(``templates/marketplace_product_public.html``) is the canonical, indexable
product page, and its call to action read **"Sign in to buy"**. The member one
(``bot.pulse_marketplace_listing_page``) offered Contact Seller, Save and
Report. There was no buy button anywhere on the website, and no cart -- the cart
existed only inside the app.

So the site made a promise and then broke it *after* the reader had created an
account, which is the most expensive place to break one. Every status-code
census read the pair as healthy: both renderings answered 200.

## What this file pins

Two things, and it is deliberate that they are in one file rather than two:

* The web can put an item in a cart and show it back (``/pulse/cart``).
* The public page's wording matches what signing in actually does.

Separating them is how the bug came back: the wording lives in a template and
the capability lives in a route, and a suite that only knew about one of them
would have gone green on either half alone.

## No new backend, asserted rather than claimed

``services/marketplace_cart_routes.py`` has answered the native app since before
there was a web cart. Its ``_require_user()`` resolves through
``bot.api_account_user()``, which accepts the web session cookie, so the browser
was always an authenticated caller -- a parallel ``/api/web/cart`` would have
been a second cart over one table. ``test_the_web_calls_the_same_endpoints_the_app_does``
asserts that by driving the six documented paths with nothing but a session
cookie, which is the only form of that claim that cannot rot.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# Importing bot binds DATABASE_URL for the process and runs init_db() at module
# scope, so the env has to be set before the import rather than in setUp.
_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="web_cart_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("COINPILOTX_DB_INIT_STARTUP_MODE", "sync")

import bot  # noqa: E402
from services import app_links  # noqa: E402

CART_API = "/api/pulse/marketplace/cart"

# The page's own delegation handler contains the bare selector
# ``closest('[data-add-to-cart]')`` and is served to sellers too, so a bare
# substring search reports the button present on every rendering. Only the
# button carries the attribute with a value.
ADD_BUTTON = "data-add-to-cart='{listing_id}'"


class WebCartTestCase(unittest.TestCase):
    """One approved seller, one live listing, one buyer who is not the seller."""

    @classmethod
    def setUpClass(cls):
        bot.webhook_app.config["SECRET_KEY"] = "web-cart-tests"
        cls.app = bot.webhook_app

    def setUp(self):
        with self.app.app_context():
            conn = bot.db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
                (f"buyer{id(self)}", f"buyer{id(self)}@example.com", "Cart Buyer"),
            )
            self.buyer_id = cur.lastrowid
            cur.execute(
                "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
                (f"seller{id(self)}", f"seller{id(self)}@example.com", "Cart Seller"),
            )
            self.seller_id = cur.lastrowid
            # `approved` on both the seller and the listing, because
            # `_line_state()` reports `restricted` for anything less and every
            # assertion below about a usable cart would be testing the refusal
            # path instead of the one it names.
            cur.execute(
                "INSERT INTO marketplace_sellers (user_id, status, business_name, display_name) "
                "VALUES (?, ?, ?, ?)",
                (self.seller_id, "approved", "Probe Store", "Probe Store"),
            )
            cur.execute(
                """INSERT INTO marketplace_listings
                   (seller_user_id, title, description, category, price_label, currency,
                    quantity, status, approval_status, delivery_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.seller_id, "Probe Widget", "A widget, for probing.", "Education",
                 "$19.99", "USD", 5, "active", "approved", "digital"),
            )
            self.listing_id = cur.lastrowid
            conn.commit()

        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["account_user_id"] = self.buyer_id

    def seller_client(self):
        handle = self.app.test_client()
        with handle.session_transaction() as session:
            session["account_user_id"] = self.seller_id
        return handle

    def add(self, qty=1, client=None):
        return (client or self.client).post(
            CART_API, json={"listing_id": self.listing_id, "qty": qty})

    # -- the promise ---------------------------------------------------------

    def test_the_public_page_promises_only_what_signing_in_delivers(self):
        """The bug, stated as an assertion over both renderings of one URL.

        Not a string check. The public wording and the member control are
        asserted together, because either one alone goes green in the broken
        state: the old page said "Sign in to buy" with no cart anywhere, and a
        cart with no mention of it on the public page is equally a mismatch.
        """
        anonymous = self.app.test_client().get(f"/pulse/marketplace/{self.listing_id}")
        public_body = anonymous.get_data(as_text=True)
        self.assertEqual(anonymous.status_code, 200)
        self.assertIn("Sign in to add to cart", public_body)
        self.assertNotIn("Sign in to buy", public_body,
                         "the old promise is back, and nothing on the web completes a purchase")

        member_body = self.client.get(f"/pulse/marketplace/{self.listing_id}").get_data(as_text=True)
        self.assertIn(ADD_BUTTON.format(listing_id=self.listing_id), member_body)
        self.assertIn(CART_API, member_body)

    def test_a_seller_is_not_offered_a_button_the_server_would_refuse(self):
        """``cart_add`` answers OWN_LISTING for a seller's own item.

        Both halves are checked. Hiding the button is presentation; the refusal
        is the rule, and a test that only checked the markup would pass against
        a page that hid the button while the endpoint had stopped refusing.
        """
        seller = self.seller_client()
        body = seller.get(f"/pulse/marketplace/{self.listing_id}").get_data(as_text=True)
        self.assertNotIn(ADD_BUTTON.format(listing_id=self.listing_id), body)

        refused = self.add(client=seller)
        self.assertEqual(refused.status_code, 400)
        # `_error(code=...)` puts the code on the wire as `error_code`, which is
        # the key `pulseApi` reads; a bare `code` is not in the response at all,
        # so asserting it would have accepted any 400 the route ever returns.
        self.assertEqual((refused.get_json() or {}).get("error_code"), "OWN_LISTING")

    # -- the cart page ------------------------------------------------------

    def test_the_cart_requires_a_signed_in_member(self):
        response = self.app.test_client().get("/pulse/cart")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("Location", ""))
        self.assertIn("next=/pulse/cart", response.headers.get("Location", ""))

    def test_the_cart_page_renders_and_loads_its_own_script(self):
        """The lines are drawn by JS from the API, so the script is the page.

        Without this the route could answer 200 forever while rendering an empty
        shell -- and the shell interpolates ``script_html`` *inside* a
        ``<script>`` element, so a ``<script src>`` routed through that
        parameter would be nested and silently never fetched. That is the exact
        mistake this asserts against.
        """
        response = self.client.get("/pulse/cart")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("data-cart-root", body)
        self.assertIn("/static/js/pulsesoc_cart.js", body)
        self.assertNotIn("<script src='/static/js/pulsesoc_cart.js'></script></script>", body)

    def test_the_script_knows_every_state_the_server_can_send(self):
        """An unknown state fails quietly and the buyer pays for less.

        ``pulsesoc_cart.js`` maps each ``_line_state()`` word to a label and to
        ``buys``, and a state missing from that map falls to a default of
        ``buys: false`` -- so renaming a state server-side would not error, it
        would print "Unavailable" and silently drop the line from the subtotal.
        Deliberately ``assertEqual`` on the sets rather than a subset check: a
        state the server can no longer send is dead UI, and finding out means
        reading the page's vocabulary against the server's, in both directions.
        """
        source = os.path.join(REPO, "services", "marketplace_cart_routes.py")
        with open(source, encoding="utf-8") as handle:
            body = handle.read()
        start = body.index("def _line_state(")
        served = set(re.findall(r'return "([a-z_]+)"', body[start:body.index("\ndef ", start)]))

        script = os.path.join(REPO, "static", "js", "pulsesoc_cart.js")
        with open(script, encoding="utf-8") as handle:
            js = handle.read()
        block = js[js.index("var STATES = {"):js.index("function stateOf(")]
        known = set(re.findall(r"^\s+([a-z_]+):\s+\{", block, re.M))

        self.assertEqual(known, served,
                         "pulsesoc_cart.js and _line_state() disagree about line states")

    def test_the_app_handoff_href_comes_from_the_registry(self):
        """Injected by the route, not written into the JavaScript.

        ``services/app_links.py`` owns what a PulseSoc destination's link looks
        like; a literal in a script file would be a second registry that could
        not be flipped with the first. It must also be the ``/open/...``
        interstitial rather than the canonical marker link, because iOS does not
        consult associated domains for a same-domain tap and a marker link would
        send an installed member to the App Store.
        """
        body = self.client.get("/pulse/cart").get_data(as_text=True)
        expected = app_links.open_interstitial_url("cart", source="web")
        self.assertIn(f'data-cart-app-href="{expected}"', body)
        self.assertIn("/open/cart", expected)
        self.assertNotIn("pulse_app=1", expected)

    def test_the_source_is_one_the_registry_recognises(self):
        """``normalize_source`` folds an unknown source to "system" in silence.

        The first cut of the route passed ``source="web_cart"`` and got
        ``?pulse_src=system`` back with no error, so every web cart handoff would
        have been attributed to the wrong surface. This pins the round trip
        rather than the spelling.
        """
        self.assertIn("web", app_links.APP_LINK_SOURCES)
        href = app_links.open_interstitial_url("cart", source="web")
        self.assertIn("pulse_src=web", href)

    def test_the_cart_is_in_the_navigation(self):
        """``pulse_shell_rail_items`` is the one catalogue both shells read.

        A cart reachable only by typing the URL is not a cart a buyer has.
        """
        hrefs = [href for _label, href, _icon in bot.pulse_shell_rail_items()]
        self.assertIn("/pulse/cart", hrefs)

    # -- the API the app already had ---------------------------------------

    def test_the_web_calls_the_same_endpoints_the_app_does(self):
        """Six documented paths, driven with nothing but a session cookie.

        This is the form of "no new backend" that cannot rot into a comment. If
        someone later adds a web-only cart API, this still passes -- and the
        manifest and route-auth gates would catch the new routes -- but if the
        shared endpoints stop accepting a cookie, every one of these fails and
        the web cart is the thing that broke.
        """
        added = self.add(qty=2)
        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.get_json()["badge_count"], 2)

        listed = self.client.get(CART_API)
        self.assertEqual(listed.status_code, 200)
        lines = listed.get_json()["lines"]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["state"], "available")
        line_id = lines[0]["line_id"]

        self.assertEqual(
            self.client.patch(f"{CART_API}/{line_id}", json={"qty": 3}).status_code, 200)
        self.assertEqual(self.client.get(CART_API).get_json()["lines"][0]["qty"], 3)

        self.assertEqual(
            self.client.post(f"{CART_API}/{line_id}/confirm-price", json={}).status_code, 200)

        options = self.client.get(f"{CART_API}/checkout-options")
        self.assertEqual(options.status_code, 200)
        self.assertIn("card_payments_available", options.get_json())

        self.assertEqual(self.client.delete(f"{CART_API}/{line_id}").status_code, 200)
        self.assertEqual(self.client.get(CART_API).get_json()["lines"], [])

    def test_the_cart_is_one_cart_not_a_web_copy(self):
        """The whole justification for shipping a web cart before web checkout.

        ``marketplace_cart_items`` is keyed on the buyer, so a line added by the
        browser is the same row the app reads. This is asserted at the table,
        because "they share a cart" read off two API responses would also be
        true of two tables that happened to agree.
        """
        self.add(qty=2)
        with self.app.app_context():
            conn = bot.db()
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, listing_id, qty FROM marketplace_cart_items "
                "WHERE user_id=? AND listing_id=?",
                (self.buyer_id, self.listing_id))
            rows = cur.fetchall()
        self.assertEqual(len(rows), 1, "the web cart did not write marketplace_cart_items")

    def test_one_buyers_cart_is_not_another_buyers(self):
        """Every cart route filters on ``user_id``; this is the check that it does.

        A cart page that read the table without that predicate would look
        perfect in every single-user test above.
        """
        self.add(qty=2)
        other = self.app.test_client()
        with self.app.app_context():
            conn = bot.db()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
                (f"other{id(self)}", f"other{id(self)}@example.com", "Other Buyer"))
            other_id = cur.lastrowid
            conn.commit()
        with other.session_transaction() as session:
            session["account_user_id"] = other_id
        self.assertEqual(other.get(CART_API).get_json()["lines"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
