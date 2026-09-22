"""How repetitive the output actually was, as numbers rather than as an opinion.

Click-through rate cannot see the failure this pipeline exists to fix. A feed
that shows one excellent product two hundred times can hold a perfectly
respectable CTR — better than a varied one, often, because the winner is by
construction the highest-scoring item in the catalogue. Every ranking metric
scores a *placement*; repetition is a property of the *sequence*, and nothing
that looks at one placement at a time can detect it.

So these are sequence statistics, and they are deliberately blunt. An operator
looking at a surface that "feels stuck" needs to know which of four different
things is happening — one product recurring, one seller dominating, one category
crowding out the rest, or the same item chasing the user between surfaces — and
each has a different fix. A single composite "diversity score" would blend all
four into a number that moves without saying why.

Reading the numbers
-------------------

Every rate is a share of impressions in ``[0, 1]``, and none of them has a
"correct" value. Zero repeat rate over a long window is not a healthy feed, it
is a catalogue being walked exhaustively regardless of relevance; a user who
shops for one thing *should* see that category concentrated. What matters is the
shape over time and the comparison between surfaces — reels should be quieter
than marketplace on every one of these, because its policy says so, and if it
is not then the policy is not reaching the output.

``immediate_product_repeats`` is the exception: it has a correct value, and the
value is zero. The same product twice in a row is the specific thing the brief
names, and no amount of relevance justifies it.

On the ordering requirement
---------------------------

:func:`summarize` reads its input as a chronological sequence — "had this been
shown before" is answered by position, not by timestamp. Handing it rows in
``event_at DESC`` (the order every other read in this package uses, because
recency is what they want) silently inverts the cross-surface measure: the
*second* sighting becomes the first. :func:`from_events` therefore re-sorts
ascending, and its docstring says why so that the next reader does not "fix" it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from . import config, subject

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepetitionReport:
    """The seven numbers, plus the counts they were derived from."""

    impressions: int = 0
    unique_products_shown: int = 0
    unique_sellers_shown: int = 0
    unique_categories_shown: int = 0
    repeat_product_rate: float = 0.0
    repeat_seller_rate: float = 0.0
    cross_surface_repeat_rate: float = 0.0
    category_concentration: float = 0.0
    seller_concentration: float = 0.0
    new_product_exposure_rate: float = 0.0
    #: Times a product followed itself with nothing in between. Must be zero.
    immediate_product_repeats: int = 0
    immediate_seller_repeats: int = 0
    #: ``surface -> impressions``, so a caller can see whether one branch is
    #: carrying the whole sequence before reading anything into the rates.
    by_surface: Mapping[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "impressions": self.impressions,
            "unique_products_shown": self.unique_products_shown,
            "unique_sellers_shown": self.unique_sellers_shown,
            "unique_categories_shown": self.unique_categories_shown,
            "repeat_product_rate": round(self.repeat_product_rate, 4),
            "repeat_seller_rate": round(self.repeat_seller_rate, 4),
            "cross_surface_repeat_rate": round(self.cross_surface_repeat_rate, 4),
            "category_concentration": round(self.category_concentration, 4),
            "seller_concentration": round(self.seller_concentration, 4),
            "new_product_exposure_rate": round(self.new_product_exposure_rate, 4),
            "immediate_product_repeats": self.immediate_product_repeats,
            "immediate_seller_repeats": self.immediate_seller_repeats,
            "by_surface": dict(self.by_surface),
        }


EMPTY = RepetitionReport()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def summarize(
    events: Iterable[Mapping[str, Any]],
    *,
    new_product_ids: Optional[Iterable[Any]] = None,
) -> RepetitionReport:
    """Repetition statistics over one chronological sequence of impressions.

    Each event needs ``listing_id`` and, where known, ``seller_user_id``,
    ``category`` and ``surface``. Missing fields are counted as absent rather
    than as a distinct value: a row with no category must not invent a
    "" category that then dominates the concentration figure.

    ``new_product_ids`` is supplied by the caller rather than derived, because
    "new" is a judgement (newly listed? never before shown to anyone? shown
    fewer than n times?) and the caller is the only one that knows which it
    means. Absent it the exploration rate is reported as zero, which reads
    correctly as "not measured" only because it sits beside an impression count
    that shows the sequence was non-empty.
    """
    new_ids = {_int(value) for value in (new_product_ids or ())} - {0}

    impressions = 0
    product_first_seen: set[int] = set()
    seller_first_seen: set[int] = set()
    category_seen: set[str] = set()
    product_surfaces: dict[int, set[str]] = {}
    category_counts: dict[str, int] = {}
    seller_counts: dict[int, int] = {}
    by_surface: dict[str, int] = {}

    repeat_products = 0
    repeat_sellers = 0
    cross_surface_repeats = 0
    new_exposures = 0
    immediate_products = 0
    immediate_sellers = 0

    previous_product = 0
    previous_seller = 0

    for event in events:
        listing_id = _int(event.get("listing_id"))
        if not listing_id:
            continue
        impressions += 1
        seller_id = _int(event.get("seller_user_id"))
        category = _text(event.get("category"))
        surface = _text(event.get("surface"))

        if listing_id in product_first_seen:
            repeat_products += 1
        else:
            product_first_seen.add(listing_id)

        if seller_id:
            if seller_id in seller_first_seen:
                repeat_sellers += 1
            else:
                seller_first_seen.add(seller_id)
            seller_counts[seller_id] = seller_counts.get(seller_id, 0) + 1

        if category:
            category_seen.add(category)
            category_counts[category] = category_counts.get(category, 0) + 1

        if surface:
            by_surface[surface] = by_surface.get(surface, 0) + 1
            seen_on = product_surfaces.setdefault(listing_id, set())
            # A repeat on the *same* surface is already counted by
            # `repeat_product_rate`. This one is the distinct failure the brief
            # describes — feed, then reels, then messenger, same shoes — and
            # conflating the two would let a surface look cross-contaminated
            # when it was only repetitive.
            if seen_on and surface not in seen_on:
                cross_surface_repeats += 1
            seen_on.add(surface)

        if listing_id in new_ids:
            new_exposures += 1

        if listing_id == previous_product:
            immediate_products += 1
        if seller_id and seller_id == previous_seller:
            immediate_sellers += 1
        previous_product = listing_id
        previous_seller = seller_id

    if not impressions:
        return EMPTY

    total = float(impressions)
    return RepetitionReport(
        impressions=impressions,
        unique_products_shown=len(product_first_seen),
        unique_sellers_shown=len(seller_first_seen),
        unique_categories_shown=len(category_seen),
        repeat_product_rate=repeat_products / total,
        repeat_seller_rate=repeat_sellers / total,
        cross_surface_repeat_rate=cross_surface_repeats / total,
        category_concentration=(max(category_counts.values()) / total) if category_counts else 0.0,
        seller_concentration=(max(seller_counts.values()) / total) if seller_counts else 0.0,
        new_product_exposure_rate=new_exposures / total,
        immediate_product_repeats=immediate_products,
        immediate_seller_repeats=immediate_sellers,
        by_surface=by_surface,
    )


def from_events(
    cur,
    *,
    subject_ref: str = "",
    surface: str = "",
    since_seconds: Optional[int] = None,
    limit: int = 2000,
) -> RepetitionReport:
    """The same report, read back from the impression log.

    Rows are fetched newest-first — that is what the index supports — and then
    reversed, because :func:`summarize` reads position as time. Doing the
    reversal here rather than in the SQL keeps the query on
    ``idx_cd_impr_product_freq``; an ``ORDER BY event_at ASC`` over a bounded
    recent window would have to sort the whole window to find its start.

    ``subject_ref`` empty means "everybody", which is the operator's view of a
    surface. Per-viewer is the debugging view: "why does *this* person keep
    seeing the same thing" is a different question from "is this surface stuck",
    and the answers routinely disagree — a surface can look healthy in aggregate
    while every individual sees three products, because different people see
    different threes.

    Fails soft, like every read in this package: an unreadable log is a missing
    measurement, never an error on a path that also serves placements.
    """
    window = config.exposure_lookback_seconds() if since_seconds is None else int(since_seconds)
    clauses = ["e.event_at>?", "e.self_view=0"]
    params: list[Any] = [subject.window_start_iso(max(60, window))]
    if subject_ref:
        clauses.append("e.subject_ref=?")
        params.append(subject_ref)
    if surface:
        clauses.append("e.surface=?")
        params.append(surface)
    params.append(max(1, int(limit)))

    try:
        cur.execute(
            "SELECT e.listing_id AS listing_id, e.seller_user_id AS seller_user_id, "
            "e.surface AS surface, COALESCE(l.category,'') AS category "
            "FROM commerce_discovery_impression_events e "
            "LEFT JOIN marketplace_listings l ON l.id = e.listing_id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY e.event_at DESC LIMIT ?",
            tuple(params),
        )
        fetched = cur.fetchall() or []
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_METRICS_UNREADABLE", exc_info=True)
        return EMPTY

    rows = [row if isinstance(row, dict) else dict(row) for row in fetched]
    rows.reverse()
    return summarize(rows)
