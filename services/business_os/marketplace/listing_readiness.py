"""Business OS — Marketplace LISTING READINESS: one verdict, computed once.

Answers a single question about one ``marketplace_listings`` row — can this be
sold? — and answers it on the server so that no client has to guess.

    {"publishable": bool, "checkout_ready": bool,
     "blockers": [CODE, ...], "warnings": [CODE, ...]}

Why this module exists
----------------------
The seller Store screen loads ``GET /api/pulse/marketplace/seller/listings``,
which carried no verdict at all. So the client grew one:
``mobile-native/src/api/storeDashboard.ts`` derives a five-rung ladder and a
per-listing health state, with a ``LOW_STOCK_THRESHOLD`` no server code knows
about. That was a reasonable local decision and the wrong global one, because a
client cannot see what a client was not sent. Two things it gets wrong today are
recorded as GAP 22 and GAP 23 in ``STORE_SELLER_MANAGEMENT_ARCHITECTURE.md``.

The sibling engine ``services/business_os/suppliers/drafts.py:_validate`` answers
the same question for supplier-imported drafts, and answers it well — it already
keeps ``UNKNOWN_INVENTORY`` distinct from out-of-stock, which is the distinction
the client loses. It cannot be reused as-is: it requires a
``marketplace_product_sources`` row and priced variants, and a merchant-authored
listing has neither.

So this module deliberately does NOT invent a vocabulary. Every code it shares
with that evaluator is spelled identically, and a test asserts the spellings
match, so the two cannot drift into describing the same fault by two names. The
intended end state is one engine with a supplier-aware extension; this is the
half that the Store workspace needs, bound to the other half by that test rather
than by a promise.

Two honesty rules this module exists to enforce
-----------------------------------------------
* **Unknown is not zero.** ``marketplace_listings.quantity`` is nullable, and a
  NULL means the seller does not track stock. Reporting that as out-of-stock
  tells a merchant their product is unavailable when the truth is that nobody
  knows. It is reported as ``UNKNOWN_INVENTORY`` and it blocks *checkout*
  without blocking publication — failing closed on the promise to a buyer while
  failing open on the merchant's right to list.
* **Missing is not free.** A blank ``price_label`` is a blocker, never a price.
  It must never reach a buyer as "Free" or "$0.00", and it must not reach the
  merchant as silence either: ``MISSING_PRICE`` is what the row has to say.

One gate, not two
-----------------
The submit route used to carry three checks of its own — a description, a
prohibited-goods decision, and a cover row of type image/gif — that this module
knew nothing about. That was survivable while submitting was the only way to
publish, because the route was the last word. Bulk publish removes that safety
net: it acts on a verdict, so anything the verdict cannot see is a gate that
does not exist. All three now live here, and the route asks rather than repeats.

The visible symptom, before: a listing in a prohibited category read
"Ready to publish" on the seller's own row and was refused the moment they
tapped it.

What this module does NOT decide
-------------------------------
Whether a row is physical or a download. That question already has an owner in
``services/marketplace_listing_types.effective_listing_type``, and this module
asks it rather than reading the columns itself. The reason is not tidiness: the
columns lie. ``product_type`` and ``delivery_type`` are both declared
``TEXT DEFAULT 'digital'``, so a physical lamp created by the modern write path
— which sets ``listing_type`` — carries 'digital' in the other two. A first draft
of this module matched on those two columns, concluded that every listing in the
store was stockless, and reported no inventory state for any of them.

No money and no supplier facts appear in a verdict — only codes. A buyer-facing
surface may render this object without leaking cost, margin or credentials, and a
test pins that. The verdict is attached in the seller route rather than in
``pulse_marketplace_listing_payload``, because that serializer also feeds the
public listing page and ``/api/pulse/marketplace/search``; readiness is the
merchant's own business and a test asserts it does not appear there.
"""

from __future__ import annotations

from typing import Any, Optional

from services import marketplace_listing_types as _types
from services import marketplace_listing_lifecycle as _life
from services import marketplace_goods_policy as _goods

# --- vocabulary --------------------------------------------------------------
# Spelled to match services/business_os/suppliers/drafts.py. See
# tests/business_os/test_listing_readiness.py, which fails if either side
# renames one of these without the other.
MISSING_TITLE = "MISSING_TITLE"
MISSING_CATEGORY = "MISSING_CATEGORY"
NO_VALID_MEDIA = "NO_VALID_MEDIA"
MISSING_PRICE = "MISSING_PRICE"
RESTRICTED_PRODUCT = "RESTRICTED_PRODUCT"
UNKNOWN_INVENTORY = "UNKNOWN_INVENTORY"

