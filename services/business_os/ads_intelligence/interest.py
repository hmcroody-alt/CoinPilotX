"""Interest graph — decayed, first-party affinity used for relevance only.

What this is for
----------------
Given a subject, this module answers "which of the closed category list does
their own behaviour suggest they care about, and how strongly". A ranker uses
that to order campaigns that are *already eligible*. That is the whole scope.

What this is deliberately NOT for
---------------------------------
It is not an audience system, and the direction of the API is the enforcement.
``interests`` is in ``advertising/targeting.py``'s ``AUDIENCE_PROHIBITED_FIELDS``
— the canonical layer refuses, with a specific error code, to let an advertiser
target by interest. That is a deliberate product decision, and an interest graph
is exactly the component that could quietly undo it.

So this module never exposes a category-to-subjects lookup. You can ask what one
subject likes; you cannot ask who likes a category. ``category_reach`` returns a
count and only a count, because a count is what audience-size safety needs and a
list is what profiling needs. There is a test asserting no function here returns
subject identifiers, because the day someone adds ``subjects_in_category`` for a
good reason is the day the prohibition silently stops holding.

Relevance ranking and audience targeting look similar and are not the same
thing. Ranking changes the *order* of campaigns a person was already eligible
for. Targeting changes *who is eligible*. Only the second one lets an advertiser
address a group of people, which is the thing being prohibited.

How a score is built
--------------------
Every affinity is a projection, recomputed from ``ads_intel_events`` — never
incremented in place. Replay is what makes a weight change auditable: retuning
``SIGNAL_WEIGHTS`` and rebuilding produces a defensible number, where nudging a
stored score does not.

Three filters decide whether an event may contribute at all:

* **Validity.** Only ``valid`` events. Fraud must not train the graph — if
  invalid traffic could shape affinity, buying fake engagement would buy
  audience relevance, which turns a fraud problem into a targeting problem.
* **Privacy class.** Only classes whose ``targeting`` permission is true.
  Inferred dislike is ``measurement_only`` and is counted in reports but may not
  shape what anyone is shown.
* **Category.** The category comes from the *campaign*, server-side. A client
  cannot name a category, so no device can write itself into a segment.

Decay is exponential with a per-window half-life, so a signal contributes half
as much once the half-life has passed. That is what stops one intense afternoon
from classifying somebody indefinitely, and it is why the same event produces
different scores in the 7-, 30- and 90-day windows: short windows catch intent,
long windows catch taste.

Explicit negatives can only ever subtract. See ``privacy.classify_event`` for
why they are allowed to act at all.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import taxonomy
from . import privacy as _privacy

_LOG = logging.getLogger(__name__)

#: Where a campaign may declare what it is about. An advertiser labelling their
#: own creative is not targeting — they are describing their own content — so
#: this is allowed where an ``interests`` targeting field is not. The value is
#: validated against the closed taxonomy and anything else is ignored.
CATEGORY_META_KEYS = ("ads_category", "category", "vertical")


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse_ts(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# --------------------------------------------------------------------------- #
# Category resolution
# --------------------------------------------------------------------------- #

def normalise_category(raw: Any) -> Optional[str]:
    """A category, or None. Never a guess.

    Unknown input returns None rather than a default, because a wrong category
    is worse than no category: no category costs one relevance signal, a wrong
    one teaches the graph something untrue and then decays slowly.
    """
    text = str(raw or "").strip().lower()
    return text if text in taxonomy.INTEREST_CATEGORY_SET else None


def campaign_category(conn, campaign_id: Any) -> Optional[str]:
    """What a campaign is about, from its own advertiser-declared metadata.

    Read from the canonical campaign row, not from the event: the event comes
    from a device and a device must not be able to name the category it is
    training. This is the single reason category resolution lives server-side.

    An unreadable or absent metadata blob is None, not an error — a campaign
    that never declared a category simply contributes no interest signal.
    """
    cid = str(campaign_id or "").strip()
    if not cid:
        return None
    try:
        row = conn.execute(
            "SELECT metadata_json FROM business_os_ad_campaigns "
            "WHERE campaign_id = ?", (cid,)).fetchone()
    except Exception:
        # The advertising tables may not exist in every context this runs in.
        # No category is a correct answer here; raising would make an optional
        # relevance signal able to break its caller.
        return None
    if not row or not row[0]:
        return None
    try:
        meta = json.loads(row[0])
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    for key in CATEGORY_META_KEYS:
        found = normalise_category(meta.get(key))
        if found:
            return found
    return None


# --------------------------------------------------------------------------- #
# Decay
# --------------------------------------------------------------------------- #

def decay_factor(age_days: float, half_life_days: float) -> float:
    """Exponential decay: the weight remaining after ``age_days``.

    Half-life rather than a linear ramp or a hard cutoff. A cutoff makes a
    signal worth full value the day before it expires and nothing the day after,
    which produces score cliffs that look like bugs to whoever is reading a
    dashboard. Decay is smooth, so a rebuild run an hour later gives an almost
    identical answer.
    """
    try:
        age = float(age_days)
        half = float(half_life_days)
    except Exception:
        return 0.0
    if half <= 0:
        return 0.0
    if age <= 0:
        return 1.0
    return float(0.5 ** (age / half))


def clamp_score(value: float) -> float:
    """Hold a score inside the taxonomy's bounds."""
    try:
        score = float(value)
    except Exception:
        return 0.0
    return max(taxonomy.AFFINITY_MIN, min(taxonomy.AFFINITY_MAX, score))


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def _may_shape_delivery(event_name: str,
                        stored_class: Optional[str] = None) -> bool:
    """Whether this event may shape what somebody is shown.

    The class stored on the row wins when present. That is the whole point of
    storing it: a signal collected under a narrow promise keeps that promise
    even if the event type is later reclassified more permissively. Deriving
    from the name is the fallback for rows written before the column existed.
    """
    klass = str(stored_class or "").strip() or _privacy.classify_event(event_name)
    return _privacy.allows(klass, "targeting")


