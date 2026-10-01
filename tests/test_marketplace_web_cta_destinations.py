"""Every Marketplace CTA on pulsesoc.com points at the surface that holds the
feature, and none of them re-derives how.

There are two kinds of Marketplace destination, and which kind a destination is
is the product decision:

  * **The storefront** -- `/pulse/marketplace` and `/pulse/marketplace/<id>` --
    is a real, public, indexable web page. Navigation goes straight to it. The
    app is still offered *on* the page, by one "Open in PulseSoc" aside, which is
    an addition to a working page rather than a wall in front of an empty one.
  * **Everything else** -- create a product, seller tools, a seller dashboard,
    orders, purchases, a store, one order -- has no web surface worth landing on,
    so a website button opens the app through `/open/<destination>?pulse_src=web`.

The *shape* of that second link is not a product decision, and it is the part
that is easy to get wrong in a way nothing catches. The obvious implementation is
the canonical app-intent link, `/pulse/marketplace?pulse_app=1`. It is correct in
an email and wrong here: iOS does not consult associated domains for a tap whose
destination is the same domain as the page, so the request reaches Flask, the
fallback router sees iOS plus the marker, and 302s to the App Store -- handing a
listing for PulseSoc to a member holding PulseSoc. Nothing about that failure is
visible from the server, because "no app installed" and "same-domain navigation"
arrive looking identical. So these tests assert `/open/<destination>`
specifically rather than asserting "not the web marketplace": a test that only
banned a canonical path would pass on the marker link, which is the regression
actually worth catching.

## What changed, and what did not

This file used to assert that Marketplace was app-first *everywhere*, storefront
included: "a visitor who clicks Marketplace on the website must not land on the
unfinished web grid." That premise is retired rather than relaxed -- the web
Marketplace is built, public and indexable, and a nav link that refuses to open
the section it names is now a dead end in the middle of the site. The two
storefront destinations moved to web-first; the other eight did not move.

What survives untouched is the rule that made the old arrangement safe and makes
this one safe too: **no Marketplace destination is ever hand-written.** An
app-only button comes from `app_first_href`; the storefront path comes from
`marketplace_href()`, which derives it from `marketplace_storefront.BASE_PATH`; a
product URL comes from `marketplace_storefront.product_path`. The source scan in
section 2 is unchanged in substance and still bans a literal
`href="/pulse/marketplace..."`, which is now a derivation rule rather than an
app-first one: ~15 nav call sites must not pin a path the route owns.

Three layers, because each one alone is satisfiable by a broken page:

  1. the builders -- `app_first_href` produces `/open/...`, never a marker link,
     and `marketplace_href`/`product_path` produce the real route;
  2. the source -- no Marketplace href literal survives anywhere in bot.py, so a
     new CTA cannot be hand-written next to a converted one;
  3. the render -- seven real pages plus a seeded product, rendered, with every
     emitted Marketplace href compared against the builders' output.

Layer 2 is the one that keeps working as the file grows. Layer 3 is the one that
proves layer 2 was measuring something: an f-string compiles happily with a CTA
interpolated into a CSS block.

This module sets DATABASE_URL at import time, so it must run in its own pytest
process.

Run: python3 -m pytest tests/test_marketplace_web_cta_destinations.py
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
os.environ.setdefault("FLASK_SECRET_KEY", "marketplace-web-cta-destination-tests")

import bot  # noqa: E402
from services import app_links  # noqa: E402
from services import marketplace_storefront  # noqa: E402


# enforce_https 301s anything that does not look like it arrived over TLS.
HTTPS = {"X-Forwarded-Proto": "https"}

# The destinations a *website button* opens in the app, with a resource id for
# the two that name one thing rather than a section.
#
# `marketplace` and `product` are deliberately absent: they are the storefront,
# and a button for either goes to the web page. They are still registered
# destinations -- the app CTA on the storefront asks `app_first_href` for one --
# and `test_the_storefront_app_cta_is_the_only_interstitial_left_for_marketplace`
# pins that the CTA is the only place on the site that does.
APP_ONLY_DESTINATIONS = {
    "marketplace_create": None,
    "store": "ada-goods",
    "seller": None,
    "seller_apply": None,
    "seller_dashboard": None,
    "orders": None,
    "order": 88,
    "purchases": None,
}

# The two that are now reached on the web.
WEB_FIRST_DESTINATIONS = ("marketplace", "product")

OPEN_HREF = re.compile(r"/open/[a-z_]+(?:/[^\"'?\s]*)?\?pulse_src=web")

#: A seller and one fully public listing, so layer 3 has a real card to read a
#: href out of. Both visibility predicates have to pass or the grid renders an
#: empty state and every card assertion below would be vacuous: `public_sql`
#: wants approved + active, `discovery_visible_sql` wants a seller account that
#: is active and not hidden from discovery.
SELLER_USER_ID = 90210
LISTING_ID = 77101


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


@pytest.fixture(scope="module")
def catalogue(client):
    """One public listing, so a grid card and a product page exist to read.

    Seeded lazily rather than inside `client`, so the pages measured by `PAGES`
    below are measured against an empty catalogue as well as a stocked one. A
    card adds no `/open/...` destination to the page -- measured, not assumed --
    so both orderings agree, and a future card that *does* add one fails
    `test_the_page_offers_exactly_the_destinations_expected` rather than hiding.
    """
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, username, email, display_name, "
        "hidden_from_discovery, account_status) VALUES (?,?,?,?,0,'active')",
        (SELLER_USER_ID, "cardseller", "cardseller@example.com", "Card Seller"),
    )
    cur.execute(
        "INSERT INTO marketplace_sellers (user_id, status, display_name) "
        "VALUES (?,'approved','Card Seller Store')",
        (SELLER_USER_ID,),
    )
    cur.execute(
        "INSERT INTO marketplace_listings (id, seller_user_id, title, description, "
        "category, price_label, currency, approval_status, status, quantity) "
        "VALUES (?,?,?,?,?,?,?,'approved','active',5)",
        (
            LISTING_ID,
            SELLER_USER_ID,
            "Card Seller Study Guide",
            "A listing that exists so a card has a href.",
            "Education",
            "$10.00",
            "USD",
        ),
    )
    conn.commit()
    return LISTING_ID


def render(client, path):
    response = client.get(path, headers=HTTPS)
    assert response.status_code == 200, f"{path} returned HTTP {response.status_code}"
    return response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 1. The builders
# ---------------------------------------------------------------------------


def test_the_helper_produces_the_interstitial_for_every_app_only_destination():
    for key, resource_id in APP_ONLY_DESTINATIONS.items():
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
    for key, resource_id in APP_ONLY_DESTINATIONS.items():
        href = bot.app_first_href(key, resource_id)
        assert app_links.APP_INTENT_PARAM not in href, key
        assert not href.startswith("/pulse/"), key


def test_the_helper_carries_a_resource_id_through():
    assert bot.app_first_href("order", 88) == "/open/order/88?pulse_src=web"
    assert (
        bot.app_first_href("store", "ada-goods")
        == "/open/store/ada-goods?pulse_src=web"
    )


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
            bot.app_first_href("order", bad)


def test_the_storefront_destinations_are_web_first_in_the_registry():
    """The registry is where "this has a web page" is recorded, so read it there.

    `app_links` decides what an app-first CTA is allowed to be, and these two
    entries are what make the storefront's nav links legitimate rather than an
    exception someone carved out at a call site. `web_equivalent=True` plus
    absence from `APP_FIRST_DESPITE_WEB_ROUTE` is the pair that means "reach this
    on the web"; flipping either one back without changing the nav would leave
    the site claiming one thing and doing another.
    """
    for key in WEB_FIRST_DESTINATIONS:
        assert app_links.DESTINATIONS[key].web_equivalent, key
        assert key not in app_links.APP_FIRST_DESPITE_WEB_ROUTE, key


def test_the_storefront_href_is_derived_from_the_route_and_not_typed():
    """`marketplace_href` is to the storefront what `app_first_href` is to the app.

    One function, ~15 call sites, and a path that comes from
    `marketplace_storefront.BASE_PATH` -- so the nav cannot drift from the route
    it names. The category form is the same derivation plus the same slug rule
    the filter parser reads, which is what makes a nav link to a department land
    on a filtered grid instead of an empty one.
    """
    assert bot.marketplace_href() == marketplace_storefront.BASE_PATH
    assert bot.marketplace_href() == "/pulse/marketplace"
    assert bot.marketplace_href("Sneakers & Shoes") == (
        "/pulse/marketplace?category=sneakers-shoes"
    )
    assert marketplace_storefront.product_path(4271) == "/pulse/marketplace/4271"


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

    One pattern covers both kinds of destination, for different reasons. An
    app-only path here would be a button that reaches an unbuilt web surface. A
    literal `/pulse/marketplace` here would reach the right page by the wrong
    means: the storefront's path is owned by `marketplace_storefront.BASE_PATH`
    and handed out by `marketplace_href()`, and a pinned copy in one of ~15 nav
    call sites is how a route and its navigation drift apart. Neither is caught
    by rendering, because both render a link that works today.
    """
    offenders = []
    for line_number, line in enumerate(_bot_source().splitlines(), start=1):
        for match in HREF_LITERAL.finditer(line):
            href = match.group(1)
            if any(href.startswith(allowed) for allowed in SOURCE_EXCEPTIONS):
                continue
            offenders.append(f"bot.py:{line_number}  href={href!r}")
    assert not offenders, (
        "These hrefs bypass `app_first_href` / `marketplace_href`. A Marketplace "
        "destination is built by a helper, never typed:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_would_notice_a_hand_written_href():
    # Anti-vacuity: the pattern above is doing work, not matching nothing.
    sample = "<a class='button' href='/pulse/marketplace'>Marketplace</a>"
    assert HREF_LITERAL.search(sample).group(1) == "/pulse/marketplace"
    assert HREF_LITERAL.search("href='/pulse/marketplace/create'")
    assert HREF_LITERAL.search("href='/pulse/merchant/apply'")
    assert not HREF_LITERAL.search("href='/open/marketplace?pulse_src=web'")
    # And the shape every nav entry actually has: an interpolation, not a
    # literal. `[^'"{}]*` is what excludes it, so pin that it does -- otherwise
    # the scan above would fail on every correct call site and get deleted.
    assert not HREF_LITERAL.search("href='{marketplace_href()}'")


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


def test_no_javascript_hand_interpolates_a_product_url():
    """A product URL is built in Python, by one function, or not at all.

    Both bans are live rules with a different failure behind each.
    `/open/product/${` would be a card diverting a click off the canonical page
    the SEO work exists to get indexed. `/pulse/marketplace/${` would be the
    right URL built in the wrong place: the storefront renders its cards
    server-side from `marketplace_storefront.product_path`, and a second copy of
    that shape in a template string is the drift `product_path` prevents.

    This replaces an assertion that `marketplaceProductHref` -- a JS helper that
    substituted an id into an interstitial template -- was present. It is gone
    because the thing it fed is gone: the grid is server-rendered now, and search
    result cards keep the canonical url the API already returns. The rule
    outlived the helper, so it is stated over the source rather than over a
    symbol.
    """
    source = _bot_source()
    assert "/pulse/marketplace/${" not in source
    assert "/open/product/${" not in source


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


# path -> the `/open/...` destinations that page is expected to offer. `seller`
# is the "Seller Tools" nav entry, which every shell renders, so it is on all of
# them; `marketplace_create` likewise appears in the creator/merchant menus.
#
# `marketplace` appears on exactly one page: the storefront's own "Open in
# PulseSoc" aside. That is the asymmetry the rebuild created, and the reason this
# mapping is asserted as equality -- `marketplace` reappearing anywhere else
# means a nav entry went back to being an install wall.
PAGES = {
    "/pulse": {"marketplace_create", "seller"},
    "/pulse/marketplace": {
        "marketplace",
        "marketplace_create",
        "seller",
        "seller_apply",
        "seller_dashboard",
    },
    "/pulse/creator-studio": {"marketplace_create", "seller"},
    "/pulse/creator-monetization": {"marketplace_create", "seller"},
    "/pulse/merchant/dashboard": {"marketplace_create", "seller", "seller_apply"},
    "/pulse/videos": {"marketplace_create", "seller"},
    "/pulse/marketplace/create": {"marketplace_create", "seller", "seller_apply"},
}

#: The one page whose `/open/marketplace` link is legitimate.
STOREFRONT_PATH = "/pulse/marketplace"

#: What the member shell contributes to every page above on its own: the "Seller
#: Tools" nav entry and the "Create a product" entry in the creator menus. Named
#: because the product page's expectation is this plus its own app CTA, and
#: spelling that out is what makes the assertion there readable as "the shell,
#: plus one".
SHELL_NAV_DESTINATIONS = {"marketplace_create", "seller"}


def _open_destinations(body):
    return {href.split("?")[0].split("/")[2] for href in OPEN_HREF.findall(body)}


@pytest.mark.parametrize("path", sorted(PAGES))
def test_every_page_navigates_to_the_storefront(client, path):
    """The inverse of what this test asserted before, and the point of the rebuild.

    It used to require that no page emit a `/pulse/marketplace` href at all.
    Every one of these seven pages now does, because the section is a real page
    and the nav entry that names it opens it.
    """
    body = render(client, path)
    href = bot.marketplace_href()
    assert f"href='{href}'" in body or f'href="{href}"' in body, (
        f"{path} does not link to the storefront at all; a page that names "
        "Marketplace in its navigation has to open it"
    )
    # `/pulse/merchant/apply` is still not a website CTA: the application flow
    # lives in the app and the web page behind this path is a stub.
    assert "href='/pulse/merchant/apply'" not in body


@pytest.mark.parametrize("path", sorted(PAGES))
def test_the_page_offers_exactly_the_destinations_expected(client, path):
    assert _open_destinations(render(client, path)) == PAGES[path]


def test_the_storefront_app_cta_is_the_only_interstitial_left_for_marketplace(client):
    """"Open in PulseSoc" is an addition to a working page, not a wall.

    Two things make it legitimate where a nav entry would not be: it sits on the
    destination itself, so a visitor who wanted the page already has it, and it
    is marked `data-app-link`, which is what distinguishes it from navigation.
    Both are asserted, because the first alone would pass on a storefront whose
    *breadcrumb* had quietly become an interstitial.
    """
    body = render(client, STOREFRONT_PATH)
    interstitial = bot.app_first_href("marketplace")
    assert f'<a href="{interstitial}" data-app-link="marketplace">' in body
    assert body.count(f'"{interstitial}"') == 1, (
        "more than one Marketplace interstitial on the storefront; the app CTA "
        "is one invitation, not a pattern"
    )
    # Every other page reaches the section by its URL. Asserted here as well as
    # through PAGES so the failure names the rule rather than a set difference.
    for path in sorted(PAGES):
        if path == STOREFRONT_PATH:
            continue
        assert "marketplace" not in _open_destinations(render(client, path)), path


def test_the_grid_card_links_to_the_canonical_product_page(client, catalogue):
    """A card's href is the product's own URL, built by `product_path`.

    This replaces `test_the_grid_ships_the_client_side_product_template`, which
    asserted the grid shipped an `/open/product/__RESOURCE_ID__` shape for the
    browser to fill in. There is no client-rendered card any more -- the grid is
    server-rendered and the id is known at render time -- so the template is gone
    and the assertion moved to the link it used to produce.

    Compared against `product_path`'s own output rather than a literal, so the
    card and the route cannot disagree about the path shape.
    """
    body = render(client, STOREFRONT_PATH)
    expected = marketplace_storefront.product_path(catalogue)
    assert "Card Seller Study Guide" in body, (
        "the seeded listing is not in the grid, so this test is reading an empty "
        "catalogue and would pass for the wrong reason"
    )
    assert f'href="{expected}"' in body
    assert f"/open/product/{catalogue}" not in body


def test_the_product_page_is_reached_by_its_own_url_and_offers_the_app(
    client, catalogue
):
    """The product page mirrors the storefront: web-first, app offered on it.

    The app CTA here carries the listing id, so tapping it opens *this* product
    rather than the app's Home tab -- which is the failure `app_links` validates
    against, and the reason the href is built rather than written.

    The expected set is the member shell's own nav plus `product`, and notably
    without `marketplace`: the breadcrumb back to the catalogue is a web link,
    like every other navigation on the site. A signed-out visitor sees only
    `product`, because the public document carries no member nav -- covered by
    the storefront suite rather than here, where every fixture is signed in.
    """
    body = render(client, marketplace_storefront.product_path(catalogue))
    assert _open_destinations(body) == SHELL_NAV_DESTINATIONS | {"product"}
    interstitial = bot.app_first_href("product", catalogue)
    assert f'<a href="{interstitial}" data-app-link="product">' in body


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


def test_the_link_map_is_empty_and_that_is_what_keeps_product_cards_canonical():
    """The mechanism is intact; it has nothing to divert any more.

    Its only entry was `marketplace`, which rewrote every product search result
    to `/open/product/<id>` because there was no product page worth landing on.
    There is one now, so the entry was removed and marketplace cards fall through
    the renderers' "a card type with no entry keeps its canonical url" branch --
    which is the behaviour wanted, and which the tests below pin in each of the
    three renderers.

    Asserted as a property of the builder so the failure is unambiguous: an entry
    reappearing here means product clicks have been diverted off the indexable
    page again, and that is a decision, not a detail.
    `app_first_link_map_script` is kept rather than deleted because it is the one
    place a card type can be made app-first again without putting a URL back into
    a JavaScript literal.
    """
    payload = (
        bot.app_first_link_map_script()
        .split("window.PULSE_APP_FIRST_LINKS=", 1)[1]
        .rsplit(";</script>", 1)[0]
    )
    assert json.loads(payload) == {}


def test_the_rendered_map_matches_the_builder(client):
    """The page ships what the builder built, with nothing added in the template.

    Kept separate from the assertion above because the injection is a string
    replace into a shell: the builder can be right while the page ships a second,
    hand-written map next to it.
    """
    assert _injected_link_map(render(client, "/pulse")) == {}


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
    # server-side change and no card type has to be excluded by hand. With the
    # map now empty this branch is what every card takes, product cards included
    # -- and their canonical url is the storefront page.
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

    The canonical url is now the product path. It was
    `/pulse/marketplace?listing=` while the web Marketplace had no product page
    to point at; it does now, and the query form was never read by it --
    `marketplace_storefront.Filters.from_args` parses category / q / sort / page
    and drops the rest, so a web search result landed on the unfiltered grid.
    Native resolves both shapes (`notificationRouting.ts:474` and `:481`), so
    nothing regressed there.

    Asserted as "canonical, and specifically not the query form" rather than as
    one literal, because the point of the test is which URL shape the API commits
    to.
    """
    source = _bot_source()
    assert '"url": f"/pulse/marketplace/{r.get(\'id\')}",' in source
    assert "/pulse/marketplace?listing=" not in source


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
    enforce a CTA decision would break all four.
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


def test_the_web_marketplace_answers_a_direct_request(client):
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
