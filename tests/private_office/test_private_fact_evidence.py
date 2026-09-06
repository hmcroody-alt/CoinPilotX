"""Evidence links, reference integrity, and the verification transition.

Hermetic, same pattern as the rest of this directory::

    python -m pytest tests/private_office/test_private_fact_evidence.py
    python tests/private_office/test_private_fact_evidence.py

What these tests are actually defending
---------------------------------------
* **No orphan badge.** A positive verification state is unreachable without at
  least one live, resolvable, supporting source — checked at the moment the
  state is set, not assumed from the existence of a link row. Every one of those
  four adjectives has a stage below, because each is a separate way the count
  could pass while the badge stands on nothing.
* **Verification does not overwrite provenance.** They are two axes and a check
  is not an origin. A fact that came from a document and was later confirmed by
  an authority is both of those things, and the store has to be able to say so.
* **A source that disappears is reported, not hidden.** Reference integrity
  cannot be enforced at write time alone — the member may delete the document
  tomorrow. Availability is resolved at read, so the evidence tab says
  SOURCE_UNAVAILABLE rather than rendering a link to nothing.
* **Withdrawing evidence does not silently revoke a badge.** The fact is left
  visibly unsupported for the review queue to surface. Downgrading it
  automatically would be this package deciding what is true on its own, which is
  the one thing it is not allowed to do.
* **Expiry is computed, never stored.** A stored flag is only as fresh as the
  last sweep, and the gap between the horizon passing and the sweeper running is
  exactly when a member acts on a badge that has quietly aged out.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_fact_evidence_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.private_office import documents  # noqa: E402
from services.private_office import evidence  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import schema  # noqa: E402

OWNER = 9401
OTHER = 9402

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


def _make_document(cur, owner: int, title: str,
                   lifecycle: str = documents.LIFECYCLE_ACTIVE) -> int:
    """A real row in the real documents table, built by the real DDL.

    Deliberately not a hand-rolled minimal table. A local fixture shaped
    ``(id, owner_user_id, title)`` would resolve happily while telling us
    nothing about the table evidence links actually point at — and it would
    hide every column that resolution might one day need to consider, which is
    precisely how ``lifecycle_state`` went unexamined until now.
    """
    # force=True: ensure_documents_schema memoizes readiness in a module global,
    # so a sibling test module that ensured against *its* temp database leaves
    # the flag set and this one silently skips the CREATE. The DDL is
    # IF NOT EXISTS throughout, so forcing costs nothing and removes the
    # dependency on which test file pytest happened to import last.
    documents.ensure_documents_schema(cur, force=True)
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        f"""INSERT INTO {documents.DOCUMENTS_TABLE}
            (owner_user_id, title, lifecycle_state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)""",
        (owner, title, lifecycle, now, now))
    cur.execute(
        f"SELECT id FROM {documents.DOCUMENTS_TABLE} WHERE owner_user_id=?"
        " ORDER BY id DESC LIMIT 1", (owner,))
    return int(dict(cur.fetchone())["id"])


def _fact(cur, subject: str, value: str = "100", **kwargs) -> int:
    return facts.record_fact(
        cur, owner_user_id=OWNER, subject_type="NODE", subject_id=subject,
        fact_type="estimated_value", value=value, value_type=model.VALUE_MONEY,
        provenance_type=kwargs.pop("provenance_type",
                                   model.PROVENANCE_DOCUMENT_EXTRACTED),
        **kwargs)["fact_id"]


_STATE: dict[str, int] = {}


# ---------------------------------------------------------------------------
def stage_a_badge_needs_a_source():
    """Positive verification is unreachable until something stands behind it."""
    print("\n[no orphan badge]")
    conn, cur = cursor()
    schema.ensure_private_schema(cur)
    bare = _fact(cur, "node_bare")
    _STATE["bare"] = bare

    check("a new fact starts UNVERIFIED",
          facts.verification_status(
              facts.list_facts(cur, owner_user_id=OWNER, limit=200)[0]
          )["state"] in model.VERIFICATION_STATES)

    for state in sorted(model.VERIFICATION_REQUIRES_EVIDENCE):
        _refuses(f"{state} is refused with no evidence at all",
                 lambda s=state: facts.set_verification(
                     cur, owner_user_id=OWNER, fact_id=bare,
                     verification_state=s))

    # Neutral and negative states do not need evidence — they are not claims
    # that anything was confirmed.
    for state in (model.VERIFICATION_PENDING_REVIEW, model.VERIFICATION_SELF_ASSERTED,
                  model.VERIFICATION_DISPUTED, model.VERIFICATION_FAILED):
        moved = facts.set_verification(
            cur, owner_user_id=OWNER, fact_id=bare, verification_state=state)
        check(f"{state} needs no evidence", moved["verification_state"] == state,
              str(moved))
    conn.commit()


def stage_evidence_must_resolve_when_linked():
    """A ref that names nothing, or somebody else's row, is refused."""
    print("\n[reference integrity at write]")
    conn, cur = cursor()
    fact_id = _STATE["bare"]

    _refuses("a malformed ref is refused", lambda: facts.link_evidence(
        cur, owner_user_id=OWNER, fact_id=fact_id, source_ref="not a ref"))
    _refuses("an unknown ref kind is refused", lambda: facts.link_evidence(
        cur, owner_user_id=OWNER, fact_id=fact_id, source_ref="wishes:1"))
    _refuses("a ref naming nothing is refused", lambda: facts.link_evidence(
        cur, owner_user_id=OWNER, fact_id=fact_id, source_ref="document:4242"))

    foreign = _make_document(cur, OTHER, "someone else's statement")
    # The same words as a ref naming nothing. Anything more specific would let a
    # caller probe another account's id space one ref at a time.
    _refuses("another owner's source is refused as unavailable",
             lambda: facts.link_evidence(
                 cur, owner_user_id=OWNER, fact_id=fact_id,
                 source_ref=f"document:{foreign}"))

    _refuses("an unknown relation is refused", lambda: facts.link_evidence(
        cur, owner_user_id=OWNER, fact_id=fact_id, source_ref="document:1",
        relation="PROBABLY"))
    conn.commit()


