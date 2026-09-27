"""The web Business hub, and the one thing it must never do: overclaim.

`/business` is the browser counterpart of the Business tile on the mobile
Profile grid. Its whole value is that a member can trust it -- a card that says
"Web" has to mean the page works in a browser, and a card that says "App" has to
mean it does not. A hub that gets that backwards is worse than no hub, because a
member who follows a green card to an unfinished surface learns the page lies and
stops reading the other thirteen.

So the tests split in two, and the second half is the point.

## Existence

The page resolves, redirects an anonymous visitor to login, and renders every
section. Ordinary and cheap.

## Truthfulness

Four independent ways this page could become a lie, each guarded:

1. **A green card that does not work.** `web` cards are fetched with a real
   signed-in session and must answer 200 -- not merely be present in the url_map.
   That distinction is the whole reason this file exists: every one of the
   app-first routes ALSO answers 200 (`/pulse/merchant/dashboard` renders "Apply
   and complete verification before merchant tools unlock"), so a 200 is
   necessary and not sufficient, and the registry's job is to know which 200s are
   real pages. This test can only catch the direction where the registry claims
   too much; the other direction is #4.

2. **A card pointing nowhere.** Every href must route, including the
   `/open/<destination>` interstitials the app-first cards carry.

3. **Mobile drifts ahead.** `businessOs.ts` is the source of truth for what the
   Business tile contains. When someone adds a section there -- or flips
   `customers` to `backed: true` once it is built -- this hub silently keeps
   showing the old thirteen. The parity test reads that TypeScript file and fails
   on any key or `backed` value the Python registry does not match, which is the
   mechanism that stops this page from going the way of
   `/admin/capability-matrix`: a hand-kept mirror whose seeded states went four
   months stale and now describe a product that does not exist.

4. **`app_links` drifts behind.** A section's state comes from
   `web_equivalent`, so a page that gets built without the flag being flipped
   leaves a working page marked "App" -- `app_links.py`'s own comment warns that a
   stale `False` "takes a working web page away from someone who could have used
   it". Caught by asserting the withheld set equals `APP_FIRST_DESPITE_WEB_ROUTE`,
   so a route cannot be half-migrated.

Run: python3 -m pytest tests/test_business_web_sections.py
"""

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="business_web_sections_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import app_links  # noqa: E402
from services import business_web_sections as bws  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUSINESS_OS_TS = os.path.join(REPO, "mobile-native", "src", "api", "businessOs.ts")

#: States a card may carry. Named here so a new state cannot be introduced
#: without a test author noticing it has no wording and no guard.
STATES = {"web", "app_first", "app_only", "unbuilt"}


@pytest.fixture(scope="module")
def app():
    return bot.webhook_app


@pytest.fixture(scope="module")
def cards(app):
    return bws.resolve(app)


@pytest.fixture(scope="module")
def client(app):
    """A signed-in, non-admin, unapproved-seller session.

    Deliberately the weakest real account, because that is the visitor most
    likely to be misled: an approved seller sees real content on pages an
    unapproved one sees a gate on, so testing as an approved seller would hide
    exactly the cards whose honesty is in question.
    """

    app.config["SECRET_KEY"] = "business-hub-tests"
    with app.app_context():
        conn = bot.db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
            ("bizhub", "bizhub@example.com", "Biz Hub"),
        )
        conn.commit()
        user_id = cur.lastrowid
    handle = app.test_client()
    with handle.session_transaction() as session:
        session["account_user_id"] = user_id
    return handle


# --------------------------------------------------------------------------
# Existence
# --------------------------------------------------------------------------


def test_the_hub_requires_a_signed_in_member(app):
    anonymous = app.test_client()
    response = anonymous.get(bws.HUB_PATH)
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")


def test_the_hub_renders_every_section_it_does_not_own(client, cards):
    response = client.get(bws.HUB_PATH)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for card in cards:
        if card["state"] == "hub":
            continue
        assert card["label"] in body, f"{card['key']} is missing from the page"


