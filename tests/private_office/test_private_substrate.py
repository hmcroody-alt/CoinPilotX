"""Stages 6-13 — the private fact store and private graph, proved at runtime.

Hermetic: points ``services.db`` at a throwaway SQLite file and lets
``ensure_private_schema`` build everything it needs. Runs either way::

    python -m pytest tests/private_office/test_private_substrate.py
    python tests/private_office/test_private_substrate.py

What these tests are actually defending
---------------------------------------
Rule 13 of this mission says static code existence is not a pass, so every
claim the substrate modules make in their docstrings is asserted here against a
real database:

* Two members whose graphs are shaped identically never see each other's rows,
  and a foreign id is indistinguishable from an id that was never issued
  (Stage 14 — existence itself must not leak).
* ``INSURANCE_POLICY OWNS PERSON`` is refused at write time, so no traversal
  ever has to defend against it (Stage 9).
* A property valued at 800k in 2024 and 950k in 2026 is **not** a conflict,
  while 35% and 40% for the same period **is** (Stage 13 — the single
  distinction the contradiction engine exists to make).
* The same claim from two sources is two rows; the same claim from one source
  twice is one row with a newer observation (Stage 7 — corroboration survives
  dedupe).
* The audit table records that a policy was read and never what it said
  (Stage 18).
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_substrate_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import contradictions  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import graph  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 7001
USER_B = 7002

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


# ---------------------------------------------------------------------------
def stage_schema_bootstrap():
    """Stage 34 — the ensure is idempotent and reports its state as data."""
    print("\n[schema bootstrap]")
    conn, cur = cursor()
    schema.reset_schema_cache()
    first = schema.ensure_private_schema(cur, force=True)
    check("first ensure reports ready", first["status"] == schema.STATUS_READY,
          str(first.get("error") or first.get("missing")))
    second = schema.ensure_private_schema(cur, force=True)
    check("second ensure is still ready (idempotent)",
          second["status"] == schema.STATUS_READY)
    check("all four tables present",
          set(first["tables"]) == set(schema.TABLES),
          str(sorted(first["tables"])))
    check("no required column missing", not first["missing"], str(first["missing"]))
    conn.commit()
    conn.close()


def stage_fact_write_and_read():
    """Stage 7 — validation, normalization, dedupe, and what dedupe preserves."""
    print("\n[fact store]")
    conn, cur = cursor()

    written = facts.record_fact(
        cur, owner_user_id=USER_A, subject_type="NODE", subject_id="1",
        fact_type="estimated_value", value="$950,000", value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED, domain=model.DOMAIN_FINANCIAL,
    )
    check("a valid fact is written", written["status"] == facts.STATUS_WRITTEN, str(written))

    again = facts.record_fact(
        cur, owner_user_id=USER_A, subject_type="NODE", subject_id="1",
        fact_type="estimated_value", value="950000", value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_USER_ASSERTED, domain=model.DOMAIN_FINANCIAL,
    )
    check("same claim, same source refreshes rather than duplicating",
          again["status"] == facts.STATUS_REFRESHED and again["fact_id"] == written["fact_id"],
          str(again))
    check("'$950,000' and '950000' normalize to one value",
          again["fact_key"] == written["fact_key"])

    corroborating = facts.record_fact(
        cur, owner_user_id=USER_A, subject_type="NODE", subject_id="1",
        fact_type="estimated_value", value="950000", value_type=model.VALUE_MONEY,
        provenance_type=model.PROVENANCE_PROVIDER_ASSERTED, domain=model.DOMAIN_FINANCIAL,
    )
    check("the same claim from a second source is a second row",
          corroborating["status"] == facts.STATUS_WRITTEN
          and corroborating["fact_id"] != written["fact_id"],
          str(corroborating))

    for label, kwargs in (
        ("unknown domain", {"domain": "SPORTS"}),
        ("unknown provenance", {"provenance_type": "VIBES"}),
        ("derived provenance as an origin", {"provenance_type": model.PROVENANCE_STALE}),
        ("non-numeric money", {"value": "about 400k"}),
        ("camelCase fact type", {"fact_type": "estimatedValue"}),
        ("window that closes before it opens",
         {"valid_from": "2026-01-01T00:00:00+00:00", "valid_to": "2025-01-01T00:00:00+00:00"}),
    ):
        base = dict(
            owner_user_id=USER_A, subject_type="NODE", subject_id="1",
            fact_type="estimated_value", value="1000", value_type=model.VALUE_MONEY,
            provenance_type=model.PROVENANCE_USER_ASSERTED,
        )
        base.update(kwargs)
        try:
            facts.record_fact(cur, **base)
            check(f"rejects {label}", False, "write succeeded")
        except facts.PrivateFactRejected:
            check(f"rejects {label}", True)

    rows = facts.list_facts(cur, owner_user_id=USER_A, subject_type="NODE", subject_id="1")
    check("both surviving rows are readable", len(rows) == 2, str(len(rows)))
    check("each row carries a decoded freshness verdict",
          all("freshness" in r and "stale" in r["freshness"] for r in rows))

    ceiling = facts.list_facts(
        cur, owner_user_id=USER_A, sensitivity_ceiling=model.SENSITIVITY_INTERNAL)
    check("a ceiling below the row's sensitivity releases nothing",
          ceiling == [], str(len(ceiling)))
    check("an unrecognised ceiling releases nothing",
          facts.list_facts(cur, owner_user_id=USER_A, sensitivity_ceiling="MAXIMUM") == [])
    check("naming only unknown domains returns nothing, not everything",
          facts.list_facts(cur, owner_user_id=USER_A, domains=["SPORTS"]) == [])

    conn.commit()
    conn.close()


def stage_graph_write_rules():
    """Stages 8-10 — relations are constrained and endpoints must be the owner's."""
    print("\n[graph writer]")
    conn, cur = cursor()

    person = graph.upsert_node(
        cur, owner_user_id=USER_A, node_type=model.NODE_PERSON, external_ref="user:7001")
    business = graph.upsert_node(
        cur, owner_user_id=USER_A, node_type=model.NODE_BUSINESS, external_ref="biz:A",
        domain=model.DOMAIN_FINANCIAL)
    check("a node is created", person["status"] == graph.STATUS_CREATED, str(person))
    repeat = graph.upsert_node(
        cur, owner_user_id=USER_A, node_type=model.NODE_PERSON, external_ref="user:7001")
    check("the same external ref resolves to the same node",
          repeat["status"] == graph.STATUS_EXISTING and repeat["node_id"] == person["node_id"])

    raised = graph.upsert_node(
        cur, owner_user_id=USER_A, node_type=model.NODE_PERSON, external_ref="user:7001",
        sensitivity=model.SENSITIVITY_RESTRICTED)
    check("sensitivity may be raised on an existing node",
          raised["sensitivity"] == model.SENSITIVITY_RESTRICTED, str(raised))
    lowered = graph.upsert_node(
        cur, owner_user_id=USER_A, node_type=model.NODE_PERSON, external_ref="user:7001",
        sensitivity=model.SENSITIVITY_PUBLIC)
    check("sensitivity may NOT be lowered silently",
          lowered["sensitivity"] == model.SENSITIVITY_RESTRICTED, str(lowered))

    edge = graph.record_edge(
        cur, owner_user_id=USER_A, source=person["node_id"],
        relation_type=model.RELATION_OWNS, target=business["node_id"],
        provenance_type=model.PROVENANCE_USER_ASSERTED)
    check("PERSON OWNS BUSINESS is accepted", edge["status"] == graph.STATUS_WRITTEN, str(edge))

    property_spec = graph.NodeSpec(
        node_type=model.NODE_PROPERTY, external_ref="prop:A", domain=model.DOMAIN_FINANCIAL)
    owns_property = graph.record_edge(
        cur, owner_user_id=USER_A, source=business["node_id"],
        relation_type=model.RELATION_OWNS, target=property_spec,
        provenance_type=model.PROVENANCE_USER_ASSERTED)
    check("a NodeSpec target is resolved-or-created inside the write",
          owns_property["status"] == graph.STATUS_WRITTEN, str(owns_property))

    policy_spec = graph.NodeSpec(
        node_type=model.NODE_INSURANCE_POLICY, external_ref="pol:A", domain=model.DOMAIN_FINANCIAL)
    covered = graph.record_edge(
        cur, owner_user_id=USER_A, source=owns_property["target_node_id"],
        relation_type=model.RELATION_COVERED_BY, target=policy_spec,
        provenance_type=model.PROVENANCE_PROVIDER_ASSERTED)
    check("PROPERTY COVERED_BY INSURANCE_POLICY is accepted",
          covered["status"] == graph.STATUS_WRITTEN, str(covered))

    try:
        graph.record_edge(
            cur, owner_user_id=USER_A, source=covered["target_node_id"],
            relation_type=model.RELATION_OWNS, target=person["node_id"],
            provenance_type=model.PROVENANCE_USER_ASSERTED)
        check("rejects INSURANCE_POLICY OWNS PERSON", False, "write succeeded")
    except graph.PrivateGraphRejected:
        check("rejects INSURANCE_POLICY OWNS PERSON", True)

    try:
        graph.record_edge(
            cur, owner_user_id=USER_A, source=person["node_id"],
            relation_type=model.RELATION_OWNS, target=person["node_id"],
            provenance_type=model.PROVENANCE_USER_ASSERTED)
        check("rejects a self-loop", False, "write succeeded")
    except graph.PrivateGraphRejected:
        check("rejects a self-loop", True)

    try:
        graph.record_edge(
            cur, owner_user_id=USER_A, source=person["node_id"],
            relation_type="ENTANGLED_WITH", target=business["node_id"],
            provenance_type=model.PROVENANCE_USER_ASSERTED)
        check("rejects an unknown relation", False, "write succeeded")
    except graph.PrivateGraphRejected:
        check("rejects an unknown relation", True)

    # `edge` above was written with no validity window, i.e. "this is true
    # now". Restating it the same way is the same relationship seen again, and
    # must land on the same row — this is the ordinary caller, who has a
    # relationship to record and no date to attach to it.
    repeat_edge = graph.record_edge(
        cur, owner_user_id=USER_A, source=person["node_id"],
        relation_type=model.RELATION_OWNS, target=business["node_id"],
        provenance_type=model.PROVENANCE_USER_ASSERTED)
    check("the same undated edge from the same source refreshes rather than duplicating",
          repeat_edge["status"] == graph.STATUS_REFRESHED
          and repeat_edge["edge_id"] == edge["edge_id"], str(repeat_edge))

    # The dated path has to dedupe too, and separately: a claim about a stated
    # period is not the same claim as an undated one, so it gets its own row
    # first and then refreshes onto itself.
    dated_kwargs = dict(
        owner_user_id=USER_A, source=person["node_id"],
        relation_type=model.RELATION_OWNS, target=business["node_id"],
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        valid_from="2024-01-01T00:00:00+00:00")
    dated_edge = graph.record_edge(cur, **dated_kwargs)
    check("a dated restatement is a distinct claim, not a refresh",
          dated_edge["status"] == graph.STATUS_WRITTEN
          and dated_edge["edge_id"] != edge["edge_id"], str(dated_edge))
    repeat_dated = graph.record_edge(cur, **dated_kwargs)
    check("the same dated edge refreshes rather than duplicating",
          repeat_dated["status"] == graph.STATUS_REFRESHED
          and repeat_dated["edge_id"] == dated_edge["edge_id"], str(repeat_dated))
    check("the undated edge kept its own stored window",
          _edge_valid_from(cur, edge["edge_id"]) != "2024-01-01T00:00:00+00:00",
          _edge_valid_from(cur, edge["edge_id"]))

    conn.commit()
    conn.close()
    return None


