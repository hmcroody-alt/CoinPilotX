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
from services import pulse_runtime_assets  # noqa: E402

CART_API = "/api/pulse/marketplace/cart"
STOREFRONT_SCRIPT = "/static/js/pulse_marketplace.js"

# The member page is rendered by ``services/marketplace_storefront.py`` now,
# through the same ``render_product`` the grid links into, so the control is the
# storefront's ``data-mkt-add`` rather than the inline page's old
# ``data-add-to-cart``. The rename is not the point; the button is.
#
# Matched as an opening tag and not as a bare attribute, for the reason the old
# literal carried its value: ``static/js/pulse_marketplace.js`` selects on
# ``[data-mkt-add]``, so a name-only search would match the handler on a page
# carrying no button at all.
def has_add_button(body, listing_id):
    return re.search(r'<button\b[^>]*\bdata-mkt-add="%d"' % listing_id, body) is not None


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
        self.assertNotIn("Sign in to buy", public_body,
                         "the old promise is back, and nothing on the web completes a purchase")
        # This used to assert "Sign in to add to cart" was present, and that was
        # the right assertion while signing in was what the page could honestly
        # offer. The promise has got shorter: the add is the promise now, and it
        # is kept on this page without an account. Both halves still have to
        # agree -- the failure mode the file exists for is one surface
        # advertising what the other does not do -- so the member's control is
        # asserted below, and the visitor's is asserted to be that same control.
        self.assertNotIn("Sign in to add to cart", public_body)
        self.assertTrue(has_add_button(public_body, self.listing_id),
                        "the visitor is shown a product page with no way to buy")

        member_body = self.client.get(f"/pulse/marketplace/{self.listing_id}").get_data(as_text=True)
        self.assertTrue(has_add_button(member_body, self.listing_id),
                        "the public page promises a cart the member page does not offer")
        # The endpoint moved out of the page and into the storefront's script,
        # so the wiring is asserted as the chain it now is: the page loads that
        # script, and that script posts to this endpoint. Dropping the
        # `assertIn(CART_API, member_body)` that used to stand here would have
        # left the button proven present and connected to nothing.
        self.assertIn(STOREFRONT_SCRIPT, member_body)
        with open(os.path.join(REPO, "static", "js", "pulse_marketplace.js"),
                  encoding="utf-8") as handle:
            self.assertIn(CART_API, handle.read())

    def test_a_seller_is_not_offered_a_button_the_server_would_refuse(self):
        """``cart_add`` answers OWN_LISTING for a seller's own item.

        Both halves are checked. Hiding the button is presentation; the refusal
        is the rule, and a test that only checked the markup would pass against
        a page that hid the button while the endpoint had stopped refusing.
        """
        seller = self.seller_client()
        body = seller.get(f"/pulse/marketplace/{self.listing_id}").get_data(as_text=True)
        self.assertFalse(has_add_button(body, self.listing_id))

        refused = self.add(client=seller)
        self.assertEqual(refused.status_code, 400)
        # `_error(code=...)` puts the code on the wire as `error_code`, which is
        # the key `pulseApi` reads; a bare `code` is not in the response at all,
        # so asserting it would have accepted any 400 the route ever returns.
        self.assertEqual((refused.get_json() or {}).get("error_code"), "OWN_LISTING")

    # -- the cart page ------------------------------------------------------

    def test_the_cart_opens_for_a_visitor_and_is_never_shared_cached(self):
        """The inverse of what stood here, which asserted a 302 to ``/login``.

        That redirect was the last wall on the road from a product page to a
        purchase: a visitor could add a line and then could not look at it. A
        visitor has a cart of their own server-side
        (``services/marketplace_guest_customer``), so the page that reads it has
        to open.

        The header assertions are why this is not simply ``assertEqual(200)``.
        ``/pulse/cart`` renders in ``marketplace_storefront.public_document`` --
        the same frame as pages that *are* indexable and shared-cached for five
        minutes -- and it is the first anonymous-reachable marketplace URL whose
        body is one browser's rather than everyone's. A cart served from a shared
        cache is one shopper's basket shown to another, and ``Vary: Cookie``
        alone would not save it, because the guest cookie is what distinguishes
        two carts.
        """
        response = self.app.test_client().get("/pulse/cart")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("data-cart-root", body)
        self.assertIn("/static/js/pulsesoc_cart.js", body)

        cache = response.headers.get("Cache-Control", "")
        self.assertIn("no-store", cache)
        self.assertNotIn("public", cache)
        self.assertIn("Cookie", response.headers.get("Vary", ""))
        self.assertIn('content="noindex', body)

    def test_the_visitors_cart_is_the_members_cart_page(self):
        """One page, not a stripped-down public imitation of one.

        Asserted over the body inside the frame rather than the whole document,
        because the frames genuinely differ -- a member gets the social shell's
        masthead and its sidebar of cards, a visitor the storefront's. What must
        not differ is the cart: a second, simpler guest cart page is exactly the
        thing this work exists to not build.

        The slice runs from the cart root to its own script tag, which is the
        rendered template end to end, so a line, a control or a data attribute
        added for one viewer and not the other fails here.
        """
        def inner(html):
            start = html.index('<div class="cart" data-cart-root')
            end = html.index("</script>", html.index("pulsesoc_cart.js", start))
            return html[start:end]

        self.assertEqual(
            inner(self.app.test_client().get("/pulse/cart").get_data(as_text=True)),
            inner(self.client.get("/pulse/cart").get_data(as_text=True)),
        )

    def test_both_frames_define_the_globals_the_cart_script_calls(self):
        """A page that loads its script and not the script's runtime is dead.

        ``pulsesoc_cart.js`` calls ``pulseApi()`` and ``toast()`` by bare name.
        Both were defined inline by ``pulse_social_shell`` and by nothing else,
        which was correct while the shell was the only frame that ever wrapped
        a page script -- and stopped being correct the moment the visitor's
        cart went out through ``public_document``. The symptom is nasty because
        the page looks right: 200, correct markup, script tag present, and then
        the first ``load()`` throws ``ReferenceError`` and the cart is
        permanently and silently empty. Every other test in this file passed
        while that was true, including the one directly above comparing the two
        bodies -- the bodies *were* identical. What differed was the frame.

        Asserted as one shared file rather than "each frame defines them",
        because two definitions is the other way to pass this, and these two
        encode a wire contract with the server (``pulseApi`` reads
        ``error_code``, and treats ``ok:false`` on a 200 as a failure) that
        only stays one contract while it is one file.

        Not deferred, and that is load-bearing: the cart script is deferred and
        so runs after parsing, but the shell's inline page code runs during it.
        A ``defer`` here would define these after that code had already called
        them.
        """
        src = pulse_runtime_assets.RUNTIME_SRC
        tag = pulse_runtime_assets.runtime_html()
        for who, client in (("a visitor", self.app.test_client()),
                            ("a member", self.client)):
            with self.subTest(who=who):
                body = client.get("/pulse/cart").get_data(as_text=True)
                self.assertIn(tag, body)
                self.assertNotIn(f'<script src="{src}" defer', body)
                self.assertEqual(body.count(src), 1, "loaded twice")
                self.assertNotIn("function pulseApi", body,
                                 "a frame went back to defining its own")

    def test_a_hidden_panel_is_actually_hidden(self):
        """``hidden`` is only a UA rule, and this page outranks it.

        ``pulsesoc_cart.js`` picks which panel is up by toggling the ``hidden``
        attribute on each one. That attribute is nothing but a user-agent
        ``display:none``, so *any* author rule setting ``display`` on the same
        element beats it -- and ``.cart .lines{display:flex}`` is exactly such
        a rule, at matching specificity and later in source order. The result
        was that removing the last item in a cart painted "Nothing in your cart
        yet" *underneath the line that had just been removed*: the empty state
        and the content on screen together, which is the one thing the standing
        rule about empty states forbids.

        Nothing caught it because nothing was wrong with the markup or the
        script -- ``show()`` set the attribute correctly every time. The defect
        was that the attribute had been overruled, which is only observable by
        resolving the cascade.

        Asserted as a reset over ``.cart [hidden]`` rather than as a fix to the
        one clashing rule, because the next panel to gain a ``display`` is the
        next time this returns. There is no app-wide ``[hidden]`` reset in this
        codebase to inherit -- every component declares its own -- so the cart
        has to declare it, and it needs ``!important`` because equal
        specificity is precisely what let the clash happen.
        """
        css = self._cart_style_block(self.client.get("/pulse/cart").get_data(as_text=True))
        rule = re.search(r"\.cart\s+\[hidden\]\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule, "no [hidden] reset scoped to the cart")
        self.assertRegex(rule.group(1), r"display\s*:\s*none\s*!important")

        # And the rule it has to beat is still there, so this stays a live
        # assertion rather than one guarding a clash that no longer exists.
        self.assertRegex(css, r"\.cart\s+\.lines\s*\{[^}]*display\s*:")

    def test_the_toast_does_not_eat_the_next_click(self):
        """An announcement parked over the buttons must not be a target.

        The toast is ``position:fixed`` at the bottom centre for 3.2 seconds,
        which is directly over the Add-to-cart buttons of the grid beneath it.
        Without ``pointer-events:none`` a shopper who adds one product has the
        click on the *next* product silently swallowed by the confirmation of
        the first -- the worst possible place to lose an interaction, because
        the buyer believes they added two things and the cart disagrees.

        Safe to make inert because every writer of this node sets
        ``textContent``, so it never holds anything clickable to begin with.

        Checked in both copies. The runtime builds its own node when the
        document did not ship one, and the member shell ships a styled
        ``#toast`` of its own; fixing either alone leaves the two frames
        behaving differently, which is the divergence this branch exists to
        remove.
        """
        # The assignment, not the file. The comment above that line explains
        # the rule in the same words it is written in, so a search of the whole
        # source goes green on a copy of this file with the declaration deleted
        # and the prose left behind -- which is what a first draft of this test
        # did, and the mutant walked straight through it.
        runtime = open(os.path.join(REPO, "static/js/pulse_runtime.js")).read()
        assignment = re.search(
            r"node\.style\.cssText\s*=\s*((?:\s*\"[^\"]*\"\s*\+?)+);", runtime
        )
        self.assertIsNotNone(assignment, "the runtime stopped styling its toast node")
        css_text = "".join(re.findall(r"\"([^\"]*)\"", assignment.group(1)))
        self.assertIn("pointer-events:none", css_text,
                      "the runtime's own toast node is still a click target")

        shell = self.client.get("/pulse/cart").get_data(as_text=True)
        rule = re.search(r"\.toast\{([^}]*)\}", shell)
        self.assertIsNotNone(rule, "the member shell stopped shipping a .toast rule")
        self.assertIn("pointer-events:none", rule.group(1),
                      "the shell's toast is still a click target")

    def test_the_frame_supplies_the_palette_its_body_class_promises(self):
        """``mkt-public`` without the stylesheet that defines it is a lie.

        ``public_document`` sets ``body class="mkt-public"`` unconditionally,
        and the ``--store-*`` palette that class names is declared in exactly
        one place: ``pulse_marketplace.css``. That file arrived only when a
        page passed ``assets_html()``. The cart is the first body to arrive
        without it, so every token resolved to nothing -- the document still
        *looked* light because ``_PUBLIC_BASE_CSS`` spells its own fallbacks
        inline, which is what hid the gap, but a wrapped body asking for
        ``--store-text-primary`` got silence and fell back to its own dark
        value: near-white text on a near-white page, for the one audience with
        no app to escape to.

        A frame declares what every page it wraps may assume. That is already
        the rule ``assets_html`` states for the ``pulseApi``/``toast`` runtime,
        and a palette named by a class this function sets is the same kind of
        thing.

        Both halves are asserted, because supplying it twice is the other way
        to pass: a page that brings the bundle must still link it once.
        """
        from services import marketplace_storefront as ms

        visitor = self.app.test_client().get("/pulse/cart").get_data(as_text=True)
        self.assertIn('class="mkt-public"', visitor)
        self.assertEqual(visitor.count(ms.CSS_HREF), 1,
                         "the visitor's cart does not get the palette exactly once")

        grid = self.app.test_client().get("/pulse/marketplace").get_data(as_text=True)
        self.assertIn('class="mkt-public"', grid)
        self.assertEqual(grid.count(ms.CSS_HREF), 1,
                         "a page that brings the bundle now links it twice")

    def test_the_cart_reads_its_colours_off_the_frame(self):
        """One body, two documents, and it must be legible in both.

        The cart's palette was eight literals chosen for the dark member shell.
        Wrapped in the light public document those produced invisible text, and
        the fix is not a second stylesheet for visitors -- that is the
        two-marketplaces split this branch exists to close. The body asks the
        document what colour it is.

        The fallback slot is doing the compatibility work: the member shell
        declares no ``--store-*`` anywhere, so each ``var()`` there resolves to
        the literal that was already in place and the signed-in cart is
        unchanged. This pins those fallbacks, so a later edit cannot quietly
        restyle the member cart while making the visitor's look right.
        """
        css = self._cart_style_block(self.client.get("/pulse/cart").get_data(as_text=True))
        expected = {
            "--ink": ("--store-text-primary", "#f4f7f5"),
            "--dim": ("--store-text-muted", "#8d9a92"),
            "--line": ("--store-border-hairline", "rgba(255,255,255,.12)"),
            "--go": ("--store-text-link", "#2ecc71"),
            "--warn": ("--store-status-warning", "#e8c468"),
            "--card": ("--store-bg-card", "#0a0a0a"),
            "--well": ("--store-bg-page", "#070707"),
            "--sunken": ("--store-bg-skeleton", "#141414"),
        }
        for name, (token, fallback) in expected.items():
            with self.subTest(token=name):
                self.assertRegex(
                    css,
                    r"%s\s*:\s*var\(\s*%s\s*,\s*%s\s*\)"
                    % (re.escape(name), re.escape(token), re.escape(fallback)),
                )

        # The point of the exercise: no colour is left spelled as a bare dark
        # literal outside a fallback slot, because that is the one kind that
        # cannot follow the frame.
        stripped = re.sub(r"var\([^)]*\)", "", css)
        leftover = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped))
        self.assertEqual(leftover, set(), "a colour the frame cannot reach")

    @staticmethod
    def _cart_style_block(body):
        """The cart's own ``<style>``, picked out of whichever frame wrapped it.

        Keyed on a declaration only this block has, because both documents ship
        several ``<style>`` elements and the shell's is far larger.
        """
        for block in re.findall(r"<style>(.*?)</style>", body, re.S):
            if "--sunken" in block:
                return block
        raise AssertionError("the cart stopped shipping its stylesheet")

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

    def test_a_line_says_which_variant_it_is(self):
        """Two sizes of one shirt are two rows that must not read identically.

        ``_variant_label()`` exists to make a line self-describing, and the
        serializer has always sent it. The page ignored it, so a cart holding
        Small and Medium showed the same title, the same picture and the same
        price twice -- and "Remove" then removes whichever one the buyer did
        not mean. Asserted against the serializer's own key so that renaming
        the field server-side fails here rather than blanking the line.
        """
        source = os.path.join(REPO, "services", "marketplace_cart_routes.py")
        with open(source, encoding="utf-8") as handle:
            self.assertIn('"variant_label"', handle.read(),
                          "the serializer no longer sends variant_label")

        script = os.path.join(REPO, "static", "js", "pulsesoc_cart.js")
        with open(script, encoding="utf-8") as handle:
            js = handle.read()
        self.assertIn("line.variant_label", js,
                      "the cart page never reads variant_label, so two variants "
                      "of one listing render as the same row twice")

    def test_the_title_column_is_allowed_to_shrink(self):
        """The bug was one CSS keyword, and it has no other symptom.

        The line was ``grid-template-columns:72px 1fr auto``. An ``auto`` track
        claims its min-content width first, and the controls are wide
        ("Accept new price", a quantity box, "Remove"), so on a phone the title
        was left ~150px and a 140-character supplier title wrapped into a
        word-per-line ribbon 16 lines tall. A bare ``1fr`` would not have saved
        it either: a flex track's automatic minimum is min-content, so the
        column refuses to shrink below its longest word.

        ``minmax(0,1fr)`` is what actually lets the column shrink, and it reads
        like a formatting detail, so this pins it. Both layouts are checked --
        the phone one and the one the media query restores -- because the
        starving track was in the wide layout and only the breakpoint keeps it
        away from small screens now.
        """
        page = os.path.join(REPO, "templates", "marketplace_cart.html")
        with open(page, encoding="utf-8") as handle:
            css = handle.read()
        tracks = re.findall(r"\.cart \.line\{[^}]*grid-template-columns:([^;}]+)", css)
        tracks += re.findall(r"\.cart \.line\{grid-template-columns:([^;}]+)", css)
        self.assertTrue(tracks, "no .cart .line grid declaration found")
        for track in tracks:
            self.assertIn("minmax(0,1fr)", track.replace(" ", ""),
                          f"the title column cannot shrink: {track.strip()!r}")

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
