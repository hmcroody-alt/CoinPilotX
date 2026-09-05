"""Batch D — the structured record store's schema.

Run either way::

    python -m pytest tests/private_office/test_structured_record_schema.py
    python tests/private_office/test_structured_record_schema.py

What these tests are actually defending
---------------------------------------
This suite runs before there is a writer, which is the point: almost every
protection the structured record store will offer is a property of the *shape*
of these three tables, and a shape is far cheaper to get right now than after
ten thousand rows are in it.

* **There is no blob.** The obvious implementation of "a record with arbitrary
  fields" is one ``payload_json`` column, and it makes field-level privacy,
  indexed search, expiry sweeps and migration all impossible at once. The check
  is over the DDL, because a column that does not exist cannot be filled in by
  a later feature in a hurry.

* **A RESTRICTED value has somewhere else to go.** ``value_text`` is for values
  at CONFIDENTIAL and below; ``cipher_text`` and ``cipher_key_id`` exist so the
  writer has a correct option, and ``is_encrypted`` exists so a row that claims
  protection it does not have is findable.

* **History remembers paths, not values.** The revision table has no
  ``value_before``. An undo log of a passport number is a second copy of the
  passport number in a table with none of the masking, none of the encryption
  and none of the step-up gating that protects the first one.

* **A screen and an index read different columns from the value.**
  ``masked_text`` and ``search_text`` are stored beside ``value_text`` rather
  than derived at read time, so a list query never has to load a secret in
  order to avoid showing it.

* **Verification is never earned by machine.** The three verified states are
  absent from ``MACHINE_WRITABLE_VERIFICATION``, so an extractor that
  "recognised the document with high confidence" still cannot write
  ``USER_VERIFIED``. Confidence is not the same thing as somebody having looked.

* **Owner scope is structural.** Every table carries ``owner_user_id``, every
  uniqueness constraint is owner-scoped, and every index leads with it — so a
  query that forgot the owner clause is visibly slow rather than quietly
  correct-looking, and a record key chosen by account A cannot collide with
  account B's.

* **The three outcomes are real.** ``ready``, ``missing`` and ``error`` are
  produced and asserted, including the case where the tables cannot be created
  at all, because a schema bootstrap that can only report success is a bootstrap
  whose failure surfaces later as a missing-table error in a route.

* **The table matches the value object.** ``FieldValue`` was designed to be a
  field row. If a field is added to one and not the other the write silently
  drops it, so the correspondence is asserted rather than remembered.
"""

import os
import re
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_structured_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import record_templates as templates  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import structured_records as store  # noqa: E402

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
    return conn, cur


def setup_environment() -> None:
    store.reset_structured_schema_cache()
    conn, cur = _connect()
    store.ensure_structured_schema(cur, force=True)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Test doubles for the two unhappy outcomes
# ---------------------------------------------------------------------------
class _BrokenCursor:
    """Refuses everything. Produces the ``error`` outcome.

    Deliberately not a subclass of anything: ``get_table_columns`` dispatches on
    ``hasattr(obj, "cursor")``, so a double that grew a ``cursor`` attribute
    would be treated as a connection and the test would exercise a path the
    production code never takes.
    """

    def execute(self, *_args, **_kwargs):
        raise RuntimeError("database is locked")

    def fetchall(self):
        raise RuntimeError("database is locked")


class _NoCreateCursor:
    """Passes reads through and silently drops DDL. Produces ``missing``.

    This is the shape of a real production failure rather than an invented one:
    a database role with SELECT but not CREATE. The ensure completes without
    raising, the introspection finds nothing, and the correct answer is
    "missing" — not "ready", and not an exception several frames away.
    """

    def __init__(self, inner):
        self._inner = inner
        self.suppressed = 0

    def execute(self, sql, *args, **kwargs):
        text = str(sql).strip().upper()
        if text.startswith("CREATE TABLE") or text.startswith("CREATE INDEX") \
                or text.startswith("ALTER TABLE"):
            self.suppressed += 1
            return None
        return self._inner.execute(sql, *args, **kwargs)

    def fetchall(self):
        return self._inner.fetchall()

    def fetchone(self):
        return self._inner.fetchone()


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def stage_bootstrap() -> None:
    print("\n[bootstrap]")
    conn, cur = _connect()

    store.reset_structured_schema_cache()
    first = store.ensure_structured_schema(cur, force=True)
    second = store.ensure_structured_schema(cur, force=True)
    conn.commit()

    check("ensure_structured_schema reports ready", first.get("status") == "ready", str(first))
    check("ensure_structured_schema is idempotent", second.get("status") == "ready", str(second))
    check("ensure_structured_schema returns a dict with a status",
          isinstance(second, dict) and "status" in second)
    check("a forced ensure is not reported as cached", second.get("cached") is False)

    # Only success is remembered. A cached failure turns a transient lock into
    # an outage that lasts until somebody redeploys.
    cached = store.ensure_structured_schema(cur)
    check("a repeat ensure is served from the success cache", cached.get("cached") is True)

    check("require_structured_schema returns on a healthy store",
          store.require_structured_schema(cur).get("status") == "ready")

    conn.close()