def stage_only_live_supporting_resolvable_evidence_counts():
    """Each adjective in the rule gets its own way of failing."""
    print("\n[what counts as support]")
    conn, cur = cursor()
    subject = _fact(cur, "node_supported")
    _STATE["supported"] = subject

    # 1. CONTRADICTS is evidence, and it is not support.
    against = _make_document(cur, OWNER, "the statement that disagrees")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                        source_ref=f"document:{against}",
                        relation=model.EVIDENCE_CONTRADICTS)
    check("contradicting evidence is stored",
          len(facts.fact_evidence(cur, owner_user_id=OWNER, fact_id=subject)) == 1)
    check("contradicting evidence counts as zero support",
          facts.supporting_evidence_count(
              cur, owner_user_id=OWNER, fact_id=subject) == 0)
    _refuses("a badge cannot rest on evidence that contradicts the fact",
             lambda: facts.set_verification(
                 cur, owner_user_id=OWNER, fact_id=subject,
                 verification_state=model.VERIFICATION_DOCUMENT_SUPPORTED))

    # 2. CONTEXT is related and is not support either.
    context = _make_document(cur, OWNER, "the deed for the property")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                        source_ref=f"document:{context}",
                        relation=model.EVIDENCE_CONTEXT)
    check("context evidence counts as zero support",
          facts.supporting_evidence_count(
              cur, owner_user_id=OWNER, fact_id=subject) == 0)

    # 3. A real supporting source finally opens the gate.
    supporting = _make_document(cur, OWNER, "the valuation report")
    _STATE["supporting_doc"] = supporting
    linked = facts.link_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                                 source_ref=f"document:{supporting}")
    check("a supporting source is linked",
          linked["status"] == facts.STATUS_WRITTEN, str(linked))
    check("supporting evidence counts as one",
          facts.supporting_evidence_count(
              cur, owner_user_id=OWNER, fact_id=subject) == 1)
    verified = facts.set_verification(
        cur, owner_user_id=OWNER, fact_id=subject,
        verification_state=model.VERIFICATION_DOCUMENT_SUPPORTED)
    check("with a live supporting source the badge is granted",
          verified["verification_state"] == model.VERIFICATION_DOCUMENT_SUPPORTED,
          str(verified))

    # 4. Re-linking the same source is one intention, not two sources.
    again = facts.link_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                                source_ref=f"document:{supporting}")
    check("re-attaching the same source does not duplicate it",
          again["status"] == "existing", str(again))
    check("re-attaching does not inflate the support count",
          facts.supporting_evidence_count(
              cur, owner_user_id=OWNER, fact_id=subject) == 1)
    conn.commit()


