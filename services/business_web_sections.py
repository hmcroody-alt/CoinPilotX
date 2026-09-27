"""The Business hub's section registry for pulsesoc.com.

`/business` is the web counterpart of the Business tile on the mobile Profile
grid. This module decides what that page is allowed to say about each section,
and the whole design is about one question: how does a card avoid lying?

## Why this cannot be a list of links

The obvious implementation is fourteen `<a href>`s. It would be wrong on its
first day, because the fourteen sections are in four genuinely different states
and only one of them is "there is a web page for this":

* Some have a finished web page (`/pulse/messages`, `/pulse/growth`).
* Some have a Flask route that answers 200 and is nevertheless **withheld on
  purpose** -- `services/app_links.py` records them in
  `APP_FIRST_DESPITE_WEB_ROUTE` because they render through
  `pulse_social_shell()` with no real template behind them. `/pulse/merchant/
  dashboard` answering 200 does not mean a seller can run a store from a
  browser; for an unapproved seller its entire content is "Apply and complete
  verification before merchant tools unlock."
* Some exist only inside the app and are not even addressable:
  `mobile-native/src/navigation/linking.ts` publishes **no** universal link for
  any `BusinessOs*` screen, so there is no URL that opens Insights. A card
  cannot deep-link to it because no such link exists.
* Two -- Customers and Team -- are not built anywhere. `businessOs.ts` marks
  them `backed: false` with no route, and its own comment explains they are
  shown on mobile as locked cards so a member can see PulseSoc is building them.

A status code cannot tell those apart, which is the trap: probing the routes
finds 200 for every one of them and concludes, wrongly, that the website
already has Business OS.

## Where each answer comes from

Nothing here hand-asserts that a page exists. `resolve()` derives every card's
state from two live sources:

* `services/app_links.py` -- the canonical registry of "one place a member can
  be sent", which already carries `web_equivalent` per destination and already
  has `website_href()` to turn it into the right href. A section that names a
  destination inherits that decision, so when a section is later promoted to
  web-first the flag moves in one place and this page follows with no edit here.
  That is the same centralisation argument `website_href` makes for the two
  dozen Marketplace buttons in `bot.py`.
* the live Flask `url_map` -- so a route that is renamed or deleted turns its
  card into a reported fault instead of a 404 a member finds.

The failure this is written against is the capability matrix at
`/admin/capability-matrix`: a hand-maintained table of what the product can do,
whose seeded states went four months stale and now describe a product that does
not exist. A registry that states coverage instead of deriving it becomes that.

## What the page is allowed to claim

Exactly four things, and `state` is which:

| state       | the card says                            | how it is reached |
|-------------|------------------------------------------|-------------------|
| `web`       | this works in your browser               | a real link       |
| `app_first` | the web page is unfinished, use the app  | `/open/<dest>`    |
| `app_only`  | this lives in the iPhone app             | `/app`            |
| `unbuilt`   | PulseSoc has not built this yet          | nothing           |

`app_only` points at `/app` -- the App Store landing page -- and deliberately
not at a deep link, because for those sections no deep link exists. Minting
`pulsesoc://businessos/insights` would produce a URL the shipped binary does
not resolve, which is the dead-link failure `app_links.Destination` records
`native_supported` to prevent.

`unbuilt` renders no href at all. A disabled-looking card is honest; a card
that navigates somewhere unrelated to make the grid feel complete is not.
"""

from __future__ import annotations

from services import app_links

#: The hub's own path. Named here because the template, the route and the
#: parity test all need to agree on it.
HUB_PATH = "/business"

