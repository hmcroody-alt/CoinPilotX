"""Idempotent migration and conservative legacy backfill for the fact store.

Two jobs, and they are deliberately different in kind.

**The migration** is the schema: tables, columns, indexes. That already exists
and lives in :func:`schema.ensure_private_schema`, which creates with
``IF NOT EXISTS`` and adds later columns as guarded ``ALTER``s. This module does
not reimplement it; :func:`migrate` calls it and then does the part it cannot
do. A column ``DEFAULT`` reaches every existing row, so the six columns added
after the first release — ``verification_state`` and the supersession pointers —
are already correct on legacy rows without anybody writing an ``UPDATE``. That
was a deliberate choice made in ``TABLE_ADDED_COLUMNS`` and it is why the
backfill below is as small as it is.

**The backfill** is the data, and only one specific kind of damage to it: a row
whose *label* columns hold something this package cannot read. Not the value —
never the value.

What this can honestly repair
-----------------------------
Three columns, and the test for including one was whether the repair states
something *true* rather than something *convenient*:

``provenance_type``
    An unreadable origin becomes ``LEGACY_UNKNOWN``. This is the case the
    foundation map named and it is the easiest to get wrong. The tempting
    repairs are ``USER_ASSERTED`` — the owner probably typed it — and
    ``PROVIDER_ASSERTED`` — it probably came from a feed. Both are guesses
    dressed as data, and both are unfalsifiable afterwards: once a row says
    ``USER_ASSERTED`` nothing distinguishes it from a row where the owner
    really did type the value. ``LEGACY_UNKNOWN`` is the only label that
    remains true no matter what the origin actually was, and it is the only one
    that leaves the question open for somebody to answer properly later.

``sensitivity``
    An unreadable sensitivity becomes ``RESTRICTED``, the *highest* rank and so
    the narrowest release. See the note on widening below.

``domain``
    An unreadable domain becomes ``GENERAL``. Domain is a filter, not a gate —
    it decides which screen a fact appears under, not who may see it — so this
    repair changes no access. ``GENERAL`` is the bucket for "we cannot say
    which", which is exactly the situation.

What this deliberately leaves alone
-----------------------------------
Reported in ``unrepairable``, never written:

``lifecycle_state``
    An unreadable lifecycle cannot be repaired without guessing at intent.
    ``ACTIVE`` would resurface a fact the member may have archived on purpose;
    ``ARCHIVED`` would hide one they still rely on. Inferring it from
    ``superseded_by_id`` only covers rows that happen to sit in a chain and
    silently guesses for every other row. There is no honest default, so this
    module reports the count and writes nothing.

``value_type``, ``typed_value``, ``value_number``, ``fact_key``
    Repairing an unreadable ``value_type`` means re-parsing the member's value
    under a type nobody chose, which is rewriting their data. ``fact_key`` is
    worse: it is the ``UNIQUE(owner_user_id, fact_key)`` identity, and
    recomputing it could collapse two distinct rows onto one key — a migration
    that deletes a fact by way of a constraint violation. The formula has not
    changed since the store was created, verified against the pre-mission
    revision, so there is nothing to recompute and this module never tries.

``observed_at``, ``valid_from``
    A missing timestamp cannot be invented. Using ``created_at``, or the wall
    clock, would place the observation at a moment nobody observed anything,
    and the contradiction engine decides overlaps by exactly these fields.

The one widening, stated plainly
--------------------------------
A row whose ``sensitivity`` is unreadable is currently invisible to *everyone*,
including the owner: ``list_facts`` builds its ``sensitivity IN (...)`` clause
from recognised names only, so an unrecognised one matches no ceiling at any
tier. Repairing it to ``RESTRICTED`` therefore moves the row from "nobody can
read this" to "only a caller holding the top ceiling can read this" — which in
practice means the owner, and no subsystem.

That is a widening and it is the only one in this module. It is done because
the alternative is a fact the member gave us that they can never see again,
which is data loss wearing the costume of caution. ``RESTRICTED`` rather than
``DEFAULT_SENSITIVITY`` precisely because the default is ``CONFIDENTIAL``, and
that would release the row to every subsystem carrying a confidential ceiling —
a genuine disclosure created by a migration, which is the thing that must never
happen quietly.

Bounded, batched and convergent
-------------------------------
Convergent means a second run changes nothing, and it is a stronger property
than idempotent: it has to hold when the first run was *interrupted*, because a
migration over a large table will be. Each batch commits nothing itself and
repairs only rows the scan found damaged, so a run that dies halfway leaves a
store that is partly repaired and entirely consistent, and the next run picks
up the remainder. There is no cursor to lose and no state to resume from.

Refuses by default
------------------
:func:`run_backfill` does not write unless the caller passes ``apply=True``. A
migration whose obvious call mutates the store is one that gets run by accident
from a REPL, and the dry run is the thing an operator actually wants first: the
same scan, the same counts, no ``UPDATE``.

Reports counts, never content
-----------------------------
The return carries how many rows of each kind were found and repaired. It never
carries a fact id, a value, or a subject. An operator running a migration has
no business reading anybody's private facts, and a log line from this module
ends up somewhere far less protected than the table it describes.
"""

