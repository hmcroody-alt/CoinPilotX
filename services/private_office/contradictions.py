"""Stage 13 — deciding when two facts actually disagree.

The distinction the whole module turns on
-----------------------------------------
Stage 13 is explicit that ordinary temporal change is not a contradiction::

    a property valued at 800,000 in 2024 and 950,000 in 2026   -> not a conflict
    an ownership share of 35% and 40% for the same period      -> a conflict

Both pairs are "two different values for one fact type about one subject". The
difference is entirely *when each one claims to be true*, and a detector that
ignores that produces a system which flags every asset whose value ever moved.
That failure is not merely noisy — it is corrosive. A conflict list where most
entries are ordinary history is a list nobody reads, and the one entry that
matters arrives in it.

So the rule here is a rule about intervals, not about values:

**An open-ended assertion is implicitly superseded by a later assertion about
the same subject and fact type.** "The property is worth 800k, from 2024" stops
claiming anything about 2026 the moment "the property is worth 950k, from 2026"
is recorded. Their windows no longer intersect, so their values are never
compared. Two claims recorded for the *same* effective moment do intersect, and
those are compared.

Everything else follows from that. A closed window is honoured as written; two
readings taken within :data:`SIMULTANEITY_HOURS` of each other are treated as
describing the same moment rather than as an instantaneous change.

Why nothing is resolved automatically
-------------------------------------
Stage 13 ends with "UNDX must not silently choose", and this module is where
that is either held or lost. It would be easy — and would look like an
improvement — to let the stronger provenance win: a VERIFIED read-back outranks
a USER_ASSERTED number, so mark the weaker one superseded and move on.

That is wrong twice. It is wrong on the facts, because the case where a member's
own statement disagrees with a provider's record is precisely the case where the
provider might be the one that is out of date. And it is wrong on the product,
because the honest answer to "when does my policy renew?" when two sources say
two dates is to say so and ask, which is a *better* answer than a confident
wrong date and is not reachable once one of the two rows has been quietly
retired.

So conflicts are recorded and left ``unresolved`` until a *person* settles them.
The rows keep their original provenance — deliberately, rather than being
restamped ``CONFLICTING`` — because "your insurer's record says March, the policy
document says April" is only sayable while both rows still remember where they
came from. Overwriting provenance with the fact that there is a conflict destroys
the material needed to explain it.

Contested is a state, not a source
----------------------------------
What the rows *do* get is ``verification_state = CONFLICTING``. That is the
second axis the ledger core exists to provide, and it is what makes the sentence
above survivable: provenance keeps answering "where did this come from", while
verification answers "what has since been done about it", and a contested row now
says both. Before the two axes were separated there was exactly one column, so
recording the conflict meant destroying the provenance — which is why the
original code recorded nothing and left ``CONFLICTING`` as a word the package
knew and never used.

The consequence is that a contested fact stops reading as current truth
everywhere at once: ``CONFLICTING`` is in ``UNTRUSTWORTHY_VERIFICATION`` and
ranks at zero, so a briefing, a projection or an UNDX answer that would have
quoted it now cannot. Marking a conflict and leaving every competitor still
reading as believable was the gap — the disagreement was findable by anyone who
went looking for it, and invisible to everyone who did not.

Resolution, and why it needs a durable record
---------------------------------------------
Detection is stateless: it re-derives the same conflict from the same rows every
time it runs. Without somewhere to record that a member has already decided, the
member is asked the same question every day forever, and the third or fourth
time they are asked they will start clicking whatever makes it stop. That is how
a truth ledger acquires values nobody believes.

:func:`resolve_conflict` is the settlement. It is an owner action — the writer
refuses any other actor, because "UNDX must not silently choose" is not a
preference that survives a convenient exception — and the winner it nominates
lands on ``USER_CONFIRMED``, never ``VERIFIED``. A member weighing two sources
is exercising judgement, which is the most a person can contribute and is still
not a system of record being read.

Values, and where they may go
-----------------------------
:func:`detect_conflicts` returns typed values, because its caller is answering
the owner's own question about the owner's own data. Nothing in this module logs
them: every log line and every audit row carries ids, counts and fact types
only, per Stage 18 and rule 8.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta
from typing import Any, Sequence

from services.private_office import audit as _audit
from services.private_office import facts as _facts
from services.private_office import model as _model
from services.private_office import schema as _schema
from services.private_office import telemetry as _telemetry

LOGGER = logging.getLogger("private_office.contradictions")

#: Two readings whose effective windows start within this many hours of each
#: other are treated as describing the same moment. Without a window, two facts
#: recorded a minute apart would be "sequential states" and never compared,
#: which would make the detector trivially defeatable by recording the second
#: value a moment after the first — the exact shape of a bad import.
SIMULTANEITY_HOURS = 24

#: How a conflict was settled. A closed vocabulary owned by this module, never
#: anything a member typed — the same rule ``reason_code`` follows in the writer.
#:
#: ``RESOLUTION_DISMISSED`` is not a lesser outcome. A member may look at two
#: figures and conclude that the sources were never describing the same thing,
#: or that both are wrong. Offering only "pick a winner" would force them to
#: nominate a value they do not believe in order to clear the prompt.
RESOLUTION_WINNER = "winner_chosen"
RESOLUTION_DISMISSED = "dismissed"
RESOLUTIONS: tuple[str, ...] = (RESOLUTION_WINNER, RESOLUTION_DISMISSED)

#: What happens to the rows that did not win.
#:
#: ``DISPOSITION_DISPUTE`` is the default, and the default is the reversible one
#: on purpose. Disputing leaves the losing figure live and flagged, so a member
#: who chose wrong can still see what they rejected and change their mind.
#: ``DISPOSITION_SUPERSEDE`` retires it — correct when the loser is genuinely a
#: stale reading rather than a rival account, and terminal, which is why it is
#: never what happens unless a caller asks for it by name.
DISPOSITION_DISPUTE = "dispute"
DISPOSITION_SUPERSEDE = "supersede"
DISPOSITION_NONE = "none"
DISPOSITIONS: tuple[str, ...] = (
    DISPOSITION_DISPUTE, DISPOSITION_SUPERSEDE, DISPOSITION_NONE,
)

#: Outcomes of :func:`resolve_conflict`, kept distinct for the same reason the
#: writer keeps its four apart: "there is no such conflict" and "you may not do
#: that" are different answers and a caller that cannot tell them apart cannot
#: say anything useful to the member.
RESOLVE_APPLIED = "applied"
RESOLVE_REFUSED = "refused"
RESOLVE_NOT_FOUND = "not_found"

#: When two numbers count as materially different.
#:
#: Money and plain numbers use a *relative* tolerance because their disagreement
#: scales: 1,000 apart on 950,000 is a rounding difference between two systems,
#: and 1,000 apart on 1,200 is two different answers. Percentages use an
#: *absolute* tolerance because they do not scale — 35% versus 40% is five
#: points whether the subject is a company or a policy excess, and a relative
#: rule would make disagreements about small percentages invisible.
RELATIVE_TOLERANCE: dict[str, float] = {
    _model.VALUE_MONEY: 0.005,
    _model.VALUE_NUMBER: 0.005,
}
ABSOLUTE_TOLERANCE: dict[str, float] = {
    _model.VALUE_PERCENT: 0.01,
}

#: Facts examined in one detection pass. A private store is not large, but an
#: unbounded scan is a full read of one member's most sensitive table triggered
#: by whatever calls this, and the pairwise comparison below is quadratic in
#: group size.
MAX_SCAN = 2000
MAX_GROUP = 50

REASON_NUMERIC = "values_differ_beyond_tolerance"
REASON_DATE = "dates_differ"
REASON_BOOLEAN = "boolean_values_differ"
REASON_TEXT = "text_values_differ"

_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Interval logic
# ---------------------------------------------------------------------------
def _interval(row: dict) -> tuple[Any, Any]:
    """``(start, end)`` for a fact, where ``end`` may be ``None`` for open."""
    return (_facts._parse_iso(row.get("valid_from")), _facts._parse_iso(row.get("valid_to")))


def windows_overlap(left: dict, right: dict) -> bool:
    """Do these two facts claim to describe a common moment?

    This is where "ordinary temporal change" is separated from disagreement, so
    the three cases are spelled out rather than folded into one comparison:

    1. Either fact cannot be placed in time — treated as **not** overlapping.
       A fact with an unparseable ``valid_from`` is one the store cannot reason
       about, and inventing an overlap for it would manufacture conflicts out of
       bad data.
    2. The earlier fact is open-ended and the later one starts materially after
       it — the later assertion supersedes it. This is the 800k-then-950k case
       and it is not a conflict.
    3. Otherwise, plain interval intersection.
    """
    left_start, left_end = _interval(left)
    right_start, right_end = _interval(right)
    if left_start is None or right_start is None:
        return False

    # Order the pair so `e_` is the earlier claim and `l_` the later one.
    if left_start <= right_start:
        e_start, e_end, l_start, l_end = left_start, left_end, right_start, right_end
    else:
        e_start, e_end, l_start, l_end = right_start, right_end, left_start, left_end

    separation = l_start - e_start
    simultaneous = separation <= timedelta(hours=SIMULTANEITY_HOURS)

    if e_end is None and not simultaneous:
        # The earlier claim was open-ended and a later claim about the same
        # subject and fact type has arrived. The earlier one is implicitly
        # closed at the later one's start, so they describe different periods.
        return False

    if e_end is None:
        # Open-ended and simultaneous: two claims about the same moment.
        return True

    # Both bounded on the earlier side. They intersect when the later one starts
    # before the earlier one ends. Equality is not an overlap — a window that
    # ends exactly where the next begins is a clean handover.
    if l_start >= e_end:
        return False
    if l_end is not None and l_end <= e_start:
        return False
    return True


# ---------------------------------------------------------------------------
# Value comparison
# ---------------------------------------------------------------------------
def _normalize_text(value: object) -> str:
    text = _PUNCT_RE.sub(" ", str(value or "").strip().lower())
    return _WS_RE.sub(" ", text).strip()


def materially_incompatible(left: dict, right: dict) -> str | None:
    """The reason these two values disagree, or ``None`` if they do not.

    Returns a reason code rather than a boolean so the conflict record can say
    *how* the two facts differ without the caller re-deriving it, and so a
    numeric near-miss and a text difference are distinguishable in the QA
    output rather than both arriving as "conflict".
    """
    kind = _model.normalize_value_type(left.get("value_type"))
    other_kind = _model.normalize_value_type(right.get("value_type"))
    if not kind or kind != other_kind:
        # Two facts of the same type recorded with different value types is a
        # modelling problem, not a disagreement about the world, and guessing a
        # comparison across types would produce conflicts nobody can act on.
        return None

    if kind in _model.NUMERIC_VALUE_TYPES:
        try:
            a = float(left.get("value_number"))
            b = float(right.get("value_number"))
        except (TypeError, ValueError):
            return None
        gap = abs(a - b)
        absolute = ABSOLUTE_TOLERANCE.get(kind)
        if absolute is not None:
            return REASON_NUMERIC if gap > absolute else None
        scale = max(abs(a), abs(b))
        tolerance = RELATIVE_TOLERANCE.get(kind, 0.0) * scale
        return REASON_NUMERIC if gap > tolerance else None

    if kind == _model.VALUE_DATE:
        # No tolerance, on purpose. Two renewal dates a day apart are not nearly
        # the same date; they are two answers to a question that has one, and a
        # tolerance here would swallow exactly the conflict Stage 21 exists to
        # surface.
        return REASON_DATE if str(left.get("typed_value")) != str(right.get("typed_value")) else None

    if kind == _model.VALUE_BOOLEAN:
        return REASON_BOOLEAN if str(left.get("typed_value")) != str(right.get("typed_value")) else None

    a_text = _normalize_text(left.get("typed_value"))
    b_text = _normalize_text(right.get("typed_value"))
    if not a_text or not b_text or a_text == b_text:
        return None
    if a_text in b_text or b_text in a_text:
        # One string containing the other is an elaboration — "12 Rue Test" and
        # "12 Rue Test, Paris" are the same address written at two levels of
        # detail. Flagging that as a contradiction is how the conflict list
        # fills with noise and stops being read.
        return None
    return REASON_TEXT


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def conflict_id(owner_user_id: int, fact_keys: Sequence[str]) -> str:
    """Deterministic id for one conflict.

    Derived from the owner and the sorted keys of the competing facts, so
    re-running detection produces the same id and a conflict can be marked,
    re-marked and referenced across processes without a registry. Nothing that
    identifies the *values* goes into it.
    """
    raw = "\x1f".join([str(int(owner_user_id or 0))] + sorted(str(k) for k in fact_keys))
    return "conf_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _competing_entry(row: dict) -> dict:
    return {
        "fact_id": int(row.get("id") or 0),
        "fact_key": str(row.get("fact_key") or ""),
        "typed_value": row.get("typed_value"),
        "value_type": row.get("value_type"),
        "provenance_type": row.get("provenance_type"),
        "provenance": _facts.decode_provenance_ref(row.get("provenance_ref")).__dict__.copy(),
        "confidence": row.get("confidence"),
        "observed_at": row.get("observed_at"),
        "valid_from": row.get("valid_from"),
        "valid_to": row.get("valid_to"),
        "freshness": _facts.staleness(row),
    }


def _row_value(row, name: str, index: int):
    """Read one column from a cursor row that may or may not be a mapping.

    The same accessor the writer uses. Some callers hand this package a
    ``sqlite3.Row``, some a plain tuple from a PostgreSQL cursor, and indexing a
    tuple by name fails at a point far from the configuration that caused it.
    """
    return row[name] if hasattr(row, "keys") else row[index]


def load_resolutions(
    cur,
    *,
    owner_user_id: int,
    conflict_ids: Sequence[str],
) -> dict[str, dict]:
    """Settled conflicts among ``conflict_ids``, keyed by conflict id.

    One read for the whole batch rather than one per conflict. Detection runs on
    every retrieval, so a lookup that scaled with the number of disagreements a
    member has would make the members with the most conflicts — the ones the
    feature exists for — the slowest to serve.
    """
    owner = int(owner_user_id or 0)
    wanted = [str(value)[:64] for value in conflict_ids or () if str(value or "").strip()]
    if owner <= 0 or not wanted:
        return {}
    found: dict[str, dict] = {}
    # Chunked because a member with a very noisy import can produce more
    # conflicts than some drivers accept bound parameters for, and the failure
    # mode of exceeding that limit is an exception from the driver rather than
    # anything this package could explain.
    for start in range(0, len(wanted), 200):
        chunk = wanted[start:start + 200]
        placeholders = ",".join("?" * len(chunk))
        cur.execute(
            f"SELECT conflict_id, resolution, winning_fact_id, loser_disposition, "
            f"resolved_by_actor_type, resolved_at "
            f"FROM {_schema.FACT_CONFLICTS_TABLE} "
            f"WHERE owner_user_id = ? AND conflict_id IN ({placeholders})",
            [owner, *chunk],
        )
        for row in cur.fetchall() or ():
            marker = str(_row_value(row, "conflict_id", 0) or "")
            if not marker:
                continue
            found[marker] = {
                "conflict_id": marker,
                "resolution": str(_row_value(row, "resolution", 1) or ""),
                "winning_fact_id": int(_row_value(row, "winning_fact_id", 2) or 0),
                "loser_disposition": str(_row_value(row, "loser_disposition", 3) or ""),
                "resolved_by_actor_type": str(_row_value(row, "resolved_by_actor_type", 4) or ""),
                "resolved_at": str(_row_value(row, "resolved_at", 5) or ""),
            }
    return found


def detect_conflicts(
    cur,
    *,
    owner_user_id: int,
    subject_type: str | None = None,
    subject_id: object = None,
    subject_ids: Sequence[object] | None = None,
    fact_types: Sequence[str] | None = None,
    limit: int = MAX_SCAN,
    include_resolved: bool = False,
) -> list[dict]:
    """Unresolved contradictions in one owner's facts.

    Each entry is ``{"conflict_id", "owner_user_id", "subject_type",
    "subject_id", "fact_type", "reason", "competing_fact_ids", "competing",
    "unresolved", "resolution"}``.

    Detection itself is stateless and derives nothing from history: the same
    rows always produce the same conflict with the same id. What changes the
    answer is :func:`resolve_conflict` having written a settlement, which is
    looked up here and, by default, removes the conflict from the result. That
    default is the whole point of the resolution record — a conflict a member
    has already decided must stop being presented as an open question, or the
    review queue becomes something they learn to dismiss without reading. Pass
    ``include_resolved=True`` to see settled ones as well, with ``unresolved``
    false and the settlement under ``resolution``; a history view wants that and
    a prompt never does.

    Note what does *not* suppress a conflict: a new competing source. The
    conflict id is derived from the fact keys of the competitors, so a third
    source arriving changes the id and the settlement no longer matches. That
    looks like a bug and is the correct behaviour — the member settled "the
    insurer says March, the document says April", not "and the broker says
    June".

    ``subject_ids`` asks about many subjects in **one** read. Stage 37: the
    grouping below is already keyed by subject, so answering for a hundred
    nodes never needed a hundred queries — but ``retrieval`` had no way to say
    so and looped on ``subject_id`` instead, which is the N+1 that stage
    forbids. Passing ``subject_ids`` requires ``subject_type``, because a batch
    read that did not pin the subject kind would compare a node's facts against
    a document's. ``subject_id`` and ``subject_ids`` are mutually exclusive;
    the batch wins if both arrive, since it is the more specific request.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    _schema.require_private_schema(cur)

    scan = min(int(limit or MAX_SCAN), MAX_SCAN)
    if subject_ids is not None:
        wanted = [str(value) for value in subject_ids if str(value or "").strip()]
        if not wanted or not subject_type:
            return []
        rows = _facts.list_facts_for_subjects(
            cur,
            owner_user_id=owner,
            subject_type=subject_type,
            subject_ids=wanted,
            fact_types=fact_types,
            limit=scan,
        )
    else:
        rows = _facts.list_facts(
            cur,
            owner_user_id=owner,
            subject_type=subject_type,
            subject_id=subject_id,
            fact_types=fact_types,
            limit=scan,
        )

    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (
            str(row.get("subject_type") or ""),
            str(row.get("subject_id") or ""),
            str(row.get("fact_type") or ""),
        )
        bucket = grouped.setdefault(key, [])
        if len(bucket) < MAX_GROUP:
            bucket.append(row)

    conflicts: list[dict] = []
    for (s_type, s_id, f_type), bucket in sorted(grouped.items()):
        if len(bucket) < 2:
            continue
        # Pairwise, then merged: three sources disagreeing about one renewal
        # date is one conflict with three competitors, not three pairwise
        # conflicts a user would have to reconcile against each other.
        clusters: list[dict] = []
        for index, left in enumerate(bucket):
            for right in bucket[index + 1:]:
                if not windows_overlap(left, right):
                    continue
                reason = materially_incompatible(left, right)
                if not reason:
                    continue
                placed = False
                for cluster in clusters:
                    if left["id"] in cluster["ids"] or right["id"] in cluster["ids"]:
                        cluster["ids"].update({left["id"], right["id"]})
                        cluster["rows"][left["id"]] = left
                        cluster["rows"][right["id"]] = right
                        placed = True
                        break
                if not placed:
                    clusters.append({
                        "ids": {left["id"], right["id"]},
                        "rows": {left["id"]: left, right["id"]: right},
                        "reason": reason,
                    })

        for cluster in clusters:
            members = [cluster["rows"][i] for i in sorted(cluster["ids"])]
            keys = [str(m.get("fact_key") or "") for m in members]
            conflicts.append({
                "conflict_id": conflict_id(owner, keys),
                "owner_user_id": owner,
                "subject_type": s_type,
                "subject_id": s_id,
                "fact_type": f_type,
                "reason": cluster["reason"],
                "competing_fact_ids": [int(m["id"]) for m in members],
                "competing": [_competing_entry(m) for m in members],
                "unresolved": True,
                "resolution": None,
            })

    if conflicts:
        settled = load_resolutions(
            cur, owner_user_id=owner,
            conflict_ids=[c["conflict_id"] for c in conflicts])
        if settled:
            for conflict in conflicts:
                record = settled.get(conflict["conflict_id"])
                if not record:
                    continue
                conflict["unresolved"] = False
                conflict["resolution"] = record
            if not include_resolved:
                conflicts = [c for c in conflicts if c["unresolved"]]

    if conflicts:
        # Fact types and counts only — never a value. A conflict about a policy
        # number that logged both policy numbers would put the secret in the
        # place it is least protected, which is the failure rule 8 names.
        LOGGER.info(
            "PRIVATE_CONFLICT_DETECTED owner=%s count=%s types=%s",
            owner, len(conflicts),
            ",".join(sorted({c["fact_type"] for c in conflicts}))[:200],
        )
        # Stage 38, one event per conflict. Note that `fact_type` is in the log
        # line above but not in the metric: the log is operator-facing and
        # short-lived, whereas a metric dimension is retained and cardinality
        # over a member-influenced string is how a "safe" label set turns into
        # a list of what individual members hold.
        for conflict in conflicts:
            _telemetry.emit(
                _telemetry.EVENT_CONFLICT_DETECTED,
                reason=conflict.get("reason"),
                domain=next((str(m.get("domain") or "")
                             for m in conflict.get("competing") or ()
                             if m.get("domain")), None),
                competing_count=len(conflict.get("competing_fact_ids") or ()),
                resolved=not conflict.get("unresolved", True))
    return conflicts


