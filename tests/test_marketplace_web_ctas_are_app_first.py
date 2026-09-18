"""Every Marketplace button on pulsesoc.com opens the app, and none of them
re-derives how.

Marketplace is app-first: the web one was never designed, the native one is the
product. So a visitor who clicks "Marketplace" on the website must not land on
the unfinished web grid. That much is a product decision.

The *shape* of the link is not, and it is the part that is easy to get wrong in
a way nothing catches. The obvious implementation is the canonical app-intent
link, `/pulse/marketplace?pulse_app=1`. It is correct in an email and wrong
here: iOS does not consult associated domains for a tap whose destination is
the same domain as the page, so the request reaches Flask, the fallback router
sees iOS plus the marker, and 302s to the App Store -- handing a listing for
PulseSoc to a member holding PulseSoc. Nothing about that failure is visible
from the server, because "no app installed" and "same-domain navigation" arrive
looking identical.

`/open/<destination>` is the shape that is right for both groups, so these tests
assert it specifically rather than asserting "not the web marketplace". A test
that only banned `/pulse/marketplace` would pass on the canonical marker link,
which is the regression actually worth catching.

Three layers, because each one alone is satisfiable by a broken page:

  1. the builder -- `app_first_href` produces `/open/...`, never a marker link;
  2. the source -- no Marketplace href literal survives anywhere in bot.py, so
     a new CTA cannot be hand-written next to a converted one;
  3. the render -- seven real pages, rendered, with every emitted Marketplace
     href compared against the builder's output.

Layer 2 is the one that keeps working as the file grows. Layer 3 is the one that
proves layer 2 was measuring something: an f-string compiles happily with a CTA
interpolated into a CSS block.

This module sets DATABASE_URL at import time, so it must run in its own pytest
process.

Run: python3 -m pytest tests/test_marketplace_web_ctas_are_app_first.py
"""

import ast
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
# Without a stable key the session cookie set below cannot be read back.
os.environ.setdefault("FLASK_SECRET_KEY", "marketplace-app-first-cta-tests")

import bot  # noqa: E402
from services import app_links  # noqa: E402


# enforce_https 301s anything that does not look like it arrived over TLS.
HTTPS = {"X-Forwarded-Proto": "https"}

# Every destination the app-first Marketplace decision covers, with a resource
# id for the four that name one thing rather than a section.
MARKETPLACE_DESTINATIONS = {
    "marketplace": None,
    "marketplace_create": None,
    "product": 4271,
    "store": "ada-goods",
    "seller": None,
    "seller_apply": None,
    "seller_dashboard": None,
    "orders": None,
    "order": 88,
    "purchases": None,
}

OPEN_HREF = re.compile(r"/open/[a-z_]+(?:/[^\"'?\s]*)?\?pulse_src=web")


@pytest.fixture(scope="module")
def client():
    with bot.webhook_app.app_context():
        bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
        ("adalovelace", "ada@example.com", "x"),
    )
    user_id = cur.lastrowid
    conn.commit()

    test_client = bot.webhook_app.test_client()
    with test_client.session_transaction() as session:
        session["account_user_id"] = user_id
    logging.disable(logging.CRITICAL)
    yield test_client
    logging.disable(logging.NOTSET)


@pytest.fixture(scope="module")
def approved_merchant(client):
    """A second member whose merchant application was approved.

    Separate from `client` on purpose: the approved dashboard is the page with
    the Payouts button on it, and the unapproved one is what most members see.
    Both need to be renderable in the same run.
    """
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
        ("gracehopper", "grace@example.com", "x"),
    )
    user_id = cur.lastrowid
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, display_name, status) "
        "VALUES (?,?,?)",
        (user_id, "Grace Goods", "approved"),
    )
    conn.commit()

    test_client = bot.webhook_app.test_client()
    with test_client.session_transaction() as session:
        session["account_user_id"] = user_id
    return test_client


def render(client, path):
    response = client.get(path, headers=HTTPS)
    assert response.status_code == 200, f"{path} returned HTTP {response.status_code}"
    return response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 1. The builder
# ---------------------------------------------------------------------------


