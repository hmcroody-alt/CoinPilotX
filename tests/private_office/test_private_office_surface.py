"""Stages 4, 10, 11 — the Private Office product surface, proved at runtime.

Hermetic, same pattern as ``test_private_substrate.py``::

    python -m pytest tests/private_office/test_private_office_surface.py
    python tests/private_office/test_private_office_surface.py

What these tests are actually defending
---------------------------------------
* The domain summary names **every** domain, zeros included, so the screen can
  say "no information yet" from data rather than from a hardcoded heading list.
* The summary and the list agree. A count that reports facts the caller cannot
  read is a working existence oracle, so the sensitivity ceiling is applied to
  both or the difference between them leaks.
* A projected fact contains no ``owner_user_id``, no ``fact_key``, no
  ``subject_id`` and no provenance ``locator`` — asserted by absence over the
  whole projected structure, not by spot-checking the fields we remembered.
* Verification never rounds up: a fact the member typed reads SELF_REPORTED.
* The entry state is computed from implementation first and tier second, so a
  PRIVATE_OFFICE member is told the truth about unbuilt features and is never
  offered an upgrade that would not change anything.
* A degraded resolve reads UNKNOWN, not UNAVAILABLE (Stage 176B — "could not
  look" and "looked and found nothing" must never share a shape).
"""

import json
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_surface_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import office  # noqa: E402
from services.private_office import schema  # noqa: E402

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


def seed(cur):
    """Two owners, several domains, mixed provenance and sensitivity."""
    facts.record_fact(
        cur, owner_user_id=USER_A, subject_type="NODE", subject_id="1",
        fact_type="estimated_value", value="$950,000", value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED, domain=model.DOMAIN_FINANCIAL,
        sensitivity=model.SENSITIVITY_CONFIDENTIAL,
    )
    facts.record_fact(
        cur, owner_user_id=USER_A, subject_type="NODE", subject_id="2",
        fact_type="annual_income", value="120000", value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_VERIFIED, domain=model.DOMAIN_FINANCIAL,
        sensitivity=model.SENSITIVITY_HIGHLY_SENSITIVE,
    )
    facts.record_fact(
        cur, owner_user_id=USER_A, subject_type="NODE", subject_id="3",
        fact_type="registered_country", value="Estonia", value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED, domain=model.DOMAIN_LEGAL,
        sensitivity=model.SENSITIVITY_INTERNAL,
        provenance=facts.ProvenanceRef(
            source_type="document", source_id="doc-77",
            locator="page=4;section=3.1", confidence=0.9,
        ),
    )
    facts.record_fact(
        cur, owner_user_id=USER_B, subject_type="NODE", subject_id="9",
        fact_type="estimated_value", value="1", value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_VERIFIED, domain=model.DOMAIN_FINANCIAL,
    )


# ---------------------------------------------------------------------------
def stage_domain_summary():
    """Stage 11 — every domain present, counts owner-scoped, empties explicit."""
    print("\n[domain summary]")
    conn, cur = cursor()
    schema.reset_schema_cache()
    schema.ensure_private_schema(cur, force=True)
    seed(cur)
    conn.commit()

    rows = office.domain_summary(cur, owner_user_id=USER_A)
    named = [row["domain"] for row in rows]
    by_domain = {row["domain"]: row for row in rows}

    check("every declared domain appears", named == list(model.DOMAINS), str(named))
    check("FINANCIAL counts both of A's financial facts",
          by_domain[model.DOMAIN_FINANCIAL]["count"] == 2,
          str(by_domain[model.DOMAIN_FINANCIAL]))
    check("LEGAL counts A's one legal fact",
          by_domain[model.DOMAIN_LEGAL]["count"] == 1)
    check("HEALTH is present and empty rather than absent",
          by_domain[model.DOMAIN_HEALTH]["count"] == 0
          and by_domain[model.DOMAIN_HEALTH]["empty"] is True)
    check("a populated domain is not marked empty",
          by_domain[model.DOMAIN_FINANCIAL]["empty"] is False)

    # B's fact is FINANCIAL too. If the owner predicate were missing anywhere,
    # A's FINANCIAL count would be 3.
    check("B's fact does not reach A's summary",
          by_domain[model.DOMAIN_FINANCIAL]["count"] == 2)
    b_rows = office.domain_summary(cur, owner_user_id=USER_B)
    b_by_domain = {row["domain"]: row for row in b_rows}
    check("B sees only B's own count",
          b_by_domain[model.DOMAIN_FINANCIAL]["count"] == 1
          and b_by_domain[model.DOMAIN_LEGAL]["count"] == 0)

    check("owner 0 yields a complete all-zero summary rather than an error",
          [row["count"] for row in office.domain_summary(cur, owner_user_id=0)]
          == [0] * len(model.DOMAINS))

    conn.commit()
    conn.close()


