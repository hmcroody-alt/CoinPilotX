"""HTML for the two PulseSoc web Marketplace pages.

Why this is a module and not an f-string in ``bot.py``
-----------------------------------------------------
``bot.py`` is a 115k-line Flask monolith whose page HTML lives in inline
f-strings. Two consequences made that the wrong home for a storefront:

1. Nothing in an inline f-string is reachable from a test without booting the
   whole app — and ``import bot`` runs ``init_db()`` at module scope. A
   storefront's card geometry, badge honesty, escaping and structured data all
   need direct assertions.
2. The commerce rules are shared by both pages. Written twice in two f-strings,
   the discovery card and the product page priced the same listing from
   different columns — which is precisely the defect the old pages shipped
   (the grid printed ``price_label``, the API printed something else).

So ``services/marketplace_web`` owns the *facts*, this module owns the
*markup*, and the two Flask routes own only SQL and the HTTP response. This
module imports no Flask and never imports ``bot``; it is a pure function from
plain dicts to strings.

What this module will not render
--------------------------------
There is no code path here that can emit a star rating, a review count, a
"sold" count, a shipping estimate, a delivery window, a compare-at price, a
discount percentage, a "Trusted Seller" mark, or a stock countdown — not
because a flag turns them off, but because no function accepts those inputs.
The catalogue has no data behind any of them.

The purchase CTA
----------------
This is the one place where a reference mockup was deliberately not followed.
The references show a "Buy Now" primary action. Production says two things that
make that button a lie:

* ``services/marketplace_payment_pause.marketplace_card_payments_paused()``
  returns ``True`` unconditionally, so ``/api/pulse/payments/checkout`` refuses
  every Marketplace card start with ``PAYMENT_UNAVAILABLE``.
* That same route prices a marketplace product from
  ``parse_price_label_to_cents(item["price_label"])`` — the *label* — while this
  storefront prices from ``marketplace_listing_variants.price_cents``, which is
  what the supplier sync writes. For the 41 listings priced by variants, a Buy
  button would either refuse ("not priced for checkout", because the label is
  empty) or charge a number the page never displayed.

A checkout button that transacts at a price the page did not show is a money
bug, not a missing feature. So the primary action is the seller conversation —
a real, working endpoint — and the page states the platform's own payment
policy in the platform's own words. When the pause lifts and checkout is made
variant-aware, `buy_href` below is the single place that changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from services import app_links
from services import marketplace_listing_lifecycle
from services import marketplace_web as mw
from services import search_visibility

#: Canonical, stable, human-readable product and listing URLs. These are the
#: paths the old routes already served, so nothing that was indexable or
#: shareable before changes shape. See §21 of the mission brief: existing URLs
#: must not break.
BASE_PATH = "/pulse/marketplace"

#: Asset versions are bumped by hand, matching the convention every other
#: stylesheet link in `bot.py` uses.
#:
#: Bump these in the same commit as any edit to either file. They are served
#: `cache-control: public, max-age=31536000, immutable`, so a browser that has
#: already fetched a given `?v=` will not revalidate it for a year — it does not
#: ask, and the origin never gets the chance to answer. Reusing a token ships
#: fresh server HTML against stale JavaScript, which is worse than shipping
#: nothing: the variant picker renders, the radios do nothing, and the add button
#: stays disabled because the code that enables it is the code that did not
#: arrive.
#:
#: `20260927a` did exactly that. It was introduced by #84 and deployed, so
#: production had served it; this branch then rewrote the variant resolver in
#: `pulse_marketplace.js` and the option-group rules in `pulse_marketplace.css`
#: and left the token alone. Every visitor who had loaded a storefront page since
#: #84 would have kept the pre-variant script.
#:
#: `20260928a` shipped the dark storefront. `20260928b` inverts the palette to
#: match the native app's light Store, which is a whole-page colour change: a
#: browser holding the previous CSS would paint the new light-page markup with
#: dark-page rules — white text on a white card — so this is precisely the bump
#: the comment above exists to force.
CSS_HREF = "/static/css/pulse_marketplace.css?v=storefront-20261001b"
JS_SRC = "/static/js/pulse_marketplace.js?v=storefront-20261001b"

#: Cards per grid page. Mirrors `marketplace_web.PAGE_SIZE` so pagination maths
#: has one source.
PAGE_SIZE = mw.PAGE_SIZE

#: The platform's own statement about Marketplace payments, quoted rather than
#: paraphrased. Imported lazily in `payment_policy_line` so this module stays
#: importable without the payments stack.
_PAYMENT_POLICY_FALLBACK = (
    "Arrange payment directly with the seller. Card checkout is not available "
    "for Marketplace right now."
)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Viewer:
    """Who is looking, for authorization-sensitive affordances only.

    `is_admin` and `user_id` gate *rendering* of the Promote control. They are
    not the authorization — the API route re-checks ownership server-side.
    Hiding a button is a UI courtesy; it is never a permission.
    """

    user_id: int = 0
    signed_in: bool = False
    is_admin: bool = False

    def owns(self, seller_user_id: Any) -> bool:
        try:
            seller = int(seller_user_id or 0)
        except (TypeError, ValueError):
            return False
        return bool(self.user_id) and seller == int(self.user_id)


@dataclass(frozen=True)
class Filters:
    """The URL-addressable state of the discovery page.

    Every field here round-trips through the query string, which is what makes a
    filtered view shareable, bookmarkable, reloadable and crawlable. Nothing
    about the visible result set is held in JavaScript state or a cookie.
    """

    category: str = ""
    q: str = ""
    sort: str = mw.DEFAULT_SORT
    page: int = 1

    @classmethod
    def from_args(cls, args: Mapping[str, Any]) -> "Filters":
        """Parse and *clamp*, never trust.

        A category is reduced to a slug shape by `slugify`, so a crafted value
        cannot escape into markup or SQL — it is only ever compared against
        slugs derived from the catalogue's own categories. `sort` is snapped to
        the four known keys. `page` is coerced to a positive int and later
        clamped to the real page count by `paginate`.
        """
        raw_page = str(args.get("page") or "1").strip()
        try:
            page = int(raw_page)
        except ValueError:
            page = 1
        category = str(args.get("category") or "").strip()
        # A department/section pair arrives as "mens-clothing/shirts". Each
        # segment is slugified independently so the separator survives.
        category = "/".join(
            mw.slugify(segment) for segment in category.split("/") if mw.slugify(segment)
        )[:120]
        return cls(
            category=category,
            q=str(args.get("q") or "").strip()[:120],
            sort=mw.normalize_sort(args.get("sort")),
            page=max(1, page),
        )

    def as_base(self) -> dict[str, Any]:
        return {"category": self.category, "q": self.q, "sort": self.sort, "page": self.page}

    def url(self, **overrides: Any) -> str:
        """A URL for this page with some facets changed.

        Any facet change resets the page, because page 7 of a different filter
        is almost never a page that exists. Callers that mean to move within the
        current filter pass `page=` explicitly.
        """
        base = self.as_base()
        if "page" not in overrides:
            overrides["page"] = 1
        return BASE_PATH + mw.build_query_string(base, **overrides)


@dataclass
class RenderedPage:
    """Everything a Flask route needs to build a response.

    Split into `head_html` / `body_html` / `script_html` so the same page body
    can be wrapped by either the member shell or the public document. See
    `bot.pulse_marketplace_page` for why there are two wrappers.
    """

    title: str
    meta_description: str
    body_html: str
    canonical_path: str = BASE_PATH
    indexable: bool = False
    jsonld: tuple[dict[str, Any], ...] = ()
    og_image: str = ""
    #: Extra <link>/<script> tags the body depends on, safe in either wrapper.
    assets_html: str = ""
    robots_extra: str = ""
    #: `og:type`. A product page is `product`, not `website` — Facebook, Slack
    #: and Discord all render a product card differently, and the value is a
    #: fact about the page, so it belongs beside the rest of the metadata.
    og_type: str = "website"
    #: The listing ids this page actually rendered, in the order it rendered
    #: them. Reported rather than recomputed because filtering, sorting and
    #: paging all happen inside `render_discovery`: a caller that wants to
    #: describe this page's contents in structured data would otherwise have to
    #: re-derive the result set, and the copy that drifted would be the one
    #: Google reads. Empty on pages that are not lists.
    listed_ids: tuple[int, ...] = ()


# ---------------------------------------------------------------------------
# Small primitives
# ---------------------------------------------------------------------------

esc = mw.esc


def _attr(name: str, value: Any) -> str:
    """One attribute, or nothing at all when the value is empty.

    Emitting `alt=""` is meaningful (a decorative image); emitting `title=""` or
    `href=""` is not — an empty `href` reloads the page. So empties are dropped
    rather than rendered.
    """
    text = mw._clean(value)
    return f' {name}="{esc(text)}"' if text else ""


def payment_policy_line() -> str:
    """The real payment lane, in the platform's own words.

    Read from `marketplace_payment_pause` rather than restated, so this sentence
    cannot drift from the behaviour of the checkout route that enforces it. When
    the pause lifts, this line stops appearing without anything here changing.
    """
    try:
        from services import marketplace_payment_pause
    except Exception:
        return _PAYMENT_POLICY_FALLBACK
    try:
        if not marketplace_payment_pause.marketplace_card_payments_paused():
            return ""
        return str(marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_MESSAGE or "")
    except Exception:
        return _PAYMENT_POLICY_FALLBACK


def product_path(listing_id: Any) -> str:
    """Where a product card points, taken from the app-link registry.

    Not `f"{BASE_PATH}/{id}"`, though that is exactly what the registry returns
    today. `services/app_links.py` is the single authority for where a Marketplace
    button goes, and it holds one fact a literal here cannot: whether `product`
    has a finished web page at all. While it does, the href is that page; if that
    ever flips, the same call returns the `/open/product/...` interstitial
    instead. A literal would keep producing a web path straight through the flip
    and would go on *working* -- linking members to a page the registry had
    already decided not to send them to, with nothing failing to say so.

    That is not hypothetical. It happened on this page, between the
    server-rendered card and its JavaScript twin, which is why the registry grew
    `website_href_template` for browser-built cards in the first place.

    `app_links` is stdlib-only and imports neither Flask nor `bot`, so taking the
    dependency keeps the engine bootable in 0.04s and its suite database-free.
    """
    return app_links.website_href("product", int(listing_id or 0), source="web")


def product_url(listing_id: Any, origin: str = mw.PUBLIC_ORIGIN) -> str:
    return f"{origin}{product_path(listing_id)}"


def cart_path() -> str:
    """Where the header's cart link points, taken from the same registry.

    A literal `/pulse/cart` is the right answer today and the wrong thing to
    write, for the reason `product_path` sets out at length: `app_links` owns
    whether `cart` is a web page or an `/open/cart` handoff, and this link is one
    of the places that would still be pointing at the retired answer after that
    decision changes. There is nothing special about the cart here — it is the
    same rule, applied to a second destination.
    """
    return app_links.website_href("cart", source="web")


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


def media_box(
    item: Optional[mw.MediaItem],
    *,
    alt: str,
    eager: bool = False,
    shape: str = "",
    inner_html: str = "",
) -> str:
    """The one image component every product photo on the site goes through.

    Four things this does that a bare ``<img>`` does not:

    * The *box* carries the aspect ratio (in CSS), so the grid's geometry is
      fixed before any byte of image arrives. No layout shift, at any
      connection speed, regardless of what the supplier's image dimensions turn
      out to be.
    * ``object-fit: contain`` (in CSS) means nothing is ever stretched and
      nothing is ever cropped through the subject. This catalogue's images are
      not art-directed — there is no focal-point data and no safe crop region —
      so a destructive crop would silently behead products. A tall product
      letterboxes instead.
    * ``loading``/``decoding``/``fetchpriority`` are set per position: the first
      card and the gallery's first slide load eagerly at high priority because
      they are the largest contentful paint; everything below the fold is lazy.
    * A failure element is always in the markup, so a dead CDN URL renders a
      labelled plate rather than a browser's broken-image glyph. The `is-broken`
      class is applied by the page script on the image's `error` event, and also
      for an already-failed cached image (`naturalWidth === 0`), which no
      `error` listener can catch.

    No ``srcset``. That is a measured decision, not an omission: product images
    are served from an R2 bucket behind ``cdn.coinpilotx.app`` with no image
    transform service in front of it — there is no resize endpoint in the repo
    and no Cloudflare Images binding. Emitting a ``srcset`` of URLs that do not
    exist would break every image; emitting ``sizes`` without ``srcset`` does
    nothing at all. When a transform origin exists, it belongs here and only
    here.
    """
    classes = "mkt-media" + (f" {shape}" if shape else "")
    if item is None or not item.url:
        # Absence, not error: a listing with no photograph gets a quiet plate.
        return (
            f'<div class="{classes} is-empty">'
            f'<span class="mkt-media-fallback">No photo yet</span>'
            f"{inner_html}</div>"
        )
    if item.kind == "video":
        poster = _attr("poster", item.poster)
        return (
            f'<div class="{classes}">'
            f'<video src="{esc(item.url)}"{poster} controls preload="none" playsinline></video>'
            f'<span class="mkt-media-fallback">Video unavailable</span>'
            f"{inner_html}</div>"
        )
    loading = "eager" if eager else "lazy"
    priority = ' fetchpriority="high"' if eager else ""
    return (
        f'<div class="{classes}">'
        f'<img src="{esc(item.url)}" alt="{esc(alt)}" loading="{loading}"'
        f' decoding="async"{priority}>'
        f'<span class="mkt-media-fallback">Image unavailable</span>'
        f"{inner_html}</div>"
    )


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------

_BADGE_CLASS = {
    mw.BADGE_FEATURED: "is-featured",
    mw.BADGE_NEW: "is-new",
    mw.BADGE_DIGITAL: "is-digital",
}


def badges_html(badges: Sequence[mw.Badge]) -> str:
    if not badges:
        return ""
    chips = "".join(
        f'<span class="mkt-badge {_BADGE_CLASS.get(badge.key, "is-quiet")}">{esc(badge.label)}</span>'
        for badge in badges
    )
    return f'<div class="mkt-badges">{chips}</div>'


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


def _stock_class(line: str) -> str:
    lowered = line.lower()
    if "out of stock" in lowered:
        return " is-out"
    if lowered.startswith("only") or "last one" in lowered:
        return " is-low"
    return ""


def product_card(
    row: Mapping[str, Any],
    *,
    price: mw.PriceView,
    badges: Sequence[mw.Badge] = (),
    stock: str = "",
    eager: bool = False,
    seller_href: str = "",
    cart: Optional[mw.CartAffordance] = None,
    choose_options: bool = False,
) -> str:
    """One grid card.

    Card geometry is uniform *by construction* rather than by luck — see the
    stylesheet's §6 note. The DOM order encodes the information hierarchy the
    brief asks for and the CSS does not reorder it: photograph, then title, then
    price, then secondary metadata. So the hierarchy survives with CSS disabled
    and is the order a screen reader announces.

    The whole card is a single link (`.mkt-card-link::after` stretches over it),
    not a div with a click handler, so it is keyboard reachable, focusable,
    middle-clickable and copyable. The seller link is raised above that overlay
    so it stays separately clickable — the one place two links overlap, handled
    with z-index rather than by nesting anchors, which is invalid HTML.

    ``cart`` is opt-in per call site and absent by default, which is what keeps
    the related-products rail and any other card consumer unchanged: a rail whose
    caller never derived a `CartAffordance` renders exactly the markup it did
    before. When it is supplied the button is raised above the card-wide link the
    same way the seller link is, because a click meant for "add to cart" must not
    navigate to the product page instead.

    ``choose_options`` is the other half of that opt-in and is mutually exclusive
    with it: a listing whose buyer has to pick a size gets a link to the picker
    where a single-variant listing gets the button. Both occupy the same
    ``.mkt-card-actions`` row so the grid keeps one action line at one height,
    which is what stops a mixed page of configurable and non-configurable
    products from looking ragged.
    """
    listing_id = int(row.get("id") or 0)
    title = mw._clean(row.get("title")) or "Marketplace listing"
    media = mw.gallery_items(row, limit=1)
    first = media[0] if media else None
    # Alt text is the product's own name. It is the only honest description of a
    # product photograph available here, and it is what a screen-reader user
    # needs in order to tell two cards apart.
    box = media_box(first, alt=title, eager=eager, inner_html=badges_html(badges))

    seller_name = mw._clean(row.get("seller_store_name")) or ""
    seller_block = ""
    if seller_name:
        initial = esc(seller_name[:1].upper())
        avatar_url = mw._clean(row.get("seller_avatar_url"))
        avatar = (
            f'<img class="mkt-seller-avatar" src="{esc(avatar_url)}" alt="" loading="lazy" decoding="async">'
            if avatar_url
            else f'<span class="mkt-seller-avatar" aria-hidden="true">{initial}</span>'
        )
        if seller_href:
            seller_block = (
                f'<a class="mkt-card-seller" href="{esc(seller_href)}">{avatar}'
                f"<span>{esc(seller_name)}</span></a>"
            )
        else:
            seller_block = (
                f'<span class="mkt-card-seller">{avatar}<span>{esc(seller_name)}</span></span>'
            )

    price_block = ""
    if price.known:
        range_class = " is-range" if price.is_range else ""
        price_block = f'<p class="mkt-card-price{range_class}">{esc(price.display)}</p>'
    # No price, no price element. The old grid printed an empty pill for an
    # unpriced listing, which reads as a price the seller set to nothing.

    stock_block = (
        f'<p class="mkt-card-stock{_stock_class(stock)}">{esc(stock)}</p>' if stock else ""
    )

    # Same rule as the price above, for the same reason. `.mkt-card-body` is a
    # flex column with a gap, so an empty `.mkt-card-meta` is not free: it is
    # still a flex item, so it consumes one gap and pushes the stock line 4px
    # down on exactly those cards whose seller has no store name. That is a
    # geometry difference decided by whether a *sibling* field is populated,
    # which is the opposite of "uniform by construction".
    meta_block = f'<div class="mkt-card-meta">{seller_block}</div>' if seller_block else ""

    # Omitting the price keeps the card honest but moves every line below it up
    # by the height of the slot it did not fill, so an unpriced listing shows its
    # seller name on the row where its neighbours show a price. The stylesheet
    # gives this class the missing height back, on the title's margin rather than
    # on an empty `<p>`, so the reserve is a layout fact and not a paragraph
    # claiming to hold a price nobody set.
    body_class = "mkt-card-body" if price.known else "mkt-card-body is-unpriced"

    # `hidden` until `pulse_marketplace.js` binds it, following Save and Report on
    # the product page: adding to the cart is a `fetch` and there is no GET or
    # POST form behind this button, so to a scriptless visitor an unhidden one
    # would be a control that visibly does nothing. The whole card is still a
    # link to the product page, where the same purchase is reachable, so nothing
    # is lost by the button being absent.
    #
    # `aria-label` carries the product name because the visible text is "Add to
    # cart" on every card, and a screen-reader user listing the page's buttons
    # would otherwise hear the same three words fifteen times with nothing to
    # tell them apart.
    cart_block = ""
    if cart is not None:
        cart_block = (
            '<div class="mkt-card-actions">'
            f'<button class="mkt-add" type="button" data-mkt-add="{int(cart.listing_id)}"'
            f' aria-label="{esc(f"{cart.label}: {title}")}" hidden>{esc(cart.label)}</button>'
            "</div>"
        )
    elif choose_options:
        # A listing with sizes or colours gets a way through to the picker rather
        # than a quick-add, because the cart API has no variant column and the
        # one-tap add would book an unnamed one. See
        # `mw.CART_HIDDEN_NEEDS_CHOICE`.
        #
        # An `<a>`, and deliberately not `hidden`: unlike the button this needs no
        # script, so hiding it until bind time would hide a working link. It is a
        # second anchor pointing where the card-wide link already points, which is
        # redundant for a mouse and not redundant otherwise — it is the only thing
        # on the card that says the product has options, and it gives the keyboard
        # an action stop in the row where every neighbouring card has one.
        choose_label = "Choose options"
        cart_block = (
            '<div class="mkt-card-actions">'
            f'<a class="mkt-add is-choose" href="{esc(product_path(listing_id))}"'
            f' data-mkt-choose="{listing_id}"'
            f' aria-label="{esc(f"{choose_label}: {title}")}">{esc(choose_label)}</a>'
            "</div>"
        )

    return (
        f'<li><article class="mkt-card">{box}'
        f'<div class="{body_class}">'
        f'<h3 class="mkt-card-title">'
        f'<a class="mkt-card-link" href="{esc(product_path(listing_id))}">{esc(title)}</a>'
        f"</h3>"
        f"{price_block}"
        f"{meta_block}"
        f"{stock_block}"
        f"{cart_block}"
        f"</div></article></li>"
    )


def product_grid(cards: Sequence[str], *, label: str = "Products") -> str:
    if not cards:
        return ""
    return (
        f'<ul class="mkt-grid" aria-label="{esc(label)}">' + "".join(cards) + "</ul>"
    )


# ---------------------------------------------------------------------------
# Category navigation
# ---------------------------------------------------------------------------


def category_nav(
    taxonomy: Sequence[mw.CategoryNode],
    filters: Filters,
    *,
    total: int,
) -> str:
    """Real departments, derived from the catalogue's own category column.

    The reference mockup's category strip is not reproduced: its departments are
    invented ("Trending", "Deals"). These come from `build_taxonomy`, which
    groups the supplier breadcrumbs on a folded key so that "Mens Clothing" and
    "Men's Clothing" are one department with the majority spelling as its label,
    and the count beside each is a real row count.

    A department's sections only appear once that department is selected. On a
    15-department catalogue, rendering every section at once would be a wall of
    chips longer than the product grid.
    """
    if not taxonomy:
        return ""
    selected_root = filters.category.split("/")[0] if filters.category else ""

    all_active = "" if filters.category else ' aria-current="true"'
    chips = [
        f'<li><a class="mkt-chip" href="{esc(filters.url(category=""))}"{all_active}>'
        f'All<span class="mkt-chip-count">{total}</span></a></li>'
    ]
    for node in taxonomy:
        active = ' aria-current="true"' if node.slug == selected_root else ""
        chips.append(
            f'<li><a class="mkt-chip" href="{esc(filters.url(category=node.slug))}"{active}>'
            f'{esc(node.label)}<span class="mkt-chip-count">{node.count}</span></a></li>'
        )
    primary = (
        '<ul class="mkt-chiprow" aria-label="Product departments">' + "".join(chips) + "</ul>"
    )

    node = next((n for n in taxonomy if n.slug == selected_root), None)
    if not node or not node.children:
        return f'<nav class="mkt-cats">{primary}</nav>'

    sub_active = ' aria-current="true"' if filters.category == node.slug else ""
    sub = [
        f'<li><a class="mkt-chip" href="{esc(filters.url(category=node.slug))}"{sub_active}>'
        f"All {esc(node.label)}</a></li>"
    ]
    for child in node.children:
        active = ' aria-current="true"' if child.slug == filters.category else ""
        sub.append(
            f'<li><a class="mkt-chip" href="{esc(filters.url(category=child.slug))}"{active}>'
            f'{esc(child.label)}<span class="mkt-chip-count">{child.count}</span></a></li>'
        )
    secondary = (
        f'<ul class="mkt-chiprow is-sub" aria-label="{esc(node.label)} sections">'
        + "".join(sub)
        + "</ul>"
    )
    return f'<nav class="mkt-cats">{primary}{secondary}</nav>'


def toolbar(filters: Filters) -> str:
    """Search and sort, both as real GET forms.

    Two separate forms, each carrying the other's state in hidden inputs, so
    submitting either preserves the full filter set and produces a URL identical
    to the one a link would have produced. With JavaScript off, both work; with
    it on, the sort form's submit button is hidden and `change` submits it.

    The sort options are the four in `marketplace_web.SORT_OPTIONS`. "Most
    popular" and "Top rated" are absent because there is no view counter and no
    review table — offering them would mean sorting by something arbitrary and
    labelling it popularity.
    """
    hidden_for_search = ""
    if filters.category:
        hidden_for_search += f'<input type="hidden" name="category" value="{esc(filters.category)}">'
    if filters.sort != mw.DEFAULT_SORT:
        hidden_for_search += f'<input type="hidden" name="sort" value="{esc(filters.sort)}">'

    search = (
        f'<form class="mkt-search" role="search" method="get" action="{esc(BASE_PATH)}">'
        f"{hidden_for_search}"
        f'<label class="mkt-sr" for="mkt-q">Search marketplace products</label>'
        f'<input id="mkt-q" type="search" name="q" value="{esc(filters.q)}"'
        f' placeholder="Search products, categories, or sellers" autocomplete="off">'
        f'<button class="mkt-ghost" type="submit">Search</button>'
        f"</form>"
    )

    hidden_for_sort = ""
    if filters.category:
        hidden_for_sort += f'<input type="hidden" name="category" value="{esc(filters.category)}">'
    if filters.q:
        hidden_for_sort += f'<input type="hidden" name="q" value="{esc(filters.q)}">'
    options = "".join(
        f'<option value="{esc(key)}"{" selected" if key == filters.sort else ""}>{esc(label)}</option>'
        for key, label in mw.SORT_OPTIONS
    )
    sort = (
        f'<form class="mkt-sort" method="get" action="{esc(BASE_PATH)}">'
        f"{hidden_for_sort}"
        f'<label class="mkt-sr" for="mkt-sort">Sort products</label>'
        f'<select id="mkt-sort" name="sort" data-mkt-sort>{options}</select>'
        f'<button class="mkt-ghost" type="submit" data-mkt-sort-submit>Apply</button>'
        f"</form>"
    )
    return f'<div class="mkt-toolbar">{search}{sort}</div>'


def applied_filters(filters: Filters, taxonomy: Sequence[mw.CategoryNode]) -> str:
    """Each active facet, with a link that removes just that one.

    The remove link is the current URL minus one parameter, so clearing a filter
    is a normal navigation with working back-button semantics.
    """
    tags: list[str] = []
    if filters.category:
        label = _category_label(filters.category, taxonomy) or filters.category
        tags.append(
            f'<a class="mkt-tag" href="{esc(filters.url(category=""))}">'
            f'{esc(label)}<span class="mkt-tag-x" aria-hidden="true">×</span>'
            f'<span class="mkt-sr">Remove category filter</span></a>'
        )
    if filters.q:
        tags.append(
            f'<a class="mkt-tag" href="{esc(filters.url(q=""))}">'
            f'“{esc(filters.q)}”<span class="mkt-tag-x" aria-hidden="true">×</span>'
            f'<span class="mkt-sr">Clear search</span></a>'
        )
    if not tags:
        return ""
    return (
        '<div class="mkt-applied"><span class="mkt-applied-label">Filtered by</span>'
        + "".join(tags)
        + f'<a class="mkt-tag" href="{esc(BASE_PATH)}">Clear all</a></div>'
    )


def _category_label(slug: str, taxonomy: Sequence[mw.CategoryNode]) -> str:
    for node in taxonomy:
        if node.slug == slug:
            return node.label
        for child in node.children:
            if child.slug == slug:
                return f"{node.label} · {child.label}"
    return ""


def pagination(page: mw.Page, filters: Filters) -> str:
    """Numbered links, because a storefront's pages must be crawlable.

    Infinite scroll would make pages 2..n unreachable to a crawler and
    unlinkable to a shopper. The window is elided with a real gap marker rather
    than rendering 40 numbers.
    """
    if page.pages <= 1:
        return ""
    parts: list[str] = []
    if page.has_prev:
        parts.append(
            f'<a href="{esc(filters.url(page=page.page - 1))}" rel="prev">Previous</a>'
        )
    else:
        parts.append('<span class="is-disabled">Previous</span>')

    window = {1, page.pages, page.page}
    window.update({page.page - 1, page.page + 1})
    numbers = sorted(n for n in window if 1 <= n <= page.pages)
    previous = 0
    for number in numbers:
        if previous and number - previous > 1:
            parts.append('<span class="mkt-pager-gap" aria-hidden="true">…</span>')
        if number == page.page:
            parts.append(f'<span aria-current="page">{number}</span>')
        else:
            parts.append(f'<a href="{esc(filters.url(page=number))}">{number}</a>')
        previous = number

    if page.has_next:
        parts.append(
            f'<a href="{esc(filters.url(page=page.page + 1))}" rel="next">Next</a>'
        )
    else:
        parts.append('<span class="is-disabled">Next</span>')
    return (
        '<nav class="mkt-pager" aria-label="Product pages">' + "".join(parts) + "</nav>"
    )


def state_block(
    heading: str, body: str, *, kind: str = "empty", actions_html: str = ""
) -> str:
    """Empty and error states, which are never the same element.

    A failed query and a genuinely empty result set must not render the same
    thing: "No products match" about a database error tells the visitor a
    falsehood about the catalogue and tells the operator nothing. `kind="error"`
    gets its own styling and its own copy.
    """
    cls = "mkt-state is-error" if kind == "error" else "mkt-state"
    actions = f'<div class="mkt-state-actions">{actions_html}</div>' if actions_html else ""
    role = ' role="alert"' if kind == "error" else ""
    return (
        f'<section class="{cls}"{role}><h2>{esc(heading)}</h2>'
        f"<p>{esc(body)}</p>{actions}</section>"
    )


def cart_link_html(cart_count: Optional[int]) -> str:
    """The cart entry point, shared by the grid and the product page.

    An ordinary link, server-rendered and not hidden: it works with JavaScript
    off, because `/pulse/cart` is a real page a GET reaches. Only the *count*
    inside it is script-updatable, and it ships with the server's own number so
    the first paint is already correct rather than blank until a fetch lands.

    The count is inside the link's accessible name rather than beside it as a
    bare number, so a screen reader announces "Your cart, 3 items" instead of
    "Cart" followed by a stray "3". `aria-hidden` on the visible pill stops it
    being read twice.

    `None` is the caller's opt-out — a route that could not read the cart — and
    yields nothing rather than a zero, because a confident "0" from a failed
    read is a lie about an order in progress.

    One function rather than one per page because `setCartCount` in
    `pulse_marketplace.js` rewrites every `[data-mkt-cart-link]` in the
    document from one response. Two hand-written copies of this markup would
    drift, and the drift would surface as a screen reader announcing a stale
    basket on whichever page was not updated.
    """
    if cart_count is None:
        return ""
    count = max(0, int(cart_count))
    pill = (
        f'<span class="mkt-cart-count" aria-hidden="true" data-mkt-cart-count>{count}</span>'
        if count
        else '<span class="mkt-cart-count" aria-hidden="true" data-mkt-cart-count hidden></span>'
    )
    if count == 1:
        label = "Your cart, 1 item"
    elif count:
        label = f"Your cart, {count} items"
    else:
        label = "Your cart, empty"
    return (
        f'<a class="mkt-cart-link" href="{esc(cart_path())}"'
        f' data-mkt-cart-link aria-label="{esc(label)}">'
        f'<span aria-hidden="true">Cart</span>{pill}</a>'
    )


# ---------------------------------------------------------------------------
# Discovery page
# ---------------------------------------------------------------------------


def render_discovery(
    *,
    listings: Sequence[Mapping[str, Any]],
    variants_by_listing: Mapping[int, Sequence[Mapping[str, Any]]],
    filters: Filters,
    viewer: Viewer,
    app_cta_html: str = "",
    merchant_html: str = "",
    origin: str = mw.PUBLIC_ORIGIN,
    load_error: bool = False,
    cart_count: Optional[int] = None,
) -> RenderedPage:
    """The category / discovery experience.

    `listings` is the *whole* eligible catalogue for this request, already
    filtered by the SQL visibility predicates. Filtering, sorting and paging
    happen here rather than in SQL for one measured reason: the eligible
    catalogue is 15 rows and the whole table is 47, so a round trip per facet
    would cost more than the work. `render_discovery` is where that trade-off
    is stated, and it is the thing to change first at catalogue scale — see the
    scalability note in the final report.

    `cart_count` is the buyer's own cart size as the *cart API* counts it, passed
    in rather than derived, because the number on this page and the number the
    cart page prints have to be the same number. `None` means the caller did not
    ask for the cart affordances at all and the header carries no cart link;
    ``0`` is a real answer and renders the link with no count beside it, because
    a badge reading "0" is noise where an absent badge is the same information.
    """
    rows: list[dict[str, Any]] = []
    for row in listings:
        item = dict(row)
        listing_id = int(item.get("id") or 0)
        variants = list(variants_by_listing.get(listing_id) or ())
        item["price"] = mw.derive_price(item, variants)
        item["_stock"] = mw.stock_line(item, variants)
        # Kept on the row because the cart affordance below needs them too, and
        # re-reading `variants_by_listing` down there would be a second lookup
        # free to be keyed differently from this one.
        item["_variants"] = variants
        rows.append(item)

    taxonomy = mw.build_taxonomy([r.get("category") for r in rows])
    total_all = len(rows)

    # Does the requested category correspond to a real node? A crafted or stale
    # slug is not an error — it is a legitimately empty department — but it must
    # not be indexable, or every junk `?category=` value becomes a thin page in
    # the index. Checked against the taxonomy rather than against the result
    # count, so a real-but-currently-empty department still stays indexable.
    known_category = not filters.category
    for node in taxonomy:
        if node.slug == filters.category or any(
            child.slug == filters.category for child in node.children
        ):
            known_category = True
            break

    if filters.category:
        rows = [r for r in rows if mw.category_matches(r.get("category"), filters.category)]
    if filters.q:
        rows = [r for r in rows if mw.matches_query(r, filters.q)]

    ordered = mw.sort_products(rows, filters.sort)
    page = mw.paginate(ordered, filters.page)

    # Badges are classified for the page, then suppressed where they would be
    # uninformative. A "New" badge on most of a page is a fact about the
    # importer's run date, not about the products.
    badge_map = {int(r.get("id") or 0): mw.classify_badges(r) for r in page.items}
    badge_map = mw.suppress_uninformative_badges(badge_map)

    # Derived per row rather than once for the page, because three of the four
    # reasons to withhold the button are properties of the individual listing.
    # `cart_count is None` is the caller's opt-out and suppresses every button,
    # which is what keeps a call site that never wired up the cart API from
    # sprouting controls that post to it.
    cards = []
    for index, row in enumerate(page.items):
        affordance, hidden_reason = (None, "")
        if cart_count is not None:
            affordance, hidden_reason = mw.cart_affordance(
                row,
                price=row["price"],
                signed_in=viewer.signed_in,
                viewer_user_id=viewer.user_id,
                variants=row.get("_variants") or (),
            )
        cards.append(product_card(
            row,
            price=row["price"],
            badges=badge_map.get(int(row.get("id") or 0), ()),
            stock=row.get("_stock") or "",
            # Only the first card is eager: it is the largest contentful paint
            # on this page and the only image reliably above the fold.
            eager=(index == 0),
            cart=affordance,
            # The one withheld reason that still renders something. The other
            # four mean "this cannot be bought"; this one means "not in one tap",
            # and sending the buyer to the picker is the correct answer to it.
            choose_options=(hidden_reason == mw.CART_HIDDEN_NEEDS_CHOICE),
        ))

    if load_error:
        body_main = state_block(
            "We could not load the catalogue",
            "Something went wrong on our side, not with your filters. "
            "Reload to try again.",
            kind="error",
            actions_html=f'<a class="mkt-ghost" href="{esc(filters.url())}">Reload</a>',
        )
    elif cards:
        body_main = product_grid(cards, label="Marketplace products")
    elif filters.q or filters.category:
        body_main = state_block(
            "No products match those filters",
            "Try a different department, or clear the filters to see the whole catalogue.",
            actions_html=f'<a class="mkt-ghost" href="{esc(BASE_PATH)}">Clear filters</a>',
        )
    else:
        body_main = state_block(
            "No products are listed yet",
            "Approved sellers can publish products to the PulseSoc Marketplace.",
        )

    heading = "Marketplace"
    if filters.category:
        heading = _category_label(filters.category, taxonomy) or "Marketplace"

    # The count is a real count of what this page's filters match.
    if load_error:
        count_line = ""
    elif page.total == 1:
        count_line = "1 product"
    else:
        count_line = f"{page.total} products"
    if filters.q and not load_error:
        count_line += f" matching “{filters.q}”"

    crumbs_html = ""
    if filters.category:
        crumbs = [("Marketplace", "")]
        label = _category_label(filters.category, taxonomy)
        if label:
            crumbs.append((label, filters.category))
        crumbs_html = _crumbs_html(crumbs, filters)

    cart_html = cart_link_html(cart_count)

    head = (
        f'<header class="mkt-head">{crumbs_html}'
        f'<div class="mkt-head-row"><h1 class="mkt-title">{esc(heading)}</h1>'
        f"{cart_html}</div>"
        + (
            f'<p class="mkt-subtitle"><span class="mkt-count">{esc(count_line)}</span></p>'
            if count_line
            else ""
        )
        + "</header>"
    )

    body = (
        '<div class="mkt">'
        f"{head}"
        f"{app_cta_html}"
        f"{category_nav(taxonomy, filters, total=total_all)}"
        f"{toolbar(filters)}"
        f"{applied_filters(filters, taxonomy)}"
        f'<section class="mkt-section">{body_main}</section>'
        f"{pagination(page, filters)}"
        f"{merchant_html}"
        '<div class="mkt-sr" role="status" aria-live="polite" id="mkt-live"></div>'
        "</div>"
    )

    # Accumulated rather than written as one conditional expression: the earlier
    # ternary form bound `if count_line` over the whole concatenation, so an
    # empty catalogue silently dropped the category name from the description.
    description = "Browse products from PulseSoc sellers"
    if filters.category:
        description += f" in {heading}"
    description += f". {count_line}." if count_line else "."

    # Three things make a discovery URL not worth indexing, and all three get
    # `noindex,follow` so a crawler still walks through to the products:
    #   * a search-results URL — unbounded, user-generated, thin by definition;
    #   * page 2 and beyond — a duplicate permutation of a canonical page 1;
    #   * a category slug no real listing carries — otherwise any invented
    #     `?category=` value mints an indexable empty page.
    # The canonical for all three is the department's own page 1, which the
    # category filter alone already produces.
    # ...and a fourth, which is not about the URL but about this reply: a failed
    # catalogue read. The route answers 503 for it, but the status alone is not
    # enough — a crawler that indexes anyway would record "We could not load the
    # catalogue" as the Marketplace's contents, and the department page would rank
    # for its own error message. The URL is still worth crawling later, so this
    # stays `noindex,follow` like the other three rather than becoming `nofollow`.
    #
    # ...and a fifth: a catalogue with nothing in it at all. A URL that answers
    # 200 with no products on it is the soft-404 pattern Google names, and on a
    # new deployment it is a real state rather than a hypothetical one. Keyed on
    # `total_all`, the size of the whole eligible catalogue, and deliberately not
    # on the post-filter result count — an existing department that happens to be
    # out of stock today is a real page that should keep its ranking, which is
    # the distinction the `known_category` check above already draws.
    indexable = (
        not filters.q
        and filters.page == 1
        and known_category
        and not load_error
        and total_all > 0
    )
    robots_extra = "" if indexable else "noindex,follow"

    return RenderedPage(
        title=(f"{heading} · PulseSoc Marketplace" if filters.category else "PulseSoc Marketplace"),
        meta_description=description[:300],
        body_html=body,
        canonical_path=BASE_PATH
        + mw.build_query_string({"category": filters.category if known_category else ""}),
        indexable=indexable,
        robots_extra=robots_extra,
        jsonld=tuple(
            item
            for item in (
                mw.breadcrumb_jsonld(
                    [(heading, filters.category)] if filters.category else [], origin
                ),
            )
            if item
        ),
        assets_html=assets_html(),
        listed_ids=tuple(int(row.get("id") or 0) for row in page.items),
    )


def _crumbs_html(crumbs: Sequence[tuple[str, str]], filters: Filters) -> str:
    items: list[str] = []
    last = len(crumbs) - 1
    for index, (label, slug) in enumerate(crumbs):
        if index == last:
            items.append(f'<li aria-current="page">{esc(label)}</li>')
        else:
            href = filters.url(category=slug) if slug else BASE_PATH
            items.append(f'<li><a href="{esc(href)}">{esc(label)}</a></li>')
    return f'<nav aria-label="Breadcrumb"><ol class="mkt-crumbs">{"".join(items)}</ol></nav>'


def assets_html() -> str:
    return (
        f'<link rel="stylesheet" href="{CSS_HREF}">'
        f'<script src="{JS_SRC}" defer></script>'
    )


# ---------------------------------------------------------------------------
# Product detail page
# ---------------------------------------------------------------------------


def gallery_html(media: Sequence[mw.MediaItem], *, title: str) -> str:
    """Keyboard-accessible gallery that degrades to one image.

    Implemented as a `tablist` of thumbnails over `tabpanel` slides, which is
    the pattern assistive technology already understands, and which the page
    script enhances with arrow-key traversal. With JavaScript disabled the
    thumbnails are anchors to each slide's id and the first slide is visible, so
    the gallery is still a gallery.

    In this catalogue every listing currently resolves to exactly one image, so
    the rail is genuinely absent rather than padded with repeats of the same
    photograph — which is what the reference's seven-thumbnail strip would have
    required.
    """
    if not media:
        return f'<div class="mkt-gallery">{media_box(None, alt=title)}</div>'
    if len(media) == 1:
        return (
            f'<div class="mkt-gallery"><div class="mkt-gallery-stage">'
            f"{media_box(media[0], alt=title, eager=True)}"
            f"</div></div>"
        )

    slides: list[str] = []
    thumbs: list[str] = []
    for index, item in enumerate(media):
        hidden = "" if index == 0 else " hidden"
        label = f"{title} — image {index + 1} of {len(media)}"
        slides.append(
            f'<div class="mkt-gallery-slide" role="tabpanel" id="mkt-slide-{index}"'
            f' aria-labelledby="mkt-thumb-{index}"{hidden}>'
            f"{media_box(item, alt=label, eager=(index == 0))}</div>"
        )
        selected = "true" if index == 0 else "false"
        tabindex = "0" if index == 0 else "-1"
        active = " is-active" if index == 0 else ""
        thumb_item = mw.MediaItem(url=item.poster or item.url, kind="image")
        thumbs.append(
            # `role="presentation"` on the wrapper because a `tablist` has to
            # own its `tab` children directly. The `<li>` is here so the rail
            # is a real list in markup, but left unmarked it lands between the
            # two as a generic element and breaks that relationship -- the
            # thumbs are then announced without their position in the set.
            f'<li role="presentation">'
            f'<a class="mkt-gallery-thumb{active}" role="tab" id="mkt-thumb-{index}"'
            f' href="#mkt-slide-{index}" aria-controls="mkt-slide-{index}"'
            f' aria-selected="{selected}" tabindex="{tabindex}">'
            f"{media_box(thumb_item, alt=f'Show image {index + 1}')}</a></li>"
        )

    return (
        f'<div class="mkt-gallery" data-mkt-gallery>'
        f'<div class="mkt-gallery-stage">{"".join(slides)}</div>'
        f'<div class="mkt-gallery-nav">'
        f'<button class="mkt-ghost" type="button" data-mkt-gallery-prev hidden>‹'
        f'<span class="mkt-sr">Previous image</span></button>'
        f'<span class="mkt-gallery-counter" data-mkt-gallery-counter>1 / {len(media)}</span>'
        f'<button class="mkt-ghost" type="button" data-mkt-gallery-next hidden>›'
        f'<span class="mkt-sr">Next image</span></button>'
        f"</div>"
        f'<ul class="mkt-gallery-rail" role="tablist" aria-label="{esc(title)} images">'
        f'{"".join(thumbs)}</ul>'
        f"</div>"
    )


def option_name(group: mw.OptionGroup) -> str:
    """The query-string parameter one option group round-trips through."""
    return f"opt_{mw.slugify(group.key) or 'option'}"


def resolve_variant(
    views: Sequence[mw.VariantView], chosen: Mapping[str, str]
) -> Optional[mw.VariantView]:
    """The variant a selection names, or ``None`` when the selection is partial.

    Server-side twin of the browser's resolver, so the page a no-JS visitor gets
    after submitting the option form shows the same price the scripted page
    would have shown. Returns ``None`` rather than guessing a default variant:
    picking one for the shopper is how a person ends up enquiring about a size
    they did not choose.
    """
    if not views:
        return None
    for view in views:
        options = dict(view.options)
        if not options:
            continue
        if all(chosen.get(name) == value for name, value in options.items()):
            return view
    return None


def options_html(
    groups: Sequence[mw.OptionGroup],
    selected: Mapping[str, str],
    *,
    offered: Mapping[str, set[str]] | None = None,
) -> str:
    """Real variant controls over real rows.

    Each group is a `fieldset`/`legend` so the group name is announced with
    every option, and each option is a genuine radio input, which gives keyboard
    users native arrow-key traversal within the group for free.

    Colour swatches only render a colour when `marketplace_web._swatch_for`
    knows the name. 69 of the catalogue's 70 distinct colour values resolve; the
    one that does not is literally "Colorful", which correctly gets a text
    option instead of a misleading single dot.
    """
    if not groups:
        return ""
    blocks: list[str] = []
    for group in groups:
        field_name = option_name(group)
        chosen = selected.get(group.key, "")
        allowed = offered.get(group.key) if offered else None
        entries: list[str] = []
        for index, option in enumerate(group.options):
            input_id = f"mkt-{mw.slugify(group.key)}-{index}"
            checked = " checked" if option.value == chosen else ""
            disabled = (
                " disabled"
                if allowed is not None and option.value not in allowed
                else ""
            )
            swatch = (
                f'<span class="mkt-swatch" style="background:{esc(option.swatch)}" aria-hidden="true"></span>'
                if option.swatch
                else ""
            )
            color_class = " is-color" if option.swatch else ""
            entries.append(
                f'<span class="mkt-option{color_class}">'
                f'<input type="radio" id="{esc(input_id)}" name="{esc(field_name)}"'
                f' data-mkt-option="{esc(group.key)}"'
                f' value="{esc(option.value)}"{checked}{disabled}>'
                f'<label for="{esc(input_id)}">{swatch}{esc(option.label)}</label>'
                f"</span>"
            )
        chosen_label = (
            f'<span class="mkt-optiongroup-chosen">: {esc(chosen)}</span>' if chosen else ""
        )
        blocks.append(
            f'<fieldset class="mkt-optiongroup">'
            f"<legend>{esc(group.label)}{chosen_label}</legend>"
            f'<div class="mkt-optionlist">{"".join(entries)}</div>'
            f"</fieldset>"
        )
    return f'<div class="mkt-options">{"".join(blocks)}</div>'


def spec_table(attributes: Sequence[tuple[str, str]]) -> str:
    """The supplier's own packed attribute run, re-presented as a table.

    Nothing here is authored: `parse_description` recovers `Key: Value` pairs
    from an undelimited supplier string, so every row is a re-presentation of
    words the seller's feed supplied. 38 of 47 live listings yield a table.
    """
    if not attributes:
        return ""
    rows = "".join(
        f"<dt>{esc(key)}</dt><dd>{esc(value)}</dd>" for key, value in attributes
    )
    return (
        '<section class="mkt-panel"><h2>Specifications</h2>'
        f'<dl class="mkt-specs">{rows}</dl></section>'
    )


def notes_html(notes: Sequence[str]) -> str:
    """Supplier caveats, demoted behind a disclosure but not discarded.

    These are real statements (measurement tolerances, colour-variance
    warnings). They are also machine-translated boilerplate that would crowd
    out the canonical product facts if promoted, so they sit in a closed
    `<details>` — present for anyone who wants them, and in the DOM for a
    crawler, but not competing with the price.
    """
    if not notes:
        return ""
    items = "".join(f"<li>{esc(note)}</li>" for note in notes)
    return (
        '<details class="mkt-notes"><summary>Seller notes and measurement details</summary>'
        f'<ul class="mkt-notes-body">{items}</ul></details>'
    )


def description_html(description: mw.DescriptionView) -> str:
    """Prose, clamped with a real disclosure rather than a fade.

    A gradient fade over clipped text hides content from keyboard users and
    leaves a crawler reading text no human can reach. The clamp is removable
    and the control is only rendered when the text is actually clipped (the page
    script measures it).
    """
    text = description.summary or description.body
    if not text:
        return ""
    return (
        '<section class="mkt-panel"><h2>About this product</h2>'
        f'<p class="mkt-prose mkt-clamp" id="mkt-desc">{esc(text)}</p>'
        '<button class="mkt-ghost" type="button" data-mkt-clamp-toggle="mkt-desc"'
        ' aria-expanded="false" aria-controls="mkt-desc" hidden>Show more</button>'
        "</section>"
    )


def seller_card(
    row: Mapping[str, Any],
    *,
    viewer: Viewer,
    store_href: str = "",
    listing_count: int = 0,
) -> str:
    """Visible seller identity, with only the facts the tables hold.

    No rating, no response time, no "Trusted Seller", no join date: there is no
    review table, no response-time metric and no verified-merchant tier that a
    buyer-facing claim could be drawn from. The seller's store name, their
    handle, and how many other products they have listed are real, and that is
    what appears.
    """
    name = mw._clean(row.get("seller_store_name")) or "PulseSoc seller"
    username = mw._clean(row.get("seller_username"))
    seller_id = int(row.get("seller_user_id") or 0)
    initial = esc(name[:1].upper())
    avatar_url = mw._clean(row.get("seller_avatar_url"))
    figure = (
        f'<figure class="mkt-seller-figure"><img src="{esc(avatar_url)}" alt="" loading="lazy" decoding="async"></figure>'
        if avatar_url
        else f'<figure class="mkt-seller-figure" aria-hidden="true">{initial}</figure>'
    )
    # The store name links to the seller's profile, but only for a signed-in
    # viewer. `/pulse/u/<username>` redirects an anonymous visitor to /login, so
    # linking it on the public copy of the page would hand a crawler — and the
    # visitor who most needs to see products — a login wall instead of a
    # destination. Plain text is the honest treatment when there is nowhere
    # this viewer can actually go.
    href = store_href or (f"/pulse/u/{mw.url_quote(username)}" if username and viewer.signed_in else "")
    name_html = (
        f'<a class="mkt-seller-name" href="{esc(href)}">{esc(name)}</a>'
        if href
        else f'<span class="mkt-seller-name">{esc(name)}</span>'
    )
    meta_bits: list[str] = []
    if username:
        meta_bits.append(f"@{username}")
    if listing_count > 1:
        meta_bits.append(f"{listing_count} products listed")
    meta = (
        f'<p class="mkt-seller-meta">{esc(" · ".join(meta_bits))}</p>' if meta_bits else ""
    )

    # Deliberately no action here. "Message seller" already exists as the primary
    # control in the purchase block above, as a working anchor; repeating it as a
    # JavaScript-only button gave the document two controls with the same
    # accessible name, one of which did nothing without JavaScript. And a second
    # "Sign in" prompt for an anonymous visitor adds nothing to the one attached
    # to the price. The seller card states identity; the action row owns actions.
    actions = ""

    return (
        '<section class="mkt-panel"><h2>Seller</h2>'
        f'<div class="mkt-seller">{figure}<div>{name_html}{meta}</div></div>'
        f"{actions}</section>"
    )


#: Kinds that cannot be delivered over a wire, and the ones that can. Only used
#: to decide whether `delivery_type` contradicts the product it belongs to — not
#: to infer a delivery method the seller never stated.
_PHYSICAL_KINDS = frozenset({"physical", "shipped", "shipping", "pickup"})
_DIGITAL_KINDS = frozenset({"digital", "download", "course", "file"})


def fulfilment_html(row: Mapping[str, Any]) -> str:
    """Delivery, returns and payment — only where a column answers.

    `estimated_delivery` is empty on every production row, so no delivery window
    is ever printed. `product_type` and `refund_policy` are printed verbatim when
    set and omitted entirely when not. The payment line is the platform's own
    policy string, not copy written here.

    `delivery_type` is the exception, because it cannot be read at face value.
    `bot.py` declares it `TEXT DEFAULT 'digital'`, so a row where the seller
    never answered the question is indistinguishable from one where they
    answered "digital" — and the overwhelmingly common case is the former. Taken
    verbatim it told a buyer that a physical product was delivered digitally,
    directly under a "Product type: physical" line this same table had just
    printed.

    So it is printed only when it does not contradict the type. The precedence
    is the one the rest of the codebase already applies to this column: a
    listing's kind is `listing_type or product_type`, and `delivery_type` is the
    last resort rather than the authority. Where the two disagree, the column
    default is the likelier explanation than a seller who shipped an aerosol by
    download, and an omitted row is the honest rendering of an unanswered
    question. Where they agree, or where no type was set at all, it prints.
    """
    facts: list[tuple[str, str]] = []
    product_type = mw._clean(row.get("product_type"))
    delivery = mw._clean(row.get("delivery_type"))
    refund = mw._clean(row.get("refund_policy"))
    estimated = mw._clean(row.get("estimated_delivery"))
    if product_type:
        facts.append(("Product type", product_type))
    kind = (mw._clean(row.get("listing_type")) or product_type).lower()
    contradicted = kind in _PHYSICAL_KINDS and delivery.lower() in _DIGITAL_KINDS
    if delivery and not contradicted:
        facts.append(("Delivery", delivery))
    if estimated:
        facts.append(("Estimated delivery", estimated))
    if refund:
        facts.append(("Returns", refund))

    policy = payment_policy_line()
    if not facts and not policy:
        return ""
    rows = "".join(
        f"<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>" for label, value in facts
    )
    table = f'<dl class="mkt-facts">{rows}</dl>' if rows else ""
    policy_html = f'<p class="mkt-subtitle">{esc(policy)}</p>' if policy else ""
    return (
        '<section class="mkt-panel"><h2>Delivery and payment</h2>'
        f"{table}{policy_html}</section>"
    )


def render_unavailable(*, canonical_path: str = BASE_PATH) -> RenderedPage:
    """The page a route serves when it could not read the product at all.

    This is the one state the product route had no rendering for, and its absence
    was a real defect rather than a cosmetic one. A failed read used to escape as
    an unhandled exception, so the canonical URL of a live product answered 500
    with a stack-trace page as the product's contents.

    404 would be worse than the 500, not better: a 404 on a canonical URL retires
    it from the search index, which is the correct answer for a delisted product
    and precisely the wrong one for a database hiccup. So the route answers 503
    and serves this, and the distinction between "gone" and "not right now" —
    which is the whole reason both statuses exist — survives.

    `indexable=False` with no JSON-LD is deliberate on top of the 503. A crawler
    that ignores the status must still not be handed an empty `Product` object;
    the absence of structured data is what stops this page from being recorded as
    a product with no price. `canonical_path` is still the product's own URL, so
    a crawler that keeps the URL keeps the right one.
    """

    body = (
        '<div class="mkt">'
        '<header class="mkt-head"><h1 class="mkt-title">Marketplace</h1></header>'
        + state_block(
            "We could not load this product",
            "Something went wrong on our side. The product has not been removed — "
            "reload the page to try again.",
            kind="error",
            actions_html=(
                f'<a class="mkt-ghost" href="{esc(canonical_path)}">Reload</a>'
                f'<a class="mkt-ghost" href="{esc(BASE_PATH)}">Back to Marketplace</a>'
            ),
        )
        + "</div>"
    )
    return RenderedPage(
        title="Product temporarily unavailable · PulseSoc Marketplace",
        meta_description=(
            "This PulseSoc Marketplace product could not be loaded right now."
        ),
        body_html=body,
        canonical_path=canonical_path,
        indexable=False,
        assets_html=assets_html(),
    )


def render_product(
    *,
    listing: Mapping[str, Any],
    variants: Sequence[Mapping[str, Any]],
    related: Sequence[Mapping[str, Any]] = (),
    related_variants: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
    selected_options: Mapping[str, str] | None = None,
    viewer: Viewer = Viewer(),
    app_cta_html: str = "",
    promote_html: str = "",
    delivery_html: str = "",
    store_href: str = "",
    seller_listing_count: int = 0,
    cart_count: Optional[int] = None,
    origin: str = mw.PUBLIC_ORIGIN,
) -> RenderedPage:
    """The product detail experience.

    `cart_count` follows `render_discovery`: `None` is the caller's opt-out and
    suppresses the add-to-cart control entirely, which is what a route that
    could not read the cart must pass. Otherwise `mw.cart_affordance` decides,
    and this page obeys it without adding a judgement of its own.

    The grid sends a listing with options here labelled "Choose options", and the
    picker below is the point of that trip. What this page adds is the *answer*:
    `mw.cart_affordance` is given the resolved `chosen_variant`, so the quick-add
    the grid could not offer becomes available here the moment the buyer has
    actually picked a combination — and the cart line records that combination,
    because `marketplace_cart_items` now carries a `variant_id` and the route
    snapshots the variant's own `price_cents`.

    While the picker is incomplete the control is rendered *disabled* rather than
    omitted, so the script has something to enable as the selection resolves. See
    the comment at the call site for why omitting it would leave a scripted page
    permanently without a button.

    `delivery_html` arrives rendered, for the same reason `app_cta_html` does:
    this module stays unable to hold a second opinion about what a delivery
    window says. `services.delivery.web.html` is the only renderer of that markup
    on either web surface, and the route is the only thing that decides whether
    the estimate may be resolved from the reader at all -- which is a property of
    the response's cacheability, not of the page. Passing the estimate itself
    here would move both of those decisions into a renderer that is also used to
    build the shared-cached grid.
    """
    listing_id = int(listing.get("id") or 0)
    title = mw._clean(listing.get("title")) or "Marketplace listing"
    canonical = product_path(listing_id)

    price = mw.derive_price(listing, variants)
    description = mw.parse_description(listing.get("description"))
    media = mw.gallery_items(listing)
    groups = mw.build_option_groups(variants)
    views = mw.build_variant_views(variants, price.currency)

    # Which option values the URL named. Only values that really exist in a
    # variant row are honoured, so a crafted query cannot inject a phantom
    # option into the page or into the enquiry that follows it.
    selected: dict[str, str] = {}
    incoming = dict(selected_options or {})
    for group in groups:
        wanted = mw._clean(incoming.get(option_name(group)))
        if wanted and any(option.value == wanted for option in group.options):
            selected[group.key] = wanted

    # Combinations reachable given the rest of the current selection. Rendering
    # an unreachable option as disabled beats hiding it: controls that vanish as
    # you choose are far more confusing than struck-through ones.
    offered: dict[str, set[str]] = {}
    for group in groups:
        reachable: set[str] = set()
        for view in views:
            options = dict(view.options)
            if all(
                options.get(name) == value
                for name, value in selected.items()
                if name != group.key
            ):
                value = options.get(group.key)
                if value:
                    reachable.add(value)
        offered[group.key] = reachable

    chosen_variant = resolve_variant(views, selected)

    # The price the page shows: the chosen variant's if one is fully chosen,
    # otherwise the listing's range. Never a fabricated midpoint.
    if chosen_variant is not None and chosen_variant.price_cents is not None:
        price_display = mw.format_money(chosen_variant.price_cents, chosen_variant.currency)
    else:
        price_display = price.display

    stock_text = (
        chosen_variant.stock_label
        if chosen_variant is not None
        else mw.stock_line(listing, variants)
    )
    in_stock: Optional[bool] = None
    if chosen_variant is not None:
        in_stock = chosen_variant.available
    elif views:
        in_stock = any(view.available for view in views)
    elif stock_text:
        in_stock = "out of stock" not in stock_text.lower()
    else:
        # `stock_text` is empty for a course, a download or any other type with
        # nothing to count, and that silence is right on the page -- "In stock"
        # against a course tells a reader nothing. It is not right in the
        # `Offer`, where `availability` is a required property and its absence
        # reads as unknown rather than as not-applicable. So the structured
        # claim falls back to the lifecycle rule `public_sql` already filtered
        # this row on, rather than to the sentence a human was shown.
        in_stock = marketplace_listing_lifecycle.inventory_available(listing)

    badges = mw.classify_badges(listing)
    crumbs = mw.category_crumbs(listing.get("category"))

    # --- breadcrumbs -----------------------------------------------------
    crumb_items = [f'<li><a href="{esc(BASE_PATH)}">Marketplace</a></li>']
    for label, slug in crumbs:
        crumb_items.append(
            f'<li><a href="{esc(BASE_PATH)}?category={esc(slug)}">{esc(label)}</a></li>'
        )
    crumb_items.append(f'<li aria-current="page">{esc(title)}</li>')
    crumbs_html = (
        '<nav aria-label="Breadcrumb"><ol class="mkt-crumbs">'
        + "".join(crumb_items)
        + "</ol></nav>"
    )

    # --- buy panel -------------------------------------------------------
    price_block = ""
    if price_display:
        note = ""
        if price.is_range and chosen_variant is None:
            note = '<span class="mkt-price-note">Price varies by option</span>'
        price_block = (
            f'<div class="mkt-price"><span class="mkt-price-value" data-mkt-price>'
            f"{esc(price_display)}</span>{note}</div>"
        )

    stock_hidden = "" if stock_text else " hidden"
    stock_out = " is-out" if stock_text and "out of stock" in stock_text.lower() else ""
    stock_block = (
        f'<p class="mkt-stock{stock_out}" data-mkt-stock{stock_hidden}>{esc(stock_text)}</p>'
    )

    # The client-side variant table. `price` in each entry is the string this
    # server formatted — the browser selects between server-authored strings and
    # never computes a price. `key` is the opaque `variant_key` a future
    # variant-aware checkout resolves, so no amount ever travels in the form.
    variant_payload = json.dumps([view.as_client_dict() for view in views], sort_keys=True)

    # Add to cart gets its own row above the rest, not a cell in `.mkt-actions`.
    # That row is a two-column grid built for one primary plus one ghost, and
    # this is a second primary; sharing the row would either squeeze Message
    # seller to half width or push Save onto a line of its own. The panel is
    # itself a gapped grid, so a sibling div needs no margin of its own.
    #
    # `.mkt-cta` and not the grid card's `.mkt-add`: the class carries a 12px
    # font and a translucent fill sized for a tile, and `.mkt-add[data-mkt-added]`
    # would paint the label mint on the mint gradient. The script binds on the
    # `data-mkt-add` attribute, so the class is free to differ.
    #
    # `hidden` until that script runs, for the same reason the grid's button is:
    # the add is a `fetch` with no form behind it, so an unhidden one would be a
    # control a scriptless visitor could press and get nothing from. Unlike the
    # grid, there is no card-wide link underneath it to swallow the click.
    buy_action_html = ""
    if cart_count is not None:
        affordance, hidden_reason = mw.cart_affordance(
            listing,
            price=price,
            signed_in=viewer.signed_in,
            viewer_user_id=viewer.user_id,
            variants=variants,
            # The selection this page resolved, which is the whole difference
            # between this call and the grid's. A card cannot answer the options
            # question and is refused; this page *is* the picker, so once the
            # buyer has picked it hands the answer over and the button appears.
            # `None` when the picker is incomplete, which keeps the refusal.
            chosen_variant=chosen_variant,
        )
        # Rendered disabled, not omitted, when the *only* thing missing is a
        # choice. `needs_choice` is returned last, after every other refusal has
        # passed, so reaching it means this listing is addable and this buyer may
        # add it — the single open question is one the page's own picker answers.
        #
        # Omitting the button instead would make the scripted page unable to ever
        # show one: the server renders from the options in the URL, the buyer
        # changes radios without a round trip, and there would be no element for
        # the script to enable. A disabled control that becomes live as you choose
        # is also the honest rendering of the state — it says "there is a way to
        # buy this, and you are not finished" rather than leaving the buyer to
        # wonder whether the product is purchasable at all.
        needs_choice = hidden_reason == mw.CART_HIDDEN_NEEDS_CHOICE
        if affordance is not None or needs_choice:
            variant_attr = int(affordance.variant_id) if affordance is not None else 0
            label = affordance.label if affordance is not None else "Add to cart"
            disabled = "" if affordance is not None else " disabled"
            buy_action_html = (
                '<div class="mkt-actions-buy">'
                f'<button class="mkt-cta" type="button"'
                f' data-mkt-add="{int(listing_id)}"'
                # Always present, `0` for a listing with nothing to choose. The
                # script reads it verbatim and rewrites it as the picker resolves,
                # so the field posted is the field shown.
                f' data-mkt-variant="{variant_attr}"'
                f"{disabled} hidden>{esc(label)}</button>"
                "</div>"
            )

    actions: list[str] = []
    seller_id = int(listing.get("seller_user_id") or 0)
    seller_username = mw._clean(listing.get("seller_username"))

    # One filled action per panel. Message seller is the primary when it is the
    # only way to transact, and steps down to a ghost when Add to cart is
    # present — two mint gradients stacked would leave the buyer to guess which
    # one buys the thing, and the answer is never "message".
    #
    # "Present" includes the disabled add above, which is deliberate: buying is a
    # way to transact with this listing, the buyer is simply one radio away from
    # it, and promoting Message seller would tell them the opposite. The disabled
    # `.mkt-cta` is painted grey by the stylesheet, so the page carries no mint
    # gradient at all until the picker resolves — which is the honest state, and
    # the one that makes the button lighting up mean something.
    contact_class = "mkt-ghost" if buy_action_html else "mkt-cta"
    if viewer.signed_in and seller_id and not viewer.owns(seller_id):
        # An anchor, not a button, and for a reason. `/pulse/messages/new?q=` is
        # a real page that finds this seller by username, so the primary action
        # on the page still works with JavaScript disabled — it just takes an
        # extra click. The script intercepts it and calls
        # `/api/pulse/messages/start`, which opens the conversation directly.
        # When the seller has no username there is nothing to search for, so the
        # control falls back to a button the script must enable.
        if seller_username:
            href = f"/pulse/messages/new?q={mw.url_quote(seller_username)}"
            actions.append(
                f'<a class="{contact_class}" href="{esc(href)}" data-mkt-contact="{seller_id}">'
                f"Message seller</a>"
            )
        else:
            actions.append(
                f'<button class="{contact_class}" type="button" data-mkt-contact="{seller_id}"'
                f" hidden>Message seller</button>"
            )
    elif not viewer.signed_in:
        # The verb has to be the one the next page actually offers. Signing in
        # lands on this same renderer with `data-mkt-add`, which adds to a cart
        # -- it does not complete a purchase -- so "Sign in to buy" would be a
        # promise broken *after* the reader had made an account. This branch
        # was unreachable while anonymous readers got their own template, and
        # that template had already been corrected to this wording.
        actions.append(
            f'<a class="mkt-cta" href="/login?next={esc(canonical)}">'
            f"Sign in to add to cart</a>"
        )
    elif viewer.owns(seller_id):
        actions.append(
            '<span class="mkt-cta" aria-disabled="true">This is your listing</span>'
        )
    # Save and Report are for *other people's* listings. Offering a seller a
    # button to report themselves is not a feature, and saving your own product
    # to your own wishlist is noise; both are omitted for the owner rather than
    # rendered and then rejected by the API.
    is_own_listing = viewer.signed_in and bool(seller_id) and viewer.owns(seller_id)
    if viewer.signed_in and not is_own_listing:
        # `hidden` until the page script binds it. Save and Report are
        # fetch-only — there is no GET or POST form behind them — so without
        # JavaScript the button would be a control that visibly does nothing.
        # The script removes `hidden` as it binds each one, which is the same
        # pattern the gallery arrows use.
        actions.append(
            f'<button class="mkt-ghost" type="button" data-mkt-save="{listing_id}"'
            f' aria-pressed="false" hidden>Save</button>'
        )
    actions_html = f'<div class="mkt-actions">{"".join(actions)}</div>' if actions else ""

    secondary: list[str] = []
    if viewer.signed_in and not is_own_listing:
        secondary.append(
            f'<button class="mkt-ghost is-danger" type="button" data-mkt-report="{listing_id}"'
            f" hidden>Report listing</button>"
        )
    secondary_html = (
        f'<div class="mkt-secondary-actions">{"".join(secondary)}</div>' if secondary else ""
    )

    note_block = ""
    if groups and chosen_variant is None:
        note_block = (
            '<p class="mkt-price-note" data-mkt-variant-note>'
            "Choose an option to see its exact price and availability.</p>"
        )

    # Variant selection is a real GET form, so it works with JavaScript
    # disabled: the server re-renders with the chosen combination in the URL,
    # which also makes a chosen variant shareable. The page script hides the
    # submit button and updates the price in place instead.
    #
    # The actions sit *outside* that form, deliberately. Two reasons: none of
    # them submits the variant selection, and `promote_html` is supplied by the
    # route — if it ever contains a form of its own, nesting it here would be
    # invalid HTML and the browser would drop the inner form silently.
    #
    # `delivery_html` goes directly under the price, inside the form, matching
    # the anonymous page and the app's PDP. Inside is safe *because* the country
    # picker it may contain is `<select data-delivery-country>` with no `name`:
    # an unnamed control is not submitted, so pressing "Update selection" cannot
    # carry a destination into the canonical URL and make one reader's corridor a
    # shareable link. If that select ever grows a `name`, move this outside the
    # form rather than stripping the attribute.
    variant_form = (
        f'<form class="mkt-panel-form" method="get" action="{esc(canonical)}"'
        f" data-mkt-variants='{esc(variant_payload)}'>"
        f"{price_block}{stock_block}{delivery_html}"
        f"{options_html(groups, selected, offered=offered)}"
        f"{note_block}"
        + (
            '<button class="mkt-ghost" type="submit" data-mkt-variant-submit>'
            "Update selection</button>"
            if groups
            else ""
        )
        + "</form>"
    )
    buy_panel = (
        '<section class="mkt-panel" aria-label="Purchase options">'
        f"{variant_form}{buy_action_html}{actions_html}{secondary_html}{promote_html}"
        "</section>"
    )

    badge_row = ""
    if badges:
        badge_row = (
            '<div class="mkt-applied">'
            + "".join(
                f'<span class="mkt-badge {_BADGE_CLASS.get(b.key, "is-quiet")}">{esc(b.label)}</span>'
                for b in badges
            )
            + "</div>"
        )

    # The same cart link the grid carries. Without it an add on this page is a
    # dead end: the buyer gets "In cart" and no way to reach what they added,
    # because the shell around this body has no commerce chrome of its own.
    info = (
        '<div class="mkt-detail-info">'
        f'<header class="mkt-head">'
        f'<div class="mkt-head-row"><h1 class="mkt-title">{esc(title)}</h1>'
        f"{cart_link_html(cart_count)}</div>{badge_row}</header>"
        f"{buy_panel}"
        f"{app_cta_html}"
        f"{seller_card(listing, viewer=viewer, store_href=store_href, listing_count=seller_listing_count)}"
        f"{fulfilment_html(listing)}"
        "</div>"
    )

    detail = (
        '<section class="mkt-detail">'
        f'<div class="mkt-detail-media">{gallery_html(media, title=title)}</div>'
        f"{info}"
        "</section>"
    )

    # --- related ---------------------------------------------------------
    related_html = ""
    if related:
        rel_variants = dict(related_variants or {})
        rel_cards: list[str] = []
        for row in related:
            item = dict(row)
            rid = int(item.get("id") or 0)
            item_price = mw.derive_price(item, list(rel_variants.get(rid) or ()))
            rel_cards.append(
                product_card(
                    item,
                    price=item_price,
                    # No badges in the related rail: a "New" chip on a
                    # suggestion is noise, and the page-level suppression rule
                    # that keeps badges honest does not apply to a rail of 4.
                    badges=(),
                    stock="",
                )
            )
        if rel_cards:
            related_html = (
                # The size lives in `.mkt-related > h2`, not in a `style=`
                # attribute: an inline declaration cannot be overridden by the
                # responsive block that has to beat the shell's global
                # `h2 { ... !important }` on phones, and it would need its own
                # CSP hash besides.
                '<section class="mkt-related"><h2>'
                "More from this department</h2>"
                + product_grid(rel_cards, label="Related products")
                + "</section>"
            )

    body = (
        '<div class="mkt">'
        f"{crumbs_html}"
        f"{detail}"
        f"{spec_table(description.attributes)}"
        f"{description_html(description)}"
        f"{notes_html(description.notes)}"
        f"{related_html}"
        '<div class="mkt-sr" role="status" aria-live="polite" id="mkt-live"></div>'
        "</div>"
    )

    payload = dict(listing)
    jsonld: list[dict[str, Any]] = [
        mw.product_jsonld(
            payload,
            price,
            description,
            media,
            product_url(listing_id, origin),
            in_stock=in_stock,
        )
    ]
    breadcrumb = mw.breadcrumb_jsonld(crumbs, origin)
    if breadcrumb:
        jsonld.append(breadcrumb)

    return RenderedPage(
        title=f"{title} · PulseSoc Marketplace",
        meta_description=mw.meta_description(listing, price, description),
        body_html=body,
        canonical_path=canonical,
        # A product page is the storefront's indexable unit. The route decides
        # whether *this* listing is eligible; this flag says the page shape is.
        indexable=True,
        jsonld=tuple(jsonld),
        og_image=media[0].url if media and media[0].kind == "image" else "",
        assets_html=assets_html(),
        og_type="product",
    )


# ---------------------------------------------------------------------------
# Head fragment
# ---------------------------------------------------------------------------


def head_html(page: RenderedPage, *, origin: str = mw.PUBLIC_ORIGIN) -> str:
    """Canonical, robots, Open Graph, Twitter and JSON-LD for one page.

    Built here rather than in the route so both wrappers emit byte-identical
    metadata, and so the structured data on a page can be asserted in a test
    without a Flask client.
    """
    canonical = f"{origin}{page.canonical_path}"
    # The positive directive is asked for, not written here.
    #
    # This line used to spell out `index,follow,max-image-preview:large`, which
    # is a shorter directive than `search_visibility.classify` issues for the
    # same path -- it drops `max-snippet:-1` and `max-video-preview:-1`, the two
    # that tell Google it may show a full snippet and a full video preview
    # rather than its conservative defaults. Every other indexable page on the
    # site gets those through `search_visibility.robots_meta`; the storefront
    # silently opted out of them by restating the policy from memory. Asking the
    # module that owns it is the only way the two stay equal.
    #
    # `robots_extra` still wins, because a renderer that has decided this
    # particular page is a soft 404 knows something about the row that a
    # path-shaped policy cannot. `page.indexable` is the page-shape question and
    # `robots_extra` the per-row one, which is why both exist.
    robots = (
        page.robots_extra
        if page.robots_extra
        else (search_visibility.robots_meta(page.canonical_path)
              if page.indexable else search_visibility.NOINDEX_NOFOLLOW)
    )
    tags = [
        f'<link rel="canonical" href="{esc(canonical)}">',
        f'<meta name="robots" content="{esc(robots)}">',
        f'<meta name="description" content="{esc(page.meta_description)}">',
        f'<meta property="og:type" content="{esc(page.og_type)}">',
        f'<meta property="og:title" content="{esc(page.title)}">',
        f'<meta property="og:description" content="{esc(page.meta_description)}">',
        f'<meta property="og:url" content="{esc(canonical)}">',
        '<meta property="og:site_name" content="PulseSoc">',
    ]
    if page.og_image:
        tags.append(f'<meta property="og:image" content="{esc(page.og_image)}">')
        tags.append('<meta name="twitter:card" content="summary_large_image">')
        tags.append(f'<meta name="twitter:image" content="{esc(page.og_image)}">')
    else:
        # No image, no large-image card: Twitter renders a broken frame for a
        # `summary_large_image` card with nothing to put in it.
        tags.append('<meta name="twitter:card" content="summary">')
    tags.append(f'<meta name="twitter:title" content="{esc(page.title)}">')
    tags.append(f'<meta name="twitter:description" content="{esc(page.meta_description)}">')
    for block in page.jsonld:
        # JSON is the one place `esc()` must not be used — HTML-escaping would
        # corrupt the payload. Two facts make the single replace below
        # sufficient. `ensure_ascii=True` already emits U+2028/U+2029 (the two
        # characters that are line terminators in JavaScript but legal inside a
        # JSON string) as \\uXXXX escapes. And inside a raw-text <script>
        # element the only sequence that can end the element early is a literal
        # `<`. Escaping every `<` as \\u003c is still valid JSON and decodes to
        # the identical string, so `</script>` and `<!--` both survive as data.
        encoded = json.dumps(block, ensure_ascii=True).replace("<", "\\u003c")
        tags.append(f'<script type="application/ld+json">{encoded}</script>')
    return "".join(tags)


# ---------------------------------------------------------------------------
# The public document
# ---------------------------------------------------------------------------
#
# Why a second wrapper exists at all.
#
# `pulse_social_shell()` is the right frame for a signed-in member: it carries
# the nav, the drawer, the dock and the member's own avatar. It is the wrong
# frame for a crawler, for two reasons that are in its first four lines — it
# calls `require_account()` and redirects anyone anonymous to `/login`, and the
# document it emits hardcodes `noindex,nofollow`. Googlebot is anonymous, so
# under that shell alone every product URL is a 302 to a login page and nothing
# in the Marketplace can ever be indexed.
#
# The fix is not to loosen the shell — the member view is personalised and
# `noindex` on it is correct. It is to serve the same `body_html` inside a
# second, minimal document when there is no session. That keeps one renderer,
# one stylesheet and one set of structured data, and puts the entire difference
# between the two experiences in the frame rather than in the content.

# Light, because the storefront it frames is light.
#
# This block used to paint a dark gradient page and dark-theme chrome, which
# was right when it was written against the rest of the site and wrong for the
# one subtree it actually wraps: `.mkt` resolves the native app's *light* store
# palette, so the result was a white content column inset in a near-black page
# — a dark band down either side at every viewport from 320px up, and the
# widest at desktop. Painting the document in the storefront's own surface is
# the fix; colouring the gutters would only have moved the seam.
#
# Every colour below is a `--store-*` token rather than a literal. The tokens
# are declared on `body.mkt-public` by `pulse_marketplace.css`, which this
# document links, and `var()` resolves against the cascade on the element — so
# these rules read the same transcription of `storeLight.ts` that the cards do
# and cannot drift from them. The fallbacks are the matching light values, for
# the one case that would otherwise paint dark text on dark: the stylesheet
# failing to load.
#
# The masthead stays dark on purpose. A black header over a light catalogue is
# what the native store does and what the Business surfaces lock to; it is not
# a leftover of the dark document.
_PUBLIC_BASE_CSS = (
    "*{box-sizing:border-box}"
    "html,body{max-width:100%;overflow-x:hidden}"
    "body{margin:0;background:var(--store-bg-page,#eaeded);"
    "color:var(--store-text-primary,#0f1111);"
    "font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;"
    "-webkit-font-smoothing:antialiased}"
    ".mkt-doc{width:min(100% - 24px,1180px);margin:0 auto;"
    "padding:0 0 calc(56px + env(safe-area-inset-bottom))}"
    # Full-bleed so the dark masthead reaches both edges of the window rather
    # than ending where the centred measure does, which is the same seam this
    # block exists to remove.
    ".mkt-doc-bar{background:var(--store-bg-header,#0b0b0c);"
    "margin:0 calc(50% - 50vw) 18px;padding:max(10px,env(safe-area-inset-top)) "
    "calc(50vw - 50% + 4px) 10px;display:flex;flex-wrap:wrap;align-items:center;"
    "gap:12px;justify-content:space-between}"
    ".mkt-doc-brand{display:inline-flex;align-items:center;gap:8px;font-weight:900;"
    "font-size:18px;color:var(--store-text-on-dark,#fff);text-decoration:none;"
    "letter-spacing:-.01em}"
    ".mkt-doc-bar nav{display:flex;flex-wrap:wrap;gap:8px}"
    ".mkt-doc-bar nav a{font-size:13px;font-weight:700;text-decoration:none;"
    "color:var(--store-text-on-dark-muted,#c7cdd3);"
    "border:1px solid rgba(255,255,255,.22);border-radius:999px;padding:7px 13px}"
    ".mkt-doc-bar nav a:hover{border-color:rgba(255,255,255,.5);"
    "color:var(--store-text-on-dark,#fff)}"
    ".mkt-doc-bar nav a.is-primary{background:linear-gradient("
    "135deg,var(--store-cta-from,#2ee6a8),var(--store-cta-to,#22c48d));"
    "color:var(--store-cta-text,#04231a);border-color:transparent}"
    ".mkt-doc-foot{margin-top:40px;padding-top:18px;"
    "border-top:1px solid var(--store-border-hairline,#d5d9d9);"
    "font-size:13px;color:var(--store-text-muted,#565959);display:grid;gap:10px}"
    ".mkt-doc-foot a{color:var(--store-text-link,#0a7050)}"
    ".mkt-doc-links{display:flex;flex-wrap:wrap;gap:8px 18px}"
    ".mkt-doc a:focus-visible,.mkt-doc button:focus-visible{"
    "outline:2px solid var(--store-select-border,#189669);outline-offset:2px}"
)


#: The four commerce policy pages, plus the company pages a shopper looks for
#: before handing over a card. Duplicated from `_public_shell.html`'s footer
#: rather than imported because that is a Jinja template and this is a Python
#: string builder; `tests/test_marketplace_public_pages.py` asserts the two
#: carry the same commerce set, which is the part Merchant Center's review
#: looks for from the landing page. A sitemap is not a path a reviewer follows.
_PUBLIC_FOOTER_LINKS = (
    ("/", "Home"),
    ("/app", "iPhone app"),
    ("/about", "About"),
    ("/help", "Help"),
    ("/terms", "Terms"),
    ("/privacy", "Privacy"),
    ("/support", "Support"),
    ("/returns", "Returns"),
    ("/refund-policy", "Refunds"),
    ("/shipping", "Shipping"),
    ("/contact", "Contact"),
)


def public_document(
    page: RenderedPage,
    *,
    origin: str = mw.PUBLIC_ORIGIN,
    sign_in_href: str = "/login",
    head_extra: str = "",
    extra_html: str = "",
) -> str:
    """The signed-out, indexable document for one storefront page.

    Deliberately small. It supplies a document, a masthead, a footer and the
    handful of base rules a page needs when the shell's inline stylesheet is
    not there — nothing else. All commerce markup is `page.body_html`, byte for
    byte the same string the member shell receives, so the two experiences
    cannot drift apart in content and there is no second storefront to maintain.

    `mkt-public` on the body is what stops this document framing the storefront
    in the wrong colour. The storefront subtree is deliberately light (see the
    `.mkt` palette block in `pulse_marketplace.css`), this document's base rules
    are dark, and a light panel inset in a dark page paints a dark band down
    either side of the content — measured at every viewport from 320px up. The
    class lets that stylesheet claim the page surface too, scoped so it cannot
    reach a document that is not a storefront.

    `head_extra` is for tags the route owns rather than the renderer: today the
    Smart App Banner, whose app-id comes from the App Store URL that
    `bot.app_link_context` already treats as the single authority.

    `extra_html` lands after the body, and mirrors the parameter of the same
    name on `bot._marketplace_member_storefront_reply`. It is for markup that
    belongs to the page but not inside it — today the delivery estimate's
    stylesheet and the script that fills the pending sentence in. The renderer
    cannot place those: `delivery_html` arrives already rendered precisely so
    this module holds no opinion about delivery, and the assets are the route's
    to version.

    There is deliberately no `app_cta_html` here, though an earlier draft had
    one. `render_discovery` and `render_product` already place that CTA inside
    the body, because the member shell receives nothing but the body and the
    promotion has to reach that reader too. A second slot in this wrapper is
    therefore not a placement choice, it is a second copy — which is what the
    route hit the first time it passed one. `extra_html` is not that case and
    the difference is worth stating: the renderer never emits those assets for
    anyone, so this slot is the only one, not the second.
    """
    lang = "en"
    footer_links = "".join(
        f'<a href="{esc(href)}">{esc(label)}</a>' for href, label in _PUBLIC_FOOTER_LINKS
    )
    return (
        "<!doctype html>"
        f'<html lang="{lang}">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f"<title>{esc(page.title)}</title>"
        f"{head_html(page, origin=origin)}"
        f"{head_extra}"
        '<link rel="stylesheet" href="/static/css/pulsesoc-tokens.css?v=storefront-20260926a">'
        f"<style>{_PUBLIC_BASE_CSS}</style>"
        f"{page.assets_html}"
        "</head>"
        '<body class="mkt-public">'
        '<div class="mkt-doc">'
        '<header class="mkt-doc-bar">'
        f'<a class="mkt-doc-brand" href="{esc(BASE_PATH)}">PulseSoc Marketplace</a>'
        "<nav aria-label=\"PulseSoc\">"
        f'<a href="{esc(BASE_PATH)}">Browse</a>'
        '<a href="/pulse">PulseSoc</a>'
        f'<a class="is-primary" href="{esc(sign_in_href)}">Sign in</a>'
        "</nav>"
        "</header>"
        "<main>"
        f"{page.body_html}"
        "</main>"
        '<footer class="mkt-doc-foot">'
        "<p>Products are listed by independent PulseSoc sellers. "
        f'<a href="{esc(BASE_PATH)}">Browse the Marketplace</a> or '
        '<a href="/pulse">join PulseSoc</a> to message a seller.</p>'
        f'<nav class="mkt-doc-links" aria-label="PulseSoc">{footer_links}</nav>'
        "</footer>"
        "</div>"
        f"{extra_html}"
        "</body></html>"
    )
