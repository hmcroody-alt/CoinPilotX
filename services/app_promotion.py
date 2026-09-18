"""Where the website asks someone to install PulseSoc, and how often.

`app_links` already owns *where a link goes*. This module owns the other half:
which promotional surfaces exist, what they say, when one is allowed to appear,
and how long a dismissal lasts. It deliberately builds nothing about
destinations itself -- every URL here comes back through `app_links`, so there
is still exactly one link authority on this site.

The reason this is a module and not four blocks of markup spread through
`bot.py` is the frequency rule. "No more than one promotional surface at a
time, dismissible, remembered for a week" cannot be enforced by four
independent surfaces that each keep their own localStorage key and each decide
for themselves whether they are allowed to draw. It has to be arbitrated in one
place, which is `static/js/pulse_app_promotion.js`, and that script is only
coherent if the surface registry it arbitrates over is generated from here.

What counts as a promotional surface
------------------------------------
Only the *uninvited* ones -- the sidebar card, the Marketplace explanation, and
Apple's Smart App Banner. The "Get PulseSoc" header control is navigation
chrome: it is inert until pressed, it never expands on its own, and it is the
thing a member reaches for when they came looking. Counting it would mean the
site has no discoverable way to find the app for a week after one dismissal,
which is the opposite of the point.

The existing PWA install prompt (`static/js/pulse_pwa_install.js`) is a
promotional surface owned by someone else. It is arbitrated with, not absorbed:
this module never renders it and never changes its copy, but the two scripts
agree to stand down for each other so a visitor is never asked to install two
different things at once.
"""

from __future__ import annotations

import html as _html
import json
import re

from services import app_links


# --------------------------------------------------------------------------
# Surfaces
# --------------------------------------------------------------------------

SURFACE_HEADER = "header_control"
SURFACE_DESKTOP_CARD = "desktop_card"
SURFACE_MARKETPLACE_NOTE = "marketplace_note"
SURFACE_SMART_BANNER = "smart_banner"

# The surfaces the frequency policy arbitrates over, and how each behaves.
#
# `oncePerSession` is the difference between a surface that interrupts and one
# that does not. The Marketplace note appears over the page in response to a
# gesture, so showing it twice in one visit is nagging even if it was never
# dismissed. The sidebar card is in-flow furniture at the bottom of a column
# nobody has to look at; suppressing it after one page view would not make it
# less intrusive, it would just make the app undiscoverable for the rest of the
# session.
#
# `priority` breaks ties when two surfaces become eligible at once. Nothing
# ever swaps a visible surface for a higher-priority one -- first eligible
# wins and holds the slot until it is dismissed, because replacing a card
# someone is reading with a different card is worse than showing neither.
SURFACE_POLICY = {
    SURFACE_MARKETPLACE_NOTE: {"oncePerSession": True, "priority": 10},
    SURFACE_DESKTOP_CARD: {"oncePerSession": False, "priority": 20},
}

ARBITRATED_SURFACES = tuple(
    sorted(SURFACE_POLICY, key=lambda key: SURFACE_POLICY[key]["priority"])
)

# One week. Long enough that "not now" is respected rather than re-asked the
# next morning, short enough that a member who changes phones is not locked out
# of the prompt for a quarter. Deliberately longer than the PWA prompt's 24h:
# installing a native app is a bigger ask than adding a bookmark.
DISMISS_MEMORY_DAYS = 7
DISMISS_MEMORY_MS = DISMISS_MEMORY_DAYS * 24 * 60 * 60 * 1000

STORAGE_PREFIX = "pulseAppPromo:"

# Set for the lifetime of the tab, so a dismissal that never reached
# localStorage (private mode, storage disabled, quota) still stops the surface
# coming back on every navigation within the session.
SESSION_STORAGE_PREFIX = "pulseAppPromoSession:"


# --------------------------------------------------------------------------
# Copy
# --------------------------------------------------------------------------

CARD_TITLE = "PulseSoc for iPhone"
CARD_BODY = "Create, message, go live and take PulseSoc anywhere."
CARD_CONTINUE_LABEL = "Continue on web"
CARD_DISMISS_LABEL = "Dismiss the PulseSoc app suggestion"

HEADER_CONTROL_LABEL = "Get PulseSoc"

