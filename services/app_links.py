"""The single canonical authority for PulseSoc app-intent links.

An *app-intent* link means "open this in the PulseSoc app". The contract is:

    app-intent link -> app installed? YES -> native app, at the exact destination
                                      NO  -> the official App Store listing

The ordinary website is deliberately NOT the fallback for an app-intent link.
That is different from ordinary web navigation ("Read the Privacy Policy"),
which this module leaves completely alone -- see WEB_INTENT_PREFIXES.

Three facts about the *released* binary constrain everything here, and they were
established by reading the shipped sources rather than by assumption:

1. The Associated Domains entitlement claims exactly one host, `pulsesoc.com`
   (`mobile-native/ios/PulseSoc/PulseSoc.entitlements`), and the published
   apple-app-site-association claims a fixed list of path components --
   `/pulse`, `/pulse/*`, `/search*`, `/dashboard`, `/dashboard/*`,
   `/account/*`, `/settings/*`, `/notifications`, `/saved` and `/education/*`
   (`services/native_app_links.py`, `APPLE_LINK_COMPONENTS`). iOS will never
   hand any other path to the app. A brand-new path family such as `/open/...`
   would be silently ignored by every installed copy of the app in the world,
   which is why `/open/...` is a user-initiated `pulsesoc://` interstitial and
   not a universal link. See `docs/routing/PULSESOC_WEBSITE_TO_NATIVE_ROUTING.md`.

2. The binary's own router (`mobile-native/src/navigation/nativeRouteActions.ts`
   and `linking.ts`) matches on the URL *path* with anchored regexes. Query
   strings are parsed separately and never participate in matching.

3. Therefore the way to mark a link as app-intent, without shipping a new iOS
   build and without breaking any link already in the wild, is a **query
   parameter**. `?pulse_app=1` is inert to the installed app -- it opens the
   destination exactly as it would have -- and is visible to Flask when the app
   is *not* installed and the request falls through to the server.

Consequence: this module can only promise destinations the released binary
genuinely resolves. `native_supported=False` entries exist so callers can pick
truthful wording instead of labelling a button "Open this product" when it would
land the member on unrelated Home content.

The module is intentionally pure -- it imports nothing from `bot` -- so it can be
tested without booting Flask. The App Store URL and the iOS user-agent test stay
where they already live (`bot.pulsesoc_app_store_url`, `bot.is_ios_user_agent`);
duplicating them here would create exactly the second competing link system this
module exists to prevent.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit


# --------------------------------------------------------------------------
# Host / base URL
# --------------------------------------------------------------------------

# Hard-coded on purpose. `bot.public_app_base_url()` falls back to
# https://coinpilotx.app, a host the app does NOT claim in its entitlement, so a
# link built on it can never open the app no matter how correct the path is.
CANONICAL_APP_HOST = "pulsesoc.com"
CANONICAL_APP_ORIGIN = f"https://{CANONICAL_APP_HOST}"

# Hosts we will accept as "already ours" when re-writing an existing link.
ALLOWED_APP_LINK_HOSTS = frozenset({CANONICAL_APP_HOST, f"www.{CANONICAL_APP_HOST}"})


# --------------------------------------------------------------------------
# App Store listing
# --------------------------------------------------------------------------

# The official production listing, and the only place in the codebase that
# should spell it out. It lived in two unrelated modules before this, one of
# which took whatever PULSESOC_APP_STORE_URL contained without checking it --
# a typo'd or hostile Railway variable would have been handed straight to
# members as a "Download PulseSoc" button.
APP_STORE_URL_PREFIX = "https://apps.apple.com/"
APP_STORE_FALLBACK_URL = f"{APP_STORE_URL_PREFIX}us/app/pulsesoc/id6777591572"


def app_store_url() -> str:
    """The App Store destination, from trusted server configuration only.

    Read per call rather than cached at import so a corrected Railway variable
    takes effect on the next request instead of the next deploy. An override
    that is not an apps.apple.com URL is ignored, not trusted: this value is
    used as a redirect target, so accepting arbitrary input here would turn
    every app-intent link into an open redirect.
    """
    configured = (os.environ.get("PULSESOC_APP_STORE_URL") or "").strip()
    if configured.startswith(APP_STORE_URL_PREFIX):
        return configured
    return APP_STORE_FALLBACK_URL


# The QR code is a committed static asset rather than a render-time encode: the
# listing URL is a constant, and encoding it per request would add a dependency
# and a CPU cost for a picture that never changes. The consequence is that the
# asset is only truthful while `app_store_url()` still returns the URL it was
# generated from, which is what `app_store_qr_asset()` checks. An operator who
# points PULSESOC_APP_STORE_URL somewhere else gets no QR rather than a QR that
# silently sends phones to the old listing.
APP_STORE_QR_ASSET = "/static/img/app-store-qr.svg"


def app_store_qr_asset() -> str:
    """The QR image path, or "" when it would no longer encode the live URL."""

    return APP_STORE_QR_ASSET if app_store_url() == APP_STORE_FALLBACK_URL else ""


# --------------------------------------------------------------------------
# Custom URL scheme
# --------------------------------------------------------------------------

# Declared in `mobile-native/src/navigation/linking.ts` alongside
# `https://pulsesoc.com`. React Navigation strips the prefix and routes what is
# left, so `pulsesoc://pulse/marketplace/9` resolves through exactly the same
# route table as the universal link.
#
# This exists for one job: the interstitial's "Open PulseSoc" button. A custom
# scheme is the only way a web page can reach an already-installed app on a path
# the association does not claim, and unlike a universal link it does not need a
# new binary. It is not an alternative link authority -- nothing shareable is
# ever built from it, because a `pulsesoc://` link in an email or a message is a
# dead end for every person who has not installed the app.
APP_SCHEME = "pulsesoc://"


def app_scheme_url(path: str) -> str:
    """The `pulsesoc://` form of a canonical path, for a user-initiated open.

    Raises unless the path resolves to a destination the shipped binary declares.
    That is a security boundary as much as a correctness one: the result goes
    into an `href`, and a scheme URL assembled from unvalidated request input is
    how `javascript:` or an unrelated app's scheme gets into the page.
    """

    normalized = _normalize_path(path)
    spec = match_destination(normalized)
    if spec is None or not spec.native_supported:
        raise AppLinkError(f"no native destination for {path!r}")
    return f"{APP_SCHEME}{normalized.lstrip('/')}"


# --------------------------------------------------------------------------
# Markers
# --------------------------------------------------------------------------

# Verified to have zero pre-existing occurrences anywhere in the repository, so
# neither marker can collide with a query parameter some route already reads.
APP_INTENT_PARAM = "pulse_app"
APP_INTENT_VALUE = "1"
APP_SOURCE_PARAM = "pulse_src"

APP_LINK_SOURCES = frozenset(
    {"email", "push", "sms", "share", "qr", "web", "system", "invite"}
)
DEFAULT_APP_LINK_SOURCE = "system"


# --------------------------------------------------------------------------
# Telemetry event names
# --------------------------------------------------------------------------

EVENT_LINK_GENERATED = "app_link_generated"
EVENT_OPEN_REQUESTED = "app_link_open_requested"
EVENT_LINK_INVALID = "app_link_invalid"
EVENT_LINK_FALLBACK = "app_link_fallback"
EVENT_UNKNOWN_DESTINATION = "app_link_unknown_destination"
# The app-only interstitial was rendered: the visitor asked for a destination
# with no production-ready web surface on a platform that cannot open the app.
EVENT_APP_ONLY_INTERSTITIAL = "app_link_app_only_interstitial"
# The visitor pressed "Open PulseSoc" on an interstitial, so a `pulsesoc://`
# launch was attempted. Records the choice, never whether the app was there --
# the page cannot observe that, and claiming otherwise in telemetry would make
# every later adoption number wrong.
EVENT_NATIVE_OPEN_SELECTED = "app_link_native_open_selected"


# --------------------------------------------------------------------------
# Resource id grammars
# --------------------------------------------------------------------------

# Mirrors the binary's own `[1-9]\d*` in nativeObjectDestination(). Deliberately
# excludes 0 and leading zeros: the app's positiveId() maps those to 0, which
# every detail screen treats as "no resource".
POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]{0,17}$")

# Mirrors the store/profile/slug captures, which are `[^/]+` in the binary. We
# are stricter than the app on purpose -- a narrower generator can only ever
# produce links the app already handles.
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")

ID_KIND_NONE = "none"
ID_KIND_POSITIVE_INT = "positive_int"
ID_KIND_SLUG = "slug"

_ID_VALIDATORS = {
    ID_KIND_NONE: lambda _value: False,
    ID_KIND_POSITIVE_INT: lambda value: bool(POSITIVE_INT_RE.match(value)),
    ID_KIND_SLUG: lambda value: bool(SLUG_RE.match(value)),
}


class AppLinkError(ValueError):
    """Raised when a link cannot be built truthfully. Never swallowed silently."""


# --------------------------------------------------------------------------
# Destination vocabulary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Destination:
    """One canonical place a member can be sent.

    `native_supported` records whether the CURRENT APP STORE BINARY resolves this
    path, verified against linking.ts / nativeRouteActions.ts. `web_equivalent`
    records whether pulsesoc.com renders the same resource, verified against
    `bot.webhook_app.url_map`. The two together decide what a non-iOS visitor
    gets, and they decide what a caller is allowed to promise in a button label.
    """

    key: str
    path_template: str
    id_kind: str = ID_KIND_NONE
    id_required: bool = False
    native_supported: bool = True
    native_screen: str = ""
    web_equivalent: bool = False
    auth_required: bool = False
    # Truthful default CTA wording. Callers may override, but a caller that
    # cannot describe the destination should fall back to this.
    label: str = "Open in PulseSoc"
    # Noun phrase for the app-only interstitial's heading ("Marketplace is
    # available in the PulseSoc iPhone app"). Separate from `label`, which is
    # imperative CTA wording and reads as nonsense in a headline. Only worth
    # setting on destinations that can actually reach that page, i.e. the
    # `web_equivalent=False` ones; everything else falls back to a heading that
    # names no surface rather than naming the wrong one.
    display_name: str = ""
    notes: str = ""
    # Path segments that live *under* this destination but are not resources of
    # it. `/pulse/profile/security` is the account security page, not a member
    # called "security"; `/pulse/groups/create` is the composer, not a group.
    # Without this, a slug destination would happily mint a link that opens
    # unrelated content -- the exact failure the CTA-honesty rule forbids.
    reserved_ids: frozenset[str] = frozenset()
    # Set when this destination's path sits under a WEB_INTENT_PREFIXES entry
    # but is nevertheless an app destination the shipped binary resolves.
    #
    # `/dashboard` and `/account/` are web-intent *families* -- most of what
    # lives under them is a browser-only analytics or billing surface -- yet the
    # association claims `/dashboard*` and `/account/*` and the binary declares
    # `dashboard`, `dashboard/home`, `account/settings` and a handful more. Left
    # unreconciled that split produced a genuinely incoherent product: a member
    # WITH the app got the native screen (iOS matched the association before
    # Flask ever saw the request), while a member WITHOUT it got the web page
    # instead of the App Store, because `is_web_intent_path` short-circuits
    # `fallback_decision` before the destination registry is consulted.
    #
    # Declaring the exception here rather than in a second list beside
    # WEB_INTENT_PREFIXES is deliberate: there is then exactly one place where a
    # path is both named and justified, and `tests/test_app_links.py` can check
    # every flagged path against linking.ts. A hand-kept parallel list is how
    # the two would drift back apart.
    overrides_web_intent: bool = False

    @property
    def supports_resource(self) -> bool:
        return self.id_kind != ID_KIND_NONE

    def accepts_id(self, value: str) -> bool:
        if value.lower() in self.reserved_ids:
            return False
        return _ID_VALIDATORS[self.id_kind](value)


def _d(*args, **kwargs) -> Destination:
    return Destination(*args, **kwargs)


_DESTINATION_LIST: tuple[Destination, ...] = (
    # --- core feed -------------------------------------------------------
    _d(
        "home",
        "/pulse",
        native_screen="Tabs>Home",
        web_equivalent=True,
        label="Open PulseSoc",
    ),
    _d(
        "post",
        "/pulse/post/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="PostDetail",
        web_equivalent=True,
        label="Open this post in PulseSoc",
    ),
    _d(
        "reels",
        "/pulse/reels",
        native_screen="Tabs>Reels",
        web_equivalent=True,
        label="Open Reels in PulseSoc",
    ),
    _d(
        "reel",
        "/pulse/reels/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="ReelDetail",
        web_equivalent=True,
        label="Open this reel in PulseSoc",
    ),
    _d(
        "status",
        "/pulse/status/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="StatusDetail",
        web_equivalent=True,
        label="Open this status in PulseSoc",
    ),
    _d(
        "status_feed",
        "/pulse/status",
        native_screen="Tabs>Status",
        web_equivalent=True,
        label="Open Status in PulseSoc",
    ),
    _d(
        "live",
        "/pulse/live/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="LiveDetail",
        web_equivalent=True,
        label="Open this live stream in PulseSoc",
    ),
    # --- people ----------------------------------------------------------
    _d(
        "profile",
        "/pulse/profile/{id}",
        id_kind=ID_KIND_SLUG,
        id_required=True,
        native_screen="ProfileDetail",
        web_equivalent=True,
        label="Open this profile in PulseSoc",
        reserved_ids=frozenset({"edit", "security", "settings", "privacy"}),
    ),
    _d(
        "my_profile",
        "/pulse/profile",
        native_screen="Tabs>Profile",
        web_equivalent=True,
        auth_required=True,
        label="Open your profile in PulseSoc",
    ),
    # --- messaging -------------------------------------------------------
    _d(
        "conversation",
        "/pulse/messages/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="Chat",
        web_equivalent=True,
        auth_required=True,
        label="Open this conversation in PulseSoc",
    ),
    _d(
        "messages",
        "/pulse/messages",
        native_screen="Tabs>Messenger",
        web_equivalent=True,
        auth_required=True,
        label="Open Messages in PulseSoc",
    ),
    # A single message has no route in the released binary -- the deepest it
    # goes is the conversation. Callers must degrade to `conversation` rather
    # than promise message-level positioning. Kept in the vocabulary so the gap
    # is visible instead of being rediscovered later.
    _d(
        "message",
        "/pulse/messages/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_supported=False,
        native_screen="Chat (conversation only)",
        web_equivalent=True,
        auth_required=True,
        label="Open this conversation in PulseSoc",
        notes=(
            "The released binary has no per-message route. Use `conversation` "
            "with the conversation id; do not label the link as opening a "
            "specific message."
        ),
    ),
    # --- notifications ---------------------------------------------------
    _d(
        "notifications",
        "/pulse/notifications",
        native_screen="NotificationCenter",
        web_equivalent=True,
        auth_required=True,
        label="Open notifications in PulseSoc",
    ),
    _d(
        "notification",
        "/pulse/notifications/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="NotificationCenter",
        web_equivalent=False,
        auth_required=True,
        label="Open notifications in PulseSoc",
        notes=(
            "Binary resolves the id into NotificationCenter. There is no web "
            "route for the id form; the web fallback drops to "
            "/pulse/notifications."
        ),
        display_name="This notification",
    ),
    # --- groups and events ----------------------------------------------
    _d(
        "group",
        "/pulse/groups/{id}",
        id_kind=ID_KIND_SLUG,
        id_required=True,
        native_screen="GroupDetail",
        web_equivalent=True,
        label="Open this group in PulseSoc",
        reserved_ids=frozenset({"create", "new", "discover"}),
    ),
    _d(
        "groups",
        "/pulse/groups",
        native_screen="Tabs>Groups",
        web_equivalent=True,
        label="Open Groups in PulseSoc",
    ),
    _d(
        "event",
        "/pulse/events/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="EventDetail",
        web_equivalent=False,
        label="Open this event in PulseSoc",
        notes="No web route for a single event; the web fallback is /pulse/events.",
        display_name="This event",
    ),
    _d(
        "events",
        "/pulse/events",
        native_screen="Events",
        web_equivalent=True,
        label="Open Events in PulseSoc",
    ),
    # --- commerce --------------------------------------------------------
    #
    # The whole Marketplace family is app-first, and `web_equivalent=False` is
    # how that is expressed rather than a special case somewhere downstream.
    #
    # It is a statement about the *website*, not about the app: `/pulse/
    # marketplace`, `/pulse/marketplace/<id>` and the seller surfaces do have
    # Flask routes, but they render through `pulse_social_shell()` with no
    # template behind them and were never designed for the browser. Marking them
    # `web_equivalent=True` -- which `marketplace` and `store` previously were --
    # meant `fallback_decision` sent desktop visitors *into* that unfinished
    # surface. Flipping the flag routes them to the app-only interstitial
    # instead, which is the honest answer until the web Marketplace ships.
    #
    # To return a row to web-first: flip this flag back, delete its entry from
    # MARKETPLACE_WEB_PATHS in bot.py, and re-run tests/test_app_links.py. The
    # procedure is written out in docs/routing/.
    _d(
        "marketplace",
        "/pulse/marketplace",
        native_screen="Tabs>Marketplace",
        web_equivalent=False,
        label="Open Marketplace in PulseSoc",
        notes="App-first: the web Marketplace is not production-ready.",
        display_name="Marketplace",
    ),
    _d(
        "product",
        "/pulse/marketplace/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="MarketplaceDetail",
        web_equivalent=False,
        label="Open this listing in PulseSoc",
        notes="App-first: no production-ready web route for a single listing.",
        display_name="This listing",
    ),
    _d(
        "marketplace_create",
        "/pulse/marketplace/create",
        native_screen="MarketplaceCreateGateway",
        web_equivalent=False,
        auth_required=True,
        label="Create a listing in PulseSoc",
        display_name="The listing composer",
    ),
    _d(
        "store",
        "/pulse/merchant/{id}",
        id_kind=ID_KIND_SLUG,
        id_required=True,
        native_screen="MerchantProfile",
        web_equivalent=False,
        label="Open this store in PulseSoc",
        notes="App-first: the web store page is not production-ready.",
        reserved_ids=frozenset({"apply", "dashboard", "payouts"}),
        display_name="This store",
    ),
    _d(
        "seller",
        "/pulse/seller-store",
        native_screen="SellerStore",
        web_equivalent=False,
        auth_required=True,
        label="Open Seller Tools in PulseSoc",
        display_name="Seller Tools",
    ),
    _d(
        "seller_dashboard",
        "/pulse/merchant/dashboard",
        native_screen="MerchantDashboard",
        web_equivalent=False,
        auth_required=True,
        label="Open your seller dashboard in PulseSoc",
        display_name="The seller dashboard",
    ),
    _d(
        "seller_apply",
        "/pulse/merchant/apply",
        native_screen="MerchantApply",
        web_equivalent=False,
        auth_required=True,
        label="Apply to sell in PulseSoc",
        display_name="The seller application",
    ),
    _d(
        "purchases",
        "/pulse/purchases",
        native_screen="BuyerPurchases",
        web_equivalent=False,
        auth_required=True,
        label="Open your purchases in PulseSoc",
        display_name="Purchase history",
    ),
    _d(
        "orders",
        "/pulse/orders",
        native_screen="BuyerOrders",
        web_equivalent=False,
        auth_required=True,
        label="Open your orders in PulseSoc",
        notes="App-first with the rest of Marketplace. /pulse/orders does render on the web; the decision is not to send members there yet.",
        display_name="Order history",
    ),
    _d(
        "order",
        "/pulse/orders/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="BuyerOrderDetail",
        web_equivalent=False,
        auth_required=True,
        label="Open this order in PulseSoc",
        notes="App-first with the rest of Marketplace. /pulse/orders/<id> does render on the web; the decision is not to send members there yet.",
        display_name="This order",
    ),
    # --- AI / private ----------------------------------------------------
    _d(
        "undx",
        "/pulse/ai",
        native_screen="Tabs>PulseAI",
        web_equivalent=False,
        auth_required=True,
        label="Open PulseSoc AI",
        display_name="PulseSoc AI",
    ),
    _d(
        "private_office",
        "/pulse/private-office",
        native_screen="PrivateOffice",
        web_equivalent=False,
        auth_required=True,
        label="Open Private Office in PulseSoc",
        notes=(
            "Private Office keeps its own second lock inside the app. A deep "
            "link names the destination; it grants nothing."
        ),
        display_name="Private Office",
    ),
    # --- misc ------------------------------------------------------------
    _d(
        "search",
        "/search",
        native_screen="Search",
        web_equivalent=True,
        label="Search in PulseSoc",
    ),
    _d(
        "settings",
        "/pulse/settings",
        native_screen="Tabs>Settings",
        web_equivalent=True,
        auth_required=True,
        label="Open settings in PulseSoc",
    ),
    _d(
        "premium",
        "/pulse/premium",
        native_screen="Premium",
        web_equivalent=True,
        auth_required=True,
        label="Open Premium in PulseSoc",
    ),
    # --- creator and account workflows -----------------------------------
    #
    # Every path below was read off mobile-native/src/navigation/linking.ts and
    # is resolved by the CURRENT App Store binary. `tests/test_app_links.py`
    # re-derives that list from linking.ts and fails if any entry here stops
    # being declared, so this block cannot quietly outlive the routes it names.
    _d(
        "dashboard",
        "/dashboard",
        native_screen="UserDashboard",
        web_equivalent=True,
        auth_required=True,
        overrides_web_intent=True,
        label="Open your dashboard in PulseSoc",
    ),
    _d(
        "create",
        "/pulse/compose",
        native_screen="DashboardComposeAlias",
        web_equivalent=True,
        auth_required=True,
        label="Create a post in PulseSoc",
    ),
    _d(
        "creator_studio",
        "/pulse/creator-studio",
        native_screen="CreatorStudio",
        web_equivalent=True,
        auth_required=True,
        label="Open Creator Studio in PulseSoc",
    ),
    _d(
        "promote",
        "/pulse/growth",
        native_screen="GrowthCenter",
        web_equivalent=True,
        auth_required=True,
        label="Open Promote in PulseSoc",
    ),
    _d(
        "portfolio",
        "/pulse/portfolio",
        native_screen="Portfolio",
        web_equivalent=True,
        auth_required=True,
        label="Open your portfolio in PulseSoc",
    ),
    _d(
        "saved",
        "/saved",
        native_screen="Saved",
        web_equivalent=True,
        auth_required=True,
        label="Open Saved in PulseSoc",
    ),
    _d(
        "friends",
        "/dashboard/network/friends",
        native_screen="DashboardLegacyModule",
        web_equivalent=True,
        auth_required=True,
        overrides_web_intent=True,
        label="Open Friends in PulseSoc",
    ),
    _d(
        "profile_edit",
        "/pulse/profile/edit",
        native_screen="ProfileEdit",
        web_equivalent=True,
        auth_required=True,
        label="Edit your profile in PulseSoc",
    ),
    # The top-level alias. `/pulse/notifications` above is the in-product path;
    # this is the one the association claims as its own component and the one
    # notification emails have always used.
    _d(
        "notifications_web",
        "/notifications",
        native_screen="NotificationCenter",
        web_equivalent=True,
        auth_required=True,
        label="Open notifications in PulseSoc",
    ),
    _d(
        "account_settings",
        "/account/settings",
        native_screen="AccountWebSettings",
        web_equivalent=True,
        auth_required=True,
        overrides_web_intent=True,
        label="Open account settings in PulseSoc",
    ),
    _d(
        "account_security",
        "/account/security",
        native_screen="AccountWebSecurity",
        web_equivalent=True,
        auth_required=True,
        overrides_web_intent=True,
        label="Open account security in PulseSoc",
    ),
    # --- named, and deliberately not linkable ----------------------------
    #
    # Collections and Roast Battle are app-first in product terms and have no
    # native route at all: neither appears in linking.ts, in nativeRouteActions
    # .ts, or as a screen file. They are registered as `native_supported=False`
    # rather than left out so that the gap is a fact the code states, and so the
    # CTA-honesty rule does the enforcing -- `build_app_link("collections")`
    # raises, and `app_intent_url` leaves any such path unmarked, which means no
    # button promising them can ship by accident.
    #
    # Removing `native_supported=False` is the deliberate act that turns each of
    # these on, and it is only correct once the route exists in a RELEASED
    # binary. See docs/routing/ for the checklist.
    _d(
        "collections",
        "/pulse/collections",
        native_supported=False,
        native_screen="",
        web_equivalent=True,
        auth_required=True,
        label="Open Collections in PulseSoc",
        notes=(
            "No native route in the shipped binary. Requires a future build "
            "before any link or CTA may name it. The web page at "
            "/pulse/collections is finished and is where everyone goes today."
        ),
    ),
    _d(
        "roast_battle",
        "/pulse/roast-battle",
        native_supported=False,
        native_screen="",
        web_equivalent=True,
        auth_required=True,
        label="Open Roast Battle in PulseSoc",
        notes=(
            "No native route in the shipped binary. Requires a future build "
            "before any link or CTA may name it. The web page at "
            "/pulse/roast-battle is finished and is where everyone goes today."
        ),
    ),
)

DESTINATIONS: Mapping[str, Destination] = {d.key: d for d in _DESTINATION_LIST}

# Destinations a caller may safely fall back to when the requested one is not
# resolvable at resource granularity.
DEGRADE_TO: Mapping[str, str] = {
    "message": "conversation",
    "notification": "notifications",
    "event": "events",
    "product": "marketplace",
    "order": "orders",
}


# --------------------------------------------------------------------------
# Web-intent protection (mission section 2)
# --------------------------------------------------------------------------

# Paths that are legitimate *website* navigation. They are never marked as
# app-intent, never redirected to the App Store, and never rewritten -- turning
# "Read the Privacy Policy" into an app launch would break a legal obligation
# and strand every desktop reader.
#
# Exact paths first, then prefixes. Classification is by purpose, not by label.
WEB_INTENT_PATHS = frozenset(
    {
        "/",
        "/privacy",
        "/privacy-policy",
        "/terms",
        "/terms-of-service",
        "/legal",
        "/cookies",
        "/cookie-policy",
        "/support",
        "/help",
        "/contact",
        "/about",
        "/pricing",
        "/download",
        "/security",
        "/accessibility",
        "/status",
        "/login",
        "/logout",
        "/signup",
        "/register",
        "/account",
        "/checkout",
        "/reset-password",
        "/forgot-password",
        "/verify-email",
    }
)

WEB_INTENT_PREFIXES = (
    "/legal/",
    "/docs/",
    "/blog/",
    "/help/",
    "/support/",
    "/press/",
    "/careers/",
    "/account/",
    "/checkout/",
    "/billing/",
    # The whole /dashboard family is a web-designed analytics and management
    # surface -- creator, crypto, economy, network, system. There is no native
    # equivalent to open, so these must never be marked as app intents or an
    # iPhone member would be sent to the App Store instead of the page they
    # asked for.
    "/dashboard",
    # The livestream studio is a browser broadcasting surface. Classified here
    # so link handling leaves it strictly alone.
    "/pulse/live/studio",
    # The web client shell. Everything else under /pulse/ is a native object and
    # belongs to the app; this is the browser's own copy of the product, served
    # by bot.py's pulse_web_app_shell. Classifying it is only half the job --
    # this registry has no effect on iOS. The enforcing half is the pair of
    # `exclude` components in services/native_app_links.py, which sit above
    # /pulse/* so the association hands these URLs to Safari. Change one and the
    # other is wrong.
    "/pulse/app",
    "/api/",
    "/static/",
    "/.well-known/",
    "/admin",
    "/auth/",
    "/oauth",
    "/webhook",
)


# Destinations that are app-first even though pulsesoc.com does have a route for
# them. Everywhere else `web_equivalent` is expected to agree with the Flask URL
# map, and `tests/test_app_intent_fallback_router.py` checks that against the
# live map -- a check worth having, because the field had already drifted: two
# entries carried the note "no web route" long after `/pulse/orders` was built.
#
# Since `web_equivalent=False` now decides what a *desktop* visitor sees, a stale
# False is no longer a harmless annotation. It takes a working web page away from
# someone who could have used it.
APP_FIRST_DESPITE_WEB_ROUTE: dict[str, str] = {
    "marketplace": "Marketplace is app-first by decision until the web Marketplace is rebuilt.",
    "marketplace_create": "Listing creation is app-first with the rest of Marketplace.",
    "product": "Listing detail is app-first with the rest of Marketplace.",
    "store": "Merchant storefronts are app-first with the rest of Marketplace.",
    "seller": "Seller tools are app-first with the rest of Marketplace.",
    "seller_apply": "Seller onboarding is app-first with the rest of Marketplace.",
    "seller_dashboard": "The seller dashboard is app-first with the rest of Marketplace.",
    "orders": "Order management is app-first with the rest of Marketplace.",
    "order": "Order detail is app-first with the rest of Marketplace.",
    "purchases": "Purchase history is app-first with the rest of Marketplace.",
    # Pre-dating the app-first decision and deliberately left alone. Both render
    # a web page, so `web_equivalent=True` is arguably the truthful value -- and
    # changing it would be a behaviour change for two subsystems this work has
    # not otherwise touched. Recorded as an open question in the routing doc
    # rather than changed quietly here.
    "undx": "Pre-existing classification, retained pending review.",
    "private_office": "Pre-existing classification, retained pending review.",
}


# The exact paths that are app destinations despite sitting inside a web-intent
# family. Derived from the registry so there is one source of truth; see the
# `overrides_web_intent` field for why the exception is declared there.
WEB_INTENT_OVERRIDES: frozenset[str] = frozenset(
    spec.path_template
    for spec in _DESTINATION_LIST
    if spec.overrides_web_intent and spec.native_supported
)


def is_web_intent_path(path: str) -> bool:
    """True when the path is ordinary website navigation and must be left alone.

    The override check runs first. Without it a prefix always wins, and the
    prefixes are families (`/dashboard`, `/account/`) that contain both kinds of
    page -- so the specific app destinations inside them could never be reached.
    """

    normalized = _normalize_path(path)
    if normalized in WEB_INTENT_OVERRIDES:
        return False
    if normalized in WEB_INTENT_PATHS:
        return True
    return any(normalized.startswith(prefix) for prefix in WEB_INTENT_PREFIXES)


# --------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _normalize_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "/"
    if "://" in raw:
        # Callers hold a mix of relative paths and full URLs. Matching on the
        # path either way keeps a stored `https://pulsesoc.com/pulse/reels/3`
        # from being classed as unknown and losing its destination label.
        # Host safety is the caller's job -- app_intent_url checks it before it
        # ever gets here.
        raw = urlsplit(raw).path or "/"
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    if not raw.startswith("/"):
        raw = f"/{raw}"
    raw = re.sub(r"/{2,}", "/", raw)
    if len(raw) > 1:
        raw = raw.rstrip("/") or "/"
    return raw


def _reject_unsafe(value: str, what: str) -> str:
    """Refuse anything that could turn a link into an injection vector."""

    raw = str(value or "")
    if _CONTROL_CHARS_RE.search(raw):
        raise AppLinkError(f"{what} contains control characters.")
    if "\\" in raw:
        raise AppLinkError(f"{what} contains a backslash.")
    lowered = raw.strip().lower()
    for scheme in ("javascript:", "data:", "vbscript:", "file:"):
        if lowered.startswith(scheme):
            raise AppLinkError(f"{what} uses a forbidden scheme.")
    if ".." in raw:
        raise AppLinkError(f"{what} contains a path traversal segment.")
    return raw


# --------------------------------------------------------------------------
# The canonical builder
# --------------------------------------------------------------------------


def build_app_link(
    destination: str,
    resource_id: str | int | None = None,
    params: Mapping[str, object] | None = None,
    source: str = DEFAULT_APP_LINK_SOURCE,
) -> str:
    """Build the one canonical app-intent URL for a destination.

    Always absolute and always on `https://pulsesoc.com`, because that is the
    only host the app's entitlement claims. Raises `AppLinkError` rather than
    quietly emitting a link that would land the member somewhere else.
    """

    spec = DESTINATIONS.get(str(destination or "").strip().lower())
    if spec is None:
        raise AppLinkError(f"Unknown app link destination: {destination!r}")

    path = resolve_destination_path(spec, resource_id)
    query = dict(_safe_params(params))
    query[APP_INTENT_PARAM] = APP_INTENT_VALUE
    query[APP_SOURCE_PARAM] = normalize_source(source)
    return f"{CANONICAL_APP_ORIGIN}{path}?{urlencode(query)}"


def resolve_destination_path(
    spec: Destination | str, resource_id: str | int | None = None
) -> str:
    """The bare `/pulse/...` path for a destination, with the id validated."""

    if isinstance(spec, str):
        resolved = DESTINATIONS.get(spec.strip().lower())
        if resolved is None:
            raise AppLinkError(f"Unknown app link destination: {spec!r}")
        spec = resolved

    if not spec.supports_resource:
        if resource_id not in (None, ""):
            raise AppLinkError(
                f"Destination {spec.key!r} does not take a resource id."
            )
        return spec.path_template

    raw_id = "" if resource_id is None else str(resource_id).strip()
    if not raw_id:
        if spec.id_required:
            raise AppLinkError(f"Destination {spec.key!r} requires a resource id.")
        return spec.path_template.split("/{id}")[0]

    _reject_unsafe(raw_id, "Resource id")
    if not spec.accepts_id(raw_id):
        raise AppLinkError(
            f"Resource id {raw_id!r} is not a valid {spec.id_kind} "
            f"for destination {spec.key!r}."
        )
    return spec.path_template.replace("{id}", quote(raw_id, safe=""))


def normalize_source(source: str | None) -> str:
    candidate = str(source or "").strip().lower()
    return candidate if candidate in APP_LINK_SOURCES else DEFAULT_APP_LINK_SOURCE


def _safe_params(params: Mapping[str, object] | None) -> Iterable[tuple[str, str]]:
    for key, value in (params or {}).items():
        name = str(key or "").strip()
        if not name or name in (APP_INTENT_PARAM, APP_SOURCE_PARAM):
            # The markers are ours. A caller cannot spoof or clear them.
            continue
        if value is None:
            continue
        text = str(value)
        _reject_unsafe(name, "Query parameter name")
        _reject_unsafe(text, "Query parameter value")
        yield name, text


# --------------------------------------------------------------------------
# Adapter for existing relative links (email / push migration)
# --------------------------------------------------------------------------


def app_intent_url(link: str, source: str = DEFAULT_APP_LINK_SOURCE) -> str:
    """Mark an existing PulseSoc link as app-intent, if it is one.

    This is the migration seam. Callers that already hold a relative path such as
    `/pulse/post/1234` pass it straight through and get back an absolute,
    marked, canonical-host URL. Anything that is web-intent (Privacy Policy,
    password reset, checkout), off-host, or not a destination the released binary
    resolves comes back untouched -- that is what preserves ordinary website
    navigation automatically rather than by a caller remembering to opt out.
    """

    raw = str(link or "").strip()
    if not raw:
        return raw

    try:
        _reject_unsafe(raw, "Link")
    except AppLinkError:
        return raw

    if raw.startswith("//"):
        # Protocol-relative: a classic open-redirect shape. Never ours.
        return raw

    if "://" in raw:
        parts = urlsplit(raw)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_APP_LINK_HOSTS:
            return raw
        path, existing_query = parts.path, parts.query
    elif raw.startswith("/"):
        head, _, existing_query = raw.partition("?")
        path, existing_query = head, existing_query.split("#", 1)[0]
    else:
        return raw

    normalized = _normalize_path(path)
    if is_web_intent_path(normalized):
        return raw

    spec = match_destination(normalized)
    if spec is None or not spec.native_supported:
        return raw

    query = {
        key: value
        for key, value in parse_qsl(existing_query, keep_blank_values=True)
        if key not in (APP_INTENT_PARAM, APP_SOURCE_PARAM)
    }
    query[APP_INTENT_PARAM] = APP_INTENT_VALUE
    query[APP_SOURCE_PARAM] = normalize_source(source)
    return f"{CANONICAL_APP_ORIGIN}{normalized}?{urlencode(query)}"


# --------------------------------------------------------------------------
# Reverse matching -- used by the adapter and by the server-side fallback router
# --------------------------------------------------------------------------


def _compile(spec: Destination) -> re.Pattern[str]:
    if not spec.supports_resource:
        return re.compile(f"^{re.escape(spec.path_template)}$")
    prefix = spec.path_template.split("/{id}")[0]
    body = r"[1-9][0-9]{0,17}" if spec.id_kind == ID_KIND_POSITIVE_INT else r"[^/]+"
    return re.compile(f"^{re.escape(prefix)}/({body})$")


# Ordered longest-prefix-first so `/pulse/reels/12` matches `reel`, not `reels`.
_MATCHERS: tuple[tuple[re.Pattern[str], Destination], ...] = tuple(
    sorted(
        ((_compile(spec), spec) for spec in _DESTINATION_LIST if spec.native_supported),
        key=lambda pair: (-len(pair[1].path_template), pair[1].key),
    )
)


def match_destination(path: str) -> Destination | None:
    """The destination a path names, or None if the binary would not resolve it."""

    normalized = _normalize_path(path)
    for pattern, spec in _MATCHERS:
        found = pattern.match(normalized)
        if not found:
            continue
        if spec.supports_resource and found.groups():
            if not spec.accepts_id(found.group(1)):
                # A reserved sub-page, not a resource. Fall through so a
                # sibling destination can claim it, rather than minting a link
                # that would open unrelated content.
                continue
        return spec
    return None


def is_app_intent_query(query: Mapping[str, object] | None) -> bool:
    """True when a request carries our app-intent marker."""

    if not query:
        return False
    getter = getattr(query, "get", None)
    value = getter(APP_INTENT_PARAM) if callable(getter) else None
    return str(value or "").strip() == APP_INTENT_VALUE


def app_link_source(query: Mapping[str, object] | None) -> str:
    if not query:
        return DEFAULT_APP_LINK_SOURCE
    getter = getattr(query, "get", None)
    return normalize_source(getter(APP_SOURCE_PARAM) if callable(getter) else None)


# --------------------------------------------------------------------------
# Fallback policy
# --------------------------------------------------------------------------

FALLBACK_APP_STORE = "app_store"
FALLBACK_WEB = "web"
FALLBACK_IGNORE = "ignore"
# Render the "this lives in the iPhone app" page: an explanation, an App Store
# button and a QR code to carry the destination to a phone. Distinct from
# FALLBACK_APP_STORE because a desktop visitor cannot install an iPhone app on
# the machine they are sitting at, so redirecting them to the listing ends their
# session on a page they can do nothing with.
FALLBACK_APP_ONLY = "app_only"


def fallback_decision(path: str, is_ios: bool, is_app_intent: bool) -> tuple[str, str]:
    """What the server should do when an app-intent request reaches it.

    Reaching Flask at all means the app did not claim the link, which means it is
    not installed (or the platform is not iOS). Returns `(action, detail)`; the
    caller supplies the actual App Store URL from `bot.pulsesoc_app_store_url()`,
    so no redirect target here is ever derived from request input.

    On iOS: the App Store listing, always -- that is the whole contract.

    Off iOS, an iOS App Store listing is not a usable destination, so a desktop or
    Android visitor continues to the web page when one genuinely exists. When it
    does not, they get FALLBACK_APP_ONLY: a page that names the destination,
    links the listing and offers a QR code to carry it to a phone.

    `native_supported=False` short-circuits both of those, including the iOS
    branch, because no amount of installing reaches a route the binary does not
    have.

    That last branch used to return the App Store listing itself. Redirecting a
    desktop visitor to an iPhone listing they cannot install from is a dead end,
    and for the Marketplace family -- now `web_equivalent=False` throughout -- it
    would have become the single most common outcome on desktop.
    """

    if not is_app_intent:
        return FALLBACK_IGNORE, "not_app_intent"

    normalized = _normalize_path(path)
    if is_web_intent_path(normalized):
        return FALLBACK_IGNORE, "web_intent_path"

    spec = match_destination(normalized)
    if spec is None:
        # Unknown destination: fail safe onto our own home page. Never a
        # redirect built from anything the caller sent.
        return FALLBACK_WEB, "unknown_destination"

    if not spec.native_supported:
        # The shipped binary has no route for this, so installing the app would
        # not get the visitor there -- on iOS or anywhere else. `app_intent_url`
        # never marks these, so reaching here means a hand-made or stale URL,
        # which is exactly the case worth handling rather than assuming away.
        # Sending them to the listing would be the CTA-honesty violation the
        # registry exists to prevent, just committed by the server instead of by
        # a button.
        return (FALLBACK_WEB, spec.key) if spec.web_equivalent else (
            FALLBACK_WEB,
            "native_unsupported",
        )

    if is_ios:
        return FALLBACK_APP_STORE, spec.key
    if spec.web_equivalent:
        return FALLBACK_WEB, spec.key
    return FALLBACK_APP_ONLY, spec.key


def destination_label(destination: str, fallback: str = "Open in PulseSoc") -> str:
    """Truthful CTA wording for a destination the binary actually resolves."""

    spec = DESTINATIONS.get(str(destination or "").strip().lower())
    if spec is None or not spec.native_supported:
        return fallback
    return spec.label


def describe_destinations() -> list[dict[str, object]]:
    """The vocabulary as data, for documentation and tests."""

    return [
        {
            "key": spec.key,
            "path": spec.path_template,
            "id_kind": spec.id_kind,
            "id_required": spec.id_required,
            "native_supported": spec.native_supported,
            "native_screen": spec.native_screen,
            "web_equivalent": spec.web_equivalent,
            "auth_required": spec.auth_required,
            "label": spec.label,
            "notes": spec.notes,
        }
        for spec in _DESTINATION_LIST
    ]


__all__ = [
    "APP_INTENT_PARAM",
    "APP_INTENT_VALUE",
    "APP_SOURCE_PARAM",
    "APP_LINK_SOURCES",
    "APP_FIRST_DESPITE_WEB_ROUTE",
    "APP_SCHEME",
    "APP_STORE_QR_ASSET",
    "AppLinkError",
    "CANONICAL_APP_HOST",
    "CANONICAL_APP_ORIGIN",
    "DEGRADE_TO",
    "DESTINATIONS",
    "Destination",
    "EVENT_APP_ONLY_INTERSTITIAL",
    "EVENT_LINK_FALLBACK",
    "EVENT_LINK_GENERATED",
    "EVENT_LINK_INVALID",
    "EVENT_NATIVE_OPEN_SELECTED",
    "EVENT_OPEN_REQUESTED",
    "EVENT_UNKNOWN_DESTINATION",
    "FALLBACK_APP_ONLY",
    "FALLBACK_APP_STORE",
    "FALLBACK_IGNORE",
    "FALLBACK_WEB",
    "WEB_INTENT_OVERRIDES",
    "app_intent_url",
    "app_link_source",
    "app_scheme_url",
    "app_store_qr_asset",
    "app_store_url",
    "build_app_link",
    "describe_destinations",
    "destination_label",
    "fallback_decision",
    "is_app_intent_query",
    "is_web_intent_path",
    "match_destination",
    "normalize_source",
    "resolve_destination_path",
]