def test_the_helper_produces_the_interstitial_for_every_marketplace_destination():
    for key, resource_id in MARKETPLACE_DESTINATIONS.items():
        href = bot.app_first_href(key, resource_id)
        assert href.startswith(f"/open/{key}"), key
        assert href == app_links.open_interstitial_url(
            key, resource_id, source="web"
        ), key


def test_an_on_site_cta_is_never_the_canonical_marker_link():
    """The regression this whole file exists for.

    `build_app_link` is the right builder for an email and the wrong one here.
    Reaching for it would look like a tidy consolidation and would send every
    installed iOS member to the App Store.
    """
    for key, resource_id in MARKETPLACE_DESTINATIONS.items():
        href = bot.app_first_href(key, resource_id)
        assert app_links.APP_INTENT_PARAM not in href, key
        assert not href.startswith("/pulse/"), key


def test_the_helper_carries_a_resource_id_through():
    assert bot.app_first_href("product", 4271) == "/open/product/4271?pulse_src=web"
    assert bot.app_first_href("order", 88).startswith("/open/order/88?")


def test_the_helper_refuses_a_destination_the_shipped_binary_cannot_open():
    # CTA honesty, enforced at the render that creates the button rather than
    # under the member who taps it.
    for key in ("collections", "roast_battle"):
        assert not app_links.DESTINATIONS[key].native_supported
        with pytest.raises(app_links.AppLinkError):
            bot.app_first_href(key)


def test_the_helper_refuses_an_unknown_destination_and_a_bad_id():
    with pytest.raises(app_links.AppLinkError):
        bot.app_first_href("marketpalce")
    for bad in ("abc", "-1", "0", "../etc"):
        with pytest.raises(app_links.AppLinkError):
            bot.app_first_href("product", bad)


def test_the_client_side_template_is_the_same_url_the_server_would_have_built():
    """The JS listing card substitutes an id; it does not assemble a URL.

    If these two ever disagree, the browser-rendered card and the server-rendered
    card link to different places for the same listing, and only one of them is
    covered by a route test.
    """
    template = app_links.open_interstitial_url_template("product", source="web")
    assert template.count(app_links.CLIENT_ID_TOKEN) == 1
    for listing_id in (1, 9, 4271):
        assert template.replace(
            app_links.CLIENT_ID_TOKEN, str(listing_id)
        ) == app_links.open_interstitial_url("product", listing_id, source="web")


def test_the_template_refuses_the_same_things_the_url_builder_refuses():
    with pytest.raises(app_links.AppLinkError):
        app_links.open_interstitial_url_template("collections")
    with pytest.raises(app_links.AppLinkError):
        app_links.open_interstitial_url_template("marketplace")  # takes no id
    with pytest.raises(app_links.AppLinkError):
        app_links.open_interstitial_url_template("nonsense")


# ---------------------------------------------------------------------------
# 2. The source: nothing hand-writes a Marketplace destination
# ---------------------------------------------------------------------------


# Each exception is a decision, not an oversight, and says which one.
SOURCE_EXCEPTIONS = {
    "/pulse/merchant/payouts": (
        "The Stripe Connect `return_url` and `refresh_url`. Onboarding comes "
        "back to a browser, so this has to stay a page a browser can land on."
    ),
    "/pulse/orders": (
        "A notification deep-link target and the order page's own back link. "
        "Neither is a website CTA: the deep link must stay canonical for the "
        "app to resolve it, and the back link is navigation inside a surface "
        "the member already reached."
    ),
    "/pulse/purchases": "Same: notification routing, not a button.",
}

# `(?<![.\[])` keeps this to rendered anchors. It excludes the two other things
# in bot.py that spell "href=": a CSS attribute selector (`a[href="..."]`) and a
# JavaScript navigation (`location.href='...'`). Neither is a button, and both
# are pinned by name below so the exclusion cannot quietly start hiding one.
HREF_LITERAL = re.compile(
    r"""(?<![.\[])href\s*=\s*['"]((?:/pulse/(?:marketplace|merchant|store|seller|orders|purchases)|/pulse/seller-tools)[^'"{}]*)['"]"""
)


def _bot_source():
    return (ROOT / "bot.py").read_text(encoding="utf-8")


