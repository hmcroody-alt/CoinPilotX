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

Where these are computed
------------------------

Three entry points, and the difference between them is what window they describe.

:func:`summarize` takes a sequence and is the primitive the other two are built
on. :func:`from_events` reads the impression log — the operator's view, "is this
surface stuck". :func:`from_state` derives the same numbers from the
:class:`exposure.ExposureState` the serve path has *already loaded* for its
cooldowns, which makes it free: no query, and no way for the metrics to describe
a different window than the one the ranker actually ranked against.

``from_state`` is the one that runs in production, once per served request, via
:func:`observe`. That matters more than it sounds. Before it existed this module
had no caller outside a test, so every anti-repetition control in the package was
enforced and *unobserved* — which is how a diversity term that had been dead
since it was written survived: the tests asserted it was computed, nothing
asserted it reached the output, and no number in production could tell anyone
apart. Measurement that only runs under test measures the test.

:func:`observe` reads the window as it stood at the *start* of the request, since
that is when the state was frozen. So a repetitive response is alerted on by the
request after it, not by itself. That is a one-request lag on a window measured in
days, and closing it would mean a second exposure read per request to observe
something the next read sees anyway.

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

from . import config, subject, taxonomy

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

    # -- aisle-level concentration -------------------------------------------
    #: Categories fold to a canonical path (``taxonomy.category_key``); an aisle
    #: is that path's first segment (``taxonomy.segment_root``). The pair is
    #: reported separately because they diagnose different faults: a viewer shown
    #: eleven different *leaves* of one aisle has a low category concentration
    #: and a high segment one, and the second is the number that matches what the
    #: feed looks like to them.
    unique_segments_shown: int = 0
    segment_concentration: float = 0.0

    # -- the raw maxima the rates above are shares of -------------------------
    #: Kept because a rate alone cannot be compared against a cap. The caps in
    #: ``router`` are integer counts, so checking "did the policy reach the
    #: output" needs the count, not the share of a denominator the cap never
    #: knew about.
    top_product_count: int = 0
    top_seller_count: int = 0
    top_category_count: int = 0
    top_segment_count: int = 0

    #: True when the numbers are not a measurement — the exposure read that fed
    #: them failed and resolved soft to a partial state. Alerting must stay
    #: silent on this, or a database hiccup reads as a repetition incident.
    degraded: bool = False

    def as_dict(self) -> dict:
        return {
            "impressions": self.impressions,
            "unique_products_shown": self.unique_products_shown,
            "unique_sellers_shown": self.unique_sellers_shown,
            "unique_categories_shown": self.unique_categories_shown,
            "unique_segments_shown": self.unique_segments_shown,
            "repeat_product_rate": round(self.repeat_product_rate, 4),
            "repeat_seller_rate": round(self.repeat_seller_rate, 4),
            "cross_surface_repeat_rate": round(self.cross_surface_repeat_rate, 4),
            "category_concentration": round(self.category_concentration, 4),
            "segment_concentration": round(self.segment_concentration, 4),
            "seller_concentration": round(self.seller_concentration, 4),
            "new_product_exposure_rate": round(self.new_product_exposure_rate, 4),
            "immediate_product_repeats": self.immediate_product_repeats,
            "immediate_seller_repeats": self.immediate_seller_repeats,
            "top_product_count": self.top_product_count,
            "top_seller_count": self.top_seller_count,
            "top_category_count": self.top_category_count,
            "top_segment_count": self.top_segment_count,
            "degraded": self.degraded,
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
    product_counts: dict[int, int] = {}
    category_counts: dict[str, int] = {}
    segment_counts: dict[str, int] = {}
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
        # Folded, not merely lower-cased. `Jewelry & Watches > Rings` and
        # `jewelry watches / rings` are one category, and counting them as two
        # halves the concentration figure for a viewer being shown one aisle
        # under two spellings — understating exactly the case the number exists
        # to catch. This is the same fold `exposure` and `router`'s caps use, so
        # the measurement and the control can only ever agree about what a
        # category is.
        category = taxonomy.category_key(event.get("category"))
        segment = taxonomy.segment_root(event.get("category"))
        surface = _text(event.get("surface"))

        product_counts[listing_id] = product_counts.get(listing_id, 0) + 1
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
        if segment:
            segment_counts[segment] = segment_counts.get(segment, 0) + 1

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
        unique_segments_shown=len(segment_counts),
        segment_concentration=(max(segment_counts.values()) / total) if segment_counts else 0.0,
        top_product_count=max(product_counts.values()) if product_counts else 0,
        top_seller_count=max(seller_counts.values()) if seller_counts else 0,
        top_category_count=max(category_counts.values()) if category_counts else 0,
        top_segment_count=max(segment_counts.values()) if segment_counts else 0,
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


def from_state(state) -> RepetitionReport:
    """The same report, derived from an :class:`exposure.ExposureState`.

    This exists so the measurement can run on the serve path without costing it
    anything. ``exposure.load`` already fetches the viewer's recent impression
    window on every request — the cooldowns need it — and that window holds every
    fact these numbers are made of. Recomputing them here is arithmetic over data
    already in memory: no query, no second read to get out of step with the first,
    and no possibility of the metrics describing a different window than the one
    the ranker actually used.

    The state is taken structurally rather than imported, so ``metrics`` adds no
    edge to the package's import graph for a type it only reads attributes off.

    What this cannot report
    -----------------------

    ``new_product_exposure_rate`` stays zero: "new" is the caller's judgement and
    the exposure state has no opinion about it. ``by_surface`` stays empty for a
    sharper reason — the state records *which* surfaces a product was seen on, not
    how many times on each, so the only number available would be distinct
    product-surface pairs. Putting that in a field whose docstring says
    "impressions" would be a wrong number rather than a missing one, and a wrong
    number is worse: the missing one reads as unmeasured.

    Denominator
    -----------

    ``sum(product_counts)``, not ``state.total_impressions``. The latter sums
    *seller* counts on purpose, so that a row carrying no seller cannot inflate
    the denominator every seller's share is measured against. Here the
    denominator has to mean what it means in :func:`summarize` — rows carrying a
    listing id — or the two functions would disagree about the same window.
    """
    product_counts = dict(getattr(state, "product_counts", {}) or {})
    seller_counts = dict(getattr(state, "seller_counts", {}) or {})
    category_counts = dict(getattr(state, "category_counts", {}) or {})
    surface_age = dict(getattr(state, "surface_age", {}) or {})

    impressions = sum(int(value or 0) for value in product_counts.values())
    if impressions <= 0:
        return EMPTY

    # Keys are already folded — `exposure` stores them through the same
    # `taxonomy.category_key` this module now uses — so the aisle is the first
    # segment of a key that is known to be canonical.
    segment_counts: dict[str, int] = {}
    for key, count in category_counts.items():
        root = taxonomy.segment_root(key)
        if root:
            segment_counts[root] = segment_counts.get(root, 0) + int(count or 0)

    seller_total = sum(int(value or 0) for value in seller_counts.values())
    total = float(impressions)

    # (distinct surfaces - 1) per product, which is exactly what `summarize`
    # accumulates row by row: the first sighting on each *additional* surface
    # counts once, and a repeat on a surface already seen does not.
    cross_surface = sum(
        max(0, len(ages or {}) - 1) for ages in surface_age.values()
    )

    return RepetitionReport(
        impressions=impressions,
        unique_products_shown=len(product_counts),
        unique_sellers_shown=len(seller_counts),
        unique_categories_shown=len(category_counts),
        repeat_product_rate=(impressions - len(product_counts)) / total,
        repeat_seller_rate=max(0, seller_total - len(seller_counts)) / total,
        cross_surface_repeat_rate=cross_surface / total,
        category_concentration=(max(category_counts.values()) / total) if category_counts else 0.0,
        seller_concentration=(max(seller_counts.values()) / total) if seller_counts else 0.0,
        immediate_product_repeats=_adjacent_repeats(getattr(state, "recent_products", ())),
        immediate_seller_repeats=_adjacent_repeats(getattr(state, "recent_sellers", ())),
        unique_segments_shown=len(segment_counts),
        segment_concentration=(max(segment_counts.values()) / total) if segment_counts else 0.0,
        top_product_count=max(product_counts.values()) if product_counts else 0,
        top_seller_count=max(seller_counts.values()) if seller_counts else 0,
        top_category_count=max(category_counts.values()) if category_counts else 0,
        top_segment_count=max(segment_counts.values()) if segment_counts else 0,
        degraded=bool(getattr(state, "degraded", False)),
    )


def _adjacent_repeats(sequence: Iterable[Any]) -> int:
    """Times a value immediately follows itself.

    Order-insensitive: the exposure state holds these newest-first while
    :func:`summarize` reads oldest-first, and equality between neighbours reads
    the same in either direction. So this is the one measure that can be taken
    from the state without first reversing it.
    """
    repeats = 0
    previous: Any = None
    for index, value in enumerate(sequence):
        if index and value == previous:
            repeats += 1
        previous = value
    return repeats


# --- alerting ---------------------------------------------------------------
#: Alert codes. Strings rather than an enum because their only consumer is a log
#: line an operator greps, and a grep for `SELLER_CONCENTRATION` should find the
#: definition as well as the occurrence.
IMMEDIATE_PRODUCT_REPEAT = "IMMEDIATE_PRODUCT_REPEAT"
REPEAT_PRODUCT_RATE = "REPEAT_PRODUCT_RATE"
CROSS_SURFACE_RATE = "CROSS_SURFACE_RATE"
SELLER_CONCENTRATION = "SELLER_CONCENTRATION"
CATEGORY_CONCENTRATION = "CATEGORY_CONCENTRATION"
SEGMENT_CONCENTRATION = "SEGMENT_CONCENTRATION"
#: The volume cap was exceeded. Unlike every code above except
#: ``IMMEDIATE_PRODUCT_REPEAT``, this is not a rate with a tunable threshold — it
#: reports that an *enforced* limit did not hold, so the only acceptable observed
#: value is one at or below the cap.
#:
#: It should never fire, and that is the point. ``pool._reject`` drops at-cap
#: rows, so a viewer whose top product count exceeds their surface's cap means
#: either the enforcement regressed or the exposure ledger and the pool are
#: reading different windows. Both fail silently otherwise: the feed keeps
#: working and simply gets more repetitive, which is the exact condition this
#: module exists to make visible.
PRODUCT_CAP_EXCEEDED = "PRODUCT_CAP_EXCEEDED"


@dataclass(frozen=True)
class Alert:
    """One threshold that was crossed, with the numbers that crossed it."""

    code: str
    observed: float
    threshold: float

    def __str__(self) -> str:
        return f"{self.code}={self.observed:.4g}>{self.threshold:.4g}"

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "observed": round(float(self.observed), 4),
            "threshold": round(float(self.threshold), 4),
        }