MARKETPLACE_PILL_LABEL = "APP"
# Not "Marketplace opens in...". The same marker lands on Seller Tools, which
# is the other app-first destination in the same nav lists, and a pill whose
# accessible name names the wrong screen is worse than no pill.
MARKETPLACE_PILL_TITLE = "Opens in the PulseSoc iPhone app"
MARKETPLACE_NOTE_TITLE = "Marketplace lives in the app"
MARKETPLACE_NOTE_BODY = "Scan to open or download PulseSoc."
MARKETPLACE_NOTE_DISMISS_LABEL = "Dismiss the Marketplace app explanation"

QR_ALT = "QR code that opens the PulseSoc listing on the App Store"
QR_CAPTION = "Scan with your iPhone camera."

# States what the link does, so the accessible name still means something read
# out of context. Matches the wording already used by `_app_link_cta.html`.
APP_STORE_LABEL = "Download on the App Store"


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------

EVENT_SURFACE_SHOWN = "app_promo_surface_shown"
EVENT_SURFACE_DISMISSED = "app_promo_surface_dismissed"
EVENT_SURFACE_SUPPRESSED = "app_promo_surface_suppressed"
EVENT_APP_STORE_SELECTED = "app_promo_app_store_selected"
EVENT_HEADER_OPENED = "app_promo_header_opened"

# Everything reported is a constant from this file plus a surface key. No path,
# no query string, no resource id: a promotion event has no business carrying
# which listing someone was looking at, and `/api/track` already records the
# session and user agent it needs on its own.
TELEMETRY_EVENTS = {
    "shown": EVENT_SURFACE_SHOWN,
    "dismissed": EVENT_SURFACE_DISMISSED,
    "suppressed": EVENT_SURFACE_SUPPRESSED,
    "appStore": EVENT_APP_STORE_SELECTED,
    "headerOpened": EVENT_HEADER_OPENED,
}


# --------------------------------------------------------------------------
# Smart App Banner
# --------------------------------------------------------------------------

# Only the pages a logged-out visitor can actually reach. Every other anonymous
# surface on this domain is a legal page, an auth page or the `/open/`
# interstitial, and none of those is improved by a banner: the legal pages exist
# to be read on the web, and the interstitial *is* the app pitch.
#
# `/app` joined the list when it stopped redirecting anonymous visitors to
# /signup. It is the one page on this domain whose subject is the app, so it
# meets the stated rule more squarely than the other two do.
SMART_BANNER_PATHS = frozenset({"/", "/search", "/app"})

# `/features` and its eight children are the same argument as `/app`, made one
# feature at a time, so they carry the same banner.
#
# A prefix rather than the nine literal paths because the slugs are defined in
# `seo/features.py` and this package cannot import from `seo` -- the dependency
# runs the other way and must keep running the other way. The usual objection to
# a prefix is that it claims URLs nobody defined, which does not apply here:
# `/features/<slug>` 404s for an unknown slug, so there is no page for the
# over-broad rule to reach. `tests/test_feature_pages.py` asserts that every
# defined feature path gets the banner, which is the half a prefix cannot prove
# on its own.
SMART_BANNER_PREFIXES = ("/features",)

# Generic on purpose. `app-argument` is handed to the app verbatim when the
# banner is tapped, and iOS shows the banner whether or not the app is
# installed -- so naming a destination here would be a promise the website
# cannot keep. There is no public content page on this domain today, so there
# is no per-page destination to preserve, and claiming one would send an
# installed member somewhere they were not.
SMART_BANNER_APP_ARGUMENT = f"{app_links.CANONICAL_APP_ORIGIN}/"

_APP_STORE_ID_RE = re.compile(r"/id(\d+)\b")


def app_store_app_id() -> str:
    """The numeric App Store id, read back out of the one URL that defines it.

    Not a second constant. `app_links.APP_STORE_FALLBACK_URL` is the single
    place the listing is spelled out, and a Smart App Banner carrying a
    different id than the download button beside it is exactly the drift this
    avoids. Returns "" when the configured URL has no id in it, which is the
    honest answer: no banner beats a banner pointing at an unknown app.
    """

    found = _APP_STORE_ID_RE.search(app_links.app_store_url())
    return found.group(1) if found else ""


