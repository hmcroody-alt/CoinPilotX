"""Durable conflict resolution — recording the owner's decision, and obeying it.

Hermetic, same pattern as the rest of this directory::

    python -m pytest tests/private_office/test_private_fact_conflicts.py
    python tests/private_office/test_private_fact_conflicts.py

What these tests are actually defending
---------------------------------------
* **The engine still never chooses.** Every lifecycle change below is traceable
  to an outcome a caller supplied. There is no path where detection archives
  anything, and no path where a stronger provenance wins on its own.
* **A settled conflict stops re-surfacing.** Detection without resolution is a
  list that only grows, and a conflict list nobody can clear is a conflict list
  nobody reads — which loses the one entry that mattered. ``SEPARATED`` is the
  outcome that genuinely needs this: its competitors all stay ACTIVE and keep
  matching the detector forever.
* **DEFERRED closes nothing.** "I looked and could not decide" must not hide the
  conflict, or engaging with a backlog becomes the way to make it disappear.
* **A decision binds to the set it was made about.** A conflict that acquires a
  third competitor is a different conflict and must be asked again. A stale
  decision silently swallowing a new disagreement is the worst failure available
  here, because it is invisible from every screen.
* **Rejection is atomic.** A resolution that names a fact the owner does not own
  must archive nothing at all — not "nothing after the first one".
* **Losing does not mean vanishing.** Rejected facts are archived, keeping their
  value, provenance and evidence. The store stops asserting them; it does not
  forget them.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_fact_conflicts_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import contradictions  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402

OWNER = 9601
OTHER = 9602

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def cursor():
    conn = db.connect()
    return conn, conn.cursor()


def _refuses(label: str, fn, expected=facts.PrivateFactRejected) -> None:
    try:
        fn()
    except expected as exc:
        check(label, True, str(exc))
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"raised {type(exc).__name__} instead: {exc}")
    else:
        check(label, False, "no exception raised")


_SUBJECT = 0


def _disagreeing_pair(cur, owner: int = OWNER, values=(0.35, 0.40)) -> dict:
    """Two facts that genuinely conflict, on a subject of their own.

    Each call gets a fresh ``subject_id`` so stages cannot contaminate each
    other through the detector's grouping — every conflict below is about
    exactly the rows its own stage created.

    The pair is built to defeat the *temporal change* exemption rather than to
    dodge it: same fact type, same subject, overlapping windows, six hours
    apart so they land inside ``SIMULTANEITY_HOURS`` and are compared as two
    claims about one moment rather than as a value that moved.
    """
    global _SUBJECT
    _SUBJECT += 1
    subject_id = f"n{_SUBJECT}"
    ids = []
    for offset, value in zip(("00", "06"), values):
        stamp = f"2026-01-01T{offset}:00:00Z"
        ids.append(facts.record_fact(
            cur, owner_user_id=owner, subject_type="NODE", subject_id=subject_id,
            fact_type="ownership_share", value_type=model.VALUE_PERCENT,
            value=value, provenance_type=model.PROVENANCE_USER_ASSERTED,
            valid_from=stamp, observed_at=stamp, actor_user_id=owner,
        )["fact_id"])

    found = [c for c in contradictions.detect_conflicts(
        cur, owner_user_id=owner, subject_id=subject_id)]
    if len(found) != 1:
        raise AssertionError(
            f"fixture did not produce exactly one conflict: {len(found)}")
    return {"conflict": found[0], "ids": ids, "subject_id": subject_id}


def _lifecycle(cur, fact_id: int) -> str:
    cur.execute(
        f"SELECT lifecycle_state FROM {schema.FACTS_TABLE} WHERE id = ?",
        (fact_id,))
    row = cur.fetchone()
    return "" if row is None else str(row[0] or "")


def _resolve(cur, conflict: dict, outcome: str, **kwargs) -> dict:
    """Resolve using the detector's own output, the way a route would."""
    return contradictions.resolve_conflict(
        cur, owner_user_id=kwargs.pop("owner_user_id", OWNER),
        conflict_id=conflict["conflict_id"], outcome=outcome,
        competing_fact_ids=conflict["competing_fact_ids"],
        subject_type=conflict["subject_type"], subject_id=conflict["subject_id"],
        fact_type=conflict["fact_type"], reason=conflict["reason"],
        actor_user_id=kwargs.pop("actor_user_id", OWNER), **kwargs)