def alerts(report: RepetitionReport, *, product_cap: int = 0) -> tuple[Alert, ...]:
    """Which of this report's numbers are out of bounds.

    ``product_cap`` is the caller's surface allowance, and defaults to ``0``
    meaning "do not check it". It is passed in rather than read from ``router``
    because the cap is per-surface and this module does not know the surface — and
    because importing ``router`` here to find out would couple the measurement to
    the policy it is supposed to be able to contradict.

    Returns empty for a degraded report. A failed exposure read resolves soft to
    a partial state, so its numbers describe how much of the window was readable
    rather than how repetitive the window was — alerting on them would turn every
    transient database hiccup into a repetition incident, and an alert channel
    that fires on database weather is one nobody reads.

    The rate thresholds are gated on sample size; the immediate-repeat invariant
    is not. That asymmetry is the module's whole thesis restated: rates have no
    correct value and only mean something in comparison, while "the same product
    twice in a row" is wrong on its own, at any sample size, with no baseline to
    compare against.
    """
    if report.degraded or report.impressions <= 0:
        return ()

    found: list[Alert] = []

    if report.immediate_product_repeats > 0:
        found.append(Alert(IMMEDIATE_PRODUCT_REPEAT, float(report.immediate_product_repeats), 0.0))

    # Above the sample gate, alongside the other invariant, for the reason the
    # docstring gives: a cap that has been exceeded is wrong at any sample size.
    # Waiting for `repetition_min_sample` impressions before saying so would mean
    # the smallest and most obvious breach — a viewer shown one product four times
    # and nothing else — is the one case that never reports.
    if product_cap > 0 and report.top_product_count > product_cap:
        found.append(Alert(PRODUCT_CAP_EXCEEDED, float(report.top_product_count), float(product_cap)))

    if report.impressions < config.repetition_min_sample():
        return tuple(found)

    concentration = config.repetition_max_concentration()
    for code, observed, limit in (
        (REPEAT_PRODUCT_RATE, report.repeat_product_rate, config.repetition_max_repeat_rate()),
        (CROSS_SURFACE_RATE, report.cross_surface_repeat_rate, config.repetition_max_cross_surface_rate()),
        (SELLER_CONCENTRATION, report.seller_concentration, concentration),
        (CATEGORY_CONCENTRATION, report.category_concentration, concentration),
        (SEGMENT_CONCENTRATION, report.segment_concentration, concentration),
    ):
        if observed > limit:
            found.append(Alert(code, observed, limit))

    return tuple(found)