def mark_conflicts(
    cur,
    *,
    owner_user_id: int,
    conflicts: Sequence[dict],
    actor_user_id: int | None = None,
    purpose: str = "system_maintenance",
) -> int:
    """Stamp the competing rows as contested. Returns rows updated.

    Two things happen to each row, and the difference between them is the
    difference the ledger core was built to make.

    ``conflict_id`` is written directly here: it is a grouping key, not a claim
    about the fact, and it is what lets a resolution address the whole cluster
    later.

    ``verification_state`` becomes ``CONFLICTING``, and that goes through
    :func:`services.private_office.facts.flag_conflicting_fact` rather than
    through the UPDATE below — one canonical writer owns durable state changes,
    so the transition is checked against the state machine and lands in the
    audit trail and the fact history like every other one. A row that was
    already ``DISPUTED`` is left alone by that machine, because a person saying
    "this is wrong" is more specific than a scan saying "these two disagree",
    and a nightly sweep that overwrote the member's own judgement would be
    losing the more expensive signal to the cheaper one.

    What this still does **not** do is change ``provenance_type`` to
    ``CONFLICTING`` or move any row to ``SUPERSEDED``. Both would look tidier
    and both destroy the ability to explain the conflict: the answer the owner
    needs is "your insurer's record says one date and the policy document says
    another", and that sentence requires each row to still know where it came
    from. Which is exactly why the contested-ness lives on the verification axis
    instead — it is a state the fact was moved into, and it is now recorded as
    one.

    Conflicts already carrying a resolution are skipped rather than re-flagged.
    Detection filters them out by default, so reaching one here means a caller
    is holding a stale list, and re-contesting a settled disagreement would undo
    the member's decision without anything recording that it had been undone.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return 0
    _schema.require_private_schema(cur)
    updated = 0
    now_iso = _facts._now_iso()
    for conflict in conflicts or ():
        marker = str(conflict.get("conflict_id") or "")[:64]
        ids = [int(i) for i in conflict.get("competing_fact_ids") or () if str(i).strip()]
        if not marker or not ids:
            continue
        if conflict.get("unresolved") is False:
            continue
        placeholders = ",".join("?" * len(ids))
        cur.execute(
            f"UPDATE {_schema.FACTS_TABLE} SET conflict_id = ?, updated_at = ? "
            f"WHERE owner_user_id = ? AND id IN ({placeholders})",
            [marker, now_iso, owner, *ids],
        )
        updated += len(ids)
        for fact_id in ids:
            _facts.flag_conflicting_fact(
                cur, owner_user_id=owner, fact_id=fact_id,
                actor_user_id=actor_user_id, purpose=purpose)
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_CONFLICT_DETECTED,
            object_type=str(conflict.get("subject_type") or ""),
            object_id=str(conflict.get("subject_id") or ""),
            purpose=purpose, outcome=_audit.OUTCOME_OK, result_count=len(ids),
        )
    return updated


def _competitors(cur, *, owner_user_id: int, marker: str) -> list[dict]:
    """The live rows carrying this conflict marker.

    Read from the facts table rather than from a caller-supplied list, and that
    is a security decision rather than a convenience one. A resolution names a
    winning fact id, and trusting the caller's idea of which ids belong to the
    conflict would let a request nominate a fact from somewhere else — or from
    somebody else — and have the writer confirm it. Re-reading with the owner
    predicate in the SELECT means the winner can only ever be one of this
    member's own contested rows.
    """
    cur.execute(
        f"SELECT id, verification_state, lifecycle_state, domain, subject_type, "
        f"subject_id "
        f"FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND conflict_id = ? AND lifecycle_state = ? "
        f"ORDER BY id",
        (int(owner_user_id), marker, _model.LIFECYCLE_ACTIVE),
    )
    rows = []
    for row in cur.fetchall() or ():
        rows.append({
            "id": int(_row_value(row, "id", 0) or 0),
            "verification_state": str(_row_value(row, "verification_state", 1) or ""),
            "lifecycle_state": str(_row_value(row, "lifecycle_state", 2) or ""),
            "domain": str(_row_value(row, "domain", 3) or ""),
            "subject_type": str(_row_value(row, "subject_type", 4) or ""),
            "subject_id": str(_row_value(row, "subject_id", 5) or ""),
        })
    return rows


def _write_resolution(
    cur,
    *,
    owner_user_id: int,
    marker: str,
    resolution: str,
    winning_fact_id: int,
    competing_count: int,
    reason: str,
    loser_disposition: str,
    actor_type: str,
    actor_user_id: int,
) -> bool:
    """Persist the settlement. Returns True if it replaced an earlier one.

    Read-then-write rather than an upsert, because ``INSERT OR IGNORE`` means
    two different things on the two engines this package runs on and
    ``services.db`` rewrites it — so dedupe here is a decision the writer makes
    in code that can be read, not a side effect of a statement that behaves
    differently in production than in the test that covered it.

    Re-settling is allowed. A member may change their mind, or new evidence may
    arrive that does not change the competitor set. What is not allowed is
    losing the fact that it happened twice, which is why the audit row and the
    metric both mark it.
    """
    owner = int(owner_user_id)
    now_iso = _facts._now_iso()
    cur.execute(
        f"SELECT id FROM {_schema.FACT_CONFLICTS_TABLE} "
        f"WHERE owner_user_id = ? AND conflict_id = ?",
        (owner, marker),
    )
    existing = cur.fetchone()
    if existing is not None:
        cur.execute(
            f"UPDATE {_schema.FACT_CONFLICTS_TABLE} SET resolution = ?, "
            f"winning_fact_id = ?, competing_count = ?, reason = ?, "
            f"loser_disposition = ?, resolved_by_actor_type = ?, "
            f"resolved_by_actor_id = ?, resolved_at = ?, updated_at = ? "
            f"WHERE owner_user_id = ? AND conflict_id = ?",
            (resolution, winning_fact_id, competing_count, reason,
             loser_disposition, actor_type, int(actor_user_id), now_iso, now_iso,
             owner, marker),
        )
        return True
    cur.execute(
        f"INSERT INTO {_schema.FACT_CONFLICTS_TABLE} "
        f"(owner_user_id, conflict_id, resolution, winning_fact_id, "
        f"competing_count, reason, loser_disposition, resolved_by_actor_type, "
        f"resolved_by_actor_id, resolved_at, created_at, updated_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (owner, marker, resolution, winning_fact_id, competing_count, reason,
         loser_disposition, actor_type, int(actor_user_id), now_iso, now_iso,
         now_iso),
    )
    return False


def resolve_conflict(
    cur,
    *,
    owner_user_id: int,
    conflict_id: str,
    resolution: str = RESOLUTION_WINNER,
    winning_fact_id: int = 0,
    reason: str = "",
    loser_disposition: str = DISPOSITION_DISPUTE,
    actor_user_id: int | None = None,
    actor_type: str = _facts.ACTOR_OWNER,
    purpose: str = "user_request",
) -> dict:
    """Settle a detected conflict. The one place a disagreement ends.

    Returns ``{"status", "conflict_id", "resolution", "winning_fact_id",
    "competing_fact_ids", "losers_moved", "resettled", "reason"}`` where
    ``status`` is ``applied``, ``refused`` or ``not_found``.

    Three rules, and each of them is the reason this function exists rather than
    the caller doing it in three statements:

    **Only the owner may settle.** ``actor_type`` must be ``owner``. UNDX may
    surface a conflict, explain it, and recommend — it may not decide, and the
    refusal lives here rather than in a route because a second route would
    otherwise be a second chance to forget. This is Stage 13's "UNDX must not
    silently choose" expressed as a predicate instead of a comment.

    **The winner is confirmed, not verified.** The nomination goes through
    :func:`services.private_office.facts.resolve_fact`, which lands on
    ``USER_CONFIRMED``. There is no argument, no override and no flag that
    reaches ``VERIFIED``: a member choosing between two sources has exercised
    judgement, and judgement is not a system of record being read.

    **The losers move through the state machine.** Never a direct UPDATE. They
    are disputed by default — live, flagged, and still visible to a member who
    wants to see what they rejected — or superseded if the caller says so, which
    retires them and is deliberately not the default because it cannot be
    undone.

    ``RESOLUTION_DISMISSED`` takes no winner and moves every competitor out of
    ``CONFLICTING`` together. It is the answer for "these two were never
    describing the same thing", and without it the only way to clear a
    mis-detected conflict would be to declare one of the rows the truth.
    """
    owner = int(owner_user_id or 0)
    marker = str(conflict_id or "").strip()[:64]
    outcome = {
        "status": RESOLVE_REFUSED, "conflict_id": marker,
        "resolution": "", "winning_fact_id": 0, "competing_fact_ids": [],
        "losers_moved": 0, "resettled": False, "reason": "",
    }
    if owner <= 0 or not marker:
        outcome["reason"] = "invalid_request"
        return outcome
    if resolution not in RESOLUTIONS:
        outcome["reason"] = "unknown_resolution"
        return outcome
    if loser_disposition not in DISPOSITIONS:
        outcome["reason"] = "unknown_disposition"
        return outcome

    actor_class = str(actor_type or "").strip().lower()
    if actor_class != _facts.ACTOR_OWNER:
        # Audited rather than silently dropped. An attempt by a machine to
        # settle a member's disagreement is exactly the event the trail exists
        # for, and it is worth being able to count even when it is a bug rather
        # than an attack.
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_CONFLICT_RESOLVED, object_type="", object_id="",
            purpose=purpose, outcome=_audit.OUTCOME_DENIED,
        )
        outcome["reason"] = "actor_not_owner"
        return outcome

    _schema.require_private_schema(cur)
    competing = _competitors(cur, owner_user_id=owner, marker=marker)
    if len(competing) < 2:
        # Fewer than two live rows carry the marker, so there is nothing left to
        # disagree. Reported as not-found rather than as success: a caller whose
        # conflict evaporated between reading and deciding should hear that the
        # question changed, not that their answer was recorded.
        outcome["status"] = RESOLVE_NOT_FOUND
        outcome["reason"] = "no_such_conflict"
        return outcome

    ids = [row["id"] for row in competing]
    outcome["competing_fact_ids"] = list(ids)
    winner = int(winning_fact_id or 0)
    if resolution == RESOLUTION_WINNER:
        if winner not in ids:
            outcome["reason"] = "winner_not_in_conflict"
            return outcome
    else:
        winner = 0
        loser_disposition = DISPOSITION_NONE

    moved = 0
    if resolution == RESOLUTION_DISMISSED:
        # Nobody lost, so every competitor is released from CONFLICTING by the
        # same operation the winner would have used. The member has looked at
        # all of them and said they can all stand.
        for fact_id in ids:
            applied = _facts.resolve_fact(
                cur, owner_user_id=owner, fact_id=fact_id,
                actor_user_id=actor_user_id, actor_type=_facts.ACTOR_OWNER,
                reason_code="conflict_dismissed", purpose=purpose)
            if applied.get("status") == "applied":
                moved += 1
    else:
        _facts.resolve_fact(
            cur, owner_user_id=owner, fact_id=winner,
            actor_user_id=actor_user_id, actor_type=_facts.ACTOR_OWNER,
            reason_code="conflict_resolution", purpose=purpose)
        for fact_id in ids:
            if fact_id == winner:
                continue
            if loser_disposition == DISPOSITION_SUPERSEDE:
                applied = _facts.retire_fact(
                    cur, owner_user_id=owner, fact_id=fact_id,
                    actor_user_id=actor_user_id, actor_type=_facts.ACTOR_OWNER,
                    reason_code="conflict_resolution", purpose=purpose)
            elif loser_disposition == DISPOSITION_DISPUTE:
                applied = _facts.dispute_fact(
                    cur, owner_user_id=owner, fact_id=fact_id,
                    actor_user_id=actor_user_id, actor_type=_facts.ACTOR_OWNER,
                    reason_code="conflict_resolution", purpose=purpose)
            else:
                continue
            if applied.get("status") == "applied":
                moved += 1

    reason_code = str(reason or "")[:64]
    resettled = _write_resolution(
        cur, owner_user_id=owner, marker=marker, resolution=resolution,
        winning_fact_id=winner, competing_count=len(ids), reason=reason_code,
        loser_disposition=loser_disposition, actor_type=_facts.ACTOR_OWNER,
        actor_user_id=int(actor_user_id or owner),
    )

    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_CONFLICT_RESOLVED,
        object_type=competing[0]["subject_type"],
        object_id=competing[0]["subject_id"],
        purpose=purpose, outcome=_audit.OUTCOME_OK, result_count=len(ids),
    )
    # Ids and counts only, per the module docstring. Which fact won is a fact
    # about the member's holdings; that it was settled at all is process health.
    LOGGER.info(
        "PRIVATE_CONFLICT_RESOLVED owner=%s resolution=%s competing=%s moved=%s",
        owner, resolution, len(ids), moved,
    )
    _telemetry.emit(
        _telemetry.EVENT_CONFLICT_RESOLVED,
        resolution=resolution, reason=reason_code or None,
        loser_disposition=loser_disposition, actor_type=_facts.ACTOR_OWNER,
        domain=next((row["domain"] for row in competing if row["domain"]), None),
        competing_count=len(ids), losers_moved=moved, resettled=resettled,
    )

    outcome.update({
        "status": RESOLVE_APPLIED,
        "resolution": resolution,
        "winning_fact_id": winner,
        "losers_moved": moved,
        "resettled": resettled,
        "reason": reason_code,
    })
    return outcome