def score_events(rows, *, window_days: int, now: Optional[datetime] = None) -> dict:
    """Score one subject's events into ``{category: {...}}`` for one window.

    ``rows`` are ``(event_name, occurred_at, category)`` triples, or 4-tuples
    that also carry the stored privacy class. Kept separate from the database so
    the decay and weighting logic is testable without a schema.
    """
    horizon = _now(now)
    cutoff = horizon - timedelta(days=int(window_days))
    half_life = taxonomy.INTEREST_HALF_LIFE_DAYS.get(
        int(window_days), float(window_days) / 2.0)

    out: dict = {}
    for row in rows:
        event_name, occurred_at, category = row[0], row[1], row[2]
        stored_class = row[3] if len(row) > 3 else None
        name = str(event_name or "")
        cat = normalise_category(category)
        if not cat:
            continue
        weight = taxonomy.SIGNAL_WEIGHTS.get(name)
        if weight is None:
            continue
        if not _may_shape_delivery(name, stored_class):
            continue
        when = _parse_ts(occurred_at)
        if when is None or when < cutoff or when > horizon + timedelta(minutes=5):
            continue

        contribution = float(weight) * decay_factor(
            (horizon - when).total_seconds() / 86400.0, half_life)

        # An explicit negative may only ever subtract. The privacy argument for
        # letting these shape delivery at all rests on this: a complaint can
        # suppress a category, and can never build one up. A positive weight
        # here would be a taxonomy bug, and clamping to <= 0 means that bug
        # cannot become a profile attribute while it waits to be noticed.
        if name in taxonomy.EXPLICIT_NEGATIVE_EVENTS:
            contribution = min(0.0, contribution)

        slot = out.setdefault(cat, {
            "score": 0.0, "positive_signals": 0, "negative_signals": 0,
            "signal_count": 0, "last_signal_at": None,
        })
        slot["score"] += contribution
        slot["signal_count"] += 1
        if contribution < 0:
            slot["negative_signals"] += 1
        elif contribution > 0:
            slot["positive_signals"] += 1
        if slot["last_signal_at"] is None or when > slot["last_signal_at"]:
            slot["last_signal_at"] = when

    for slot in out.values():
        slot["score"] = clamp_score(slot["score"])
    return out