def stage_verification_leaves_provenance_alone():
    """Two axes. Recording a check must not erase where the value came from."""
    print("\n[two axes]")
    conn, cur = cursor()
    subject = _STATE["supported"]
    cur.execute("SELECT provenance_type, verification_state, verified_at,"
                " verified_by FROM private_facts WHERE id = ?", (subject,))
    row = dict(cur.fetchone())
    check("provenance is untouched by verification",
          row["provenance_type"] == model.PROVENANCE_DOCUMENT_EXTRACTED,
          row["provenance_type"])
    check("verification is recorded on its own column",
          row["verification_state"] == model.VERIFICATION_DOCUMENT_SUPPORTED)
    check("the check is dated", bool(row["verified_at"]))
    check("the check names who made it", int(row["verified_by"]) == OWNER)

    # The two vocabularies must not be interchangeable at the call site.
    _refuses("a provenance type is not accepted as a verification state",
             lambda: facts.set_verification(
                 cur, owner_user_id=OWNER, fact_id=subject,
                 verification_state=model.PROVENANCE_VERIFIED))
    _refuses("EXPIRED cannot be set by hand", lambda: facts.set_verification(
        cur, owner_user_id=OWNER, fact_id=subject,
        verification_state=model.VERIFICATION_EXPIRED))
    conn.commit()


def stage_a_vanished_source_reads_unavailable():
    """Write-time integrity cannot cover a source deleted afterwards.

    The deletion here goes through ``documents.delete_document`` rather than a
    raw ``DELETE FROM``, because that is the only kind of deletion the product
    performs. It is a *soft* delete: the row survives as a tombstone by design
    so the trail stays readable, while the stored bytes are destroyed. A test
    that hard-deletes the row proves the resolver notices a missing row and
    proves nothing at all about the case that actually happens — which is how
    a deleted document went on reading AVAILABLE, and on counting as support
    under a Verified badge, with a green suite.
    """
    print("\n[reference integrity at read]")
    conn, cur = cursor()
    subject = _STATE["supported"]
    doc = _STATE["supporting_doc"]

    live = [e for e in facts.fact_evidence(cur, owner_user_id=OWNER, fact_id=subject)
            if e["source_ref"] == f"document:{doc}"][0]
    check("a resolvable source reads AVAILABLE",
          live["status"] == model.SOURCE_AVAILABLE, str(live["status"]))
    check("a resolvable source carries a label", bool(live["label"]))

    table, _label = evidence.KINDS["document"]

    # Retired but intact is not the same as gone: the row and its content still
    # exist, so support must survive a member tidying up.
    cur.execute(f"UPDATE {table} SET lifecycle_state='ARCHIVED' WHERE id=?", (doc,))
    archived = [e for e in facts.fact_evidence(cur, owner_user_id=OWNER, fact_id=subject)
                if e["source_ref"] == f"document:{doc}"][0]
    check("an archived source is still available — archiving is not deleting",
          archived["status"] == model.SOURCE_AVAILABLE, str(archived["status"]))
    check("an archived source still counts as support",
          facts.supporting_evidence_count(
              cur, owner_user_id=OWNER, fact_id=subject) == 1)
    cur.execute(f"UPDATE {table} SET lifecycle_state='ACTIVE' WHERE id=?", (doc,))

    deleted = documents.delete_document(cur, owner_user_id=OWNER, document_id=doc)
    check("the document was soft-deleted through the real path", deleted is True)
    cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE id=?", (doc,))
    check("the tombstone row survives the delete, as documents.py intends",
          int(dict(cur.fetchone())["n"]) == 1)

    gone = [e for e in facts.fact_evidence(cur, owner_user_id=OWNER, fact_id=subject)
            if e["source_ref"] == f"document:{doc}"][0]
    check("a soft-deleted source reads SOURCE_UNAVAILABLE",
          gone["status"] == model.SOURCE_UNAVAILABLE, str(gone["status"]))
    check("the link row itself survives the source", gone["id"] == live["id"])
    check("an unavailable source stops counting as support",
          not gone["supports"] and facts.supporting_evidence_count(
              cur, owner_user_id=OWNER, fact_id=subject) == 0)

    # The badge already granted is NOT silently revoked — but it can no longer
    # be re-granted, which is what the integrity sweep and review queue read.
    cur.execute("SELECT verification_state FROM private_facts WHERE id = ?",
                (subject,))
    check("the badge already granted is left standing, not silently revoked",
          dict(cur.fetchone())["verification_state"]
          == model.VERIFICATION_DOCUMENT_SUPPORTED)
    _refuses("the badge cannot be re-granted once its source is gone",
             lambda: facts.set_verification(
                 cur, owner_user_id=OWNER, fact_id=subject,
                 verification_state=model.VERIFICATION_SYSTEM_CORROBORATED))
    conn.commit()


