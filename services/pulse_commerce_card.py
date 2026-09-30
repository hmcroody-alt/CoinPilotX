"""The web's renderer for a post's commerce attachment.

## What this fixes

``services/pulsedrop/hydration.py`` builds a complete commerce overlay for every
published PulseDrop post, and ``pulse_feed_engine._attach_commerce`` hangs it on
``post["commerce"]`` for the single-post read, the feed, the reels lane and a
profile's posts. The native app renders it — ``CommerceOverlay.tsx``. The web
received the identical object on every one of those posts and dropped it on the
floor: a grep for ``commerce`` across ``static/js/`` and ``templates/`` returned
nothing before this module existed.

So the gap was never data, API or serialization. It was that no web surface had
a renderer. This is that renderer, and the JavaScript twin in
``static/js/pulse_commerce_card.js`` is the same markup for the surfaces that
build their cards in the browser.

## Why there are two implementations and not one

The three web surfaces that carry an overlay do not agree on where HTML comes
from. ``/pulse/post/<id>`` is a server-rendered f-string and is the SEO and
link-unfurl target, so its card has to exist in the response body — a card
assembled by JavaScript is a card Googlebot and Slack never see. The home feed
and the reels lane build their cards in the browser from ``/api/pulse/feed`` and
``/api/pulse/reels``, and server-rendering those would mean re-plumbing two
paginated fetch loops.

Two renderers is a real cost and it is paid deliberately. What keeps them from
drifting is ``tests/pulse_commerce/test_commerce_card_parity.py``, which reads
both files and asserts they emit the same class names, read the same payload
fields and apply the same three gates. A field added to one and not the other
fails that test.

## What it must not do

The overlay is read fresh from ``marketplace_listings`` on every serialization
precisely so it cannot go stale. This module therefore renders *only* what it is
handed. It does not look a listing up, does not format a price, does not decide
availability from anything except the fields on the payload, and does not invent
a delivery estimate, a rating, a stock count, a discount or a badge. A price is
shown verbatim because the server already formatted it in the listing's own
currency; a client-side ``toFixed`` renders ¥4900 as $49.00 for a Japanese
seller.

It is also not PulseDrop-specific. Nothing here reads an author name, a hashtag,
a title or an image URL. It renders ``post["commerce"]`` when a post has one, and
that key is produced from ``pulsedrop_publications.listing_id`` — a stored
identity join — so the day a human seller can attach a product to their own post,
this renders it with no change.
"""

from __future__ import annotations

import html
from typing import Any, Mapping

from services.marketplace_listing_lifecycle import STOCKLESS_TYPES

__all__ = [
    "AVAILABLE",
    "OUT_OF_STOCK",
    "UNAVAILABLE",
    "NOT_PRICED",
    "REMOVED",
    "availability_block",
    "cta_enabled",
    "price_visible",
    "is_commerce_overlay",
    "card_html",
    "post_card_html",
]

#: The same vocabulary as ``hydration.py`` and the client's
#: ``MarketplacePurchaseBlock``. Not re-spelled as new constants with new names:
#: three independent spellings of "sold out" is how two surfaces end up
#: disagreeing about one listing in one session.
AVAILABLE = ""
OUT_OF_STOCK = "OUT_OF_STOCK"
UNAVAILABLE = "UNAVAILABLE"
NOT_PRICED = "NOT_PRICED"
REMOVED = "REMOVED"

#: Codes this build understands. Anything else is an older or a newer server and
#: gets derived rather than trusted — see :func:`availability_block`.
_KNOWN = frozenset({AVAILABLE, OUT_OF_STOCK, UNAVAILABLE, NOT_PRICED, REMOVED})

#: States in which the price may still be shown. Mirrors ``_DISCLOSED`` in
#: ``hydration.py`` and ``commercePriceVisible`` in the app: the line is drawn at
#: *who took the product down*. Sold out and unpriced are the seller still
#: offering the listing. Withdrawn, suspended and deleted were taken off sale,
#: and advertising a price for those under a verified badge would make this the
#: one PulseSoc surface that does not honour a withdrawal.
_PRICE_OK = frozenset({AVAILABLE, OUT_OF_STOCK, NOT_PRICED})

#: States whose chip is styled as gone rather than as a transient stock fact.
_GONE = frozenset({UNAVAILABLE, REMOVED})


def is_commerce_overlay(value: Any) -> bool:
    """Present, a mapping, and shaped like an overlay.

    Mirrors ``isPulseCommerceOverlay``. Deliberately checks ``pulsedrop`` rather
    than merely truthiness, so a post carrying some other future ``commerce``
    dialect does not get rendered by a renderer that cannot read it.
    """
    if not isinstance(value, Mapping):
        return False
    return value.get("pulsedrop") is True and isinstance(value.get("product"), Mapping)


