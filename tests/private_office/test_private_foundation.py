"""Shared foundation — evidence references and the private job record.

Run either way::

    python -m pytest tests/private_office/test_private_foundation.py
    python tests/private_office/test_private_foundation.py

What these tests defend
-----------------------
* **A ref is identity, never content.** The parser accepts ``fact:382`` and
  nothing value-shaped; a policy number cannot ride into a citation column.
* **Citations are verifiable or they are nothing.** An unknown kind parses to
  ``None`` rather than passing through, and resolution answers ``exists=False``
  identically for another member's row and for no row at all — the Stage 14
  non-leak shape, applied to evidence.
* **The vocabulary is typed once.** The table each record-kind resolves
  against is asserted equal to what ``records.SPECS`` declares, so the two
  modules cannot quietly disagree.
* **A job row is bookkeeping, not a payload.** Notes are truncated and
  flattened, subject/result refs go through the evidence parser, and the
  status walk is owner-scoped — one member cannot finish another's job.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_foundation_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts as facts_mod  # noqa: E402
from services.private_office import jobs  # noqa: E402
from services.private_office import model  # noqa: E402
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


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    records.ensure_records_schema(cur)
    jobs.ensure_jobs_schema(cur)
    return conn, cur


def setup_environment() -> None:
    schema.reset_schema_cache()
    records.reset_records_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Evidence refs
# ---------------------------------------------------------------------------

def stage_ref_grammar() -> None:
    print("\n[evidence: grammar]")
    check("format_ref produces kind:id", evidence.format_ref("fact", 382) == "fact:382")
    check("format_ref refuses unknown kinds", evidence.format_ref("password", 1) == "")
    check("format_ref refuses zero and negative ids",
          evidence.format_ref("fact", 0) == "" and evidence.format_ref("fact", -3) == "")
    check("parse_ref round-trips", evidence.parse_ref("obligation:12") == ("obligation", 12))
    check("parse_ref refuses unknown kinds", evidence.parse_ref("secret:12") is None)
    check("parse_ref refuses value-shaped text",
          all(evidence.parse_ref(v) is None for v in
              ("fact:12; DROP TABLE", "fact:0", "fact:", ":12", "4200.00", "AB-1234-K", "")))
    check("parse_ref refuses leading zeros (no aliasing two spellings of one id)",
          evidence.parse_ref("fact:007") is None)

    refs = evidence.normalize_refs(["fact:1", "fact:1", "FACT:2", "junk", "node:3"])
    check("normalize dedupes, lowercases and drops junk", refs == ["fact:1", "fact:2", "node:3"], str(refs))
    flood = evidence.normalize_refs([f"fact:{i}" for i in range(1, 100)])
    check("normalize caps the list", len(flood) == evidence.MAX_REFS, str(len(flood)))

    packed = evidence.pack_refs(["fact:1", "document:2"])
    check("pack/unpack round-trips", evidence.unpack_refs(packed) == ["fact:1", "document:2"], packed)
    check("empty packs to empty string", evidence.pack_refs([]) == "")
    check("malformed storage reads as no evidence, never raises",
          evidence.unpack_refs("{not json") == [] and evidence.unpack_refs('{"a":1}') == []
          and evidence.unpack_refs('["secret:9", 4]') == [])


def stage_ref_vocabulary_agreement() -> None:
    print("\n[evidence: vocabulary typed once]")
    expected = {
        "obligation": records.TYPE_OBLIGATION,
        "event": records.TYPE_EVENT,
        "decision": records.TYPE_DECISION,
        "request": records.TYPE_REQUEST,
        "risk": records.TYPE_RISK,
        "opportunity": records.TYPE_OPPORTUNITY,
    }
    for kind, record_type in expected.items():
        check(f"evidence '{kind}' resolves against the records table",
              evidence.KINDS[kind][0] == records.private_table_for(record_type),
              f"{evidence.KINDS[kind][0]} != {records.private_table_for(record_type)}")
    check("evidence 'fact' resolves against the facts table",
          evidence.KINDS["fact"][0] == schema.FACTS_TABLE)
    check("evidence 'node' resolves against the nodes table",
          evidence.KINDS["node"][0] == schema.NODES_TABLE)
    check("evidence 'edge' resolves against the edges table",
          evidence.KINDS["edge"][0] == schema.EDGES_TABLE)
    check("no evidence kind resolves against the audit table",
          all(table != schema.AUDIT_TABLE for table, _ in evidence.KINDS.values()))


def stage_ref_resolution() -> None:
    print("\n[evidence: owner-checked resolution]")
    conn, cur = _connect()

    outcome = facts_mod.record_fact(
        cur,
        owner_user_id=USER_A,
        subject_type="OWNER",
        subject_id=str(USER_A),
        fact_type="preferred_airline",
        value_type=model.VALUE_STRING,
        value="stated in test",
        provenance_type=model.PROVENANCE_USER_ASSERTED,
    )
    fact_id = int(outcome["fact_id"])
    conn.commit()

    ref = evidence.format_ref("fact", fact_id)
    own = evidence.resolve_refs(cur, USER_A, [ref])
    check("owner resolves own fact", own and own[0]["exists"] is True, str(own))
    check("resolution label is the type, not the value",
          own and own[0]["label"] == "preferred_airline", str(own))

    other = evidence.resolve_refs(cur, USER_B, [ref])
    missing = evidence.resolve_refs(cur, USER_A, ["fact:999999"])
    check("another member's row resolves exists=False", other and other[0]["exists"] is False)
    check("a missing row resolves exists=False", missing and missing[0]["exists"] is False)
    check("cross-owner and missing are indistinguishable (no existence oracle)",
          other and missing and
          {k: other[0][k] for k in ("exists", "label")} ==
          {k: missing[0][k] for k in ("exists", "label")})

    check("owner 0 resolves nothing",
          all(not e["exists"] for e in evidence.resolve_refs(cur, 0, [ref])))

    # `briefing` names a table that does not exist yet in this database. The
    # citation is unverifiable, and unverifiable must read as unverified.
    ghost = evidence.resolve_refs(cur, USER_A, ["briefing:1"])
    check("a kind whose table is absent resolves exists=False, never raises",
          ghost and ghost[0]["exists"] is False, str(ghost))

    conn.close()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def stage_jobs_schema() -> None:
    print("\n[jobs: schema]")
    ddl = jobs.JOBS_TABLE_DDL
    check("jobs DDL uses the translatable primary key form",
          "INTEGER PRIMARY KEY AUTOINCREMENT" in ddl)
    check("jobs DDL creates only if absent", "CREATE TABLE IF NOT EXISTS" in ddl)
    for banned in ("INSERT OR IGNORE", "AUTO_INCREMENT", "SERIAL", "WITHOUT ROWID"):
        check(f"jobs DDL avoids {banned}", banned not in ddl.upper())
    check("jobs table has no payload or detail column",
          all(word not in ddl.lower() for word in ("payload", "detail", "json", "content")))
    for statement in jobs.JOBS_INDEX_DDL:
        after_on = statement.split(" ON ", 1)[1]
        columns = after_on[after_on.index("(") + 1:after_on.rindex(")")]
        check("jobs index leads with owner_user_id",
              columns.split(",")[0].strip() == "owner_user_id", statement)

    conn, cur = _connect()
    jobs.ensure_jobs_schema(cur)  # second run must be a no-op, not an error
    conn.commit()
    conn.close()
    check("ensure_jobs_schema is idempotent", True)


def stage_jobs_lifecycle() -> None:
    print("\n[jobs: lifecycle and owner scope]")
    conn, cur = _connect()

    try:
        jobs.create_job(cur, owner_user_id=USER_A, job_type="mine_bitcoin")
        check("unknown job types are refused", False)
    except jobs.PrivateJobRejected:
        check("unknown job types are refused", True)
    try:
        jobs.create_job(cur, owner_user_id=0, job_type=jobs.JOB_SHIELD_SCAN)
        check("ownerless jobs are refused", False)
    except jobs.PrivateJobRejected:
        check("ownerless jobs are refused", True)

    job_id = jobs.create_job(
        cur, owner_user_id=USER_A, job_type=jobs.JOB_DOCUMENT_EXTRACTION,
        subject_ref="document:1",
    )
    conn.commit()
    row = jobs.get_job(cur, owner_user_id=USER_A, job_id=job_id)
    check("a created job is QUEUED with its subject ref",
          row and row["status"] == jobs.STATUS_QUEUED and row["subject_ref"] == "document:1",
          str(row))
    check("another member cannot read the job",
          jobs.get_job(cur, owner_user_id=USER_B, job_id=job_id) is None)
    check("another member cannot finish the job",
          jobs.finish_job(cur, owner_user_id=USER_B, job_id=job_id) is False)

    check("start moves QUEUED to RUNNING",
          jobs.start_job(cur, owner_user_id=USER_A, job_id=job_id) is True)
    check("start does not apply twice",
          jobs.start_job(cur, owner_user_id=USER_A, job_id=job_id) is False)

    long_note = "line one\nline two  " + "x" * 500
    check("finish records result ref and a flattened, truncated note",
          jobs.finish_job(cur, owner_user_id=USER_A, job_id=job_id,
                          result_ref="fact:12", outcome_note=long_note) is True)
    conn.commit()
    row = jobs.get_job(cur, owner_user_id=USER_A, job_id=job_id)
    check("finished job is SUCCEEDED with timestamps",
          row and row["status"] == jobs.STATUS_SUCCEEDED and row["finished_at"], str(row))
    check("note is single-line and capped",
          row and "\n" not in row["outcome_note"] and len(row["outcome_note"]) <= 200)
    check("result ref survived the parser", row and row["result_ref"] == "fact:12")
    check("a finished job cannot fail afterwards",
          jobs.fail_job(cur, owner_user_id=USER_A, job_id=job_id) is False)

    failed_id = jobs.create_job(cur, owner_user_id=USER_A, job_type=jobs.JOB_SHIELD_SCAN)
    jobs.start_job(cur, owner_user_id=USER_A, job_id=failed_id)
    check("a running job can fail",
          jobs.fail_job(cur, owner_user_id=USER_A, job_id=failed_id,
                        outcome_note="provider timeout") is True)
    conn.commit()

    listed = jobs.list_jobs(cur, owner_user_id=USER_A)
    check("listing returns the member's jobs newest first",
          [j["id"] for j in listed][:2] == [failed_id, job_id], str(listed))
    check("listing filters by type",
          all(j["job_type"] == jobs.JOB_SHIELD_SCAN for j in
              jobs.list_jobs(cur, owner_user_id=USER_A, job_type=jobs.JOB_SHIELD_SCAN)))
    check("an unknown type filter lists nothing",
          jobs.list_jobs(cur, owner_user_id=USER_A, job_type="mystery") == [])
    check("the other member sees no jobs", jobs.list_jobs(cur, owner_user_id=USER_B) == [])

    bad_ref_id = jobs.create_job(cur, owner_user_id=USER_A, job_type=jobs.JOB_SHIELD_SCAN,
                                 subject_ref="secret:99")
    conn.commit()
    row = jobs.get_job(cur, owner_user_id=USER_A, job_id=bad_ref_id)
    check("an unparseable subject ref is stored as empty, not as text",
          row and row["subject_ref"] == "", str(row))

    conn.close()


# ---------------------------------------------------------------------------
# Audit vocabulary
# ---------------------------------------------------------------------------

def stage_audit_vocabulary() -> None:
    print("\n[audit: capability vocabulary]")
    for action in (
        audit.ACTION_DOCUMENT_CREATE, audit.ACTION_DOCUMENT_READ,
        audit.ACTION_DOCUMENT_DELETE, audit.ACTION_CLAIM_REVIEWED,
        audit.ACTION_BRIEFING_GENERATED, audit.ACTION_BRIEFING_READ,
        audit.ACTION_SHIELD_SCAN, audit.ACTION_SHIELD_READ,
        audit.ACTION_SHIELD_FINDING_UPDATE, audit.ACTION_CONCIERGE_MESSAGE,
    ):
        check(f"{action} is in the closed set", action in audit.ACTIONS)
    for purpose in ("document_processing", "shield_monitoring", "concierge_service"):
        check(f"purpose '{purpose}' is recognized",
              audit.normalize_purpose(purpose) == purpose)

    conn, cur = _connect()
    landed = audit.record(
        cur, actor_user_id=USER_A, owner_user_id=USER_A,
        action=audit.ACTION_SHIELD_SCAN, object_type="SHIELD_SCAN",
        object_id="job:1", purpose="shield_monitoring",
    )
    conn.commit()
    check("a capability action writes an audit row", landed is True)
    conn.close()


def test_everything() -> None:
    setup_environment()
    stage_ref_grammar()
    stage_ref_vocabulary_agreement()
    stage_ref_resolution()
    stage_jobs_schema()
    stage_jobs_lifecycle()
    stage_audit_vocabulary()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    print("PRIVATE OFFICE SHARED FOUNDATION — evidence refs and jobs")
    print(f"db: {_TMP_DB}")
    setup_environment()
    stage_ref_grammar()
    stage_ref_vocabulary_agreement()
    stage_ref_resolution()
    stage_jobs_schema()
    stage_jobs_lifecycle()
    stage_audit_vocabulary()

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
