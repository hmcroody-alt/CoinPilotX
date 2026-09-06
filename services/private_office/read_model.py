"""The read model — the shapes the Private Office screens actually ask for.

Why this exists at all
----------------------
Every number below can already be obtained by calling two or three functions in
``facts``, ``contradictions``, ``evidence`` and ``review`` and combining them.
That is precisely the problem. A screen that assembles its own overview decides,
privately and by accident, what "needs attention" means; a second screen decides
something slightly different; and the two disagree in front of the member with
no way to tell which is right. The composition happens once, here, so that
disagreement has nowhere to live.

Exact counts and observed counts are not the same thing
-------------------------------------------------------
This is the one idea the module is built around, and it is the one most likely
to be quietly undone by a later change.

Some properties of a fact are **stored**: its domain, its provenance type, its
lifecycle state. Those can be counted by the database across the whole store,
exactly, for the price of one ``GROUP BY``.

Other properties are **computed at read**: whether a verification has aged out,
whether a fact has gone stale, whether its evidence still resolves. The package
computes these rather than storing them for a reason given in full in
``review.py`` — a stored flag is only as current as the last sweeper run. But
the consequence lands here: a property that only exists once a row is in memory
can only be counted over rows that were actually read, and reading every row of
an unbounded private store is the full export this package exists to prevent.

So the overview reports both kinds and refuses to blur them. Exact counts sit at
the top level. Computed counts sit under ``observed``, next to the number of
rows they were computed from and a ``complete`` flag saying whether that was all
of them. A caller drawing a proportion — "38% of your facts are unverified" —
must check ``complete`` first, because otherwise it is drawing a proportion of
the first five hundred rows and labelling it with the total.

The tempting shortcut is to compute expiry in SQL and make everything exact. It
would work, once. Then ``VERIFICATION_HORIZON_DAYS`` changes in ``model.py`` and
the SQL does not, and the store now holds two answers to "is this expired" with
no indication which one any given screen used.

Bounded by construction
-----------------------
Every function here takes a ceiling it cannot be argued above, for the reason
``list_facts`` gives: an unbounded read of a private store is a full export
waiting for one caller to forget a limit. Where a bound bites, the result says
so — ``complete``, ``has_more``, ``truncated`` — rather than returning a short
answer shaped exactly like a complete one.

Reads only
----------
Nothing here writes, and the module is deliberately absent from the write
boundary's ``WRITER_MODULES``. Composing reads is the whole job; a read model
that corrected what it noticed would be making decisions on a screen refresh.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Sequence

from services.private_office import contradictions as _contradictions
from services.private_office import evidence as _evidence
from services.private_office import facts as _facts
from services.private_office import model as _model
from services.private_office import review as _review
from services.private_office import schema as _schema

LOGGER = logging.getLogger("private_office.read_model")

#: Rows read to compute the overview's ``observed`` block. Matched to the review
#: queue's scan so the two cannot report different pictures of the same store.
MAX_OVERVIEW_SCAN = 500

#: One page of the fact list. The ceiling is well under ``list_facts``' own 500
#: because this page is rendered, and a screen asking for five hundred rows is a
#: screen that has not decided what it is showing.
MAX_PAGE = 100
DEFAULT_PAGE = 25

#: Events in one fact's timeline, across its whole supersession chain.
MAX_TIMELINE = 200

#: Distinct sources reported by :func:`source_index`, and evidence links read to
#: find them. The link ceiling is the larger number because many links share one
#: source — that is the normal case, not the exception.
MAX_SOURCES = 100
MAX_SOURCE_LINKS = 600

#: Facts returned by :func:`expiring_facts`, and how far ahead it will look.
#: A year is the outer edge of "you could plan around this"; past that the list
#: fills with items nobody can act on and stops being read.
MAX_EXPIRING = 100
DEFAULT_EXPIRING_DAYS = 90
MAX_EXPIRING_DAYS = 365

#: Conflict groups returned by :func:`open_conflicts`.
MAX_CONFLICT_GROUPS = 50


def _clamp(value: object, default: int, ceiling: int, floor: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number <= 0:
        number = default
    return max(floor, min(number, ceiling))


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def _count_by_provenance(cur, owner: int) -> dict[str, int]:
    """Exact provenance histogram, every declared type present.

    Complete by construction for the same reason ``count_facts_by_domain`` is:
    a surface that groups by provenance has to be able to say "nothing was
    inferred" as confidently as it says "three were inferred", and a map that
    omitted the empty types would make the screen invent the missing keys.

    A row carrying a provenance this package no longer recognises is counted
    under ``LEGACY_UNKNOWN`` rather than dropped. Dropping it would make the
    histogram disagree with the total, and the difference would be invisible.
    """
    summary = {name: 0 for name in _model.PROVENANCE_TYPES}
    cur.execute(
        f"""SELECT provenance_type, COUNT(*) AS n FROM {_schema.FACTS_TABLE}
        WHERE owner_user_id = ? AND lifecycle_state = ?
        GROUP BY provenance_type""",
        (owner, _model.LIFECYCLE_ACTIVE),
    )
    for row in cur.fetchall():
        data = dict(row)
        name = _model.normalize_provenance(data.get("provenance_type"))
        if not name:
            name = _model.PROVENANCE_LEGACY_UNKNOWN
        summary[name] = summary.get(name, 0) + int(data.get("n") or 0)
    return summary


def facts_overview(cur, *, owner_user_id: int, scan: int = MAX_OVERVIEW_SCAN,
                   at: datetime | None = None) -> dict[str, Any]:
    """The landing surface: what is in the store, and what wants looking at.

    The split between the exact counts at the top level and the ``observed``
    block is explained at module scope and is the contract callers depend on:
    ``total``, ``by_domain`` and ``by_provenance`` describe every active fact;
    everything under ``observed`` describes ``observed["scanned"]`` of them and
    says as much.

    ``review`` is carried through from :func:`review.review_summary` rather than
    recomputed, so the count on the tab and the list behind it come from one
    pass over one scan. Recomputing it here with a different bound would
    reproduce the "12 need review" header opening onto nine rows that the review
    module was written to avoid.
    """
    owner = int(owner_user_id or 0)
    empty_observed = {
        "scanned": 0,
        "complete": True,
        "by_verification": {name: 0 for name in _model.VERIFICATION_STATES},
        "needs_attention": 0,
        "stale": 0,
    }
    if owner <= 0:
        return {
            "total": 0,
            "by_domain": {name: 0 for name in _model.DOMAINS},
            "by_provenance": {name: 0 for name in _model.PROVENANCE_TYPES},
            "observed": empty_observed,
            "review": {"total": 0, "by_reason": {}, "top_reason": "",
                       "truncated": False},
            "open_conflicts": 0,
            "expiring_soon": 0,
        }

    _schema.require_private_schema(cur)
    moment = at or _facts._now()
    bounded_scan = _clamp(scan, MAX_OVERVIEW_SCAN, MAX_OVERVIEW_SCAN)

    total = _facts.count_facts(cur, owner_user_id=owner)
    by_domain = _facts.count_facts_by_domain(cur, owner_user_id=owner)
    by_provenance = _count_by_provenance(cur, owner)

    rows = _facts.list_facts(cur, owner_user_id=owner, limit=bounded_scan)
    by_verification = {name: 0 for name in _model.VERIFICATION_STATES}
    needs_attention = 0
    stale = 0
    for row in rows:
        status = _facts.verification_status(row, at=moment)
        state = status["effective_state"]
        if state in by_verification:
            by_verification[state] += 1
        if state in _model.VERIFICATION_NEEDS_ATTENTION:
            needs_attention += 1
        if _facts.staleness(row, at=moment)["stale"]:
            stale += 1

    observed = {
        "scanned": len(rows),
        # Not ``len(rows) < total``. The scan ceiling and the total are both
        # bounds on the same read, and a store holding exactly ``scan`` facts is
        # completely described by a scan of ``scan`` — so completeness is about
        # whether anything was left behind, not about whether the ceiling was
        # reached.
        "complete": len(rows) >= total,
        "by_verification": by_verification,
        "needs_attention": needs_attention,
        "stale": stale,
    }

    return {
        "total": total,
        "by_domain": by_domain,
        "by_provenance": by_provenance,
        "observed": observed,
        "review": _review.review_summary(cur, owner_user_id=owner,
                                         scan=bounded_scan, at=moment),
        "open_conflicts": len(_contradictions.detect_conflicts(
            cur, owner_user_id=owner, limit=_contradictions.MAX_SCAN)),
        "expiring_soon": len(expiring_facts(
            cur, owner_user_id=owner, within_days=DEFAULT_EXPIRING_DAYS,
            limit=MAX_EXPIRING, at=moment)),
    }


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def facts_page(
    cur,
    *,
    owner_user_id: int,
    subject_type: str | None = None,
    subject_id: object = None,
    fact_types: Sequence[str] | None = None,
    domains: Sequence[str] | None = None,
    include_superseded: bool = False,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
    at: datetime | None = None,
) -> dict[str, Any]:
    """One page of facts, and an honest answer about whether there are more.

    ``has_more`` is established by asking for one row beyond the page and
    discarding it, not by a second ``COUNT``. The count would be a second query
    answering a question one query already answers, and — worse — it would be
    evaluated at a different instant than the page, so a fact recorded between
    the two would produce a "next" button leading nowhere, or hide one that
    exists.
    """
    owner = int(owner_user_id or 0)
    page = _clamp(limit, DEFAULT_PAGE, MAX_PAGE)
    start = max(0, int(offset or 0))
    if owner <= 0:
        return {"items": [], "limit": page, "offset": start,
                "has_more": False, "next_offset": None}

    moment = at or _facts._now()
    rows = _facts.list_facts(
        cur, owner_user_id=owner, subject_type=subject_type,
        subject_id=subject_id, fact_types=fact_types, domains=domains,
        include_superseded=include_superseded,
        limit=page + 1, offset=start)

    has_more = len(rows) > page
    visible = rows[:page]

    items = [_summarize(row, at=moment) for row in visible]
    return {
        "items": items,
        "limit": page,
        "offset": start,
        "has_more": has_more,
        "next_offset": start + page if has_more else None,
    }


def _summarize(row: dict, *, at: datetime) -> dict[str, Any]:
    """The list-row shape: enough to render, not enough to be an export.

    Deliberately not the raw row. ``list_facts`` returns whatever columns the
    table has, and a list endpoint that forwards that wholesale gains new fields
    every time the schema does — including, eventually, one nobody meant to put
    on a screen.
    """
    return {
        "fact_id": int(row.get("id") or 0),
        "subject_type": str(row.get("subject_type") or ""),
        "subject_id": str(row.get("subject_id") or ""),
        "fact_type": str(row.get("fact_type") or ""),
        "domain": str(row.get("domain") or ""),
        "typed_value": row.get("typed_value"),
        "value_type": str(row.get("value_type") or ""),
        "sensitivity": str(row.get("sensitivity") or ""),
        "provenance_type": str(row.get("provenance_type") or ""),
        "lifecycle_state": str(row.get("lifecycle_state") or ""),
        "observed_at": str(row.get("observed_at") or ""),
        "valid_from": str(row.get("valid_from") or ""),
        "valid_to": str(row.get("valid_to") or ""),
        "confidence": row.get("confidence"),
        "conflict_id": str(row.get("conflict_id") or ""),
        "verification": _facts.verification_status(row, at=at),
        "freshness": _facts.staleness(row, at=at),
    }


# ---------------------------------------------------------------------------
# Detail and timeline
# ---------------------------------------------------------------------------

def _load(cur, owner: int, fact_id: int) -> dict | None:
    cur.execute(
        f"SELECT * FROM {_schema.FACTS_TABLE} WHERE owner_user_id = ? AND id = ?",
        (owner, fact_id),
    )
    row = cur.fetchone()
    return dict(row) if row is not None else None


def fact_detail(cur, *, owner_user_id: int, fact_id: int,
                at: datetime | None = None) -> dict[str, Any] | None:
    """One fact, with everything the detail screen needs to be honest about it.

    Returns ``None`` for a fact that does not exist *and* for one belonging to
    somebody else — the same answer for both, because a distinguishable
    "forbidden" tells a caller that a fact id exists, which is the whole of what
    they were trying to learn.

    The conflict verdict is computed the same way the review queue computes it,
    by consulting the resolution table rather than reading the stamped
    ``conflict_id`` as an answer. A detail screen that showed CONTRADICTED
    because a marker was present would keep showing it forever, since
    ``mark_conflicts`` never clears the marker.
    """
    owner = int(owner_user_id or 0)
    target = int(fact_id or 0)
    if owner <= 0 or target <= 0:
        return None
    _schema.require_private_schema(cur)
    row = _load(cur, owner, target)
    if row is None:
        return None

    moment = at or _facts._now()
    marker = str(row.get("conflict_id") or "").strip()
    resolutions = _contradictions.conflict_resolutions(
        cur, owner_user_id=owner, conflict_ids=[marker] if marker else None)
    open_conflict = _review._open_conflict(row, resolutions)

    links = _facts.fact_evidence(cur, owner_user_id=owner, fact_id=target)
    refs = [str(link.get("source_ref") or "") for link in links
            if str(link.get("source_ref") or "")]
    available: dict[str, bool] = {}
    for start in range(0, len(refs), _evidence.MAX_REFS):
        for entry in _evidence.resolve_refs(
                cur, owner, refs[start:start + _evidence.MAX_REFS]):
            available[entry["ref"]] = bool(entry.get("exists"))

    evidence_view = []
    for link in links:
        ref = str(link.get("source_ref") or "")
        evidence_view.append({
            "source_ref": ref,
            "source_kind": str(link.get("source_kind") or ""),
            "relation": str(link.get("relation") or ""),
            "linked_at": str(link.get("linked_at") or ""),
            # Three-valued on purpose. False means the resolver looked and the
            # document is not there; None means it could not look at all,
            # because `evidence.normalize_refs` drops any ref whose kind it does
            # not know and so never returns a verdict for it. Collapsing the two
            # would report a citation this build cannot parse as a document the
            # member has lost.
            "available": available.get(ref),
        })

    detail = _summarize(row, at=moment)
    detail.update({
        "provenance": asdict(
            _facts.decode_provenance_ref(row.get("provenance_ref"))),
        "evidence": evidence_view,
        "evidence_count": len(evidence_view),
        "missing_sources": sorted(
            {entry["source_ref"] for entry in evidence_view
             if entry["available"] is False}),
        "open_conflict": open_conflict,
        "chain": _facts.fact_chain(cur, owner_user_id=owner, fact_id=target),
        "review_reasons": _review.review_reasons(
            row,
            missing_source=any(entry["available"] is False
                               for entry in evidence_view),
            open_conflict=open_conflict, at=moment),
    })
    return detail


def _history_for(cur, owner: int, fact_ids: Sequence[int],
                 limit: int) -> list[dict]:
    """History for several facts in one query.

    Batched because the timeline follows a supersession chain, and a chain of
    ten corrections read one ``fact_history`` call at a time is ten round trips
    to build one screen.
    """
    ids = sorted({int(i) for i in fact_ids if int(i or 0) > 0})
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    try:
        cur.execute(
            f"""SELECT id, fact_id, change_type, actor_user_id, from_state,
                       to_state, related_fact_id, note_key, created_at
            FROM {_schema.FACT_HISTORY_TABLE}
            WHERE owner_user_id = ? AND fact_id IN ({placeholders})
            ORDER BY created_at DESC, id DESC LIMIT ?""",
            [owner, *ids, limit],
        )
        return [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        LOGGER.warning("PRIVATE_READ_HISTORY_FAILED error=%s", exc)
        return []


def fact_timeline(cur, *, owner_user_id: int, fact_id: int,
                  limit: int = MAX_TIMELINE) -> dict[str, Any]:
    """What has happened to this fact, across its whole supersession chain.

    The chain is the point. A fact that has been corrected twice is the third
    row in a chain of three, and its own history begins at the moment it was
    written — so a timeline scoped to one row answers "when was this value
    recorded" while appearing to answer "when did I first learn this". Those
    differ by exactly the interval the member is usually asking about.

    Ordered newest first and bounded. ``truncated`` says whether the bound bit,
    because a timeline that silently stops is one that will eventually be read
    as "and nothing happened before this".
    """
    owner = int(owner_user_id or 0)
    target = int(fact_id or 0)
    bounded = _clamp(limit, MAX_TIMELINE, MAX_TIMELINE)
    if owner <= 0 or target <= 0:
        return {"fact_id": target, "chain": [], "events": [],
                "truncated": False}
    _schema.require_private_schema(cur)
    if _load(cur, owner, target) is None:
        return {"fact_id": target, "chain": [], "events": [],
                "truncated": False}

    chain = _facts.fact_chain(cur, owner_user_id=owner, fact_id=target)
    if target not in chain:
        chain = [*chain, target]

    rows = _history_for(cur, owner, chain, bounded + 1)
    truncated = len(rows) > bounded
    events = [
        {
            "at": str(row.get("created_at") or ""),
            "fact_id": int(row.get("fact_id") or 0),
            "change_type": str(row.get("change_type") or ""),
            "from_state": str(row.get("from_state") or ""),
            "to_state": str(row.get("to_state") or ""),
            "related_fact_id": int(row.get("related_fact_id") or 0),
            "note_key": str(row.get("note_key") or ""),
            # So a chain timeline can visually separate "this happened to the
            # value you are looking at" from "this happened to the one it
            # replaced" without the screen re-deriving the chain.
            "is_this_fact": int(row.get("fact_id") or 0) == target,
        }
        for row in rows[:bounded]
    ]
    return {"fact_id": target, "chain": chain, "events": events,
            "truncated": truncated}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def source_index(cur, *, owner_user_id: int,
                 limit: int = MAX_SOURCES) -> dict[str, Any]:
    """Every source this owner's facts cite, with how much rests on each.

    Grouped by source rather than listed per fact, because the question this
    screen answers is "what am I relying on" and the useful unit is the
    document, not the citation. A source supporting eleven facts is the one
    worth checking; the same source listed eleven times is a list nobody reads.

    Availability is resolved in chunks of :data:`evidence.MAX_REFS`, once per
    distinct source. Resolving per link would issue a query for every citation
    of a document that has not changed between them.

    Availability is three-valued. A ref the resolver cannot parse — an unknown
    kind, which the coming legacy backfill makes a real possibility rather than
    a theoretical one — comes back ``None``, not ``False``. This module does not
    get to convert "this build does not understand that citation" into "your
    document is gone".
    """
    owner = int(owner_user_id or 0)
    bounded = _clamp(limit, MAX_SOURCES, MAX_SOURCES)
    if owner <= 0:
        return {"sources": [], "truncated": False}
    _schema.require_private_schema(cur)

    try:
        cur.execute(
            f"""SELECT source_ref, source_kind, fact_id, linked_at
            FROM {_schema.FACT_EVIDENCE_TABLE}
            WHERE owner_user_id = ? AND detached_at = ''
            ORDER BY id ASC LIMIT ?""",
            (owner, MAX_SOURCE_LINKS),
        )
        links = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        # An unreadable evidence table means the source list is unknown, not
        # empty. Returning an empty list here would render as "you have cited
        # nothing", which is a statement about the member's store rather than
        # about this deployment's schema.
        LOGGER.warning("PRIVATE_READ_SOURCES_FAILED error=%s", exc)
        return {"sources": [], "truncated": False, "unavailable": True}

    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for link in links:
        ref = str(link.get("source_ref") or "")
        if not ref:
            continue
        entry = grouped.get(ref)
        if entry is None:
            entry = {
                "source_ref": ref,
                "source_kind": str(link.get("source_kind") or ""),
                "fact_ids": [],
                "first_linked_at": str(link.get("linked_at") or ""),
                "last_linked_at": str(link.get("linked_at") or ""),
            }
            grouped[ref] = entry
            order.append(ref)
        fact_id = int(link.get("fact_id") or 0)
        if fact_id and fact_id not in entry["fact_ids"]:
            entry["fact_ids"].append(fact_id)
        linked_at = str(link.get("linked_at") or "")
        if linked_at:
            if not entry["first_linked_at"] or linked_at < entry["first_linked_at"]:
                entry["first_linked_at"] = linked_at
            if linked_at > entry["last_linked_at"]:
                entry["last_linked_at"] = linked_at

    truncated = len(order) > bounded or len(links) >= MAX_SOURCE_LINKS
    visible = order[:bounded]

    available: dict[str, bool] = {}
    for start in range(0, len(visible), _evidence.MAX_REFS):
        chunk = visible[start:start + _evidence.MAX_REFS]
        for entry in _evidence.resolve_refs(cur, owner, chunk):
            available[entry["ref"]] = bool(entry.get("exists"))

    sources = []
    for ref in visible:
        entry = grouped[ref]
        sources.append({
            "source_ref": ref,
            "source_kind": entry["source_kind"],
            "fact_count": len(entry["fact_ids"]),
            "fact_ids": sorted(entry["fact_ids"]),
            "first_linked_at": entry["first_linked_at"],
            "last_linked_at": entry["last_linked_at"],
            "available": available.get(ref),
        })

    # Most-relied-upon first, then by ref so the order is total: two sources
    # supporting the same number of facts must not swap between reads.
    sources.sort(key=lambda item: (-item["fact_count"], item["source_ref"]))
    return {"sources": sources, "truncated": truncated}


# ---------------------------------------------------------------------------
# Expiring
# ---------------------------------------------------------------------------

def expiring_facts(cur, *, owner_user_id: int,
                   within_days: int = DEFAULT_EXPIRING_DAYS,
                   limit: int = MAX_EXPIRING,
                   at: datetime | None = None) -> list[dict]:
    """Active facts whose own validity window is closing, soonest first.

    Windows that have **already** closed are included, and they sort to the top
    because they are the most overdue rather than the least relevant. A fact
    still marked ACTIVE with a ``valid_to`` of last March is asserting something
    it has itself declared out of date; dropping it for being past the horizon
    would remove exactly the rows most in need of the member's attention. This
    matches ``review._validity_ending``, and the two must keep matching — a
    fact flagged VALIDITY_ENDING in the queue but absent from this list is a
    member being told to act with nowhere to act.

    Facts without a ``valid_to`` are not here at all. Absence of a window is not
    a window that never ends; it is the member never having said, and inventing
    an expiry for it would be this module deciding something.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    _schema.require_private_schema(cur)
    moment = at or _facts._now()
    horizon = _clamp(within_days, DEFAULT_EXPIRING_DAYS, MAX_EXPIRING_DAYS)
    bounded = _clamp(limit, MAX_EXPIRING, MAX_EXPIRING)
    cutoff = (moment + timedelta(days=horizon)).isoformat()

    cur.execute(
        f"""SELECT * FROM {_schema.FACTS_TABLE}
        WHERE owner_user_id = ? AND lifecycle_state = ?
          AND valid_to != '' AND valid_to <= ?
        ORDER BY valid_to ASC, id ASC LIMIT ?""",
        (owner, _model.LIFECYCLE_ACTIVE, cutoff, bounded),
    )
    rows = [dict(row) for row in cur.fetchall()]

    items = []
    for row in rows:
        view = _summarize(row, at=moment)
        end = _facts._parse_iso(row.get("valid_to"))
        view["days_remaining"] = (
            int((end - moment).total_seconds() // 86400) if end else None)
        view["already_expired"] = bool(end and end <= moment)
        items.append(view)
    return items


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------

def open_conflicts(cur, *, owner_user_id: int,
                   limit: int = MAX_CONFLICT_GROUPS) -> dict[str, Any]:
    """Disagreements nobody has settled, grouped as the detector found them.

    Delegates the detection itself rather than reimplementing the comparison.
    ``detect_conflicts`` already excludes resolved groups by default, and a
    second opinion here about what counts as material incompatibility would be a
    second definition of "these two facts disagree" — with the screen and the
    review queue each able to pick a different one.
    """
    owner = int(owner_user_id or 0)
    bounded = _clamp(limit, MAX_CONFLICT_GROUPS, MAX_CONFLICT_GROUPS)
    if owner <= 0:
        return {"conflicts": [], "truncated": False}
    groups = _contradictions.detect_conflicts(
        cur, owner_user_id=owner, limit=_contradictions.MAX_SCAN)
    return {
        "conflicts": groups[:bounded],
        "truncated": len(groups) > bounded,
    }
