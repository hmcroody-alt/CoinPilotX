"""The review queue — which facts need the member's attention, and why.

What makes this a queue rather than a filter
--------------------------------------------
Every signal it ranks on already exists somewhere: ``facts.staleness``,
``facts.verification_status``, ``facts.fact_evidence``,
``contradictions.detect_conflicts``. A screen could call all four and sort the
union, and that is exactly what this module exists to stop happening in four
places with four different orderings.

The ordering is the product. A member opens this once a month, works down until
they lose interest, and closes it. Whatever is above the line they stop at is
the only part of the queue that exists, so putting the wrong thing first is not
a cosmetic problem — it is the difference between catching a contradiction and
not.

Why the score is a maximum, not a sum
-------------------------------------
A fact can be stale *and* have an unknown origin *and* have a validity window
ending. Summing those gives 70, which outranks a live contradiction at 100 — so
three mild observations about one dusty row would bury the one case where the
store is holding two different answers and cannot say which it would give.

Taking the maximum keeps the model's promise that "the answer to 'why is this
first' is a sentence": the primary reason *is* the ranking, so the queue can
always say ``CONTRADICTED`` next to the item at the top and be telling the
literal truth about why it is there. Additional reasons are carried on the item
and break ties, which is the most they should ever do.

Computed, never stored
----------------------
No table backs this. Staleness, expiry and source availability are all resolved
at read for the reason spelled out across the package: a stored flag is only as
current as the last sweeper run, and the gap between the horizon passing and the
sweep firing is exactly when somebody acts on a badge that has quietly aged out.
A queue built from cached flags would inherit that window and add its own.

What it deliberately does not do
--------------------------------
It does not write, resolve, archive, or downgrade anything. Appearing in this
queue is an invitation to look, not a verdict — the package's central rule is
that it never decides what is true on its own, and a review queue that
auto-retired what it flagged would be exactly that decision wearing a different
name.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Sequence

from services.private_office import contradictions as _contradictions
from services.private_office import evidence as _evidence
from services.private_office import facts as _facts
from services.private_office import model as _model
from services.private_office import schema as _schema

LOGGER = logging.getLogger("private_office.review")

#: Facts examined in one pass, and items returned. The scan is larger than the
#: page because reasons are computed after the read — a member whose first
#: fifty facts are all healthy still needs the contradiction sitting at row 200.
MAX_REVIEW_SCAN = 500
MAX_REVIEW_ITEMS = 50

#: Distinct evidence refs resolved in one pass. Availability is the only signal
#: here that costs a query per source rather than a field on the row, so it is
#: the only one with its own ceiling. Past it, sources are reported unchecked
#: rather than missing — see :func:`_facts_missing_a_source`.
MAX_REVIEW_REFS = 200

#: How close a validity window has to be to its end before the member is asked
#: about it. A month is roughly "you could still do something about this",
#: which is the only useful definition: warning a year ahead produces an item
#: nobody can action, and warning a day ahead produces one nobody can act on.
VALIDITY_ENDING_DAYS = 30


def _validity_ending(row: dict, at: datetime) -> bool:
    """Is this fact's own claim about *when it is true* running out?

    A window that has already closed counts. A fact still marked ACTIVE whose
    ``valid_to`` passed last month is asserting something it has itself
    declared out of date, and that is a stronger reason to look at it than one
    that expires next week — not a reason to drop it off the list for being too
    late to warn about.
    """
    end = _facts._parse_iso(row.get("valid_to"))
    if end is None:
        return False
    return end <= at + timedelta(days=VALIDITY_ENDING_DAYS)


def _facts_missing_a_source(cur, owner: int, fact_ids: Sequence[int]) -> set[int]:
    """Fact ids with at least one attached source that no longer resolves.

    Batched deliberately. The per-fact path is ``facts.fact_evidence``, and
    calling it once per row would issue a query per fact plus a resolution per
    ref — the N+1 the retrieval stage forbids, on the one screen most likely to
    be opened against a large store.

    Refs beyond :data:`MAX_REVIEW_REFS` are left *unchecked* and therefore
    unflagged. That is the same call ``fact_evidence`` makes and for the same
    reason: reporting a source as gone because the resolver ran out of budget
    would put SOURCE_UNAVAILABLE next to a document that is sitting right
    there, and a queue that cries wolf about missing evidence is one whose
    genuine missing-evidence items get ignored.
    """
    ids = sorted({int(i) for i in fact_ids if int(i or 0) > 0})
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    try:
        cur.execute(
            f"""SELECT fact_id, source_ref FROM {_schema.FACT_EVIDENCE_TABLE}
            WHERE owner_user_id = ? AND detached_at = ''
              AND fact_id IN ({placeholders})
            ORDER BY id ASC LIMIT ?""",
            [owner, *ids, MAX_REVIEW_REFS * 2],
        )
        links = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        # No evidence table on this deployment, or an unreadable one. Reporting
        # every fact's sources as missing would flood the queue with an
        # infrastructure problem dressed up as a data problem.
        LOGGER.warning("PRIVATE_REVIEW_EVIDENCE_READ_FAILED error=%s", exc)
        return set()
    if not links:
        return set()

    distinct: list[str] = []
    for link in links:
        ref = str(link.get("source_ref") or "")
        if ref and ref not in distinct:
            distinct.append(ref)
        if len(distinct) >= MAX_REVIEW_REFS:
            break

    available: dict[str, bool] = {}
    for start in range(0, len(distinct), _evidence.MAX_REFS):
        chunk = distinct[start:start + _evidence.MAX_REFS]
        for entry in _evidence.resolve_refs(cur, owner, chunk):
            available[entry["ref"]] = bool(entry.get("exists"))

    missing: set[int] = set()
    for link in links:
        ref = str(link.get("source_ref") or "")
        if ref in available and not available[ref]:
            missing.add(int(link.get("fact_id") or 0))
    missing.discard(0)
    return missing


def _open_conflict(row: dict, resolutions: dict[str, dict]) -> bool:
    """Is this fact carrying a contradiction nobody has settled?

    The stamped ``conflict_id`` alone is not enough. It is written by
    ``mark_conflicts`` and never cleared, so a fact keeps its marker after the
    member resolves the disagreement — treating the marker as the answer would
    make CONTRADICTED permanent and the queue unclearable.

    A closing resolution settles it only if that resolution was made *about
    this fact*. The stored competing set is what makes that checkable, and the
    check matters: a decision reached about two other rows says nothing about
    whether this one is still in dispute.
    """
    marker = str(row.get("conflict_id") or "").strip()
    if not marker:
        return False
    decision = resolutions.get(marker)
    if decision is None or not decision.get("closed"):
        return True
    return int(row.get("id") or 0) not in (decision.get("competing_fact_ids") or ())


def review_reasons(row: dict, *, missing_source: bool = False,
                   open_conflict: bool = False,
                   at: datetime | None = None) -> list[str]:
    """Every reason this fact wants attention, strongest first.

    Pure: the two signals that need the database are passed in, so this
    function is directly testable against a plain dict and the queue's ordering
    can be exercised without building an evidence graph for each case.
    """
    moment = at or _facts._now()
    verification = _facts.verification_status(row, at=moment)
    effective = verification["effective_state"]
    provenance = _model.normalize_provenance(row.get("provenance_type")) or ""

    reasons: list[str] = []
    if open_conflict:
        reasons.append(_model.REVIEW_CONTRADICTED)
    if effective == _model.VERIFICATION_FAILED:
        reasons.append(_model.REVIEW_VERIFICATION_FAILED)
    if effective == _model.VERIFICATION_DISPUTED:
        reasons.append(_model.REVIEW_DISPUTED)
    if missing_source:
        reasons.append(_model.REVIEW_SOURCE_UNAVAILABLE)
    if verification["expired"]:
        reasons.append(_model.REVIEW_VERIFICATION_EXPIRED)
    if _validity_ending(row, moment):
        reasons.append(_model.REVIEW_VALIDITY_ENDING)
    if effective == _model.VERIFICATION_PENDING_REVIEW:
        # PENDING_REVIEW is the state a fact sits in while it waits for the
        # member to accept or reject it, which is precisely "proposed".
        reasons.append(_model.REVIEW_PROPOSED)
    if provenance == _model.PROVENANCE_LEGACY_UNKNOWN:
        reasons.append(_model.REVIEW_UNKNOWN_ORIGIN)
    if _facts.staleness(row, at=moment)["stale"]:
        reasons.append(_model.REVIEW_STALE)

    return sorted(reasons, key=lambda r: -_model.REVIEW_WEIGHT.get(r, 0))


def review_queue(
    cur,
    *,
    owner_user_id: int,
    limit: int = MAX_REVIEW_ITEMS,
    scan: int = MAX_REVIEW_SCAN,
    reasons: Sequence[str] | None = None,
    at: datetime | None = None,
) -> list[dict]:
    """Facts needing attention, ranked, each carrying its own explanation.

    Only ACTIVE facts are considered. A superseded row has already been
    corrected and an archived one is no longer asserted, so neither is
    something the member can usefully act on — putting them here would fill the
    queue with history.

    ``reasons`` narrows to particular kinds ("show me only contradictions")
    without changing the ranking of what comes back, so a filtered queue and
    the full one agree about relative priority.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    _schema.require_private_schema(cur)
    moment = at or _facts._now()

    wanted: set[str] = set()
    for value in reasons or ():
        name = str(value or "").strip().upper()
        if name in _model.REVIEW_WEIGHT:
            wanted.add(name)

    rows = _facts.list_facts(
        cur, owner_user_id=owner, limit=min(int(scan or MAX_REVIEW_SCAN),
                                            MAX_REVIEW_SCAN))
    if not rows:
        return []

    fact_ids = [int(r.get("id") or 0) for r in rows]
    missing = _facts_missing_a_source(cur, owner, fact_ids)
    resolutions = _contradictions.conflict_resolutions(
        cur, owner_user_id=owner,
        conflict_ids=[str(r.get("conflict_id") or "") for r in rows
                      if str(r.get("conflict_id") or "").strip()] or None)

    items: list[dict] = []
    for row in rows:
        fact_id = int(row.get("id") or 0)
        found = review_reasons(
            row, missing_source=fact_id in missing,
            open_conflict=_open_conflict(row, resolutions), at=moment)
        if not found:
            continue
        if wanted and not (wanted & set(found)):
            continue
        primary = found[0]
        verification = _facts.verification_status(row, at=moment)
        items.append({
            "fact_id": fact_id,
            "subject_type": str(row.get("subject_type") or ""),
            "subject_id": str(row.get("subject_id") or ""),
            "fact_type": str(row.get("fact_type") or ""),
            "domain": str(row.get("domain") or ""),
            "typed_value": row.get("typed_value"),
            "value_type": str(row.get("value_type") or ""),
            "provenance_type": str(row.get("provenance_type") or ""),
            "verification": verification,
            "freshness": _facts.staleness(row, at=moment),
            "conflict_id": str(row.get("conflict_id") or ""),
            "valid_to": str(row.get("valid_to") or ""),
            "reasons": found,
            # The item is ranked by its strongest reason and says so in the
            # same breath, so the order is never something the member has to
            # reverse-engineer from a score they cannot see.
            "primary_reason": primary,
            "priority": _model.REVIEW_WEIGHT.get(primary, 0),
        })

    # Ties break on how many things are wrong, then on id. The id is there so
    # the order is total: two items alike in every respect must not swap places
    # between two reads of the same data, or the member loses their place.
    items.sort(key=lambda item: (-item["priority"], -len(item["reasons"]),
                                 item["fact_id"]))
    return items[:max(0, min(int(limit or MAX_REVIEW_ITEMS), MAX_REVIEW_ITEMS))]


def review_summary(cur, *, owner_user_id: int, scan: int = MAX_REVIEW_SCAN,
                   at: datetime | None = None) -> dict[str, Any]:
    """Counts per reason, plus the total needing attention.

    Counted over the same scan the queue uses, so the badge on a tab and the
    list behind it cannot disagree — a "12 need review" header opening onto
    nine rows is the kind of small inconsistency that costs a member's trust in
    everything else on the screen.

    A fact with three reasons counts once in ``total`` and once under each of
    its reasons, so ``by_reason`` sums to more than the total by design.
    """
    items = review_queue(cur, owner_user_id=owner_user_id, limit=MAX_REVIEW_ITEMS,
                         scan=scan, at=at)
    by_reason = {reason: 0 for reason in _model.REVIEW_REASONS}
    for item in items:
        for reason in item["reasons"]:
            if reason in by_reason:
                by_reason[reason] += 1
    return {
        "total": len(items),
        "by_reason": by_reason,
        "top_reason": items[0]["primary_reason"] if items else "",
        "truncated": len(items) >= MAX_REVIEW_ITEMS,
    }