def test_the_hub_does_not_offer_a_card_back_to_itself(cards):
    """The Dashboard section IS this page, so it must not become a control.

    A grid where one card navigates to the page you are already on is the one
    control on it that cannot do anything, and it would be the first thing a
    member clicked.
    """

    hub_cards = bws.hub_cards(bot.webhook_app)
    assert [c["key"] for c in hub_cards if c["key"] == "dashboard"] == []
    assert bws.HUB_PATH not in {c["href"] for c in hub_cards}


def test_every_card_carries_a_known_state_and_matching_wording(cards):
    for card in cards:
        if card["state"] == "hub":
            continue
        assert card["state"] in STATES, card
        # `web` is the only state that speaks for itself; the other three have to
        # explain themselves or the card is just a dead-looking tile.
        if card["state"] == "web":
            assert card["note"] == ""
        else:
            assert card["note"], f"{card['key']} is {card['state']} and says nothing"


# --------------------------------------------------------------------------
# Truthfulness
# --------------------------------------------------------------------------


def test_every_web_card_actually_answers_for_a_signed_in_member(client, cards):
    """A green card is a promise, so it is fetched rather than looked up.

    The url_map cannot distinguish a finished page from a gate that renders 200,
    which is why membership is not the check here.
    """

    failures = []
    for card in cards:
        if card["state"] != "web":
            continue
        response = client.get(card["href"], follow_redirects=True)
        if response.status_code != 200:
            failures.append(f"{card['key']} -> {card['href']} ({response.status_code})")
    assert failures == [], (
        "These cards claim to work in a browser and do not:\n  "
        + "\n  ".join(failures)
    )


def test_no_card_points_at_a_path_that_does_not_route(client, cards):
    """Includes the app-first interstitials, which are links too."""

    failures = []
    for card in cards:
        href = str(card["href"])
        if not href:
            continue
        response = client.get(href)
        if response.status_code == 404:
            failures.append(f"{card['key']} -> {href}")
    assert failures == [], "These cards point at a 404:\n  " + "\n  ".join(failures)


def test_unbuilt_sections_offer_no_link_at_all(cards):
    """Nothing to click, on purpose.

    The tempting alternative is to send these somewhere plausible so the grid
    feels complete. A card that navigates somewhere unrelated to hide a gap is
    the failure this whole registry is written against.
    """

    for card in cards:
        if card["state"] == "unbuilt":
            assert card["href"] == "", card
            assert card["mobile_backed"] is False, (
                f"{card['key']} is marked unbuilt here but backed on mobile"
            )


def test_app_only_cards_do_not_mint_a_deep_link_that_does_not_exist(cards):
    """`linking.ts` publishes no universal link for any BusinessOs* screen.

    So these cards go to the App Store landing page. A `/open/<destination>`
    interstitial would be worse than it looks: it names a destination the shipped
    binary cannot resolve, so the member installs the app and lands nowhere.
    """

    for card in cards:
        if card["state"] == "app_only":
            assert card["href"] == bws.APP_LANDING_PATH, card


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------


def _mobile_sections():
    """Parse `BUSINESS_OS_SECTIONS` out of businessOs.ts.

    Regex rather than a TypeScript parse because the shape being read is two
    fields of a flat object literal, and the assertion that matters is about
    those two fields. If the file is ever restructured so this stops matching,
    `test_the_parse_found_the_registry_at_all` fails loudly rather than this
    file quietly asserting nothing about an empty list -- which is the way a
    parse-based guard normally dies.
    """

    source = open(BUSINESS_OS_TS, encoding="utf-8").read()
    start = source.index("export const BUSINESS_OS_SECTIONS")
    end = source.index("\n];", start)
    block = source[start:end]
    sections = []
    for chunk in re.finditer(r"\{(.*?)\n  \}", block, re.S):
        body = chunk.group(1)
        key = re.search(r'key:\s*"([^"]+)"', body)
        backed = re.search(r"backed:\s*(true|false)", body)
        if key and backed:
            sections.append((key.group(1), backed.group(1) == "true"))
    return sections


def test_the_parse_found_the_registry_at_all():
    """Guards the guard. An empty parse would make the parity test vacuous."""

    sections = _mobile_sections()
    assert len(sections) >= 10, f"parsed only {len(sections)} mobile sections"
    assert ("dashboard", True) in sections