#: Sections of the mobile Business tile, in the order
#: `mobile-native/src/api/businessOs.ts` lists them.
#:
#: `label` and `blurb` are mirrored from that file on purpose rather than
#: rewritten: a member who uses both surfaces should not have to work out that
#: "Payouts and billing" here is "Payments" there.
#:
#: Each entry names AT MOST ONE destination:
#:
#: * `destination` -- an `app_links` destination key. Preferred, because the
#:   web-or-app decision is then inherited rather than restated.
#: * `web_path` -- a raw path, for the one section whose page `app_links` does
#:   not model. `resolve()` verifies it against the live url_map and rejects it
#:   if `app_links` also claims it, so the two registries cannot disagree about
#:   the same URL.
#:
#: `mobile_backed` mirrors `businessOs.ts`'s `backed` flag, which that file
#: defines as "verified live /api/pulse/* coverage, not aspiration". It is what
#: separates `app_only` (built, in the app) from `unbuilt` (built nowhere), and
#: `tests/test_business_web_sections.py` reads `businessOs.ts` to check these
#: values still match it.
SECTIONS: tuple[dict[str, object], ...] = (
    {
        "key": "dashboard",
        "label": "Dashboard",
        "blurb": "Everything happening across your business right now.",
        # The hub is this section. Giving it a card that links to the page the
        # member is already on would be the grid's only meaningless control.
        "is_hub": True,
        "mobile_backed": True,
    },
    {
        "key": "profile",
        "label": "Business Profile",
        "blurb": "How buyers see your business.",
        # `my_profile` is `/pulse/profile`, which resolves to the member's own
        # public profile -- literally how a buyer sees them. The app's
        # BusinessProfile screen adds "and what is missing from it", which the
        # web page does not have; the blurb claims only the half that is true
        # on both surfaces.
        "destination": "my_profile",
        "mobile_backed": True,
    },
    {
        "key": "store",
        "label": "Store",
        "blurb": "Your listings, inventory and storefront.",
        "destination": "seller_dashboard",
        "mobile_backed": True,
    },
    {
        "key": "marketplace",
        "label": "Marketplace",
        "blurb": "List an item and manage what you sell.",
        # The composer, not `/pulse/marketplace`. The public grid is web-first
        # and a member can reach it from the main navigation, but this card is
        # about selling, and pointing a "list an item" card at a page that only
        # browses would be the quiet mismatch this registry exists to prevent.
        "destination": "marketplace_create",
        "mobile_backed": True,
    },
    {
        "key": "advertising",
        "label": "Advertising",
        "blurb": "Ad accounts, campaigns, budgets and delivery.",
        # `/pulse/growth`, the Growth Center: a real web page with campaign
        # builder, budget manager, placements and spend ledger panels.
        "destination": "promote",
        "mobile_backed": True,
    },
    {
        "key": "orders",
        "label": "Orders",
        "blurb": "Orders buyers placed with you.",
        "destination": "orders",
        "mobile_backed": True,
    },
    {
        "key": "customers",
        "label": "Customers",
        "blurb": "Customer records and segments.",
        "mobile_backed": False,
    },
    {
        "key": "messages",
        "label": "Messages",
        "blurb": "Conversations with buyers.",
        "destination": "messages",
        "mobile_backed": True,
    },
    {
        "key": "insights",
        "label": "Insights",
        "blurb": "Delivery, spend and store performance.",
        # No destination on purpose. `linking.ts` publishes no universal link
        # for `BusinessOsInsights`, so there is no URL that opens it, and there
        # is no web page for it either. `/pulse/creator/analytics` is the
        # nearest-looking web page and is not this: its content is four
        # descriptions of analytics features ("Audience Heatmap -- shows when
        # real audience activity clusters") with no figures behind them, so
        # sending a seller there to read store performance would hand them
        # marketing copy where they expected numbers.
        "mobile_backed": True,
    },
    {
        "key": "payments",
        "label": "Payments",
        "blurb": "Payouts, ad wallet and billing.",
        # `seller_payouts` is web-equivalent, and not by choice -- it is the URL
        # Stripe Connect onboarding returns to, so it is reached in a browser by
        # definition.
        "destination": "seller_payouts",
        "mobile_backed": True,
    },
    {
        "key": "events",
        "label": "Events",
        "blurb": "Events you host and promote.",
        # Deliberately NOT the `events` destination. `/pulse/events` is the
        # events a member can attend; this card is the events they *run*.
        # `EVENTS_CARD_CONFIG` in `businessOs.ts` states that separation and
        # calls it load-bearing ("my events" vs "what happened"), so collapsing
        # the two here would undo a decision taken deliberately on the other
        # surface.
        "mobile_backed": True,
    },
    {
        "key": "team",
        "label": "Team",
        "blurb": "People who help run the business.",
        "mobile_backed": False,
    },
    {
        "key": "verification",
        "label": "Verification",
        "blurb": "Verify the business and unlock ad delivery.",
        # The one section given a raw path. `app_links` has no row for the
        # verification centre even though the app deep-links it
        # (`VerificationCenter` is in `linking.ts`) and the web page is real and
        # renders for an ordinary account. Naming the path here rather than
        # adding a destination keeps this change out of a registry whose rows
        # are checked against the shipped binary; `resolve()` still verifies it
        # against the url_map, so it cannot rot into a 404.
        "web_path": "/pulse/verification/business",
        "mobile_backed": True,
    },
    {
        "key": "settings",
        "label": "Settings",
        "blurb": "Business preferences and account controls.",
        "destination": "settings",
        "mobile_backed": True,
    },
)

#: Wording for the two states that are not a working web page. Kept here so the
#: template has no product copy of its own to drift from.
STATE_NOTES: dict[str, str] = {
    "app_first": "The web version is not finished. Open this in the iPhone app.",
    "app_only": "This runs in the PulseSoc iPhone app.",
    "unbuilt": "Not built yet, on either surface.",
}

#: Where an `app_only` card sends a member: the App Store landing page, because
#: no deep link to these screens exists.
APP_LANDING_PATH = "/app"