from __future__ import annotations

import logging
from typing import Any

from services.private_office import facts as _facts
from services.private_office import model as _model
from services.private_office import schema as _schema

LOGGER = logging.getLogger(__name__)

#: Rows examined in one batch. Matched to the other bounded scans in the
#: package rather than tuned upward: a migration is allowed to be slow, and a
#: single statement that loads the whole table is how a migration takes a lock
#: long enough to be noticed by everybody else.
MAX_BACKFILL_BATCH = 500

#: Batches one call may run before it stops and reports the remainder. A
#: migration that runs until it finishes holds a connection for an unbounded
#: time; one that stops and says how much is left can be called again by
#: whatever scheduled it, and the second call is cheap once the store is clean.
MAX_BACKFILL_BATCHES = 20

#: What a repair pass can put right, and what it found and would not touch.
#: Named as keys rather than returned positionally because the two lists grow at
#: different rates and a caller reading `counts[2]` is a caller who will read
#: the wrong one the day something is inserted above it.
REPAIR_PROVENANCE = "provenance_unreadable"
REPAIR_SENSITIVITY = "sensitivity_unreadable"
REPAIR_DOMAIN = "domain_unreadable"

REPAIRS: tuple[str, ...] = (
    REPAIR_PROVENANCE,
    REPAIR_SENSITIVITY,
    REPAIR_DOMAIN,
)

UNREPAIRABLE_LIFECYCLE = "lifecycle_unreadable"
UNREPAIRABLE_VALUE_TYPE = "value_type_unreadable"
UNREPAIRABLE_OBSERVED_AT = "observed_at_missing"
UNREPAIRABLE_VALID_FROM = "valid_from_missing"

UNREPAIRABLE: tuple[str, ...] = (
    UNREPAIRABLE_LIFECYCLE,
    UNREPAIRABLE_VALUE_TYPE,
    UNREPAIRABLE_OBSERVED_AT,
    UNREPAIRABLE_VALID_FROM,
)

#: The replacement for each repairable column. Kept beside the vocabulary so
#: the choice is readable in one place rather than spelled inline at the point
#: of the ``UPDATE``, where the reasoning above would be invisible.
REPLACEMENT: dict[str, tuple[str, str]] = {
    REPAIR_PROVENANCE: ("provenance_type", _model.PROVENANCE_LEGACY_UNKNOWN),
    REPAIR_SENSITIVITY: ("sensitivity", _model.SENSITIVITY_RESTRICTED),
    REPAIR_DOMAIN: ("domain", _model.DOMAIN_GENERAL),
}


def _empty_report(*, applied: bool = False, after_id: int = 0) -> dict:
    return {
        "scanned": 0,
        "repaired": {name: 0 for name in REPAIRS},
        "unrepairable": {name: 0 for name in UNREPAIRABLE},
        "applied": applied,
        "complete": False,
        "remaining": 0,
        # Where the scan stopped. On a failure this is the id it was given, not
        # zero: a caller that resumes from a failed run must not be told to
        # start over, and a caller that resumes from a run which never reached
        # the store must not be told it made progress. Both are the same value
        # here because neither read a row.
        "next_after_id": int(after_id),
    }


def _damaged(row: dict) -> tuple[list[str], list[str]]:
    """Which labels on this row are unreadable, split by what can be done.

    Reads through the same normalizers every other module in the package uses.
    That is the point rather than an implementation detail: "unreadable" has to
    mean *unreadable by the code that will read it*, not unreadable by a second
    opinion written here. A private list of valid names in this module would
    drift from ``model``'s the first time a vocabulary grew, and the drift would
    show up as a migration rewriting rows that were never damaged.
    """
    repairable: list[str] = []
    if not _model.normalize_provenance(row.get("provenance_type")):
        repairable.append(REPAIR_PROVENANCE)
    if not _model.normalize_sensitivity(row.get("sensitivity")):
        repairable.append(REPAIR_SENSITIVITY)
    if not _model.normalize_domain(row.get("domain")):
        repairable.append(REPAIR_DOMAIN)

    blocked: list[str] = []
    if not _model.normalize_lifecycle(row.get("lifecycle_state")):
        blocked.append(UNREPAIRABLE_LIFECYCLE)
    if not _model.normalize_value_type(row.get("value_type")):
        blocked.append(UNREPAIRABLE_VALUE_TYPE)
    if not str(row.get("observed_at") or "").strip():
        blocked.append(UNREPAIRABLE_OBSERVED_AT)
    if not str(row.get("valid_from") or "").strip():
        blocked.append(UNREPAIRABLE_VALID_FROM)
    return repairable, blocked