# ---------------------------------------------------------------------------
def stage_the_vocabulary_is_registered() -> None:
    """The words this feature needs exist, and the closing set excludes DEFERRED.

    A guard rather than a tautology: ``RESOLUTION_CLOSES`` is the switch that
    decides whether a conflict disappears, and a later edit that adds DEFERRED
    to it would silence the backlog with a one-line change and no visible
    failure anywhere else.
    """
    print("\n[vocabulary]")
    for name in ("KEPT", "SEPARATED", "ALL_REJECTED", "DEFERRED"):
        check(f"{name} is a resolution outcome",
              name in model.RESOLUTION_OUTCOMES)
        check(f"{name} normalizes to itself",
              model.normalize_resolution(name) == name)
    check("normalize_resolution rejects an unknown outcome",
          model.normalize_resolution("PROBABLY") is None)
    check("normalize_resolution is case-insensitive",
          model.normalize_resolution("kept") == model.RESOLUTION_KEPT)
    check("DEFERRED does not close a conflict",
          model.RESOLUTION_DEFERRED not in model.RESOLUTION_CLOSES)
    check("the other three do close",
          model.RESOLUTION_CLOSES == {model.RESOLUTION_KEPT,
                                      model.RESOLUTION_SEPARATED,
                                      model.RESOLUTION_ALL_REJECTED})
    check("CONFLICT_RESOLVED is a history change type",
          model.CHANGE_CONFLICT_RESOLVED in model.HISTORY_CHANGE_TYPES)
    check("resolving is its own audit action",
          audit.ACTION_CONFLICT_RESOLVED in audit.ACTIONS)
    check("archiving is a different audit action from superseding",
          audit.ACTION_FACT_ARCHIVE in audit.ACTIONS
          and audit.ACTION_FACT_ARCHIVE != audit.ACTION_CONFLICT_RESOLVED)


def stage_kept_archives_only_the_losers() -> None:
    """KEPT retires the competitors and leaves the survivor untouched."""
    print("\n[KEPT]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)
    keep, lose = fixture["ids"]

    result = _resolve(cur, fixture["conflict"], model.RESOLUTION_KEPT,
                      kept_fact_id=keep)
    check("the outcome is recorded as KEPT",
          result["outcome"] == model.RESOLUTION_KEPT)
    check("exactly one fact was archived", result["archived_count"] == 1,
          str(result))
    check("the kept fact is still ACTIVE",
          _lifecycle(cur, keep) == model.LIFECYCLE_ACTIVE)
    check("the losing fact is ARCHIVED",
          _lifecycle(cur, lose) == model.LIFECYCLE_ARCHIVED)
    check("the loser is archived, not deleted — the row survives",
          _lifecycle(cur, lose) != "")

    # Losing a conflict is not being corrected. Nothing replaced this row, so
    # forging a supersession link would put a successor in a chain the member
    # never created and make the fact detail screen tell a story that is false.
    cur.execute(
        f"SELECT superseded_by_id FROM {schema.FACTS_TABLE} WHERE id = ?", (lose,))
    check("the archived loser has no forged successor",
          int(cur.fetchone()[0] or 0) == 0)

    remaining = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=fixture["subject_id"])
    check("the conflict no longer surfaces", remaining == [], str(remaining))
    conn.commit()