class SectionRegistryError(RuntimeError):
    """Raised when a section cannot be resolved truthfully.

    Deliberately fatal rather than a card that silently degrades. Every cause is
    a repo-level mistake -- a destination key that does not exist, a `web_path`
    no longer in the url_map, or both fields set on one section -- and each one
    would otherwise reach a member as a link that goes nowhere.
    """


def _routes(app, path: str) -> bool:
    """Whether a GET of this exact path reaches a handler.

    Matched through the url_map's adapter rather than compared against the set
    of rule strings, because a rule is a pattern and a card carries a concrete
    URL. `/pulse/verification/business` appears in the map as
    `/pulse/verification/<track>`, so a set-membership test on rule strings
    reports the real, working verification page as missing -- which is exactly
    the false negative that makes a derived check worth having only if the
    derivation is right.

    Redirect rules count as routing. Werkzeug signals a rule that matches but
    wants a different URL (a missing trailing slash) by raising a redirect
    rather than returning a match, and a card whose href 301s to the page is
    still a card that works.
    """

    from werkzeug.routing import RoutingException
    from werkzeug.exceptions import HTTPException

    adapter = app.url_map.bind("pulsesoc.com")
    try:
        adapter.match(path, method="GET")
    except RoutingException:
        return True
    except HTTPException:
        return False
    return True


def resolve(app) -> list[dict[str, object]]:
    """Every section as the hub should render it, newest truth from both sources.

    `app` is the live Flask application, which is what makes this a measurement
    rather than a claim: a section whose route has been renamed raises here, at
    boot or in the test suite, instead of shipping a card that 404s.

    Every href this returns has been matched against the url_map before being
    returned, including the `/open/<destination>` interstitials, so the page
    cannot render a link that does not route.
    """

    app_link_paths = {
        spec.path_template for spec in app_links.DESTINATIONS.values()
    }
    cards: list[dict[str, object]] = []

    for section in SECTIONS:
        key = str(section["key"])
        destination = str(section.get("destination") or "")
        web_path = str(section.get("web_path") or "")
        is_hub = bool(section.get("is_hub"))
        backed = bool(section["mobile_backed"])

        if sum(bool(x) for x in (destination, web_path, is_hub)) > 1:
            raise SectionRegistryError(
                f"section {key!r} names more than one destination; a card has "
                "exactly one place to send a member"
            )

        if is_hub:
            state, href = "hub", ""
        elif destination:
            spec = app_links.DESTINATIONS.get(destination)
            if spec is None:
                raise SectionRegistryError(
                    f"section {key!r} names unknown app_links destination "
                    f"{destination!r}"
                )
            # `website_href` is the authority on which of the two hrefs is
            # right, so this branch never re-derives it -- it only reports which
            # branch was taken, for the template's wording.
            href = app_links.website_href(destination)
            state = "web" if spec.web_equivalent else "app_first"
            # Checked for both branches, not just the web one. An `app_first`
            # card's href is the `/open/<destination>` interstitial, and that
            # route can break too -- in which case the card would send a member
            # to a 404 while claiming to send them to the App Store.
            if not _routes(app, href):
                raise SectionRegistryError(
                    f"section {key!r} resolves destination {destination!r} to "
                    f"{href!r}, which no GET route serves"
                )
        elif web_path:
            if not _routes(app, web_path):
                raise SectionRegistryError(
                    f"section {key!r} names web_path {web_path!r}, which no GET "
                    "route serves"
                )
            if web_path in app_link_paths:
                raise SectionRegistryError(
                    f"section {key!r} names web_path {web_path!r}, which "
                    "app_links also owns; name the destination instead so the "
                    "two registries cannot disagree"
                )
            state, href = "web", web_path
        elif backed:
            state, href = "app_only", APP_LANDING_PATH
        else:
            state, href = "unbuilt", ""

        cards.append(
            {
                "key": key,
                "label": section["label"],
                "blurb": section["blurb"],
                "state": state,
                "href": href,
                "note": STATE_NOTES.get(state, ""),
                "mobile_backed": backed,
            }
        )

    return cards


def hub_cards(app) -> list[dict[str, object]]:
    """`resolve()` minus the hub's own section.

    Separate from `resolve()` because the parity test wants all fourteen
    sections -- including `dashboard` -- while the page wants the thirteen it
    can actually offer.
    """

    return [card for card in resolve(app) if card["state"] != "hub"]


def coverage(app) -> dict[str, int]:
    """How many sections are in each state. Rendered on the page.

    The count is on the page rather than only in a report because the honest
    summary is the point: a member should be able to see at a glance how much of
    their business PulseSoc can currently run in a browser, without counting
    cards or discovering it one click at a time.
    """

    counts = {"web": 0, "app_first": 0, "app_only": 0, "unbuilt": 0}
    for card in hub_cards(app):
        counts[str(card["state"])] += 1
    return counts
