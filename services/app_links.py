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
   apple-app-site-association claims exactly two path components, `/pulse/*`
   and `/search*` (`services/native_app_links.py`). iOS will never hand any
   other path to the app. A brand-new path family such as `/open/...` would be
   silently ignored by every installed copy of the app in the world.

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
    notes: str = ""
    # Path segments that live *under* this destination but are not resources of
    # it. `/pulse/profile/security` is the account security page, not a member
    # called "security"; `/pulse/groups/create` is the composer, not a group.
    # Without this, a slug destination would happily mint a link that opens
    # unrelated content -- the exact failure the CTA-honesty rule forbids.
    reserved_ids: frozenset[str] = frozenset()

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
    ),
    _d(
        "events",
        "/pulse/events",
        native_screen="Events",
        web_equivalent=True,
        label="Open Events in PulseSoc",
    ),
    # --- commerce --------------------------------------------------------
    _d(
        "marketplace",
        "/pulse/marketplace",
        native_screen="Tabs>Marketplace",
        web_equivalent=True,
        label="Open Marketplace in PulseSoc",
    ),
    _d(
        "product",
        "/pulse/marketplace/{id}",
        id_kind=ID_KIND_POSITIVE_INT,
        id_required=True,
        native_screen="MarketplaceDetail",
        web_equivalent=False,
        label="Open this listing in PulseSoc",
        notes="No web route for a single listing; the web fallback is /pulse/marketplace.",
    ),
    _d(
        "store",
        "/pulse/merchant/{id}",
        id_kind=ID_KIND_SLUG,
        id_required=True,
        native_screen="MerchantProfile",
        web_equivalent=True,
        label="Open this store in PulseSoc",
        reserved_ids=frozenset({"apply", "dashboard", "payouts"}),
    ),
    _d(
        "orders",
        "/pulse/orders",
        native_screen="BuyerOrders",
        web_equivalent=False,
        auth_required=True,
        label="Open your orders in PulseSoc",
        notes="No web route at all; /pulse/purchases is the nearest web surface.",
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
        notes="No web route; the web fallback is /pulse/purchases.",
    ),
    # --- AI / private ----------------------------------------------------
    _d(
        "undx",
        "/pulse/ai",
        native_screen="Tabs>PulseAI",
        web_equivalent=False,
        auth_required=True,
        label="Open PulseSoc AI",
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
    "/api/",
    "/static/",
    "/.well-known/",
    "/admin",
    "/auth/",
    "/oauth",
    "/webhook",
)


def is_web_intent_path(path: str) -> bool:
    """True when the path is ordinary website navigation and must be left alone."""

    normalized = _normalize_path(path)
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


def fallback_decision(path: str, is_ios: bool, is_app_intent: bool) -> tuple[str, str]:
    """What the server should do when an app-intent request reaches it.

    Reaching Flask at all means the app did not claim the link, which means it is
    not installed (or the platform is not iOS). Returns `(action, detail)`; the
    caller supplies the actual App Store URL from `bot.pulsesoc_app_store_url()`,
    so no redirect target here is ever derived from request input.

    On iOS: the App Store listing, always -- that is the whole contract.

    Off iOS, an iOS App Store listing is not a usable destination, so a desktop or
    Android visitor continues to the web page when one genuinely exists. When it
    does not, the listing is still the only honest place left to send them. This
    is a deliberate reading of "never the website": the rule protects the
    app-first contract on the platform where the app can actually be installed,
    and stranding a desktop reader on a 404 would serve nobody.
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

    if is_ios:
        return FALLBACK_APP_STORE, spec.key
    if spec.web_equivalent:
        return FALLBACK_WEB, spec.key
    return FALLBACK_APP_STORE, spec.key


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
    "AppLinkError",
    "CANONICAL_APP_HOST",
    "CANONICAL_APP_ORIGIN",
    "DEGRADE_TO",
    "DESTINATIONS",
    "Destination",
    "EVENT_LINK_FALLBACK",
    "EVENT_LINK_GENERATED",
    "EVENT_LINK_INVALID",
    "EVENT_OPEN_REQUESTED",
    "EVENT_UNKNOWN_DESTINATION",
    "FALLBACK_APP_STORE",
    "FALLBACK_IGNORE",
    "FALLBACK_WEB",
    "app_intent_url",
    "app_link_source",
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