def _scan_batch(cur, *, after_id: int, batch: int) -> list[dict]:
    """One batch of rows by ascending id, across all owners.

    Crossing owners is correct here and nowhere else in this package. Every
    member-facing read takes a required ``owner_user_id`` and puts it in the
    ``WHERE`` clause; a migration has no member on whose behalf it is acting and
    inventing one would mean either running it as somebody or enumerating owners
    from a table this module would then also have to be trusted with. What keeps
    the boundary intact is not the query — it is that nothing derived from these
    rows leaves the module. Only counts are returned.

    Ordering by id, with the previous batch's last id as the cursor, is what
    makes an interrupted run resumable without storing a cursor anywhere: ids
    are immutable here, and no repair below changes one.
    """
    cur.execute(
        f"""SELECT id, owner_user_id, provenance_type, sensitivity, domain,
                   lifecycle_state, value_type, observed_at, valid_from
            FROM {_schema.FACTS_TABLE}
            WHERE id > ?
            ORDER BY id ASC LIMIT ?""",
        (int(after_id), int(batch)),
    )
    rows = cur.fetchall()
    columns = ("id", "owner_user_id", "provenance_type", "sensitivity", "domain",
               "lifecycle_state", "value_type", "observed_at", "valid_from")
    out: list[dict] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
            continue
        try:
            out.append(dict(row))
        except (TypeError, ValueError):
            out.append({name: row[index] for index, name in enumerate(columns)})
    return out


def _repair(cur, *, row: dict, kinds: list[str], actor_user_id: int) -> bool:
    """Rewrite the unreadable labels on one row. Returns whether it landed.

    One statement per row rather than one per column, so a row is never left
    half-repaired by a failure between two updates.

    The ``WHERE`` clause repeats the owner even though ``id`` is the primary key
    and already unique. It is not redundant defensively — it is redundant on
    purpose, because this is the one module in the package that reads across
    owners, and a statement here that could touch a row belonging to somebody
    other than the row it read is the exact mistake the owner predicate exists
    to make impossible.
    """
    assignments = []
    params: list[Any] = []
    for kind in kinds:
        column, replacement = REPLACEMENT[kind]
        assignments.append(f"{column} = ?")
        params.append(replacement)
    if not assignments:
        return False
    assignments.append("updated_at = ?")
    params.append(_facts._now_iso())

    owner = int(row.get("owner_user_id") or 0)
    fact_id = int(row.get("id") or 0)
    try:
        cur.execute(
            f"UPDATE {_schema.FACTS_TABLE} SET {', '.join(assignments)} "
            f"WHERE id = ? AND owner_user_id = ?",
            [*params, fact_id, owner],
        )
    except Exception as exc:
        # Logged with the column names and never the values. The names are
        # vocabulary; the values are the member's.
        LOGGER.warning(
            "PRIVATE_BACKFILL_REPAIR_FAILED columns=%s error=%s",
            ",".join(kind for kind in kinds), exc)
        return False

    # History is best-effort in `facts` and it is best-effort here for the same
    # reason: losing the note that a repair happened is bad, and losing the
    # repair in order to protect the note is worse.
    _facts._history(
        cur, owner_user_id=owner, fact_id=fact_id,
        change_type=_model.CHANGE_BACKFILLED,
        actor_user_id=int(actor_user_id or 0),
        to_state=",".join(REPLACEMENT[kind][0] for kind in kinds)[:64],
        note_key=_model.NOTE_LEGACY_BACKFILL)
    return True