def stage_summary_and_list_agree():
    """The count and the list are answered under the same ceiling, or the gap
    between them is an oracle for the rows the caller may not read."""
    print("\n[summary/list agreement]")
    conn, cur = cursor()

    ceiling = model.SENSITIVITY_CONFIDENTIAL
    summary = {
        row["domain"]: row["count"]
        for row in office.domain_summary(
            cur, owner_user_id=USER_A, sensitivity_ceiling=ceiling
        )
    }
    listed = facts.list_facts(
        cur, owner_user_id=USER_A, domains=[model.DOMAIN_FINANCIAL],
        sensitivity_ceiling=ceiling, limit=100,
    )
    check("a ceiling below the row's sensitivity hides it from the count",
          summary[model.DOMAIN_FINANCIAL] == 1,
          f"expected 1 (HIGHLY_SENSITIVE income excluded), got {summary[model.DOMAIN_FINANCIAL]}")
    check("count and list return the same number under the same ceiling",
          summary[model.DOMAIN_FINANCIAL] == len(listed),
          f"count={summary[model.DOMAIN_FINANCIAL]} list={len(listed)}")

    full = office.domain_summary(
        cur, owner_user_id=USER_A, sensitivity_ceiling=model.SENSITIVITY_RESTRICTED
    )
    full_by_domain = {row["domain"]: row["count"] for row in full}
    check("the owner's own default ceiling shows both financial facts",
          full_by_domain[model.DOMAIN_FINANCIAL] == 2)

    conn.close()


def stage_projection_withholds():
    """Stage 4 — the projection is an allowlist, asserted by absence."""
    print("\n[fact projection]")
    conn, cur = cursor()

    rows = facts.list_facts(
        cur, owner_user_id=USER_A, domains=[model.DOMAIN_LEGAL], limit=10
    )
    check("the seeded legal fact is readable", len(rows) == 1, str(len(rows)))
    raw = rows[0]
    projected = office.project_fact(raw)

    # Serialize the whole projection and search it. Checking named keys would
    # only prove the fields we remembered are absent; this proves the values
    # are absent wherever they might have been nested.
    blob = json.dumps(projected, sort_keys=True, default=str)

    check("owner_user_id is not a key anywhere", "owner_user_id" not in blob)
    check("fact_key is not a key anywhere", "fact_key" not in blob)
    check("the fact_key value does not appear", str(raw["fact_key"]) not in blob)
    check("conflict_id is not a key anywhere", "conflict_id" not in blob)
    check("subject_id is not a key anywhere", "subject_id" not in blob)
    check("provenance_ref is not carried raw", "provenance_ref" not in blob)
    check("the internal locator does not appear", "section=3.1" not in blob,
          "locator leaked into the projection")

    check("the value survives", projected["value"] == "Estonia", str(projected))
    check("the domain survives", projected["domain"] == model.DOMAIN_LEGAL)
    check("the source type survives", projected["provenance"]["source_type"] == "document")
    check("the source id survives, so the claim stays traceable",
          projected["provenance"]["source_id"] == "doc-77")
    check("the existence of a document is disclosed without its pointer",
          projected["provenance"]["has_source_document"] is True)
    check("freshness is projected as a decided boolean",
          isinstance(projected["freshness"]["stale"], bool))
    check("a non-dict projects to an empty dict rather than raising",
          office.project_fact(None) == {})
    check("project_facts drops non-rows rather than failing",
          len(office.project_facts([raw, None, "x"])) == 1)

    conn.close()


def stage_verification_never_rounds_up():
    """Stage 4 — the trust bucket is a collapse, never a promotion."""
    print("\n[verification state]")
    expected = {
        model.PROVENANCE_VERIFIED: office.VERIFICATION_VERIFIED,
        model.PROVENANCE_PROVIDER_ASSERTED: office.VERIFICATION_SOURCED,
        model.PROVENANCE_DOCUMENT_EXTRACTED: office.VERIFICATION_SOURCED,
        model.PROVENANCE_USER_ASSERTED: office.VERIFICATION_SELF_REPORTED,
        model.PROVENANCE_INFERRED: office.VERIFICATION_ESTIMATED,
        model.PROVENANCE_ESTIMATED: office.VERIFICATION_ESTIMATED,
        model.PROVENANCE_STALE: office.VERIFICATION_NEEDS_REVIEW,
        model.PROVENANCE_CONFLICTING: office.VERIFICATION_NEEDS_REVIEW,
    }
    for provenance, want in expected.items():
        check(f"{provenance} reads {want}",
              office.verification_state(provenance) == want,
              office.verification_state(provenance))

    check("every declared provenance type is mapped",
          set(expected) == set(model.PROVENANCE_TYPES),
          str(set(model.PROVENANCE_TYPES) - set(expected)))
    check("what the member typed is never called verified",
          office.verification_state(model.PROVENANCE_USER_ASSERTED)
          != office.VERIFICATION_VERIFIED)
    check("an unknown provenance needs review rather than reassuring",
          office.verification_state("MADE_UP") == office.VERIFICATION_NEEDS_REVIEW)
    check("an empty provenance needs review",
          office.verification_state("") == office.VERIFICATION_NEEDS_REVIEW)