# Codes with no counterpart in the supplier evaluator, because a supplier draft
# cannot be in these states: it has variants where a merchant listing has a
# single listing-level quantity.
OUT_OF_STOCK = "OUT_OF_STOCK"
LOW_STOCK = "LOW_STOCK"

#: Also without a supplier counterpart: a supplier draft inherits the provider's
#: copy, so it cannot reach the evaluator with no description at all. A
#: merchant-authored draft can, and the submit route has always refused it.
MISSING_DESCRIPTION = "MISSING_DESCRIPTION"

#: Media kinds that can stand as a listing's cover. A video is media but it is
#: not a cover: the still frame a buyer sees in a grid comes from an image, and
#: a video-only listing renders as the black placeholder ``NO_VALID_MEDIA``
#: exists to prevent. Matches the ``media_type IN ('image','gif')`` the submit
#: route requires of its cover row.
COVER_MEDIA_TYPES = frozenset({"image", "gif"})

#: At or below this quantity a listing is low. The threshold lives here because
#: the client used to own a copy of it, which meant the number a merchant saw and
#: the number the server believed were two independent facts.
LOW_STOCK_THRESHOLD = 5

#: Codes that stop a buyer completing a purchase, whether or not they stop the
#: listing being published. `checkout_ready` is computed from this set here, so
#: no caller has to know it: clients render the boolean, never the rule.
CHECKOUT_BLOCKING = frozenset({
    MISSING_TITLE, MISSING_CATEGORY, NO_VALID_MEDIA, MISSING_PRICE,
    RESTRICTED_PRODUCT, OUT_OF_STOCK,
    # Unknown stock fails closed. We will not promise a stranger's card that
    # something is purchasable when nothing in the system knows whether it is.
    UNKNOWN_INVENTORY,
})

#: Listing types, in the five-type vocabulary of
#: ``services/marketplace_listing_types.py``, that have no stock concept. A
#: download does not run out, so an absent quantity on one of these is not a fact
#: about stock at all.
STOCKLESS_LISTING_TYPES = ("digital", "service", "event", "booking")

#: Legacy ``product_type`` values that predate the five-type vocabulary and are
#: stockless anyway -- today, just "course". DERIVED from checkout's own list
#: rather than written out here: an earlier version of this line was a guess
#: ("membership", "music", "ebook", read off an admin dropdown), and the guesses
#: were inert at best and wrong at worst, because checkout does not treat any of
#: them as stockless. Deriving it means this can never again claim something is
#: stockless that checkout will refuse to sell without stock.
#:
#: ``effective_listing_type`` resolves anything outside its own five names to
#: "physical", which is right for its purpose and would give every course in the
#: store an inventory state if used alone here.
LEGACY_STOCKLESS_PRODUCT_TYPES = tuple(
    sorted(set(_life.STOCKLESS_TYPES) - set(_types.LISTING_TYPES)))

#: Everything with no stock concept, under either vocabulary.
STOCKLESS_PRODUCT_TYPES = STOCKLESS_LISTING_TYPES + LEGACY_STOCKLESS_PRODUCT_TYPES


def _text(value: Any) -> str:
    return str(value or "").strip()


def _has_price(price_label: Any) -> bool:
    """Whether this listing carries a price at all.

    Not a price *parser*: ``bot.parse_price_label_to_cents`` is authoritative for
    the number, and no module in this package imports the monolith. This answers
    only the question readiness needs, and it answers it the same way checkout
    does — that parser maps both "" and a wordy label with no figures in it to
    zero cents, so neither spelling promises money and neither is a price here.
    """
    label = _text(price_label)
    return any(ch.isdigit() for ch in label)


def _has_cover(listing: dict, media: Optional[list]) -> bool:
    """Whether something will render where the buyer expects a photograph.

    Two sources, either alone sufficient. The cover columns count because the
    shared serializer draws from them even with no media rows attached, so
    demanding rows would print ``NO_VALID_MEDIA`` underneath a visible picture.

    Attached rows count only when at least one is an image. ``bool(media)`` was
    the previous test and it passed a listing whose only attachment was a video:
    media, but not a cover. The media type is defaulted to "image" when absent
    because ``pulse_marketplace_media_payload`` defaults it the same way, and a
    verdict that read a blank column as "not an image" would disagree with the
    thumbnail the seller is looking at.
    """
    if _text(listing.get("cover_image_url")) or _text(listing.get("media_url")):
        return True
    for row in media or []:
        kind = _text((row or {}).get("media_type")).lower() or "image"
        if kind in COVER_MEDIA_TYPES and _text((row or {}).get("media_url")):
            return True
    return False


