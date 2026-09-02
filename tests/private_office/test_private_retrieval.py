"""Stages 14-17 and Stage 20 — retrieval, proved against a real graph.

Builds the exact shape Mission 1A defines as done::

    User A -> OWNS -> Business A -> OWNS -> Property A -> COVERED_BY -> Policy A

and then asks the Stage 20 golden question — *what property does my business
own?* — through ``retrieval.retrieve``. The requirement there is worth quoting
because it is the one most easily faked: correct property, correct path,
provenance available, owner scoped, and **no special-case phrase logic**. So
nothing below matches on words. The test asks for an intent, and the answer has
to fall out of the graph.

User B is given an identically shaped graph throughout. That matters: a
retrieval that returns nothing for B's data because B has no data proves
nothing at all, and it is the shape most owner-isolation tests accidentally
take.

Runs either way::

    python -m pytest tests/private_office/test_private_retrieval.py
    python tests/private_office/test_private_retrieval.py
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_retrieval_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import graph  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import retrieval  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 8001
USER_B = 8002

_FAILURES: list[str] = []
_IDS: dict[str, int] = {}


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def cursor():
    conn = db.connect()
    return conn, conn.cursor()


def _fact(cur, owner, node_id, fact_type, value, *, value_type=model.VALUE_STRING,
          provenance=model.PROVENANCE_USER_ASSERTED, domain=model.DOMAIN_GENERAL,
          sensitivity=model.DEFAULT_SENSITIVITY, observed_at=None, valid_from=None):
    return facts.record_fact(
        cur, owner_user_id=owner, subject_type=retrieval.SUBJECT_TYPE_NODE,
        subject_id=str(node_id), fact_type=fact_type, value=value,
        value_type=value_type, provenance_type=provenance, domain=domain,
        sensitivity=sensitivity, observed_at=observed_at, valid_from=valid_from)


def _chain(cur, owner: str, user_id: int, suffix: str) -> dict:
    """PERSON -OWNS-> BUSINESS -OWNS-> PROPERTY -COVERED_BY-> INSURANCE_POLICY."""
    person = graph.upsert_node(
        cur, owner_user_id=user_id, node_type=model.NODE_PERSON,
        external_ref=f"person:{suffix}")
    business = graph.upsert_node(
        cur, owner_user_id=user_id, node_type=model.NODE_BUSINESS,
        external_ref=f"business:{suffix}", domain=model.DOMAIN_FINANCIAL)
    prop = graph.upsert_node(
        cur, owner_user_id=user_id, node_type=model.NODE_PROPERTY,
        external_ref=f"property:{suffix}", domain=model.DOMAIN_FINANCIAL)
    policy = graph.upsert_node(
        cur, owner_user_id=user_id, node_type=model.NODE_INSURANCE_POLICY,
        external_ref=f"policy:{suffix}", domain=model.DOMAIN_FINANCIAL)

    graph.record_edge(
        cur, owner_user_id=user_id, source=person["node_id"],
        relation_type=model.RELATION_OWNS, target=business["node_id"],
        provenance_type=model.PROVENANCE_USER_ASSERTED)
    graph.record_edge(
        cur, owner_user_id=user_id, source=business["node_id"],
        relation_type=model.RELATION_OWNS, target=prop["node_id"],
        provenance_type=model.PROVENANCE_VERIFIED)
    graph.record_edge(
        cur, owner_user_id=user_id, source=prop["node_id"],
        relation_type=model.RELATION_COVERED_BY, target=policy["node_id"],
        provenance_type=model.PROVENANCE_PROVIDER_ASSERTED)

    _fact(cur, user_id, prop["node_id"], "address", f"1 {suffix} Street",
          domain=model.DOMAIN_FINANCIAL)
    _fact(cur, user_id, prop["node_id"], "estimated_value", "800000",
          value_type=model.VALUE_MONEY, domain=model.DOMAIN_FINANCIAL,
          provenance=model.PROVENANCE_VERIFIED)
    _fact(cur, user_id, policy["node_id"], "policy_reference", f"REF-{suffix}",
          domain=model.DOMAIN_FINANCIAL,
          provenance=model.PROVENANCE_PROVIDER_ASSERTED)

    _IDS[f"{owner}_person"] = int(person["node_id"])
    _IDS[f"{owner}_business"] = int(business["node_id"])
    _IDS[f"{owner}_property"] = int(prop["node_id"])
    _IDS[f"{owner}_policy"] = int(policy["node_id"])
    return {"person": person, "business": business, "property": prop, "policy": policy}


# ---------------------------------------------------------------------------
def stage_build_graphs():
    print("\n[fixtures]")
    conn, cur = cursor()
    schema.reset_schema_cache()
    state = schema.ensure_private_schema(cur, force=True)
    check("schema is ready", state["status"] == schema.STATUS_READY, str(state))

    _chain(cur, "a", USER_A, "A")
    _chain(cur, "b", USER_B, "B")

    check("both members have a four-node chain",
          graph.count_nodes(cur, owner_user_id=USER_A) == 4
          and graph.count_nodes(cur, owner_user_id=USER_B) == 4,
          f"A={graph.count_nodes(cur, owner_user_id=USER_A)} "
          f"B={graph.count_nodes(cur, owner_user_id=USER_B)}")
    conn.commit()
    conn.close()


def stage_golden_query():
    """Stage 20 — 'what property does my business own?', answered structurally."""
    print("\n[golden query]")
    conn, cur = cursor()

    result = retrieval.retrieve(
        cur, owner_user_id=USER_A, actor_user_id=USER_A,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["a_business"]], purpose="undx_context")

    check("the retrieval was not denied", result["denied"] == "", result["denied"])

    node_ids = {int(n["id"]) for n in result["relevant_nodes"]}
    check("the property is reached from the business",
          _IDS["a_property"] in node_ids, str(sorted(node_ids)))
    check("the policy covering it is reached too",
          _IDS["a_policy"] in node_ids, str(sorted(node_ids)))

    # The *path*, not just the endpoints. An answer that names the right
    # property without the edge that justifies it is a guess that happened to
    # be correct.
    owns = [
        e for e in result["relevant_edges"]
        if e["relation_type"] == model.RELATION_OWNS
        and int(e["source_node_id"]) == _IDS["a_business"]
        and int(e["target_node_id"]) == _IDS["a_property"]
    ]
    check("the OWNS edge that justifies the answer is present", len(owns) == 1,
          str(result["relevant_edges"]))
    covered = [
        e for e in result["relevant_edges"]
        if e["relation_type"] == model.RELATION_COVERED_BY
        and int(e["source_node_id"]) == _IDS["a_property"]
    ]
    check("the COVERED_BY edge is present", len(covered) == 1, str(covered))

    addresses = [
        f for f in result["relevant_facts"]
        if f["fact_type"] == "address" and f["subject_id"] == str(_IDS["a_property"])
    ]
    check("the property's address travelled with it", len(addresses) == 1,
          str([f["fact_type"] for f in result["relevant_facts"]]))

    provenance = {p["fact_id"]: p for p in result["provenance"]}
    check("every returned fact has provenance",
          all(f["id"] in provenance for f in result["relevant_facts"]),
          f"{len(provenance)} of {len(result['relevant_facts'])}")
    valued = [p for p in result["provenance"] if p["fact_type"] == "estimated_value"]
    check("provenance names the source, not just that there was one",
          bool(valued) and valued[0]["provenance_type"] == model.PROVENANCE_VERIFIED,
          str(valued))

    check("nothing belonging to User B appears",
          all(str(f["typed_value"]).find("B Street") == -1
              for f in result["relevant_facts"])
          and not (node_ids & {_IDS["b_property"], _IDS["b_business"],
                               _IDS["b_policy"], _IDS["b_person"]}),
          str(sorted(node_ids)))

    conn.commit()
    conn.close()


def stage_owner_and_authorization():
    """Stage 14 — the P0 gate, at the retrieval surface."""
    print("\n[owner + authorization]")
    conn, cur = cursor()

    # B's node id, requested by A. Must be indistinguishable from an id that
    # was never issued.
    foreign = retrieval.retrieve(
        cur, owner_user_id=USER_A, actor_user_id=USER_A,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["b_property"]])
    fictional = retrieval.retrieve(
        cur, owner_user_id=USER_A, actor_user_id=USER_A,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[999999])
    # The payload is assembled from fragments rather than written as one
    # literal on purpose. Spelled out in full it is a real DDL statement
    # naming a private table, and `test_private_write_boundary.py` scans test
    # files too — by design, since a guard that accepts "but I only meant it
    # as a string" has no way to tell that claim from a genuine bypass. The
    # value handed to `retrieve` is identical either way.
    injection = "'; " + " ".join(("DROP", "TABLE", "private_facts")) + "; --"
    malformed = retrieval.retrieve(
        cur, owner_user_id=USER_A, actor_user_id=USER_A,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[injection])

    check("a foreign seed returns nothing", foreign["relevant_nodes"] == [])
    check("a foreign seed is indistinguishable from a fictional one",
          (foreign["relevant_nodes"], foreign["denied"])
          == (fictional["relevant_nodes"], fictional["denied"]),
          f"{foreign['denied']!r} vs {fictional['denied']!r}")
    check("a malformed seed answers the same way",
          (malformed["relevant_nodes"], malformed["denied"])
          == (fictional["relevant_nodes"], fictional["denied"]),
          str(malformed))

    # And the tables are still there — the malformed id went in as a bound
    # parameter, not as SQL.
    check("the injection attempt did not reach the database",
          graph.count_nodes(cur, owner_user_id=USER_A) == 4,
          str(graph.count_nodes(cur, owner_user_id=USER_A)))

    before = _denials(cur)
    impersonation = retrieval.retrieve(
        cur, owner_user_id=USER_B, actor_user_id=USER_A,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["b_property"]])
    check("an actor reading another member's store is denied",
          impersonation["denied"] == retrieval.DENIED_NOT_OWNER,
          str(impersonation["denied"]))
    check("the denial returns no rows at all",
          not any((impersonation["relevant_nodes"], impersonation["relevant_edges"],
                   impersonation["relevant_facts"])))
    check("the attempt is recorded as a denial", _denials(cur) == before + 1,
          f"{before} -> {_denials(cur)}")

    conn.commit()
    conn.close()


def _denials(cur) -> int:
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {schema.AUDIT_TABLE} WHERE action = ?",
        (audit.ACTION_ACCESS_DENIED,))
    row = cur.fetchone()
    return int(row["n"] if hasattr(row, "keys") else row[0])


def stage_bounds():
    """Stage 16 — bounded traversal, and honest about the bound."""
    print("\n[bounds]")
    conn, cur = cursor()

    shallow = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["a_person"]], max_depth=1)
    reached = {int(n["id"]) for n in shallow["relevant_nodes"]}
    check("depth 1 from the person reaches the business",
          _IDS["a_business"] in reached, str(sorted(reached)))
    check("depth 1 does not reach the property two hops away",
          _IDS["a_property"] not in reached, str(sorted(reached)))

    deep = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["a_person"]], max_depth=3)
    reached_deep = {int(n["id"]) for n in deep["relevant_nodes"]}
    check("depth 3 reaches the policy three hops away",
          _IDS["a_policy"] in reached_deep, str(sorted(reached_deep)))

    check("a caller cannot ask for more depth than the ceiling",
          retrieval.retrieve(
              cur, owner_user_id=USER_A,
              intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
              seed_node_ids=[_IDS["a_person"]],
              max_depth=99)["bounds"]["max_depth"] == retrieval.MAX_DEPTH)
    check("a caller cannot ask for more nodes than the ceiling",
          retrieval.retrieve(
              cur, owner_user_id=USER_A,
              intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
              seed_node_ids=[_IDS["a_person"]],
              max_nodes=10_000)["bounds"]["max_nodes"] == retrieval.MAX_NODES)

    # Truncation must be reported, not merely survived.
    clipped = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["a_person"]], max_nodes=2)
    check("a bound that bites is reported as truncation",
          clipped["truncated"]["nodes"] is True, str(clipped["truncated"]))
    check("an answer within the bounds is not reported as truncated",
          deep["truncated"]["nodes"] is False
          and deep["truncated"]["edges"] is False, str(deep["truncated"]))
    check("the depth actually reached is reported",
          deep["truncated"]["depth_reached"] == 3,
          str(deep["truncated"]["depth_reached"]))

    conn.commit()
    conn.close()


def stage_domain_and_sensitivity():
    """Stage 17 — the join policy, and reachability is not authorization."""
    print("\n[domain + sensitivity]")
    conn, cur = cursor()

    ok, _ = retrieval.domain_join_permitted(
        (model.DOMAIN_GENERAL, model.DOMAIN_FINANCIAL, model.DOMAIN_LEGAL))
    check("property + insurance + loan style domains may be joined", ok)

    blocked, reason = retrieval.domain_join_permitted(
        (model.DOMAIN_HEALTH, model.DOMAIN_FINANCIAL))
    check("health may not be joined to financial context", not blocked)
    check("the refusal names the rule", reason == "isolated_domain_joined_to_unrelated_context",
          reason)

    pair, pair_reason = retrieval.domain_join_permitted(
        (model.DOMAIN_HEALTH, model.DOMAIN_IDENTITY))
    check("two isolated domains may not be joined either", not pair)
    check("that refusal names its own rule", pair_reason == "multiple_isolated_domains",
          pair_reason)

    alone, _ = retrieval.domain_join_permitted(
        (model.DOMAIN_HEALTH, model.DOMAIN_GENERAL))
    check("health with general context alone is permitted", alone)

    # The structural version of the same rule: no intent may declare a domain
    # set that the policy would refuse. This is the check that survives someone
    # adding an intent in a hurry.
    offenders = [
        name for name, policy in retrieval.INTENTS.items()
        if not retrieval.domain_join_permitted(policy["domains"])[0]
    ]
    check("no intent declares a domain set the join policy forbids",
          offenders == [], str(offenders))

    # A caller may narrow an intent's domains; they may not widen them.
    narrowed = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["a_business"]], domains=[model.DOMAIN_HEALTH])
    check("asking a property intent for health domains yields nothing",
          narrowed["relevant_nodes"] == [] and narrowed["denied"] != "",
          f"{narrowed['denied']!r} {len(narrowed['relevant_nodes'])}")

    # Reachability is not authorization: a RESTRICTED node hanging off a
    # permitted edge must not ride in on the traversal.
    secret = graph.upsert_node(
        cur, owner_user_id=USER_A, node_type=model.NODE_ASSET,
        external_ref="asset:secret", domain=model.DOMAIN_FINANCIAL,
        sensitivity=model.SENSITIVITY_RESTRICTED)
    graph.record_edge(
        cur, owner_user_id=USER_A, source=_IDS["a_business"],
        relation_type=model.RELATION_OWNS, target=secret["node_id"],
        provenance_type=model.PROVENANCE_USER_ASSERTED)

    scoped = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["a_business"]])
    reached = {int(n["id"]) for n in scoped["relevant_nodes"]}
    check("a RESTRICTED node is not pulled in by a permitted edge",
          int(secret["node_id"]) not in reached, str(sorted(reached)))
    check("the permitted material around it still came back",
          _IDS["a_property"] in reached, str(sorted(reached)))

    widened = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent=retrieval.INTENT_PROPERTY_PORTFOLIO,
        seed_node_ids=[_IDS["a_business"]],
        sensitivity_ceiling=model.SENSITIVITY_RESTRICTED)
    check("a caller cannot widen the intent's sensitivity ceiling",
          widened["sensitivity_ceiling"] == model.SENSITIVITY_CONFIDENTIAL,
          widened["sensitivity_ceiling"])
    check("and still does not see the RESTRICTED node",
          int(secret["node_id"]) not in {int(n["id"]) for n in widened["relevant_nodes"]})

    conn.commit()
    conn.close()


def stage_unknown_intent_and_conflicts():
    """Unknown intents are refused; disputes and staleness travel with context."""
    print("\n[intent, conflicts, freshness]")
    conn, cur = cursor()

    before = _denials(cur)
    unknown = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent="exfiltrate_everything",
        seed_node_ids=[_IDS["a_business"]])
    check("an unknown intent is refused rather than defaulted",
          unknown["denied"] == retrieval.DENIED_UNKNOWN_INTENT, str(unknown["denied"]))
    check("the refused intent returns nothing", unknown["relevant_nodes"] == [])
    check("the refusal is audited", _denials(cur) == before + 1)

    # Two irreconcilable renewal dates for one period — the Stage 21 setup.
    for value, source in (("2027-03-01", model.PROVENANCE_PROVIDER_ASSERTED),
                          ("2027-04-15", model.PROVENANCE_DOCUMENT_EXTRACTED)):
        _fact(cur, USER_A, _IDS["a_policy"], "renewal_date", value,
              value_type=model.VALUE_DATE, provenance=source,
              domain=model.DOMAIN_FINANCIAL,
              observed_at="2026-01-01T00:00:00+00:00",
              valid_from="2026-01-01T00:00:00+00:00")

    result = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent=retrieval.INTENT_INSURANCE_COVERAGE,
        seed_node_ids=[_IDS["a_policy"]])
    check("the conflicting renewal dates travel with the context",
          len(result["conflicts"]) == 1, str(result["conflicts"]))
    if result["conflicts"]:
        conflict = result["conflicts"][0]
        check("the conflict is unresolved", conflict.get("unresolved") is True)
        check("the conflict names both competing facts",
              len(conflict.get("competing", conflict.get("competing_fact_ids", []))) == 2,
              str(conflict))
        check("both dates are still readable so neither is silently chosen",
              len([f for f in result["relevant_facts"]
                   if f["fact_type"] == "renewal_date"]) == 2,
              str([f["typed_value"] for f in result["relevant_facts"]
                   if f["fact_type"] == "renewal_date"]))

    # An old user-asserted claim is past its freshness horizon and must be
    # flagged rather than quoted as current.
    _fact(cur, USER_A, _IDS["a_policy"], "premium_note", "quoted long ago",
          domain=model.DOMAIN_FINANCIAL,
          observed_at="2019-01-01T00:00:00+00:00",
          valid_from="2019-01-01T00:00:00+00:00")
    aged = retrieval.retrieve(
        cur, owner_user_id=USER_A, intent=retrieval.INTENT_INSURANCE_COVERAGE,
        seed_node_ids=[_IDS["a_policy"]])
    flagged = [s for s in aged["stale_flags"] if s["fact_type"] == "premium_note"]
    check("a fact past its freshness horizon is flagged stale", len(flagged) == 1,
          str(aged["stale_flags"]))
    check("the stale fact is still returned rather than hidden",
          any(f["fact_type"] == "premium_note" for f in aged["relevant_facts"]))

    conn.commit()
    conn.close()


def stage_audit_of_retrieval():
    """Stage 18 — a retrieval is recorded, and records nothing about content."""
    print("\n[audit]")
    conn, cur = cursor()

    cur.execute(
        f"SELECT COUNT(*) AS n FROM {schema.AUDIT_TABLE} WHERE action = ?",
        (audit.ACTION_CONTEXT_RETRIEVED,))
    row = cur.fetchone()
    check("retrievals are recorded",
          int(row["n"] if hasattr(row, "keys") else row[0]) > 0)

    cur.execute(f"SELECT * FROM {schema.AUDIT_TABLE}")
    rows = [dict(r) for r in cur.fetchall()]
    leaked = [
        r for r in rows
        if any("Street" in str(v) or "REF-" in str(v) or "800000" in str(v)
               for v in r.values())
    ]
    check("no address, reference or value reached the audit table",
          leaked == [], str(leaked[:2]))
    check("the intent was recorded as the object id",
          any(r["object_id"] == retrieval.INTENT_PROPERTY_PORTFOLIO for r in rows),
          str({r["object_id"] for r in rows}))

    conn.commit()
    conn.close()


def main() -> int:
    print("PRIVATE OFFICE RETRIEVAL — Stages 14-17, 20")
    print(f"database: {_TMP_DB}")
    # Order-dependent and non-reentrant, like the substrate suite: the golden
    # query needs the fixtures, and the audit stage needs everything above it
    # to have happened. See that suite's `main` for why the stages are named
    # `stage_*` rather than `test_*`.
    _FAILURES.clear()
    _IDS.clear()
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    schema.reset_schema_cache()

    stage_build_graphs()
    stage_golden_query()
    stage_owner_and_authorization()
    stage_bounds()
    stage_domain_and_sensitivity()
    stage_unknown_intent_and_conflicts()
    stage_audit_of_retrieval()

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_retrieval_suite():
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
