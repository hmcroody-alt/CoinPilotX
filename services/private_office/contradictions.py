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

So conflicts are recorded and left ``unresolved``. The rows keep their original
provenance — deliberately, rather than being restamped ``CONFLICTING`` — because
"your insurer's record says March, the policy document says April" is only
sayable while both rows still remember where they came from. Overwriting
provenance with the fact that there is a conflict destroys the material needed
to explain it.

Values, and where they may go
-----------------------------
:func:`detect_conflicts` returns typed values, because its caller is answering
the owner's own question about the owner's own data. Nothing in this module logs
them: every log line and every audit row carries ids, counts and fact types
only, per Stage 18 and rule 8.
"""

from __future__ import annotations

import hashlib
import json
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
    "unresolved"}``, plus ``"resolution"`` when a decision exists.

    Resolution is an act by the owner — confirming which source is right — and
    this module still never chooses. What it now does is *remember* that the
    owner chose: a conflict whose stored outcome is in
    :data:`~services.private_office.model.RESOLUTION_CLOSES` is filtered out by
    default, so a settled disagreement stops re-surfacing forever. Pass
    ``include_resolved=True`` to get the full list with each entry annotated.

    Which outcomes this actually filters is narrower than it looks.
    ``list_facts`` reads ``lifecycle_state = ACTIVE``, so the losers of a
    ``KEPT`` or ``ALL_REJECTED`` decision have already dropped out of the scan
    and the conflict cannot re-form. The outcome that genuinely needs
    suppressing here is ``SEPARATED`` — where the member said "these describe
    different things" and every competitor stays active and keeps matching. For
    the other two this is defence in depth: if an archive had partially failed,
    the conflict resurfacing is the correct behaviour and the stored resolution
    would be the wrong thing to trust.

    ``DEFERRED`` is deliberately not in ``RESOLUTION_CLOSES``. A member who
    looked and could not decide has not settled anything, and hiding the
    conflict because they *engaged* with it is how a backlog disappears from
    the only screen that would have shown it.

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
            })

    if conflicts:
        resolutions = conflict_resolutions(
            cur, owner_user_id=owner,
            conflict_ids=[c["conflict_id"] for c in conflicts])
        kept: list[dict] = []
        for conflict in conflicts:
            decision = resolutions.get(conflict["conflict_id"])
            if decision is not None and (
                    decision["competing_fact_ids"] != sorted(conflict["competing_fact_ids"])):
                # The id matched but the set did not. The id is a hash of the
                # competing fact *keys* while the stored set is fact *ids*, so
                # this is reachable — a fact re-recorded under the same key
                # produces the same id with a different row. A decision made
                # about rows that are no longer the ones in front of the member
                # is not a decision about this conflict, and honouring it would
                # suppress a disagreement nobody ever saw.
                decision = None
            if decision is not None:
                conflict["unresolved"] = not decision["closed"]
                conflict["resolution"] = decision
            if include_resolved or conflict["unresolved"]:
                kept.append(conflict)
        conflicts = kept

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
    """Stamp ``conflict_id`` onto the competing rows. Returns rows updated.

    What this deliberately does **not** do is change ``provenance_type`` to
    ``CONFLICTING`` or move any row to ``SUPERSEDED``. Both would look tidier
    and both destroy the ability to explain the conflict: the answer the owner
    needs is "your insurer's record says one date and the policy document says
    another", and that sentence requires each row to still know where it came
    from. Marking is additive, and a resolved conflict is cleared by the owner
    acting, not by this function choosing.
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
        placeholders = ",".join("?" * len(ids))
        cur.execute(
            f"UPDATE {_schema.FACTS_TABLE} SET conflict_id = ?, updated_at = ? "
            f"WHERE owner_user_id = ? AND id IN ({placeholders})",
            [marker, now_iso, owner, *ids],
        )
        updated += len(ids)
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_CONFLICT_DETECTED,
            object_type=str(conflict.get("subject_type") or ""),
            object_id=str(conflict.get("subject_id") or ""),
            purpose=purpose, outcome=_audit.OUTCOME_OK, result_count=len(ids),
        )
    return updated


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
#: Resolutions read back per call. A conflict resolved and re-resolved a
#: handful of times is normal; thousands of rows for one ``conflict_id`` is a
#: caller in a loop, and the read that answers "what is the current decision"
#: should not degrade because of it.
MAX_RESOLUTION_SCAN = 200


def _pack_ids(ids: Sequence[int]) -> str:
    """Storage form for a competing set: a JSON array of ascending ints."""
    return json.dumps(sorted({int(i) for i in ids}))


