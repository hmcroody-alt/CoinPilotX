"""Structural integrity of the fact store — states the writer cannot produce.

What this module is for, and what it is not for
-----------------------------------------------
``review.py`` asks "which of my facts should I look at again". This module asks
a different and narrower question: **is the store itself in a shape that makes
some other answer wrong?**

The distinction is the whole design. A fact that is old, contested or
unverified is a fact the member decides about, and a store full of them is
working correctly. A supersession cycle is not something to decide about — it
means a chain walk returns a truncated list and some screen is about to render
the wrong "current" value. Every finding here should be absent from a healthy
store, which is the strongest property a diagnostic can have: a non-zero count
always needs explaining and is never routine.

That is also why these findings do not go in the review queue. Merged, they
would sit on one weight scale with content reasons, and "this database is
lying to you" would be triaged behind "you have not confirmed your address"
for the rest of time.

Where these states come from
----------------------------
If the writer enforces a rule, how is it ever broken? Three ways, and naming
them is what keeps the checks honest:

* **Legacy backfill.** Rows that predate a rule. ``VERIFICATION_REQUIRES_EVIDENCE``
  is enforced by ``facts.py``, so a verified row with no evidence cannot be
  written today — but it can be *migrated in*.
* **Later change elsewhere.** A fact verified against a document is correct at
  write time. ``documents.delete_document`` destroys the bytes months later and
  the badge is now unsupported, without anything having touched the fact.
* **Direct SQL.** Restored backups, manual fixes, an ops script. The write
  boundary guard covers this repository's code, not a psql session.

So this module is not a paranoid double-check of the writer. It looks for the
damage that arrives *around* the writer, which is the only kind that gets in.

Reads only
----------
Nothing here writes, and the module is deliberately absent from the write
boundary's ``WRITER_MODULES``. That is not an oversight to be corrected later:
a sweep that repaired what it found would be making unsupervised decisions
about a member's records on a screen refresh, and the one thing worse than a
store with a broken supersession pointer is a store that silently rewrote a
pointer while the member was not looking. Findings are reported. Repair is a
separate, deliberate, audited act.

Honesty about what could not be checked
---------------------------------------
Every check here is bounded, and a bounded check has a third answer besides
"clean" and "broken": **could not look**. That answer is reported rather than
rounded down, and the reason is specific to this module's subject matter.

A supersession pointer naming a row outside the scan window is
indistinguishable, from inside the window, from a pointer naming a row that
does not exist. Reporting it as a dangling pointer would tell the member their
history is corrupt because their store is large. So the sweep resolves pointer
targets explicitly and, where even that budget runs out, counts the remainder
under ``uncheckable`` instead of guessing.

This is the same discipline as the tri-state ``available`` on evidence
references, for the same reason: a diagnostic that reports unknowns as faults
gets ignored, and once it is ignored the real faults are invisible too.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.private_office import contradictions as _contradictions
from services.private_office import evidence as _evidence
from services.private_office import facts as _facts
from services.private_office import model as _model
from services.private_office import schema as _schema

LOGGER = logging.getLogger("private_office.integrity")

#: Rows examined in one sweep. Matched to ``review.MAX_REVIEW_SCAN`` and
#: ``read_model.MAX_OVERVIEW_SCAN`` so the three cannot report different
#: pictures of the same store, and bounded for the reason every read here is
#: bounded: an unbounded read of a private store is a full export waiting for
#: one caller to forget a limit.
MAX_INTEGRITY_SCAN = 500

#: Findings returned in one call. A sweep that surfaces a hundred distinct
#: structural faults has already made its point; the hundred-and-first changes
#: nothing about what happens next, and rendering it costs the same as the
#: first.
MAX_INTEGRITY_FINDINGS = 100

#: Supersession pointers followed *outside* the scan window before the rest are
#: declared uncheckable. This budget is what makes ``SUPERSESSION_DANGLING`` a
#: real finding rather than an artefact of the window: without it, every chain
#: straddling the scan boundary would look broken.
MAX_LINK_PROBE = 500

#: Evidence link rows read when checking verified facts. Two per candidate fact
#: is generous — a fact needs *one* resolvable source to clear the check — and
#: the cap exists so a single fact with a thousand citations cannot starve the
#: sweep of every other fact's evidence.
MAX_EVIDENCE_LINKS = 1000

#: Reference resolutions attempted. Beyond this the remaining refs are counted
#: as uncheckable rather than assumed missing, per the module docstring.
MAX_EVIDENCE_REFS = 200

#: Why something could not be examined. These are the keys of the
#: ``uncheckable`` block, and they are named for the *limit that bit* rather
#: than for the check that gave up, because "which budget do I raise" is the
#: only actionable question a reader of this block has.
UNCHECKABLE_SCAN_WINDOW = "scan_window"
UNCHECKABLE_LINK_BUDGET = "link_budget"
UNCHECKABLE_EVIDENCE_BUDGET = "evidence_budget"
UNCHECKABLE_UNRESOLVABLE_REF = "unresolvable_ref"
UNCHECKABLE_EVIDENCE_UNREADABLE = "evidence_unreadable"

UNCHECKABLE_REASONS: tuple[str, ...] = (
    UNCHECKABLE_SCAN_WINDOW,
    UNCHECKABLE_LINK_BUDGET,
    UNCHECKABLE_EVIDENCE_BUDGET,
    UNCHECKABLE_UNRESOLVABLE_REF,
    UNCHECKABLE_EVIDENCE_UNREADABLE,
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _total_facts(cur, owner: int) -> int:
    """Exact row count for the owner, or ``-1`` if it cannot be taken.

    ``-1`` rather than ``0`` on failure. A zero here would make ``complete``
    read True — "the sweep covered everything, there is nothing to cover" —
    off the back of a failed query, which is the precise inversion of what
    happened.
    """
    try:
        cur.execute(
            f"SELECT COUNT(*) AS n FROM {_schema.FACTS_TABLE} WHERE owner_user_id = ?",
            (owner,),
        )
        row = cur.fetchone()
    except Exception as exc:
        LOGGER.warning("PRIVATE_INTEGRITY_COUNT_FAILED error=%s", exc)
        return -1
    if row is None:
        return -1
    data = dict(row)
    try:
        return int(data.get("n") or 0)
    except (TypeError, ValueError):
        return -1


def _links(row: dict) -> tuple[int, int]:
    """``(supersedes_id, superseded_by_id)`` as integers. Zero means no link."""
    try:
        back = int(row.get("supersedes_id") or 0)
    except (TypeError, ValueError):
        back = 0
    try:
        forward = int(row.get("superseded_by_id") or 0)
    except (TypeError, ValueError):
        forward = 0
    return (max(0, back), max(0, forward))


def _probe_links(cur, owner: int, wanted: Sequence[int]) -> dict[int, dict]:
    """Fetch the link columns for ids outside the scan window.

    Only four columns, not whole rows. The sweep needs to know whether these
    ids *exist* and where they point; it does not need their values, and
    pulling values for rows the caller never asked to see would widen a
    structural check into a data read.
    """
    ids = sorted({int(i) for i in wanted if int(i or 0) > 0})[:MAX_LINK_PROBE]
    if not ids:
        return {}
    found: dict[int, dict] = {}
    # Chunked so a large straddling chain does not build a single statement
    # with five hundred bind parameters.
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        placeholders = ",".join("?" * len(chunk))
        try:
            cur.execute(
                f"""SELECT id, supersedes_id, superseded_by_id, lifecycle_state
                FROM {_schema.FACTS_TABLE}
                WHERE owner_user_id = ? AND id IN ({placeholders})""",
                [owner, *chunk],
            )
            for row in cur.fetchall():
                data = dict(row)
                found[int(data.get("id") or 0)] = data
        except Exception as exc:
            LOGGER.warning("PRIVATE_INTEGRITY_LINK_PROBE_FAILED error=%s", exc)
            return found
    found.pop(0, None)
    return found


def _evidence_links(cur, owner: int, fact_ids: Sequence[int]) -> dict[int, list[dict]]:
    """Attached (not detached) evidence links for the given facts, batched.

    Batched for the reason ``review._facts_missing_a_source`` gives: the
    per-fact path issues a query per row plus a resolution per ref, which is
    the N+1 the retrieval stage forbids, on a sweep that by construction looks
    at hundreds of rows.

    A failure returns ``{}`` and is recorded by the caller as unreadable rather
    than as "no evidence anywhere". The difference matters more here than
    almost anywhere else in the package: treating an unreadable evidence table
    as empty would report every verified fact in the store as
    ``VERIFIED_WITHOUT_EVIDENCE`` at once — an infrastructure fault dressed up
    as mass data corruption, and the member would have no way to tell.
    """
    ids = sorted({int(i) for i in fact_ids if int(i or 0) > 0})
    if not ids:
        return {}
    grouped: dict[int, list[dict]] = {}
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        placeholders = ",".join("?" * len(chunk))
        try:
            cur.execute(
                f"""SELECT fact_id, source_ref, relation
                FROM {_schema.FACT_EVIDENCE_TABLE}
                WHERE owner_user_id = ? AND detached_at = ''
                  AND fact_id IN ({placeholders})
                ORDER BY id ASC LIMIT ?""",
                [owner, *chunk, MAX_EVIDENCE_LINKS],
            )
            rows = [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            LOGGER.warning("PRIVATE_INTEGRITY_EVIDENCE_READ_FAILED error=%s", exc)
            raise
        for row in rows:
            grouped.setdefault(int(row.get("fact_id") or 0), []).append(row)
    grouped.pop(0, None)
    return grouped


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
def _finding(kind: str, row: dict, **detail: Any) -> dict:
    """One finding, carrying enough identity to act on without a second query.

    The subject fields are copied in rather than left to the caller to look up,
    because a findings list whose entries are bare fact ids is a list nobody
    can triage: "fact 4471 has a broken pointer" is not actionable until
    somebody fetches fact 4471, and by then the sweep's own ordering has been
    lost. No *value* is copied — identity is what triage needs, and the value
    is the part with the sensitivity rules on it.
    """
    return {
        "kind": kind,
        "severity": _model.integrity_severity(kind),
        "fact_id": int(row.get("id") or 0),
        "subject_type": str(row.get("subject_type") or ""),
        "subject_id": str(row.get("subject_id") or ""),
        "fact_type": str(row.get("fact_type") or ""),
        "lifecycle_state": str(row.get("lifecycle_state") or ""),
        "detail": detail,
    }


def _supersession_findings(
    cur, owner: int, rows: list[dict], known: dict[int, dict],
) -> tuple[list[dict], dict[str, int]]:
    """Link-shape and cycle checks over the loaded window.

    Four distinct faults share this pass because they share the pointer probe,
    and issuing it once is the difference between one extra query and five.
    """
    findings: list[dict] = []
    unchecked: dict[str, int] = {}

    # Every id named by a pointer that is not already in hand. Resolving these
    # is what separates "this pointer is dangling" from "this pointer leaves
    # the window", and without it the sweep would report the second as the
    # first on every store larger than the scan.
    outside: set[int] = set()
    for row in rows:
        back, forward = _links(row)
        for target in (back, forward):
            if target > 0 and target not in known:
                outside.add(target)
    probed = _probe_links(cur, owner, sorted(outside))
    if len(outside) > MAX_LINK_PROBE:
        unchecked[UNCHECKABLE_LINK_BUDGET] = len(outside) - MAX_LINK_PROBE

    # `resolvable` answers "can I say anything about this id at all". An id in
    # neither map was never looked at, and nothing below may conclude from it.
    resolvable: set[int] = set(known) | set(probed)
    beyond: set[int] = {i for i in outside if i not in probed} - set(known)
    # An id we asked about and did not get back genuinely does not exist —
    # unless the probe itself ran short, in which case it is unknown. The
    # budget overflow is already counted above, so only subtract what was
    # actually attempted.
    attempted = set(sorted(outside)[:MAX_LINK_PROBE])
    missing_ids: set[int] = {i for i in attempted if i not in probed}
    beyond -= missing_ids

    def _link_of(target: int) -> tuple[int, int] | None:
        source = known.get(target) or probed.get(target)
        if source is None:
            return None
        return _links(source)

    for row in rows:
        fact_id = int(row.get("id") or 0)
        back, forward = _links(row)
        lifecycle = str(row.get("lifecycle_state") or "").strip().upper()

        # The dangerous one. A row still filed ACTIVE that already has a
        # successor is a value the member corrected and the store kept serving:
        # it passes the lifecycle predicate on every default read, feeds every
        # projection, and nothing on it says a newer answer exists.
        if forward > 0 and lifecycle == _model.LIFECYCLE_ACTIVE:
            findings.append(_finding(
                _model.INTEGRITY_ACTIVE_WITH_SUCCESSOR, row,
                superseded_by_id=forward))

        # The mirror image, and far less dangerous: a row marked SUPERSEDED
        # with nothing named as its replacement. Nothing wrong is being served
        # — the row is already excluded from default reads — but the member's
        # history has a gap where the correction should be, so "what replaced
        # this" has no answer.
        if forward == 0 and lifecycle == _model.LIFECYCLE_SUPERSEDED:
            findings.append(_finding(
                _model.INTEGRITY_SUPERSEDED_WITHOUT_SUCCESSOR, row))

        for target, direction in ((forward, "superseded_by_id"),
                                  (back, "supersedes_id")):
            if target <= 0:
                continue
            if target in missing_ids:
                findings.append(_finding(
                    _model.INTEGRITY_SUPERSESSION_DANGLING, row,
                    pointer=direction, target_id=target))
                continue
            if target not in resolvable:
                continue
            counterpart = _link_of(target)
            if counterpart is None:
                continue
            other_back, other_forward = counterpart
            # A link is a claim made by two rows, and the two can disagree.
            # Checking only one direction would accept a chain that reads as
            # A→B going forward and B→C going back, which walks to a different
            # answer depending on which end the reader started from.
            agreed = (other_back == fact_id if direction == "superseded_by_id"
                      else other_forward == fact_id)
            if not agreed:
                findings.append(_finding(
                    _model.INTEGRITY_SUPERSESSION_ASYMMETRIC, row,
                    pointer=direction, target_id=target,
                    counterpart_supersedes_id=other_back,
                    counterpart_superseded_by_id=other_forward))

    if beyond:
        unchecked[UNCHECKABLE_SCAN_WINDOW] = len(beyond)

    # Cycles, walked here rather than through `facts.fact_chain`. That helper
    # is bounded and *returns a truncated chain* when it revisits an id, which
    # is right for a reader — a request should not hang — and useless as a
    # detector, because a truncated chain and a legitimately long one are the
    # same value. Detection needs the seen-set itself, so the walk is repeated
    # with the loop as the thing being looked for instead of the thing being
    # survived.
    on_cycle: set[int] = set()
    settled: set[int] = set()
    for row in rows:
        start = int(row.get("id") or 0)
        if start in settled:
            continue
        path: list[int] = []
        seen: set[int] = set()
        cursor = start
        for _ in range(_facts.MAX_CHAIN):
            if cursor <= 0 or cursor not in resolvable:
                break
            if cursor in seen:
                on_cycle.update(path[path.index(cursor):])
                break
            if cursor in on_cycle:
                on_cycle.update(path)
                break
            seen.add(cursor)
            path.append(cursor)
            link = _link_of(cursor)
            if link is None:
                break
            cursor = link[1]
        settled.update(seen)

    for row in rows:
        fact_id = int(row.get("id") or 0)
        if fact_id in on_cycle:
            findings.append(_finding(
                _model.INTEGRITY_SUPERSESSION_CYCLE, row,
                superseded_by_id=_links(row)[1]))

    return findings, unchecked


def _provenance_findings(rows: list[dict]) -> list[dict]:
    """Whether each row's stated origin can still be read and reached.

    Two checks, and the gap between them is the point.

    ``PROVENANCE_UNREADABLE`` is unambiguous: ``provenance_ref`` holds
    something, and ``decode_provenance_ref`` — which never raises, and returns
    an empty ref for anything it cannot parse — got nothing out of it. The
    column is non-empty and means nothing.

    ``PROVENANCE_ORPHANED`` is deliberately narrow, and most rows can never
    trigger it. ``provenance_ref`` is not a foreign key: ``source_type`` and
    ``source_id`` are free strings describing an origin that may well be
    outside this system entirely — a bank statement, a phone call, a registry
    the member read. There is nothing to dangle. Only when those two fields
    happen to spell a reference in the evidence vocabulary is there a row to go
    and look for, and only then can its absence mean anything. Everything else
    is not clean and not broken; it is outside what this check can see, and
    saying so is the entire reason the check is written this way rather than as
    a join.
    """
    findings: list[dict] = []
    for row in rows:
        raw = str(row.get("provenance_ref") or "").strip()
        if not raw:
            continue
        decoded = _facts.decode_provenance_ref(raw)
        if not any((decoded.source_type, decoded.source_id,
                    decoded.locator, decoded.observed_at)):
            findings.append(_finding(
                _model.INTEGRITY_PROVENANCE_UNREADABLE, row,
                length=len(raw)))
    return findings


def _provenance_orphans(
    cur, owner: int, rows: list[dict],
) -> tuple[list[dict], dict[str, int]]:
    """The subset of provenance references that name a real, checkable row.

    Resolution goes through ``evidence.resolve_refs`` rather than a hand-rolled
    lookup so that "does this source still exist" has exactly one answer in the
    package. That resolver already treats a deleted document as gone, another
    member's row as absent, and a table this deployment has never created as
    unverifiable — three rules that would each have to be re-derived, and would
    each eventually be re-derived differently, in a second implementation.
    """
    findings: list[dict] = []
    unchecked: dict[str, int] = {}
    candidates: list[tuple[dict, str]] = []
    for row in rows:
        decoded = _facts.decode_provenance_ref(row.get("provenance_ref"))
        ref = _evidence.format_ref(decoded.source_type, decoded.source_id)
        if ref:
            candidates.append((row, ref))
    if not candidates:
        return findings, unchecked

    wanted: list[str] = []
    for _row, ref in candidates:
        if ref not in wanted:
            wanted.append(ref)
    if len(wanted) > MAX_EVIDENCE_REFS:
        unchecked[UNCHECKABLE_EVIDENCE_BUDGET] = len(wanted) - MAX_EVIDENCE_REFS
        wanted = wanted[:MAX_EVIDENCE_REFS]

    available: dict[str, bool] = {}
    for start in range(0, len(wanted), _evidence.MAX_REFS):
        chunk = wanted[start:start + _evidence.MAX_REFS]
        for entry in _evidence.resolve_refs(cur, owner, chunk):
            available[str(entry.get("ref") or "")] = bool(entry.get("exists"))

    for row, ref in candidates:
        # `is False`, never falsiness. A ref absent from the map was not
        # resolved — it fell outside the budget, or the resolver dropped it —
        # and an absent key is an unanswered question, not a negative answer.
        if available.get(ref) is False:
            findings.append(_finding(
                _model.INTEGRITY_PROVENANCE_ORPHANED, row, source_ref=ref))
    return findings, unchecked


def _verification_findings(
    cur, owner: int, rows: list[dict],
) -> tuple[list[dict], dict[str, int]]:
    """No verified badge without something underneath it.

    ``VERIFICATION_REQUIRES_EVIDENCE`` is enforced by the writer, so a row here
    got in around it — a legacy backfill, or a document deleted long after the
    fact was verified. The second case is the common one and it is why this
    check cannot be a one-off migration: it becomes true with time, without
    anything touching the fact.

    The two findings are separated because the remedies differ. Nothing at all
    attached means the claim was never substantiated and the state should come
    down. Links that no longer resolve means it *was* substantiated and the
    support has since gone — the member may still have the document, and
    telling them their verification was baseless would be wrong.

    **The stored column is read, not the computed status.** ``read_model`` and
    ``review`` both compute expiry rather than trusting
    ``verification_state``, and doing the same here would be wrong for this
    check specifically. ``VERIFICATION_REQUIRES_EVIDENCE`` is an invariant on
    what is *written*, so it is violated or not by what is in the column,
    independently of the clock. Filtering by computed status would mean an
    unsupportable badge stops being a finding once it ages out and becomes one
    again the moment anybody re-verifies — a fault that heals itself by the
    passage of time and returns on a refresh is a fault nobody will ever
    successfully fix.
    """
    findings: list[dict] = []
    unchecked: dict[str, int] = {}
    candidates = [
        row for row in rows
        if _model.verification_needs_evidence(row.get("verification_state"))
    ]
    if not candidates:
        return findings, unchecked

    fact_ids = [int(row.get("id") or 0) for row in candidates]
    try:
        links = _evidence_links(cur, owner, fact_ids)
    except Exception:
        # Already logged. Every candidate is unchecked rather than unsupported:
        # see `_evidence_links` for why the alternative is unacceptable.
        unchecked[UNCHECKABLE_EVIDENCE_UNREADABLE] = len(candidates)
        return findings, unchecked

    supporting: dict[int, list[str]] = {}
    for fact_id, rows_for_fact in links.items():
        refs: list[str] = []
        for link in rows_for_fact:
            if not _model.evidence_supports(link.get("relation")):
                continue
            ref = str(link.get("source_ref") or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        supporting[fact_id] = refs

    distinct: list[str] = []
    for refs in supporting.values():
        for ref in refs:
            if ref not in distinct:
                distinct.append(ref)
    if len(distinct) > MAX_EVIDENCE_REFS:
        unchecked[UNCHECKABLE_EVIDENCE_BUDGET] = (
            unchecked.get(UNCHECKABLE_EVIDENCE_BUDGET, 0)
            + len(distinct) - MAX_EVIDENCE_REFS)
        distinct = distinct[:MAX_EVIDENCE_REFS]

    available: dict[str, bool] = {}
    for start in range(0, len(distinct), _evidence.MAX_REFS):
        chunk = distinct[start:start + _evidence.MAX_REFS]
        for entry in _evidence.resolve_refs(cur, owner, chunk):
            available[str(entry.get("ref") or "")] = bool(entry.get("exists"))

    unresolvable = 0
    for row in candidates:
        fact_id = int(row.get("id") or 0)
        refs = supporting.get(fact_id) or []
        if not refs:
            findings.append(_finding(
                _model.INTEGRITY_VERIFIED_WITHOUT_EVIDENCE, row,
                verification_state=str(row.get("verification_state") or ""),
                attached=len(links.get(fact_id) or [])))
            continue
        verdicts = [available[ref] for ref in refs if ref in available]
        if not verdicts:
            # Every citation on this fact is one nobody can check — a ref in a
            # kind the resolver does not know, or one past the budget. The
            # badge is neither confirmed nor refuted, and calling it unsupported
            # would condemn a fact on the strength of not having looked.
            unresolvable += 1
            continue
        if not any(verdicts):
            findings.append(_finding(
                _model.INTEGRITY_VERIFIED_EVIDENCE_UNRESOLVABLE, row,
                verification_state=str(row.get("verification_state") or ""),
                refs=len(refs)))
    if unresolvable:
        unchecked[UNCHECKABLE_UNRESOLVABLE_REF] = unresolvable
    return findings, unchecked


def _duplicate_findings(rows: list[dict]) -> list[dict]:
    """Genuinely redundant rows — and nothing else.

    This check is the one most likely to do harm if it is written the obvious
    way, so the definition is narrow on purpose and every narrowing is load
    bearing.

    **Identical rows cannot exist.** ``fact_key`` hashes subject, fact type,
    value, provenance type, provenance ref and ``valid_from`` under
    ``UNIQUE(owner_user_id, fact_key)``. Two rows that agree on all of those
    are one row. So "find the duplicates" cannot mean "find identical rows";
    there are none, by construction.

    **Two sources agreeing is corroboration, not duplication.** The same value
    recorded from a bank statement and from a registry differs in
    ``provenance_ref``, so both rows exist — and that is the store working. A
    sweep that reported them as duplicates would be advising the member to
    delete an independent second source, which is the opposite of what this
    package is for. Provenance identity is therefore part of the grouping key,
    not something the check looks past.

    **Two periods are not two copies.** Same subject, same fact type, same
    value, same source, different ``valid_from`` is the ordinary shape of a
    thing that was true, stopped being true, and became true again. Only rows
    whose validity windows *overlap* are asserting the same value about the
    same moment twice, and ``contradictions.windows_overlap`` is reused for
    that judgement rather than reimplemented — it already knows that an
    open-ended earlier claim followed by a later one is a handover rather than
    an overlap, and a second implementation would eventually stop knowing it.

    What survives all three: the same source saying the same thing about the
    same period, recorded twice, differing only in a locator or a timestamp.
    That is a duplicate, it is worth removing, and it is the only case here.

    Only ACTIVE rows are considered. A superseded row is *supposed* to duplicate
    its successor's subject and type — that is what a correction looks like —
    and including history would make every corrected fact in the store a
    duplicate of itself.
    """
    findings: list[dict] = []
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if str(row.get("lifecycle_state") or "").strip().upper() != _model.LIFECYCLE_ACTIVE:
            continue
        decoded = _facts.decode_provenance_ref(row.get("provenance_ref"))
        key = (
            str(row.get("subject_type") or ""),
            str(row.get("subject_id") or ""),
            str(row.get("fact_type") or ""),
            str(row.get("value_type") or ""),
            str(row.get("typed_value") or ""),
            str(row.get("provenance_type") or ""),
            decoded.source_type,
            decoded.source_id,
        )
        groups.setdefault(key, []).append(row)

    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda r: int(r.get("id") or 0))
        for index, later in enumerate(ordered[1:], start=1):
            # The *earliest* overlapping row is named as the original, so the
            # finding is stable: adding a third copy does not renumber which
            # row the first two findings pointed at.
            for earlier in ordered[:index]:
                if _contradictions.windows_overlap(earlier, later):
                    findings.append(_finding(
                        _model.INTEGRITY_DUPLICATE_VALUE, later,
                        duplicate_of=int(earlier.get("id") or 0)))
                    break
    return findings


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------
def check_integrity(
    cur,
    *,
    owner_user_id: int,
    scan: int = MAX_INTEGRITY_SCAN,
    limit: int = MAX_INTEGRITY_FINDINGS,
    kinds: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Sweep one member's fact store for structural faults.

    Returns ``{"findings", "counts", "scanned", "total", "complete",
    "truncated", "uncheckable"}``.

    ``counts`` is over everything the sweep found, and ``findings`` is the
    bounded, ranked slice of it that came back. Those are different numbers
    whenever ``truncated`` is true, and they are reported separately for the
    same reason ``read_model`` separates exact counts from observed ones: a
    caller drawing "3 problems" off the length of a truncated list is drawing
    the ceiling, not the store.

    ``complete`` means the scan window covered every row the member has. It is
    False — not omitted, and never quietly True — when the row count could not
    be taken at all, because a store whose size is unknown has definitionally
    not been swept in full.

    ``kinds`` narrows to particular findings without changing severity order,
    so a filtered sweep and a full one agree about what matters most. It
    filters the returned list and not the ``counts``: "show me only the cycles"
    is a request about attention, not a request to be told the rest are gone.
    """
    owner = int(owner_user_id or 0)
    empty: dict[str, Any] = {
        "findings": [], "counts": {}, "scanned": 0, "total": 0,
        "complete": False, "truncated": False, "uncheckable": {},
    }
    if owner <= 0:
        return empty
    _schema.require_private_schema(cur)

    window = max(1, min(int(scan or MAX_INTEGRITY_SCAN), MAX_INTEGRITY_SCAN))
    ceiling = max(1, min(int(limit or MAX_INTEGRITY_FINDINGS),
                         MAX_INTEGRITY_FINDINGS))

    wanted: set[str] = set()
    for value in kinds or ():
        name = _model.normalize_integrity_finding(value)
        if name:
            wanted.add(name)

    # `include_superseded=True` because history is exactly what this sweep is
    # about. A supersession cycle lives entirely among superseded rows, and a
    # check that only saw ACTIVE ones would be structurally incapable of
    # finding the fault it is named for.
    rows = _facts.list_facts(
        cur, owner_user_id=owner, include_superseded=True, limit=window)
    total = _total_facts(cur, owner)
    scanned = len(rows)
    if not rows:
        return {**empty, "total": max(0, total), "complete": total == 0}

    known: dict[int, dict] = {}
    for row in rows:
        fact_id = int(row.get("id") or 0)
        if fact_id > 0:
            known[fact_id] = row

    findings: list[dict] = []
    uncheckable: dict[str, int] = {}

    def _absorb(produced: list[dict], unchecked: dict[str, int] | None = None) -> None:
        findings.extend(produced)
        for reason, count in (unchecked or {}).items():
            uncheckable[reason] = uncheckable.get(reason, 0) + int(count or 0)

    # Each pass is independent and each is allowed to fail on its own. A sweep
    # that aborts entirely because the evidence table is missing would report
    # nothing about supersession, which is both readable and unrelated — and
    # "the integrity check errored" is the least useful possible answer to
    # "is my store sound".
    try:
        _absorb(*_supersession_findings(cur, owner, rows, known))
    except Exception as exc:
        LOGGER.warning("PRIVATE_INTEGRITY_SUPERSESSION_FAILED error=%s", exc)
    try:
        _absorb(_provenance_findings(rows))
        _absorb(*_provenance_orphans(cur, owner, rows))
    except Exception as exc:
        LOGGER.warning("PRIVATE_INTEGRITY_PROVENANCE_FAILED error=%s", exc)
    try:
        _absorb(*_verification_findings(cur, owner, rows))
    except Exception as exc:
        LOGGER.warning("PRIVATE_INTEGRITY_VERIFICATION_FAILED error=%s", exc)
    try:
        _absorb(_duplicate_findings(rows))
    except Exception as exc:
        LOGGER.warning("PRIVATE_INTEGRITY_DUPLICATE_FAILED error=%s", exc)

    counts: dict[str, int] = {}
    for item in findings:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    selected = [f for f in findings if not wanted or f["kind"] in wanted]
    # Severity first, then fact id, then kind. The last two are not cosmetic:
    # without a total order the same store yields a different page on every
    # call, and a list that reshuffles under the member is one they cannot work
    # through.
    selected.sort(key=lambda f: (-int(f.get("severity") or 0),
                                 int(f.get("fact_id") or 0),
                                 str(f.get("kind") or "")))
    truncated = len(selected) > ceiling

    return {
        "findings": selected[:ceiling],
        "counts": counts,
        "scanned": scanned,
        "total": max(0, total),
        "complete": total >= 0 and scanned >= total,
        "truncated": truncated,
        "uncheckable": uncheckable,
    }


def integrity_summary(
    cur, *, owner_user_id: int, scan: int = MAX_INTEGRITY_SCAN,
) -> dict[str, Any]:
    """Counts only — no per-fact detail — for an overview panel.

    Separate from :func:`check_integrity` rather than a flag on it, because the
    two have different privacy shapes. This one names no fact and no subject,
    so it can sit on a dashboard next to figures that are already aggregate.
    ``total_findings`` is summed from ``counts`` rather than from the returned
    list, so it does not silently become the truncation ceiling.
    """
    report = check_integrity(cur, owner_user_id=owner_user_id, scan=scan, limit=1)
    counts = report.get("counts") or {}
    return {
        "counts": counts,
        "total_findings": sum(int(v or 0) for v in counts.values()),
        "scanned": report.get("scanned", 0),
        "total": report.get("total", 0),
        "complete": bool(report.get("complete")),
        "uncheckable": report.get("uncheckable") or {},
    }
