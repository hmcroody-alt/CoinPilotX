"""The whole "Open in PulseSoc" chain, followed end to end in one test.

Every link in this chain already has its own tests, and the defect still
shipped -- because each one was correct in isolation. The PDP rendered a
button, the builder produced a well-formed canonical universal link, the
native router resolved `pulsesoc://pulse/marketplace/<id>`. What nothing
checked was the *join*: that the href the PDP emits is a URL this server will
answer, and that what that URL answers with carries the same listing into a
scheme the app claims.

So this file walks the real chain against the real app, following each hop
from the previous hop's output rather than from a constant:

    /pulse/marketplace/<id>            the page a member is reading
      -> href of the app CTA           must not address this same site
      -> GET that href                 the /open interstitial, HTTP 200
      -> pulsesoc:// href on it        the app's own URL for the same listing

The listing id is threaded through all four and asserted at the end. An id
that survives the first three hops and is dropped at the last one is the
original bug wearing a different hat: a button that opens the app to the wrong
place is no better than one that does not open it.

Why the canonical universal link is *not* what the CTA emits, and why that is
the fix rather than a regression: iOS does not consult associated domains for
a navigation to the domain the page is already on. A
`https://pulsesoc.com/pulse/marketplace/9?pulse_app=1` href rendered on
pulsesoc.com is therefore inert -- it reloads the page the button sits on.
That link is still correct, and still tested, for an off-domain surface such
as an email. On-site CTAs go through `/open/...`, which the association leaves
unclaimed -- no component matches it, so Safari keeps the URL and the member is
offered a real `pulsesoc://` button, a custom scheme that is not subject to the
same-domain rule. Unclaimed-by-omission is weaker than an explicit `exclude`,
so `tests/web_parity/test_aasa_claims.py` pins it.

Run: python3 -m pytest tests/web_surface/test_open_in_pulsesoc_chain.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "open-in-pulsesoc-chain-tests")

import bot  # noqa: E402
from services import app_links  # noqa: E402


# enforce_https 301s anything that does not look like it arrived over TLS.
IPHONE = {
    "X-Forwarded-Proto": "https",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148"
    ),
}
DESKTOP = {
    "X-Forwarded-Proto": "https",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
}

SELLER_USER_ID = 90421
NOW = "2026-09-29T00:00:00"

CTA_HREF = re.compile(
    r"<a[^>]*data-app-link=[\"']product[\"'][^>]*>", re.I
)
ANY_APP_CTA = re.compile(r"<a[^>]*data-app-link=[\"']([^\"']+)[\"'][^>]*>", re.I)
HREF = re.compile(r"href=[\"']([^\"']+)[\"']")
SCHEME_HREF = re.compile(r"href=[\"'](pulsesoc://[^\"']+)[\"']")


@pytest.fixture(scope="module")
def client():
    with bot.webhook_app.app_context():
        bot.init_db()
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def listing_id(client):
    """A real published listing, because the chain has to actually run.

    Skipping when the PDP does not render would make every assertion in this
    file vacuous -- and a file that skips is indistinguishable from one that
    passes in a CI summary. So the listing is seeded here and the PDP is
    asserted to render, which turns "no listing" into a failure rather than
    into silence.
    """
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
        (SELLER_USER_ID, "chain_seller", "Chain Seller"),
    )
    cur.execute(
        "INSERT OR IGNORE INTO marketplace_sellers "
        "(user_id, display_name, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        (SELLER_USER_ID, "Chain Store", "approved", NOW, NOW),
    )
    cur.execute(
        "INSERT INTO marketplace_listings "
        "(seller_user_id, title, description, short_description, category, price_label,"
        " currency, quantity, product_type, listing_type, status, approval_status,"
        " cover_image_url, safety_score, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            SELLER_USER_ID,
            "Deep Link Test Lamp",
            "A lamp that exists so the open-in-app chain can be followed.",
            "A lamp",
            "Home",
            "$19.00",
            "USD",
            5,
            "physical",
            "physical",
            "published",
            "approved",
            "https://cdn.example/lamp.jpg",
            7,
            NOW,
            NOW,
        ),
    )
    new_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return new_id


@pytest.fixture(scope="module")
def cta_href(client, listing_id):
    """Hop 1 -> 2: the href the product page actually emits.

    Read off the rendered page rather than rebuilt from the builder, which is
    the entire point: a test that calls `open_interstitial_url` itself proves
    the builder works and says nothing about what the template put in the
    document.
    """
    response = client.get(
        f"/pulse/marketplace/{listing_id}", headers=IPHONE, follow_redirects=True
    )
    assert response.status_code == 200, (
        f"/pulse/marketplace/{listing_id} returned {response.status_code}; the "
        "listing is seeded by this module, so this is a real failure and not a "
        "missing fixture"
    )
    html = response.get_data(as_text=True)
    match = CTA_HREF.search(html)
    assert match, (
        "the product page rendered without an app CTA at all; every assertion "
        "below is about that button"
    )
    href = HREF.search(match.group(0))
    assert href, f"the app CTA has no href: {match.group(0)}"
    return href.group(1).replace("&amp;", "&")


# ---------------------------------------------------------------------------
# Hop 2: the href is answerable by this server, and is not a self-link
# ---------------------------------------------------------------------------


def test_the_cta_does_not_address_the_site_it_is_rendered_on(cta_href):
    """The root cause, stated as the property that rules it out.

    Not "the href equals /open/...", which would pass again the moment someone
    re-pointed it at a different same-domain shape. The invariant is that the
    tap must leave pulsesoc.com's own navigation, because iOS will not consult
    associated domains for anything that does not.
    """
    assert app_links.CANONICAL_APP_HOST not in cta_href, cta_href
    assert app_links.APP_INTENT_PARAM not in cta_href, cta_href
    assert urlparse(cta_href).netloc == "", cta_href


def test_the_cta_carries_the_listing_and_its_provenance(cta_href, listing_id):
    assert str(listing_id) in cta_href, cta_href
    assert f"{app_links.APP_SOURCE_PARAM}=web" in cta_href, cta_href


def test_the_cta_is_not_a_javascript_or_data_url(cta_href):
    lowered = cta_href.strip().lower()
    for scheme in ("javascript:", "data:", "vbscript:", "file:"):
        assert not lowered.startswith(scheme), cta_href


# ---------------------------------------------------------------------------
# Hop 2 -> 3: following it reaches a real page
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def interstitial(client, cta_href):
    response = client.get(cta_href, headers=IPHONE)
    assert response.status_code == 200, (
        f"the CTA points at {cta_href}, which this server answers with HTTP "
        f"{response.status_code}. A button whose href 404s is the same dead "
        "end as one that reloads the page."
    )
    return response


def test_following_the_cta_never_bounces_the_member_somewhere_else(interstitial):
    # A 302 to the canonical link would strand members who DO have the app --
    # iOS does not re-evaluate associated domains on a redirect target, so they
    # land in Safari and are then sent to the App Store for an app they already
    # have installed.
    assert interstitial.status_code == 200


def test_the_interstitial_is_not_cached(interstitial):
    # It branches on the User-Agent, so a shared cache serving one visitor's
    # copy to another would hand a desktop reader an iOS-only button or hide
    # the button from an iPhone.
    assert "no-store" in interstitial.headers.get("Cache-Control", "")


def test_the_interstitial_is_not_indexable(interstitial):
    body = interstitial.get_data(as_text=True)
    assert "noindex" in body


# ---------------------------------------------------------------------------
# Hop 3 -> 4: the app's own URL for the same listing
# ---------------------------------------------------------------------------


def test_the_interstitial_hands_off_to_the_app_with_the_same_listing(interstitial, listing_id):
    """The join the original defect lived in.

    The id has to survive every hop. One that reaches the interstitial and is
    dropped here produces a button that opens the app to the marketplace root
    -- which is the "opens Home instead of the product" symptom, just arrived
    at one layer later.
    """
    body = interstitial.get_data(as_text=True)
    scheme = SCHEME_HREF.search(body)
    assert scheme, (
        "no pulsesoc:// href on the interstitial under an iPhone UA; the "
        "member has no way to reach an installed app"
    )
    url = scheme.group(1)
    assert url.endswith(f"/{listing_id}"), url
    # The path the app actually declares. `linking.ts` maps
    # `pulse/marketplace/:listingId` to MarketplaceProduct, and that agreement is
    # pinned in tests/web_surface/test_scheme_urls_match_the_native_route_table.py
    # so a rename on either side breaks a test rather than a phone. This
    # assertion is the server half; that file is the join; and
    # mobile-native/src/navigation/__tests__/marketplaceDeepLinkIdentity.test.ts
    # is the app half, which proves all three of the app's resolvers agree on the
    # screen once the path arrives.
    assert url == f"{app_links.APP_SCHEME}pulse/marketplace/{listing_id}", url


def test_a_desktop_visitor_gets_the_store_and_no_dead_scheme_button(client, cta_href):
    # A `pulsesoc://` navigation on a desktop browser can never succeed, and
    # offering it there is a guaranteed OS error sheet.
    body = client.get(cta_href, headers=DESKTOP).get_data(as_text=True)
    assert app_links.APP_SCHEME not in body
    assert "apps.apple.com" in body


def test_declining_the_app_returns_to_the_listing(interstitial, listing_id):
    # The last way this chain can still dead-end: a member who taps through,
    # decides not to install, and goes back must get the listing rather than
    # the homepage.
    body = interstitial.get_data(as_text=True)
    back = re.search(r'class="back" href="([^"]*)"', body)
    assert back, "the interstitial has no way back"
    assert back.group(1) == f"/pulse/marketplace/{listing_id}", back.group(1)


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def test_mutation_the_chain_is_followed_and_not_reconstructed(client, monkeypatch, listing_id):
    """Every hop above reads the previous hop's output. This proves it.

    Neutering the builder the PDP calls has to break the chain at hop 1. If any
    assertion above were quietly rebuilding the URL from the builder instead of
    reading the document, this would stay green.
    """
    monkeypatch.setattr(
        bot.app_links,
        "open_interstitial_url",
        lambda destination, resource_id=None, source=None: "/sentinel",
    )
    response = client.get(
        f"/pulse/marketplace/{listing_id}", headers=IPHONE, follow_redirects=True
    )
    assert response.status_code == 200, response.status_code
    match = CTA_HREF.search(response.get_data(as_text=True))
    assert match
    assert "/sentinel" in HREF.search(match.group(0)).group(1)


def test_the_page_offers_no_app_cta_that_is_a_self_link(client, listing_id):
    """Widened past `product`, because the chain is only as good as its weakest
    button: any CTA on this page addressing pulsesoc.com is inert for the same
    reason the product one was."""
    response = client.get(
        f"/pulse/marketplace/{listing_id}", headers=IPHONE, follow_redirects=True
    )
    assert response.status_code == 200, response.status_code
    html = response.get_data(as_text=True)
    offenders = []
    for match in ANY_APP_CTA.finditer(html):
        destination = match.group(1)
        if destination == "app-store":
            continue
        href = HREF.search(match.group(0))
        if not href:
            continue
        value = href.group(1).replace("&amp;", "&")
        if app_links.CANONICAL_APP_HOST in value or (
            app_links.APP_INTENT_PARAM in value
        ):
            offenders.append((destination, value))
    assert offenders == [], offenders