def _restricted(listing: dict) -> bool:
    """Whether policy refuses this product, under either of the two authorities.

    ``approval_status`` is a moderator's verdict on this particular row.
    ``marketplace_goods_policy`` is the standing rule about the *category* and
    about prohibited signals in the copy, and it is what the submit route has
    always consulted before letting a listing through.

    Asked here so that both survive one question. Before this, readiness knew
    only the first and the submit route knew only the second, so a weapons
    listing read "Ready to publish" on the seller's row and was refused the
    moment they tapped it -- and a bulk publish, which had no second gate to
    fall back on, would have taken it live.
    """
    if _text(listing.get("approval_status")).lower() in {"rejected", "suspended"}:
        return True
    return _goods.evaluate(listing).get("decision") != "ALLOWED"


def _stockless_at_checkout(listing: dict) -> bool:
    """Ask the checkout decider whether stock matters for this row at all.

    ``marketplace_listing_lifecycle.inventory_available`` is what checkout calls
    (``bot.py:92730``). It refuses a NULL quantity for anything that tracks
    stock, so probing it with the quantity removed isolates exactly the type
    half of its decision: a ``True`` means checkout will not consult stock for
    this listing.

    Asked rather than copied. The alternative — restating ``product_type or
    listing_type`` and its ``STOCKLESS_TYPES`` here — is a forecast of another
    authority's decision, and every gap fixed in this area has been a forecast
    that drifted away from the decider it was forecasting.
    """
    return bool(_life.inventory_available(dict(listing, quantity=None), 1))


def _tracks_stock(listing: dict) -> bool:
    """Whether stock is a fact about this listing at all.

    Asks ``services/marketplace_listing_types.effective_listing_type``, which
    already owns the question "which of the five types is this row" and already
    knows how to read a legacy row. Deriving a second answer here would put a
    third precedence rule in the codebase next to that one and ``bot.py:19625``.

    Note which column is NOT consulted: ``delivery_type``. It is declared
    ``TEXT DEFAULT 'digital'``, as is ``product_type``, so almost every row in
    ``marketplace_listings`` carries 'digital' in both whatever it is actually
    selling. A first draft of this function matched on those two columns and
    therefore called every listing in the store stockless -- the whole inventory
    half of the verdict silently did nothing. ``listing_type`` is the column the
    modern write path sets, which is why the type authority reads it first.
    """
    listing_type = _types.effective_listing_type(
        listing.get("listing_type"), listing.get("product_type"))
    stockless_here = (listing_type in STOCKLESS_LISTING_TYPES
                      or _text(listing.get("product_type")).lower()
                      in LEGACY_STOCKLESS_PRODUCT_TYPES)
    # Both readings must agree before stock is dismissed as irrelevant.
    #
    # The two authorities read the type columns in opposite order -- checkout
    # asks for `product_type or listing_type`, the type authority prefers
    # `listing_type` -- so a row whose columns disagree gets two answers. Taking
    # only this module's answer produced a verdict promising a purchase that
    # checkout then refused (listing_type='digital' over product_type='physical'
    # with no quantity): a false clear, which is the exact defect this whole
    # module was written to remove, reintroduced one layer up.
    #
    # Requiring agreement fails closed in both directions of the disagreement:
    # the verdict never promises a sale checkout would refuse, and at worst
    # reports a stock state for something checkout would have sold regardless --
    # which shows the merchant a real inconsistency rather than hiding it.
    return not (stockless_here and _stockless_at_checkout(listing))


def _stock_codes(listing: dict) -> list:
    """Stock state as codes, keeping unknown separate from empty."""
    if not _tracks_stock(listing):
        return []
    raw = listing.get("quantity")
    # NULL from the database, and only NULL, means "not tracked". `Number(x || 0)`
    # is what destroyed this distinction on the client; the equivalent mistake
    # here would be `int(raw or 0)`, which folds None into a real zero.
    #
    # Note what is NOT used here: `_text(raw) == ""`. `_text` is built on
    # `value or ""`, so it reports an integer 0 as blank -- the same falsy-coercion
    # error as the client's, and it read a seller's truthful "none left" as "nobody
    # knows". The blank test therefore runs on the string spellings only, and an
    # integer reaches `int()` untouched.
    if raw is None:
        return [UNKNOWN_INVENTORY]
    if isinstance(raw, str) and raw.strip() == "":
        return [UNKNOWN_INVENTORY]
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        # An unparseable quantity is not zero either. We do not know.
        return [UNKNOWN_INVENTORY]
    if quantity <= 0:
        return [OUT_OF_STOCK]
    if quantity <= LOW_STOCK_THRESHOLD:
        return [LOW_STOCK]
    return []