def test_the_web_registry_knows_every_section_the_mobile_tile_has():
    """The anti-rot mechanism.

    Fails when mobile gains a section, loses one, reorders them, or flips a
    `backed` flag. Order is included because the two grids are the same product
    surface and a member who uses both should not have to re-learn the layout.
    """

    mobile = _mobile_sections()
    web = [(str(s["key"]), bool(s["mobile_backed"])) for s in bws.SECTIONS]
    assert web == mobile, (
        "services/business_web_sections.SECTIONS has drifted from "
        "mobile-native/src/api/businessOs.ts.\n"
        f"  mobile: {mobile}\n"
        f"  web:    {web}\n\n"
        "The mobile file is the source of truth for what the Business tile "
        "contains. Update SECTIONS to match -- and if a section just became "
        "backed, decide what its web destination is rather than leaving it to "
        "fall through to 'unbuilt'."
    )


def test_the_labels_and_blurbs_are_the_ones_the_app_uses():
    """A member using both surfaces should see one product, not two vocabularies."""

    source = open(BUSINESS_OS_TS, encoding="utf-8").read()
    for section in bws.SECTIONS:
        # `events` is driven by EVENTS_CARD_CONFIG rather than a literal in the
        # section list, so its label lives elsewhere in the same file; searching
        # the whole source covers both shapes.
        assert f'"{section["label"]}"' in source, (
            f'{section["key"]}: label {section["label"]!r} does not appear in '
            "businessOs.ts"
        )
        assert f'"{section["blurb"]}"' in source, (
            f'{section["key"]}: blurb does not appear in businessOs.ts'
        )


def test_the_withheld_sections_are_exactly_the_ones_app_links_withholds(cards):
    """`app_first` must mean `APP_FIRST_DESPITE_WEB_ROUTE`, not a local opinion.

    This is the half of the honesty problem the fetch test cannot see. Fetching
    proves a green card works; nothing proves a grey card *needs* to be grey
    except agreement with the registry that made the decision. Without this, a
    page could be finished and flipped to `web_equivalent=True` while this hub
    went on sending members to the App Store.
    """

    withheld_here = {
        str(card["key"]): str(card["href"])
        for card in cards
        if card["state"] == "app_first"
    }
    destinations = {
        str(section["key"]): str(section.get("destination") or "")
        for section in bws.SECTIONS
    }
    for key in withheld_here:
        destination = destinations[key]
        assert destination in app_links.APP_FIRST_DESPITE_WEB_ROUTE, (
            f"section {key!r} renders as app-first but its destination "
            f"{destination!r} is not in APP_FIRST_DESPITE_WEB_ROUTE. Either the "
            "page is finished -- in which case flip web_equivalent and delete "
            "the entry -- or this card is withholding a working page."
        )
        assert not app_links.DESTINATIONS[destination].web_equivalent


def test_a_section_naming_two_destinations_is_refused():
    """The registry fails closed on an ambiguous row rather than picking one."""

    broken = dict(bws.SECTIONS[1])
    broken["web_path"] = "/pulse/settings"
    original = bws.SECTIONS
    bws.SECTIONS = (broken,) + original[2:]
    try:
        with pytest.raises(bws.SectionRegistryError):
            bws.resolve(bot.webhook_app)
    finally:
        bws.SECTIONS = original


def test_a_web_path_app_links_already_owns_is_refused():
    """Two registries must not describe the same URL.

    `web_path` exists for the one page `app_links` does not model. Letting it
    name a path `app_links` does own would create a second, unflagged opinion
    about that URL -- which is how `web_equivalent` would stop being the single
    switch that moves a section to the web.
    """

    broken = {
        "key": "verification",
        "label": "Verification",
        "blurb": "Verify the business and unlock ad delivery.",
        "web_path": "/pulse/settings",
        "mobile_backed": True,
    }
    original = bws.SECTIONS
    bws.SECTIONS = (broken,)
    try:
        with pytest.raises(bws.SectionRegistryError):
            bws.resolve(bot.webhook_app)
    finally:
        bws.SECTIONS = original


