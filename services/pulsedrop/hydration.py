"""The live commerce overlay: what a PulseDrop post means *today*.

## The split this module exists to enforce

A PulseDrop publication is three things that age at three different rates, and
the whole design of this subsystem depends on never letting them congeal:

    MEDIA       the product photographs, or the rendered 9:16 video.
                Written once. Immutable. Lives in ``pulse_posts.media_ids_json``
                and ``pulse_reels.video_url``.
    REFERENCE   which listing this publication is *about*.
                Written once. Immutable. Lives in
                ``pulsedrop_publications.listing_id``.
    OVERLAY     price, stock, availability, the store's name, the label, the
                call to action. Read fresh on every render. Stored nowhere.
                That is this module.

``editorial.caption`` already argues the first half of this from the writing
side: a price frozen into ``pulse_posts.content`` becomes a lie the platform
published under a verified badge the moment the seller re-prices, and there is
no repair — rewriting members' visible history to match a database is worse than
the stale number. ``reel_composer`` argues the same thing from the pixels: a
price burned into an H.264 frame cannot be corrected at all without re-encoding
and re-uploading a file members have already seen, and it cannot be translated.

Both of those modules end their argument by pointing here. This is where the
price actually comes from.

## Why the read cannot reuse ``eligibility.candidate_sql``

That predicate answers "may a buyer be shown this product", and it filters the
result set down to listings where the answer is yes. It is the right question
for the curator, which is choosing something to publish, and the wrong one here.

A Reel published in March is still in the feed in September. By then its product
may have sold out, been withdrawn, had its seller suspended, or been deleted
outright. Filtering those away would make the overlay *absent*, and an absent
overlay renders as a commerce card with no price and a button that goes nowhere
— which is the worst of the available outcomes, because it looks like a bug
rather than like a sold-out product.

So this read applies no public predicate. Every publication resolves to a state,
including the states the marketplace itself will not show, and a vanished row
resolves to :data:`REMOVED` rather than to nothing.

## The availability vocabulary is the client's, not a new one

``mobile-native/src/api/marketplaceBuyerPresentation.ts`` already defines
``MarketplacePurchaseBlock`` as ``"" | "UNAVAILABLE" | "OUT_OF_STOCK" |
"NOT_PRICED"``, already maps those to copy in ``marketplaceAvailabilityCopy``,
and already derives them from ``buyer_visible``, ``inventory_state``,
``quantity`` and ``product_type``. Inventing a second vocabulary here — and a
second set of translated strings for it — would guarantee that a PulseDrop card
and a Marketplace card eventually disagree about the same listing on the same
screen. So the payload ships **the exact fields that helper reads**, plus the
server's own verdict in the same code space, and the client is free to use
either. They agree by construction.

:data:`REMOVED` is the one genuine addition, and it is genuine because the
client's vocabulary has no word for it: a deleted listing never reaches a
Marketplace card, and only reaches this one because the post outlived it.

## Why an unavailable product gets no route at all

``/pulse/marketplace/<id>`` re-applies the public predicate and aborts 404 when
it fails, deliberately — the route's own comment explains that distinguishing
"withdrawn" from "never existed" would confirm the existence of a row to someone
guessing ids. So a CTA pointing at a sold-out product is a CTA pointing at a 404.

The overlay therefore withdraws the affordance rather than the content: the Reel
still plays, the caption still reads, the label still shows, and the commerce
row becomes a state chip with nothing to tap. That is the brief's separation of
content history from current commerce availability, made structural — there is
no code path here that can emit a route to a destination that would 404.

## Attribution

Three parties, never collapsed: PulseDrop is the **publisher**, the seller is the
**merchant**, the listing is the **commerce object**. PulseDrop must never appear
as the seller of anything, so the seller block is always populated from the
listing's own seller row and never falls back to the publisher.

:func:`attribution_token` mints the join key for the funnel the brief wants to be
able to measure later — Reel → PulseDrop → Product → Seller → Checkout →
Conversion. It is deliberately not signed and carries no authority: it is a
pointer to ``pulsedrop_publications.id``, which already holds the surface, the
listing, the seller, the label, the rank score and the ranker version. Signing it
would imply it could be trusted as a claim, and it is only ever a lookup.

## Failure

Every entry point degrades to "no overlay" and logs, rather than raising. A feed
read must not 500 because a subsystem's tables have not been created in this
deployment, and a post whose overlay is missing renders as an ordinary post by
the author ``@pulsedrop`` — which is a true and harmless thing to show.

Note that hydration is deliberately **not** gated on ``config.enabled()``.
The kill switch stops PulseDrop from publishing; it does not retroactively strip
the price off everything it has already published.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence

from services import marketplace_listing_lifecycle as lifecycle
from services import marketplace_seller_identity as seller_identity
from services.pulsedrop import editorial

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

#: Purchasable right now. The empty string, matching the client's
#: ``MarketplacePurchaseBlock`` where "no block" is "".
AVAILABLE = ""
#: The listing exists and is public, but has no stock for a stocked type.
OUT_OF_STOCK = "OUT_OF_STOCK"
#: Withdrawn, unapproved, archived, or its seller is not an approved store.
#: One code for several causes because the buyer's next move is identical.
UNAVAILABLE = "UNAVAILABLE"
#: Public and in stock, but the price label does not parse to an amount, so
#: checkout could not charge it. Reported separately because it is the only one
#: of these the seller can fix in a second.
NOT_PRICED = "NOT_PRICED"
#: The listing row is gone. Not in the client's vocabulary, because a deleted
#: listing cannot reach a Marketplace card — only a post that outlived it.
REMOVED = "REMOVED"

#: States in which a tap has somewhere to go. See the module docstring: the
#: product page 404s for everything else, so this set is the one authority for
#: whether a route may be emitted.
_ROUTABLE = frozenset({AVAILABLE})

#: States in which the overlay may still publish live product detail — the
#: current title, price, cover and category.
#:
#: The line is drawn at *who took it down*. A product that is merely sold out or
#: unpriced is still the listing the seller is offering; the stock rule is the
#: only one it fails, and "Sold out, $49.00" is what every other marketplace
#: surface shows. A product that is withdrawn, archived, unapproved, or whose
#: seller has been suspended has been taken off sale by the seller or by the
#: platform, and continuing to read its current price out of the database and
#: show it under a verified badge would make this the one surface in PulseSoc
#: that does not honour a withdrawal.
#:
#: The post itself keeps its caption, its title and its media — those were
#: published, they are content history, and nothing here revokes them. What
#: stops is the *live read*. Which is the same distinction the whole module is
#: built on, applied to itself.
_DISCLOSED = frozenset({AVAILABLE, OUT_OF_STOCK, NOT_PRICED})


# ---------------------------------------------------------------------------
# Call to action
# ---------------------------------------------------------------------------

#: The only actionable CTA PulseDrop emits.
#:
#: The brief offers "View Product / View Details / Shop Now" and this module
#: declines the third. The tap opens a product page; it does not complete a
#: purchase, and "Shop Now" on a button that cannot take money is the fake
#: checkout flow the brief forbids two paragraphs later. "View product" is what
#: the tap does.
CTA_VIEW_PRODUCT = "VIEW_PRODUCT"
#: No tap. Emitted with ``enabled: False`` so the client renders the state chip
#: in the button's place rather than an enabled control that fails.
CTA_NONE = "NONE"

#: ``commerce:`` is the namespace, not ``extended:``.
#:
#: ``extended`` is a *tier filename* -- the app ships ``core.json`` and
#: ``extended.json`` per locale -- and the namespaces are the top-level blocks
#: inside them, registered in ``mobile-native/src/i18n/catalogs/index.ts``.
#: A key prefixed ``extended:`` does not throw: the resolver keeps the whole
#: string as a path inside ``common``, misses, and ``humanizeKey()`` returns
#: title-cased English. So ``extended:pulsedrop.cta.view_product`` would render
#: "View Product" in all eleven languages and ``npm run i18n:validate`` would
#: still report 100%. There is no test that catches this; only the prefix does.
#:
#: The rule below: reuse where the words already exist, add ``pulsedrop.*`` only
#: for what is genuinely new. ``commerce:marketplace.outOfStock`` and
#: ``commerce:marketplace.statusRemoved`` are reuse; they are in the app's
#: catalogs today.
#:
#: The CTA was reuse too, and is not any more. It pointed at
#: ``commerce:productSignal.viewProduct`` — the feed's other shoppable card
#: already says "View product" in all eleven locales, so borrowing it beat
#: asking eleven translators for a second string that could drift from the
#: first. That reasoning was sound and the fact behind it was wrong: the
#: ``productSignal`` namespace belongs to an unmerged branch and does not exist
#: on ``main``. A key the catalogs do not have resolves to the client's
#: ``defaultValue``, which is ``CTA_FALLBACK`` below — English, silently, in ten
#: of the eleven locales, with ``npm run i18n:validate`` still reporting 100%
#: because a key nothing declares is a key nothing misses.
#:
#: So PulseDrop owns this one. The eleven values were lifted word-for-word from
#: ``productSignal.viewProduct``, so no translator was asked and the two strings
#: start identical; if that branch lands they are duplicates saying the same
#: thing, which is the cheap failure. Depending on a namespace that may never
#: land is the expensive one.
CTA_I18N = {
    CTA_VIEW_PRODUCT: "commerce:pulsedrop.cta.viewProduct",
    CTA_NONE: "",
}
CTA_FALLBACK = {
    CTA_VIEW_PRODUCT: "View product",
    CTA_NONE: "",
}

#: The state chip's key, per availability code. The client already owns copy for
#: the first three via ``marketplaceAvailabilityCopy``; these keys exist for the
#: web surface and for the states that helper cannot see.
#: Two of these five already exist in the app's catalogs and are reused:
#: ``marketplace.outOfStock`` ("Out of stock") and ``marketplace.statusRemoved``
#: ("Removed") are translated in all eleven locales for the marketplace's own
#: screens, and a PulseDrop card describing the same listing state in different
#: words would be the same product reading two ways in one session. Only
#: UNAVAILABLE and NOT_PRICED are new, because the marketplace never has to name
#: them -- it 404s a withdrawn listing instead of describing it.
AVAILABILITY_I18N = {
    AVAILABLE: "",
    OUT_OF_STOCK: "commerce:marketplace.outOfStock",
    UNAVAILABLE: "commerce:pulsedrop.availability.unavailable",
    NOT_PRICED: "commerce:pulsedrop.availability.notPriced",
    REMOVED: "commerce:marketplace.statusRemoved",
}
#: Word-for-word the ``en`` value of each key above, not a paraphrase of it.
#: The fallback is what the web surface renders and what a client shows when a
#: key is missing, so "Sold out" here against "Out of stock" in the catalog
#: would make the same listing read two ways depending on which surface loaded.
#: When one side changes, change both.
AVAILABILITY_FALLBACK = {
    AVAILABLE: "",
    OUT_OF_STOCK: "Out of stock",
    UNAVAILABLE: "No longer available",
    NOT_PRICED: "Not priced yet",
    REMOVED: "Removed",
}

SIGNAL = "signal"
REEL = "reel"

#: Chunk size for the ``IN`` list. A feed page is 20-40 posts and a profile page
#: is 30, so this is never reached in practice; it exists so that a caller
#: hydrating a whole profile's history cannot build a ten-thousand-placeholder
#: statement and hit a driver parameter limit.
_CHUNK = 200

#: Columns the overlay needs. Deliberately a short list and not ``l.*``: this
#: runs on every feed read, and the two heavy columns on ``marketplace_listings``
#: (``description`` and ``gallery_json``) are exactly the ones the overlay has no
#: use for. The lifecycle rules need ``status``, ``approval_status``,
#: ``quantity``, ``product_type``, ``listing_type`` and the seller's status;
#: every one of them is here, because a rule whose column is missing answers
#: ``None`` and falls back to ``passes_when_unknown``, which would let a
#: suspended seller's product read as available.
_LISTING_COLUMNS = (
    "id", "seller_user_id", "title", "price_label", "currency", "quantity",
    "product_type", "listing_type", "status", "approval_status",
    "cover_image_url", "category",
)


def _select() -> str:
    columns = ", ".join(f"l.{name} AS listing_{name}" for name in _LISTING_COLUMNS)
    return f"""
        SELECT p.id AS publication_id,
               p.post_id AS post_id,
               p.surface AS surface,
               p.listing_id AS ref_listing_id,
               p.seller_user_id AS ref_seller_user_id,
               p.editorial_label AS editorial_label,
               p.reel_id AS reel_id,
               p.published_at AS published_at,
               {columns},
               COALESCE(ms.status,'') AS seller_status,
               {seller_identity.store_name_select('ms')},
               COALESCE(u.username,'') AS seller_username,
               COALESCE(u.display_name,'') AS seller_account_name
        FROM pulsedrop_publications p
        LEFT JOIN marketplace_listings l ON l.id = p.listing_id
        LEFT JOIN users u ON u.user_id = p.seller_user_id
        LEFT JOIN marketplace_sellers ms ON ms.user_id = p.seller_user_id
        WHERE p.state = 'published' AND p.post_id IN ({{placeholders}})
    """


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def commerce_for_posts(cur, post_ids: Iterable[Any]) -> dict[int, dict]:
    """The overlay for every PulseDrop post in ``post_ids``, keyed by post id.

    Takes a cursor rather than opening its own connection, matching
    ``eligibility.media_for_listings``. That is not a style preference: the pool
    is eight connections with a three-second checkout timeout, and a payload
    builder that opens a connection per row is a recorded outage in this repo.
    One statement, one round trip, however many posts.

    Posts with no PulseDrop publication are simply absent from the result, which
    is what lets a caller hydrate a mixed feed by passing every post id it has
    and merging whatever comes back.
    """
    ids = _ints(post_ids)
    if not ids:
        return {}
    out: dict[int, dict] = {}
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        try:
            placeholders = ",".join("?" for _ in chunk)
            cur.execute(_select().format(placeholders=placeholders), tuple(chunk))
            rows = [dict(row) for row in cur.fetchall() or []]
        except Exception:
            # Most likely cause by far: this deployment has never run the
            # curator, so ``pulsedrop_publications`` does not exist. A feed read
            # must not fail for that.
            log.warning("pulsedrop_hydration_read_failed count=%s", len(chunk), exc_info=True)
            return out
        for row in rows:
            post_id = int(row.get("post_id") or 0)
            if post_id:
                out[post_id] = overlay(row)
    return out


def attach(cur, posts: Sequence[dict], *, key: str = "id", field: str = "commerce") -> Sequence[dict]:
    """Merge the overlay into a list of post dicts, in place.

    Convenience for the feed and profile serializers, which already have the
    rows in hand. Returns the same list so it can be used as an expression.
    """
    overlays = commerce_for_posts(cur, [post.get(key) for post in posts])
    if not overlays:
        return posts
    for post in posts:
        found = overlays.get(int(post.get(key) or 0))
        if found:
            post[field] = found
    return posts


def commerce_for_post(post_id: Any) -> dict:
    """One post's overlay, opening and closing its own connection.

    For the single-post routes only. Anything rendering a list must use
    :func:`commerce_for_posts` with a cursor it already holds.
    """
    from services import db as db_service

    conn = None
    try:
        conn = db_service.connect()
        return commerce_for_posts(conn.cursor(), [post_id]).get(int(post_id or 0), {})
    except Exception:
        log.warning("pulsedrop_hydration_single_failed post_id=%s", post_id, exc_info=True)
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# The overlay
# ---------------------------------------------------------------------------


def overlay(row: Mapping[str, Any]) -> dict:
    """Build one overlay from a joined publication row.

    Split out from the read so it can be tested without a database, and so the
    web surface can call it with a row it fetched for its own reasons.
    """
    listing = _listing(row)
    availability = state(listing)
    surface = str(row.get("surface") or SIGNAL).strip().lower() or SIGNAL
    publication_id = int(row.get("publication_id") or 0)
    listing_id = int(row.get("ref_listing_id") or 0)
    seller_user_id = int(row.get("ref_seller_user_id") or 0)
    label = _label(row, listing)

    return {
        "pulsedrop": True,
        "surface": surface,
        "publication_id": publication_id,
        "published_at": str(row.get("published_at") or ""),
        "attribution": {
            # Named roles, not ids in a flat dict, because the one mistake that
            # matters here is an analytics consumer reading the publisher as the
            # merchant. Making them impossible to confuse is worth four keys.
            "token": attribution_token(publication_id, surface, listing_id),
            "publisher_role": "publisher",
            "merchant_role": "merchant",
            "listing_id": listing_id,
            "seller_user_id": seller_user_id,
            "surface": surface,
        },
        "product": _product(listing, listing_id, availability),
        "seller": _seller(row, seller_user_id),
        "label": {
            "key": label.key,
            "i18n_key": label.i18n_key,
            "fallback": label.fallback,
            "evidence": label.evidence,
        },
        "cta": _cta(listing_id, availability),
        "availability": {
            "code": availability,
            "i18n_key": AVAILABILITY_I18N.get(availability, ""),
            "fallback": AVAILABILITY_FALLBACK.get(availability, ""),
            "purchasable": availability == AVAILABLE,
        },
        "accessibility_text": _accessibility(listing, label, availability),
    }


def state(listing: Mapping[str, Any] | None) -> str:
    """The availability code for a listing row, or :data:`REMOVED` for ``None``.

    Order matters and is the order the buyer can do least about first, matching
    ``public_denial_code`` and the client helper that mirrors it. ``NOT_PRICED``
    comes last because a sold-out unpriced listing is more usefully described as
    sold out.
    """
    if not listing:
        return REMOVED
    denial = lifecycle.public_denial_code(listing)
    if denial == "OUT_OF_STOCK":
        return OUT_OF_STOCK
    if denial:
        # SELLER_UNAVAILABLE and ITEM_UNAVAILABLE both land here. The buyer's
        # next move after each is the same — none — and telling a stranger which
        # of the two applies would report a seller's suspension to the public.
        return UNAVAILABLE
    if _price_minor(listing) <= 0:
        return NOT_PRICED
    return AVAILABLE


def attribution_token(publication_id: int, surface: str, listing_id: int) -> str:
    """The forward-compatible join key. See the module docstring.

    Versioned by its first segment so a later scheme can be told apart from this
    one by a consumer that has already stored some. Empty when there is no
    publication to point at, rather than a token that dereferences to nothing.
    """
    if not publication_id:
        return ""
    return f"pd1.{int(publication_id)}.{_slug(surface) or SIGNAL}.{int(listing_id or 0)}"


def parse_attribution_token(token: str) -> dict:
    """Inverse of :func:`attribution_token`. ``{}`` for anything unrecognised."""
    parts = str(token or "").split(".")
    if len(parts) != 4 or parts[0] != "pd1":
        return {}
    try:
        return {
            "publication_id": int(parts[1]),
            "surface": parts[2],
            "listing_id": int(parts[3]),
        }
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


def _product(listing: Mapping[str, Any] | None, listing_id: int, availability: str) -> dict:
    """The commerce object.

    Carries ``buyer_visible``, ``inventory_state``, ``quantity`` and
    ``product_type`` because those are precisely the four fields
    ``marketplacePurchaseBlock`` reads. A client that runs its own helper over
    this block gets the same answer as ``availability.code``, which is the point
    — see the module docstring.
    """
    if not listing or availability not in _DISCLOSED:
        # Withdrawn, suspended or deleted. See :data:`_DISCLOSED`: the state is
        # reported, the live detail is not. ``exists`` distinguishes the two
        # causes for an operator reading a payload, and is the only thing here
        # that says anything about the row at all.
        return {
            "listing_id": listing_id,
            "exists": bool(listing),
            "title": "",
            "price_label": "",
            "currency": "",
            "quantity": 0,
            "product_type": "",
            "category": "",
            "cover_image_url": "",
            "buyer_visible": False,
            "inventory_state": "unknown",
            "denial_code": lifecycle.public_denial_code(listing) if listing else "",
        }
    return {
        "listing_id": listing_id,
        "exists": True,
        # The seller's current title, trimmed the same way the caption was.
        # The caption froze the title at publication and that is survivable — a
        # renamed product is still the product — but the card under it should
        # show what the product is called today.
        "title": editorial.clean_title(listing),
        # Verbatim. ``editorial.price_text`` explains why there is exactly one
        # price formatter in this product and it is not in Python.
        "price_label": editorial.price_text(listing),
        "currency": str(listing.get("currency") or "").strip().upper(),
        "quantity": _int(listing.get("quantity")),
        "product_type": str(listing.get("product_type") or listing.get("listing_type") or "").strip(),
        "category": str(listing.get("category") or "").strip(),
        "cover_image_url": str(listing.get("cover_image_url") or "").strip(),
        # Always true in this branch — the only states that reach it are the
        # ones where the listing is still the seller's live offer. Written as a
        # literal rather than a re-derivation so it cannot drift from
        # ``_DISCLOSED`` above.
        "buyer_visible": True,
        "inventory_state": "out_of_stock" if availability == OUT_OF_STOCK else "in_stock",
        # The server's finer-grained verdict, for logs and analytics. Not for
        # display: it separates a suspended seller from a withdrawn listing, and
        # ``availability.code`` is the version a stranger may see.
        "denial_code": lifecycle.public_denial_code(listing),
    }


def _seller(row: Mapping[str, Any], seller_user_id: int) -> dict:
    """The merchant. Never PulseDrop, and never defaulted to PulseDrop.

    ``display_store_name`` rather than the account's personal name: the store is
    the identity a buyer transacts with, and a marketplace that shows the
    seller's real name under a product is a different product.
    """
    approved = str(row.get("seller_status") or "").strip().lower() == "approved"
    route = ""
    screen = ""
    if seller_user_id and approved:
        route, screen = _destination("store", seller_user_id)
    return {
        "user_id": seller_user_id,
        "store_name": seller_identity.display_store_name(row),
        "username": str(row.get("seller_username") or ""),
        "route": route,
        "screen": screen,
        "role": "merchant",
    }


def _cta(listing_id: int, availability: str) -> dict:
    """The button. No route unless the destination would actually resolve."""
    if availability not in _ROUTABLE or not listing_id:
        return {
            "code": CTA_NONE,
            "i18n_key": "",
            "fallback": "",
            "enabled": False,
            "route": "",
            "screen": "",
            "url": "",
        }
    route, screen = _destination("product", listing_id)
    return {
        "code": CTA_VIEW_PRODUCT,
        "i18n_key": CTA_I18N[CTA_VIEW_PRODUCT],
        "fallback": CTA_FALLBACK[CTA_VIEW_PRODUCT],
        "enabled": bool(route),
        "route": route,
        "screen": screen,
        # The canonical https app link, for the share sheet and the web surface.
        # Built by ``app_links`` and not by string concatenation, because that
        # module owns the one host the app's entitlement claims.
        "url": _share_url("product", listing_id),
    }


def _label(row: Mapping[str, Any], listing: Mapping[str, Any] | None):
    """The editorial label recorded at publication, not re-derived now.

    Re-classifying at read time would make the chip flicker between TRENDING and
    DISCOVERY as counts move, and would detach the claim from the evidence
    string stored beside it on the publication row. "Why did PulseDrop call this
    trending" has to be answerable from the database a month later, which is
    only true if the label is the stored one.

    Falls back to DISCOVERY for a row written before the column existed or
    holding a key this build does not recognise — the label that makes the
    weakest claim is the safe default for an unknown one.
    """
    key = str(row.get("editorial_label") or "").strip().upper()
    found = editorial.LABELS.get(key)
    if found:
        return found
    return editorial.LABELS[editorial.DISCOVERY]


def _accessibility(listing: Mapping[str, Any] | None, label, availability: str) -> str:
    """One sentence a screen reader can read in place of the card.

    English, and a last-resort fallback: the native app composes its own from
    ``label.i18n_key`` plus the same live fields, because the server does not
    know the reader's language. Built here so the web surface has one and so a
    client that has not implemented the composition still reads something true.

    The availability state is appended rather than substituted, because a
    VoiceOver user needs to hear that the product exists *and* that it cannot be
    bought — hearing only "Sold out" loses the product.
    """
    if not listing or availability not in _DISCLOSED:
        # Same rule as :func:`_product`: a withdrawn listing's live price does
        # not reach a sighted member, so it must not reach a screen reader
        # either. An accessible surface that leaks what the visual one hides is
        # not more accessible, it is a second surface with a different contract.
        return AVAILABILITY_FALLBACK.get(availability, "") or AVAILABILITY_FALLBACK[REMOVED]
    base = editorial.accessibility_text(listing, label)
    suffix = AVAILABILITY_FALLBACK.get(availability, "")
    if availability == AVAILABLE or not suffix:
        return base
    return f"{base}. {suffix}".strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _listing(row: Mapping[str, Any]) -> dict:
    """Unprefix the listing columns, or ``{}`` when the LEFT JOIN found nothing.

    The prefix exists because the join has a ``users`` row and a
    ``marketplace_sellers`` row beside the listing, and all three carry columns
    named ``status``. Unprefixing here rather than in SQL keeps one list of
    column names instead of two lists that must stay in step.

    Emptiness is decided on ``listing_id``, not on truthiness of the whole dict:
    an unmatched LEFT JOIN produces every key present and set to NULL, which is
    a populated dict full of ``None``. Reading that as a listing would hand the
    lifecycle rules ``quantity=None`` and get OUT_OF_STOCK for a product that
    does not exist.
    """
    if row.get("listing_id") is None:
        return {}
    listing = {name: row.get(f"listing_{name}") for name in _LISTING_COLUMNS}
    # The lifecycle rules read the seller's status and store name off the same
    # mapping as the listing's own columns, which is how ``public_sql`` and
    # ``is_public`` stay equivalent. Carrying them here is what makes
    # ``public_denial_code`` able to answer ``SELLER_UNAVAILABLE`` at all.
    listing["seller_status"] = row.get("seller_status")
    listing["seller_store_name"] = row.get("seller_store_name")
    return listing


def _price_minor(listing: Mapping[str, Any]) -> int:
    """The price in minor units, via the parser checkout itself uses.

    Imported from ``bot`` lazily and wrapped, for two reasons. Importing ``bot``
    at module scope connects to the database and runs ``init_db()``, which this
    package's docstring promises not to do. And a worker process that never
    imported ``bot`` must still be able to import this module.

    The fallback is the weaker test the label alone can support. It is weaker in
    a specific direction — an unparseable non-empty label reads as priced — and
    that is the right way to be wrong here, because the alternative strips the
    CTA off a healthy product on a path where the parser merely was not
    available.
    """
    label = str(listing.get("price_label") or "").strip()
    try:
        from bot import parse_price_label_to_cents

        amount, _currency = parse_price_label_to_cents(
            label, str(listing.get("currency") or "USD")
        )
        return int(amount or 0)
    except Exception:
        return 1 if label else 0


def _destination(key: str, resource_id: Any) -> tuple[str, str]:
    """``(path, native_screen)`` for an app-links destination, or ``("", "")``.

    Both, because the two clients ask different questions. The native router
    matches the path (``nativeRouteActions`` already resolves
    ``/pulse/marketplace/<id>``), and a caller holding a navigator wants the
    screen name without re-parsing a string the server already knows the answer
    for.
    """
    try:
        from services import app_links

        spec = app_links.DESTINATIONS.get(key)
        if spec is None:
            return "", ""
        return app_links.resolve_destination_path(spec, resource_id), str(spec.native_screen or "")
    except Exception:
        log.warning("pulsedrop_hydration_route_failed key=%s id=%s", key, resource_id, exc_info=True)
        return "", ""


def _share_url(key: str, resource_id: Any) -> str:
    try:
        from services import app_links

        return app_links.build_app_link(key, resource_id, source="system")
    except Exception:
        return ""


def _ints(values: Iterable[Any]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for value in values or ():
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in seen:
            seen.add(number)
            out.append(number)
    return out


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _slug(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch == "_")