def smart_app_banner_content() -> str:
    """The `apple-itunes-app` meta content, or "" when it cannot be built."""

    app_id = app_store_app_id()
    if not app_id:
        return ""
    return f"app-id={app_id}, app-argument={SMART_BANNER_APP_ARGUMENT}"


def wants_smart_app_banner(path: str) -> bool:
    """Whether this path is one of the ones the banner is scoped to."""

    # An empty path is not the site root. `bot.inject_app_link_helpers` passes
    # "" when there is no request context, and "/" compares equal to "" once
    # the trailing slash is stripped -- so without this the banner would land
    # on every template rendered outside a request.
    if not path:
        return False
    normalized = path.split("?")[0].rstrip("/")
    if normalized in {p.rstrip("/") for p in SMART_BANNER_PATHS}:
        return True
    return any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in SMART_BANNER_PREFIXES
    )


def smart_app_banner_meta(path: str) -> str:
    """The meta tag for a page, or "" when this page does not carry one."""

    if not wants_smart_app_banner(path):
        return ""
    content = smart_app_banner_content()
    if not content:
        return ""
    return f'<meta name="apple-itunes-app" content="{_html.escape(content, quote=True)}">'


# --------------------------------------------------------------------------
# Runtime configuration
# --------------------------------------------------------------------------


def runtime_config() -> dict:
    """What the browser needs to enforce the policy, and nothing more."""

    return {
        "storagePrefix": STORAGE_PREFIX,
        "sessionStoragePrefix": SESSION_STORAGE_PREFIX,
        "dismissMemoryMs": DISMISS_MEMORY_MS,
        "dismissMemoryDays": DISMISS_MEMORY_DAYS,
        "surfaces": {key: dict(SURFACE_POLICY[key]) for key in ARBITRATED_SURFACES},
        "events": dict(TELEMETRY_EVENTS),
        "trackEndpoint": "/api/track",
    }


def runtime_config_script() -> str:
    """The config, inlined ahead of the policy script."""

    return (
        "<script>window.PULSE_APP_PROMOTION="
        + json.dumps(runtime_config())
        + ";</script>"
    )


def assets_html() -> str:
    """Stylesheet plus policy script, in the order they have to load.

    The stylesheet is a `<link>` rather than inline CSS because these surfaces
    render on every shell page and an inline block would be re-sent on each of
    them. The script is deferred: nothing it does is needed before first paint,
    because a dismissed surface is hidden by the server, not by this script.
    """

    return (
        '<link rel="stylesheet" href="/static/css/pulse_app_promotion.css">'
        + runtime_config_script()
        + '<script defer src="/static/js/pulse_app_promotion.js"></script>'
    )


# --------------------------------------------------------------------------
# Markup
# --------------------------------------------------------------------------


def _qr_figure_html() -> str:
    """The QR block, or "" when the asset can no longer be trusted.

    `app_store_qr_asset()` returns "" once `PULSESOC_APP_STORE_URL` no longer
    matches the URL the committed QR was generated from. That is the whole
    point of asking it rather than hard-coding the path: a stale QR is worse
    than no QR, because a phone camera will follow it without anyone reading it
    first. The App Store link below is rendered either way, so the card never
    depends on the picture.
    """

    asset = app_links.app_store_qr_asset()
    if not asset:
        return ""
    src = _html.escape(asset, quote=True)
    return (
        '<figure class="pulse-app-promo__qr">'
        f'<img src="{src}" alt="{_html.escape(QR_ALT, quote=True)}" width="120" height="120" loading="lazy" decoding="async">'
        f"<figcaption>{_html.escape(QR_CAPTION)}</figcaption>"
        "</figure>"
    )


def _app_store_link_html(surface: str, classes: str) -> str:
    store_url = _html.escape(app_links.app_store_url(), quote=True)
    return (
        f'<a class="{classes}" href="{store_url}" rel="noopener"'
        ' data-app-link="app-store"'
        f' data-app-promo-action="app-store" data-app-promo-surface="{_html.escape(surface, quote=True)}"'
        f">{_html.escape(APP_STORE_LABEL)}</a>"
    )