# --------------------------------------------------------------------------- #
# Rebuild
# --------------------------------------------------------------------------- #

def _load_subject_rows(conn, subject_ref: str, *, oldest: datetime) -> list:
    """Valid, category-resolved events for one subject since ``oldest``.

    Only ``validity = 'valid'``: suspect and invalid traffic must not train the
    graph, or buying fake engagement would buy relevance.
    """
    try:
        rows = conn.execute(
            "SELECT event_name, occurred_at, campaign_id, privacy_class "
            "FROM ads_intel_events "
            "WHERE subject_ref = ? AND occurred_at >= ? AND validity = 'valid' "
            "ORDER BY occurred_at",
            (subject_ref, _iso(oldest))).fetchall()
    except Exception:
        _LOG.warning("ADS_INTEL_INTEREST_READ_FAILED subject=%s",
                     subject_ref, exc_info=True)
        return []

    cache: dict = {}
    out = []
    for event_name, occurred_at, campaign_id, stored_class in rows:
        key = str(campaign_id or "")
        if key not in cache:
            cache[key] = campaign_category(conn, key)
        category = cache[key]
        if category:
            out.append((event_name, occurred_at, category, stored_class))
    return out


def rebuild_subject(conn, subject_ref: Any, *, now: Optional[datetime] = None,
                    windows=None) -> dict:
    """Recompute every window's affinities for one subject from the event log.

    Returns ``{window_days: {category: score}}``. Rows for categories that no
    longer score are deleted rather than left at their old value: a stale high
    score is indistinguishable from a current one to every reader, so leaving it
    behind would mean decay never actually takes effect.
    """
    ref = str(subject_ref or "").strip()
    if not ref:
        return {}
    horizon = _now(now)
    windows = tuple(windows or taxonomy.INTEREST_WINDOWS)
    if not windows:
        return {}

    rows = _load_subject_rows(
        conn, ref, oldest=horizon - timedelta(days=max(windows)))

    computed_at = _iso(horizon)
    result: dict = {}
    for window in windows:
        scored = score_events(rows, window_days=window, now=horizon)
        result[window] = {cat: slot["score"] for cat, slot in scored.items()}
        try:
            conn.execute(
                "DELETE FROM ads_intel_interest_affinity "
                "WHERE subject_ref = ? AND window_days = ?", (ref, int(window)))
            for category, slot in scored.items():
                conn.execute(
                    "INSERT INTO ads_intel_interest_affinity "
                    "(affinity_id, subject_ref, category, window_days, score, "
                    "positive_signals, negative_signals, signal_count, "
                    "last_signal_at, computed_at, policy_version, "
                    "feature_version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"{ref}:{category}:{int(window)}", ref, category,
                     int(window), float(slot["score"]),
                     int(slot["positive_signals"]),
                     int(slot["negative_signals"]), int(slot["signal_count"]),
                     _iso(slot["last_signal_at"]) if slot["last_signal_at"]
                     else None,
                     computed_at, taxonomy.PROCESSING_VERSION,
                     taxonomy.FEATURE_VERSION))
        except Exception:
            _LOG.warning("ADS_INTEL_INTEREST_WRITE_FAILED subject=%s window=%s",
                         ref, window, exc_info=True)
    try:
        conn.commit()
    except Exception:
        pass
    return result


# --------------------------------------------------------------------------- #
# Reads — subject-directed only, by design
# --------------------------------------------------------------------------- #

