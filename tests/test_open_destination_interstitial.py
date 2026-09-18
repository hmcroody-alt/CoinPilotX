"""Route-level tests for `/open/<destination>`, the compatibility interstitial.

This surface is deliberately weak: it is not a universal link, it never
redirects, and it never launches the app by itself. Those are the interesting
properties, because each one is a shortcut somebody will eventually be tempted
to take, and each shortcut breaks a different group of people:

- a 302 to the canonical link strands members who DO have the app (iOS does not
  re-evaluate associated domains on a redirect target, so they land in Safari,
  and then get bounced to the App Store for an app they already installed);
- an automatic `pulsesoc://` navigation shows an OS error sheet to everyone who
  does not have the app, which on this page is most of the traffic.

So the tests below spend most of their effort asserting what the page does NOT
do. The `pulsesoc://` button is the one genuinely load-bearing feature, and it
is asserted to be present on iOS, absent elsewhere, and to carry the exact
resource id the caller asked for.

Runs against a temp sqlite file so nothing can touch coinpilotx.db.

Run: python3 -m pytest tests/test_open_destination_interstitial.py
"""

import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="open_interstitial_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
# The page reads no member data, so none of these assertions need a schema.
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "0"

import bot  # noqa: E402
from services import app_links  # noqa: E402


IPHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
MAC = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
ANDROID = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8)"}

APP_STORE = "https://apps.apple.com/us/app/pulsesoc/id6777591572"


class OpenDestinationInterstitialTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = bot.webhook_app.test_client()
        logging.disable(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)

    def get(self, path, headers=IPHONE):
        return self.client.get(path, headers=headers)

    # -- it renders, for everyone ----------------------------------------

    def test_a_known_destination_renders_the_page(self):
        response = self.get("/open/marketplace")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("Location"))
        self.assertIn(APP_STORE, response.get_data(as_text=True))

    def test_it_renders_for_a_signed_out_visitor(self):
        """The people who reach this URL are the ones without the app.

        Gating it behind /login is how the destination gets lost, so the route
        is public on purpose. No member data is read here to protect.
        """
        response = self.get("/open/product/9")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("/login", response.headers.get("Location", ""))

    def test_the_resource_id_survives_into_the_scheme_url(self):
        body = self.get("/open/product/4271").get_data(as_text=True)
        self.assertIn("pulsesoc://pulse/marketplace/4271", body)

    def test_it_offers_the_app_button_only_on_ios(self):
        for headers, expected in ((IPHONE, True), (MAC, False), (ANDROID, False)):
            with self.subTest(ua=headers["User-Agent"][:24]):
                body = self.get("/open/marketplace", headers=headers).get_data(
                    as_text=True
                )
                self.assertEqual(app_links.APP_SCHEME in body, expected)

    def test_every_visitor_is_offered_the_listing(self):
        for headers in (IPHONE, MAC, ANDROID):
            with self.subTest(ua=headers["User-Agent"][:24]):
                body = self.get("/open/orders", headers=headers).get_data(as_text=True)
                self.assertIn(APP_STORE, body)

    def test_the_qr_code_is_present(self):
        # The affordance that actually moves a desktop visitor forward.
        body = self.get("/open/marketplace", headers=MAC).get_data(as_text=True)
        self.assertIn(app_links.APP_STORE_QR_ASSET, body)

    # -- everything it must NOT do ---------------------------------------

    def test_it_never_redirects(self):
        """A 302 here is the single most likely future regression.

        It reads as the obvious implementation and it is wrong for both groups:
        installed members land in Safari, uninstalled members lose nothing they
        would not have lost anyway.
        """
        for path in ("/open/marketplace", "/open/product/9", "/open/orders"):
            for headers in (IPHONE, MAC, ANDROID):
                with self.subTest(path=path, ua=headers["User-Agent"][:20]):
                    response = self.get(path, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertIsNone(response.headers.get("Location"))

    def test_it_never_launches_the_app_by_itself(self):
        # No meta refresh, no location assignment, no auto-clicked anchor. The
        # scheme may appear only inside an href the member chooses to follow.
        body = self.get("/open/marketplace").get_data(as_text=True)
        lowered = body.lower()
        self.assertNotIn("http-equiv=\"refresh\"", lowered)
        self.assertNotIn("window.location", lowered)
        self.assertIn(f'href="{app_links.APP_SCHEME}', body)

    # -- telemetry -------------------------------------------------------

    def test_the_native_open_click_is_reported(self):
        """Following a `pulsesoc://` href never reaches the server.

        It is also the only signal that someone arriving here already had the
        app, which is what distinguishes "this page is helping" from "this page
        is a pure install funnel". So it is beaconed, on the existing /api/track
        endpoint rather than a new one.
        """
        body = self.get("/open/marketplace").get_data(as_text=True)
        self.assertIn(app_links.EVENT_NATIVE_OPEN_SELECTED, body)
        self.assertIn("sendBeacon", body)
        self.assertIn('"/api/track"', body)
        self.assertIn('"marketplace"', body)

    def test_no_beacon_where_there_is_no_button_to_click(self):
        # A listener bound to a missing element is dead code that still ships.
        body = self.get("/open/marketplace", headers=MAC).get_data(as_text=True)
        self.assertNotIn("sendBeacon", body)

    def test_the_beacon_does_not_gate_the_navigation(self):
        # preventDefault here would break the one thing the button is for.
        body = self.get("/open/marketplace").get_data(as_text=True)
        self.assertNotIn("preventDefault", body)

    def test_it_is_not_indexable(self):
        self.assertIn("noindex", self.get("/open/marketplace").get_data(as_text=True))

    def test_an_unknown_destination_is_a_404(self):
        for path in ("/open/nonsense", "/open/", "/open/marketplace/../etc"):
            with self.subTest(path=path):
                self.assertIn(self.get(path).status_code, (404, 308))

    def test_a_destination_the_binary_cannot_open_is_a_404(self):
        """Collections and Roast Battle have no native route in the shipped app.

        Rendering an "Open PulseSoc" button for them would promise a screen that
        does not exist in the binary -- the CTA-honesty rule, committed by the
        server instead of by a button.
        """
        for key in ("collections", "roast_battle"):
            with self.subTest(key=key):
                self.assertFalse(app_links.DESTINATIONS[key].native_supported)
                self.assertEqual(self.get(f"/open/{key}").status_code, 404)

    def test_a_malformed_resource_id_is_a_404_not_a_broken_link(self):
        # `product` takes a positive int. Anything else must not reach an href.
        for bad in ("abc", "-1", "0", "9e9"):
            with self.subTest(resource_id=bad):
                response = self.get(f"/open/product/{bad}")
                self.assertEqual(response.status_code, 404)

    def test_a_reserved_segment_is_not_treated_as_a_resource(self):
        # `/pulse/merchant/apply` is the seller application, not a merchant
        # called "apply". Minting a store link for it would open the wrong page.
        self.assertEqual(self.get("/open/store/apply").status_code, 404)

    # -- security --------------------------------------------------------

    def test_nothing_in_the_page_is_assembled_from_the_query_string(self):
        hostile = (
            "/open/marketplace?next=https://evil.example.com",
            "/open/marketplace?redirect=//evil.example.com",
            "/open/marketplace?url=javascript:alert(1)",
            "/open/marketplace?pulse_src=javascript:alert(1)",
        )
        for url in hostile:
            with self.subTest(url=url):
                body = self.get(url).get_data(as_text=True)
                self.assertNotIn("evil.example.com", body)
                self.assertNotIn("javascript:", body)

    def test_the_host_header_cannot_reach_the_page(self):
        body = self.client.get(
            "/open/marketplace",
            headers={
                **IPHONE,
                "Host": "evil.example.com",
                "X-Forwarded-Host": "evil.example.com",
                "X-Forwarded-Proto": "https",
            },
        ).get_data(as_text=True)
        self.assertNotIn("evil.example.com", body)
        self.assertIn(APP_STORE, body)

    def test_the_destination_segment_cannot_be_reflected_into_the_page(self):
        # A 404 is the expected answer; what matters is that the unknown key
        # never lands in a rendered response body.
        response = self.get("/open/%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("<script>alert(1)", response.get_data(as_text=True))

    def test_post_is_not_accepted(self):
        response = self.client.post("/open/marketplace", headers=IPHONE, data={})
        self.assertEqual(response.status_code, 405)

    # -- anti-vacuity ----------------------------------------------------

    def test_mutation_two_destinations_produce_two_different_pages(self):
        """A generic page would satisfy nearly every assertion above."""
        marketplace = self.get("/open/marketplace").get_data(as_text=True)
        orders = self.get("/open/orders").get_data(as_text=True)
        self.assertNotEqual(marketplace, orders)
        self.assertIn("pulsesoc://pulse/marketplace", marketplace)
        self.assertIn("pulsesoc://pulse/orders", orders)

    def test_mutation_the_ua_check_is_what_gates_the_app_button(self):
        # Same URL, same everything except the User-Agent.
        self.assertIn(
            app_links.APP_SCHEME,
            self.get("/open/marketplace", headers=IPHONE).get_data(as_text=True),
        )
        self.assertNotIn(
            app_links.APP_SCHEME,
            self.get("/open/marketplace", headers=MAC).get_data(as_text=True),
        )

    def test_the_route_is_actually_registered(self):
        rules = {
            str(rule)
            for rule in bot.webhook_app.url_map.iter_rules()
            if str(rule).startswith("/open/")
        }
        self.assertIn("/open/<destination>", rules)
        self.assertIn("/open/<destination>/<resource_id>", rules)


if __name__ == "__main__":
    unittest.main()
