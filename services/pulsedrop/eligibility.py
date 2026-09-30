"""May PulseDrop promote this product? Composed from existing policy, not a copy.

## Why this module does not decide what "published" means

``marketplace_listing_lifecycle`` already owns that, in one table of rules with
three consumers that had drifted apart once. A sixth opinion about whether a
listing is purchasable — held by an automated account that amplifies whatever it
picks — is the worst possible place to reintroduce the drift. So the buyer gate
here *is* ``public_sql`` and ``is_public``, unchanged, and everything below them
is additive: conditions that make a publishable product a bad thing to *feature*,
which is a different and stricter question than whether someone may buy it.

## Why the extra conditions run in Python and the buyer gate runs in SQL

The buyer gate is a set membership test over indexed columns and its answer is
never interesting: a listing that fails it is not a candidate and never was.
The PulseDrop conditions are the opposite — "PulseDrop has not posted in two
days" is answered by *which* condition is rejecting everything, so each one has
to produce a countable reason code. Evaluating them row-by-row in Python over a
few hundred already-filtered rows costs nothing and yields the histogram that
``pulsedrop_runs.rejected_json`` exists to store.

## Why amplification is stricter than sale

A seller's listing is theirs. A PulseDrop post is PulseSoc's editorial voice
pointing at it, in the feed, under a verified badge, to people who did not search
for it. An open report that a moderator has not yet read is not enough to pull a
listing off sale — the report may be nonsense — but it is more than enough to
stop the platform from promoting it while the question is open. Same reasoning
for a listing that has never been priced, whose title is machine residue, or
whose copy trips the text moderator: none of those are grounds for takedown, all
of them are grounds for picking a different product.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Mapping

from services import discovery_visibility, marketplace_listing_lifecycle
from services import marketplace_seller_identity as seller_identity

log = logging.getLogger(__name__)

#: Reason codes. Stable strings: they are keys in ``rejected_json``, which is
#: read by the ops surface and by anyone diagnosing a silent PulseDrop, so
#: renaming one silently resets its history to zero.
NOT_PUBLIC = "not_public"
OPEN_REPORT = "open_report"
SAFETY_FLAGGED = "safety_flagged"
NO_IMAGE = "no_image"
UNPRICED = "unpriced"
NO_TITLE = "no_title"
TEXT_MODERATION = "text_moderation"
SELLER_IS_SYSTEM = "seller_is_system"

#: Ordered, because the histogram should name the *first* thing wrong with a
#: listing rather than an arbitrary one. Cheapest and most fundamental first.
REASON_ORDER: tuple[str, ...] = (
    NOT_PUBLIC,
    SELLER_IS_SYSTEM,
    OPEN_REPORT,
    SAFETY_FLAGGED,
    NO_TITLE,
    UNPRICED,
    NO_IMAGE,
    TEXT_MODERATION,
)

#: Above this, ``marketplace_listings.safety_score`` means a reviewer's tooling
#: scored the listing risky even though it is approved for sale. Deliberately
#: not zero: the column defaults to 0 and is populated inconsistently, so
#: treating any non-zero value as a flag would reject the whole catalog the
#: first time a scoring pass runs.
SAFETY_SCORE_CEILING = 60

#: Order states that mean money actually moved. Taken from
#: ``dashboard_economy_command_center``, which is the module that already had to
#: decide this; PulseDrop uses the narrower "revenue" set rather than the
#: "active" set, because a pending order is a signal about a buyer's intent and
#: not yet about a product's quality.
PAID_ORDER_STATES = ("paid", "completed", "fulfilled")

_LISTING_COLUMNS = (
    "id", "seller_user_id", "title", "short_description", "description", "category",
    "subcategory", "price_label", "currency", "quantity", "product_type", "listing_type",
    "listing_metadata_json", "status", "approval_status", "safety_score", "safety_flags_json",
    "featured", "cover_image_url", "gallery_json", "video_url", "media_url", "tags_json",
    "created_at", "updated_at", "published_at", "approved_at",
)


def _columns(alias: str = "l") -> str:
    return ", ".join(f"{alias}.{name}" for name in _LISTING_COLUMNS)


def candidate_sql(extra_where: str = "") -> str:
    """The catalog read, with an optional extra predicate on the listing.

    ``extra_where`` is composed in rather than patched on by the caller because
    this statement contains four correlated subqueries, each with its own
    ``WHERE``; a caller splicing text into the first one it finds edits a
    subquery and not the gate. Only this module supplies the argument, and only
    ever a literal.

    The engagement counts are correlated subqueries rather than joins with a
    GROUP BY, for two reasons: the eligible catalog is tens of rows, so the plan
    does not matter; and a LEFT JOIN + GROUP BY over four side tables is the
    shape that silently multiplies rows when two of them match, which here would
    inflate exactly the numbers the ranker trusts.

    Each engagement signal is counted twice, all-time and inside a window. They
    are different facts and the editorial layer needs both: a product with 40
    lifetime saves and none this week is popular, not trending, and labelling it
    TRENDING would be the first dishonest thing PulseDrop ever said. Nothing here
    is derived from a signal the platform does not already record — there is no
    listing-view table in this schema, so there is no view-based signal.

    Bind order is fixed and is the reason :func:`_execute` exists: two window
    cutoffs, then any ``extra_where`` binds, then the limit. The cutoffs come
    first because the subqueries are in the SELECT list.
    """
    paid = ", ".join(f"'{state}'" for state in PAID_ORDER_STATES)
    extra = f"{extra_where} AND " if extra_where else ""
    return f"""
        SELECT {_columns('l')},
               COALESCE(ms.status,'missing') AS seller_status,
               {seller_identity.store_name_select('ms')},
               COALESCE(u.username,'') AS seller_username,
               COALESCE(u.display_name,'') AS seller_account_name,
               (SELECT COUNT(*) FROM marketplace_saved_products sp
                 WHERE sp.listing_id=l.id) AS save_count,
               (SELECT COUNT(*) FROM marketplace_saved_products sp
                 WHERE sp.listing_id=l.id
                   AND COALESCE(sp.created_at,'') >= ?) AS recent_save_count,
               (SELECT COUNT(*) FROM marketplace_buyer_interest bi
                 WHERE bi.listing_id=l.id) AS interest_count,
               (SELECT COUNT(*) FROM marketplace_buyer_interest bi
                 WHERE bi.listing_id=l.id
                   AND COALESCE(bi.created_at,'') >= ?) AS recent_interest_count,
               (SELECT COUNT(*) FROM marketplace_orders mo
                 WHERE mo.listing_id=l.id
                   AND LOWER(COALESCE(mo.status,'')) IN ({paid})) AS paid_order_count,
               (SELECT COUNT(*) FROM marketplace_reports mr
                 WHERE mr.listing_id=l.id
                   AND LOWER(COALESCE(mr.status,''))='open') AS open_report_count
        FROM marketplace_listings l
        LEFT JOIN users u ON u.user_id=l.seller_user_id
        LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id
        WHERE {extra}{marketplace_listing_lifecycle.public_sql('l', 'ms')}
          AND {discovery_visibility.discovery_visible_sql('u')}
        ORDER BY COALESCE(l.published_at, l.approved_at, l.created_at) DESC, l.id DESC
        LIMIT ?
    """


#: How far back "recent" reaches for the trend signals. A week, because the
#: curator wakes every two hours and a shorter window on a catalog this size
#: would make the label a coin flip on one member's bookmark.
TREND_WINDOW_HOURS = 168


def trend_cutoff(now: datetime | None = None) -> str:
    return ((now or datetime.utcnow()) - timedelta(hours=TREND_WINDOW_HOURS)).isoformat(
        timespec="seconds"
    )


def _execute(cur, extra_where: str, extra_params: tuple, limit: int, cutoff: str) -> list[dict]:
    cur.execute(
        candidate_sql(extra_where),
        (cutoff, cutoff, *extra_params, int(limit)),
    )
    rows = [dict(row) for row in cur.fetchall() or []]
    media = media_for_listings(cur, [row.get("id") for row in rows])
    for row in rows:
        row["media_rows"] = media.get(int(row.get("id") or 0), [])
    return rows


def fetch_candidates(cur, limit: int, *, now: datetime | None = None) -> list[dict]:
    """Publishable listings, newest first, each with its media rows attached.

    Returns rows that passed the *buyer* gate only. Nothing here is eligible for
    PulseDrop yet; :func:`rejection_reason` decides that, per row, so that the
    ones it turns down can be counted.
    """
    return _execute(cur, "", (), int(limit), trend_cutoff(now))


def media_for_listings(cur, listing_ids) -> dict[int, list[dict]]:
    """Moderated product media per listing, via the marketplace's own loader.

    Imported from ``bot`` rather than reimplemented because the filter that
    matters is the moderation one — ``NOT IN ('rejected','removed','blocked',
    'blocked_review')`` — and a second copy of that tuple is a second place for
    a newly added rejection state to be forgotten. A rejected product photo
    reaching a PulseDrop Reel is precisely the failure that would justify
    switching the account off.
    """
    try:
        import bot

        return bot.pulse_marketplace_media_rows_for_listings(cur, listing_ids)
    except Exception:
        log.warning("pulsedrop_media_load_failed", exc_info=True)
        return {}


def image_urls(listing: Mapping[str, Any]) -> list[str]:
    """Every distinct still image for this listing, cover first.

    Order is the order the composer will show them in, so the cover leads: it is
    the one frame the seller chose deliberately.
    """
    urls: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        url = str(value or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    add(listing.get("cover_image_url"))
    for row in listing.get("media_rows") or []:
        item = dict(row or {})
        if str(item.get("media_type") or "").lower() in {"image", "gif", "photo"}:
            add(item.get("media_url"))
    add(listing.get("media_url"))
    for value in _gallery_urls(listing.get("gallery_json")):
        add(value)
    return urls


def video_source(listing: Mapping[str, Any]) -> str:
    """The seller's own product video, or ``""``.

    ``marketplace_product_media`` is preferred over the ``video_url`` column
    because a media row has been through moderation and carries dimensions; the
    column is a bare string a seller pasted.
    """
    for row in listing.get("media_rows") or []:
        item = dict(row or {})
        if str(item.get("media_type") or "").lower() == "video" and item.get("media_url"):
            return str(item.get("media_url"))
    return str(listing.get("video_url") or "").strip()


def _gallery_urls(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if str(item or "").strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item or "").strip()]
    return []


def _has_safety_flags(listing: Mapping[str, Any]) -> bool:
    try:
        score = int(listing.get("safety_score") or 0)
    except (TypeError, ValueError):
        score = 0
    if score > SAFETY_SCORE_CEILING:
        return True
    raw = str(listing.get("safety_flags_json") or "").strip()
    if not raw or raw in {"[]", "{}", "null"}:
        return False
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        # Unparseable is not the same as empty, and the safe reading of "the
        # safety column contains something we cannot understand" is to skip the
        # listing rather than to feature it.
        return True
    return bool(parsed)


def _text_moderation_blocked(listing: Mapping[str, Any]) -> bool:
    """The platform's own text gate, run over the copy PulseDrop would republish.

    Only ``blocked`` rejects. ``needs_review`` is what the marketplace's own
    moderation queue is for, and a listing already approved for sale whose
    description merely trips a review term should not also be invisible to
    discovery — that would make PulseDrop a second, unaccountable moderator.
    """
    try:
        from services import ai_moderation_core

        text = " ".join(
            str(listing.get(key) or "")
            for key in ("title", "short_description", "description")
        ).strip()
        if not text:
            return False
        verdict = ai_moderation_core.moderate_text(text, "pulsedrop")
        return str((verdict or {}).get("status") or "") == "blocked"
    except Exception:
        # A moderator that cannot run has not approved anything. PulseDrop is
        # optional; skipping a listing costs nothing and publishing an
        # unmoderated one costs the account.
        log.warning("pulsedrop_text_moderation_failed", exc_info=True)
        return True


def rejection_reason(listing: Mapping[str, Any], *, system_user_ids=()) -> str:
    """``""`` when PulseDrop may feature this listing, else the first reason not to.

    ``system_user_ids`` carries PulseDrop's own id and any other automated
    account: an automated curator promoting an automated account's listing is a
    closed loop that says nothing about what members are making.
    """
    if not marketplace_listing_lifecycle.is_public(listing):
        return NOT_PUBLIC
    try:
        seller_user_id = int(listing.get("seller_user_id") or 0)
    except (TypeError, ValueError):
        seller_user_id = 0
    if not seller_user_id or seller_user_id in set(system_user_ids or ()):
        return SELLER_IS_SYSTEM
    try:
        if int(listing.get("open_report_count") or 0) > 0:
            return OPEN_REPORT
    except (TypeError, ValueError):
        pass
    if _has_safety_flags(listing):
        return SAFETY_FLAGGED
    if not str(listing.get("title") or "").strip():
        return NO_TITLE
    # A blank label is the dropship-import default and renders as no price at
    # all. "Free" and "Request access" are things a seller chose to say, so they
    # pass: the card shows the seller's words, and PulseDrop never invents a
    # number. See ``marketplace_normalize_price_label``.
    if not str(listing.get("price_label") or "").strip():
        return UNPRICED
    if not image_urls(listing):
        return NO_IMAGE
    if _text_moderation_blocked(listing):
        return TEXT_MODERATION
    return ""


def partition(listings, *, system_user_ids=()) -> tuple[list[dict], dict[str, int]]:
    """Split candidates into (eligible, reason histogram).

    The histogram is returned even when everything passed — an all-zero run is
    still evidence, and ``{}`` on a tick that evaluated 40 listings reads very
    differently from ``{}`` on a tick that evaluated none.
    """
    eligible: list[dict] = []
    rejected: dict[str, int] = {}
    for listing in listings or []:
        reason = rejection_reason(listing, system_user_ids=system_user_ids)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        eligible.append(dict(listing))
    return eligible, rejected


def revalidate(
    cur, listing_id: int, *, system_user_ids=(), now: datetime | None = None
) -> tuple[dict, str]:
    """Re-read one listing and re-run the gate, for use inside the publish txn.

    The gap between ranking and publishing is small and entirely sufficient: a
    seller can sell the last unit, a moderator can suspend the store, and the
    catalog read that chose this product happened before either. Returns
    ``(listing, "")`` when it is still safe to publish and ``({}, reason)``
    otherwise — ``reason`` being a rejection code, or ``"vanished"`` when the
    row no longer satisfies the buyer gate at all.
    """
    listing_id = int(listing_id or 0)
    if not listing_id:
        return {}, "vanished"
    rows = _execute(cur, "l.id=?", (listing_id,), 1, trend_cutoff(now))
    if not rows:
        return {}, "vanished"
    row = rows[0]
    reason = rejection_reason(row, system_user_ids=system_user_ids)
    if reason:
        return {}, reason
    return row, ""