def _edge_valid_from(cur, edge_id: int) -> str:
    cur.execute(
        f"SELECT valid_from FROM {schema.EDGES_TABLE} WHERE id = ?", (int(edge_id),))
    row = cur.fetchone()
    return str(row["valid_from"] if hasattr(row, "keys") else row[0])


def stage_owner_isolation():
    """Stage 14 — the P0 gate. A foreign id and a fictional id answer the same."""
    print("\n[owner isolation]")
    conn, cur = cursor()

    # User B gets a graph shaped exactly like User A's, so nothing below can
    # pass by accident of one member simply having no data.
    b_business = graph.upsert_node(
        cur, owner_user_id=USER_B, node_type=model.NODE_BUSINESS, external_ref="biz:B")
    b_property = graph.record_edge(
        cur, owner_user_id=USER_B, source=b_business["node_id"],
        relation_type=model.RELATION_OWNS,
        target=graph.NodeSpec(node_type=model.NODE_PROPERTY, external_ref="prop:B"),
        provenance_type=model.PROVENANCE_USER_ASSERTED)
    facts.record_fact(
        cur, owner_user_id=USER_B, subject_type="NODE",
        subject_id=str(b_property["target_node_id"]), fact_type="address",
        value="B Street 1", value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_USER_ASSERTED)
    conn.commit()

    foreign_node_id = b_property["target_node_id"]
    check("A cannot read B's node by id",
          graph.get_node(cur, owner_user_id=USER_A, node_id=foreign_node_id) is None)
    check("a foreign id and a fictional id give the identical answer",
          graph.get_node(cur, owner_user_id=USER_A, node_id=foreign_node_id)
          == graph.get_node(cur, owner_user_id=USER_A, node_id=999999))
    check("a malformed id also gives that answer",
          graph.get_node(cur, owner_user_id=USER_A, node_id="../../etc/passwd") is None)

    check("A cannot resolve B's node by external ref",
          graph.resolve_node(cur, owner_user_id=USER_A,
                             node_type=model.NODE_PROPERTY, external_ref="prop:B") is None)
    check("A traversing from B's node gets nothing",
          graph.neighbors(cur, owner_user_id=USER_A, node_id=foreign_node_id) == [])
    check("A listing nodes never sees B's",
          all(int(n["owner_user_id"]) == USER_A
              for n in graph.list_nodes(cur, owner_user_id=USER_A, limit=500)))
    check("A listing facts never sees B's",
          all(int(f["owner_user_id"]) == USER_A
              for f in facts.list_facts(cur, owner_user_id=USER_A, limit=500)))
    check("A's fact search for B's subject id returns nothing",
          facts.list_facts(cur, owner_user_id=USER_A, subject_type="NODE",
                           subject_id=str(foreign_node_id)) == [])

    # Counts are the cheapest existence oracle there is.
    a_nodes = graph.count_nodes(cur, owner_user_id=USER_A)
    b_nodes = graph.count_nodes(cur, owner_user_id=USER_B)
    cur.execute(f"SELECT COUNT(*) AS n FROM {schema.NODES_TABLE}")
    total = int(cur.fetchone()["n"])
    check("counts are per-owner, not global",
          a_nodes + b_nodes == total and a_nodes > 0 and b_nodes > 0,
          f"A={a_nodes} B={b_nodes} total={total}")

    try:
        graph.record_edge(
            cur, owner_user_id=USER_A, source=foreign_node_id,
            relation_type=model.RELATION_OWNS,
            target=graph.NodeSpec(node_type=model.NODE_ASSET, external_ref="asset:X"),
            provenance_type=model.PROVENANCE_USER_ASSERTED)
        check("A cannot build an edge onto B's node", False, "write succeeded")
    except graph.PrivateGraphRejected as exc:
        check("A cannot build an edge onto B's node", True)
        check("the refusal does not admit the node exists",
              "not found" in str(exc) and "owner" not in str(exc).lower(), str(exc))

    cur.execute(
        f"SELECT COUNT(*) AS n FROM {schema.EDGES_TABLE} e "
        f"JOIN {schema.NODES_TABLE} s ON s.id = e.source_node_id "
        f"JOIN {schema.NODES_TABLE} t ON t.id = e.target_node_id "
        f"WHERE e.owner_user_id <> s.owner_user_id OR e.owner_user_id <> t.owner_user_id")
    check("no edge in the table crosses an owner boundary",
          int(cur.fetchone()["n"]) == 0)

    conn.commit()
    conn.close()