def test_a_web_path_that_stopped_routing_is_refused():
    original = bws.SECTIONS
    bws.SECTIONS = (
        {
            "key": "verification",
            "label": "Verification",
            "blurb": "Verify the business and unlock ad delivery.",
            "web_path": "/pulse/this-route-does-not-exist",
            "mobile_backed": True,
        },
    )
    try:
        with pytest.raises(bws.SectionRegistryError):
            bws.resolve(bot.webhook_app)
    finally:
        bws.SECTIONS = original


def test_an_unknown_app_links_destination_is_refused():
    original = bws.SECTIONS
    bws.SECTIONS = (
        {
            "key": "store",
            "label": "Store",
            "blurb": "Your listings, inventory and storefront.",
            "destination": "not_a_real_destination",
            "mobile_backed": True,
        },
    )
    try:
        with pytest.raises(bws.SectionRegistryError):
            bws.resolve(bot.webhook_app)
    finally:
        bws.SECTIONS = original


def test_coverage_counts_agree_with_the_cards(app):
    counts = bws.coverage(app)
    cards = bws.hub_cards(app)
    assert sum(counts.values()) == len(cards)
    for state in STATES:
        assert counts[state] == len([c for c in cards if c["state"] == state])


def test_the_page_shows_the_coverage_rather_than_only_the_cards(client, app):
    """The summary is load-bearing, not decoration.

    A member's real question is "how much of my business can I run here", and a
    grid of fourteen tiles answers it only if you count. The number is asserted
    so it cannot quietly disappear in a redesign and leave the page implying
    everything works.
    """

    body = client.get(bws.HUB_PATH).get_data(as_text=True)
    counts = bws.coverage(app)
    assert str(counts["web"]) in body
    assert "work in this browser" in body


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------


def test_the_navigation_offers_the_hub(app):
    """A hub nothing links to is not accessible, whatever it answers.

    The first cut of this feature shipped the route, the registry and eighteen
    passing tests, and no page on the site linked to ``/business`` -- the only
    way in was to type the URL. Every check above would have stayed green
    forever. ``pulse_shell_rail_items`` is the single catalogue both web shells
    read, so an entry there is an entry everywhere, and this is the assertion
    that it exists at all.
    """

    hrefs = [href for _label, href, _icon in bot.pulse_shell_rail_items()]
    assert bws.HUB_PATH in hrefs, (
        f"nothing in the navigation catalogue points at {bws.HUB_PATH}: {hrefs}"
    )


def test_the_hub_is_reachable_from_a_page_a_member_actually_lands_on(client):
    """End-to-end, through the shell, not through the catalogue function.

    ``pulse_shell_rail_items`` returning the right list proves nothing if the
    shell that renders it drops entries -- which is precisely the regression
    ``tests/web_surface/test_shell_nav_parity.py`` was written for, where one
    shell rendered twenty-three destinations and the other rendered seven. So
    this fetches a rendered page and looks for the link in the markup.
    """

    body = client.get("/pulse/settings").get_data(as_text=True)
    assert f"href='{bws.HUB_PATH}'" in body or f'href="{bws.HUB_PATH}"' in body, (
        "a signed-in member on /pulse/settings is offered no link to the "
        "Business hub, so the navigation entry is not surviving the shell"
    )


def test_the_hub_names_the_console_it_does_not_replace(client):
    """``/business-os`` predates this hub and still drives real forms.

    Several sections the hub greys out -- Store, Orders, the marketplace
    composer -- do have a working form inside that console. The hub keeps
    calling them "App" because the console is not the product page those cards
    are waiting on, but a page that greys a section while a working form for it
    exists one click away, unnamed, understates the website. This pins the link
    so the two surfaces cannot drift into one hiding the other.

    The substring ``/business-os`` on its own is useless here: the console is
    also an entry in the shared navigation catalogue, so it is in this page's
    markup either way. Written that loosely the check passed against code with
    no mention of the console in the hub at all. It matches the hub's own
    paragraph instead.
    """

    body = client.get(bws.HUB_PATH).get_data(as_text=True)
    start = body.find('class="console"')
    assert start != -1, (
        "the hub does not mention the Business OS console, so the page looks "
        "less capable than the website is"
    )
    paragraph = body[start:body.find("</p>", start)]
    assert 'href="/business-os"' in paragraph, (
        f"the hub's console paragraph does not link to it: {paragraph!r}"
    )