def open_in_app_action_html(path: str, surface: str) -> str:
    """"Open this <thing> in PulseSoc" for the page someone is actually on.

    This is the only honest form of a destination-aware action on this domain.
    A canonical `https://pulsesoc.com/...?pulse_app=1` link cannot be used here:
    it is tapped from the same domain it points at, iOS does not consult
    associated domains for a same-domain tap, so the request comes back to
    Flask and the app-intent hook sends a member who *has* the app to the App
    Store. `app_scheme_url` is the one form that reaches an installed app from
    a page on its own domain, and it raises rather than guess when the shipped
    binary does not declare the destination.

    Returns "" for every path the app cannot open, which is most of them. That
    is the CTA-honesty rule doing its job: no button is better than a button
    that opens the app on Home while claiming to open a profile.

    The caller is responsible for only passing a path on iOS. A `pulsesoc://`
    href on a desktop browser is a dead control -- nothing happens, or the OS
    shows a "no application is set to open this" sheet -- and this module
    cannot tell: `is_ios_user_agent` lives in bot.py and importing it here
    would make `app_links`'s dependents circular.
    """

    try:
        href = app_links.app_scheme_url(path)
    except app_links.AppLinkError:
        return ""
    spec = app_links.match_destination(path)
    if spec is None:
        return ""
    label = app_links.destination_label(spec.key)
    return (
        '<a class="pulse-app-promo__button pulse-app-promo__button--ghost"'
        f' href="{_html.escape(href, quote=True)}"'
        f' data-app-link="open-installed" data-app-promo-action="open-in-app"'
        f' data-app-promo-surface="{_html.escape(surface, quote=True)}">'
        f"{_html.escape(label)}</a>"
    )


def promotion_card_html(
    *,
    surface: str,
    dismissible: bool,
    hidden: bool = False,
    native_path: str = "",
) -> str:
    """The app pitch, in the one shape both places that show it use.

    The header panel and the sidebar card are the same content with different
    framing -- the panel was asked for, the card was not -- so they share this
    function. Divergence here is how a site ends up telling members two
    different things about the same app.

    A `<section>` with a heading rather than a `role="dialog"`: nothing here
    traps focus, nothing is modal, and announcing it as a dialog would tell a
    screen-reader user to expect an escape key that does nothing.
    """

    hidden_attr = " hidden" if hidden else ""
    dismiss_html = ""
    continue_html = ""
    if dismissible:
        dismiss_html = (
            '<button class="pulse-app-promo__close" type="button"'
            f' data-app-promo-dismiss="{_html.escape(surface, quote=True)}"'
            f' aria-label="{_html.escape(CARD_DISMISS_LABEL, quote=True)}">'
            '<span aria-hidden="true">×</span></button>'
        )
        continue_html = (
            '<button class="pulse-app-promo__button pulse-app-promo__button--ghost" type="button"'
            f' data-app-promo-dismiss="{_html.escape(surface, quote=True)}">'
            f"{_html.escape(CARD_CONTINUE_LABEL)}</button>"
        )

    return (
        f'<section class="pulse-app-promo pulse-app-promo--{_html.escape(surface, quote=True)}"'
        f' data-app-promo="{_html.escape(surface, quote=True)}"'
        f' aria-label="{_html.escape(CARD_TITLE, quote=True)}"{hidden_attr}>'
        '<div class="pulse-app-promo__head">'
        '<img class="pulse-app-promo__mark" src="/static/brand/pulsesoc-mark-20260913.png" alt="" width="40" height="40">'
        "<div class=\"pulse-app-promo__copy\">"
        f'<h2 class="pulse-app-promo__title">{_html.escape(CARD_TITLE)}</h2>'
        f'<p class="pulse-app-promo__note">{_html.escape(CARD_BODY)}</p>'
        "</div>"
        f"{dismiss_html}"
        "</div>"
        f"{_qr_figure_html()}"
        '<div class="pulse-app-promo__actions">'
        + (open_in_app_action_html(native_path, surface) if native_path else "")
        + _app_store_link_html(
            surface, "pulse-app-promo__button pulse-app-promo__button--primary"
        )
        + f"{continue_html}"
        "</div>"
        "</section>"
    )