def _unpack_ids(stored: object) -> list[int]:
    """Inverse of :func:`_pack_ids`; malformed storage reads as empty.

    A resolution row whose competing set will not parse is a row that can no
    longer say what it decided about. Returning ``[]`` makes it close nothing,
    so the conflict resurfaces and the member is asked again — which is the
    safe direction. Raising here would take down the conflict list entirely.
    """
    try:
        loaded = json.loads(str(stored or "").strip() or "[]")
    except ValueError:
        return []
    if not isinstance(loaded, list):
        return []
    out: list[int] = []
    for item in loaded:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            out.append(value)
    return sorted(set(out))


def _row_to_resolution(row) -> dict:
    data = dict(row)
    return {
        "id": int(data.get("id") or 0),
        "conflict_id": str(data.get("conflict_id") or ""),
        "subject_type": str(data.get("subject_type") or ""),
        "subject_id": str(data.get("subject_id") or ""),
        "fact_type": str(data.get("fact_type") or ""),
        "reason": str(data.get("reason") or ""),
        "competing_fact_ids": _unpack_ids(data.get("competing_fact_ids")),
        "outcome": str(data.get("outcome") or ""),
        "kept_fact_id": int(data.get("kept_fact_id") or 0),
        "note_key": str(data.get("note_key") or ""),
        "resolved_by": int(data.get("resolved_by") or 0),
        "resolved_at": str(data.get("resolved_at") or ""),
        "closed": str(data.get("outcome") or "") in _model.RESOLUTION_CLOSES,
    }


