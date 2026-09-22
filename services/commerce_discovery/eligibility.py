"""Which listings may be *pushed at* a buyer who did not ask for them.

This is a higher bar than "may be sold", and keeping the two apart is the whole
point of the module.

``marketplace_listing_lifecycle.public_sql()`` already answers *may be sold*:
seller approved, seller named, listing published and moderation-approved, stock
available. Every buyer surface in PulseSoc uses it and this one does too — a
recommendation for something a buyer cannot then buy is the worst possible first
impression of the feature, so discovery is a strict subset of purchasable and
never a parallel definition of it.

But purchasable is not sufficient. A listing a buyer reached by searching for it
has already survived the buyer's own judgement; a listing we insert into
somebody's feed unprompted has not. So discovery adds:

* **a cover image** — the card is mostly image; a listing without one renders as
  a grey rectangle with a price, which reads as broken rather than as an offer,
* **a resolvable price** — ``price_label`` is free text and defaults to
  "Request access". A card that cannot show a number is not an offer either,
* **a moderation-clean record** — ``approval_status='approved'`` means a
  moderator said yes; it does not mean nothing has been flagged since. A listing
  carrying a live ``moderation_reason`` is held back from push even while it
  stays purchasable to someone who navigates to it directly,
* **a listing below the risk ceiling**, where it has been scored,
* **a seller below the risk ceiling**.

The asymmetry is deliberate and worth stating plainly: a failing gate here
removes a listing from *recommendations only*. It never takes a listing off
sale, never notifies the seller, and never touches moderation state. Discovery
is allowed to be more conservative than commerce; it is never allowed to be
less.

Two-stage evaluation
--------------------

The cheap, indexable conditions are pushed into SQL (:func:`candidate_sql`) so
the database is not asked to return the whole catalogue. The judgemental ones
run in Python (:func:`gate`) over the returned rows, because ``safety_score`` is
nullable with a meaning ("never scored") that is neither "clean" nor "risky",
and because a SQL expression is a poor place to keep a rule someone will need to
argue with — this one has already been wrong once in the direction that matters.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from services import marketplace_listing_lifecycle as listing_lifecycle

LOGGER = logging.getLogger(__name__)


#: Listings at or above this on the 0–100 risk scale are not pushed.
#:
#: The column this reads is named ``safety_score`` and holds the opposite: it is
#: written from ``revenue_safety_engine.marketplace_listing_review()["risk_score"]``
#: (bot.py, on publish and on resume), where 0 is clean and 100 is worst. The
#: name is the trap. Reading it as a safety score inverts the gate twice over —
#: it rejects every clean listing, which empties discovery in a way that looks
#: like an empty catalogue rather than a bug, and it admits only the listings
#: the scorer wanted a human to look at.
#:
#: 30 is the scorer's own ``needs_review`` threshold. Anything it would route to
#: a moderator is not something to push at a stranger, even while it stays
#: perfectly purchasable to someone who navigates to it.
MAX_LISTING_RISK = 30

#: Sellers at or above this ``risk_score`` are not promoted. The column is
#: internal and never leaves the server; it is read here and discarded.
MAX_SELLER_RISK = 60

#: Price labels that parse to nothing but are not *errors* — the seller chose
#: not to publish a number. Excluded from push, not from sale.
_UNPRICED_LABELS = frozenset({"", "request access", "contact", "contact seller", "enquire", "inquire"})

#: Reasons a listing is not eligible, as stable codes. Surfaced only in admin
#: observability — a buyer never sees these, and a seller sees a friendlier
#: derivation in their own dashboard.
INELIGIBLE_CODES = (
    "not_purchasable",   # fails the shared publication gate
    "no_cover_image",
    "no_resolvable_price",
    "moderation_flagged",
    "listing_risk",
    "seller_risk",
    "suppressed",        # this viewer told us not to
    "over_exposed",      # frequency cap
)


def candidate_sql(alias: str = "l", seller_alias: str = "ms") -> str:
    """SQL conditions every candidate must satisfy, as one AND-able clause.

    Composed from ``public_sql`` rather than re-deriving it. If the publication
    rules change — and they have, four times — discovery inherits the change
    instead of drifting into a second, staler definition of purchasable.

    The two added clauses are the ones that index well. Everything judgemental
    is left to :func:`gate`.
    """
    return (
        f"{listing_lifecycle.public_sql(alias, seller_alias)} "
        # A cover image is the card. NULLIF collapses the empty-string case,
        # which is far more common in this table than a true NULL.
        f"AND NULLIF(TRIM(COALESCE({alias}.cover_image_url,'')),'') IS NOT NULL "
        f"AND NULLIF(TRIM(COALESCE({alias}.price_label,'')),'') IS NOT NULL"
    )


#: Columns a candidate row must carry for :func:`gate` to judge it. Selected
#: explicitly rather than with ``SELECT *`` because the publication rules return
#: "unknown" for an unprojected column and pass some of them by default — an
#: abbreviated projection would silently widen the gate.
CANDIDATE_COLUMNS = (
    "l.id", "l.seller_user_id", "l.title", "l.short_description", "l.description",
    "l.category", "l.subcategory", "l.price_label", "l.currency", "l.quantity",
    "l.product_type", "l.listing_type", "l.listing_metadata_json",
    "l.cover_image_url", "l.gallery_json", "l.video_url",
    "l.created_at", "l.updated_at", "l.featured", "l.delivery_type",
    "l.status", "l.approval_status",
    "l.safety_score", "l.moderation_reason",
    "COALESCE(ms.status,'missing') AS seller_status",
    "COALESCE(ms.display_name,'') AS seller_store_name",
    "COALESCE(ms.business_name,'') AS seller_business_name",
    "COALESCE(ms.verification_status,'unverified') AS seller_verification_status",
    "COALESCE(ms.risk_score,0) AS seller_risk_score",
    "COALESCE(ms.created_at,'') AS seller_created_at",
    "COALESCE(u.username,'') AS seller_username",
)


def candidate_projection() -> str:
    return ", ".join(CANDIDATE_COLUMNS)


def _text(value: Any) -> str:
    return str(value or "").strip()


def has_cover_image(listing: Mapping[str, Any]) -> bool:
    url = _text(listing.get("cover_image_url"))
    # A relative path is fine (the CDN prefix is applied at serialization); an
    # obviously-empty placeholder is not.
    return bool(url) and url.lower() not in {"null", "none", "undefined"}


def resolvable_price(listing: Mapping[str, Any], parse_price) -> Optional[tuple[int, str]]:
    """``(minor_units, currency)``, or ``None`` when no number can be shown.

    ``parse_price`` is injected rather than imported so this module stays free
    of ``bot`` — the parser lives on the Flask monolith, and importing it at
    module scope would make every test of this file boot 111k lines.
    """
    label = _text(listing.get("price_label")).lower()
    if label in _UNPRICED_LABELS:
        return None
    try:
        amount, currency = parse_price(
            listing.get("price_label") or "", listing.get("currency") or "USD"
        )
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_PRICE_PARSE_FAILED listing=%s", listing.get("id"))
        return None
    amount = int(amount or 0)
    if amount <= 0:
        return None
    return amount, (currency or "USD")


def moderation_clean(listing: Mapping[str, Any]) -> bool:
    """No *live* moderation flag on an otherwise-approved listing.

    ``moderation_reason`` is set when something was flagged and cleared back to
    empty when resolved, so a non-empty value on an approved listing means a
    flag was raised after approval. Purchasable, but not something to push.
    """
    return not _text(listing.get("moderation_reason"))


def listing_risk_ok(listing: Mapping[str, Any]) -> bool:
    """Whether the listing's own risk score is below the ceiling.

    Reads ``safety_score``, which holds a *risk* score — see
    :data:`MAX_LISTING_RISK`. An unscored listing passes: most of the catalogue
    predates the scorer, and an unparseable value is a data problem rather than
    evidence about the product.
    """
    raw = listing.get("safety_score")
    if raw in (None, ""):
        return True
    try:
        return int(raw) < MAX_LISTING_RISK
    except (TypeError, ValueError):
        return True


def seller_ok(listing: Mapping[str, Any]) -> bool:
    raw = listing.get("seller_risk_score")
    try:
        return int(raw or 0) < MAX_SELLER_RISK
    except (TypeError, ValueError):
        return True


def gate(listing: Mapping[str, Any], parse_price) -> str:
    """``""`` when this listing may be pushed, else the first failing code.

    Order matters only for reporting: the earliest code is the one an operator
    is shown, so the shared publication gate is checked first — "not
    purchasable" explains every downstream failure and none of them explains it.
    """
    if not listing_lifecycle.is_public(listing):
        return "not_purchasable"
    if not has_cover_image(listing):
        return "no_cover_image"
    if resolvable_price(listing, parse_price) is None:
        return "no_resolvable_price"
    if not moderation_clean(listing):
        return "moderation_flagged"
    if not listing_risk_ok(listing):
        return "listing_risk"
    if not seller_ok(listing):
        return "seller_risk"
    return ""


def is_eligible(listing: Mapping[str, Any], parse_price) -> bool:
    return gate(listing, parse_price) == ""