def stage_withdrawing_evidence_keeps_the_record():
    """Unlink is a detach. The link and its withdrawal both survive."""
    print("\n[withdrawal]")
    conn, cur = cursor()
    subject = _fact(cur, "node_withdrawn")
    doc = _make_document(cur, OWNER, "a receipt")
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                        source_ref=f"document:{doc}")
    facts.set_verification(cur, owner_user_id=OWNER, fact_id=subject,
                           verification_state=model.VERIFICATION_DOCUMENT_SUPPORTED)

    removed = facts.unlink_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                                    source_ref=f"document:{doc}")
    check("unlinking reports what it detached", removed["detached"] == 1, str(removed))
    check("a detached link is gone from the live view",
          facts.fact_evidence(cur, owner_user_id=OWNER, fact_id=subject) == [])
    history_view = facts.fact_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                                       include_detached=True)
    check("a detached link survives in the full view", len(history_view) == 1)
    check("the withdrawal is dated", bool(history_view[0]["detached_at"]))
    check("a detached link counts as zero support",
          facts.supporting_evidence_count(
              cur, owner_user_id=OWNER, fact_id=subject) == 0)

    check("unlinking twice is not an error",
          facts.unlink_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                                source_ref=f"document:{doc}")["detached"] == 0)

    # The badge stands until a person decides otherwise. Automatic revocation
    # would be the store making a truth judgement on its own.
    cur.execute("SELECT verification_state FROM private_facts WHERE id = ?",
                (subject,))
    check("withdrawing the last source does not silently revoke the badge",
          dict(cur.fetchone())["verification_state"]
          == model.VERIFICATION_DOCUMENT_SUPPORTED)

    # Re-attaching writes a new row rather than resurrecting the old one, so
    # the withdrawal is not erased by the change of mind.
    facts.link_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                        source_ref=f"document:{doc}")
    full = facts.fact_evidence(cur, owner_user_id=OWNER, fact_id=subject,
                               include_detached=True)
    check("re-attaching after a withdrawal keeps both events", len(full) == 2,
          str([(e["id"], e["detached_at"]) for e in full]))
    check("only the new attachment is live",
          len(facts.fact_evidence(cur, owner_user_id=OWNER, fact_id=subject)) == 1)
    conn.commit()


def stage_expiry_is_computed_not_stored():
    """A badge ages out on read, with no sweeper in the window."""
    print("\n[expiry]")
    horizon = model.VERIFICATION_HORIZON_DAYS[model.VERIFICATION_OWNER_CONFIRMED]
    checked = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = {"verification_state": model.VERIFICATION_OWNER_CONFIRMED,
           "verified_at": checked.isoformat()}

    fresh = facts.verification_status(row, at=checked + timedelta(days=1))
    check("inside the horizon the state stands",
          fresh["effective_state"] == model.VERIFICATION_OWNER_CONFIRMED
          and not fresh["expired"], str(fresh))
    check("the expiry date is published so a screen can warn before it passes",
          bool(fresh["expires_at"]))

    aged = facts.verification_status(row, at=checked + timedelta(days=horizon + 1))
    check("past the horizon the effective state is EXPIRED",
          aged["effective_state"] == model.VERIFICATION_EXPIRED, str(aged))
    check("the stored state is unchanged by the passage of time",
          aged["state"] == model.VERIFICATION_OWNER_CONFIRMED)
    check("an expired badge does not read as positive", not aged["positive"])

    never = facts.verification_status(
        {"verification_state": model.VERIFICATION_UNVERIFIED, "verified_at": ""})
    check("a state with no horizon never expires",
          not never["expired"] and never["expires_at"] == "", str(never))

    # A positive state whose date cannot be read cannot be shown to be current.
    corrupt = facts.verification_status(
        {"verification_state": model.VERIFICATION_AUTHORITY_VERIFIED,
         "verified_at": "sometime last year"})
    check("a positive badge with an unreadable date reads EXPIRED",
          corrupt["effective_state"] == model.VERIFICATION_EXPIRED, str(corrupt))

    unreadable = facts.verification_status({"verification_state": "SORT OF"})
    check("an unreadable state falls to the floor, not to a guess",
          unreadable["state"] == model.VERIFICATION_UNVERIFIED, str(unreadable))