def header_control_html() -> str:
    """The "Get PulseSoc" control in the desktop top bar.

    A `<details>` for the same reason the Apps menu and the account menu beside
    it are: it opens on click or Enter, closes on the same, is reachable by tab
    and is announced as expandable without a line of JavaScript. Critically it
    also cannot navigate -- a desktop visitor pressing this gets a panel, never
    a redirect to an App Store page they cannot use on the machine they are on.

    Deliberately not given the `pulse-topnav-control` class the icons beside it
    use. That class is a fixed 52x52 square declared twice -- once in
    pulse_desktop_feed.css and again inside the shell's inline <style> -- and a
    two-word label inside a fixed square either overflows or gets clipped. It
    gets its own class with the same glass treatment and an auto width.
    """

    return (
        '<details class="pulse-topnav-app-promo" data-app-promo-header>'
        f'<summary class="pulse-topnav-app-promo__summary" aria-label="{_html.escape(HEADER_CONTROL_LABEL, quote=True)}">'
        f"<span>{_html.escape(HEADER_CONTROL_LABEL)}</span></summary>"
        '<div class="pulse-topnav-app-promo-panel">'
        + promotion_card_html(surface=SURFACE_HEADER, dismissible=False)
        + "</div>"
        "</details>"
    )


# Every on-site button whose destination lives in the app is built by
# `app_links.open_interstitial_url`, and that function is the only thing on
# this site that produces a path under `/open/`. So "is this link app-first"
# is a question about the URL, not about the label -- which means a new
# app-first destination gets the marker automatically, and a link that stops
# being app-first loses it, without anyone maintaining a list of labels here.
APP_FIRST_HREF_PREFIX = "/open/"


def is_app_first_href(href: str) -> bool:
    """Whether this href hands off to the app rather than to a web screen."""

    return str(href or "").startswith(APP_FIRST_HREF_PREFIX)


def nav_marker_attrs(href: str) -> str:
    """The attribute that makes a nav link a trigger for the explanation."""

    return ' data-app-promo-marketplace="1"' if is_app_first_href(href) else ""


def marketplace_pill_html() -> str:
    """The `APP` marker beside a Marketplace destination.

    The visible text is two letters, which is meaningless to a screen reader in
    a list of navigation links, so the readable sentence goes in a visually
    hidden span and the letters are hidden from the accessibility tree. `title`
    carries the same sentence for a pointer user who hovers.
    """

    return (
        f'<span class="pulse-app-pill" title="{_html.escape(MARKETPLACE_PILL_TITLE, quote=True)}">'
        f'<span aria-hidden="true">{_html.escape(MARKETPLACE_PILL_LABEL)}</span>'
        f'<span class="pulse-app-pill__sr">{_html.escape(MARKETPLACE_PILL_TITLE)}</span>'
        "</span>"
    )


def nav_marker_html(href: str) -> str:
    """The `APP` pill for a nav link, or "" when the link stays on the web."""

    return marketplace_pill_html() if is_app_first_href(href) else ""


def marketplace_note_html() -> str:
    """The dismissible explanation, rendered hidden until the policy allows it.

    Shipped in the page rather than built by the script so that its copy, its
    QR and its store link come from this module like every other surface, and
    so a visitor with JavaScript disabled who somehow reveals it still gets a
    working App Store link rather than an empty box.
    """

    surface = SURFACE_MARKETPLACE_NOTE
    return (
        f'<section class="pulse-app-promo pulse-app-promo--{surface}"'
        f' data-app-promo="{surface}" data-app-promo-anchored="marketplace"'
        f' aria-label="{_html.escape(MARKETPLACE_NOTE_TITLE, quote=True)}" hidden>'
        '<div class="pulse-app-promo__head">'
        '<div class="pulse-app-promo__copy">'
        f'<h2 class="pulse-app-promo__title">{_html.escape(MARKETPLACE_NOTE_TITLE)}</h2>'
        f'<p class="pulse-app-promo__note">{_html.escape(MARKETPLACE_NOTE_BODY)}</p>'
        "</div>"
        '<button class="pulse-app-promo__close" type="button"'
        f' data-app-promo-dismiss="{surface}"'
        f' aria-label="{_html.escape(MARKETPLACE_NOTE_DISMISS_LABEL, quote=True)}">'
        '<span aria-hidden="true">×</span></button>'
        "</div>"
        f"{_qr_figure_html()}"
        '<div class="pulse-app-promo__actions">'
        + _app_store_link_html(
            surface, "pulse-app-promo__button pulse-app-promo__button--primary"
        )
        + "</div>"
        "</section>"
    )
