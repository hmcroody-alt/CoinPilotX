"""What PulseDrop is allowed to *say* about a product, and why.

## The rule the whole module exists to enforce

Every claim maps to a fact already in the database. If the fact is not recorded,
the claim is not available — not softened, not approximated, not inferred from a
proxy. An automated account is believed more readily than a person, so the cost
of one invented claim is not one bad post; it is that nothing the account says
afterwards can be relied on.

## Why there is no DEAL label

The brief asks for one, and this schema cannot support it. A discount is a
relationship between two prices and ``marketplace_listings`` stores exactly one:
``price_label``. There is no compare-at column, no price history table, and
nothing in ``marketplace_listing_variants`` that records what a product used to
cost. Emitting DEAL would therefore mean either picking cheap products and
calling that a discount, or asserting a markdown that never happened. Both are
the thing this module refuses to do, so the label is absent rather than faked.
When a compare-at price exists, :data:`LABELS` is where it goes.

Same reasoning kills "Only 2 left!". ``quantity`` is real, so PulseDrop *could*
say it — but low stock is a fact about the seller's warehouse, not about the
product being good, and phrasing it as urgency is a sales tactic rather than a
description. PulseDrop describes.

## Why the labels are ordered by evidence and not by excitement

A product can be new, featured and popular at once. Picking the most flattering
true label is how an editorial voice becomes a marketing voice. So the order
below is strongest-evidence-first: a measured burst of recent activity outranks
a timestamp, which outranks a flag a human set once and never cleared.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, NamedTuple

from services.pulse_ai.content_policy import sanitize_automated_text

#: Label keys. These travel on the wire and are stored in
#: ``pulsedrop_publications.editorial_label``; the app maps them to translated
#: copy. The server never sends a translated string, because the server does not
#: know the reader's language and the app's i18n gate would reject the key-less
#: literal anyway.
TRENDING = "TRENDING"
NEW_DROP = "NEW_DROP"
POPULAR = "POPULAR"
TOP_PICK = "TOP_PICK"
DISCOVERY = "DISCOVERY"

#: Freshly published means "a member could plausibly not have seen this yet".
#: Three days, because the curator runs every two hours and a 24-hour window on
#: a catalog this size would mean the label almost never fires.
NEW_DROP_HOURS = 72

#: A burst. Deliberately small absolute numbers: this is a platform with tens of
#: eligible listings, and a threshold tuned for a large catalog would make
#: TRENDING unreachable, which is the same failure as making it meaningless.
TRENDING_MIN_RECENT = 3

#: Earned over the product's whole life rather than this week.
POPULAR_MIN_TOTAL = 10


class Label(NamedTuple):
    key: str
    #: The i18n key the app resolves. Named here rather than in the app so the
    #: server's label set and the catalog cannot drift.
    i18n_key: str
    #: English fallback, for the web surface and for logs. Not sent as display
    #: copy to the native app.
    fallback: str
    #: One line naming the fact behind the claim, recorded on the publication
    #: row. "Why did PulseDrop call this trending" must be answerable from the
    #: database a month later, when the counts have moved on.
    evidence: str


LABELS: dict[str, Label] = {
    TRENDING: Label(
        TRENDING,
        "commerce:pulsedrop.label.trending",
        "Trending",
        "recent saves and enquiries above the burst threshold",
    ),
    NEW_DROP: Label(
        NEW_DROP,
        "commerce:pulsedrop.label.newDrop",
        "New drop",
        "published within the new-drop window",
    ),
    POPULAR: Label(
        POPULAR,
        "commerce:pulsedrop.label.popular",
        "Popular",
        "lifetime saves, enquiries and paid orders above the popular threshold",
    ),
    TOP_PICK: Label(
        TOP_PICK,
        "commerce:pulsedrop.label.topPick",
        "Top pick",
        "flagged featured by a PulseSoc reviewer",
    ),
    DISCOVERY: Label(
        DISCOVERY,
        "commerce:pulsedrop.label.discovery",
        "Discover",
        "no stronger claim is supported by recorded data",
    ),
}

#: Evaluation order. See the module docstring.
LABEL_ORDER: tuple[str, ...] = (TRENDING, NEW_DROP, POPULAR, TOP_PICK, DISCOVERY)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def published_at(listing: Mapping[str, Any]) -> str:
    """The listing's effective publication time, falling back down the chain.

    ``published_at`` is only written by paths that went through the publish
    route; dropship imports and legacy rows have ``approved_at`` or nothing but
    ``created_at``. Public because the ranker needs the same answer the label
    does — two definitions of "when did this appear" would let a listing be a
    new drop and score zero for freshness in the same breath.
    """
    for key in ("published_at", "approved_at", "created_at"):
        value = str(listing.get(key) or "").strip()
        if value:
            return value
    return ""


def is_new_drop(listing: Mapping[str, Any], now: datetime | None = None) -> bool:
    stamp = published_at(listing)
    if not stamp:
        return False
    cutoff = ((now or datetime.utcnow()) - timedelta(hours=NEW_DROP_HOURS)).isoformat(
        timespec="seconds"
    )
    # String comparison on ISO-8601, which is what every timestamp in this
    # schema is. Parsing would be more code and would have to cope with the
    # three different precisions already in the column.
    return stamp >= cutoff


def recent_engagement(listing: Mapping[str, Any]) -> int:
    return _int(listing.get("recent_save_count")) + _int(listing.get("recent_interest_count"))


def lifetime_engagement(listing: Mapping[str, Any]) -> int:
    return (
        _int(listing.get("save_count"))
        + _int(listing.get("interest_count"))
        # A paid order is the strongest thing a member can say about a product,
        # so it counts for more than a bookmark. Three is a judgement, not a
        # measurement; it is here rather than inline so it can be argued with.
        + _int(listing.get("paid_order_count")) * 3
    )


def classify(listing: Mapping[str, Any], now: datetime | None = None) -> Label:
    """The strongest claim this listing's recorded data actually supports."""
    if recent_engagement(listing) >= TRENDING_MIN_RECENT:
        return LABELS[TRENDING]
    if is_new_drop(listing, now):
        return LABELS[NEW_DROP]
    if lifetime_engagement(listing) >= POPULAR_MIN_TOTAL:
        return LABELS[POPULAR]
    if _int(listing.get("featured")) > 0:
        return LABELS[TOP_PICK]
    return LABELS[DISCOVERY]


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