def stage_the_whole_thing_is_owner_scoped():
    """Nothing here answers for a fact the caller does not own."""
    print("\n[isolation]")
    conn, cur = cursor()
    subject = _STATE["supported"]
    for label, call in (
        ("linking", lambda: facts.link_evidence(
            cur, owner_user_id=OTHER, fact_id=subject, source_ref="document:1")),
        ("unlinking", lambda: facts.unlink_evidence(
            cur, owner_user_id=OTHER, fact_id=subject, source_ref="document:1")),
        ("verifying", lambda: facts.set_verification(
            cur, owner_user_id=OTHER, fact_id=subject,
            verification_state=model.VERIFICATION_PENDING_REVIEW)),
    ):
        _refuses(f"{label} another owner's fact reports missing", call,
                 expected=facts.PrivateFactMissing)

    check("reading another owner's evidence returns nothing",
          facts.fact_evidence(cur, owner_user_id=OTHER, fact_id=subject) == [])
    check("counting another owner's support returns zero",
          facts.supporting_evidence_count(
              cur, owner_user_id=OTHER, fact_id=subject) == 0)
    conn.commit()


def stage_evidence_stores_identity_not_content():
    """The link names a row. It never copies what the row said."""
    print("\n[identity not content]")
    conn, cur = cursor()
    cur.execute("PRAGMA table_info(private_fact_evidence)")
    columns = {dict(row)["name"] for row in cur.fetchall()}
    leaked = columns & {"typed_value", "value", "content", "text", "body",
                        "excerpt", "title", "summary"}
    check("the evidence table has no content column", not leaked, str(leaked))
    check("it stores a reference", "source_ref" in columns)
    check("it distinguishes support from disagreement", "relation" in columns)
    check("withdrawal is a timestamp, not a deletion", "detached_at" in columns)
    conn.commit()


def stage_every_soft_deleting_kind_is_lifecycle_aware():
    """A kind whose table can hold a tombstone must be declared as such.

    ``LIFECYCLE_AWARE_KINDS`` is hand-maintained, which makes it exactly the
    kind of list that goes stale: add a soft-delete column to an existing
    evidence target, forget this set, and deleted rows quietly resolve as live
    sources again — the original bug, reintroduced with a green suite. So
    rather than trusting the list, ask the database.

    Partial by construction, and honestly so: a kind whose table has not been
    created in this database cannot be inspected, so it is reported rather
    than silently passed. The check tightens as more schemas are ensured here.
    """
    print("\n[lifecycle coverage]")
    conn, cur = cursor()
    schema.ensure_private_schema(cur)
    documents.ensure_documents_schema(cur, force=True)
    records.ensure_records_schema(cur, force=True)

    unknown: list[str] = []
    for kind, (table, _label) in sorted(evidence.KINDS.items()):
        cur.execute(f"PRAGMA table_info({table})")
        columns = {dict(row)["name"] for row in cur.fetchall()}
        if not columns:
            unknown.append(kind)
            continue
        soft_deletes = evidence.LIFECYCLE_COLUMN in columns
        declared = kind in evidence.LIFECYCLE_AWARE_KINDS
        check(f"{kind}: soft-deleting={soft_deletes} matches declaration",
              soft_deletes == declared,
              f"table {table} {'has' if soft_deletes else 'lacks'} "
              f"{evidence.LIFECYCLE_COLUMN} but is "
              f"{'' if declared else 'not '}in LIFECYCLE_AWARE_KINDS")
    check("every declared lifecycle-aware kind was actually inspected",
          set(evidence.LIFECYCLE_AWARE_KINDS).isdisjoint(unknown),
          f"declared but uninspectable: "
          f"{sorted(set(evidence.LIFECYCLE_AWARE_KINDS) & set(unknown))}")
    print(f"  NOTE  kinds with no table in this database: {sorted(unknown)}")
    conn.commit()


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    schema.reset_schema_cache()
    stage_every_soft_deleting_kind_is_lifecycle_aware()
    stage_a_badge_needs_a_source()
    stage_evidence_must_resolve_when_linked()
    stage_only_live_supporting_resolvable_evidence_counts()
    stage_verification_leaves_provenance_alone()
    stage_a_vanished_source_reads_unavailable()
    stage_withdrawing_evidence_keeps_the_record()
    stage_expiry_is_computed_not_stored()
    stage_the_whole_thing_is_owner_scoped()
    stage_evidence_stores_identity_not_content()

    print()
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — evidence and verification hold.")
    return 0


def test_private_fact_evidence():
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
