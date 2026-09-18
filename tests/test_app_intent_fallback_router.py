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

from werkzeug.exceptions import MethodNotAllowed, NotFound  # noqa: E402
from werkzeug.routing import RequestRedirect  # noqa: E402

import bot  # noqa: E402
from services import app_links  # noqa: E402


IPHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
IPAD = {"User-Agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)"}
# iPadOS Safari sends a desktop UA by default — a documented limitation of
# is_ios_user_agent, pinned here so the gap stays visible.
MAC = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
ANDROID = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8)"}

APP_STORE = "https://apps.apple.com/us/app/pulsesoc/id6777591572"

# Ids that satisfy each kind's validator without colliding with a reserved
# segment. The value is irrelevant to url_map matching; only its shape matters.
_SAMPLE_ID = {
    app_links.ID_KIND_POSITIVE_INT: "1",
    app_links.ID_KIND_SLUG: "sample-member",
}


def _concrete_path(spec):
    """A real path for a destination, or None when one cannot be built."""

    # "{id}" is the placeholder resolve_destination_path substitutes
    # (services/app_links.py:1023); there is no constant for it.
    if "{id}" not in spec.path_template:
        return spec.path_template
    sample = _SAMPLE_ID.get(spec.id_kind)
    if sample is None:
        return None
    return spec.path_template.replace("{id}", sample)


def _url_map_has(adapter, path):
    """Does pulsesoc.com serve a GET at this path?

    A RequestRedirect counts as yes: `/pulse/compose` 302s to `/pulse#create`,
    which is a finished web surface reached by a route that exists.
    """

    try:
        adapter.match(path, method="GET")
    except RequestRedirect:
        return True
    except (NotFound, MethodNotAllowed):
        return False
    return True


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

    def test_non_ios_gets_the_interstitial_where_no_web_page_exists(self):
        """A desktop visitor is told, not redirected.

        This used to 302 to the App Store. It is a dead end: nobody installs an
        iPhone app from the Mac they are sitting at, so the session ended on a
        page the visitor could do nothing with. Since the Marketplace family
        became app-first it would also have been the single most common desktop
        outcome on the site.
        """
        for path in ("/pulse/marketplace/9", "/pulse/orders"):
            with self.subTest(path=path):
                response = self.client.get(f"{path}?pulse_app=1", headers=MAC)
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.headers.get("Location"))
                body = response.get_data(as_text=True)
                self.assertIn(APP_STORE, body)
                self.assertIn(app_links.APP_STORE_QR_ASSET, body)

    def test_the_interstitial_never_offers_a_scheme_button_on_desktop(self):
        """`pulsesoc://` on a Mac opens nothing and reads as a broken button."""
        response = self.client.get("/pulse/orders?pulse_app=1", headers=MAC)
        self.assertNotIn(app_links.APP_SCHEME, response.get_data(as_text=True))

    def test_the_interstitial_names_the_destination_it_was_asked_for(self):
        """Anti-vacuity: a generic page would pass every assertion above.

        Two different destinations must produce two different headings, or the
        page is not carrying the member's intent through at all.
        """
        listing = self.client.get(
            "/pulse/marketplace/9?pulse_app=1", headers=MAC
        ).get_data(as_text=True)
        orders = self.client.get(
            "/pulse/orders?pulse_app=1", headers=MAC
        ).get_data(as_text=True)
        self.assertIn("This listing is available in the PulseSoc iPhone app", listing)
        self.assertIn("Order history is available in the PulseSoc iPhone app", orders)

    def test_the_interstitial_is_not_indexable(self):
        # It is a handoff, not content; the resource is indexed at its own URL.
        response = self.client.get("/pulse/orders?pulse_app=1", headers=MAC)
        self.assertIn("noindex", response.get_data(as_text=True))

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

    # -- web_equivalent must agree with the live URL map -------------------

    def test_web_equivalent_agrees_with_the_flask_url_map(self):
        """`web_equivalent` is now load-bearing, so it has to be checked.

        It used to be an annotation. It now decides what a desktop visitor on an
        app-intent link receives: a web page, or the app-only interstitial. A
        stale `False` therefore takes a working page away from someone who could
        have used it, and a stale `True` sends them to a surface that is not
        finished. The field had already drifted before it had teeth -- `order`
        and `orders` carried the note "no web route" long after `/pulse/orders`
        shipped -- so agreement with the real url_map is asserted rather than
        trusted.

        The app-first decisions are the declared exceptions, and they are
        declared in one place: `APP_FIRST_DESPITE_WEB_ROUTE`.
        """
        adapter = bot.webhook_app.url_map.bind("pulsesoc.com")
        for key, spec in sorted(app_links.DESTINATIONS.items()):
            path = _concrete_path(spec)
            if path is None:
                continue
            with self.subTest(destination=key, path=path):
                has_route = _url_map_has(adapter, path)
                if key in app_links.APP_FIRST_DESPITE_WEB_ROUTE:
                    # Exempt from the equality check, but not from being real:
                    # an entry here claims a web route exists and is withheld
                    # on purpose, so if the route vanishes the reason is a lie.
                    self.assertFalse(
                        spec.web_equivalent,
                        f"{key} is declared app-first but marked web_equivalent",
                    )
                    self.assertTrue(
                        has_route,
                        f"{key} claims to withhold a web route that does not exist",
                    )
                    continue
                self.assertEqual(
                    spec.web_equivalent,
                    has_route,
                    f"{key}: web_equivalent={spec.web_equivalent} but "
                    f"{path} {'is' if has_route else 'is not'} in the url_map",
                )

    def test_a_destination_the_binary_cannot_open_never_reaches_the_listing(self):
        """Collections and Roast Battle have no native route at all.

        `app_intent_url` refuses to mark them, so any marked URL for one is
        hand-made or stale -- which is precisely when the server is the only
        thing left to get it right. Sending an iPhone to the App Store here
        promises that installing the app reaches a screen that does not exist in
        it, and both have a finished web page sitting right there.
        """
        for path in ("/pulse/collections", "/pulse/roast-battle"):
            with self.subTest(path=path):
                response = self.get(f"{path}?pulse_app=1")
                self.assertNotEqual(response.headers.get("Location"), APP_STORE)
                body = response.get_data(as_text=True)
                self.assertNotIn("available in the PulseSoc iPhone app", body)

    def test_every_app_first_exception_names_a_real_destination(self):
        # A typo here would silently exempt nothing and re-arm the check it was
        # meant to relax -- for a different destination than the author meant.
        for key in app_links.APP_FIRST_DESPITE_WEB_ROUTE:
            with self.subTest(key=key):
                self.assertIn(key, app_links.DESTINATIONS)

    def test_mutation_the_url_map_check_can_actually_fail(self):
        # Anti-vacuity for the gate above: if `_url_map_has` returned True for
        # everything (or the adapter were misconfigured), the whole check would
        # pass while measuring nothing.
        adapter = bot.webhook_app.url_map.bind("pulsesoc.com")
        self.assertTrue(_url_map_has(adapter, "/privacy"))
        self.assertFalse(_url_map_has(adapter, "/pulse/nonsense-destination"))

    def test_marker_names_match_the_link_builder(self):
        # The hook reads what build_app_link() writes. If the two ever drift,
        # every generated link silently stops triggering the fallback.
        link = app_links.build_app_link("home", source="email")
        query = link.split("?", 1)[1]
        response = self.client.get(f"/pulse?{query}", headers=IPHONE)
        self.assertEqual(response.headers.get("Location"), APP_STORE)


if __name__ == "__main__":
    unittest.main()
