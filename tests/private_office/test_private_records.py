"""Batch C — the six canonical record primitives.

Run either way::

    python -m pytest tests/private_office/test_private_records.py
    python tests/private_office/test_private_records.py

What these tests are actually defending
---------------------------------------
The six primitives — OBLIGATION, DOMAIN EVENT, DECISION, REQUEST, RISK,
OPPORTUNITY — are shared domain objects, not six feature tables that happen to
sit next to each other. Almost every check below exists because there is a
plausible, tidy-looking implementation that would break one of these properties
without breaking anything visible.

* **Derived state is never stored.** ``DUE_SOON`` and ``OVERDUE`` are facts
  about the clock, not about the obligation. Storing them means a row that was
  true when a nightly job last ran and is wrong now, and a member reading
  "overdue" about a bill they paid. So the stored status vocabulary does not
  contain them at all, and the derived value is computed at read.

* **History survives revision.** A decision's question is not reachable from
  ``update_record``. The test proves both halves: that the narrow updater
  refuses it, and that a revision leaves the old question readable rather than
  overwriting it.

* **The absence of provider data is not good news.** A fresh risk is
  ``UNKNOWN`` severity and ``UNKNOWN`` coverage. There is no enum value meaning
  "fine", so no code path can drift into asserting safety it never checked.

* **An opportunity is not advice.** The primitive has no recommendation,
  rating or suggested-action column. The check is over the schema, because a
  column that does not exist cannot be populated by a later feature in a hurry.

* **Derived records remember where they came from.** A record whose source is a
  document, provider, fact, graph, inference or import must carry a provenance
  type. The writer refuses it otherwise.

* **Owner scope is structural.** Every read is tested directly across two
  accounts, for all six primitives, including the id-substitution attempt —
  which returns ``None`` rather than a refusal, so it is not an existence
  oracle either.

* **Retrieval is the only door.** The typed views expose primitives by name
  (``obligations``, ``risks``, …) and never a table name, and they run the same
  gates the graph retrieval runs.

* **The schema can be built twice.** ``ensure_records_schema`` is idempotent and
  its DDL is written in the one dialect ``services.db`` knows how to translate,
  which is checked here rather than discovered on a Postgres deploy.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_records_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import retrieval  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import telemetry  # noqa: E402

USER_A = 9701
USER_B = 9702

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    records.ensure_records_schema(cur)
    return conn, cur


def _iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def setup_environment() -> None:
    schema.reset_schema_cache()
    records.reset_records_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def stage_schema() -> None:
    print("\n[schema]")
    conn = db.connect()
    cur = conn.cursor()

    records.reset_records_schema_cache()
    first = records.ensure_records_schema(cur, force=True)
    second = records.ensure_records_schema(cur, force=True)
    conn.commit()

    check("ensure_records_schema reports ready", first.get("status") == "ready", str(first))
    check("ensure_records_schema is idempotent", second.get("status") == "ready", str(second))
    check("ensure_records_schema never raises and never returns None",
          isinstance(second, dict) and "status" in second)

    for record_type in records.RECORD_TYPES:
        table = records.private_table_for(record_type)
        present = set(db.get_table_columns(cur, table) or [])
        expected = set(records._column_names(records.SPECS[record_type])) | {"id"}
        check(f"{table} has every declared column",
              expected <= present, str(sorted(expected - present)))

    # The six tables are distinct objects. `private_domain_events` sharing a
    # table with `private_audit_events` would make the access log writable by
    # the feature that records life events.
    tables = [records.private_table_for(t) for t in records.RECORD_TYPES]
    check("the six primitives have six distinct tables", len(set(tables)) == 6, str(tables))
    check("the domain event table is not the audit table",
          records.private_table_for(records.TYPE_EVENT) != schema.AUDIT_TABLE)

    # Portability. `services.db` rewrites SQLite's autoincrement form and adds
    # IF NOT EXISTS to ADD COLUMN; anything outside that vocabulary would be a
    # deploy-time failure on Postgres and nothing at all in these tests.
    for record_type in records.RECORD_TYPES:
        ddl = records.table_ddl(record_type)
        check(f"{record_type} DDL uses the translatable primary key form",
              "INTEGER PRIMARY KEY AUTOINCREMENT" in ddl)
        check(f"{record_type} DDL creates only if absent",
              "CREATE TABLE IF NOT EXISTS" in ddl)
        for banned in ("INSERT OR IGNORE", "AUTO_INCREMENT", "SERIAL", "WITHOUT ROWID"):
            check(f"{record_type} DDL avoids {banned}", banned not in ddl.upper())

    # Owner scope is the first clause of every query this module issues, so an
    # index that does not lead with it cannot serve one.
    for record_type in records.RECORD_TYPES:
        for statement in records.index_ddl(record_type):
            after_on = statement.split(" ON ", 1)[1]
            columns = after_on[after_on.index("(") + 1:after_on.rindex(")")]
            first_column = columns.split(",")[0].strip()
            name = statement.split(" IF NOT EXISTS ", 1)[1].split(" ", 1)[0]
            check(f"index {name} leads with owner_user_id",
                  first_column == "owner_user_id", statement)

    conn.close()


# ---------------------------------------------------------------------------
# C1 — OBLIGATION
# ---------------------------------------------------------------------------

def stage_obligation() -> None:
    print("\n[C1 obligation]")
    conn, cur = _connect()

    result = records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title="Property tax", obligation_type="TAX", due_at=_iso_in(5),
        amount="4200", currency="usd", related_entity_ids=["382", "PROP:7"],
        domain="FINANCIAL")
    conn.commit()
    record = result["record"]

    check("an obligation is created", result["status"] == records.STATUS_CREATED, str(result["status"]))
    # Money is normalised through the same path the fact store uses, so an
    # amount is stored once in a canonical text form and once as a number the
    # database can compare. Both are asserted: keeping only the number loses
    # what the member wrote, keeping only the text makes every threshold query
    # a string comparison.
    check("money is stored in canonical text form",
          record["amount"] == "4200.0", repr(record["amount"]))
    check("money is also stored as a comparable number",
          record["amount_number"] == 4200.0, repr(record["amount_number"]))
    check("currency is normalised", record["currency"] == "USD", record["currency"])
    check("related entity refs are kept as a list",
          sorted(record["related_entity_ids"]) == ["382", "PROP:7"],
          str(record["related_entity_ids"]))
    check("the serialized record does not carry internal identity",
          "owner_user_id" not in record and "record_key" not in record,
          str(sorted(record.keys())))

    # The rule with teeth: DUE_SOON is derived, never stored.
    check("the stored status is OPEN", record["status"] == "OPEN", record["status"])
    check("the effective status is derived as DUE_SOON",
          record["effective_status"] == records.DERIVED_DUE_SOON,
          record["effective_status"])
    stored_statuses = records.SPECS[records.TYPE_OBLIGATION]["statuses"]
    check("DUE_SOON is not a storable status", records.DERIVED_DUE_SOON not in stored_statuses)
    check("OVERDUE is not a storable status", records.DERIVED_OVERDUE not in stored_statuses)

    overdue = records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title="Ground rent", obligation_type="TAX", due_at=_iso_in(-3))
    conn.commit()
    check("a past due date derives OVERDUE",
          overdue["record"]["effective_status"] == records.DERIVED_OVERDUE,
          overdue["record"]["effective_status"])

    # A resolved obligation with a past due date is resolved, not overdue.
    closed = records.update_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        record_id=overdue["record_id"], status="RESOLVED")
    conn.commit()
    check("closing an obligation stamps resolved_at",
          bool(closed["record"]["resolved_at"]), str(closed["record"]["resolved_at"]))
    check("a resolved obligation is not reported overdue",
          closed["record"]["effective_status"] == "RESOLVED",
          closed["record"]["effective_status"])

    repeat = records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title="Property tax", obligation_type="TAX", due_at=record["due_at"],
        amount="4200", currency="usd", domain="FINANCIAL")
    conn.commit()
    check("an identical create dedupes rather than duplicating",
          repeat["status"] == records.STATUS_EXISTING
          and repeat["record_id"] == result["record_id"],
          f"{repeat['status']} {repeat['record_id']} vs {result['record_id']}")

    conn.close()


# ---------------------------------------------------------------------------
# C2 — DOMAIN EVENT
# ---------------------------------------------------------------------------

def stage_domain_event() -> None:
    print("\n[C2 domain event]")
    conn, cur = _connect()

    result = records.create_record(
        cur, record_type=records.TYPE_EVENT, owner_user_id=USER_A,
        event_type="PROPERTY_PURCHASED", occurred_at=_iso_in(-400),
        title="Lake house purchase", related_document_ids=["DOC:11"])
    conn.commit()
    record = result["record"]

    check("a domain event is created", result["status"] == records.STATUS_CREATED)
    check("the event keeps its occurrence time", bool(record["occurred_at"]))
    check("document refs are normalised", record["related_document_ids"] == ["DOC:11"],
          str(record["related_document_ids"]))

    # No blob column anywhere. A `metadata_json` would start as context and end
    # as the easiest place in the platform to read a member's affairs from.
    columns = set(records._column_names(records.SPECS[records.TYPE_EVENT]))
    for banned in ("metadata", "metadata_json", "payload", "detail_json", "data", "blob"):
        check(f"the event table has no {banned} column", banned not in columns)

    # Domain events and audit events are separate stores with separate
    # vocabularies. Writing one must not put a row in the other.
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {schema.AUDIT_TABLE} "
        f"WHERE owner_user_id = ? AND action = ?",
        (USER_A, audit.ACTION_RECORD_CREATE))
    row = cur.fetchone()
    audited = int(row["n"] if hasattr(row, "keys") else row[0])
    check("creating a record leaves an access-log row", audited >= 1, str(audited))
    check("the record actions are declared in the closed audit vocabulary",
          all(a in audit.ACTIONS for a in (
              audit.ACTION_RECORD_CREATE, audit.ACTION_RECORD_UPDATE,
              audit.ACTION_RECORD_REVISE, audit.ACTION_RECORD_READ)))

    conn.close()


# ---------------------------------------------------------------------------
# C3 — DECISION
# ---------------------------------------------------------------------------

def stage_decision() -> None:
    print("\n[C3 decision]")
    conn, cur = _connect()

    first = records.create_record(
        cur, record_type=records.TYPE_DECISION, owner_user_id=USER_A,
        question="Refinance the lake house?", summary="Rates moved.",
        assumptions="Assumes the 2027 rate path holds.", deadline_at=_iso_in(30))
    conn.commit()
    check("a decision is created", first["status"] == records.STATUS_CREATED)
    check("a new decision is OPEN", first["record"]["status"] == "OPEN")

    # The narrow updater must refuse the substance of the record.
    rejected = ""
    try:
        records.update_record(
            cur, record_type=records.TYPE_DECISION, owner_user_id=USER_A,
            record_id=first["record_id"], question="Sell the lake house?")
    except records.PrivateRecordRejected as exc:
        rejected = str(exc)
    check("update_record refuses to rewrite the question", "question" in rejected, rejected)
    check("the rejection points at the right tool", "revise_record" in rejected, rejected)
    check("the question is not in the updatable set",
          "question" not in records.UPDATABLE, str(records.UPDATABLE))

    revised = records.revise_record(
        cur, record_type=records.TYPE_DECISION, owner_user_id=USER_A,
        record_id=first["record_id"], question="Refinance or sell the lake house?")
    conn.commit()

    check("a revision is a new record", revised["record_id"] != first["record_id"])
    check("the revision links back to what it superseded",
          revised["record"]["supersedes_id"] == first["record_id"],
          str(revised["record"]["supersedes_id"]))
    check("the revision number advances", revised["record"]["revision"] == 2,
          str(revised["record"]["revision"]))
    check("fields the revision did not mention are inherited",
          revised["record"]["assumptions"].startswith("Assumes the 2027"),
          revised["record"]["assumptions"])

    prior = records.get_record(cur, record_type=records.TYPE_DECISION,
                               owner_user_id=USER_A, record_id=first["record_id"])
    check("the superseded decision is still readable", prior is not None)
    check("the superseded decision keeps its original question",
          (prior or {}).get("question") == "Refinance the lake house?",
          str((prior or {}).get("question")))
    check("the superseded decision is marked superseded",
          (prior or {}).get("lifecycle_state") == records.LIFECYCLE_SUPERSEDED,
          str((prior or {}).get("lifecycle_state")))

    active = records.list_records(cur, record_type=records.TYPE_DECISION,
                                  owner_user_id=USER_A)
    questions = [r["question"] for r in active]
    check("the default listing shows only the current version",
          questions == ["Refinance or sell the lake house?"], str(questions))

    with_history = records.list_records(cur, record_type=records.TYPE_DECISION,
                                        owner_user_id=USER_A, include_superseded=True)
    check("history is reachable when asked for", len(with_history) == 2, str(len(with_history)))

    decided = records.update_record(
        cur, record_type=records.TYPE_DECISION, owner_user_id=USER_A,
        record_id=revised["record_id"], status="DECIDED", outcome="Refinanced.")
    conn.commit()
    check("deciding stamps decided_at", bool(decided["record"]["decided_at"]))
    check("the outcome is recorded", decided["record"]["outcome"] == "Refinanced.")

    conn.close()


# ---------------------------------------------------------------------------
# C4 — REQUEST
# ---------------------------------------------------------------------------

def stage_request() -> None:
    print("\n[C4 request]")
    conn, cur = _connect()

    result = records.create_record(
        cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
        title="Book the notary", category="LEGAL",
        description="Needs to be a French-speaking notary.",
        priority="HIGH", confidentiality="SENSITIVE", deadline_at=_iso_in(10))
    conn.commit()
    record = result["record"]

    check("a request is created", result["status"] == records.STATUS_CREATED)
    check("the long field is presented as a description",
          record.get("description", "").startswith("Needs to be"), str(record.get("description")))
    check("a request has no stray summary field", "summary" not in record,
          str(sorted(record.keys())))
    check("priority is kept", record["priority"] == "HIGH", record["priority"])
    check("confidentiality is kept", record["confidentiality"] == "SENSITIVE",
          record["confidentiality"])
    check("a new request is OPEN", record["status"] == "OPEN")

    # The enum vocabulary is closed, and an unknown value is refused rather than
    # coerced to the default. Coercion would be the friendlier failure and the
    # worse one: a request the caller marked URGENT_ESCALATED would be stored as
    # NORMAL and nobody would ever be told.
    rejected = ""
    try:
        records.create_record(
            cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
            title="Loose priority", category="LEGAL", priority="EXTREMELY_URGENT")
    except records.PrivateRecordRejected as exc:
        rejected = str(exc)
    check("an unknown priority is refused, not silently downgraded",
          "priority" in rejected, rejected)
    check("the rejection names the permitted vocabulary",
          all(value in rejected for value in records.PRIORITIES), rejected)

    for status in ("IN_PROGRESS", "WAITING_ON_USER", "WAITING_ON_PROVIDER"):
        moved = records.update_record(
            cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
            record_id=result["record_id"], status=status)
        check(f"a request can move to {status}", moved["record"]["status"] == status,
              moved["record"]["status"])
        check(f"{status} does not stamp completion", not moved["record"]["completed_at"],
              str(moved["record"]["completed_at"]))

    assigned = records.update_record(
        cur, record_type=records.TYPE_REQUEST, owner_user_id=USER_A,
        record_id=result["record_id"], assigned_provider_id="PROV:44",
        status="COMPLETED")
    conn.commit()
    check("a provider assignment is kept as an id",
          assigned["record"]["assigned_provider_id"] == "PROV:44",
          assigned["record"]["assigned_provider_id"])
    check("completion stamps completed_at", bool(assigned["record"]["completed_at"]))

    conn.close()


# ---------------------------------------------------------------------------
# C5 — RISK
# ---------------------------------------------------------------------------

def stage_risk() -> None:
    print("\n[C5 risk]")
    conn, cur = _connect()

    fresh = records.create_record(
        cur, record_type=records.TYPE_RISK, owner_user_id=USER_A,
        risk_type="UNDERINSURED", summary="No policy on file for the lake house.")
    conn.commit()
    record = fresh["record"]

    # The rule this stage exists for: silence is not safety.
    check("a risk with no provider data is UNKNOWN severity",
          record["severity"] == "UNKNOWN", record["severity"])
    check("a risk with no provider data has UNKNOWN coverage",
          record["coverage_state"] == "UNKNOWN", record["coverage_state"])
    for reassuring in ("SAFE", "OK", "FINE", "NONE", "CLEAR", "NO_RISK", "COVERED"):
        check(f"no severity value means {reassuring}", reassuring not in records.SEVERITIES)
        check(f"no coverage value means {reassuring}", reassuring not in records.COVERAGE_STATES)
    check("PROVIDER_REQUIRED is an expressible coverage state",
          "PROVIDER_REQUIRED" in records.COVERAGE_STATES, str(records.COVERAGE_STATES))

    escalated = records.update_record(
        cur, record_type=records.TYPE_RISK, owner_user_id=USER_A,
        record_id=fresh["record_id"], severity="HIGH",
        coverage_state="PROVIDER_REQUIRED", review_required=True)
    conn.commit()
    check("severity can be raised by a caller who knows something",
          escalated["record"]["severity"] == "HIGH", escalated["record"]["severity"])
    check("coverage can be marked as needing a provider",
          escalated["record"]["coverage_state"] == "PROVIDER_REQUIRED",
          escalated["record"]["coverage_state"])
    check("review_required is a flag, not a string",
          escalated["record"]["review_required"] is True,
          repr(escalated["record"]["review_required"]))

    # Provenance: a record derived from another artifact must say so.
    rejected = ""
    try:
        records.create_record(
            cur, record_type=records.TYPE_RISK, owner_user_id=USER_A,
            risk_type="UNDERINSURED", summary="Derived without an origin.",
            source_type=records.SOURCE_PROVIDER, source_ref="PROV:44")
    except records.PrivateRecordRejected as exc:
        rejected = str(exc)
    check("a derived risk without provenance is refused",
          "provenance" in rejected.lower(), rejected)

    derived = records.create_record(
        cur, record_type=records.TYPE_RISK, owner_user_id=USER_A,
        risk_type="UNDERINSURED", summary="Derived with an origin.",
        source_type=records.SOURCE_PROVIDER, source_ref="PROV:44",
        provenance_type="INFERRED")
    conn.commit()
    check("a derived risk with provenance is accepted",
          derived["status"] == records.STATUS_CREATED, str(derived["status"]))
    check("the origin is preserved on the row",
          derived["record"]["provenance_state"] == "INFERRED"
          and derived["record"]["source_type"] == records.SOURCE_PROVIDER,
          f"{derived['record']['provenance_state']} {derived['record']['source_type']}")

    check("every derived source demands provenance",
          all(s in records.DERIVED_SOURCES for s in (
              records.SOURCE_DOCUMENT, records.SOURCE_PROVIDER, records.SOURCE_FACT,
              records.SOURCE_GRAPH, records.SOURCE_INFERENCE, records.SOURCE_IMPORT)))
    check("a record the member typed does not demand provenance",
          records.SOURCE_USER not in records.DERIVED_SOURCES)

    conn.close()


# ---------------------------------------------------------------------------
# C6 — OPPORTUNITY
# ---------------------------------------------------------------------------

def stage_opportunity() -> None:
    print("\n[C6 opportunity]")
    conn, cur = _connect()

    result = records.create_record(
        cur, record_type=records.TYPE_OPPORTUNITY, owner_user_id=USER_A,
        title="Co-investment in a Lisbon fund", opportunity_type="INVESTMENT",
        summary="A named source flagged this as possibly relevant.",
        relevance_score=0.62, source_type=records.SOURCE_PROVIDER,
        source_ref="PROV:9", provenance_type="INFERRED")
    conn.commit()
    record = result["record"]

    check("an opportunity is created", result["status"] == records.STATUS_CREATED)
    check("a new opportunity is NEW", record["status"] == "NEW", record["status"])
    check("a relevance score from a named source is kept",
          record["relevance_score"] == 0.62, str(record["relevance_score"]))

    # The rule this stage exists for: recording that something exists is not
    # recommending it. A column that does not exist cannot be filled in later.
    columns = set(records._column_names(records.SPECS[records.TYPE_OPPORTUNITY]))
    for banned in ("recommendation", "recommended", "rating", "advice",
                   "suggested_action", "action", "verdict", "score_label",
                   "buy", "target_allocation", "expected_return"):
        check(f"an opportunity has no {banned} column", banned not in columns)
    for banned in ("RECOMMENDED", "BUY", "STRONG_BUY", "APPROVED", "ADVISED"):
        check(f"no opportunity status means {banned}",
              banned not in records.SPECS[records.TYPE_OPPORTUNITY]["statuses"])

    clamped = records.create_record(
        cur, record_type=records.TYPE_OPPORTUNITY, owner_user_id=USER_A,
        title="Out of range", opportunity_type="INVESTMENT", relevance_score=9.9)
    conn.commit()
    check("a relevance score is clamped into range",
          0.0 <= (clamped["record"]["relevance_score"] or 0.0) <= 1.0,
          str(clamped["record"]["relevance_score"]))

    passed = records.update_record(
        cur, record_type=records.TYPE_OPPORTUNITY, owner_user_id=USER_A,
        record_id=result["record_id"], status="PASSED")
    conn.commit()
    check("passing on an opportunity stamps closure", bool(passed["record"]["closed_at"]))

    conn.close()


# ---------------------------------------------------------------------------
# Account isolation — all six, directly
# ---------------------------------------------------------------------------

def stage_isolation() -> None:
    print("\n[account isolation]")
    conn, cur = _connect()

    owned: dict[str, int] = {}
    for record_type in records.RECORD_TYPES:
        fields = {
            records.TYPE_OBLIGATION: {"title": "A's obligation", "obligation_type": "TAX"},
            records.TYPE_EVENT: {"event_type": "A_EVENT", "title": "A's event"},
            records.TYPE_DECISION: {"question": "A's private question?"},
            records.TYPE_REQUEST: {"title": "A's request", "category": "LEGAL"},
            records.TYPE_RISK: {"risk_type": "A_RISK", "summary": "A's risk."},
            records.TYPE_OPPORTUNITY: {"title": "A's opportunity",
                                       "opportunity_type": "INVESTMENT"},
        }[record_type]
        created = records.create_record(
            cur, record_type=record_type, owner_user_id=USER_A, **fields)
        owned[record_type] = created["record_id"]
    conn.commit()

    for record_type, record_id in owned.items():
        # Direct id substitution. B asks for A's row by its real id.
        stolen = records.get_record(cur, record_type=record_type,
                                    owner_user_id=USER_B, record_id=record_id)
        check(f"B cannot read A's {record_type} by id", stolen is None, str(stolen))

        listed = records.list_records(cur, record_type=record_type, owner_user_id=USER_B)
        check(f"B's {record_type} listing is empty", listed == [], str(listed))

        check(f"B's open {record_type} count is zero",
              records.count_open(cur, record_type=record_type, owner_user_id=USER_B) == 0)

        # The refusal is indistinguishable from absence: B asking for a row that
        # never existed gets the same answer as B asking for A's.
        absent = records.get_record(cur, record_type=record_type,
                                    owner_user_id=USER_B, record_id=10_000_000)
        check(f"absent and forbidden are the same answer for {record_type}",
              absent == stolen, f"{absent!r} vs {stolen!r}")

        # A write aimed at A's row from B's session must not land.
        moved = records.update_record(
            cur, record_type=record_type, owner_user_id=USER_B, record_id=record_id,
            status=records.SPECS[record_type]["statuses"][-1])
        check(f"B cannot move A's {record_type}", moved.get("status") == "absent",
              str(moved.get("status")))

        still = records.get_record(cur, record_type=record_type,
                                   owner_user_id=USER_A, record_id=record_id)
        check(f"A's {record_type} is untouched after B's attempt",
              still is not None
              and still["status"] == records.SPECS[record_type]["default_status"],
              str(still and still["status"]))

        revised = records.revise_record(
            cur, record_type=record_type, owner_user_id=USER_B, record_id=record_id,
            title="B overwrote this")
        check(f"B cannot revise A's {record_type}", revised.get("status") == "absent",
              str(revised.get("status")))
    conn.commit()

    check("an ownerless read returns nothing",
          records.list_records(cur, record_type=records.TYPE_RISK, owner_user_id=0) == [])

    conn.close()


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def stage_retrieval() -> None:
    print("\n[retrieval]")
    conn, cur = _connect()

    # Two obligations, deliberately different in domain and sensitivity. The
    # catch-all intent is the *most* restricted one — a caller who has not said
    # what they are asking about has not earned a wider view — so the financial,
    # confidential one must be invisible to it and visible to the intent that
    # names the subject.
    records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title="Renew the parking permit", obligation_type="ADMIN",
        due_at=_iso_in(4), domain="GENERAL", sensitivity="INTERNAL")
    records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title="Retrievable obligation", obligation_type="TAX",
        due_at=_iso_in(4), domain="FINANCIAL", sensitivity="CONFIDENTIAL")
    conn.commit()

    check("every primitive has a named view",
          set(retrieval.RECORD_VIEWS.values()) == set(records.RECORD_TYPES),
          str(sorted(retrieval.RECORD_VIEWS.items())))

    # The views are named after primitives, never after tables. A view name that
    # leaked a table name would make the storage layout part of the API.
    tables = {records.private_table_for(t) for t in records.RECORD_TYPES}
    check("no view name is a table name", not (set(retrieval.RECORD_VIEWS) & tables),
          str(sorted(set(retrieval.RECORD_VIEWS) & tables)))

    ok = retrieval.retrieve_records(
        cur, owner_user_id=USER_A, view=retrieval.VIEW_OBLIGATIONS,
        intent=retrieval.INTENT_GENERAL, purpose="undx_context")
    conn.commit()
    check("an owner retrieving their own view is not denied", ok["denied"] == "", ok["denied"])
    check("the view returns records", ok["counts"]["returned"] >= 1, str(ok["counts"]))
    check("the payload names the view, not the table",
          ok["view"] == retrieval.VIEW_OBLIGATIONS and ok["view"] not in tables, ok["view"])
    check("the retrieved records carry derived status",
          all("effective_status" in r for r in ok["records"]))
    check("the retrieved records carry no internal identity",
          all("record_key" not in r and "owner_user_id" not in r for r in ok["records"]))

    titles = [r["title"] for r in ok["records"]]
    check("the catch-all intent does not reach confidential financial records",
          "Retrievable obligation" not in titles, str(titles))
    check("the catch-all intent still returns what it is entitled to",
          "Renew the parking permit" in titles, str(titles))

    scoped = retrieval.retrieve_records(
        cur, owner_user_id=USER_A, view=retrieval.VIEW_OBLIGATIONS,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO)
    conn.commit()
    scoped_titles = [r["title"] for r in scoped["records"]]
    check("an intent that names the subject reaches the financial record",
          "Retrievable obligation" in scoped_titles, str(scoped_titles))

    over_ceiling = retrieval.retrieve_records(
        cur, owner_user_id=USER_A, view=retrieval.VIEW_OBLIGATIONS,
        intent=retrieval.INTENT_GENERAL, sensitivity_ceiling="HIGHLY_SENSITIVE")
    conn.commit()
    check("a caller cannot raise the ceiling above their intent's",
          "Retrievable obligation" not in [r["title"] for r in over_ceiling["records"]],
          str([r["title"] for r in over_ceiling["records"]]))

    cross = retrieval.retrieve_records(
        cur, owner_user_id=USER_A, view=retrieval.VIEW_OBLIGATIONS,
        actor_user_id=USER_B)
    conn.commit()
    check("a non-owner actor is refused", cross["denied"] == retrieval.DENIED_NOT_OWNER,
          cross["denied"])
    check("a refused retrieval returns nothing", cross["records"] == [], str(cross["records"]))

    unknown_view = retrieval.retrieve_records(
        cur, owner_user_id=USER_A, view="private_obligations")
    conn.commit()
    check("a table name is not accepted as a view",
          unknown_view["denied"] == retrieval.DENIED_UNKNOWN_VIEW, unknown_view["denied"])

    unknown_intent = retrieval.retrieve_records(
        cur, owner_user_id=USER_A, view=retrieval.VIEW_OBLIGATIONS, intent="anything")
    conn.commit()
    check("an unknown intent is refused rather than defaulted",
          unknown_intent["denied"] == retrieval.DENIED_UNKNOWN_INTENT,
          unknown_intent["denied"])

    no_owner = retrieval.retrieve_records(
        cur, owner_user_id=0, view=retrieval.VIEW_OBLIGATIONS)
    check("an ownerless retrieval is refused",
          no_owner["denied"] == retrieval.DENIED_NO_OWNER, no_owner["denied"])

    b_view = retrieval.retrieve_records(
        cur, owner_user_id=USER_B, view=retrieval.VIEW_OBLIGATIONS)
    conn.commit()
    check("B's own view of their own obligations is empty and undenied",
          b_view["denied"] == "" and b_view["records"] == [],
          f"{b_view['denied']} {b_view['records']}")

    bounded = retrieval.retrieve_records(
        cur, owner_user_id=USER_A, view=retrieval.VIEW_OBLIGATIONS, limit=10_000)
    conn.commit()
    check("the view bound cannot be raised by a caller",
          len(bounded["records"]) <= retrieval.MAX_RECORDS, str(len(bounded["records"])))

    # `retrieve` itself is unchanged: records are not graph material, and folding
    # them in would have silently grown every existing caller's payload.
    graph = retrieval.retrieve(cur, owner_user_id=USER_A, intent=retrieval.INTENT_GENERAL)
    conn.commit()
    check("the graph retrieval payload did not grow a records key",
          "records" not in graph, str(sorted(graph.keys())))

    conn.close()


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

def stage_observability() -> None:
    print("\n[observability]")

    for event in (telemetry.EVENT_RECORD_WRITE, telemetry.EVENT_RECORD_CLOSED,
                  telemetry.EVENT_RECORDS_RETRIEVED):
        check(f"{event} is declared", event in telemetry.EVENTS)
        fields = set(telemetry.EVENTS[event])
        leaked = fields & set(telemetry.FORBIDDEN_FIELDS)
        check(f"{event} carries no forbidden field", not leaked, str(sorted(leaked)))
        for banned in ("title", "question", "description", "summary", "outcome_text",
                       "amount", "value", "record_id", "owner"):
            check(f"{event} does not carry {banned}", banned not in fields)

    problems = telemetry.spec_is_sound()
    check("the telemetry spec is internally sound after Batch C", not problems, str(problems))

    counts = set(telemetry.EVENTS[telemetry.EVENT_RECORDS_RETRIEVED])
    check("retrieval latency is measured", "latency_ms" in counts, str(sorted(counts)))
    check("retrieval volume is measured", "record_count" in counts, str(sorted(counts)))


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def test_private_records():
    setup_environment()
    stage_schema()
    stage_obligation()
    stage_domain_event()
    stage_decision()
    stage_request()
    stage_risk()
    stage_opportunity()
    stage_isolation()
    stage_retrieval()
    stage_observability()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    print("PRIVATE OFFICE RECORD PRIMITIVES — Batch C")
    print(f"db: {_TMP_DB}")
    setup_environment()
    stage_schema()
    stage_obligation()
    stage_domain_event()
    stage_decision()
    stage_request()
    stage_risk()
    stage_opportunity()
    stage_isolation()
    stage_retrieval()
    stage_observability()

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("PASS — every check held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