def observe(
    report: RepetitionReport,
    *,
    surface: str,
    subject_ref: str = "",
    product_cap: int = 0,
) -> tuple[Alert, ...]:
    """Emit one repetition observation, and return whatever it alerted on.

    Emitted at WARNING when something is out of bounds and DEBUG otherwise, so
    that the default production log level carries the incidents and nothing else.
    The healthy line still exists because an alert with no baseline is unreadable:
    the first question about "category concentration 0.81" is always what it was
    yesterday.

    Only the first eight characters of ``subject_ref`` are logged. Enough for an
    operator to tell two viewers' lines apart, or to follow one viewer across a
    few minutes of a live incident; not enough to accumulate a behavioural
    profile against a stable pseudonym in a log store that has none of the
    retention bounds the discovery tables have.
    """
    if not config.repetition_alerts_enabled():
        return ()

    found = alerts(report, product_cap=product_cap)
    ref = (str(subject_ref or "")[:8]) or "-"
    if found:
        LOGGER.warning(
            "COMMERCE_DISCOVERY_REPETITION surface=%s ref=%s alerts=%s report=%s",
            surface, ref, ",".join(str(alert) for alert in found), report.as_dict(),
        )
    else:
        LOGGER.debug(
            "COMMERCE_DISCOVERY_REPETITION surface=%s ref=%s alerts=none report=%s",
            surface, ref, report.as_dict(),
        )
    return found