def affinities_for(conn, subject_ref: Any, *, window_days: int = 30,
                   min_score: Optional[float] = None) -> dict:
    """This subject's affinities as ``{category: score}``.

    The only read a ranker needs, and deliberately the only direction offered.
    """
    ref = str(subject_ref or "").strip()
    if not ref:
        return {}
    try:
        rows = conn.execute(
            "SELECT category, score FROM ads_intel_interest_affinity "
            "WHERE subject_ref = ? AND window_days = ?",
            (ref, int(window_days))).fetchall()
    except Exception:
        # No affinity is a valid state, and relevance is an enhancement. A
        # missing table must degrade ranking, never fail an ad request.
        return {}
    out = {}
    for category, score in rows or ():
        value = float(score or 0.0)
        if min_score is not None and value < float(min_score):
            continue
        out[str(category)] = value
    return out


def top_categories(conn, subject_ref: Any, *, window_days: int = 30,
                   limit: int = 5, min_score: float = 0.0) -> list:
    """The strongest positive affinities, highest first.

    ``min_score`` defaults to 0 so suppressed categories are never returned as
    "top" anything.
    """
    scored = affinities_for(conn, subject_ref, window_days=window_days,
                            min_score=min_score)
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"category": c, "score": s} for c, s in ranked[:max(0, int(limit))]]


def explain_affinity(conn, subject_ref: Any, category: str, *,
                     window_days: int = 30) -> dict:
    """The stored row behind one affinity, for "why am I seeing this ad?".

    Returns the counts and recency alongside the score so a person can be shown
    how thin the evidence is, rather than a bare number that implies certainty.
    """
    ref = str(subject_ref or "").strip()
    cat = normalise_category(category)
    if not ref or not cat:
        return {"known": False}
    try:
        row = conn.execute(
            "SELECT score, positive_signals, negative_signals, signal_count, "
            "last_signal_at, computed_at FROM ads_intel_interest_affinity "
            "WHERE subject_ref = ? AND category = ? AND window_days = ?",
            (ref, cat, int(window_days))).fetchone()
    except Exception:
        return {"known": False}
    if not row:
        return {"known": False, "category": cat, "window_days": int(window_days)}
    return {
        "known": True, "category": cat, "window_days": int(window_days),
        "score": float(row[0] or 0.0),
        "positive_signals": int(row[1] or 0),
        "negative_signals": int(row[2] or 0),
        "signal_count": int(row[3] or 0),
        "last_signal_at": row[4], "computed_at": row[5],
    }


def category_reach(conn, category: str, *, window_days: int = 30,
                   min_score: float = 1.0) -> int:
    """How many subjects hold a positive affinity for a category.

    A count, never the subjects. This exists so audience-size safety can refuse
    a segment below ``MIN_AUDIENCE_SIZE`` — checking that a group is large
    enough to be anonymous should not itself require enumerating the group.
    """
    cat = normalise_category(category)
    if not cat:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM ads_intel_interest_affinity "
            "WHERE category = ? AND window_days = ? AND score >= ?",
            (cat, int(window_days), float(min_score))).fetchone()
    except Exception:
        return 0
    return int((row or [0])[0] or 0)


def meets_minimum_audience(conn, category: str, *, window_days: int = 30) -> bool:
    """Whether a category is held by enough people to be addressable safely."""
    return category_reach(conn, category, window_days=window_days) >= \
        taxonomy.MIN_AUDIENCE_SIZE


def forget_subject(conn, subject_ref: Any) -> int:
    """Erase a subject's affinities. Returns rows removed.

    Deletion has to reach the projections, not only the event log. An affinity
    row derived from deleted events is still a profile of the person who asked
    to be forgotten.
    """
    ref = str(subject_ref or "").strip()
    if not ref:
        return 0
    try:
        cur = conn.execute(
            "DELETE FROM ads_intel_interest_affinity WHERE subject_ref = ?",
            (ref,))
        removed = int(getattr(cur, "rowcount", 0) or 0)
        try:
            conn.commit()
        except Exception:
            pass
        return removed
    except Exception:
        _LOG.warning("ADS_INTEL_INTEREST_FORGET_FAILED subject=%s",
                     ref, exc_info=True)
        return 0