def run_backfill(
    cur,
    *,
    apply: bool = False,
    batch: int = MAX_BACKFILL_BATCH,
    max_batches: int = MAX_BACKFILL_BATCHES,
    actor_user_id: int = 0,
    after_id: int = 0,
) -> dict:
    """Scan the fact store for unreadable labels and, with ``apply``, repair them.

    Returns ``{"scanned", "repaired", "unrepairable", "applied", "complete",
    "remaining", "next_after_id"}``. ``repaired`` counts rows per repair kind —
    with ``apply=False`` it counts what *would* be repaired, which is the number
    an operator wants before deciding. ``complete`` says the scan reached the end
    of the table; ``remaining`` is how many damaged rows were seen after the
    batch budget ran out and so were neither counted as repaired nor lost.

    ``after_id`` and ``next_after_id`` are what make an interrupted run converge
    rather than merely restart. Without them a caller whose budget is smaller
    than the table re-reads the same prefix on every call: the first run repairs
    the damage in that prefix, and every run afterwards scans the same now-clean
    rows, reports ``complete`` False, and never reaches the tail. It would look
    like a working migration — rows get repaired, no error is raised — while
    being unable to finish, and the rows it can never reach are exactly the ones
    written most recently. Feeding ``next_after_id`` back in is the whole of the
    resumption protocol; there is no stored cursor and nothing to lose, because
    ids are immutable and no repair below changes one.

    Never raises. A migration that throws halfway through a boot sequence takes
    the process down over rows that were already broken before it looked.
    """
    start = max(0, int(after_id or 0))
    report = _empty_report(applied=bool(apply), after_id=start)
    size = max(1, min(int(batch or MAX_BACKFILL_BATCH), MAX_BACKFILL_BATCH))
    rounds = max(1, min(int(max_batches or MAX_BACKFILL_BATCHES), MAX_BACKFILL_BATCHES))

    try:
        _schema.require_private_schema(cur)
    except Exception as exc:
        # `complete` stays False, which is the whole contract of this branch: a
        # backfill that could not reach the store has not established that the
        # store is clean, and a caller that treats a zero count as "nothing to
        # do" must not be able to get one from a failure.
        LOGGER.warning("PRIVATE_BACKFILL_SCHEMA_UNAVAILABLE error=%s", exc)
        return report

    after = start
    for _round in range(rounds):
        try:
            rows = _scan_batch(cur, after_id=after, batch=size)
        except Exception as exc:
            # The cursor is published even on a failed scan. The rows already
            # read in earlier rounds were really read, and a caller that resumed
            # from zero would re-do work it has done — harmless, since a repair
            # is idempotent, but it would also re-file a history entry for every
            # row it repaired the first time.
            LOGGER.warning("PRIVATE_BACKFILL_SCAN_FAILED error=%s", exc)
            report["next_after_id"] = int(after)
            return report
        if not rows:
            report["complete"] = True
            break

        for row in rows:
            report["scanned"] += 1
            after = max(after, int(row.get("id") or 0))
            repairable, blocked = _damaged(row)
            for kind in blocked:
                report["unrepairable"][kind] += 1
            if not repairable:
                continue
            if not apply:
                for kind in repairable:
                    report["repaired"][kind] += 1
                continue
            if _repair(cur, row=row, kinds=repairable,
                       actor_user_id=actor_user_id):
                for kind in repairable:
                    report["repaired"][kind] += 1
            else:
                report["remaining"] += 1

        if len(rows) < size:
            report["complete"] = True
            break

    report["next_after_id"] = int(after)
    if not report["complete"]:
        # The budget ran out with rows still unread. Saying so is the point:
        # `complete` False plus a caller that reruns is a migration that
        # finishes, while `complete` True over a partial scan is a store
        # declared clean on the strength of the part that was looked at.
        LOGGER.info(
            "PRIVATE_BACKFILL_INCOMPLETE scanned=%s batches=%s",
            report["scanned"], rounds)

    LOGGER.info(
        "PRIVATE_BACKFILL_DONE applied=%s scanned=%s repaired=%s unrepairable=%s "
        "complete=%s",
        report["applied"], report["scanned"],
        ",".join(f"{k}={v}" for k, v in sorted(report["repaired"].items()) if v),
        ",".join(f"{k}={v}" for k, v in sorted(report["unrepairable"].items()) if v),
        report["complete"])
    return report


def migrate(cur, *, apply: bool = False, actor_user_id: int = 0) -> dict:
    """Bring the store to the current shape: schema first, then labels.

    Returns ``{"schema", "backfill"}``. The order is not negotiable and not
    merely conventional: the backfill reads ``lifecycle_state`` and
    ``verification_state``, and on a store that predates them the scan would
    raise on a missing column rather than report a clean table. Running ensure
    first also means the ``ALTER`` defaults have already landed by the time
    anything counts damage, so the six added columns never appear as findings.

    The backfill is skipped entirely when the schema did not come back ready.
    Reporting "0 rows repaired" off a store this process could not read would be
    the same lie ``complete`` exists to prevent, one level up.
    """
    schema_result = _schema.ensure_private_schema(cur, force=True)
    if schema_result.get("status") != _schema.STATUS_READY:
        LOGGER.warning(
            "PRIVATE_MIGRATE_SCHEMA_NOT_READY status=%s", schema_result.get("status"))
        return {"schema": schema_result, "backfill": _empty_report(applied=bool(apply))}
    return {
        "schema": schema_result,
        "backfill": run_backfill(cur, apply=apply, actor_user_id=actor_user_id),
    }
