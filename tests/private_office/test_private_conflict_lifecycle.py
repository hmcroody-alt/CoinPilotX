"""Batch 2 — conflict detection, contestation and settlement, proved at runtime.

Hermetic: points ``services.db`` at a throwaway SQLite file and lets
``ensure_private_schema`` build everything it needs. Runs either way::

    python -m pytest tests/private_office/test_private_conflict_lifecycle.py
    python tests/private_office/test_private_conflict_lifecycle.py

What this suite is defending
----------------------------
The ledger core separated provenance from verification. This batch spends that
separation on the thing it was bought for — a fact can now be *visibly
contested* without forgetting where it came from — and closes the loop that
made the contradiction engine unusable in practice: a member who settled a
disagreement was asked about it again on the next scan, and every scan after,
forever.

Each stage asserts one claim the modules make in prose:

* Ordinary temporal change still is not a conflict, and stamping contested rows
  did not change that. The 800k-in-2024 / 950k-in-2026 case is the regression
  this batch was most likely to break.
* A real disagreement moves both rows to ``verification_state = CONFLICTING``
  and leaves ``provenance_type`` alone, so "your insurer says March, the
  document says April" is still sayable.
* A row the *member* disputed is not overwritten by the detector. The cheap
  machine signal must not clobber the expensive human one.
* Resolution is an owner act. UNDX is refused, and the refusal is audited.
* No path — resolve, dismiss, or resolve twice — reaches ``VERIFIED`` or
  ``PROVIDER_VERIFIED``.
* A settled conflict stops being reported, and a *new* competing source
  correctly reopens it, because the member never settled the question that
  source raises.
* Every new entry point is owner-isolated, and a foreign fact id is
  indistinguishable from one that was never issued.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_conflict_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import contradictions  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import telemetry  # noqa: E402

USER_A = 8101
USER_B = 8102

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


def read_fact(cur, owner: int, fact_id: int) -> dict:
    """One row, by id, with the owner predicate in the SELECT.

    A read, so the write-boundary guard has nothing to say about it. The owner
    predicate is here anyway because a test helper that fetched by id alone
    would pass just as happily against code that had lost its isolation.
    """
    cur.execute(
        "SELECT id, provenance_type, verification_state, lifecycle_state, "
        "conflict_id, last_verified_at, valid_to "
        f"FROM {schema.FACTS_TABLE} WHERE id = ? AND owner_user_id = ?",
        (fact_id, owner),
    )
    row = cur.fetchone()
    if row is None:
        return {}
    return {
        "id": row["id"], "provenance_type": row["provenance_type"],
        "verification_state": row["verification_state"],
        "lifecycle_state": row["lifecycle_state"],
        "conflict_id": row["conflict_id"],
        "last_verified_at": row["last_verified_at"],
        "valid_to": row["valid_to"],
    }


def plant(cur, owner: int, subject_id: str, fact_type: str, value: str,
          provenance: str, **kwargs) -> int:
    written = facts.record_fact(
        cur, owner_user_id=owner, subject_type="NODE", subject_id=subject_id,
        fact_type=fact_type, value=value, value_type=kwargs.pop("value_type", model.VALUE_STRING),
        provenance_type=provenance,
        observed_at=kwargs.pop("observed_at", "2026-06-01T00:00:00+00:00"),
        valid_from=kwargs.pop("valid_from", "2026-06-01T00:00:00+00:00"),
        **kwargs)
    return int(written["fact_id"])


def detect_and_mark(cur, owner: int, subject_id: str) -> list[dict]:
    found = contradictions.detect_conflicts(
        cur, owner_user_id=owner, subject_id=subject_id)
    if found:
        contradictions.mark_conflicts(cur, owner_user_id=owner, conflicts=found)
    return found


# ---------------------------------------------------------------------------
def stage_vocabulary_is_wired():
    """The words exist, are declared everywhere they must be, and agree."""
    print("\n[vocabulary]")

    check("the writer knows a flag operation",
          facts.OP_FLAG_CONFLICT in facts.VERIFICATION_TRANSITIONS)
    check("the writer knows a resolve operation",
          facts.OP_RESOLVE in facts.VERIFICATION_TRANSITIONS)
    # And both must be in the operation vocabulary, not merely in the transition
    # table. `_write_history` filters on this tuple and returns quietly when it
    # misses, so an operation declared in one place and not the other mutates
    # the fact and leaves no trace of having done so.
    check("both operations are declared history-writable",
          {facts.OP_FLAG_CONFLICT, facts.OP_RESOLVE}
          <= set(facts.FACT_OPERATIONS))

    # The single most important assertion in this file. Two operations were
    # added that both end in a member being happier about a fact, and either of
    # them reaching VERIFIED would turn the conflict queue into the laundering
    # machine the ledger core exists to prevent.
    reachable = {to_state for _from, to_state in facts.VERIFICATION_TRANSITIONS.values()}
    check("no operation can transition a fact into VERIFIED",
          model.VERIFICATION_VERIFIED not in reachable, str(sorted(reachable)))
    check("no operation can transition a fact into PROVIDER_VERIFIED",
          model.VERIFICATION_PROVIDER_VERIFIED not in reachable)
    check("resolving lands on USER_CONFIRMED and stops there",
          facts.VERIFICATION_TRANSITIONS[facts.OP_RESOLVE][1]
          == model.VERIFICATION_USER_CONFIRMED)

    # A dispute is a person; a flag is a scan. The scan must not overwrite the
    # person.
    flag_from = facts.VERIFICATION_TRANSITIONS[facts.OP_FLAG_CONFLICT][0]
    check("the detector may not overwrite a member's own dispute",
          model.VERIFICATION_DISPUTED not in flag_from)
    check("but it may flag a provider-verified row",
          model.VERIFICATION_PROVIDER_VERIFIED in flag_from)

    # Resolution is judgement, so it moves the verification clock; flagging is
    # an observation, so it does not.
    check("resolving counts as someone having looked",
          facts.OP_RESOLVE in facts.VERIFYING_OPERATIONS)
    check("being flagged does not count as having been checked",
          facts.OP_FLAG_CONFLICT not in facts.VERIFYING_OPERATIONS)

    # Telemetry declares what the code emits, and `spec_is_sound` is the guard
    # that a field was not added to one and not the other.
    check("both new operations are declared to telemetry",
          {"flag_conflict", "resolve"} <= telemetry.FACT_OPERATION_VOCAB)
    check("the resolution event is declared",
          telemetry.EVENT_CONFLICT_RESOLVED in telemetry.EVENTS)
    check("the telemetry spec is still sound", telemetry.spec_is_sound() == [])

    # Both audit verbs already existed — the resolved one has been unused
    # vocabulary since the ledger core landed. This batch is what makes it real.
    check("both audit verbs are known",
          {audit.ACTION_CONFLICT_DETECTED, audit.ACTION_CONFLICT_RESOLVED}
          <= set(audit.ACTIONS))

    # And the reason codes the orchestration passes must survive normalisation,
    # or they are silently dropped to '' and the history says nothing.
    check("conflict reason codes are in the closed vocabulary",
          {"conflict_resolution", "conflict_dismissed"} <= set(facts.REASON_CODES))


# ---------------------------------------------------------------------------
def stage_schema_carries_resolutions():
    """The settlement table exists, is required, and is idempotent."""
    print("\n[schema]")
    conn, cur = cursor()
    schema.reset_schema_cache()
    first = schema.ensure_private_schema(cur, force=True)
    check("ensure reports ready", first["status"] == schema.STATUS_READY,
          str(first.get("error") or first.get("missing")))
    check("the conflict table is part of the substrate",
          schema.FACT_CONFLICTS_TABLE in set(first["tables"]),
          str(sorted(first["tables"])))
    second = schema.ensure_private_schema(cur, force=True)
    check("a second ensure is still ready (idempotent)",
          second["status"] == schema.STATUS_READY)

    # Required rather than optional: a deployment that cannot see resolutions
    # would re-ask every settled question, which is worse than failing loudly.
    check("the resolution columns are required, not merely added",
          "winning_fact_id" in schema.REQUIRED_COLUMNS[schema.FACT_CONFLICTS_TABLE])
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_temporal_change_is_still_not_a_conflict():
    """The regression this batch was most likely to cause."""
    print("\n[temporal change]")
    conn, cur = cursor()

    for value, year in (("800000", 2024), ("950000", 2026)):
        plant(cur, USER_A, "900", "estimated_value", value,
              model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY,
              observed_at=f"{year}-01-01T00:00:00+00:00",
              valid_from=f"{year}-01-01T00:00:00+00:00")
    found = contradictions.detect_conflicts(cur, owner_user_id=USER_A, subject_id="900")
    check("a value that rose over two years is still not a conflict",
          found == [], str(found))

    # And nothing was stamped, which is the part the new code could have got
    # wrong: a detector that flagged first and decided afterwards would leave
    # both rows contested and both invisible to every projection.
    cur.execute(
        f"SELECT verification_state FROM {schema.FACTS_TABLE} "
        "WHERE owner_user_id = ? AND subject_id = ?", (USER_A, "900"))
    states = sorted(str(r["verification_state"]) for r in cur.fetchall())
    check("neither row was stamped contested",
          model.VERIFICATION_CONFLICTING not in states, str(states))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_a_real_disagreement_is_stamped():
    """CONFLICTING lands on the verification axis; provenance is untouched."""
    print("\n[contested]")
    conn, cur = cursor()

    insurer = plant(cur, USER_A, "901", "renewal_date", "2027-03-01",
                    model.PROVENANCE_PROVIDER_ASSERTED,
                    value_type=model.VALUE_DATE)
    document = plant(cur, USER_A, "901", "renewal_date", "2027-04-15",
                     model.PROVENANCE_DOCUMENT_EXTRACTED,
                     value_type=model.VALUE_DATE)

    found = detect_and_mark(cur, USER_A, "901")
    check("two renewal dates for one period are one conflict",
          len(found) == 1, str(len(found)))
    if not found:
        conn.commit()
        conn.close()
        return
    conflict = found[0]
    check("the conflict is unresolved", conflict["unresolved"] is True)
    check("it carries no settlement yet", conflict["resolution"] is None)

    left = read_fact(cur, USER_A, insurer)
    right = read_fact(cur, USER_A, document)
    check("both rows are now visibly contested",
          left["verification_state"] == model.VERIFICATION_CONFLICTING
          and right["verification_state"] == model.VERIFICATION_CONFLICTING,
          f"{left['verification_state']} / {right['verification_state']}")

    # The whole reason the second axis exists. If contestation had gone onto the
    # provenance column — as the pre-ledger schema forced — this check would
    # fail and the product would have lost the ability to name the two sources.
    check("the insurer's row still remembers it came from the insurer",
          left["provenance_type"] == model.PROVENANCE_PROVIDER_ASSERTED,
          left["provenance_type"])
    check("the document's row still remembers it came from a document",
          right["provenance_type"] == model.PROVENANCE_DOCUMENT_EXTRACTED,
          right["provenance_type"])
    check("neither row was silently retired",
          left["lifecycle_state"] == model.LIFECYCLE_ACTIVE
          and right["lifecycle_state"] == model.LIFECYCLE_ACTIVE)
    check("both rows carry the conflict marker",
          left["conflict_id"] == right["conflict_id"] == conflict["conflict_id"])

    # A contested fact must stop reading as current truth everywhere, not just
    # on the screen that shows conflicts.
    check("contested facts are not quotable as truth",
          model.VERIFICATION_CONFLICTING in model.UNTRUSTWORTHY_VERIFICATION)
    check("and cannot win an argument on standing",
          model.VERIFICATION_RANK[model.VERIFICATION_CONFLICTING] == 0)

    # Idempotence: the detector runs on every retrieval.
    again = detect_and_mark(cur, USER_A, "901")
    check("re-running detection produces the same conflict id",
          len(again) == 1 and again[0]["conflict_id"] == conflict["conflict_id"])
    check("and re-marking does not disturb the rows",
          read_fact(cur, USER_A, insurer)["verification_state"]
          == model.VERIFICATION_CONFLICTING)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_a_members_dispute_survives_the_detector():
    """The cheap machine signal must not overwrite the expensive human one."""
    print("\n[dispute precedence]")
    conn, cur = cursor()

    mine = plant(cur, USER_A, "902", "ownership_share", "35",
                 model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_PERCENT,
                 valid_to="2026-12-31T00:00:00+00:00")
    theirs = plant(cur, USER_A, "902", "ownership_share", "40",
                   model.PROVENANCE_PROVIDER_ASSERTED, value_type=model.VALUE_PERCENT,
                   valid_to="2026-12-31T00:00:00+00:00")

    disputed = facts.dispute_fact(cur, owner_user_id=USER_A, fact_id=theirs)
    check("the member disputed the provider's figure",
          disputed["status"] == "applied", str(disputed))

    detect_and_mark(cur, USER_A, "902")
    check("the disputed row keeps the member's verdict",
          read_fact(cur, USER_A, theirs)["verification_state"]
          == model.VERIFICATION_DISPUTED,
          read_fact(cur, USER_A, theirs)["verification_state"])
    check("while the undisputed competitor is stamped contested",
          read_fact(cur, USER_A, mine)["verification_state"]
          == model.VERIFICATION_CONFLICTING,
          read_fact(cur, USER_A, mine)["verification_state"])
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_only_the_owner_may_settle():
    """UNDX may explain a conflict. It may not decide one."""
    print("\n[who may decide]")
    conn, cur = cursor()

    plant(cur, USER_A, "903", "annual_premium", "1200",
          model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY)
    plant(cur, USER_A, "903", "annual_premium", "1850",
          model.PROVENANCE_PROVIDER_ASSERTED, value_type=model.VALUE_MONEY)
    found = detect_and_mark(cur, USER_A, "903")
    check("the premium figures conflict", len(found) == 1, str(len(found)))
    if not found:
        conn.commit()
        conn.close()
        return
    marker = found[0]["conflict_id"]
    winner = found[0]["competing_fact_ids"][0]

    before = audit_count(cur, USER_A)
    refused = contradictions.resolve_conflict(
        cur, owner_user_id=USER_A, conflict_id=marker,
        winning_fact_id=winner, actor_type=facts.ACTOR_UNDX)
    check("UNDX is refused", refused["status"] == contradictions.RESOLVE_REFUSED,
          str(refused))
    check("and told why", refused["reason"] == "actor_not_owner", refused["reason"])
    check("the refusal is written to the audit trail",
          audit_count(cur, USER_A) > before)
    check("nothing moved", read_fact(cur, USER_A, winner)["verification_state"]
          == model.VERIFICATION_CONFLICTING)

    # A malformed request is refused before it can touch anything, and the two
    # refusals are distinguishable so a caller can say something useful.
    unknown = contradictions.resolve_conflict(
        cur, owner_user_id=USER_A, conflict_id=marker, winning_fact_id=winner,
        resolution="whatever_the_caller_felt_like")
    check("an unknown resolution kind is refused",
          unknown["reason"] == "unknown_resolution", str(unknown))

    # And a winner from outside the conflict cannot be smuggled in — this is the
    # check that makes re-reading the competitors from the table worthwhile.
    outsider = plant(cur, USER_A, "904", "annual_premium", "10",
                     model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY)
    smuggled = contradictions.resolve_conflict(
        cur, owner_user_id=USER_A, conflict_id=marker, winning_fact_id=outsider)
    check("a fact from another subject cannot be nominated as the winner",
          smuggled["reason"] == "winner_not_in_conflict", str(smuggled))
    conn.commit()
    conn.close()


def audit_count(cur, owner: int) -> int:
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {schema.AUDIT_TABLE} WHERE owner_user_id = ?",
        (owner,))
    row = cur.fetchone()
    return int(row["n"] if hasattr(row, "keys") else row[0])


# ---------------------------------------------------------------------------
def stage_settling_a_conflict():
    """The owner picks a winner; the loser is flagged, not deleted."""
    print("\n[settlement]")
    conn, cur = cursor()

    keep = plant(cur, USER_A, "905", "policy_start", "2026-01-05",
                 model.PROVENANCE_DOCUMENT_EXTRACTED, value_type=model.VALUE_DATE)
    drop = plant(cur, USER_A, "905", "policy_start", "2026-02-20",
                 model.PROVENANCE_MEETING_DERIVED, value_type=model.VALUE_DATE)
    found = detect_and_mark(cur, USER_A, "905")
    check("the start dates conflict", len(found) == 1, str(len(found)))
    if not found:
        conn.commit()
        conn.close()
        return
    marker = found[0]["conflict_id"]

    settled = contradictions.resolve_conflict(
        cur, owner_user_id=USER_A, conflict_id=marker, winning_fact_id=keep,
        reason=contradictions.REASON_DATE)
    check("the settlement applied",
          settled["status"] == contradictions.RESOLVE_APPLIED, str(settled))
    check("it names the winner", settled["winning_fact_id"] == keep)
    check("and moved exactly one loser", settled["losers_moved"] == 1,
          str(settled["losers_moved"]))
    check("it was not a re-settlement", settled["resettled"] is False)

    won = read_fact(cur, USER_A, keep)
    lost = read_fact(cur, USER_A, drop)
    check("the winner is confirmed by the owner",
          won["verification_state"] == model.VERIFICATION_USER_CONFIRMED,
          won["verification_state"])
    # Said again at runtime, against a real row, because the transition table
    # check earlier proves the machine and this proves the path through it.
    check("the winner is NOT verified",
          won["verification_state"] != model.VERIFICATION_VERIFIED)
    check("the winner's verification clock moved",
          bool(won["last_verified_at"]), str(won["last_verified_at"]))
    check("the loser is disputed by default, not retired",
          lost["verification_state"] == model.VERIFICATION_DISPUTED,
          lost["verification_state"])
    check("the loser's row is still live and readable",
          lost["lifecycle_state"] == model.LIFECYCLE_ACTIVE)
    check("both rows kept their provenance through the settlement",
          won["provenance_type"] == model.PROVENANCE_DOCUMENT_EXTRACTED
          and lost["provenance_type"] == model.PROVENANCE_MEETING_DERIVED)

    # The point of the whole batch.
    reopened = contradictions.detect_conflicts(
        cur, owner_user_id=USER_A, subject_id="905")
    check("the settled conflict is no longer reported", reopened == [],
          str(reopened))
    visible = contradictions.detect_conflicts(
        cur, owner_user_id=USER_A, subject_id="905", include_resolved=True)
    check("but a history view can still see it", len(visible) == 1)
    if visible:
        check("and it reads as resolved", visible[0]["unresolved"] is False)
        check("with the settlement attached",
              (visible[0]["resolution"] or {}).get("winning_fact_id") == keep,
              str(visible[0]["resolution"]))

    # The history table is what lets a member ask "why does this say what it
    # says" a year from now.
    history = facts.list_fact_history(cur, owner_user_id=USER_A, fact_id=keep)
    ops = [h.get("operation") for h in history]
    check("the fact's history records both the flag and the resolution",
          facts.OP_FLAG_CONFLICT in ops and facts.OP_RESOLVE in ops, str(ops))

    # Re-settling is legitimate and must be marked as such.
    again = contradictions.resolve_conflict(
        cur, owner_user_id=USER_A, conflict_id=marker, winning_fact_id=keep)
    check("re-settling the same conflict is allowed",
          again["status"] == contradictions.RESOLVE_APPLIED, str(again))
    check("and is marked as a re-settlement", again["resettled"] is True)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_retiring_a_loser_and_dismissing_a_conflict():
    """The two dispositions that are not the default."""
    print("\n[retire and dismiss]")
    conn, cur = cursor()

    keep = plant(cur, USER_A, "906", "account_balance", "5000",
                 model.PROVENANCE_PROVIDER_ASSERTED, value_type=model.VALUE_MONEY)
    stale = plant(cur, USER_A, "906", "account_balance", "8200",
                  model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY)
    found = detect_and_mark(cur, USER_A, "906")
    if not found:
        check("the balances conflict", False, "no conflict detected")
        conn.commit()
        conn.close()
        return
    settled = contradictions.resolve_conflict(
        cur, owner_user_id=USER_A, conflict_id=found[0]["conflict_id"],
        winning_fact_id=keep,
        loser_disposition=contradictions.DISPOSITION_SUPERSEDE)
    check("superseding the loser applied",
          settled["status"] == contradictions.RESOLVE_APPLIED, str(settled))
    retired = read_fact(cur, USER_A, stale)
    check("the retired row left the active set",
          retired["lifecycle_state"] == model.LIFECYCLE_SUPERSEDED,
          retired["lifecycle_state"])
    check("and its validity window was closed so it stops arguing",
          bool(retired["valid_to"]), str(retired["valid_to"]))
    check("the retired row is still present, not deleted", retired != {})

    # Dismissal: two sources that were never describing the same thing.
    first = plant(cur, USER_A, "907", "office_address", "12 Rue Test",
                  model.PROVENANCE_USER_ASSERTED)
    second = plant(cur, USER_A, "907", "office_address", "48 Avenue Other",
                   model.PROVENANCE_DOCUMENT_EXTRACTED)
    pair = detect_and_mark(cur, USER_A, "907")
    if not pair:
        check("the two addresses conflict", False, "no conflict detected")
        conn.commit()
        conn.close()
        return
    dismissed = contradictions.resolve_conflict(
        cur, owner_user_id=USER_A, conflict_id=pair[0]["conflict_id"],
        resolution=contradictions.RESOLUTION_DISMISSED)
    check("a conflict can be dismissed without nominating a winner",
          dismissed["status"] == contradictions.RESOLVE_APPLIED, str(dismissed))
    check("dismissal names no winner", dismissed["winning_fact_id"] == 0)
    check("and moves both rows", dismissed["losers_moved"] == 2,
          str(dismissed["losers_moved"]))
    for fact_id in (first, second):
        row = read_fact(cur, USER_A, fact_id)
        check(f"row {fact_id} left the contested state",
              row["verification_state"] != model.VERIFICATION_CONFLICTING,
              row["verification_state"])
        check(f"row {fact_id} did not become verified",
              row["verification_state"] != model.VERIFICATION_VERIFIED)
    check("a dismissed conflict stops being reported",
          contradictions.detect_conflicts(
              cur, owner_user_id=USER_A, subject_id="907") == [])
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_a_new_source_reopens_a_settled_question():
    """The behaviour that looks like a bug and is the reason the id is derived."""
    print("\n[a third source]")
    conn, cur = cursor()

    a = plant(cur, USER_A, "908", "maturity_date", "2030-01-01",
              model.PROVENANCE_PROVIDER_ASSERTED, value_type=model.VALUE_DATE)
    plant(cur, USER_A, "908", "maturity_date", "2030-06-01",
          model.PROVENANCE_DOCUMENT_EXTRACTED, value_type=model.VALUE_DATE)
    found = detect_and_mark(cur, USER_A, "908")
    if not found:
        check("the maturity dates conflict", False, "no conflict detected")
        conn.commit()
        conn.close()
        return
    contradictions.resolve_conflict(
        cur, owner_user_id=USER_A, conflict_id=found[0]["conflict_id"],
        winning_fact_id=a)
    check("the question is settled",
          contradictions.detect_conflicts(
              cur, owner_user_id=USER_A, subject_id="908") == [])

    # A broker now says something different again. The member settled a
    # two-source disagreement; they never settled this one.
    plant(cur, USER_A, "908", "maturity_date", "2031-02-14",
          model.PROVENANCE_MEETING_DERIVED, value_type=model.VALUE_DATE)
    reopened = contradictions.detect_conflicts(
        cur, owner_user_id=USER_A, subject_id="908")
    check("a new competing source reopens the question", len(reopened) >= 1,
          str(reopened))
    if reopened:
        check("under a different conflict id, because it is a different question",
              reopened[0]["conflict_id"] != found[0]["conflict_id"])
        check("and it reads as unresolved", reopened[0]["unresolved"] is True)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_owner_isolation():
    """A foreign conflict is indistinguishable from one that never existed."""
    print("\n[owner isolation]")
    conn, cur = cursor()

    mine_a = plant(cur, USER_A, "909", "valuation", "100",
                   model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY)
    plant(cur, USER_A, "909", "valuation", "400",
          model.PROVENANCE_PROVIDER_ASSERTED, value_type=model.VALUE_MONEY)
    found = detect_and_mark(cur, USER_A, "909")
    if not found:
        check("A's valuations conflict", False, "no conflict detected")
        conn.commit()
        conn.close()
        return
    marker = found[0]["conflict_id"]

    stolen = contradictions.resolve_conflict(
        cur, owner_user_id=USER_B, conflict_id=marker, winning_fact_id=mine_a)
    check("B cannot settle A's conflict",
          stolen["status"] == contradictions.RESOLVE_NOT_FOUND, str(stolen))
    check("and is told nothing about whether it exists",
          stolen["reason"] == "no_such_conflict", stolen["reason"])
    check("A's rows are untouched",
          read_fact(cur, USER_A, mine_a)["verification_state"]
          == model.VERIFICATION_CONFLICTING)
    check("B sees no conflicts of their own",
          contradictions.detect_conflicts(cur, owner_user_id=USER_B) == [])
    check("and B's resolution lookup finds nothing",
          contradictions.load_resolutions(
              cur, owner_user_id=USER_B, conflict_ids=[marker]) == {})

    # A machine cannot flag another member's fact either.
    check("B cannot flag A's fact as contested",
          facts.flag_conflicting_fact(
              cur, owner_user_id=USER_B, fact_id=mine_a)["status"] == "not_found")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_settlements_carry_no_member_content():
    """The resolution table is a decision log, not a second copy of the facts."""
    print("\n[no content leak]")
    conn, cur = cursor()

    cur.execute(f"SELECT * FROM {schema.FACT_CONFLICTS_TABLE}")
    rows = [dict(r) for r in cur.fetchall()]
    check("settlements were recorded", len(rows) >= 1, str(len(rows)))

    # Every value planted above, as a string. None of it may appear in the
    # settlement table under any column.
    planted = ("2027-03-01", "2027-04-15", "2026-01-05", "2026-02-20", "8200",
               "12 Rue Test", "48 Avenue Other", "1850", "950000")
    leaked = [
        r for r in rows
        if any(secret in str(value)
               for value in r.values() if value is not None
               for secret in planted)
    ]
    check("no fact value appears anywhere in the settlement table",
          leaked == [], str(leaked[:1]))
    check("every settlement names a known resolution kind",
          all(r["resolution"] in contradictions.RESOLUTIONS for r in rows),
          str(sorted({r["resolution"] for r in rows})))
    check("every settlement names a known disposition",
          all(r["loser_disposition"] in contradictions.DISPOSITIONS for r in rows),
          str(sorted({r["loser_disposition"] for r in rows})))
    # The column that should only ever hold one value. It exists so the day it
    # holds two is visible.
    check("every settlement was made by the owner",
          {r["resolved_by_actor_type"] for r in rows} == {facts.ACTOR_OWNER},
          str(sorted({r["resolved_by_actor_type"] for r in rows})))

    # And the audit trail records that conflicts were settled without recording
    # what they were about.
    cur.execute(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE action = ?",
        (audit.ACTION_CONFLICT_RESOLVED,))
    trail = [dict(r) for r in cur.fetchall()]
    check("the audit trail records the settlements", len(trail) >= 1)
    leaked_audit = [
        r for r in trail
        if any(secret in str(value)
               for value in r.values() if value is not None
               for secret in planted)
    ]
    check("and carries no fact value either", leaked_audit == [],
          str(leaked_audit[:1]))
    conn.commit()
    conn.close()


def main() -> int:
    print("PRIVATE FACTS — CONFLICT LIFECYCLE (Batch 2)")
    print(f"database: {_TMP_DB}")
    # Order-dependent against one database, and named `stage_*` rather than
    # `test_*` for the same reason the substrate suite is: pytest must collect
    # one entry point, or every stage runs twice and the second pass asserts
    # "written" about rows the first pass already wrote.
    _FAILURES.clear()
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    schema.reset_schema_cache()
    stage_vocabulary_is_wired()
    stage_schema_carries_resolutions()
    stage_temporal_change_is_still_not_a_conflict()
    stage_a_real_disagreement_is_stamped()
    stage_a_members_dispute_survives_the_detector()
    stage_only_the_owner_may_settle()
    stage_settling_a_conflict()
    stage_retiring_a_loser_and_dismissing_a_conflict()
    stage_a_new_source_reopens_a_settled_question()
    stage_owner_isolation()
    stage_settlements_carry_no_member_content()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_conflict_lifecycle():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