def stage_temporal_change_is_not_a_contradiction():
    """Stage 13 — the one distinction this engine exists to make."""
    print("\n[contradictions]")
    conn, cur = cursor()
    owner = 7003

    # 800k in 2024, 950k in 2026 — the mission's explicit non-conflict.
    for value, year in (("800000", 2024), ("950000", 2026)):
        facts.record_fact(
            cur, owner_user_id=owner, subject_type="NODE", subject_id="50",
            fact_type="estimated_value", value=value, value_type=model.VALUE_MONEY,
            provenance_type=model.PROVENANCE_USER_ASSERTED,
            observed_at=f"{year}-01-01T00:00:00+00:00",
            valid_from=f"{year}-01-01T00:00:00+00:00")
    found = contradictions.detect_conflicts(cur, owner_user_id=owner, subject_id="50")
    check("a value that rose over two years is NOT a conflict",
          found == [], str(found))

    # 35% and 40% for the same period — the mission's explicit conflict.
    for value, source in (("35", model.PROVENANCE_USER_ASSERTED),
                          ("40", model.PROVENANCE_PROVIDER_ASSERTED)):
        facts.record_fact(
            cur, owner_user_id=owner, subject_type="NODE", subject_id="51",
            fact_type="ownership_share", value=value, value_type=model.VALUE_PERCENT,
            provenance_type=source,
            observed_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_to="2026-12-31T00:00:00+00:00")
    shares = contradictions.detect_conflicts(cur, owner_user_id=owner, subject_id="51")
    check("35% and 40% for one period IS a conflict", len(shares) == 1, str(len(shares)))
    if shares:
        conflict = shares[0]
        check("the conflict names both competitors",
              len(conflict["competing_fact_ids"]) == 2, str(conflict["competing_fact_ids"]))
        check("the conflict is unresolved", conflict["unresolved"] is True)
        check("the conflict carries a reason",
              conflict["reason"] == contradictions.REASON_NUMERIC, conflict["reason"])
        check("both provenances survive so the disagreement can be explained",
              {c["provenance_type"] for c in conflict["competing"]}
              == {model.PROVENANCE_USER_ASSERTED, model.PROVENANCE_PROVIDER_ASSERTED})
        check("the conflict id is deterministic",
              contradictions.detect_conflicts(
                  cur, owner_user_id=owner, subject_id="51")[0]["conflict_id"]
              == conflict["conflict_id"])

        marked = contradictions.mark_conflicts(cur, owner_user_id=owner, conflicts=shares)
        check("marking stamps both rows", marked == 2, str(marked))
        cur.execute(
            f"SELECT provenance_type, lifecycle_state FROM {schema.FACTS_TABLE} "
            f"WHERE owner_user_id = ? AND conflict_id = ?",
            (owner, conflict["conflict_id"]))
        rows = [dict(r) for r in cur.fetchall()]
        check("marking does not restamp provenance as CONFLICTING",
              all(r["provenance_type"] != model.PROVENANCE_CONFLICTING for r in rows))
        check("marking does not silently supersede either row",
              all(r["lifecycle_state"] == model.LIFECYCLE_ACTIVE for r in rows), str(rows))

    # Two renewal dates for one policy period — Stage 21's golden conflict.
    for date, source in (("2027-03-01", model.PROVENANCE_PROVIDER_ASSERTED),
                         ("2027-04-15", model.PROVENANCE_DOCUMENT_EXTRACTED)):
        facts.record_fact(
            cur, owner_user_id=owner, subject_type="NODE", subject_id="52",
            fact_type="renewal_date", value=date, value_type=model.VALUE_DATE,
            provenance_type=source,
            observed_at="2026-06-01T00:00:00+00:00",
            valid_from="2026-06-01T00:00:00+00:00")
    dates = contradictions.detect_conflicts(cur, owner_user_id=owner, subject_id="52")
    check("two renewal dates for one period conflict", len(dates) == 1, str(len(dates)))
    if dates:
        check("dates conflict with no tolerance applied",
              dates[0]["reason"] == contradictions.REASON_DATE, dates[0]["reason"])

    # An address written at two levels of detail is elaboration, not conflict.
    for value in ("12 Rue Test", "12 Rue Test, Paris"):
        facts.record_fact(
            cur, owner_user_id=owner, subject_type="NODE", subject_id="53",
            fact_type="address", value=value, value_type=model.VALUE_STRING,
            provenance_type=model.PROVENANCE_USER_ASSERTED,
            observed_at="2026-06-01T00:00:00+00:00",
            valid_from="2026-06-01T00:00:00+00:00")
    addresses = contradictions.detect_conflicts(cur, owner_user_id=owner, subject_id="53")
    check("a more detailed restatement of an address is not a conflict",
          addresses == [], str(addresses))

    # Rounding between two systems is not a disagreement.
    for value, source in (("950000", model.PROVENANCE_PROVIDER_ASSERTED),
                          ("950100", model.PROVENANCE_VERIFIED)):
        facts.record_fact(
            cur, owner_user_id=owner, subject_type="NODE", subject_id="54",
            fact_type="estimated_value", value=value, value_type=model.VALUE_MONEY,
            provenance_type=source,
            observed_at="2026-06-01T00:00:00+00:00",
            valid_from="2026-06-01T00:00:00+00:00")
    rounding = contradictions.detect_conflicts(cur, owner_user_id=owner, subject_id="54")
    check("a 0.01% difference between two systems is not a conflict",
          rounding == [], str(rounding))

    conn.commit()
    conn.close()