def test_no_marketplace_destination_is_hand_written_as_an_href_in_bot_py():
    """A string-level scan, deliberately.

    The rendered tests below cover seven pages; bot.py renders roughly 1,500
    routes. This is what stops the next Marketplace button from being written
    the old way three screens from any page anyone thought to render.
    """
    offenders = []
    for line_number, line in enumerate(_bot_source().splitlines(), start=1):
        for match in HREF_LITERAL.finditer(line):
            href = match.group(1)
            if any(href.startswith(allowed) for allowed in SOURCE_EXCEPTIONS):
                continue
            offenders.append(f"bot.py:{line_number}  href={href!r}")
    assert not offenders, (
        "These hrefs bypass `app_first_href`. Marketplace is app-first, so a "
        "website button must not reach the web grid:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_would_notice_a_hand_written_href():
    # Anti-vacuity: the pattern above is doing work, not matching nothing.
    sample = "<a class='button' href='/pulse/marketplace'>Marketplace</a>"
    assert HREF_LITERAL.search(sample).group(1) == "/pulse/marketplace"
    assert HREF_LITERAL.search("href='/pulse/marketplace/create'")
    assert HREF_LITERAL.search("href='/pulse/merchant/apply'")
    assert not HREF_LITERAL.search("href='/open/marketplace?pulse_src=web'")


def test_the_two_non_anchor_references_are_the_ones_we_think_they_are():
    """The regex above excludes `.href=` and `[href=`. Name what that excludes.

    Both are deliberate and neither is a CTA, but an unnamed exclusion is how a
    real button eventually hides inside one.
    """
    source = _bot_source()

    # A CSS rule that hides bottom-nav entries while a reel is full-screen. The
    # bottom nav has five items and Marketplace is not one of them, so this
    # selector already matches nothing; it is left alone because editing reels
    # CSS to delete dead styling is not what this change is for.
    assert '.mobile-bottom-nav a[href="/pulse/marketplace"]' in source

    # Where the web product-create form sends a merchant after a successful
    # submit. A member who just built a listing in the browser continues in the
    # browser; bouncing them to an install interstitial mid-flow would lose the
    # thing they came to see.
    assert "location.href='/pulse/merchant/dashboard'" in source


def test_the_javascript_listing_card_does_not_build_its_own_url():
    source = _bot_source()
    assert "/pulse/marketplace/${" not in source
    assert "/open/product/${" not in source
    assert "marketplaceProductHref" in source


def test_every_documented_exception_is_still_present_in_the_source():
    """An exception that stops matching anything is a stale claim.

    Deleting the entry is the right answer then; leaving it means the next
    reader trusts a rule that no longer describes the file.
    """
    source = _bot_source()
    for href in SOURCE_EXCEPTIONS:
        assert f"href='{href}'" in source or f'"{href}"' in source, href


# ---------------------------------------------------------------------------
# 3. The render: real pages, real HTML
# ---------------------------------------------------------------------------


# path -> the destinations that page is expected to offer. `seller` is the
# "Seller Tools" nav entry, which every shell renders, so it is on all of them.
PAGES = {
    # `product` here is the search-card template in the injected link map, not a
    # rendered listing: the home page ships the shape, the browser fills the id.
    "/pulse": {"marketplace", "marketplace_create", "product", "seller"},
    "/pulse/marketplace": {
        "marketplace",
        "marketplace_create",
        "product",
        "seller",
        "seller_apply",
        "seller_dashboard",
    },
    "/pulse/creator-studio": {"marketplace", "marketplace_create", "seller"},
    "/pulse/creator-monetization": {"marketplace", "marketplace_create", "seller"},
    "/pulse/merchant/dashboard": {
        "marketplace",
        "marketplace_create",
        "seller",
        "seller_apply",
    },
    "/pulse/videos": {"marketplace", "marketplace_create", "seller"},
    "/pulse/marketplace/create": {
        "marketplace",
        "marketplace_create",
        "seller",
        "seller_apply",
    },
}


@pytest.mark.parametrize("path", sorted(PAGES))
def test_the_page_emits_no_web_marketplace_href(client, path):
    body = render(client, path)
    assert "href='/pulse/marketplace" not in body
    assert 'href="/pulse/marketplace' not in body
    assert "href='/pulse/merchant/apply'" not in body


@pytest.mark.parametrize("path", sorted(PAGES))
def test_the_page_offers_exactly_the_app_first_destinations_expected(client, path):
    found = {
        href.split("?")[0].split("/")[2] for href in OPEN_HREF.findall(render(client, path))
    }
    assert found == PAGES[path]


def test_the_grid_ships_the_client_side_product_template(client):
    body = render(client, "/pulse/marketplace")
    assert app_links.open_interstitial_url_template("product", source="web") in body


def test_the_merchant_dashboard_keeps_payouts_on_the_web(approved_merchant):
    """The one Marketplace button that must NOT open the app.

    Stripe Connect onboarding returns to this URL in a browser. Sending the
    button to the app would split one flow across two surfaces and leave the
    member wherever Stripe dropped them.

    This needs an approved merchant: the dashboard's unapproved branch renders
    an application prompt with no toolbar at all, so asserting against the
    default fixture would have passed while proving nothing.
    """
    body = render(approved_merchant, "/pulse/merchant/dashboard")
    assert "<h2>Merchant Tools</h2>" in body
    assert "href='/pulse/merchant/payouts'" in body
    # The other two buttons in the same toolbar did move.
    assert f"href='{bot.app_first_href('marketplace_create')}'" in body
    assert f"href='{bot.app_first_href('seller_apply')}'" in body


def test_mutation_the_pages_are_not_all_the_same_html(client):
    # Most assertions above would survive a shell that rendered one page.
    assert render(client, "/pulse/marketplace") != render(client, "/pulse/videos")


# ---------------------------------------------------------------------------
# Search and discovery cards, which the browser renders from a shared payload
# ---------------------------------------------------------------------------

SEARCH_RENDERERS = (
    "static/js/pulse_search_bridge.js",
    "static/js/pulse_home_core.js",
)


def _js(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _injected_link_map(body):
    """The `window.PULSE_APP_FIRST_LINKS` object, parsed out of a rendered page."""

    match = re.search(
        r"window\.PULSE_APP_FIRST_LINKS=(\{.*?\});</script>", body, re.DOTALL
    )
    assert match, "the shell did not inject the app-first link map"
    return json.loads(match.group(1))


@pytest.mark.parametrize("path", sorted(PAGES))
def test_no_page_ships_an_unresolved_placeholder(client, path):
    # An unresolved `__APP_FIRST_LINKS__` renders as literal text next to the
    # script that reads it, and every card silently keeps its canonical url.
    assert "__APP_FIRST_LINKS__" not in render(client, path)


def test_the_page_that_renders_search_results_injects_the_link_map(client):
    """`/pulse` is the only shell carrying the search overlay.

    Asserted here rather than across every page so that the day a second shell
    grows a search box, this test says nothing and the one below -- which reads
    the renderers themselves -- is what catches the gap.
    """
    assert "pulse-search-overlay" in render(client, "/pulse")
    _injected_link_map(render(client, "/pulse"))


def test_the_injected_map_is_built_by_app_links_not_by_hand(client):
    shape = _injected_link_map(render(client, "/pulse"))["marketplace"]
    assert shape["template"] == app_links.open_interstitial_url_template(
        "product", source="web"
    )
    assert shape["fallback"] == bot.app_first_href("marketplace")
    assert shape["token"] == app_links.CLIENT_ID_TOKEN


def test_the_browser_substitution_lands_on_the_real_url(client):
    """What the JS computes has to equal what Python would have computed.

    The browser does one string replace on a shape the server built. If that
    replace produced anything other than `open_interstitial_url`'s own output,
    the centralisation would be decorative.
    """
    shape = _injected_link_map(render(client, "/pulse"))["marketplace"]
    for listing_id in (1, 4271, 99999):
        assert shape["template"].replace(
            shape["token"], str(listing_id)
        ) == app_links.open_interstitial_url("product", listing_id, source="web")


@pytest.mark.parametrize("name", SEARCH_RENDERERS)
def test_the_search_renderer_reads_the_injected_map(name):
    source = _js(name)
    assert "window.PULSE_APP_FIRST_LINKS" in source
    # The anchor must be fed by the helper, not by the raw payload url.
    assert 'href="${esc(item.url' not in source
    assert 'href="${escapeHtml(item.url' not in source


@pytest.mark.parametrize("name", SEARCH_RENDERERS)
def test_no_javascript_file_writes_an_app_link_url_of_its_own(name):
    # The one rule this whole mechanism exists to enforce, checked where it is
    # easiest to break: a static file no Python test otherwise reads.
    source = _js(name)
    assert "/open/" not in source
    assert "pulsesoc://" not in source
    assert "apps.apple.com" not in source


@pytest.mark.parametrize("name", SEARCH_RENDERERS)
def test_a_card_type_with_no_entry_keeps_its_canonical_url(name):
    # Keyed lookup with a fallback to `item.url`, so adding a destination is a
    # server-side change and no card type has to be excluded by hand.
    source = _js(name)
    assert "item.url) ||" in source or "item.url) || " in source


def test_the_inline_search_renderer_reads_the_map_too(client):
    """There are three search-result renderers, not two.

    Two are static files; the third is a script inside the home shell in bot.py
    and is the one a reader is least likely to know exists. All three build the
    same anchor from the same payload.
    """
    source = _bot_source()
    assert "pulseSearchAppFirstHref" in source
    assert "href=\"${esc(item.url||'/pulse')}\"" not in source


def test_the_search_api_payload_stays_canonical(client):
    """The rewrite happens in the renderer, deliberately not in the API.

    `mobile-native/src/screens/SearchScreen.tsx` feeds this same `url` to
    `routeNotificationTarget`, which resolves canonical `/pulse/...` paths. An
    app-first url here would be a path the native router has never seen.
    """
    source = _bot_source()
    assert '"url": f"/pulse/marketplace?listing={r.get(\'id\')}",' in source


# ---------------------------------------------------------------------------
# The underlying web routes are untouched
# ---------------------------------------------------------------------------


WEB_ROUTES_THAT_MUST_SURVIVE = (
    "/pulse/marketplace",
    "/pulse/marketplace/create",
    "/pulse/merchant/apply",
    "/pulse/merchant/dashboard",
    "/pulse/merchant/payouts",
    "/pulse/orders",
    "/pulse/purchases",
)


def test_the_web_marketplace_routes_are_all_still_registered():
    """The CTAs moved. The routes did not.

    Stripe returns here, webhooks land here, admin workflows use these pages,
    and links already sent to members still point at them. Removing a route to
    enforce the app-first decision would break all four.
    """
    rules = {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}
    for route in WEB_ROUTES_THAT_MUST_SURVIVE:
        assert route in rules, route


def test_the_marketplace_api_surface_is_untouched():
    rules = {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}
    api = {rule for rule in rules if rule.startswith("/api/pulse/marketplace")}
    assert len(api) >= 5, sorted(api)
    for expected in (
        "/api/pulse/marketplace/search",
        "/api/pulse/marketplace/seller/apply",
        "/api/pulse/marketplace/listings/create",
    ):
        assert expected in api, expected


def test_the_web_marketplace_still_answers_a_direct_request(client):
    # Reachable by URL, just not reached by a button.
    assert client.get("/pulse/marketplace", headers=HTTPS).status_code == 200


def test_the_stripe_connect_return_surface_is_built_from_the_web_route():
    """`create_onboarding_link` must keep handing Stripe a web URL.

    Stripe redirects a browser to `return_url` when onboarding finishes. An
    `/open/...` value there would put the interstitial at the end of a payout
    setup the member was in the middle of.
    """
    source = _bot_source()
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_onboarding_link"
    ]
    assert calls, "create_onboarding_link is no longer called anywhere"
    for call in calls:
        keywords = {kw.arg for kw in call.keywords}
        assert {"return_url", "refresh_url"} <= keywords
        for keyword in call.keywords:
            if keyword.arg not in ("return_url", "refresh_url"):
                continue
            rendered = ast.get_source_segment(source, keyword.value)
            assert "/pulse/" in rendered, rendered
            assert "/open/" not in rendered, rendered


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