def stage_outcomes() -> None:
    print("\n[three outcomes]")

    store.reset_structured_schema_cache()
    broken = store.ensure_structured_schema(_BrokenCursor(), force=True)
    check("an unreachable store reports error", broken.get("status") == "error", str(broken))
    check("the error outcome carries a reason", bool(broken.get("error")))
    check("the error outcome is not cached", broken.get("cached") is False)

    raised = False
    try:
        store.require_structured_schema(_BrokenCursor(), force=True)
    except store.StructuredSchemaMissing:
        raised = True
    except Exception as exc:  # pragma: no cover - a wrong exception is a failure
        check("require raises the schema exception, not a bare error", False, repr(exc))
    check("require_structured_schema raises when the store is unreachable", raised)

    # A role that can read but not create. Point it at a database of its own so
    # the suppression is observable rather than masked by tables another stage
    # already built.
    empty_db = os.path.join(tempfile.mkdtemp(prefix="private_structured_ro_"), "empty.db")
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "sqlite:///" + empty_db
    try:
        # `services.db.connect` resolves DATABASE_URL on every call, which is
        # what makes the rebinding above enough.
        conn = db.connect()
        cur = _NoCreateCursor(conn.cursor())
        store.reset_structured_schema_cache()
        result = store.ensure_structured_schema(cur, force=True)
        check("a store that cannot be created reports missing",
              result.get("status") == "missing", str(result))
        check("the missing outcome names the tables it could not find",
              len(result.get("missing") or []) == len(store.TABLES),
              str(result.get("missing")))
        check("the missing outcome did not raise", isinstance(result, dict))
        check("the DDL was really suppressed", cur.suppressed > 0, str(cur.suppressed))
        conn.close()
    finally:
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved
        store.reset_structured_schema_cache()

    # And back to healthy: a store that heals is noticed without a restart.
    conn, cur = _connect()
    healed = store.ensure_structured_schema(cur, force=True)
    conn.commit()
    check("the store recovers once the tables exist again",
          healed.get("status") == "ready", str(healed))
    conn.close()


def stage_columns() -> None:
    print("\n[columns]")
    conn, cur = _connect()
    store.ensure_structured_schema(cur, force=True)
    conn.commit()

    for table in store.TABLES:
        present = set(db.get_table_columns(cur, table) or [])
        required = set(store.REQUIRED_COLUMNS[table])
        check(f"{table} exists with columns", bool(present))
        check(f"{table} has every required column",
              required <= present, str(sorted(required - present)))
        check(f"{table} carries owner_user_id", "owner_user_id" in present)

        # A required column that is not in the DDL would make `ensure` report
        # `missing` forever on a healthy database — the bootstrap equivalent of
        # a smoke alarm wired to nothing.
        declared = set(_ddl_columns(store.TABLE_DDL[table]))
        check(f"{table} declares every column it requires",
              required <= declared, str(sorted(required - declared)))

    conn.close()