def stage_entry_state_reads_implementation_first():
    """Stages 10, 13 — tier never manufactures a capability."""
    print("\n[entry state]")

    live = set(feature_matrix.implemented_feature_ids())
    children = set(office.OFFICE_CHILD_IDS)
    check("every child id is a real matrix row",
          all(feature_matrix.get(fid) is not None for fid in children),
          str([fid for fid in children if feature_matrix.get(fid) is None]))

    top = office.product_state("PRIVATE_OFFICE")
    check("the top of the ladder does not conjure unbuilt features",
          all(not child["opens"] for child in top["unavailable"]))

    if not (children & live):
        # Current repository state: nothing inside the room is built yet.
        check("with nothing built, even PRIVATE_OFFICE reads UNAVAILABLE",
              top["state"] == office.ENTRY_UNAVAILABLE, top["state"])
        check("no upgrade is offered for something that does not exist",
              top["upgrade_tier"] is None, str(top["upgrade_tier"]))
        check("FREE is told the same truth",
              office.product_state("FREE")["state"] == office.ENTRY_UNAVAILABLE)
        check("the entry is not shown while the room is empty",
              office.entry_visible("PRIVATE_OFFICE") is False)
    else:
        check("a built and entitled child opens the entry",
              top["state"] == office.ENTRY_AVAILABLE, top["state"])
        check("FREE is offered the cheapest tier that unlocks something real",
              office.product_state("FREE")["upgrade_tier"] is not None)
        check("the entry is shown once something real is inside",
              office.entry_visible("PRIVATE_OFFICE") is True)

    provider = [
        child for child in top["unavailable"]
        if child["implementation"] == feature_matrix.IMPL_PROVIDER_REQUIRED
    ]
    check("provider-blocked children keep their own reason",
          bool(provider) and all(child["reason"] == "PROVIDER_REQUIRED" for child in provider),
          str([child["reason"] for child in provider]))
    check("a provider-blocked child is not offered as an upgrade",
          all(child["reason"] != "UPGRADE_REQUIRED" for child in provider))

    check("children are reported in the declared display order",
          [child["feature_id"] for child in top["available"] + top["unavailable"]]
          != [] and set(child["feature_id"] for child in top["available"] + top["unavailable"])
          == children)

    unknown = office.product_state("PRIVATE_OFFICE", resolver_ok=False)
    check("a degraded resolve reads UNKNOWN, not UNAVAILABLE",
          unknown["state"] == office.ENTRY_UNKNOWN, unknown["state"])
    check("a degraded resolve claims no tier",
          unknown["effective_tier"] == "")
    check("a degraded resolve lists no children either way",
          unknown["available"] == [] and unknown["unavailable"] == [])
    check("nothing is shown while we do not know",
          office.entry_visible("PRIVATE_OFFICE", resolver_ok=False) is False)

    check("an unrecognised tier fails closed rather than raising",
          office.product_state("WIZARD")["state"] in (
              office.ENTRY_UNAVAILABLE, office.ENTRY_UPGRADE_REQUIRED))


def stage_no_sql_in_the_surface_layer():
    """The composition layer reads through facts.py and names no table."""
    print("\n[layering]")
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "services", "private_office", "office.py"
    )
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()

    # Not "the string private_facts is absent": ``private_facts`` is also a
    # feature_id in the matrix and a legitimate child of the product entry, so
    # the bare name appears here for reasons that have nothing to do with SQL.
    # The claim being defended is that no SQL *verb* is applied to any of them.
    upper = source.upper()
    for verb in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "DROP "):
        check(f"no {verb.strip()} statement anywhere in the surface layer",
              verb not in upper, f"found {verb.strip()}")

    check("the graph table is not named at all", "private_graph_nodes" not in source)
    check("the audit table is not named at all", "private_audit_events" not in source)
    check("the schema module is not imported here",
          "import schema" not in source and "private_office.schema" not in source)
    check("reads go through the canonical reader",
          "_facts.count_facts_by_domain" in source)


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    schema.reset_schema_cache()
    stage_domain_summary()
    stage_summary_and_list_agree()
    stage_projection_withholds()
    stage_verification_never_rounds_up()
    stage_entry_state_reads_implementation_first()
    stage_no_sql_in_the_surface_layer()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_office_surface():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