def observe_sources(sources: Mapping[str, int], *, surface: str) -> str:
    """Emit one line describing which retrieval question filled this pool.

    Returns the rendered mix, so a caller can assert on it without reading logs.

    This is the only production-visible signal that personalised retrieval is
    doing anything, and it is needed because the failure mode is silent. Every
    targeted source in :mod:`pool` narrows rather than widens, degrades to
    returning nothing, and has ``rotation`` absorb its unspent quota — so a source
    that has stopped working looks exactly like a healthy feed. Before sources
    existed, retrieval was one blind ordering and a viewer who had clicked twenty
    cameras and nothing else was served 14.1% cameras against a 10% catalogue
    share; that number was invisible for as long as it was true, because nothing
    anywhere counted where a candidate came from.

    Deliberately aggregate. The per-row ``candidate_source`` tag exists on the
    pool rows and is *not* persisted onto ``commerce_discovery_placements``: this
    package's schema layer is ``CREATE TABLE IF NOT EXISTS`` with no ALTER path,
    so adding a column to the declaration would apply on a fresh database and
    silently not apply to the one in production — a gap that reads as a working
    feature. A per-surface mix answers the operational question ("is affinity
    retrieval contributing?") without that hazard.

    Logged at DEBUG. Nothing here is an incident; it is the baseline an incident
    would be read against.
    """
    mix = ",".join(f"{name}={int(count)}" for name, count in sorted((sources or {}).items()))
    if not mix:
        return ""
    LOGGER.debug("COMMERCE_DISCOVERY_POOL_SOURCES surface=%s mix=%s", surface, mix)
    return mix