#: Hard ceiling on the caption. Not a feed constraint — ``pulse_posts.content``
#: is TEXT — but a curator that writes paragraphs is writing an advert. The
#: caption's job is to name the product, its price and its seller, and to get
#: out of the way of the picture.
CAPTION_MAX = 220
TITLE_MAX = 90

#: Words a hashtag may not be made of. Mostly the residue of category columns
#: and dropship imports, which would otherwise produce ``#general`` on half the
#: catalog and ``#uncategorized`` on the rest.
_HASHTAG_STOPWORDS = frozenset(
    {"general", "other", "misc", "miscellaneous", "uncategorized", "default", "none", "n/a"}
)

#: Four, and no more. Beyond this a caption reads as reach-hunting rather than
#: as description, and the app's tag row wraps.
HASHTAG_MAX = 4


def clean_title(listing: Mapping[str, Any]) -> str:
    """The seller's title, trimmed — never rewritten.

    Truncation is on a word boundary with an ellipsis, because cutting mid-word
    reads as a bug and cutting mid-word in a language without spaces reads as
    nothing at all; the boundary search falls back to a hard cut for exactly
    that case.
    """
    title = sanitize_automated_text(str(listing.get("title") or "").strip())
    title = " ".join(title.split())
    if len(title) <= TITLE_MAX:
        return title
    cut = title[:TITLE_MAX].rstrip()
    space = cut.rfind(" ")
    if space > TITLE_MAX // 2:
        cut = cut[:space].rstrip()
    return f"{cut}…"