def _price_minor(product: Mapping[str, Any]) -> int | None:
    """``None`` when the label does not parse to an amount checkout could charge.

    The payload ships a formatted label and no minor-unit integer, because
    ``editorial.price_text`` is the one price formatter in this product. So the
    only question answerable here is whether the label contains a number at all,
    which is exactly the question ``NOT_PRICED`` asks.
    """
    label = str(product.get("price_label") or "").strip()
    if not label:
        return None
    digits = "".join(ch for ch in label if ch.isdigit())
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    return value or None


def availability_block(commerce: Mapping[str, Any]) -> str:
    """The availability code, preferring the server's and deriving a fallback.

    Both halves are here for the reason ``commerceBlock`` gives in the app: the
    server is authoritative and is the only party that knows about ``REMOVED``,
    but re-deriving from the four shipped fields is what keeps the
    implementations honest. A shape this function does not recognise is an older
    or newer server, and defaulting an unknown to "available" is how a sold-out
    product gets an enabled button.
    """
    declared = commerce.get("availability") or {}
    code = declared.get("code") if isinstance(declared, Mapping) else None
    if isinstance(code, str) and code in _KNOWN:
        return code

    product = commerce.get("product") or {}
    if not isinstance(product, Mapping):
        return UNAVAILABLE
    if product.get("buyer_visible") is False:
        return UNAVAILABLE
    if str(product.get("inventory_state") or "").strip().lower() == "out_of_stock":
        return OUT_OF_STOCK
    product_type = str(product.get("product_type") or "").strip().lower()
    if product_type not in STOCKLESS_TYPES:
        try:
            quantity = int(product.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            return OUT_OF_STOCK
    if _price_minor(product) is None:
        return NOT_PRICED
    return AVAILABLE


def cta_enabled(commerce: Mapping[str, Any]) -> bool:
    """May the card be followed?

    Gated on the route as well as the flag, and on the availability state as
    well as both. ``/pulse/marketplace/<id>`` answers 404 for any listing that is
    not public — deliberately, so guessing an id cannot confirm a row exists — so
    a link to a withdrawn product is a link to an error page. The server already
    declines to emit a route for those states; this refuses to trust that it
    always will.
    """
    cta = commerce.get("cta")
    if not isinstance(cta, Mapping):
        return False
    if not cta.get("enabled"):
        return False
    if not str(cta.get("route") or "").strip():
        return False
    return availability_block(commerce) == AVAILABLE


def price_visible(commerce: Mapping[str, Any]) -> bool:
    """Whether the price may be shown for this state. See :data:`_PRICE_OK`."""
    return availability_block(commerce) in _PRICE_OK


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _safe_route(route: Any) -> str:
    """A same-origin path, or ``""``.

    The route arrives from ``app_links.resolve_destination_path`` and is a local
    path today. This refuses anything else anyway: the one thing a renderer must
    never do is turn a value from a payload into an ``href`` with a scheme on it.
    A ``javascript:`` route reaching here would be a stored XSS with the platform
    itself as the injection point.
    """
    text = str(route or "").strip()
    if not text.startswith("/") or text.startswith("//"):
        return ""
    return text


def _chip(text: str, extra: str = "") -> str:
    if not text:
        return ""
    classes = "pulse-commerce-chip" + (f" {extra}" if extra else "")
    return f"<span class='{classes}'>{_esc(text)}</span>"


def card_html(commerce: Any, *, surface: str = "") -> str:
    """The attachment card for one post, or ``""`` when there is nothing to show.

    Returning the empty string for a post with no overlay is the whole contract
    at the call sites: a caller interpolates this unconditionally and a
    non-commerce post is unchanged. It also returns ``""`` rather than raising
    for a malformed overlay, because ``§77`` of the brief is right — a post whose
    commerce hydration went wrong must still render as a post.
    """
    if not is_commerce_overlay(commerce):
        return ""

    product = commerce.get("product") or {}
    seller = commerce.get("seller") or {}
    label = commerce.get("label") or {}
    cta = commerce.get("cta") or {}
    availability = commerce.get("availability") or {}

    block = availability_block(commerce)
    routable = cta_enabled(commerce)
    show_price = price_visible(commerce) and bool(str(product.get("price_label") or "").strip())
    show_state = block != AVAILABLE

    variant = str(surface or commerce.get("surface") or "signal").strip().lower()
    if variant != "reel":
        # Anything unrecognised reads as a Signal, which is the treatment that
        # survives being placed anywhere. The reel variant assumes dark video
        # behind it and is unreadable on a light surface.
        variant = "signal"

    title = str(product.get("title") or "").strip()
    # ``cover_image_url`` is the key the server emits. The app's type declares
    # ``image_url`` and has therefore never shown a thumbnail on this card; that
    # is fixed alongside this file rather than mirrored here.
    image = str(product.get("cover_image_url") or product.get("image_url") or "").strip()
    price = str(product.get("price_label") or "").strip()
    store = str(seller.get("store_name") or seller.get("username") or "").strip()
    label_text = str(label.get("fallback") or "").strip()
    state_text = str(availability.get("fallback") or "").strip()
    cta_text = str(cta.get("fallback") or "").strip()
    # The server's sentence, but only where the server's sentence is allowed to
    # mention a price.
    #
    # ``hydration.py`` assembles ``accessibility_text`` under the same
    # disclosure rule the pixels follow, so for a withdrawn listing it is the
    # state alone. Taking it verbatim regardless would be the one place in this
    # module that *trusts* that rule rather than mirroring it — and it is the
    # place where a regression is invisible, because a reviewer looking at a
    # correct-looking card cannot see the ``aria-label`` announce a price the
    # card itself withholds. So when the price is withheld the sentence is
    # rebuilt from the parts this card is actually showing, and the accessible
    # surface cannot disclose more than the visual one.
    accessibility = str(commerce.get("accessibility_text") or "").strip()
    if not accessibility or not show_price:
        accessibility = ". ".join(
            part for part in (label_text, title, state_text if show_state else "") if part
        )

    thumb = (
        f"<span class='pulse-commerce-thumb'>"
        f"<img src='{_esc(image)}' alt='' loading='lazy' decoding='async'></span>"
        if image
        else "<span class='pulse-commerce-thumb is-empty' aria-hidden='true'></span>"
    )

    chips = _chip(label_text, "pulse-commerce-chip-label")
    if show_state:
        chips += _chip(
            state_text,
            "pulse-commerce-chip-state" + (" is-gone" if block in _GONE else ""),
        )
    chips_html = f"<span class='pulse-commerce-chips'>{chips}</span>" if chips else ""

    title_html = f"<span class='pulse-commerce-title'>{_esc(title)}</span>" if title else ""

    meta = ""
    if show_price:
        meta += f"<span class='pulse-commerce-price'>{_esc(price)}</span>"
    if store:
        meta += f"<span class='pulse-commerce-store'>{_esc(store)}</span>"
    meta_html = f"<span class='pulse-commerce-meta'>{meta}</span>" if meta else ""

    cta_html = (
        f"<span class='pulse-commerce-cta'>{_esc(cta_text)}</span>"
        if routable and cta_text
        else ""
    )

    body = f"{thumb}<span class='pulse-commerce-copy'>{chips_html}{title_html}{meta_html}{cta_html}</span>"

    # The whole block is one announcement, carrying the server's sentence. A
    # reader walking the individual spans announces "Trending", "Aurora Desk
    # Lamp", "$49.00", "Northlight Studio", "View product" as five unrelated
    # fragments; `aria-label` on the anchor replaces its contents with the
    # sentence a sighted member takes in at a glance. See `accessibility` above
    # for how that sentence is held to the same disclosure rule as the pixels.
    if routable:
        href = _safe_route((commerce.get("cta") or {}).get("route"))
        main = (
            f"<a class='pulse-commerce-main' href='{_esc(href)}' "
            f"aria-label='{_esc(accessibility)}'>{body}</a>"
        )
    else:
        # Not a link when there is nowhere to go. An unroutable state stays
        # static information rather than offering a click that lands on a 404,
        # which reads as a broken site.
        main = (
            f"<div class='pulse-commerce-main is-static' role='group' "
            f"aria-label='{_esc(accessibility)}'>{body}</div>"
        )

    store_route = _safe_route(seller.get("route"))
    store_html = (
        f"<a class='pulse-commerce-seller' href='{_esc(store_route)}'>Visit store</a>"
        if store_route
        else ""
    )

    attribution = (commerce.get("attribution") or {}).get("token") or ""
    listing_id = product.get("listing_id") or 0

    return (
        f"<section class='pulse-commerce-card pulse-commerce-{_esc(variant)}' "
        f"data-pulse-commerce='1' data-surface='{_esc(variant)}' "
        f"data-attribution='{_esc(attribution)}' data-listing-id='{_esc(listing_id)}' "
        f"data-availability='{_esc(block)}'>{main}{store_html}</section>"
    )


def post_card_html(post: Any, *, surface: str = "") -> str:
    """:func:`card_html` for ``post["commerce"]``. ``""`` for a post without one.

    The convenience the call sites actually want, so a template never has to
    write ``(post.get("commerce") or {})`` and never has to decide what a missing
    key means.
    """
    if not isinstance(post, Mapping):
        return ""
    return card_html(post.get("commerce"), surface=surface)