def stage_separated_changes_no_lifecycle_but_still_closes() -> None:
    """The outcome that actually needs the resolution table.

    SEPARATED means the detector was wrong to pair them — both are true, about
    different things. Nothing is archived, so every competitor stays ACTIVE and
    the detector will re-cluster them on the very next pass. Without a stored
    decision this conflict is unclearable by construction, which is exactly the
    "list nobody reads" failure the module docstring warns about.
    """
    print("\n[SEPARATED]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)
    left, right = fixture["ids"]

    result = _resolve(cur, fixture["conflict"], model.RESOLUTION_SEPARATED)
    check("nothing was archived", result["archived_count"] == 0)
    check("both facts remain ACTIVE",
          _lifecycle(cur, left) == model.LIFECYCLE_ACTIVE
          and _lifecycle(cur, right) == model.LIFECYCLE_ACTIVE)
    check("the resolution closes the conflict", result["closed"] is True)

    remaining = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=fixture["subject_id"])
    check("the conflict stops surfacing even though both rows still match",
          remaining == [], str(remaining))

    annotated = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=fixture["subject_id"],
        include_resolved=True)
    check("include_resolved still returns it", len(annotated) == 1)
    if annotated:
        check("it is annotated as resolved",
              annotated[0]["unresolved"] is False)
        check("the annotation carries the outcome",
              annotated[0].get("resolution", {}).get("outcome")
              == model.RESOLUTION_SEPARATED)
        check("the annotation carries the competing set it was decided about",
              annotated[0].get("resolution", {}).get("competing_fact_ids")
              == sorted(fixture["conflict"]["competing_fact_ids"]))
    conn.commit()