def resolve_conflict(
    cur,
    *,
    owner_user_id: int,
    conflict_id: str,
    outcome: str,
    competing_fact_ids: Sequence[int],
    kept_fact_id: int | None = None,
    subject_type: str = "",
    subject_id: object = "",
    fact_type: str = "",
    reason: str = "",
    actor_user_id: int | None = None,
    purpose: str = "user_request",
    note_key: object = None,
) -> dict:
    """Record the owner's decision about one conflict, and execute it.

    This is the other half of the promise the detector makes. Detection says
    "two of your sources disagree and I will not choose between them"; without
    a resolution path that sentence becomes "…and you can never make this go
    away", so the conflict list grows monotonically and stops being read for
    exactly the reason the module docstring warns about.

    The decision is the member's and this function only carries it out:

    * ``KEPT`` — one competitor is right. The others are **archived**, not
      deleted and not superseded: they keep their values, provenance and
      evidence, and stop being asserted. ``kept_fact_id`` is required and must
      name a member of the competing set.
    * ``ALL_REJECTED`` — none of them is right. All are archived. The subject
      is left with no claim of this type, which is a truthful state and a
      better one than an arbitrary survivor.
    * ``SEPARATED`` — the detector was wrong to pair them; they describe
      different things. **No lifecycle change**, because nothing here is false.
    * ``DEFERRED`` — the member looked and could not decide. Recorded so the
      looking is not lost, but it closes nothing and the conflict keeps
      surfacing.

    Why ``competing_fact_ids`` is required rather than re-derived
    ------------------------------------------------------------
    :func:`conflict_id` hashes the *fact keys of the competing set*, so a
    conflict that acquires a third competitor gets a different id and arrives
    as a new, unresolved conflict. That is the property that stops a stale
    decision from silently suppressing a genuinely new disagreement, and it
    only holds while a resolution is bound to the precise set it was made
    about. Storing the set is what lets a reader verify that binding instead of
    trusting it.

    Append-only. A member who changes their mind resolves again and the newer
    row wins; the earlier decision stays readable, because "I first said keep
    the insurer's date, then changed to the document's" is exactly the history
    a truth ledger exists to preserve.
    """
    owner = int(owner_user_id or 0)
    marker = str(conflict_id or "").strip()[:64]
    if owner <= 0 or not marker:
        raise _facts.PrivateFactRejected(
            "owner_user_id and conflict_id are required")

    decision = _model.normalize_resolution(outcome)
    if not decision:
        raise _facts.PrivateFactRejected(f"unknown outcome: {outcome!r}")

    ids = _unpack_ids(list(competing_fact_ids or ()))
    if len(ids) < 2:
        # A "conflict" with fewer than two sides is not one, and accepting it
        # would let a caller close an arbitrary conflict_id by naming a single
        # fact — including one that was never part of it.
        raise _facts.PrivateFactRejected(
            "a conflict resolution needs at least two competing facts")

    note = _model.NOTE_CONFLICT_RESOLUTION
    if note_key is not None and str(note_key).strip():
        note = _model.normalize_note_key(note_key) or ""
        if not note:
            raise _facts.PrivateFactRejected(f"unknown note_key: {note_key!r}")

    _schema.require_private_schema(cur)
    actor = int(actor_user_id or owner)

    # Every competitor is loaded under the owner predicate before anything is
    # written. A resolution naming another member's fact id must fail as
    # "not found" and must not archive anything, so the ownership check cannot
    # come after the first UPDATE.
    for fact_id in ids:
        _facts._load_fact(cur, owner, fact_id, "id")

    keep = int(kept_fact_id or 0)
    if decision == _model.RESOLUTION_KEPT:
        if keep not in ids:
            raise _facts.PrivateFactRejected(
                "kept_fact_id must name one of the competing facts")
    elif keep:
        # Silently ignoring it would let "reject everything, but keep this one"
        # store a contradiction of itself.
        raise _facts.PrivateFactRejected(
            f"kept_fact_id is meaningless for outcome {decision}")

    if decision == _model.RESOLUTION_KEPT:
        to_archive = [i for i in ids if i != keep]
    elif decision == _model.RESOLUTION_ALL_REJECTED:
        to_archive = list(ids)
    else:
        to_archive = []

    archived = 0
    for fact_id in to_archive:
        result = _facts.archive_fact(
            cur, owner_user_id=owner, fact_id=fact_id, actor_user_id=actor,
            purpose=purpose, note_key=note)
        if result.get("status") != _facts.STATUS_EXISTING:
            archived += 1

    # History on every competitor, including the survivor and including the
    # rows a SEPARATED or DEFERRED outcome leaves untouched. "Nothing changed
    # here" is a decision too, and a fact whose timeline omits the conflict it
    # was part of reads as though it was never questioned.
    for fact_id in ids:
        _facts._history(
            cur, owner_user_id=owner, fact_id=fact_id,
            change_type=_model.CHANGE_CONFLICT_RESOLVED, actor_user_id=actor,
            from_state=marker, to_state=decision,
            related_fact_id=keep if keep != fact_id else 0, note_key=note)

    now_iso = _facts._now_iso()
    cur.execute(
        f"""INSERT INTO {_schema.FACT_CONFLICTS_TABLE}
        (owner_user_id, conflict_id, subject_type, subject_id, fact_type,
         reason, competing_fact_ids, outcome, kept_fact_id, note_key,
         resolved_by, resolved_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, marker, str(subject_type or "")[:64], str(subject_id or "")[:128],
         str(fact_type or "")[:64], str(reason or "")[:64], _pack_ids(ids),
         decision, keep, note, actor, now_iso, now_iso),
    )

    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_CONFLICT_RESOLVED,
        object_type=str(subject_type or ""), object_id=str(subject_id or ""),
        purpose=purpose, outcome=_audit.OUTCOME_OK, result_count=len(ids),
    )
    LOGGER.info(
        "PRIVATE_CONFLICT_RESOLVED owner=%s outcome=%s competing=%s archived=%s",
        owner, decision, len(ids), archived)
    _telemetry.emit(
        _telemetry.EVENT_CONFLICT_RESOLVED,
        outcome=decision, reason=str(reason or "") or None,
        competing_count=len(ids),
        closed=decision in _model.RESOLUTION_CLOSES)

    return {
        "conflict_id": marker,
        "outcome": decision,
        "kept_fact_id": keep,
        "competing_fact_ids": ids,
        "archived_count": archived,
        "closed": decision in _model.RESOLUTION_CLOSES,
        "resolved_at": now_iso,
    }


def conflict_resolutions(
    cur,
    *,
    owner_user_id: int,
    conflict_ids: Sequence[str] | None = None,
) -> dict[str, dict]:
    """Current decision per ``conflict_id``, newest row wins.

    One query for many ids, for the Stage 37 reason spelled out in
    :func:`detect_conflicts`: the natural caller holds a page of conflicts and
    needs the decision for each, and doing that one id at a time is the N+1
    that stage forbids.

    Passing ``conflict_ids=None`` reads every resolution the owner has, which
    is what detection needs — it does not know which ids it is about to
    produce until after it has grouped the rows.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return {}
    _schema.require_private_schema(cur)

    sql = f"SELECT * FROM {_schema.FACT_CONFLICTS_TABLE} WHERE owner_user_id = ?"
    params: list[Any] = [owner]
    if conflict_ids is not None:
        wanted = sorted({str(c).strip()[:64] for c in conflict_ids if str(c or "").strip()})
        if not wanted:
            return {}
        sql += f" AND conflict_id IN ({','.join('?' * len(wanted))})"
        params.extend(wanted)
    # Ascending, so the newest row for each id overwrites the older ones as the
    # loop runs and the last write wins without a per-id subquery.
    sql += " ORDER BY id ASC LIMIT ?"
    params.append(MAX_RESOLUTION_SCAN * max(1, len(conflict_ids or ("",))))

    latest: dict[str, dict] = {}
    try:
        cur.execute(sql, params)
        rows = cur.fetchall() or []
    except Exception as exc:
        # A deployment without the table yet must not break the conflict list.
        # Reading as "nothing resolved" surfaces conflicts that were in fact
        # settled, which is noisy; the opposite would hide live disagreements.
        LOGGER.warning("PRIVATE_CONFLICT_RESOLUTION_READ_FAILED error=%s", exc)
        return {}
    for row in rows:
        entry = _row_to_resolution(row)
        if entry["conflict_id"]:
            latest[entry["conflict_id"]] = entry
    return latest