def price_text(listing: Mapping[str, Any]) -> str:
    """The stored price label, verbatim.

    Not reformatted. ``marketplace_normalize_price_label`` already rebuilt this
    string from the integer minor units the buyer is actually charged, so it is
    the one price representation in the product that cannot disagree with
    checkout. Re-deriving it here from ``price_label`` plus ``currency`` would
    create a second formatter, and the first thing two formatters do is disagree
    about a currency neither author tested.
    """
    return " ".join(str(listing.get("price_label") or "").split())


def seller_text(listing: Mapping[str, Any]) -> str:
    """The store name, which is the identity a buyer transacts with."""
    try:
        from services import marketplace_seller_identity as seller_identity

        return seller_identity.display_store_name(listing) or ""
    except Exception:
        return str(listing.get("seller_store_name") or "").strip()


def hashtags(listing: Mapping[str, Any]) -> list[str]:
    """Tags derived from the listing's own category fields. Never invented.

    ``tags_json`` is deliberately not read. It is seller-authored free text, so
    republishing it under PulseDrop's name would let a seller put words in the
    platform's mouth — which is a different risk from a seller's own post
    carrying their own tags.
    """
    out: list[str] = []
    seen: set[str] = set()
    for source in (listing.get("category"), listing.get("subcategory")):
        token = _hashtag_token(source)
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    for token in ("pulsesocmarketplace", "pulsedrop"):
        if token not in seen and len(out) < HASHTAG_MAX:
            seen.add(token)
            out.append(token)
    return out[:HASHTAG_MAX]


def _hashtag_token(value: Any) -> str:
    text = "".join(ch for ch in str(value or "").lower() if ch.isalnum() or ch.isspace())
    text = "".join(text.split())
    if not text or len(text) < 3 or text in _HASHTAG_STOPWORDS:
        return ""
    return text[:24]


def caption(listing: Mapping[str, Any], label: Label, *, include_hashtags: bool = True) -> str:
    """The post body: the product's name and its tags. Nothing else.

    ## Why the price is not in here, even though it obviously belongs in a
    ## commerce post

    ``pulse_posts.content`` is written once and read for years. Price, stock,
    availability and the store's own name are live facts that change without
    anyone touching the post — a seller re-prices on Tuesday and every PulseDrop
    caption quoting the old number becomes a lie the platform published under a
    verified badge. There is no update path that could fix it either, because
    rewriting members' visible history to match a database is worse than the
    stale number.

    So the split is structural and it is the same split the Reels work needs:
    the post carries the *reference*, and price, stock, seller and CTA are
    hydrated live from the listing every time the card is rendered, by the
    PulseDrop hydration layer and not by this function.
    The caption therefore holds only what is safe to freeze: the product's name,
    which is what the post is *about*, and tags derived from its category.

    A title can go stale too. That is survivable in a way a price is not: a
    renamed product is still the product, and the card underneath shows the
    current name.

    The label is not repeated in the caption either. It is rendered as a chip by
    the client from ``editorial_label``, and saying "Trending" in prose as well
    makes one fact look like two — and freezes a claim whose evidence expires.
    """
    lines: list[str] = []
    title = clean_title(listing)
    if title:
        lines.append(title)
    if include_hashtags:
        tags = " ".join(f"#{token}" for token in hashtags(listing))
        if tags:
            lines.append(tags)
    body = "\n".join(lines).strip()
    return body[:CAPTION_MAX].rstrip()


def accessibility_text(listing: Mapping[str, Any], label: Label) -> str:
    """One sentence a screen reader can read in place of the card.

    Ordered the way the card is ordered — label, product, price, seller — so a
    member using VoiceOver and a member looking at the screen are told the same
    things in the same sequence.

    Built at hydration time from live listing data, never stored, which is the
    only reason it is allowed to contain the price when :func:`caption` is not.
    English: it is the web surface's string and the native app's last-resort
    fallback. The app composes its own from ``label.i18n_key`` plus the same
    live fields, because this function cannot know the reader's language.
    """
    parts = [label.fallback, clean_title(listing)]
    price = price_text(listing)
    if price:
        parts.append(price)
    store = seller_text(listing)
    if store:
        parts.append(f"sold by {store}")
    return ". ".join(part for part in parts if part).strip()