def _ddl_columns(ddl: str) -> list[str]:
    """Column names in a CREATE TABLE body, by first token of each line."""
    names: list[str] = []
    for line in ddl.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.startswith(("CREATE", ")", "(")):
            continue
        token = stripped.split()[0]
        if token.upper() in {"UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "CONSTRAINT"}:
            continue
        names.append(token)
    return names


def stage_portability() -> None:
    print("\n[portability]")
    # `services.db` rewrites SQLite's autoincrement form for PostgreSQL. DDL
    # written outside that vocabulary is a deploy-time failure on Postgres and
    # nothing at all here, which is the worst combination available.
    for table, ddl in store.TABLE_DDL.items():
        upper = ddl.upper()
        check(f"{table} DDL uses the translatable primary key form",
              "INTEGER PRIMARY KEY AUTOINCREMENT" in upper)
        check(f"{table} DDL creates only if absent", "CREATE TABLE IF NOT EXISTS" in upper)
        for banned in ("INSERT OR IGNORE", "AUTO_INCREMENT", "SERIAL",
                       "WITHOUT ROWID", "JSONB", "TEXT[]"):
            check(f"{table} DDL avoids {banned}", banned not in upper)

    for statement in store.INDEX_DDL:
        check("every index is created only if absent",
              "CREATE INDEX IF NOT EXISTS" in statement.upper(), statement[:70])


def stage_owner_scope() -> None:
    print("\n[owner scope]")
    # Every index leads with owner_user_id. An index that does not cannot serve
    # a query whose first clause is the owner, and the query degrades to a scan
    # of every member's rows — which is both the performance problem and, on a
    # shared table, the shape of query you least want to be cheap.
    for statement in store.INDEX_DDL:
        match = re.search(r"\bON\s+\w+\s*\(([^)]*)\)", statement, re.IGNORECASE)
        columns = [c.strip() for c in (match.group(1) if match else "").split(",")]
        check(f"index leads with owner_user_id: {statement[40:80].strip()}",
              bool(columns) and columns[0] == "owner_user_id", str(columns))

    # Uniqueness is owner-scoped everywhere. A globally unique `record_key`
    # would let one account's write fail because another account had already
    # used that key — an existence oracle produced by a constraint.
    for table, ddl in store.TABLE_DDL.items():
        for match in re.finditer(r"UNIQUE\s*\(([^)]*)\)", ddl, re.IGNORECASE):
            columns = [c.strip() for c in match.group(1).split(",")]
            check(f"{table} uniqueness is owner-scoped",
                  columns and columns[0] == "owner_user_id", str(columns))

    # And there is at least one, or "unique" is a property nothing has.
    for table, ddl in store.TABLE_DDL.items():
        check(f"{table} declares an owner-scoped uniqueness constraint",
              "UNIQUE(owner_user_id" in ddl.replace("UNIQUE (", "UNIQUE("))


def stage_no_blob() -> None:
    print("\n[no blob]")
    # The single-JSON-column implementation, refused structurally. See the
    # module docstring in services/private_office/structured_records.py for what
    # it costs; the point of asserting it here is that the cost is invisible at
    # the moment somebody adds the column.
    banned_tokens = ("payload_json", "data_json", "fields_json", "metadata_json",
                     "extra_json", "attributes_json", "blob")
    for table, ddl in store.TABLE_DDL.items():
        lowered = ddl.lower()
        for token in banned_tokens:
            check(f"{table} has no {token} column", token not in lowered)

    # The field projection is the storage, so it must carry the type
    # discriminator and both numeric and date columns — otherwise "expires in
    # 90 days" and "worth more than X" are string comparisons.
    field_columns = set(_ddl_columns(store.TABLE_DDL[store.FIELDS_TABLE]))
    for column in ("value_type", "value_text", "value_number", "value_date", "currency"):
        check(f"the field projection stores {column}", column in field_columns)


def stage_secret_handling() -> None:
    print("\n[secret handling]")
    field_columns = set(_ddl_columns(store.TABLE_DDL[store.FIELDS_TABLE]))

    # A RESTRICTED value needs somewhere that is not the plaintext column, and
    # a row that claims encryption must be checkable.
    for column in ("cipher_text", "cipher_key_id", "is_encrypted"):
        check(f"the field projection can hold an encrypted value: {column}",
              column in field_columns)

    # A screen and an index read different columns from the value. Deriving
    # them at read time means every list query loads the secret in order to
    # mask it, and a query that loads a secret in order not to show it is one
    # bug away from showing it.
    for column in ("masked_text", "search_text"):
        check(f"the field projection stores the safe projection: {column}",
              column in field_columns)
    check("the field projection records whether indexing is permitted",
          "searchable" in field_columns)
    check("the field projection records the mask strategy", "mask" in field_columns)
    check("the field projection records field-level sensitivity",
          "sensitivity" in field_columns)

    # History stores paths, not values.
    revision_columns = set(_ddl_columns(store.TABLE_DDL[store.REVISIONS_TABLE]))
    check("the revision table records which paths changed",
          "changed_paths" in revision_columns)
    for banned in ("value_before", "value_after", "old_value", "new_value",
                   "previous_value", "value_text", "payload"):
        check(f"the revision table has no {banned} column", banned not in revision_columns)

    # The envelope holds no field values either — it holds a summary line the
    # writer builds from *masked* values, which is a different thing and is
    # named differently on purpose.
    envelope_columns = set(_ddl_columns(store.TABLE_DDL[store.RECORDS_TABLE]))
    for banned in ("value_text", "value_number", "document_number", "identifier"):
        check(f"the envelope has no {banned} column", banned not in envelope_columns)
    check("the envelope carries a summary line for lists",
          "summary_text" in envelope_columns)


def stage_envelope() -> None:
    print("\n[envelope]")
    columns = set(_ddl_columns(store.TABLE_DDL[store.RECORDS_TABLE]))

    # The mission's envelope, item by item. Absent from this list on purpose:
    # relationships and attachments, which are the existing relationship and
    # document tables' job — a second relationship column here would be a
    # competing system, which is the thing Stage 0 forbids.
    for column in (
        "owner_user_id", "office_id", "record_key", "template_key",
        "template_version", "contract_version", "domain", "ia_domain", "title",
        "description", "status", "lifecycle_state", "sensitivity",
        "verification_state", "source_type", "source_ref", "provenance_type",
        "evidence_ids", "effective_date", "expires_at", "review_at", "tags",
        "reminder_policy", "undx_readable", "revision", "supersedes_id",
        "created_at", "updated_at", "created_by", "updated_by",
    ):
        check(f"the envelope carries {column}", column in columns)

    # Two version numbers, because they answer different questions: "can you
    # draw this form" and "is this record shaped the way the template is now".
    check("the envelope records the template version that wrote it",
          "template_version" in columns)
    check("the envelope records the contract version that wrote it",
          "contract_version" in columns)
    check("the store agrees with the template contract version",
          store.CONTRACT_VERSION == templates.CONTRACT_VERSION)


def stage_verification() -> None:
    print("\n[verification]")
    check("there are ten verification states", len(store.VERIFICATION_STATES) == 10,
          str(store.VERIFICATION_STATES))
    check("verification states are unique",
          len(set(store.VERIFICATION_STATES)) == len(store.VERIFICATION_STATES))

    for name in ("USER_VERIFIED", "SOURCE_VERIFIED", "PROVIDER_VERIFIED",
                 "DOCUMENT_EXTRACTED_NEEDS_REVIEW", "UNDX_SUGGESTED_NEEDS_REVIEW",
                 "INFERRED", "STALE", "DISPUTED", "REJECTED", "ARCHIVED"):
        check(f"verification state {name} exists", name in store.VERIFICATION_STATES)

    # The rule the mission states as prose — never auto-convert extracted or
    # inferred data into verified truth — expressed as a set difference, so it
    # holds for a state added later without anyone remembering to re-check.
    for state in store.VERIFIED_STATES:
        check(f"a machine may not write {state}", not store.machine_may_write(state))
    for state in store.NEEDS_REVIEW_STATES:
        check(f"a machine may write {state}", store.machine_may_write(state))
    check("every state is either machine-writable or verified",
          set(store.VERIFICATION_STATES)
          == store.MACHINE_WRITABLE_VERIFICATION | store.VERIFIED_STATES)
    check("no state is both machine-writable and verified",
          not (store.MACHINE_WRITABLE_VERIFICATION & store.VERIFIED_STATES))

    check("needs-review states are not verified",
          not (store.NEEDS_REVIEW_STATES & store.VERIFIED_STATES))
    check("extraction lands in a needs-review state",
          store.needs_review(store.VERIFICATION_DOCUMENT_EXTRACTED))
    check("an UNDX suggestion lands in a needs-review state",
          store.needs_review(store.VERIFICATION_UNDX_SUGGESTED))
    check("a hand-entered record is user verified",
          store.DEFAULT_VERIFICATION == store.VERIFICATION_USER_VERIFIED)

    # Fail closed on anything unrecognised, in both directions.
    for junk in (None, "", "  ", "VERIFIED", "verified_by_ai", 7, object()):
        check(f"unknown verification {junk!r} normalizes to None",
              store.normalize_verification(junk) is None)
        check(f"unknown verification {junk!r} is not machine-writable",
              not store.machine_may_write(junk))
        check(f"unknown verification {junk!r} is not verified", not store.is_verified(junk))
        check(f"unknown verification {junk!r} does not need review",
              not store.needs_review(junk))

    check("verification normalizes case and whitespace",
          store.normalize_verification("  user_verified  ")
          == store.VERIFICATION_USER_VERIFIED)


def stage_vocabularies() -> None:
    print("\n[vocabularies]")
    # One list per concept, package-wide. Re-declaring source types here would
    # drift within a release, which is the failure model.py exists to end.
    check("source types are the package's, not a second copy",
          store.SOURCE_TYPES is records.SOURCE_TYPES)
    check("derived sources are the package's, not a second copy",
          store.DERIVED_SOURCES is records.DERIVED_SOURCES)
    check("lifecycle states are the package's, not a second copy",
          store.LIFECYCLE_STATES is model.LIFECYCLE_STATES)

    for junk in (None, "", "GUESS", 3):
        check(f"unknown source {junk!r} normalizes to None",
              store.normalize_source_type(junk) is None)
        check(f"unknown change type {junk!r} normalizes to None",
              store.normalize_change_type(junk) is None)
    check("a known source normalizes", store.normalize_source_type("document") == "DOCUMENT")
    check("a known change type normalizes", store.normalize_change_type("created") == "CREATED")

    check("change types are unique",
          len(set(store.CHANGE_TYPES)) == len(store.CHANGE_TYPES))
    check("creation is a change type", store.CHANGE_CREATED in store.CHANGE_TYPES)
    check("a reveal is a change type", store.CHANGE_REVEALED in store.CHANGE_TYPES)
    check("a template migration is a change type",
          store.CHANGE_TEMPLATE_MIGRATED in store.CHANGE_TYPES)


def stage_field_value_parity() -> None:
    print("\n[field value parity]")
    # The field table was designed around FieldValue. If an attribute is added
    # to one and not the other the write silently drops it, so the
    # correspondence is asserted rather than remembered.
    columns = set(_ddl_columns(store.TABLE_DDL[store.FIELDS_TABLE]))
    row_columns = store.field_row_columns()
    attributes = store.field_value_attributes()

    check("the declared correspondence is one-to-one",
          len(row_columns) == len(attributes), f"{len(row_columns)} vs {len(attributes)}")
    for column in row_columns:
        check(f"declared field column {column} exists on the table", column in columns)

    sample = templates.FieldValue(
        path="issuance.document_number",
        kind=templates.KIND_IDENTIFIER,
        value_type=model.VALUE_STRING,
        value_text="X1234",
        value_number=None,
        sensitivity=model.SENSITIVITY_RESTRICTED,
        mask=templates.MASK_LAST4,
        searchable=True,
    )
    for attribute in attributes:
        check(f"FieldValue supplies {attribute}", hasattr(sample, attribute))

    # Every FieldValue attribute is stored somewhere. A field the value object
    # carries and the table has no home for is a validated property thrown away
    # at the last step.
    for attribute in vars(sample):
        target = "field_path" if attribute == "path" else (
            "field_kind" if attribute == "kind" else attribute)
        check(f"FieldValue.{attribute} has a column", target in columns, target)


def stage_isolation_from_existing_schema() -> None:
    print("\n[coexistence]")
    # Batch D adds tables; it must not have quietly renamed or collided with
    # anything the fact store, the graph or the six primitives already own.
    existing = set(schema.TABLES) | set(records.TABLES)
    for table in store.TABLES:
        check(f"{table} does not collide with an existing private table",
              table not in existing)
    check("the three new tables are distinct", len(set(store.TABLES)) == 3)

    # And the existing schema still builds alongside it in one database.
    conn, cur = _connect()
    schema.reset_schema_cache()
    records.reset_records_schema_cache()
    store.reset_structured_schema_cache()
    a = schema.ensure_private_schema(cur, force=True)
    b = records.ensure_records_schema(cur, force=True)
    c = store.ensure_structured_schema(cur, force=True)
    conn.commit()
    check("the original private schema still builds", a.get("status") == "ready", str(a))
    check("the record primitives still build", b.get("status") == "ready", str(b))
    check("the structured record schema builds beside them",
          c.get("status") == "ready", str(c))
    conn.close()


STAGES = (
    stage_bootstrap,
    stage_outcomes,
    stage_columns,
    stage_portability,
    stage_owner_scope,
    stage_no_blob,
    stage_secret_handling,
    stage_envelope,
    stage_verification,
    stage_vocabularies,
    stage_field_value_parity,
    stage_isolation_from_existing_schema,
)


def test_everything():
    setup_environment()
    for stage in STAGES:
        stage()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    print("PRIVATE OFFICE STRUCTURED RECORD SCHEMA — Batch D")
    print(f"db: {_TMP_DB}")
    setup_environment()
    for stage in STAGES:
        stage()

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