def stage_audit_records_access_never_content():
    """Stage 18 — identity, not content, and structurally so."""
    print("\n[audit]")
    conn, cur = cursor()

    cur.execute(f"SELECT * FROM {schema.AUDIT_TABLE} LIMIT 1")
    row = cur.fetchone()
    columns = set(dict(row).keys()) if row is not None else set()
    check("the audit table has rows to inspect", bool(columns))
    check("there is no free-text or JSON column to put a value in",
          not (columns & {"detail", "detail_json", "payload", "value", "metadata_json"}),
          str(sorted(columns)))

    written = audit.record_denied(
        cur, actor_user_id=USER_A, owner_user_id=USER_A,
        object_type="INSURANCE_POLICY", object_id="382", purpose="undx_context")
    check("a denial is recorded", written is True)

    check("a value-shaped object id is dropped rather than truncated",
          audit.safe_object_id("POLICY NUMBER GB-4471-XX/2026 £4,200") == "")
    check("an id-shaped object id survives",
          audit.safe_object_id("INSURANCE_POLICY:382") == "INSURANCE_POLICY:382")
    check("an unknown purpose becomes 'other' rather than being stored raw",
          audit.normalize_purpose("exfiltrate everything") == "other")
    check("an unknown action is refused",
          audit.record(cur, actor_user_id=USER_A, owner_user_id=USER_A,
                       action="PRIVATE_EVERYTHING_DUMP") is False)

    cur.execute(
        f"SELECT COUNT(*) AS n FROM {schema.AUDIT_TABLE} WHERE action = ?",
        (audit.ACTION_ACCESS_DENIED,))
    check("the denial is queryable by its canonical action name",
          int(cur.fetchone()["n"]) >= 1)

    cur.execute(f"SELECT * FROM {schema.AUDIT_TABLE}")
    all_rows = [dict(r) for r in cur.fetchall()]
    leaked = [
        r for r in all_rows
        if any(str(v) and ("950000" in str(v) or "Rue Test" in str(v)) for v in r.values())
    ]
    check("no recorded fact value appears anywhere in the audit table",
          leaked == [], str(leaked[:2]))
    check("every row names a known action",
          all(r["action"] in audit.ACTIONS for r in all_rows))

    conn.commit()
    conn.close()


def main() -> int:
    print("PRIVATE OFFICE SUBSTRATE — Stages 6-13")
    print(f"database: {_TMP_DB}")
    # The stages run in order against one database and are deliberately
    # order-dependent: owner isolation only means anything once both members
    # have data, and the audit checks only mean anything once something has
    # been read. That makes the suite non-reentrant, so a repeat invocation in
    # the same process starts from an empty file rather than asserting
    # "written" about rows the previous run already wrote. It is also why the
    # stages are named `stage_*` rather than `test_*`: pytest must collect
    # exactly one entry point here, or it runs every stage once standalone and
    # then a second time inside the aggregate, and the second pass sees
    # "refreshed" everywhere the first pass saw "written".
    _FAILURES.clear()
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    schema.reset_schema_cache()
    stage_schema_bootstrap()
    stage_fact_write_and_read()
    stage_graph_write_rules()
    stage_owner_isolation()
    stage_temporal_change_is_not_a_contradiction()
    stage_audit_records_access_never_content()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_substrate_suite():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
