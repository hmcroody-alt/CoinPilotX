"""Operations Slice 1 — lifecycle, derived time, attention and the overview.

Run either way::

    python -m pytest tests/private_office/test_private_operations.py
    python tests/private_office/test_private_operations.py

Store-level on purpose: nothing here imports Flask. The HTTP gates for this
slice — 401, 423, the unavailable state — live in
``test_operations_routes.py``, next to the other route checks, so this file can
run in any environment that can run Python and SQLite.

What these tests are actually defending
---------------------------------------
Every check below exists because there is a plausible implementation that would
break the property without breaking anything visible.

* **An illegal transition changes nothing.** Not "returns an error and also
  writes" — the row is re-read after every refusal, because a writer that
  rejects *and* mutates is worse than one that does neither, and the difference
  is invisible from the return value.

* **Terminal is not a state you tidy.** Turning a COMPLETED request into
  CANCELED looks like housekeeping and is a rewrite of how a matter ended. It
  is refused; reopening first is the honest path and it is audited as a reopen.

* **Reopening is intent, not data.** ``reopen=True`` is required, so a stale
  form value carrying an old status cannot discard a closure stamp.

* **Restating a status is not an update.** It returns ``unchanged`` and does not
  touch ``updated_at``, because "when did this last change" must not come to
  mean "when was this last mentioned" — that is how a recent-activity feed
  fills with rows that did not move.

* **A refusal leaves a trace.** The denied transition writes an audit row. It
  is the only evidence an attempt happened, since by definition it wrote no
  record row.

* **Derived time is type-specific.** A completed obligation past its due date is
  not overdue; an EVENT's ``occurred_at`` is always in the past and must never
  make a member's entire history look late; a risk has no deadline at all.

* **The strongest reason ranks, not the count of reasons.** Three mild signals
  must not outrank one severe one.

* **Counts come from SQL.** The attention list is capped at fifty; a summary
  that counted the list would be correct for small accounts and wrong for large
  ones — wrong exactly where it matters.

* **An unanswerable question is not zero.** ``private_opportunities`` has no
  expiry column, so ``expiring_opportunities`` reports UNSUPPORTED.

* **Reads are bounded.** The overview issues a fixed number of queries; it does
  not grow one per record.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_operations_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import operations as ops  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9801
USER_B = 9802

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


NOW = datetime.now(timezone.utc)


def iso(delta_days: float) -> str:
    return (NOW + timedelta(days=delta_days)).isoformat()


class _CountingCursor:
    """A cursor that remembers how many statements it was asked to run.

    Used only by the query-bound check. It forwards everything, so a test using
    it is exercising the real reader rather than a mock of it.
    """

    def __init__(self, inner):
        self._inner = inner
        self.executions = 0

    def execute(self, *args, **kwargs):
        self.executions += 1
        return self._inner.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


_CONN = None


def _cursor():
    return _CONN.cursor()


def create(kind: str, owner: int = USER_A, **fields):
    cur = _cursor()
    out = records.create_record(
        cur, record_type=kind, owner_user_id=owner, actor_user_id=owner,
        purpose="test", **fields)
    _CONN.commit()
    return out


def update(kind: str, record_id: int, owner: int = USER_A, **fields):
    cur = _cursor()
    out = records.update_record(
        cur, record_type=kind, owner_user_id=owner, record_id=record_id,
        actor_user_id=owner, purpose="test", **fields)
    _CONN.commit()
    return out


def fetch(kind: str, record_id: int, owner: int = USER_A):
    cur = _cursor()
    rows = records.list_records(
        cur, record_type=kind, owner_user_id=owner, limit=200)
    for row in rows:
        if int(row.get("id") or 0) == int(record_id):
            return row
    return None


def audit_rows(action: str, owner: int = USER_A) -> int:
    cur = _cursor()
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {schema.AUDIT_TABLE} "
        f"WHERE owner_user_id = ? AND action = ?",
        (owner, action),
    )
    row = cur.fetchone()
    return int(row["n"] if hasattr(row, "keys") else row[0])


def setup_environment():
    global _CONN
    _CONN = db.connect()
    cur = _CONN.cursor()
    schema.ensure_private_schema(cur, force=True)
    records.ensure_records_schema(cur, force=True)
    _CONN.commit()


# ---------------------------------------------------------------------------
def stage_transition_engine():
    print("\n[transition engine]")

    # The edge set is derived from the specs, not restated. If someone adds a
    # status to a spec, it arrives with a rule rather than without one.
    for kind in records.RECORD_TYPES:
        spec = records.SPECS[kind]
        derived = set(records.working_statuses(kind))
        expected = {s for s in spec["statuses"] if s not in spec["closing"]}
        check(f"{kind}: working statuses derive from the spec", derived == expected,
              f"{derived} != {expected}")

    obligation = create("OBLIGATION", title="Pay the levy",
                        obligation_type="PAYMENT", due_at=iso(60))
    oid = obligation["record_id"]

    moved = update("OBLIGATION", oid, status="RESOLVED")
    check("a legal close reports updated",
          moved.get("status") == records.STATUS_UPDATED, str(moved.get("status")))
    row = fetch("OBLIGATION", oid)
    check("closing stamps closed_at", bool(row.get("resolved_at") or row.get("closed_at")),
          str(row))

    # Terminal to terminal. The tidy-looking rewrite.
    before = fetch("OBLIGATION", oid)
    rejected = False
    try:
        update("OBLIGATION", oid, status="DISMISSED")
    except records.PrivateRecordRejected as exc:
        rejected = True
        check("terminal to terminal is refused with a reason",
              "reopen" in str(exc).lower(), str(exc))
    check("RESOLVED to DISMISSED is refused", rejected)
    after = fetch("OBLIGATION", oid)
    check("the refused transition mutated nothing", before == after,
          f"{before} != {after}")

    # Reopen without intent.
    denied_before = audit_rows(audit.ACTION_RECORD_TRANSITION_DENIED)
    rejected = False
    try:
        update("OBLIGATION", oid, status="OPEN")
    except records.PrivateRecordRejected:
        rejected = True
    check("reopening without reopen=True is refused", rejected)
    check("the refusal is audited",
          audit_rows(audit.ACTION_RECORD_TRANSITION_DENIED) == denied_before + 1)
    check("the refusal still mutated nothing", fetch("OBLIGATION", oid) == after)

    reopened = update("OBLIGATION", oid, status="OPEN", reopen=True)
    check("reopen=True is accepted",
          reopened.get("status") == records.STATUS_UPDATED, str(reopened))
    row = fetch("OBLIGATION", oid)
    check("reopening clears the closure stamp",
          not (row.get("resolved_at") or row.get("closed_at")), str(row))
    check("a reopen is audited as a reopen",
          audit_rows(audit.ACTION_RECORD_REOPEN) >= 1)

    # Reopen must land on the default status, not an arbitrary one.
    update("OBLIGATION", oid, status="RESOLVED")
    rejected = False
    try:
        update("OBLIGATION", oid, status="DISMISSED", reopen=True)
    except records.PrivateRecordRejected:
        rejected = True
    check("reopen cannot be used to pick a different terminal", rejected)
    update("OBLIGATION", oid, status="OPEN", reopen=True)

    # An unrecognised status must not be coerced to the default.
    rejected = False
    try:
        update("OBLIGATION", oid, status="RESOLVEDD")
    except records.PrivateRecordRejected:
        rejected = True
    check("a typo'd status is refused, not coerced", rejected)
    check("the typo left the record OPEN",
          (fetch("OBLIGATION", oid) or {}).get("status") == "OPEN")

    # Restating the current status.
    before = fetch("OBLIGATION", oid)
    same = update("OBLIGATION", oid, status="OPEN")
    check("restating the current status reports unchanged",
          same.get("status") == records.STATUS_UNCHANGED, str(same.get("status")))
    check("restating did not touch updated_at",
          (fetch("OBLIGATION", oid) or {}).get("updated_at") == before.get("updated_at"))

    # A request may not skip straight from a terminal to a working state either.
    req = create("REQUEST", title="Book the car", category="TRAVEL",
                 description="Airport, 6am.")
    rid = req["record_id"]
    update("REQUEST", rid, status="CANCELED")
    rejected = False
    try:
        update("REQUEST", rid, status="IN_PROGRESS", reopen=True)
    except records.PrivateRecordRejected:
        rejected = True
    check("a reopened request returns to OPEN, not mid-flight", rejected)
    check("REQUEST is not reopenable from COMPLETED",
          "COMPLETED" not in records.REOPENABLE[records.TYPE_REQUEST])


def stage_derived_time():
    print("\n[derived time is read-time and type-specific]")

    late = create("OBLIGATION", title="Late filing", obligation_type="FILING",
                  due_at=iso(-3))
    row = fetch("OBLIGATION", late["record_id"])
    check("a past due date derives OVERDUE",
          row.get("effective_status") == records.DERIVED_OVERDUE, str(row.get("effective_status")))
    check("the stored status is still OPEN", row.get("status") == "OPEN")

    update("OBLIGATION", late["record_id"], status="RESOLVED")
    row = fetch("OBLIGATION", late["record_id"])
    check("a completed obligation past due is NOT overdue",
          row.get("effective_status") == "RESOLVED", str(row.get("effective_status")))

    stale = create("REQUEST", title="Stale ask", category="ADMIN",
                   description="Ignored.", deadline_at=iso(-9))
    update("REQUEST", stale["record_id"], status="CANCELED")
    row = fetch("REQUEST", stale["record_id"])
    check("a canceled request past deadline is NOT overdue",
          row.get("effective_status") == "CANCELED", str(row.get("effective_status")))

    # Per-type "soon". A decision six days out is due soon; an obligation six
    # days out is too; a request six days out is not, because a request's
    # runway is measured in days rather than weeks.
    dec = create("DECISION", question="Refinance?", summary="Rates moved.",
                 deadline_at=iso(6))
    check("a decision 6 days out is DUE_SOON",
          (fetch("DECISION", dec["record_id"]) or {}).get("effective_status")
          == records.DERIVED_DUE_SOON)
    req = create("REQUEST", title="Order the parts", category="ADMIN",
                 description="Not urgent.", deadline_at=iso(6))
    check("a request 6 days out is not yet DUE_SOON",
          (fetch("REQUEST", req["record_id"]) or {}).get("effective_status") == "OPEN")
    req2 = create("REQUEST", title="Confirm the booking", category="TRAVEL",
                  description="Soon.", deadline_at=iso(2))
    check("a request 2 days out is DUE_SOON",
          (fetch("REQUEST", req2["record_id"]) or {}).get("effective_status")
          == records.DERIVED_DUE_SOON)

    # The types with no deadline. An EVENT's occurred_at is always in the past;
    # a naive rule marks the member's entire history overdue.
    ev = create("EVENT", event_type="MEETING", title="Met the accountant",
                occurred_at=iso(-400))
    check("a four-hundred-day-old event is not overdue",
          (fetch("EVENT", ev["record_id"]) or {}).get("effective_status") == "RECORDED")
    risk = create("RISK", title="Coverage gap", risk_type="INSURANCE",
                  summary="Policy lapsed.")
    check("a risk has no deadline and is never overdue",
          (fetch("RISK", risk["record_id"]) or {}).get("effective_status") == "OPEN")
    for kind in (records.TYPE_EVENT, records.TYPE_RISK, records.TYPE_OPPORTUNITY):
        check(f"{kind} declares why it has no deadline",
              kind in records.NO_DEADLINE_REASON)
        check(f"{kind} is absent from DEADLINE_FIELDS",
              kind not in records.DEADLINE_FIELDS)


def stage_attention_classifier():
    print("\n[attention classifier]")
    cur = _cursor()

    high = create("RISK", title="Uninsured exposure", risk_type="INSURANCE",
                  summary="No cover.", severity="CRITICAL")
    waiting = create("REQUEST", title="Send your passport", category="ADMIN",
                     description="Blocked on you.")
    update("REQUEST", waiting["record_id"], status="WAITING_ON_USER")
    provider = create("REQUEST", title="Awaiting the bank", category="FINANCE",
                      description="With them.")
    update("REQUEST", provider["record_id"], status="WAITING_ON_PROVIDER")

    queue = ops.attention(cur, owner_user_id=USER_A)
    by_id = {(i["record_type"], i["id"]): i for i in queue["items"]}

    item = by_id.get(("RISK", high["record_id"]))
    check("a critical risk is on the queue", item is not None)
    if item:
        check("its primary reason is HIGH_RISK",
              item["primary_reason"] == ops.REASON_HIGH_RISK, str(item["reasons"]))
    item = by_id.get(("REQUEST", waiting["record_id"]))
    check("WAITING_ON_USER reads as RESPONSE_REQUIRED",
          item is not None and ops.REASON_RESPONSE_REQUIRED in item["reasons"])
    item = by_id.get(("REQUEST", provider["record_id"]))
    check("WAITING_ON_PROVIDER reads as BLOCKED",
          item is not None and item["primary_reason"] == ops.REASON_BLOCKED)

    check("every queued item carries at least one reason",
          all(i["reasons"] for i in queue["items"]))
    check("every reason is a declared reason",
          all(set(i["reasons"]) <= ops.REASONS for i in queue["items"]))
    check("no closed record is on the queue",
          all(i["status"] not in records.SPECS[i["record_type"]]["closing"]
              for i in queue["items"]))

    # Strongest reason, not the sum. An overdue obligation with one reason must
    # outrank a decision carrying several mild ones.
    overdue = create("OBLIGATION", title="Overdue tax", obligation_type="FILING",
                     due_at=iso(-1), priority="LOW")
    mild = create("DECISION", question="Which colour?", summary="Low stakes.",
                  deadline_at=iso(400))
    queue = ops.attention(cur, owner_user_id=USER_A)
    order = [(i["record_type"], i["id"]) for i in queue["items"]]
    check("the overdue obligation outranks the merely-open decision",
          ("OBLIGATION", overdue["record_id"]) in order
          and (("DECISION", mild["record_id"]) not in order
               or order.index(("OBLIGATION", overdue["record_id"]))
               < order.index(("DECISION", mild["record_id"]))),
          str(order))
    check("OVERDUE is the strongest reason in the policy",
          ops.REASON_RANK[0] == ops.REASON_OVERDUE)
    check("BLOCKED is the weakest", ops.REASON_RANK[-1] == ops.REASON_BLOCKED)

    # Determinism. Two reads of unchanged data return the same order.
    again = ops.attention(cur, owner_user_id=USER_A)
    check("the order is stable across reads",
          [(i["record_type"], i["id"]) for i in again["items"]] == order)

    # Missing context comes from a field, not from an absence being read as one.
    gap = create("RISK", title="Unreviewed structure", risk_type="LEGAL",
                 summary="Needs counsel.", review_required=True)
    queue = ops.attention(cur, owner_user_id=USER_A)
    item = {(i["record_type"], i["id"]): i for i in queue["items"]}.get(
        ("RISK", gap["record_id"]))
    check("review_required reads as MISSING_REQUIRED_CONTEXT",
          item is not None and ops.REASON_MISSING_CONTEXT in item["reasons"],
          str(item))

    check("the unsupported reasons are declared, not omitted",
          "EXPIRING_OPPORTUNITY" in queue["unsupported_reasons"])

    # A closed record with a still-severe field. Severity does not decay when a
    # risk is resolved, so this is the case where "closed is closed" has to be
    # enforced by the classifier rather than falling out of the other guards:
    # every derived-time reason is already suppressed for closed rows, so a
    # resolved CRITICAL risk is the only thing that would climb back onto the
    # queue if the closing check were removed.
    settled = create("RISK", title="Old exposure", risk_type="INSURANCE",
                     summary="Since covered.", severity="CRITICAL")
    update("RISK", settled["record_id"], status="RESOLVED")
    queue = ops.attention(cur, owner_user_id=USER_A)
    check("a resolved critical risk is off the queue",
          ("RISK", settled["record_id"])
          not in {(i["record_type"], i["id"]) for i in queue["items"]})
    check("reasons_for finds nothing on a closed record",
          ops.reasons_for(fetch("RISK", settled["record_id"]),
                          record_type="RISK") == ())


def stage_bounds():
    print("\n[bounds]")
    cur = _cursor()
    # Sixty overdue obligations: more than MAX_ATTENTION_ITEMS, so the page and
    # the count must disagree in exactly one direction.
    for n in range(60):
        create("OBLIGATION", title=f"Bulk {n}", obligation_type="PAYMENT",
               due_at=iso(-2), owner=USER_B)
    # Fifty-five critical risks, also past the page cap. Counting the page
    # instead of the table would report fifty-or-fewer for these, right up until
    # a member has more than a page of severe exposures — the one moment the
    # number is worth printing. The summaries differ because a RISK's identity
    # is (risk_type, summary): fifty-five rows with one summary is one record,
    # by design, and the test would otherwise be asserting against a single row.
    for n in range(55):
        create("RISK", title=f"Exposure {n}", risk_type="INSURANCE",
               summary=f"Uncovered asset {n}.", severity="CRITICAL",
               owner=USER_B)
    queue = ops.attention(cur, owner_user_id=USER_B)
    check("the page is capped", len(queue["items"]) == ops.MAX_ATTENTION_ITEMS,
          str(len(queue["items"])))
    check("the total is not capped", queue["total"] > ops.MAX_ATTENTION_ITEMS,
          str(queue["total"]))
    check("truncation is declared", queue["truncated"] is True)

    over = ops.attention(cur, owner_user_id=USER_B, limit=10_000)
    check("an oversized limit is clamped, not honoured",
          len(over["items"]) == ops.MAX_ATTENTION_ITEMS, str(len(over["items"])))

    summary = ops.overview(cur, owner_user_id=USER_B)
    check("the overdue count is SQL, not the length of the page",
          summary["overdue"] >= 60, str(summary["overdue"]))
    check("the high-risk count is SQL, not the length of the page",
          summary["active_high_risks"] >= 55, str(summary["active_high_risks"]))
    check("needs_attention agrees with the uncapped total",
          summary["needs_attention"] == queue["total"],
          f"{summary['needs_attention']} != {queue['total']}")


def stage_counts_and_overview():
    print("\n[overview]")
    cur = _cursor()
    summary = ops.overview(cur, owner_user_id=USER_A)

    for field in ("as_of", "needs_attention", "due_today", "due_this_week",
                  "overdue", "pending_decisions", "open_requests",
                  "awaiting_response", "active_risks", "active_high_risks",
                  "active_opportunities", "recently_completed",
                  "expiring_opportunities", "recent_activity"):
        check(f"the overview answers {field}", field in summary)

    check("an unanswerable question is UNSUPPORTED, not zero",
          summary["expiring_opportunities"] == ops.UNSUPPORTED,
          str(summary["expiring_opportunities"]))
    check("the reason is stated",
          "expiry" in ops.UNSUPPORTED_REASONS["EXPIRING_OPPORTUNITY"])

    check("high risks are counted separately from all live risks",
          summary["active_high_risks"] <= summary["active_risks"],
          f"{summary['active_high_risks']} > {summary['active_risks']}")

    # A superseded revision must not be counted beside the row that replaced it.
    opp = create("OPPORTUNITY", title="Off-market building",
                 opportunity_type="PROPERTY")
    before = ops.overview(cur, owner_user_id=USER_A)["active_opportunities"]
    records.revise_record(
        cur, record_type="OPPORTUNITY", owner_user_id=USER_A,
        record_id=opp["record_id"], actor_user_id=USER_A, purpose="test",
        title="Off-market building, revised")
    _CONN.commit()
    after = ops.overview(cur, owner_user_id=USER_A)["active_opportunities"]
    check("revising does not inflate the active count", before == after,
          f"{before} -> {after}")

    # Filter widening. An unrecognised status must narrow to nothing, never
    # widen to everything.
    check("an unknown status filter counts zero, not all",
          records.count_records(cur, record_type="OBLIGATION",
                                owner_user_id=USER_A, statuses=("NOPE",)) == 0)
    check("an unknown status filter lists nothing, not everything",
          records.list_records(cur, record_type="OBLIGATION",
                               owner_user_id=USER_A, statuses=("NOPE",)) == [])

    # Activity comes from the audit ledger.
    activity = summary["recent_activity"]
    check("recent activity is a list", isinstance(activity, list))
    check("recent activity is bounded", len(activity) <= ops.MAX_RECENT_ACTIVITY,
          str(len(activity)))
    if activity:
        check("each entry names an audited action",
              all(e.get("action") in audit.RECORD_ACTIVITY_ACTIONS for e in activity),
              str({e.get("action") for e in activity}))
        check("no entry claims a target status",
              all("to_status" not in e and "new_status" not in e for e in activity))
    check("a reopen appears in the activity actions",
          audit.ACTION_RECORD_REOPEN in audit.RECORD_ACTIVITY_ACTIONS)


def stage_owner_isolation():
    print("\n[owner isolation]")
    cur = _cursor()

    a_queue = ops.attention(cur, owner_user_id=USER_A)
    b_queue = ops.attention(cur, owner_user_id=USER_B)
    a_ids = {(i["record_type"], i["id"]) for i in a_queue["items"]}
    b_ids = {(i["record_type"], i["id"]) for i in b_queue["items"]}
    check("A's queue and B's queue share no item", not (a_ids & b_ids),
          str(a_ids & b_ids))

    check("A's totals and B's totals differ", a_queue["total"] != b_queue["total"],
          f"{a_queue['total']} == {b_queue['total']}")

    for owner, other in ((USER_A, USER_B), (USER_B, USER_A)):
        summary = ops.overview(cur, owner_user_id=owner)
        theirs = ops.overview(cur, owner_user_id=other)
        check(f"{owner}'s overview is not {other}'s",
              summary["needs_attention"] != theirs["needs_attention"]
              or summary["overdue"] != theirs["overdue"])

    # A nonexistent owner gets an empty answer, not everyone's.
    empty = ops.overview(cur, owner_user_id=0)
    check("a zero owner reads nothing", empty.get("counts") == {}, str(empty))
    check("a zero owner's queue is empty",
          ops.attention(cur, owner_user_id=0)["items"] == [])
    ghost = ops.overview(cur, owner_user_id=999_999)
    check("an owner with no records sees zeros, not other members' rows",
          ghost["needs_attention"] == 0 and ghost["overdue"] == 0, str(ghost))

    # No serialized item carries an internal identifier.
    blob = repr(a_queue) + repr(ops.overview(cur, owner_user_id=USER_A))
    for leak in ("owner_user_id", "record_key"):
        check(f"{leak} never appears in the read model", leak not in blob)


def stage_query_bound():
    print("\n[query bound]")
    counting = _CountingCursor(_cursor())
    ops.overview(counting, owner_user_id=USER_A)
    first = counting.executions

    for n in range(40):
        create("OBLIGATION", title=f"Extra {n}", obligation_type="PAYMENT",
               due_at=iso(-1))

    counting = _CountingCursor(_cursor())
    ops.overview(counting, owner_user_id=USER_A)
    second = counting.executions
    check("the overview issues a fixed number of queries", first == second,
          f"{first} then {second} after 40 more records")
    check("that number is small", second < 60, str(second))


def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_transition_engine()
    stage_derived_time()
    stage_attention_classifier()
    stage_bounds()
    stage_counts_and_overview()
    stage_owner_isolation()
    stage_query_bound()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held.")
    return 0


def test_private_operations():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
