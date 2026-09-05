"""Batch D — the structured record store: writing, reading, revealing.

Run either way::

    python -m pytest tests/private_office/test_structured_record_store.py
    python tests/private_office/test_structured_record_store.py

What these tests are actually defending
---------------------------------------
The schema tests next door prove the *shape* is right — no blob column, no
before-value column, every index owner-scoped. This file proves the *writer*
uses that shape correctly, which is a different question and the one that
actually determines whether a passport number stays private.

Almost every check exists because there is a plausible, tidy-looking
implementation that would pass a code review and break one of these:

* **A restricted value has no plaintext copy anywhere.** Not in ``value_text``,
  not in ``masked_text``, not in ``search_text``, not in the summary line, not
  in the revision history, and not in any other table in the database. The check
  is a sweep of every row of every table for the literal string, because the
  interesting failure is never the column you thought about.

* **Reading a record does not decrypt it.** ``get_record`` returns masked text
  for every masked field and has no argument that changes that. The version of
  this code that fails is the one with ``include_values=True``, which is correct
  for two releases and then becomes a default.

* **A masked value is not laundered through the assistant.** The UNDX
  projection drops masked fields entirely rather than passing ``•••• 1234``,
  because a model given the masked form will reason about "the passport ending
  1234" and put that in a summary.

* **A machine cannot mark its own guess as confirmed.** An extractor asking for
  ``USER_VERIFIED`` is refused rather than silently downgraded — silently
  downgrading leaves the caller believing something untrue about its own writes.

* **Refuse rather than pretend.** With no encryption key configured the write of
  a restricted field is rejected. It does not fall back to storing the value in
  the clear while the schema, the API and the UI all go on saying it is
  protected.

* **The second account gets nothing.** Not by a filter applied after loading,
  but by the owner predicate being the first clause of every query. Tested by
  identifier substitution across get, list, search, reveal and history — and
  "denied" and "does not exist" are the same answer, so the store cannot be used
  to enumerate what another member has.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="structured_store_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import field_crypto as crypto  # noqa: E402
from services.private_office import record_templates as templates  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import structured_records as store  # noqa: E402

USER_A = 9811
USER_B = 9812

#: The value every leak check hunts for. Distinctive enough that a match is
#: never a coincidence, and long enough that a masked form cannot contain it.
SECRET = "X9912340987"

#: Generated once at import and installed by :func:`_with_keys`. The keyring is
#: set per stage rather than once at module scope because one stage removes it
#: on purpose, and a stage that leaves the process without a key would make
#: every later stage fail for the wrong reason.
_KEY = crypto.generate_key()

PASSPORT = {
    "identification.surname": "Okonkwo",
    "identification.given_names": "Amara Chidi",
    "identification.date_of_birth": "1988-04-11",
    "identification.nationality": "US",
    "identification.sex": "F",
    "issuance.document_number": SECRET,
    "issuance.issuing_country": "US",
    "issuance.issue_date": "2020-12-01",
    "issuance.expiry_date": "2030-12-01",
}

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


def _with_keys() -> None:
    os.environ["PRIVATE_OFFICE_FIELD_KEYS"] = f"k1:{_KEY}"
    os.environ["PRIVATE_OFFICE_FIELD_KEY_ACTIVE"] = "k1"


def _without_keys() -> None:
    os.environ.pop("PRIVATE_OFFICE_FIELD_KEYS", None)
    os.environ.pop("PRIVATE_OFFICE_FIELD_KEY_ACTIVE", None)


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    store.ensure_structured_schema(cur)
    return conn, cur


def setup_environment() -> None:
    _with_keys()
    store.reset_structured_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


def _create_passport(cur, owner=USER_A, **kwargs):
    fields = {"title": "US Passport", "allow_duplicate": True}
    fields.update(kwargs)
    return store.create_record(
        cur, owner_user_id=owner, template_key="passport", payload=dict(PASSPORT),
        **fields)


def _every_stored_string(cur) -> str:
    """Every value in every row of every table, as one string.

    Blunt on purpose. A leak check that looks only at the columns the author
    thought about is a check that passes for the columns the author thought
    about — and the summary line, the history row and the audit object id are
    all places a value has ended up in systems like this one.
    """
    cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    parts: list[str] = []
    for row in cur.fetchall() or ():
        table = row[0] if not hasattr(row, "keys") else row["name"]
        cur.execute(f"SELECT * FROM {table}")
        for record in cur.fetchall() or ():
            parts.append(" ".join(str(v) for v in tuple(record)))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def stage_create() -> None:
    print("\n[create]")
    _with_keys()
    conn, cur = _connect()

    result = _create_passport(cur)
    conn.commit()

    check("a valid passport is created", result["status"] == store.STATUS_CREATED,
          str(result.get("errors")))
    record = result["record"]
    check("the record carries the template it was written against",
          record["template_key"] == "passport" and record["template_version"] >= 1)
    check("the record carries the contract version that wrote it",
          record["contract_version"] == store.CONTRACT_VERSION)
    check("a hand-entered record is USER_VERIFIED",
          record["verification_state"] == store.VERIFICATION_USER_VERIFIED)
    check("a hand-entered record does not need review", record["needs_review"] is False)

    # The expiry date is lifted onto the envelope from the field that declares
    # itself the expiration driver. Without this the reminder sweep has to open
    # every record to find out when it expires, which is the pressure that leads
    # to a cached expiry and a reminder that stops arriving.
    check("the envelope's expiry comes from the expiring field",
          record["expires_at"] == "2030-12-01", record["expires_at"])

    values = {f["path"]: f for f in record["fields"]}
    check("every submitted field is stored", len(values) == len(PASSPORT),
          f"{len(values)} of {len(PASSPORT)}")
    check("an unmasked field reads back as itself",
          values["identification.surname"]["value"] == "Okonkwo")
    check("a last4-masked field reads back masked",
          values["issuance.document_number"]["value"].endswith("0987")
          and SECRET not in values["issuance.document_number"]["value"],
          values["issuance.document_number"]["value"])
    check("a year-masked date of birth reads back as the year only",
          values["identification.date_of_birth"]["value"] == "1988")
    check("a masked field advertises that it can be revealed",
          values["issuance.document_number"]["revealable"] is True)
    check("an unmasked field does not advertise a reveal",
          values["identification.surname"]["revealable"] is False)

    conn.close()


def stage_restricted_storage() -> None:
    print("\n[restricted storage]")
    _with_keys()
    conn, cur = _connect()
    result = _create_passport(cur)
    conn.commit()
    record_id = result["record_id"]

    cur.execute(
        """SELECT * FROM private_record_fields
        WHERE owner_user_id = ? AND record_id = ? AND field_path = ?""",
        (USER_A, record_id, "issuance.document_number"))
    row = dict(cur.fetchone())

    check("a restricted field is marked encrypted", bool(row["is_encrypted"]))
    check("a restricted field records which key sealed it",
          row["cipher_key_id"] == "k1", row["cipher_key_id"])
    check("a restricted field has no plaintext column content",
          row["value_text"] == "", repr(row["value_text"]))
    # An encrypted amount whose magnitude is still in a numeric column is not
    # encrypted, it is merely inconvenient to read. Same for a date.
    check("a restricted field contributes no number", row["value_number"] is None)
    check("a restricted field contributes no date", row["value_date"] == "")
    check("the ciphertext is not the value", SECRET not in str(row["cipher_text"]))
    check("the masked form is not the value", SECRET not in str(row["masked_text"]))
    check("the index text is not the value", SECRET not in str(row["search_text"]))

    # The sweep. Everything above checks a column somebody thought of; this
    # checks the ones nobody did.
    everything = _every_stored_string(cur)
    check("the value appears nowhere in the database", SECRET not in everything)

    # And the ciphertext is bound to this owner, record and path — moving it
    # anywhere else fails authentication rather than decrypting.
    moved = 0
    for owner, key, path in (
        (USER_B, str(row["record_key"]), "issuance.document_number"),
        (USER_A, "some-other-record-key", "issuance.document_number"),
        (USER_A, str(row["record_key"]), "identification.surname"),
    ):
        try:
            crypto.decrypt(str(row["cipher_text"]), owner_user_id=owner,
                           record_key=key, field_path=path)
        except crypto.FieldCryptoError:
            moved += 1
    check("a ciphertext moved to another owner, record or field does not decrypt",
          moved == 3, f"{moved} of 3 refused")

    conn.close()


def stage_refuses_without_a_key() -> None:
    print("\n[no key configured]")
    _without_keys()
    conn, cur = _connect()
    try:
        rejected = False
        try:
            _create_passport(cur, title="no key")
        except store.StructuredRecordRejected as exc:
            rejected = True
            message = str(exc)
        conn.rollback()

        check("a restricted field is refused when no key is configured", rejected)
        if rejected:
            check("the refusal names the field rather than the value",
                  "issuance.document_number" in message and SECRET not in message,
                  message)

        # The important half: nothing was written. A partial record — envelope
        # saved, restricted field silently dropped — would be worse than the
        # rejection, because the member would see a passport on file with no
        # number and no indication anything failed.
        cur.execute(
            "SELECT COUNT(*) AS n FROM private_structured_records "
            "WHERE owner_user_id = ? AND title = ?", (USER_A, "no key"))
        count = int(dict(cur.fetchone())["n"])
        check("nothing was written when the field was refused", count == 0, str(count))

        # A record with no restricted field is unaffected — the store does not
        # go entirely offline because one field kind cannot be protected.
        ok = store.create_record(
            cur, owner_user_id=USER_A, template_key="residence",
            payload={"address.line1": "12 Rue Lepic", "address.city": "Paris",
                     "address.country": "FR"},
            title="Paris", allow_duplicate=True)
        conn.commit()
        check("a record with no restricted field still writes without a key",
              ok["status"] == store.STATUS_CREATED, str(ok.get("errors")))
    finally:
        _with_keys()
        conn.close()


def stage_verification_authority() -> None:
    print("\n[verification authority]")
    _with_keys()
    conn, cur = _connect()

    refused = []
    for actor in ("extraction", "undx", "system", "import", "provider"):
        for state in sorted(store.VERIFIED_STATES):
            try:
                store.create_record(
                    cur, owner_user_id=USER_A, template_key="residence",
                    payload={"address.line1": "1 A St", "address.country": "US"},
                    actor_kind=actor, verification_state=state,
                    source_type="DOCUMENT", provenance_type="DOCUMENT_EXTRACTED",
                    allow_duplicate=True)
            except store.StructuredRecordRejected:
                refused.append((actor, state))
    conn.rollback()
    check("no machine actor may write any verified state",
          len(refused) == 5 * len(store.VERIFIED_STATES),
          f"{len(refused)} of {5 * len(store.VERIFIED_STATES)}")

    drafted = store.create_record(
        cur, owner_user_id=USER_A, template_key="residence",
        payload={"address.line1": "1 A St", "address.country": "US"},
        actor_kind="extraction",
        verification_state=store.VERIFICATION_DOCUMENT_EXTRACTED,
        source_type="DOCUMENT", provenance_type="DOCUMENT_EXTRACTED",
        allow_duplicate=True)
    conn.commit()
    check("an extractor may write a needs-review state",
          drafted["status"] == store.STATUS_CREATED, str(drafted.get("errors")))
    check("an extracted record reports that it needs review",
          drafted["record"]["needs_review"] is True)
    check("an extracted record does not report itself verified",
          drafted["record"]["verified"] is False)

    # A derived record that cannot say where it came from is a record that will
    # eventually be quoted as if a member had typed it.
    provenance_refused = False
    try:
        store.create_record(
            cur, owner_user_id=USER_A, template_key="residence",
            payload={"address.line1": "2 B St", "address.country": "US"},
            actor_kind="extraction",
            verification_state=store.VERIFICATION_DOCUMENT_EXTRACTED,
            source_type="DOCUMENT", allow_duplicate=True)
    except store.StructuredRecordRejected:
        provenance_refused = True
    conn.rollback()
    check("a derived record with no provenance type is refused", provenance_refused)

    # The needs-review queue is a query, not a scan.
    queue = store.list_records(cur, owner_user_id=USER_A, needs_review_only=True)
    check("the needs-review queue finds the extracted record",
          any(r["id"] == drafted["record_id"] for r in queue["records"]))
    check("the needs-review queue excludes hand-entered records",
          all(r["needs_review"] for r in queue["records"]))

    conn.close()


def stage_duplicates_and_idempotency() -> None:
    print("\n[duplicates and idempotency]")
    _with_keys()
    conn, cur = _connect()

    first = store.create_record(
        cur, owner_user_id=USER_B, template_key="passport", payload=dict(PASSPORT),
        title="First")
    conn.commit()
    check("the first passport is written", first["status"] == store.STATUS_CREATED)

    second = store.create_record(
        cur, owner_user_id=USER_B, template_key="passport", payload=dict(PASSPORT),
        title="Second")
    conn.commit()
    check("an identical passport is reported as a duplicate",
          second["status"] == store.STATUS_DUPLICATE, second["status"])
    check("a duplicate names the record it matched",
          second["duplicates"] == [first["record_id"]], str(second["duplicates"]))
    check("a duplicate writes nothing", second["record_id"] == 0)

    forced = store.create_record(
        cur, owner_user_id=USER_B, template_key="passport", payload=dict(PASSPORT),
        title="Second", allow_duplicate=True)
    conn.commit()
    check("a confirmed duplicate is written", forced["status"] == store.STATUS_CREATED)

    # Duplicate detection compares masked text, so it never loads the value it
    # is comparing. Proving it by observing that a change beyond the last four
    # digits still reads as a duplicate.
    near = dict(PASSPORT)
    near["issuance.document_number"] = "Z0000000987"
    near_result = store.create_record(
        cur, owner_user_id=USER_B, template_key="passport", payload=near,
        title="Near")
    conn.rollback()
    check("duplicate detection compares the masked form, not the value",
          near_result["status"] == store.STATUS_DUPLICATE, near_result["status"])

    one = store.create_record(
        cur, owner_user_id=USER_B, template_key="residence",
        payload={"address.line1": "9 Kloof", "address.country": "ZA"},
        idempotency_key="request-77", allow_duplicate=True)
    two = store.create_record(
        cur, owner_user_id=USER_B, template_key="residence",
        payload={"address.line1": "9 Kloof", "address.country": "ZA"},
        idempotency_key="request-77", allow_duplicate=True)
    conn.commit()
    check("a replayed create returns the record it already wrote",
          two["status"] == store.STATUS_EXISTING
          and two["record_id"] == one["record_id"], f"{one['status']}/{two['status']}")

    keys = {store.record_key(owner_user_id=USER_A, template_key="passport"),
            store.record_key(owner_user_id=USER_A, template_key="passport")}
    check("a record key with no idempotency key is not deterministic", len(keys) == 2)
    check("both record key shapes are indistinguishable",
          all(len(k) == 48 for k in keys
              | {store.record_key(owner_user_id=USER_A, template_key="passport",
                                  idempotency_key="x")}))
    # The handle appears in URLs and audit rows, so it must carry nothing.
    check("a record key carries no fragment of any field value",
          SECRET[-4:] not in store.record_key(
              owner_user_id=USER_A, template_key="passport", idempotency_key=SECRET))

    conn.close()


def stage_update() -> None:
    print("\n[update]")
    _with_keys()
    conn, cur = _connect()
    created = _create_passport(cur, title="To edit")
    conn.commit()
    record_id = created["record_id"]

    moved = store.update_record(
        cur, owner_user_id=USER_A, record_id=record_id,
        payload={"issuance.expiry_date": "2031-06-30"}, expected_revision=1)
    conn.commit()
    check("a patched field is stored", moved["status"] == store.STATUS_UPDATED)
    check("the envelope's expiry follows the expiring field",
          moved["record"]["expires_at"] == "2031-06-30", moved["record"]["expires_at"])
    check("a change advances the revision", moved["record"]["revision"] == 2)
    check("an untouched field is untouched",
          {f["path"] for f in moved["record"]["fields"]} == set(PASSPORT))

    conflicted = False
    try:
        store.update_record(cur, owner_user_id=USER_A, record_id=record_id,
                            title="stale", expected_revision=1)
    except store.StructuredRecordConflict:
        conflicted = True
    conn.rollback()
    check("a stale expected revision is refused", conflicted)

    required = store.update_record(cur, owner_user_id=USER_A, record_id=record_id,
                                   payload={"issuance.expiry_date": ""})
    conn.rollback()
    check("a required field cannot be cleared", required["status"] == "invalid")

    cleared = store.update_record(cur, owner_user_id=USER_A, record_id=record_id,
                                  payload={"identification.sex": ""})
    conn.commit()
    # Present-and-empty means clear, absent means leave alone. A writer that read
    # only the validated values would see no FieldValue for the cleared path and
    # silently ignore every deletion a member made.
    check("an optional field submitted empty is cleared",
          "identification.sex" not in {f["path"] for f in cleared["record"]["fields"]})

    rotated = store.update_record(
        cur, owner_user_id=USER_A, record_id=record_id,
        payload={"issuance.document_number": "K5555554321"})
    conn.commit()
    fields = {f["path"]: f for f in rotated["record"]["fields"]}
    check("a corrected restricted field re-encrypts rather than storing plaintext",
          fields["issuance.document_number"]["encrypted"] is True
          and fields["issuance.document_number"]["value"].endswith("4321"))
    everything = _every_stored_string(cur)
    check("neither the old nor the new restricted value is stored in the clear",
          SECRET not in everything and "K5555554321" not in everything)

    unchanged = store.update_record(cur, owner_user_id=USER_A, record_id=record_id)
    check("an update with nothing to change is a no-op",
          unchanged["status"] == store.STATUS_UNCHANGED)

    conn.close()


def stage_history() -> None:
    print("\n[history]")
    _with_keys()
    conn, cur = _connect()
    created = _create_passport(cur, title="History")
    conn.commit()
    record_id = created["record_id"]

    store.update_record(cur, owner_user_id=USER_A, record_id=record_id,
                        payload={"issuance.expiry_date": "2032-01-01"})
    store.update_record(cur, owner_user_id=USER_A, record_id=record_id,
                        status="ACTIVE" if "ACTIVE" in
                        templates.get_template("passport").statuses else None)
    store.reveal_field(cur, owner_user_id=USER_A, record_id=record_id,
                       field_path="issuance.document_number", step_up_verified=True)
    conn.commit()

    history = store.record_history(cur, owner_user_id=USER_A, record_id=record_id)
    kinds = [entry["change_type"] for entry in history]
    check("the history records the creation", store.CHANGE_CREATED in kinds, str(kinds))
    check("the history records the edit", store.CHANGE_UPDATED in kinds, str(kinds))
    check("the history records the reveal", store.CHANGE_REVEALED in kinds, str(kinds))
    check("the history sequence is strictly ordered",
          [e["sequence"] for e in history] ==
          sorted((e["sequence"] for e in history), reverse=True))
    check("the reveal advanced the history without changing the record",
          len(history) > int(store.get_record(
              cur, owner_user_id=USER_A, record_id=record_id,
              audit=False)["revision"]))

    check("the history names the changed path",
          any("issuance.expiry_date" in e["changed_paths"] for e in history))
    # The whole point of the table. An undo history of a passport number is a
    # second, unprotected copy of the passport number.
    check("the history holds no values", SECRET not in str(history)
          and "2030-12-01" not in str(history), str(history))

    cur.execute("SELECT * FROM private_record_revisions WHERE owner_user_id = ?",
                (USER_A,))
    rows = [dict(r) for r in cur.fetchall() or ()]
    check("no revision row anywhere holds a value",
          all(SECRET not in " ".join(str(v) for v in row.values()) for row in rows))

    conn.close()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def stage_read_never_decrypts() -> None:
    print("\n[read never decrypts]")
    _with_keys()
    conn, cur = _connect()
    created = _create_passport(cur, title="Read")
    conn.commit()
    record_id = created["record_id"]

    record = store.get_record(cur, owner_user_id=USER_A, record_id=record_id)
    check("get_record returns no restricted value", SECRET not in str(record))
    check("get_record returns the masked form",
          any(f["value"].endswith("0987") for f in record["fields"]))

    # There must be no argument that changes this. A flag like
    # `include_values=True` is correct for two releases and then becomes a
    # default on a list endpoint.
    import inspect
    signature = inspect.signature(store.get_record)
    check("get_record has no parameter that could return raw values",
          not {"include_values", "reveal", "decrypt", "raw", "unmasked"}
          & set(signature.parameters), str(list(signature.parameters)))
    check("list_records has no parameter that could return raw values",
          not {"include_values", "include_fields", "reveal", "decrypt", "raw"}
          & set(inspect.signature(store.list_records).parameters))

    listed = store.list_records(cur, owner_user_id=USER_A)
    check("a list returns no field values at all",
          all("fields" not in r for r in listed["records"]))
    check("a list line is the stored summary",
          any(r["summary"] for r in listed["records"]))
    check("a list line carries no restricted value", SECRET not in str(listed))

    conn.close()


def stage_search() -> None:
    print("\n[search]")
    _with_keys()
    conn, cur = _connect()
    _create_passport(cur, title="Searchable passport")
    conn.commit()

    by_name = store.search_records(cur, owner_user_id=USER_A, query="okonkwo")
    check("search finds a record by an unmasked searchable field",
          any(r["title"] == "Searchable passport" for r in by_name["results"]),
          str(by_name["results"])[:200])

    by_tail = store.search_records(cur, owner_user_id=USER_A, query="0987")
    check("search finds a masked identifier by its last four digits",
          bool(by_tail["results"]), str(by_tail["results"])[:200])
    check("search never returns the restricted value", SECRET not in str(by_tail))
    matched = [m for r in by_tail["results"] for m in r["matched"]]
    check("a search match shows the masked form",
          all(SECRET not in m["value"] for m in matched), str(matched))

    check("a one-character query returns nothing rather than everything",
          store.search_records(cur, owner_user_id=USER_A, query="o")["results"] == [])

    # The most important negative in this file. `medical_condition` marks no
    # field searchable, so a diagnosis cannot reach a suggestion list, a
    # notification preview or a shoulder-surfed screen.
    medical = store.create_record(
        cur, owner_user_id=USER_A, template_key="medical_condition",
        payload={"condition.name": "Type 1 diabetes",
                 "condition.category": "CONDITION"},
        title="Health record", allow_duplicate=True)
    conn.commit()
    check("a health record is stored", medical["status"] == store.STATUS_CREATED,
          str(medical.get("errors")))
    check("a health record has an empty summary line",
          medical["record"]["summary"] == "", repr(medical["record"]["summary"]))
    found = store.search_records(cur, owner_user_id=USER_A, query="diabetes")
    check("a diagnosis is not searchable", found["results"] == [],
          str(found["results"]))
    # The value itself is stored once, in its own field row — that is the point
    # of the store. What must not happen is a *second* copy in an index column,
    # which is what the next check reads directly rather than inferring from a
    # whole-database sweep that would have to except the legitimate copy.
    cur.execute(
        "SELECT search_text, masked_text FROM private_record_fields "
        "WHERE owner_user_id = ? AND record_id = ?", (USER_A, medical["record_id"]))
    indexed = [dict(r)["search_text"] for r in cur.fetchall() or ()]
    check("no health field contributes anything to the index",
          all(text == "" for text in indexed), str(indexed))

    conn.close()


def stage_reveal() -> None:
    print("\n[reveal]")
    _with_keys()
    conn, cur = _connect()
    created = _create_passport(cur, title="Reveal")
    conn.commit()
    record_id = created["record_id"]

    denied = False
    try:
        store.reveal_field(cur, owner_user_id=USER_A, record_id=record_id,
                           field_path="issuance.document_number",
                           step_up_verified=False)
    except store.StructuredRecordDenied:
        denied = True
    conn.commit()
    check("a reveal without a step-up is refused", denied)

    cur.execute(
        "SELECT action, outcome FROM private_audit_events WHERE owner_user_id = ? "
        "ORDER BY id DESC LIMIT 3", (USER_A,))
    recent = [dict(r) for r in cur.fetchall() or ()]
    check("a refused reveal is audited as a denial",
          any(r["outcome"] == audit.OUTCOME_DENIED for r in recent), str(recent))

    import inspect
    parameter = inspect.signature(store.reveal_field).parameters["step_up_verified"]
    check("step_up_verified has no default",
          parameter.default is inspect.Parameter.empty)

    revealed = store.reveal_field(
        cur, owner_user_id=USER_A, record_id=record_id,
        field_path="issuance.document_number", step_up_verified=True)
    conn.commit()
    check("a reveal with a step-up returns the value", revealed["value"] == SECRET)

    cur.execute(
        "SELECT action, object_id FROM private_audit_events WHERE owner_user_id = ? "
        "AND action = ? ORDER BY id DESC LIMIT 1",
        (USER_A, audit.ACTION_RECORD_FIELD_REVEAL))
    row = cur.fetchone()
    check("a successful reveal writes its own audit action", row is not None)
    if row is not None:
        check("the reveal audit row names the record, not the value",
              str(dict(row)["object_id"]) == str(record_id)
              and SECRET not in str(dict(row)))

    everything = _every_stored_string(cur)
    check("revealing a value does not store a copy of it", SECRET not in everything)

    plain = store.reveal_field(cur, owner_user_id=USER_A, record_id=record_id,
                               field_path="identification.surname",
                               step_up_verified=True)
    conn.commit()
    check("an unmasked field returns without ceremony", plain["value"] == "Okonkwo")
    # Recording that as a reveal would fill the one table a member checks to see
    # who looked at their passport with entries about their surname.
    check("reading an unmasked field is not recorded as a reveal",
          plain["revealed_at"] == "")

    conn.close()


def stage_undx_projection() -> None:
    print("\n[undx projection]")
    _with_keys()
    conn, cur = _connect()
    created = _create_passport(cur, title="Assistant")
    medical = store.create_record(
        cur, owner_user_id=USER_A, template_key="medical_condition",
        payload={"condition.name": "Type 1 diabetes",
                 "condition.category": "CONDITION"},
        title="Health", allow_duplicate=True)
    conn.commit()

    projection = store.undx_record(cur, owner_user_id=USER_A,
                                   record_id=created["record_id"])
    paths = {f["path"] for f in projection["fields"]}
    check("the assistant may read unmasked fields",
          "identification.surname" in paths, str(sorted(paths)))
    check("the assistant may read the expiry date it needs for reminders",
          "issuance.expiry_date" in paths)
    # Not passed through masked. A model handed `•••• 0987` will reason about
    # "the passport ending 0987" and put it in a summary that goes to a screen,
    # a notification and a log.
    check("a masked field is dropped entirely rather than passed masked",
          "issuance.document_number" not in paths)
    check("a year-masked date of birth is dropped too",
          "identification.date_of_birth" not in paths)
    check("the projection contains no restricted value",
          SECRET not in str(projection) and SECRET[-4:] not in str(projection))
    check("the projection carries the verification state",
          projection["verification_state"] == store.VERIFICATION_USER_VERIFIED)

    check("a health record is invisible to the assistant entirely",
          store.undx_record(cur, owner_user_id=USER_A,
                            record_id=medical["record_id"]) is None)

    conn.close()


def stage_expiry_sweep() -> None:
    print("\n[expiry sweep]")
    _with_keys()
    conn, cur = _connect()

    soon = store.create_record(
        cur, owner_user_id=USER_A, template_key="certification",
        payload={"credential.name": "First aid", "credential.issuer": "Red Cross",
                 "credential.expires_on": "2026-11-01"},
        title="Expiring", allow_duplicate=True)
    never = store.create_record(
        cur, owner_user_id=USER_A, template_key="residence",
        payload={"address.line1": "1 Permanent Way", "address.country": "GB"},
        title="No expiry", allow_duplicate=True)
    conn.commit()

    due = store.expiring_records(cur, owner_user_id=USER_A, before="2026-12-31")
    ids = {r["id"] for r in due}
    check("an expiring record is found by the sweep", soon["record_id"] in ids)
    # An empty string sorts below every cutoff, so a naive `expires_at <= ?`
    # would put every permanent record in the "expiring soon" list.
    check("a record that never expires is not 'expiring soon'",
          never["record_id"] not in ids, str(sorted(ids)))
    check("the sweep is far from the cutoff for records beyond it",
          soon["record_id"] not in {
              r["id"] for r in store.expiring_records(
                  cur, owner_user_id=USER_A, before="2026-01-01")})
    check("the sweep returns no field values", all("fields" not in r for r in due))

    conn.close()


def stage_archive() -> None:
    print("\n[archive]")
    _with_keys()
    conn, cur = _connect()
    created = _create_passport(cur, title="To archive")
    conn.commit()
    record_id = created["record_id"]

    result = store.archive_record(cur, owner_user_id=USER_A, record_id=record_id)
    conn.commit()
    check("archiving succeeds", result["status"] == store.STATUS_UPDATED)
    check("an archived record leaves the active lifecycle",
          result["record"]["lifecycle_state"] == "ARCHIVED")
    check("an archived record's verification says so",
          result["record"]["verification_state"] == store.VERIFICATION_ARCHIVED)

    listed = store.list_records(cur, owner_user_id=USER_A)
    check("an archived record is out of the default list",
          record_id not in {r["id"] for r in listed["records"]})
    check("an archived record is still readable",
          store.get_record(cur, owner_user_id=USER_A, record_id=record_id,
                           audit=False) is not None)

    frozen = False
    try:
        store.update_record(cur, owner_user_id=USER_A, record_id=record_id,
                            title="edited after archive")
    except store.StructuredRecordRejected:
        frozen = True
    conn.rollback()
    check("an archived record cannot be edited", frozen)

    # There is no per-record delete, and that is the point: erasure is an
    # account-level operation with different authority, and offering a delete
    # here would let it happen accidentally while retiring one document.
    check("the store offers no delete",
          not [name for name in dir(store)
               if name.startswith(("delete", "purge", "destroy", "drop_record"))],
          str([n for n in dir(store) if n.startswith("delete")]))

    conn.close()


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------
def stage_isolation() -> None:
    print("\n[cross-account isolation]")
    _with_keys()
    conn, cur = _connect()
    mine = _create_passport(cur, owner=USER_A, title="A's passport")
    conn.commit()
    record_id = mine["record_id"]

    check("B cannot read A's record by id",
          store.get_record(cur, owner_user_id=USER_B, record_id=record_id) is None)
    check("B cannot read A's record by key",
          store.get_record(cur, owner_user_id=USER_B,
                           record_key_value=mine["record"]["record_key"]) is None)
    check("A's record is not in B's list",
          record_id not in {r["id"] for r in
                            store.list_records(cur, owner_user_id=USER_B)["records"]})
    # B has passports of its own carrying the same surname, so the property to
    # assert is not that B's search is empty — it is that every row B can reach
    # is B's own. An emptiness check would pass for the wrong reason on a fixture
    # that happened not to collide, and that is the check that quietly stops
    # testing isolation the day someone changes the sample data.
    b_search = store.search_records(cur, owner_user_id=USER_B, query="okonkwo")
    reachable = sorted({r["record_id"] for r in b_search["results"]})
    check("A's record is not in B's search", record_id not in reachable,
          str(reachable))
    check("B's search reaches something, so the check above is not vacuous",
          bool(reachable))
    owners = set()
    for reached in reachable:
        cur.execute("SELECT owner_user_id FROM private_structured_records "
                    "WHERE id = ?", (reached,))
        owners |= {int(dict(r)["owner_user_id"]) for r in cur.fetchall() or ()}
    check("every row B's search reaches belongs to B", owners == {USER_B},
          str(sorted(owners)))
    check("A's record is not in B's expiry sweep",
          record_id not in {r["id"] for r in store.expiring_records(
              cur, owner_user_id=USER_B, before="2099-01-01")})
    check("A's record is not in B's needs-review queue",
          record_id not in {r["id"] for r in store.list_records(
              cur, owner_user_id=USER_B, needs_review_only=True)["records"]})
    check("B gets nothing from the assistant projection",
          store.undx_record(cur, owner_user_id=USER_B, record_id=record_id) is None)

    for label, call in (
        ("reveal", lambda: store.reveal_field(
            cur, owner_user_id=USER_B, record_id=record_id,
            field_path="issuance.document_number", step_up_verified=True)),
        ("history", lambda: store.record_history(
            cur, owner_user_id=USER_B, record_id=record_id)),
        ("update", lambda: store.update_record(
            cur, owner_user_id=USER_B, record_id=record_id, title="taken")),
        ("archive", lambda: store.archive_record(
            cur, owner_user_id=USER_B, record_id=record_id)),
    ):
        denied = False
        try:
            call()
        except store.StructuredRecordDenied:
            denied = True
        except Exception as exc:  # noqa: BLE001 - any other failure is a failure
            denied = False
            print(f"        ({label} raised {exc.__class__.__name__})")
        conn.rollback()
        check(f"B cannot {label} A's record", denied)

    # Denial and non-existence must be the same answer, or the store becomes an
    # oracle for what another member has on file.
    missing = None
    try:
        store.record_history(cur, owner_user_id=USER_B, record_id=999_999)
    except store.StructuredRecordDenied as exc:
        missing = str(exc)
    taken = None
    try:
        store.record_history(cur, owner_user_id=USER_B, record_id=record_id)
    except store.StructuredRecordDenied as exc:
        taken = str(exc)
    check("a record that is not yours and a record that does not exist read alike",
          missing == taken and missing is not None, f"{missing!r} vs {taken!r}")

    # And the record survived every attempt.
    after = store.get_record(cur, owner_user_id=USER_A, record_id=record_id,
                             audit=False)
    check("A's record is unchanged after B's attempts",
          after is not None and after["title"] == "A's passport"
          and after["lifecycle_state"] == "ACTIVE")

    conn.close()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
_STAGES = (
    stage_create,
    stage_restricted_storage,
    stage_refuses_without_a_key,
    stage_verification_authority,
    stage_duplicates_and_idempotency,
    stage_update,
    stage_history,
    stage_read_never_decrypts,
    stage_search,
    stage_reveal,
    stage_undx_projection,
    stage_expiry_sweep,
    stage_archive,
    stage_isolation,
)


def test_structured_record_store():
    setup_environment()
    for stage in _STAGES:
        stage()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    print("PRIVATE OFFICE STRUCTURED RECORD STORE — Batch D")
    print(f"db: {_TMP_DB}")
    setup_environment()
    for stage in _STAGES:
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
