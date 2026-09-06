"""Batch 3 — evidence, demotion and the review queue, proved at runtime.

Hermetic: points ``services.db`` at a throwaway SQLite file and lets
``ensure_private_schema`` build everything it needs. Runs either way::

    python -m pytest tests/private_office/test_private_evidence_review.py
    python tests/private_office/test_private_evidence_review.py

What this suite is defending
----------------------------
Batches 1 and 2 built the verification axis and made a fact contestable. Two of
its states stayed unreachable: ``EVIDENCE_SUPPORTED``, which nothing could
produce, and ``NEEDS_REVIEW``, which nothing could fall into. A state that no
code path reaches is not a conservative default — it is a column value that
looks like a guarantee and is never tested, and the first feature to need it
will reach it by writing the string directly.

Each stage asserts one claim the modules make in prose:

* Evidence is the *only* route to ``EVIDENCE_SUPPORTED``, and a member's own
  say-so is not evidence. ``confirm_fact`` still stops at ``USER_CONFIRMED``.
* The citation is recorded even when it does not promote. Attaching a document
  to a disputed fact keeps the document and leaves the dispute standing —
  a fact must not be argued out of contention by whoever uploaded a file last.
* Detaching one of several citations changes nothing. Detaching the *last* one
  demotes to ``NEEDS_REVIEW``, because "we no longer know why we believed this"
  deserves a person.
* A detached citation is kept, not deleted. It is the only surviving
  explanation for a demotion.
* The staleness sweep flags without verifying: ``last_verified_at`` must not
  move, or a background job would make every fact permanently fresh.
* Staleness is a softer claim than expiry and must not read the same in a
  history — ``freshness_horizon``, never ``system_sweep``.
* The review queue surfaces stale facts the sweep has not reached yet. A queue
  built only from stored state shows nothing on the store that needs it most.
* Every new entry point is owner-isolated, and a foreign fact id is
  indistinguishable from one that was never issued.
* No citation, and no audit row about one, carries a fact's value.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_evidence_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import telemetry  # noqa: E402

# One owner per stage where the stage reads a *population* rather than a row.
# The sweep and the queue both scan every active fact an owner has, so sharing
# a user id between them would make each stage's assertions depend on what the
# previous stage happened to plant — the classic way an order-dependent suite
# starts passing for the wrong reason.
USER_A = 8201   # evidence mechanics
USER_B = 8202   # owner isolation
USER_C = 8203   # the staleness sweep
USER_D = 8204   # the review queue

STALE_OBSERVED = "2020-01-01T00:00:00+00:00"
FRESH_OBSERVED = "2026-08-20T00:00:00+00:00"

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
        "last_verified_at, observed_at "
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
        "last_verified_at": row["last_verified_at"],
        "observed_at": row["observed_at"],
    }


def plant(cur, owner: int, subject_id: str, fact_type: str, value: str,
          provenance: str, **kwargs) -> int:
    written = facts.record_fact(
        cur, owner_user_id=owner, subject_type="NODE", subject_id=subject_id,
        fact_type=fact_type, value=value,
        value_type=kwargs.pop("value_type", model.VALUE_STRING),
        provenance_type=provenance,
        observed_at=kwargs.pop("observed_at", FRESH_OBSERVED),
        valid_from=kwargs.pop("valid_from", FRESH_OBSERVED),
        **kwargs)
    return int(written["fact_id"])


def ops_for(cur, owner: int, fact_id: int) -> list[str]:
    return [
        str(h.get("operation") or "")
        for h in facts.list_fact_history(cur, owner_user_id=owner, fact_id=fact_id)
    ]


def reasons_for(cur, owner: int, fact_id: int) -> list[str]:
    return [
        str(h.get("reason_code") or "")
        for h in facts.list_fact_history(cur, owner_user_id=owner, fact_id=fact_id)
    ]


# ---------------------------------------------------------------------------
def stage_vocabulary_is_wired():
    """The words exist, are declared everywhere they must be, and agree."""
    print("\n[vocabulary]")

    check("the writer knows an attach operation",
          facts.OP_ATTACH_EVIDENCE in facts.VERIFICATION_TRANSITIONS)
    check("the writer knows a detach operation",
          facts.OP_DETACH_EVIDENCE in facts.VERIFICATION_TRANSITIONS)
    check("the writer knows a review-flag operation",
          facts.OP_FLAG_REVIEW in facts.VERIFICATION_TRANSITIONS)
    # Declared in the transition table is not enough. `_write_history` filters
    # on FACT_OPERATIONS and returns quietly when it misses, so an operation in
    # one and not the other mutates the fact and leaves no trace of having done
    # so — and a demoted fact whose history says only "created" is exactly the
    # row a member would most want explained.
    check("all three are declared history-writable",
          {facts.OP_ATTACH_EVIDENCE, facts.OP_DETACH_EVIDENCE,
           facts.OP_FLAG_REVIEW} <= set(facts.FACT_OPERATIONS))

    # The Batch 1 invariant, restated against the two operations this batch
    # adds. Evidence is the strongest thing short of reading a system of record,
    # and it still must not reach VERIFIED.
    reachable = {to_state for _from, to_state in facts.VERIFICATION_TRANSITIONS.values()}
    check("no operation can transition a fact into VERIFIED",
          model.VERIFICATION_VERIFIED not in reachable, str(sorted(reachable)))
    check("no operation can transition a fact into PROVIDER_VERIFIED",
          model.VERIFICATION_PROVIDER_VERIFIED not in reachable)
    check("attaching evidence lands on EVIDENCE_SUPPORTED",
          facts.VERIFICATION_TRANSITIONS[facts.OP_ATTACH_EVIDENCE][1]
          == model.VERIFICATION_EVIDENCE_SUPPORTED)
    check("and the two states this batch exists to reach are now reachable",
          {model.VERIFICATION_EVIDENCE_SUPPORTED,
           model.VERIFICATION_NEEDS_REVIEW} <= reachable, str(sorted(reachable)))

    attach_from = facts.VERIFICATION_TRANSITIONS[facts.OP_ATTACH_EVIDENCE][0]
    # The exclusion that stops a document from settling an argument nobody
    # settled.
    check("evidence may not clear a member's dispute",
          model.VERIFICATION_DISPUTED not in attach_from)
    check("nor a detected conflict",
          model.VERIFICATION_CONFLICTING not in attach_from)
    # And the exclusion that stops better sourcing from being punished:
    # EVIDENCE_SUPPORTED ranks at 70, so accepting a rank-90 or rank-100 row
    # here would let attaching a supporting document *downgrade* a fact.
    check("attaching to a VERIFIED fact cannot downgrade it",
          model.VERIFICATION_VERIFIED not in attach_from)
    check("nor to a PROVIDER_VERIFIED one",
          model.VERIFICATION_PROVIDER_VERIFIED not in attach_from)
    check("EVIDENCE_SUPPORTED does rank below the attested states",
          model.VERIFICATION_RANK[model.VERIFICATION_EVIDENCE_SUPPORTED]
          < model.VERIFICATION_RANK[model.VERIFICATION_VERIFIED])

    detach_from = facts.VERIFICATION_TRANSITIONS[facts.OP_DETACH_EVIDENCE][0]
    check("only a fact standing on its evidence has anything to lose",
          detach_from == frozenset({model.VERIFICATION_EVIDENCE_SUPPORTED}),
          str(sorted(detach_from)))
    check("and it falls to NEEDS_REVIEW, not quietly back to UNVERIFIED",
          facts.VERIFICATION_TRANSITIONS[facts.OP_DETACH_EVIDENCE][1]
          == model.VERIFICATION_NEEDS_REVIEW)
    # The reason that matters: NEEDS_REVIEW ranks at zero and is untrustworthy,
    # so the demotion is visible to every reader. UNVERIFIED would not be.
    check("NEEDS_REVIEW is an untrustworthy state, so readers can see it",
          model.VERIFICATION_NEEDS_REVIEW in model.UNTRUSTWORTHY_VERIFICATION)

    flag_from = facts.VERIFICATION_TRANSITIONS[facts.OP_FLAG_REVIEW][0]
    check("the sweep may not overwrite a member's dispute",
          model.VERIFICATION_DISPUTED not in flag_from)
    check("nor a detected conflict",
          model.VERIFICATION_CONFLICTING not in flag_from)
    # But age does apply to an attestation. A bank balance read six months ago
    # is still the bank's own answer and is no longer current.
    check("but a provider-verified fact still ages",
          model.VERIFICATION_PROVIDER_VERIFIED in flag_from)

    # None of the three is someone having looked. This is the assertion that
    # keeps the freshness horizon meaningful.
    check("attaching evidence is not someone having checked the fact",
          facts.OP_ATTACH_EVIDENCE not in facts.VERIFYING_OPERATIONS)
    check("neither is detaching it",
          facts.OP_DETACH_EVIDENCE not in facts.VERIFYING_OPERATIONS)
    check("and a sweep noticing a fact is old is certainly not",
          facts.OP_FLAG_REVIEW not in facts.VERIFYING_OPERATIONS)

    # Evidence kinds, and the subset a person can actually be shown.
    check("the evidence kinds are a closed set",
          model.normalize_evidence_type("nonsense") == "")
    check("and a known kind normalizes",
          model.normalize_evidence_type("document") == model.EVIDENCE_DOCUMENT)
    check("a statement is evidence but not resolvable",
          model.EVIDENCE_STATEMENT not in model.RESOLVABLE_EVIDENCE)
    check("a document is",
          model.EVIDENCE_DOCUMENT in model.RESOLVABLE_EVIDENCE)

    # Reason codes must survive normalisation or the history says nothing.
    check("the evidence reason codes are in the closed vocabulary",
          {"evidence_attached", "evidence_withdrawn"} <= set(facts.REASON_CODES))
    # Staleness is not expiry, and the two must not read the same in a history.
    check("staleness has its own reason code, distinct from expiry's",
          "freshness_horizon" in facts.REASON_CODES
          and "system_sweep" in facts.REASON_CODES)

    check("all three operations are declared to telemetry",
          {"attach_evidence", "detach_evidence", "flag_review"}
          <= telemetry.FACT_OPERATION_VOCAB)
    check("the evidence event is declared",
          telemetry.EVENT_EVIDENCE_LINKED in telemetry.EVENTS)
    check("the sweep event is declared",
          telemetry.EVENT_REVIEW_SWEEP in telemetry.EVENTS)
    check("the sweep reports both halves, not just what it flagged",
          {"scanned", "flagged"} <= set(telemetry.EVENTS[telemetry.EVENT_REVIEW_SWEEP]))
    check("the telemetry spec is still sound", telemetry.spec_is_sound() == [])

    check("both audit verbs are known",
          {audit.ACTION_FACT_EVIDENCE_ATTACH, audit.ACTION_FACT_EVIDENCE_DETACH,
           audit.ACTION_FACT_REVIEW_FLAG} <= set(audit.ACTIONS))

    # The queue's vocabulary is richer than the sweep's, and the reason it is
    # allowed to be is that the sweep only ever publishes one of them. If the
    # queue's extra values ever leaked into the telemetry enum they would become
    # new dimensions on a metric nobody asked for.
    check("the sweep publishes a reason the telemetry vocabulary knows",
          facts.REVIEW_STALE in telemetry.REVIEW_REASON_VOCAB)
    check("the queue's reasons are a closed set",
          len(set(facts.REVIEW_REASONS)) == len(facts.REVIEW_REASONS))
    check("and a superset of what the sweep may publish",
          telemetry.REVIEW_REASON_VOCAB <= set(facts.REVIEW_REASONS)
          or facts.REVIEW_STALE in facts.REVIEW_REASONS)


# ---------------------------------------------------------------------------
def stage_schema_carries_evidence():
    """The citation table exists, is required, and is idempotent."""
    print("\n[schema]")
    conn, cur = cursor()
    schema.reset_schema_cache()
    first = schema.ensure_private_schema(cur, force=True)
    check("ensure reports ready", first["status"] == schema.STATUS_READY,
          str(first.get("error") or first.get("missing")))
    check("the evidence table is part of the substrate",
          schema.FACT_EVIDENCE_TABLE in set(first["tables"]),
          str(sorted(first["tables"])))
    second = schema.ensure_private_schema(cur, force=True)
    check("a second ensure is still ready (idempotent)",
          second["status"] == schema.STATUS_READY)

    # `detached_at` is required, not merely present. A deployment whose evidence
    # table came up without it has no live predicate, which fails in the
    # dangerous direction: facts sitting at EVIDENCE_SUPPORTED on citations that
    # were withdrawn.
    required = set(schema.REQUIRED_COLUMNS[schema.FACT_EVIDENCE_TABLE])
    check("the live predicate column is required",
          "detached_at" in required, str(sorted(required)))
    check("and so is the kind of evidence",
          "evidence_type" in required)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_evidence_is_the_only_route_to_supported():
    """A member's say-so is not evidence, and evidence is not a member's say-so."""
    print("\n[promotion]")
    conn, cur = cursor()

    # Confirmation first, so the contrast is against the same fact type.
    said_so = plant(cur, USER_A, "1001", "policy_number", "AZ-4410",
                    model.PROVENANCE_USER_ASSERTED)
    facts.confirm_fact(cur, owner_user_id=USER_A, fact_id=said_so)
    check("clicking 'yes, that's right' lands on USER_CONFIRMED",
          read_fact(cur, USER_A, said_so)["verification_state"]
          == model.VERIFICATION_USER_CONFIRMED)
    check("and never on EVIDENCE_SUPPORTED",
          read_fact(cur, USER_A, said_so)["verification_state"]
          != model.VERIFICATION_EVIDENCE_SUPPORTED)

    backed = plant(cur, USER_A, "1002", "policy_start", "2026-01-05",
                   model.PROVENANCE_DOCUMENT_EXTRACTED, value_type=model.VALUE_DATE)
    result = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=backed,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="doc-77",
        locator="page=4;section=3.1")
    check("the citation was recorded",
          result["status"] == facts.EVIDENCE_ATTACHED, str(result))
    check("it promoted the fact", result["promoted"] is True, str(result))
    check("and the fact now stands on its evidence",
          read_fact(cur, USER_A, backed)["verification_state"]
          == model.VERIFICATION_EVIDENCE_SUPPORTED)
    check("one live citation", result["live_evidence"] == 1, str(result))
    check("the row got an id back despite lastrowid being unusable",
          int(result["evidence_id"]) > 0, str(result["evidence_id"]))

    # The promotion is not a verification. If it were, a document uploaded by a
    # background importer would reset the freshness clock on every fact it
    # touched.
    check("promotion did not stamp the verification clock",
          not str(read_fact(cur, USER_A, backed)["last_verified_at"] or ""),
          str(read_fact(cur, USER_A, backed)["last_verified_at"]))

    listed = facts.list_evidence(cur, owner_user_id=USER_A, fact_id=backed)
    check("the citation is listed", len(listed) == 1, str(len(listed)))
    if listed:
        check("with its locator, so a person can be shown the clause",
              listed[0]["locator"] == "page=4;section=3.1", str(listed[0]))
        check("and it reads as live", listed[0]["live"] is True)
        check("and as something a person could actually open",
              listed[0]["resolvable"] is True)

    check("the fact's history records the attachment",
          facts.OP_ATTACH_EVIDENCE in ops_for(cur, USER_A, backed),
          str(ops_for(cur, USER_A, backed)))
    check("with the reason that says evidence arrived",
          "evidence_attached" in reasons_for(cur, USER_A, backed),
          str(reasons_for(cur, USER_A, backed)))

    # An unrecognised kind is refused rather than defaulted. A default would let
    # a typo attach evidence of a type nobody chose and promote the fact anyway.
    raised = False
    try:
        facts.attach_evidence(
            cur, owner_user_id=USER_A, fact_id=backed,
            evidence_type="vibes", evidence_ref="doc-78")
    except facts.PrivateFactRejected:
        raised = True
    check("an unknown evidence kind is refused, not defaulted", raised)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_attaching_is_idempotent():
    """The same citation twice is one citation."""
    print("\n[idempotence]")
    conn, cur = cursor()

    fact_id = plant(cur, USER_A, "1003", "insurer_name", "Northgate Mutual",
                    model.PROVENANCE_DOCUMENT_EXTRACTED)
    first = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="doc-90")
    second = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="doc-90")
    check("the second attach is a no-op",
          second["status"] == facts.EVIDENCE_UNCHANGED, str(second))
    check("and reports no promotion, because nothing moved",
          second["promoted"] is False)
    check("it names the same citation row",
          second["evidence_id"] == first["evidence_id"])
    check("still exactly one live citation",
          len(facts.list_evidence(cur, owner_user_id=USER_A, fact_id=fact_id)) == 1)

    # Whitespace must not create a second row that the UNIQUE constraint then
    # rejects, so the natural key is normalised before the lookup, not after.
    padded = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="  doc-90  ")
    check("a padded reference is the same citation",
          padded["status"] == facts.EVIDENCE_UNCHANGED, str(padded))

    # Two pages of one document are two citations, not a duplicate.
    facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="doc-90",
        locator="page=11")
    check("a different locator is a different citation",
          len(facts.list_evidence(cur, owner_user_id=USER_A, fact_id=fact_id)) == 2)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_evidence_does_not_launder_a_dispute():
    """The citation lands; the objection stands."""
    print("\n[no laundering]")
    conn, cur = cursor()

    contested = plant(cur, USER_A, "1004", "estimated_value", "740000",
                      model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY)
    facts.dispute_fact(cur, owner_user_id=USER_A, fact_id=contested)
    check("the fact is disputed",
          read_fact(cur, USER_A, contested)["verification_state"]
          == model.VERIFICATION_DISPUTED)

    result = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=contested,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="survey-12")
    # Both halves matter, and they are the whole design of the ordering inside
    # `attach_evidence`: the link row is written first and the promotion is
    # attempted second, so a refused promotion cannot discard the evidence.
    check("the citation was still recorded",
          result["status"] == facts.EVIDENCE_ATTACHED, str(result))
    check("but it did not promote the fact", result["promoted"] is False,
          str(result))
    check("the dispute stands",
          read_fact(cur, USER_A, contested)["verification_state"]
          == model.VERIFICATION_DISPUTED)
    check("and the evidence is visible to whoever settles it",
          len(facts.list_evidence(cur, owner_user_id=USER_A, fact_id=contested)) == 1)

    # Same story for a machine-detected conflict.
    clashing = plant(cur, USER_A, "1005", "account_balance", "5000",
                     model.PROVENANCE_PROVIDER_ASSERTED, value_type=model.VALUE_MONEY)
    facts.flag_conflicting_fact(cur, owner_user_id=USER_A, fact_id=clashing)
    conflicted = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=clashing,
        evidence_type=model.EVIDENCE_RECORD, evidence_ref="stmt-3")
    check("evidence on a conflicting fact is kept",
          conflicted["status"] == facts.EVIDENCE_ATTACHED, str(conflicted))
    check("and does not resolve the conflict on its own",
          conflicted["promoted"] is False
          and read_fact(cur, USER_A, clashing)["verification_state"]
          == model.VERIFICATION_CONFLICTING)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_detaching_the_last_citation_demotes():
    """One of three is housekeeping. The last one is a demotion."""
    print("\n[detachment]")
    conn, cur = cursor()

    fact_id = plant(cur, USER_A, "1006", "lease_end", "2027-03-31",
                    model.PROVENANCE_DOCUMENT_EXTRACTED, value_type=model.VALUE_DATE)
    facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="lease-1")
    facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_MEETING, evidence_ref="mtg-4")
    check("the fact stands on two citations",
          read_fact(cur, USER_A, fact_id)["verification_state"]
          == model.VERIFICATION_EVIDENCE_SUPPORTED)

    partial = facts.detach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_MEETING, evidence_ref="mtg-4")
    check("dropping one citation is recorded",
          partial["status"] == facts.EVIDENCE_DETACHED, str(partial))
    check("and does not demote the fact", partial["demoted"] is False, str(partial))
    check("one citation remains", partial["live_evidence"] == 1, str(partial))
    check("the fact is still supported",
          read_fact(cur, USER_A, fact_id)["verification_state"]
          == model.VERIFICATION_EVIDENCE_SUPPORTED)

    final = facts.detach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="lease-1")
    check("dropping the last citation demotes", final["demoted"] is True, str(final))
    check("no citations remain", final["live_evidence"] == 0, str(final))
    check("and the fact is put in front of the member",
          read_fact(cur, USER_A, fact_id)["verification_state"]
          == model.VERIFICATION_NEEDS_REVIEW)

    # The soft delete. A detached citation is the only surviving explanation for
    # a demotion; hard-deleting it would leave the history saying the fact was
    # demoted and nothing saying why.
    check("no citation is visible by default",
          facts.list_evidence(cur, owner_user_id=USER_A, fact_id=fact_id) == [])
    withdrawn = facts.list_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id, include_detached=True)
    check("but the withdrawn citations are kept, not deleted",
          len(withdrawn) == 2, str(len(withdrawn)))
    check("and each is stamped with when it was withdrawn",
          all(str(item["detached_at"] or "") for item in withdrawn), str(withdrawn))
    check("and by whom, as a class",
          all(item["detached_by_actor_type"] == facts.ACTOR_OWNER for item in withdrawn))

    check("the history records the withdrawal",
          facts.OP_DETACH_EVIDENCE in ops_for(cur, USER_A, fact_id))
    check("with the reason that distinguishes it from time passing",
          "evidence_withdrawn" in reasons_for(cur, USER_A, fact_id),
          str(reasons_for(cur, USER_A, fact_id)))

    # Detaching twice must not re-run the zero-count check, or a double click
    # would demote a fact whose evidence went last week.
    again = facts.detach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="lease-1")
    check("detaching an already-detached citation is a no-op",
          again["status"] == facts.EVIDENCE_UNCHANGED, str(again))
    check("and demotes nothing a second time", again["demoted"] is False)

    # And the round trip: re-uploading the document must actually bring it back.
    # `INSERT OR IGNORE` would have reported success and done nothing here.
    revived = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="lease-1")
    check("re-attaching a withdrawn citation revives it",
          revived["status"] == facts.EVIDENCE_ATTACHED, str(revived))
    check("it is live again", revived["live_evidence"] == 1, str(revived))
    check("and it re-promotes the fact out of review",
          read_fact(cur, USER_A, fact_id)["verification_state"]
          == model.VERIFICATION_EVIDENCE_SUPPORTED)
    revived_rows = [
        item for item in facts.list_evidence(cur, owner_user_id=USER_A, fact_id=fact_id)
        if item["evidence_ref"] == "lease-1"
    ]
    check("and the withdrawal residue was cleared with it",
          bool(revived_rows)
          and not str(revived_rows[0]["detached_by_actor_type"] or ""),
          str(revived_rows[:1]))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_evidence_needs_a_live_fact():
    """A retired fact does not accrue support for a claim nobody is making."""
    print("\n[retired facts]")
    conn, cur = cursor()

    fact_id = plant(cur, USER_A, "1007", "former_address", "12 Rue Test",
                    model.PROVENANCE_USER_ASSERTED)
    facts.archive_fact(cur, owner_user_id=USER_A, fact_id=fact_id)
    check("the fact is archived",
          read_fact(cur, USER_A, fact_id)["lifecycle_state"]
          == model.LIFECYCLE_ARCHIVED)

    refused = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="doc-old")
    check("evidence cannot be attached to it",
          refused["status"] == facts.OUTCOME_REFUSED, str(refused))
    check("and the reason says why", refused["reason"] == "fact_not_active",
          str(refused))
    check("nothing was written",
          facts.list_evidence(cur, owner_user_id=USER_A, fact_id=fact_id,
                              include_detached=True) == [])

    missing = facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=999999,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="doc-none")
    check("a fact that was never issued reads as not found",
          missing["status"] == facts.OUTCOME_NOT_FOUND, str(missing))

    absent = facts.detach_evidence(
        cur, owner_user_id=USER_A, fact_id=fact_id,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="never-attached")
    check("detaching a citation that never existed reads as not found",
          absent["status"] == facts.OUTCOME_NOT_FOUND, str(absent))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_the_staleness_sweep():
    """Age flags a fact. It does not verify one, and it is not expiry."""
    print("\n[staleness sweep]")
    conn, cur = cursor()

    old = plant(cur, USER_C, "1101", "estimated_value", "620000",
                model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY,
                observed_at=STALE_OBSERVED, valid_from=STALE_OBSERVED)
    recent = plant(cur, USER_C, "1102", "policy_start", "2026-08-01",
                   model.PROVENANCE_DOCUMENT_EXTRACTED, value_type=model.VALUE_DATE,
                   observed_at=FRESH_OBSERVED, valid_from=FRESH_OBSERVED)
    contested = plant(cur, USER_C, "1103", "insurer_name", "Old Mutual",
                      model.PROVENANCE_USER_ASSERTED,
                      observed_at=STALE_OBSERVED, valid_from=STALE_OBSERVED)
    facts.dispute_fact(cur, owner_user_id=USER_C, fact_id=contested)

    result = facts.sweep_stale_facts(cur, owner_user_id=USER_C)
    # Both numbers are real counts. A sweep that reported {"scanned": 0} when it
    # could not read the table would be indistinguishable from a healthy one,
    # which is the incident this package was written out of.
    check("the sweep reports what it scanned",
          result["scanned"] >= 2, str(result))
    check("and what it flagged", result["flagged"] >= 1, str(result))
    check("the aged fact is now in review",
          read_fact(cur, USER_C, old)["verification_state"]
          == model.VERIFICATION_NEEDS_REVIEW)
    check("the recent one was left alone",
          read_fact(cur, USER_C, recent)["verification_state"]
          == model.VERIFICATION_UNVERIFIED,
          read_fact(cur, USER_C, recent)["verification_state"])
    # The member's own judgement outranks the sweep's vaguer observation, and
    # all three states rank at zero — so overwriting would be a strict loss of
    # information for no gain in caution.
    check("a disputed fact is not downgraded to 'somebody should look at this'",
          read_fact(cur, USER_C, contested)["verification_state"]
          == model.VERIFICATION_DISPUTED)

    # The assertion that keeps the horizon meaningful. A clock a background job
    # could refresh would make every fact permanently fresh.
    check("the sweep did not stamp the verification clock",
          not str(read_fact(cur, USER_C, old)["last_verified_at"] or ""),
          str(read_fact(cur, USER_C, old)["last_verified_at"]))
    # Nothing was retired. Staleness is the soft claim; the fact stays readable
    # and is merely presented with its observation date attached.
    check("and it retired nothing — the fact is still active",
          read_fact(cur, USER_C, old)["lifecycle_state"] == model.LIFECYCLE_ACTIVE)

    check("the history says the fact aged out, not that a window closed",
          "freshness_horizon" in reasons_for(cur, USER_C, old),
          str(reasons_for(cur, USER_C, old)))
    check("and expiry's reason code is nowhere near it",
          "system_sweep" not in reasons_for(cur, USER_C, old))
    check("the operation is recorded",
          facts.OP_FLAG_REVIEW in ops_for(cur, USER_C, old))

    # Idempotent: a nightly sweep must not re-flag what it already flagged.
    second = facts.sweep_stale_facts(cur, owner_user_id=USER_C)
    check("a second sweep flags nothing new", second["flagged"] == 0, str(second))
    check("but still reports having scanned", second["scanned"] >= 1, str(second))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_the_review_queue():
    """One list, several reasons, and nothing mutated by reading it."""
    print("\n[review queue]")
    conn, cur = cursor()

    stale = plant(cur, USER_D, "1201", "estimated_value", "500000",
                  model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY,
                  observed_at=STALE_OBSERVED, valid_from=STALE_OBSERVED)
    disputed = plant(cur, USER_D, "1202", "policy_number", "BX-1120",
                     model.PROVENANCE_PROVIDER_ASSERTED)
    facts.dispute_fact(cur, owner_user_id=USER_D, fact_id=disputed)
    clashing = plant(cur, USER_D, "1203", "account_balance", "1850",
                     model.PROVENANCE_PROVIDER_ASSERTED, value_type=model.VALUE_MONEY)
    facts.flag_conflicting_fact(cur, owner_user_id=USER_D, fact_id=clashing)
    unbacked = plant(cur, USER_D, "1204", "lease_end", "2028-06-30",
                     model.PROVENANCE_DOCUMENT_EXTRACTED, value_type=model.VALUE_DATE)
    facts.attach_evidence(
        cur, owner_user_id=USER_D, fact_id=unbacked,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="lease-9")
    facts.detach_evidence(
        cur, owner_user_id=USER_D, fact_id=unbacked,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="lease-9")
    healthy = plant(cur, USER_D, "1205", "insurer_name", "Northgate Mutual",
                    model.PROVENANCE_DOCUMENT_EXTRACTED)

    queue = facts.review_queue(cur, owner_user_id=USER_D)
    by_id = {int(row["id"]): row for row in queue}

    check("the aged fact is in the queue", stale in by_id, str(sorted(by_id)))
    check("the disputed one too", disputed in by_id)
    check("the conflicting one too", clashing in by_id)
    check("and the one whose evidence was withdrawn", unbacked in by_id)
    # The population a stored-state query would miss: a healthy, fresh fact has
    # no business in a review queue.
    check("a fresh, uncontested fact is not", healthy not in by_id,
          str(sorted(by_id)))

    # The reason is not decoration. "This document was withdrawn" and "nobody
    # has looked at this since 2020" call for completely different actions.
    check("the aged fact reads as stale",
          by_id.get(stale, {}).get("review_reason") == facts.REVIEW_STALE,
          str(by_id.get(stale, {}).get("review_reason")))
    check("the disputed one reads as disputed",
          by_id.get(disputed, {}).get("review_reason") == facts.REVIEW_DISPUTED)
    check("the conflicting one reads as conflicting",
          by_id.get(clashing, {}).get("review_reason") == facts.REVIEW_CONFLICTING)
    check("and the demoted one says its evidence was removed",
          by_id.get(unbacked, {}).get("review_reason") == facts.REVIEW_EVIDENCE_REMOVED,
          str(by_id.get(unbacked, {}).get("review_reason")))
    check("every reason comes from the closed set",
          all(row["review_reason"] in facts.REVIEW_REASONS for row in queue),
          str(sorted({row["review_reason"] for row in queue})))

    # The stale row has never been swept, and the queue must say so honestly
    # rather than pretending a background job found it.
    check("the aged fact is surfaced without having been flagged",
          by_id.get(stale, {}).get("flagged") is False,
          str(by_id.get(stale, {})))
    check("while the demoted one was genuinely flagged",
          by_id.get(unbacked, {}).get("flagged") is True)

    # Read-only. A queue whose contents depended on who opened it last would be
    # unusable as a review surface.
    before = read_fact(cur, USER_D, stale)
    facts.review_queue(cur, owner_user_id=USER_D)
    facts.review_queue(cur, owner_user_id=USER_D)
    after = read_fact(cur, USER_D, stale)
    check("reading the queue changes nothing", before == after,
          f"{before} != {after}")

    # And after a sweep the same row is still there, now for a stored reason.
    facts.sweep_stale_facts(cur, owner_user_id=USER_D)
    swept = {int(row["id"]): row for row in facts.review_queue(cur, owner_user_id=USER_D)}
    check("the aged fact survives the sweep that flags it", stale in swept)
    check("and is now marked as flagged",
          swept.get(stale, {}).get("flagged") is True, str(swept.get(stale, {})))
    check("the healthy fact still is not in the queue", healthy not in swept)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_owner_isolation():
    """A foreign citation is indistinguishable from one that never existed."""
    print("\n[owner isolation]")
    conn, cur = cursor()

    mine = plant(cur, USER_A, "1301", "valuation", "100",
                 model.PROVENANCE_USER_ASSERTED, value_type=model.VALUE_MONEY)
    facts.attach_evidence(
        cur, owner_user_id=USER_A, fact_id=mine,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="private-doc-1")

    stolen = facts.attach_evidence(
        cur, owner_user_id=USER_B, fact_id=mine,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="intruder")
    check("B cannot cite A's fact",
          stolen["status"] == facts.OUTCOME_NOT_FOUND, str(stolen))
    check("and is told nothing about whether it exists",
          stolen["reason"] == "no_such_fact", str(stolen["reason"]))

    removed = facts.detach_evidence(
        cur, owner_user_id=USER_B, fact_id=mine,
        evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="private-doc-1")
    check("B cannot withdraw A's citation",
          removed["status"] == facts.OUTCOME_NOT_FOUND, str(removed))
    check("B sees none of A's citations",
          facts.list_evidence(cur, owner_user_id=USER_B, fact_id=mine) == [])
    check("not even the withdrawn ones",
          facts.list_evidence(cur, owner_user_id=USER_B, fact_id=mine,
                              include_detached=True) == [])
    check("A's citation is untouched",
          len(facts.list_evidence(cur, owner_user_id=USER_A, fact_id=mine)) == 1)
    check("A's fact is untouched",
          read_fact(cur, USER_A, mine)["verification_state"]
          == model.VERIFICATION_EVIDENCE_SUPPORTED)

    # B's sweep and queue see only B's store, which is empty.
    check("B's sweep finds nothing of A's",
          facts.sweep_stale_facts(cur, owner_user_id=USER_B)
          == {"scanned": 0, "flagged": 0})
    check("and B's review queue is empty",
          facts.review_queue(cur, owner_user_id=USER_B) == [])

    # An owner id is required, not defaulted. A default of zero would make every
    # one of these calls a cross-owner query waiting to happen.
    for label, call in (
        ("list", lambda: facts.list_evidence(cur, owner_user_id=0, fact_id=mine)),
        ("attach", lambda: facts.attach_evidence(
            cur, owner_user_id=0, fact_id=mine,
            evidence_type=model.EVIDENCE_DOCUMENT, evidence_ref="x")),
        ("sweep", lambda: facts.sweep_stale_facts(cur, owner_user_id=0)),
        ("queue", lambda: facts.review_queue(cur, owner_user_id=0)),
    ):
        raised = False
        try:
            call()
        except facts.PrivateFactRejected:
            raised = True
        check(f"{label} refuses an ownerless call", raised)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
def stage_citations_carry_no_member_content():
    """The evidence table is a citation index, not a second copy of the facts."""
    print("\n[no content leak]")
    conn, cur = cursor()

    cur.execute(f"SELECT * FROM {schema.FACT_EVIDENCE_TABLE}")
    rows = [dict(r) for r in cur.fetchall()]
    check("citations were recorded", len(rows) >= 1, str(len(rows)))

    # Every value planted above, as a string. None of it may appear in the
    # citation table under any column — `evidence_ref` is an opaque identifier
    # resolved through the owning package's own reader, so that a citation never
    # becomes a way to learn the contents of a document you cannot open.
    planted = ("AZ-4410", "2026-01-05", "Northgate Mutual", "740000", "5000",
               "2027-03-31", "12 Rue Test", "620000", "Old Mutual", "500000",
               "BX-1120", "1850", "2028-06-30", "2026-08-01")
    leaked = [
        r for r in rows
        if any(secret in str(value)
               for value in r.values() if value is not None
               for secret in planted)
    ]
    check("no fact value appears anywhere in the citation table",
          leaked == [], str(leaked[:1]))
    check("every citation names a known evidence kind",
          all(r["evidence_type"] in model.EVIDENCE_TYPES for r in rows),
          str(sorted({r["evidence_type"] for r in rows})))
    check("every citation names a known actor class",
          all(r["attached_by_actor_type"] in facts.ACTOR_TYPES for r in rows),
          str(sorted({r["attached_by_actor_type"] for r in rows})))

    # The audit trail records that evidence moved without recording what it was
    # about.
    cur.execute(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE action IN (?, ?, ?)",
        (audit.ACTION_FACT_EVIDENCE_ATTACH, audit.ACTION_FACT_EVIDENCE_DETACH,
         audit.ACTION_FACT_REVIEW_FLAG),
    )
    trail = [dict(r) for r in cur.fetchall()]
    check("the audit trail records the attachments and the sweep",
          len(trail) >= 1, str(len(trail)))
    leaked_audit = [
        r for r in trail
        if any(secret in str(value)
               for value in r.values() if value is not None
               for secret in planted)
    ]
    check("and carries no fact value either", leaked_audit == [],
          str(leaked_audit[:1]))

    # The history table likewise. It carries operations and reasons, never
    # values — the previous value of a revised fact is the superseded row.
    cur.execute(f"SELECT * FROM {schema.FACT_HISTORY_TABLE}")
    history = [dict(r) for r in cur.fetchall()]
    leaked_history = [
        r for r in history
        if any(secret in str(value)
               for value in r.values() if value is not None
               for secret in planted)
    ]
    check("nor does the history table", leaked_history == [],
          str(leaked_history[:1]))
    conn.commit()
    conn.close()


def main() -> int:
    print("PRIVATE FACTS — EVIDENCE AND REVIEW (Batch 3)")
    print(f"database: {_TMP_DB}")
    # Order-dependent against one database, and named `stage_*` rather than
    # `test_*` for the same reason the other suites in this package are: pytest
    # must collect one entry point, or every stage runs twice and the second
    # pass asserts "attached" about citations the first pass already wrote.
    _FAILURES.clear()
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    schema.reset_schema_cache()
    stage_vocabulary_is_wired()
    stage_schema_carries_evidence()
    stage_evidence_is_the_only_route_to_supported()
    stage_attaching_is_idempotent()
    stage_evidence_does_not_launder_a_dispute()
    stage_detaching_the_last_citation_demotes()
    stage_evidence_needs_a_live_fact()
    stage_the_staleness_sweep()
    stage_the_review_queue()
    stage_owner_isolation()
    stage_citations_carry_no_member_content()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_evidence_review():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
