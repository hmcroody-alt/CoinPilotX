"""Route-level tests for the app-intent fallback hook in bot.py.

The hook has exactly one power: on an app-intent GET from iOS, 302 to the App
Store listing. Everything else must pass through untouched. These tests pin both
halves, because "redirects correctly" is only half the contract — the other half
is that an ordinary visitor browsing pulsesoc.com is never intercepted, and the
Privacy Policy never turns into an app launch.

Runs against a temp sqlite file so nothing can touch coinpilotx.db.

Run: python3 -m pytest tests/test_app_intent_fallback_router.py
"""

import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="app_intent_router_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
# The hook runs before any view, so none of these assertions need a schema.
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "0"

import bot  # noqa: E402
from services import app_links  # noqa: E402


IPHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
IPAD = {"User-Agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)"}
# iPadOS Safari sends a desktop UA by default — a documented limitation of
# is_ios_user_agent, pinned here so the gap stays visible.
MAC = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
ANDROID = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8)"}

APP_STORE = "https://apps.apple.com/us/app/pulsesoc/id6777591572"


class AppIntentFallbackRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = bot.webhook_app.test_client()
        logging.disable(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)

    def get(self, path, headers=IPHONE):
        return self.client.get(path, headers=headers)

    # -- the contract ----------------------------------------------------

    def test_ios_app_intent_link_goes_to_the_app_store(self):
        response = self.get("/pulse/post/5?pulse_app=1&pulse_src=email")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], APP_STORE)

    def test_every_marked_destination_reaches_the_listing_on_ios(self):
        for path in (
            "/pulse",
            "/pulse/reels/9",
            "/pulse/status/3",
            "/pulse/profile/ada",
            "/pulse/messages/12",
            "/pulse/marketplace/9",
            "/pulse/orders/4",
            "/pulse/groups/founders",
            "/pulse/events/2",
            "/pulse/private-office",
            "/pulse/ai",
            "/search",
        ):
            with self.subTest(path=path):
                response = self.get(f"{path}?pulse_app=1&pulse_src=email")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], APP_STORE)

    def test_ipad_with_a_mobile_ua_also_reaches_the_listing(self):
        response = self.get("/pulse?pulse_app=1", headers=IPAD)
        self.assertEqual(response.headers.get("Location"), APP_STORE)

    # -- everything the hook must NOT do ---------------------------------

    def test_unmarked_traffic_is_never_intercepted(self):
        # An ordinary visitor. The view may fail for lack of a schema in this
        # process; what matters is that it was reached rather than redirected.
        response = self.get("/pulse/post/5")
        self.assertNotEqual(response.headers.get("Location"), APP_STORE)

    def test_marked_privacy_policy_still_renders_the_policy(self):
        response = self.get("/privacy?pulse_app=1&pulse_src=email")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("Location"))

    def test_marked_web_intent_paths_are_left_alone(self):
        for path in ("/terms", "/support", "/"):
            with self.subTest(path=path):
                response = self.get(f"{path}?pulse_app=1")
                self.assertNotEqual(response.headers.get("Location"), APP_STORE)

    def test_non_ios_keeps_the_web_page_when_one_exists(self):
        for headers in (MAC, ANDROID):
            with self.subTest(ua=headers["User-Agent"][:24]):
                response = self.client.get("/pulse/post/5?pulse_app=1", headers=headers)
                self.assertNotEqual(response.headers.get("Location"), APP_STORE)

    def test_non_ios_still_gets_the_listing_where_no_web_page_exists(self):
        # There is no web route for a single listing or for orders, so the
        # listing is the only honest destination left.
        for path in ("/pulse/marketplace/9", "/pulse/orders"):
            with self.subTest(path=path):
                response = self.client.get(f"{path}?pulse_app=1", headers=MAC)
                self.assertEqual(response.headers.get("Location"), APP_STORE)

    def test_unknown_marked_path_falls_through_to_a_normal_404(self):
        # Section 18: no 500, and no redirect assembled from the request.
        response = self.get("/pulse/nonsense-destination?pulse_app=1")
        self.assertEqual(response.status_code, 404)

    def test_post_requests_are_never_redirected(self):
        # A marked POST would mean losing the body to a 302. The hook only ever
        # considers GET.
        response = self.client.post(
            "/pulse/post/5?pulse_app=1", headers=IPHONE, data={}
        )
        self.assertNotEqual(response.headers.get("Location"), APP_STORE)

    # -- security --------------------------------------------------------

    def test_the_redirect_target_cannot_be_influenced_by_the_request(self):
        for hostile in (
            "/pulse?pulse_app=1&next=https://evil.example.com",
            "/pulse?pulse_app=1&redirect=//evil.example.com",
            "/pulse?pulse_app=1&url=javascript:alert(1)",
            "/pulse?pulse_app=1&pulse_src=https://evil.example.com",
        ):
            with self.subTest(url=hostile):
                response = self.client.get(hostile, headers=IPHONE)
                self.assertEqual(response.headers["Location"], APP_STORE)

    def test_host_header_cannot_move_the_redirect_target(self):
        # X-Forwarded-Proto is set because enforce_https() (bot.py:2676) runs
        # first and reflects request.host into its http->https 301 for EVERY
        # request, marked or not. That reflection predates this hook and is
        # unchanged by it; asserting past it is what isolates the app-intent
        # target as genuinely constant.
        response = self.client.get(
            "/pulse?pulse_app=1",
            headers={
                **IPHONE,
                "Host": "evil.example.com",
                "X-Forwarded-Host": "evil.example.com",
                "X-Forwarded-Proto": "https",
            },
        )
        self.assertEqual(response.headers.get("Location"), APP_STORE)

    def test_marker_must_be_exactly_one(self):
        for value in ("0", "", "true", "yes", "2"):
            with self.subTest(value=value):
                response = self.get(f"/pulse?pulse_app={value}")
                self.assertNotEqual(response.headers.get("Location"), APP_STORE)

    # -- anti-vacuity ----------------------------------------------------

    def test_mutation_the_marker_is_what_causes_the_redirect(self):
        # Same path, same UA, same everything except the marker. If the two
        # agreed, every assertion above would be measuring something else.
        marked = self.get("/pulse?pulse_app=1")
        unmarked = self.get("/pulse")
        self.assertEqual(marked.headers.get("Location"), APP_STORE)
        self.assertNotEqual(unmarked.headers.get("Location"), APP_STORE)

    def test_mutation_the_app_store_url_authority_is_the_one_used(self):
        original = bot.pulsesoc_app_store_url
        bot.pulsesoc_app_store_url = lambda: "https://apps.apple.com/us/app/sentinel/id1"
        try:
            response = self.get("/pulse?pulse_app=1")
            self.assertEqual(
                response.headers["Location"], "https://apps.apple.com/us/app/sentinel/id1"
            )
        finally:
            bot.pulsesoc_app_store_url = original

    def test_mutation_ios_detection_is_what_splits_the_two_paths(self):
        # /pulse has a web page, so the only reason iOS diverges from macOS is
        # the UA test.
        self.assertEqual(self.get("/pulse?pulse_app=1").headers.get("Location"), APP_STORE)
        self.assertNotEqual(
            self.client.get("/pulse?pulse_app=1", headers=MAC).headers.get("Location"),
            APP_STORE,
        )

    def test_hook_is_actually_registered(self):
        names = {
            func.__name__
            for funcs in bot.webhook_app.before_request_funcs.values()
            for func in funcs
        }
        self.assertIn("route_app_intent_links_to_the_app_store", names)

    def test_marker_names_match_the_link_builder(self):
        # The hook reads what build_app_link() writes. If the two ever drift,
        # every generated link silently stops triggering the fallback.
        link = app_links.build_app_link("home", source="email")
        query = link.split("?", 1)[1]
        response = self.client.get(f"/pulse?{query}", headers=IPHONE)
        self.assertEqual(response.headers.get("Location"), APP_STORE)


if __name__ == "__main__":
    unittest.main()