def stage_all_rejected_leaves_no_claim() -> None:
    """ALL_REJECTED archives every competitor.

    Leaving the subject with no claim of this type is the point. A truth ledger
    that insisted on keeping *one* of two values it had just been told are both
    wrong would be inventing a fact to avoid an empty field.
    """
    print("\n[ALL_REJECTED]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)

    result = _resolve(cur, fixture["conflict"], model.RESOLUTION_ALL_REJECTED)
    check("both facts were archived", result["archived_count"] == 2, str(result))
    check("neither remains ACTIVE",
          all(_lifecycle(cur, i) == model.LIFECYCLE_ARCHIVED
              for i in fixture["ids"]))
    check("no survivor was recorded", result["kept_fact_id"] == 0)
    check("the conflict no longer surfaces",
          contradictions.detect_conflicts(
              cur, owner_user_id=OWNER, subject_id=fixture["subject_id"]) == [])
    conn.commit()


def stage_deferred_keeps_the_conflict_open() -> None:
    """Recording "I could not decide" must not look like deciding."""
    print("\n[DEFERRED]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)

    result = _resolve(cur, fixture["conflict"], model.RESOLUTION_DEFERRED)
    check("DEFERRED closes nothing", result["closed"] is False)
    check("nothing was archived", result["archived_count"] == 0)

    still = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=fixture["subject_id"])
    check("the conflict still surfaces", len(still) == 1, str(still))
    if still:
        check("and it still reads unresolved", still[0]["unresolved"] is True)
        check("but the deferral is visible on it",
              still[0].get("resolution", {}).get("outcome")
              == model.RESOLUTION_DEFERRED)

    # The member comes back and decides. The later row wins.
    _resolve(cur, fixture["conflict"], model.RESOLUTION_SEPARATED)
    check("a later decision supersedes the deferral",
          contradictions.detect_conflicts(
              cur, owner_user_id=OWNER, subject_id=fixture["subject_id"]) == [])

    stored = contradictions.conflict_resolutions(
        cur, owner_user_id=OWNER,
        conflict_ids=[fixture["conflict"]["conflict_id"]])
    entry = stored.get(fixture["conflict"]["conflict_id"], {})
    check("the current decision is the newer one",
          entry.get("outcome") == model.RESOLUTION_SEPARATED)

    # Append-only: changing your mind must not erase having deferred.
    cur.execute(
        f"SELECT COUNT(*) FROM {schema.FACT_CONFLICTS_TABLE} "
        f"WHERE owner_user_id = ? AND conflict_id = ?",
        (OWNER, fixture["conflict"]["conflict_id"]))
    check("both decisions are retained — the table is append-only",
          int(cur.fetchone()[0] or 0) == 2)
    conn.commit()


def stage_a_decision_binds_to_the_set_it_was_made_about() -> None:
    """A new competitor re-opens the question.

    This is the quiet failure mode. If a resolution were keyed on
    ``conflict_id`` alone and that id were stable across membership changes, a
    decision made about two sources would suppress a later disagreement
    involving a third — and nothing on any screen would say so.
    """
    print("\n[set binding]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)
    _resolve(cur, fixture["conflict"], model.RESOLUTION_SEPARATED)
    check("the two-way conflict is settled",
          contradictions.detect_conflicts(
              cur, owner_user_id=OWNER, subject_id=fixture["subject_id"]) == [])

    third = facts.record_fact(
        cur, owner_user_id=OWNER, subject_type="NODE",
        subject_id=fixture["subject_id"], fact_type="ownership_share",
        value_type=model.VALUE_PERCENT, value=0.55,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        valid_from="2026-01-01T09:00:00Z", observed_at="2026-01-01T09:00:00Z",
        actor_user_id=OWNER)["fact_id"]

    reopened = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=fixture["subject_id"])
    check("a third competitor re-opens the conflict", len(reopened) == 1,
          str(reopened))
    if reopened:
        check("it is unresolved", reopened[0]["unresolved"] is True)
        check("it has a different conflict_id than the settled pair",
              reopened[0]["conflict_id"] != fixture["conflict"]["conflict_id"])
        check("and it names all three competitors",
              sorted(reopened[0]["competing_fact_ids"])
              == sorted(fixture["ids"] + [third]))

    # Defence in depth: even a resolution stored under the *right* id must be
    # ignored if the competing set it recorded no longer matches what is in
    # front of the member.
    contradictions.resolve_conflict(
        cur, owner_user_id=OWNER, conflict_id=reopened[0]["conflict_id"],
        outcome=model.RESOLUTION_SEPARATED,
        competing_fact_ids=fixture["ids"],  # the stale two-way set
        actor_user_id=OWNER)
    still = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=fixture["subject_id"])
    check("a decision recorded against a stale set does not suppress",
          len(still) == 1 and still[0]["unresolved"] is True, str(still))
    conn.commit()


def stage_the_engine_never_decides_on_its_own() -> None:
    """Detection is read-only, whatever the provenance gap between competitors.

    The tempting bug is letting the stronger source win automatically. It looks
    like an improvement and it is wrong on the facts: a member's own statement
    disagreeing with a provider's record is precisely the case where the
    provider may be the stale one.
    """
    print("\n[no silent choice]")
    conn, cur = cursor()
    global _SUBJECT
    _SUBJECT += 1
    subject_id = f"n{_SUBJECT}"

    weak = facts.record_fact(
        cur, owner_user_id=OWNER, subject_type="NODE", subject_id=subject_id,
        fact_type="ownership_share", value_type=model.VALUE_PERCENT, value=0.35,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        valid_from="2026-01-01T00:00:00Z", observed_at="2026-01-01T00:00:00Z",
        actor_user_id=OWNER)["fact_id"]
    strong = facts.record_fact(
        cur, owner_user_id=OWNER, subject_type="NODE", subject_id=subject_id,
        fact_type="ownership_share", value_type=model.VALUE_PERCENT, value=0.40,
        provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED,
        valid_from="2026-01-01T06:00:00Z", observed_at="2026-01-01T06:00:00Z",
        actor_user_id=OWNER)["fact_id"]

    found = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=subject_id)
    check("the mismatch is detected", len(found) == 1)
    check("detection changed no lifecycle — the weaker source survives",
          _lifecycle(cur, weak) == model.LIFECYCLE_ACTIVE)
    check("nor the stronger one",
          _lifecycle(cur, strong) == model.LIFECYCLE_ACTIVE)
    check("both keep their original provenance, so the disagreement is sayable",
          {r["provenance_type"] for r in found[0]["competing"]}
          == {model.PROVENANCE_USER_ASSERTED,
              model.PROVENANCE_DOCUMENT_EXTRACTED})

    contradictions.mark_conflicts(cur, owner_user_id=OWNER, conflicts=found)
    check("marking a conflict does not archive either side",
          _lifecycle(cur, weak) == model.LIFECYCLE_ACTIVE
          and _lifecycle(cur, strong) == model.LIFECYCLE_ACTIVE)
    check("marking does not resolve it",
          len(contradictions.detect_conflicts(
              cur, owner_user_id=OWNER, subject_id=subject_id)) == 1)
    check("and no resolution row was invented",
          contradictions.conflict_resolutions(
              cur, owner_user_id=OWNER,
              conflict_ids=[found[0]["conflict_id"]]) == {})
    conn.commit()