def evaluate(listing: dict, *, media: Optional[list] = None) -> dict:
    """The one verdict. ``listing`` is a ``marketplace_listings`` row.

    ``media`` is the listing's attached media rows when the caller already has
    them. It is *additional* evidence, not a replacement: the row's own cover
    columns still count, because the shared serializer treats a listing with a
    ``cover_image_url`` and no media rows as having a cover, and a verdict that
    disagreed with the picture on the screen would be its own defect.

    Blockers prevent publication. Warnings are true of the listing but do not.
    Some warnings still prevent *checkout* — an out-of-stock listing is a normal
    thing to have published — which is why the two booleans are separate and why
    both are computed here rather than by whoever renders them.
    """
    blockers = []
    warnings = []

    if not _text(listing.get("title")):
        blockers.append(MISSING_TITLE)
    if not _text(listing.get("description")):
        blockers.append(MISSING_DESCRIPTION)
    if not _text(listing.get("category")):
        blockers.append(MISSING_CATEGORY)

    if not _has_cover(listing, media):
        blockers.append(NO_VALID_MEDIA)

    if not _has_price(listing.get("price_label")):
        blockers.append(MISSING_PRICE)

    if _restricted(listing):
        blockers.append(RESTRICTED_PRODUCT)

    # Stock never blocks publication. A merchant restocking a live listing is
    # the ordinary case, and unpublishing it under them would lose the listing's
    # ranking and reviews over a temporary fact.
    warnings.extend(_stock_codes(listing))

    publishable = not blockers
    checkout_ready = publishable and not any(
        code in CHECKOUT_BLOCKING for code in warnings)
    return {
        "publishable": publishable,
        "checkout_ready": checkout_ready,
        "blockers": blockers,
        "warnings": warnings,
        "summary": summary(blockers),
        "fixes": [fix(code) for code in blockers],
    }


# --- saying it in words -------------------------------------------------------
#
# The verdict carries its own prose because the alternative is a lookup table on
# every surface that renders it. There are already three (the seller row, the
# edit workspace, the bulk preview) and a fourth on the web dashboard, and a
# code with no entry in one of them renders as the raw CODE or as nothing.
#
# Codes stay in the payload. Clients that want to branch on a specific fault
# still can; they just no longer have to own the English.

#: What the merchant should go and do, in the imperative. Deliberately an
#: instruction and not a restatement of the fault: "MISSING_PRICE" tells a
#: seller what is wrong, "Add price" tells them what to do about it.
#:
#: Articles are dropped because these labels are read in two places with very
#: different budgets: on their own line in Ready to Sell, and concatenated onto
#: a store row as "2 things left · Add price + Add photo". "Add a price and a
#: photo" is the sentence a human would write and the one that wraps to three
#: lines on a phone. Two label sets would be one set that drifts.
FIXES = {
    MISSING_TITLE: "Add title",
    MISSING_DESCRIPTION: "Add description",
    MISSING_CATEGORY: "Choose category",
    NO_VALID_MEDIA: "Add photo",
    MISSING_PRICE: "Add price",
    RESTRICTED_PRODUCT: "Resolve policy review",
    OUT_OF_STOCK: "Restock",
    LOW_STOCK: "Running low",
    UNKNOWN_INVENTORY: "Set stock count",
}

#: Which section of the edit workspace fixes each code, so a blocker on the
#: Ready to Sell screen can be tapped and land somewhere useful. An unmapped
#: code sends the seller to the overview rather than nowhere.
SECTIONS = {
    MISSING_TITLE: "details",
    MISSING_DESCRIPTION: "details",
    MISSING_CATEGORY: "details",
    NO_VALID_MEDIA: "media",
    MISSING_PRICE: "pricing",
    RESTRICTED_PRODUCT: "policies",
    OUT_OF_STOCK: "inventory",
    LOW_STOCK: "inventory",
    UNKNOWN_INVENTORY: "inventory",
}


def fix(code: str) -> dict:
    """One blocker as something a seller can read and tap."""
    return {
        "code": code,
        "label": FIXES.get(code, "Review this listing"),
        "section": SECTIONS.get(code, "overview"),
    }


def summary(blockers: list) -> str:
    """The one-line count that goes on a store row.

    "2 things left" rather than "Draft — not published", which tells a seller
    the state they can already see and nothing about how to leave it.
    """
    count = len(blockers or [])
    if not count:
        return "Ready to publish"
    return f"{count} thing{'' if count == 1 else 's'} left"