def stage_bad_decisions_are_refused_atomically() -> None:
    """Invalid resolutions raise, and leave nothing half-applied."""
    print("\n[refusals]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)
    conflict = fixture["conflict"]
    left, right = fixture["ids"]

    _refuses("an unknown outcome is refused",
             lambda: _resolve(cur, conflict, "PROBABLY_THE_FIRST"))
    _refuses("KEPT without a survivor is refused",
             lambda: _resolve(cur, conflict, model.RESOLUTION_KEPT))
    _refuses("KEPT naming a fact outside the competing set is refused",
             lambda: _resolve(cur, conflict, model.RESOLUTION_KEPT,
                              kept_fact_id=999999))
    _refuses("a survivor on ALL_REJECTED is refused, not ignored",
             lambda: _resolve(cur, conflict, model.RESOLUTION_ALL_REJECTED,
                              kept_fact_id=left))
    _refuses("a one-sided conflict is refused",
             lambda: contradictions.resolve_conflict(
                 cur, owner_user_id=OWNER, conflict_id=conflict["conflict_id"],
                 outcome=model.RESOLUTION_KEPT, competing_fact_ids=[left],
                 kept_fact_id=left, actor_user_id=OWNER))
    _refuses("a missing conflict_id is refused",
             lambda: contradictions.resolve_conflict(
                 cur, owner_user_id=OWNER, conflict_id="",
                 outcome=model.RESOLUTION_SEPARATED,
                 competing_fact_ids=[left, right], actor_user_id=OWNER))
    _refuses("an unknown note_key is refused",
             lambda: _resolve(cur, conflict, model.RESOLUTION_SEPARATED,
                              note_key="because_i_said_so"))

    check("no refusal archived anything",
          _lifecycle(cur, left) == model.LIFECYCLE_ACTIVE
          and _lifecycle(cur, right) == model.LIFECYCLE_ACTIVE)
    check("no refusal wrote a resolution row",
          contradictions.conflict_resolutions(
              cur, owner_user_id=OWNER,
              conflict_ids=[conflict["conflict_id"]]) == {})
    check("the conflict is untouched and still surfaces",
          len(contradictions.detect_conflicts(
              cur, owner_user_id=OWNER, subject_id=fixture["subject_id"])) == 1)
    conn.commit()


def stage_resolution_is_owner_scoped() -> None:
    """One member cannot resolve, read, or archive another member's conflict."""
    print("\n[owner isolation]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)
    conflict = fixture["conflict"]
    left, right = fixture["ids"]

    # The ownership check has to precede the first UPDATE, or a cross-owner
    # call archives one row before discovering it had no right to.
    _refuses("another owner cannot resolve this conflict",
             lambda: contradictions.resolve_conflict(
                 cur, owner_user_id=OTHER, conflict_id=conflict["conflict_id"],
                 outcome=model.RESOLUTION_ALL_REJECTED,
                 competing_fact_ids=[left, right], actor_user_id=OTHER),
             expected=facts.PrivateFactMissing)
    check("and archived neither fact in the attempt",
          _lifecycle(cur, left) == model.LIFECYCLE_ACTIVE
          and _lifecycle(cur, right) == model.LIFECYCLE_ACTIVE)
    cur.execute(
        f"SELECT COUNT(*) FROM {schema.FACT_CONFLICTS_TABLE} WHERE owner_user_id = ?",
        (OTHER,))
    check("and wrote no resolution row for the other owner",
          int(cur.fetchone()[0] or 0) == 0)

    # The case that actually needs the *pre-loop* ownership check. Above, the
    # caller owned neither fact, so `archive_fact`'s own owner predicate
    # rejected the very first one and nothing had happened yet. Here the caller
    # owns the first competitor and not the second: archiving as it goes would
    # retire a legitimate fact and only then discover the resolution was
    # invalid, leaving the member's ledger holding half of a decision nobody
    # made. Verified by mutation — deleting the pre-loop check leaves every
    # other check in this stage green.
    mixed = facts.record_fact(
        cur, owner_user_id=OTHER, subject_type="NODE",
        subject_id=fixture["subject_id"], fact_type="ownership_share",
        value_type=model.VALUE_PERCENT, value=0.60,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        valid_from="2026-01-01T06:00:00Z", observed_at="2026-01-01T06:00:00Z",
        actor_user_id=OTHER)["fact_id"]
    _refuses("a resolution mixing in another owner's fact is refused",
             lambda: contradictions.resolve_conflict(
                 cur, owner_user_id=OWNER, conflict_id=conflict["conflict_id"],
                 outcome=model.RESOLUTION_ALL_REJECTED,
                 competing_fact_ids=[left, mixed], actor_user_id=OWNER),
             expected=facts.PrivateFactMissing)
    check("the owner's own fact was not archived before the refusal",
          _lifecycle(cur, left) == model.LIFECYCLE_ACTIVE,
          f"lifecycle={_lifecycle(cur, left)}")
    check("the other owner's fact was not touched either",
          _lifecycle(cur, mixed) == model.LIFECYCLE_ACTIVE)

    _resolve(cur, conflict, model.RESOLUTION_SEPARATED)
    check("the owner's own resolution is readable by the owner",
          conflict["conflict_id"] in contradictions.conflict_resolutions(
              cur, owner_user_id=OWNER, conflict_ids=[conflict["conflict_id"]]))
    check("the same conflict_id reads as nothing for another owner",
          contradictions.conflict_resolutions(
              cur, owner_user_id=OTHER,
              conflict_ids=[conflict["conflict_id"]]) == {})
    check("another owner's detection is unaffected",
          contradictions.detect_conflicts(cur, owner_user_id=OTHER) == [])
    conn.commit()


def stage_the_decision_lands_in_history_and_audit() -> None:
    """Every competitor's timeline records the conflict, including the survivor.

    A fact that was questioned and won should say so. If only the losers were
    annotated, the survivor's timeline would read as though it had never been
    in dispute — which is the story a truth ledger is supposed to be able to
    tell.
    """
    print("\n[history and audit]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)
    keep, lose = fixture["ids"]

    before_audit = _audit_count(cur, audit.ACTION_CONFLICT_RESOLVED)
    _resolve(cur, fixture["conflict"], model.RESOLUTION_KEPT, kept_fact_id=keep)

    for fact_id, role in ((keep, "survivor"), (lose, "loser")):
        entries = facts.fact_history(cur, owner_user_id=OWNER, fact_id=fact_id)
        kinds = [e.get("change_type") for e in entries]
        check(f"the {role} records CONFLICT_RESOLVED",
              model.CHANGE_CONFLICT_RESOLVED in kinds, str(kinds))
        resolved = next(
            (e for e in entries
             if e.get("change_type") == model.CHANGE_CONFLICT_RESOLVED), {})
        check(f"the {role}'s entry names the outcome",
              resolved.get("to_state") == model.RESOLUTION_KEPT)
        check(f"the {role}'s entry names the conflict it settled",
              resolved.get("from_state") == fixture["conflict"]["conflict_id"])

    loser_kinds = [e.get("change_type") for e in
                   facts.fact_history(cur, owner_user_id=OWNER, fact_id=lose)]
    check("the loser also records being archived",
          model.CHANGE_ARCHIVED in loser_kinds, str(loser_kinds))
    check("the survivor does not",
          model.CHANGE_ARCHIVED not in [
              e.get("change_type") for e in
              facts.fact_history(cur, owner_user_id=OWNER, fact_id=keep)])

    check("the resolution is audited",
          _audit_count(cur, audit.ACTION_CONFLICT_RESOLVED) > before_audit)

    # Rule 8: the audit trail explains that a decision happened without
    # recording either of the values it was about.
    cur.execute(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE owner_user_id = ? AND action = ?",
        (OWNER, audit.ACTION_CONFLICT_RESOLVED))
    blob = " ".join(str(v) for row in cur.fetchall() for v in dict(row).values())
    check("no competing value leaked into the audit row",
          "0.35" not in blob and "0.4" not in blob, blob[:200])
    conn.commit()


def _audit_count(cur, action: str) -> int:
    cur.execute(
        f"SELECT COUNT(*) FROM {schema.AUDIT_TABLE} "
        f"WHERE owner_user_id = ? AND action = ?", (OWNER, action))
    return int(cur.fetchone()[0] or 0)


def stage_resolution_survives_a_corrupt_row() -> None:
    """A resolution that cannot be read must re-open the conflict, not crash it.

    Degrading toward "ask the member again" is the safe direction. The opposite
    — a parse failure that suppresses — would hide a live disagreement, and a
    raise would take down the whole conflict screen over one bad row.
    """
    print("\n[resilience]")
    conn, cur = cursor()
    fixture = _disagreeing_pair(cur)
    _resolve(cur, fixture["conflict"], model.RESOLUTION_SEPARATED)
    check("settled to begin with",
          contradictions.detect_conflicts(
              cur, owner_user_id=OWNER, subject_id=fixture["subject_id"]) == [])

    cur.execute(
        f"UPDATE {schema.FACT_CONFLICTS_TABLE} SET competing_fact_ids = ? "
        f"WHERE owner_user_id = ? AND conflict_id = ?",
        ("{not json", OWNER, fixture["conflict"]["conflict_id"]))
    reopened = contradictions.detect_conflicts(
        cur, owner_user_id=OWNER, subject_id=fixture["subject_id"])
    check("a corrupt competing set re-opens the conflict rather than hiding it",
          len(reopened) == 1 and reopened[0]["unresolved"] is True, str(reopened))
    conn.commit()


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    schema.reset_schema_cache()
    conn, cur = cursor()
    schema.ensure_private_schema(cur)
    conn.commit()

    stage_the_vocabulary_is_registered()
    stage_kept_archives_only_the_losers()
    stage_separated_changes_no_lifecycle_but_still_closes()
    stage_all_rejected_leaves_no_claim()
    stage_deferred_keeps_the_conflict_open()
    stage_a_decision_binds_to_the_set_it_was_made_about()
    stage_the_engine_never_decides_on_its_own()
    stage_bad_decisions_are_refused_atomically()
    stage_resolution_is_owner_scoped()
    stage_the_decision_lands_in_history_and_audit()
    stage_resolution_survives_a_corrupt_row()

    print()
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — conflicts resolve durably, and only when a person says so.")
    return 0


def test_private_fact_conflicts():
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
