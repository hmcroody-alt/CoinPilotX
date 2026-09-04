"""Batch B item 5 — the Capital Graph read boundary, proved against a real graph.

``capital_graph`` is the only sanctioned way to *view* the private graph. Its
value is entirely in what it refuses to do, and none of those refusals are
visible in the output of a working call — a payload with no net worth in it
looks exactly like a payload that has not got round to adding one yet. So this
file pins the absences as hard as it pins the presences.

Four properties, in the order they would hurt:

1. **The boundary is real.** Every read goes through ``retrieval.retrieve``.
   The module imports no ``graph`` and writes no SQL, so there is no path from
   a route or a UNDX executor to a node row that skips the five gates. This is
   asserted against the source text, because the day somebody adds
   ``from services.private_office import graph`` for one "quick" lookup is the
   day the gates stop being mandatory, and every behavioural test here would
   still pass.

2. **No total.** A net worth is a single confident number assembled from rows
   whose provenance runs from a bank read-back to a figure typed in 2023, over
   a set the traversal explicitly bounds. ``counted`` and ``complete`` are the
   honest replacements and the payload is scanned for anything shaped like a
   sum.

3. **Nothing is resolved and nothing is rounded up.** A conflict comes out with
   both values and ``unresolved`` true; ``truth_state`` reports the *weakest*
   provenance present and puts CONFLICTING above VERIFIED. The failure being
   prevented is a screen that says "verified" over two contradictory numbers.

4. **The surface is not an existence oracle.** A node that is absent, belongs to
   another member, or is out of the view's domain must produce the *same bytes*.
   User B is given an identically shaped graph throughout so that "A sees
   nothing of B's" cannot pass merely because B has nothing.

Run either way::

    python -m pytest tests/private_office/test_capital_graph.py
    python tests/private_office/test_capital_graph.py
"""

import json
import os
import re
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_capital_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import capital_graph as cg  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import graph  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import retrieval  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9301
USER_B = 9302

_FAILURES: list[str] = []
_IDS: dict[str, int] = {}

SOURCE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "services", "private_office", "capital_graph.py",
)


def check(label: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    text = f"{label}{(' — ' + str(detail)) if detail != '' else ''}"
    _FAILURES.append(text)
    print(f"  FAIL  {text}")


def cursor():
    conn = db.connect()
    return conn, conn.cursor()


def _fact(cur, owner, node_id, fact_type, value, **kwargs):
    kwargs.setdefault("value_type", model.VALUE_STRING)
    kwargs.setdefault("provenance_type", model.PROVENANCE_USER_ASSERTED)
    kwargs.setdefault("domain", model.DOMAIN_FINANCIAL)
    return facts.record_fact(
        cur, owner_user_id=owner, subject_type=retrieval.SUBJECT_TYPE_NODE,
        subject_id=str(node_id), fact_type=fact_type, value=value, **kwargs)


def _chain(cur, owner: str, user_id: int, suffix: str) -> None:
    """PERSON -OWNS-> BUSINESS -OWNS-> PROPERTY -COVERED_BY-> POLICY.

    The same shape the retrieval suite builds, so a change to the substrate
    breaks both together rather than leaving this one quietly reading a graph
    nobody else recognises. The PERSON is deliberately given no facts: a node
    with nothing recorded about it must come back MISSING, and MISSING is the
    state most easily faked by borrowing a neighbour's.
    """
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

    # One VERIFIED fact and one ESTIMATED fact on the same node. The property's
    # truth state must be ESTIMATED: the strong row must not vouch for the weak
    # one. This is the single most important fixture in the file.
    _fact(cur, user_id, prop["node_id"], "address", f"1 {suffix} Street",
          provenance_type=model.PROVENANCE_VERIFIED)
    _fact(cur, user_id, prop["node_id"], "estimated_value", "800000",
          value_type=model.VALUE_MONEY,
          provenance_type=model.PROVENANCE_ESTIMATED)
    _fact(cur, user_id, policy["node_id"], "policy_reference", f"REF-{suffix}",
          provenance_type=model.PROVENANCE_PROVIDER_ASSERTED)

    for name, row in (("person", person), ("business", business),
                      ("property", prop), ("policy", policy)):
        _IDS[f"{owner}_{name}"] = int(row["node_id"])


# ---------------------------------------------------------------------------
def stage_build_graphs():
    print("\n[fixtures]")
    conn, cur = cursor()
    schema.reset_schema_cache()
    schema.ensure_private_schema(cur)
    _chain(cur, "a", USER_A, "alpha")
    _chain(cur, "b", USER_B, "bravo")
    conn.commit()
    conn.close()
    check("two independent graphs exist", len(_IDS) == 8, sorted(_IDS))


def stage_the_boundary_is_real():
    """No import of ``graph``, no SQL, no table names. Asserted on the text.

    Behaviour cannot prove this. A module that reached straight into
    ``graph.get_node`` for one lookup would return the same rows for the owner's
    own data and would only differ on the day an authorization gate mattered.
    """
    print("\n[the read boundary]")
    with open(SOURCE_PATH, "r", encoding="utf-8") as handle:
        source = handle.read()

    check("does not import the graph store",
          re.search(r"^\s*(from\s+\S*private_office\s+import\s+[^\n]*\bgraph\b"
                    r"|import\s+\S*\bprivate_office\.graph\b)", source, re.M) is None)
    check("contains no SQL",
          re.search(r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b",
                    source) is None)
    check("names no private table",
          "private_graph_" not in source and "private_facts" not in source)
    check("calls retrieval.retrieve", "_retrieval.retrieve(" in source)


def stage_views_map_to_real_intents():
    print("\n[views]")
    unknown = [name for name in cg.VIEW_INTENTS.values()
               if name not in retrieval.INTENTS]
    check("every view maps to a declared intent", not unknown, unknown)
    check("VIEWS and VIEW_INTENTS agree",
          set(cg.VIEWS) == set(cg.VIEW_INTENTS), (cg.VIEWS, sorted(cg.VIEW_INTENTS)))
    check("the default view is one of them", cg.DEFAULT_VIEW in cg.VIEW_INTENTS)

    # Stage 17's join policy exists to keep a health record out of a payload
    # about property. A view mapping either isolated intent would put that whole
    # policy behind one dictionary entry.
    check("no health view", retrieval.INTENT_HEALTH_CONTEXT
          not in cg.VIEW_INTENTS.values())
    check("no identity view", retrieval.INTENT_IDENTITY_CONTEXT
          not in cg.VIEW_INTENTS.values())

    check("an unknown view is refused, not defaulted",
          cg.normalize_view("health_context") is None)
    check("normalize_view never invents a default",
          cg.normalize_view("") is None and cg.normalize_view(None) is None)

    conn, cur = cursor()
    for view in cg.VIEWS:
        payload = cg.summary(cur, owner_user_id=USER_A, view=view)
        check(f"view {view!r} resolves to its own intent",
              payload["denied"] == ""
              and payload["intent"] == cg.VIEW_INTENTS[view],
              (payload["denied"], payload["intent"]))
    refused = cg.summary(cur, owner_user_id=USER_A, view="net_worth")
    check("an unknown view denies rather than serving another view",
          refused["denied"] == cg.DENIED_UNKNOWN_VIEW, refused["denied"])
    check("a refusal carries no data",
          refused["nodes"] == [] and refused["facts"] == []
          and refused["complete"] is False)
    conn.close()


def stage_truth_states():
    print("\n[truth states]")

    def row(provenance, *, stale=False):
        return {"provenance": {"provenance_type": provenance},
                "freshness": {"stale": stale}}

    check("nothing recorded is MISSING",
          cg.truth_state([], conflicted=False) == cg.TRUTH_MISSING)
    check("MISSING is not inferred from a neighbour",
          cg.truth_state(None, conflicted=False) == cg.TRUTH_MISSING)

    # The precedence that matters most: a dispute outranks a verified row on one
    # side of it, because the dispute is the more important fact about the node.
    check("CONFLICTING outranks VERIFIED",
          cg.truth_state([row(model.PROVENANCE_VERIFIED)], conflicted=True)
          == cg.TRUTH_CONFLICTING)

    check("stale outranks its own provenance",
          cg.truth_state([row(model.PROVENANCE_VERIFIED, stale=True)],
                         conflicted=False) == cg.TRUTH_STALE)

    for provenance in (model.PROVENANCE_VERIFIED, model.PROVENANCE_PROVIDER_ASSERTED,
                       model.PROVENANCE_DOCUMENT_EXTRACTED,
                       model.PROVENANCE_USER_ASSERTED):
        check(f"{provenance} reads as KNOWN",
              cg.truth_state([row(provenance)], conflicted=False) == cg.TRUTH_KNOWN)

    check("INFERRED is never KNOWN",
          cg.truth_state([row(model.PROVENANCE_INFERRED)], conflicted=False)
          == cg.TRUTH_INFERRED)
    check("ESTIMATED is never KNOWN",
          cg.truth_state([row(model.PROVENANCE_ESTIMATED)], conflicted=False)
          == cg.TRUTH_ESTIMATED)

    # One good row must not vouch for the rest.
    check("the weakest provenance present wins",
          cg.truth_state([row(model.PROVENANCE_VERIFIED),
                          row(model.PROVENANCE_ESTIMATED)], conflicted=False)
          == cg.TRUTH_ESTIMATED)
    check("order of the rows does not change the answer",
          cg.truth_state([row(model.PROVENANCE_ESTIMATED),
                          row(model.PROVENANCE_VERIFIED)], conflicted=False)
          == cg.TRUTH_ESTIMATED)

    check("an unrecognised provenance is PRO_REVIEW, not KNOWN",
          cg.truth_state([row("SOMETHING_NEW")], conflicted=False)
          == cg.TRUTH_PRO_REVIEW)
    check("PRO_REVIEW outranks a good row beside it",
          cg.truth_state([row(model.PROVENANCE_VERIFIED), row("SOMETHING_NEW")],
                         conflicted=False) == cg.TRUTH_PRO_REVIEW)

    check("every state the module names is reachable vocabulary",
          set(cg.TRUTH_STATES) == {
              cg.TRUTH_KNOWN, cg.TRUTH_INFERRED, cg.TRUTH_ESTIMATED,
              cg.TRUTH_STALE, cg.TRUTH_MISSING, cg.TRUTH_CONFLICTING,
              cg.TRUTH_PRO_REVIEW})


def stage_summary_shape():
    print("\n[summary]")
    conn, cur = cursor()
    payload = cg.summary(cur, owner_user_id=USER_A)

    check("the owner's own read is not denied",
          payload["denied"] == "", payload["denied"])
    check("it walked the property portfolio",
          payload["intent"] == retrieval.INTENT_PROPERTY_PORTFOLIO,
          payload["intent"])

    by_type = {node["node_type"]: node for node in payload["nodes"]}
    check("the chain came back",
          {model.NODE_PERSON, model.NODE_BUSINESS, model.NODE_PROPERTY}
          <= set(by_type), sorted(by_type))

    check("counted reports what was seen",
          payload["counted"].get(model.NODE_PROPERTY) == 1, payload["counted"])
    check("truth_counts pre-declares all seven states",
          set(payload["truth_counts"]) == set(cg.TRUTH_STATES),
          sorted(payload["truth_counts"]))
    check("truth_counts sums to the node count",
          sum(payload["truth_counts"].values()) == len(payload["nodes"]))

    # The fixture's whole point.
    check("a verified fact does not vouch for an estimated one",
          by_type[model.NODE_PROPERTY]["truth"] == cg.TRUTH_ESTIMATED,
          [(n["node_type"], n["truth"]) for n in payload["nodes"]])
    check("a node with nothing recorded is MISSING",
          by_type[model.NODE_PERSON]["truth"] == cg.TRUTH_MISSING,
          by_type[model.NODE_PERSON])
    check("fact_count matches what was actually attached",
          by_type[model.NODE_PROPERTY]["fact_count"] == 2,
          by_type[model.NODE_PROPERTY]["fact_count"])

    check("a small graph reports itself complete",
          payload["complete"] is True, payload["truncated"])
    check("the bounds that were applied are stated",
          set(payload["bounds"]) == {"max_depth", "max_nodes", "max_edges"},
          payload["bounds"])

    check("results are ordered deterministically",
          payload["nodes"] == sorted(
              payload["nodes"], key=lambda r: (r["node_type"], r["id"]))
          and payload["edges"] == sorted(payload["edges"], key=lambda r: r["id"]))
    check("a repeated read is byte-identical",
          cg.summary(cur, owner_user_id=USER_A) == payload)
    conn.close()


def stage_no_total():
    """There is no net worth, and the payload has no field that could become one."""
    print("\n[no aggregate value]")
    conn, cur = cursor()
    payload = cg.summary(cur, owner_user_id=USER_A)
    conn.close()

    blob = json.dumps(payload).lower()
    for word in ("net_worth", "networth", "total_value", "total_assets",
                 "portfolio_value", "sum_value", "estate_value", "grand_total"):
        check(f"no {word!r} anywhere in the payload", word not in blob)

    # A money value may legitimately appear as one fact's own value. What must
    # not exist is a top-level number claiming to describe the whole set.
    numeric_top_level = [
        key for key, value in payload.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    check("no top-level scalar total", numeric_top_level == [], numeric_top_level)
    check("counted holds counts, not money",
          all(isinstance(v, int) for v in payload["counted"].values()),
          payload["counted"])
    check("the decision is recorded in the module",
          "net worth" in cg.NO_AGGREGATE_VALUE.lower())
    check("`complete` is what a client must check before saying 'N properties'",
          isinstance(payload["complete"], bool))


def stage_conflicts_are_never_resolved():
    print("\n[conflicts]")
    conn, cur = cursor()

    # Two sources, two different values, same fact type, same subject.
    node = _IDS["a_policy"]
    _fact(cur, USER_A, node, "renewal_date", "2027-03-01",
          value_type=model.VALUE_DATE,
          provenance_type=model.PROVENANCE_PROVIDER_ASSERTED)
    _fact(cur, USER_A, node, "renewal_date", "2027-04-01",
          value_type=model.VALUE_DATE,
          provenance_type=model.PROVENANCE_DOCUMENT_EXTRACTED)
    conn.commit()

    payload = cg.summary(cur, owner_user_id=USER_A, view=cg.VIEW_COVERAGE)
    conflicts = [c for c in payload["conflicts"] if c["subject_id"] == str(node)]
    check("the disagreement is surfaced", len(conflicts) >= 1,
          payload["conflicts"])

    if conflicts:
        conflict = conflicts[0]
        check("it is reported unresolved", conflict["unresolved"] is True)
        # An alarm with no action attached is worse than no alarm: the owner is
        # the only party who can say which source is right, and they cannot do
        # that without seeing both values.
        values = {entry["value"] for entry in conflict["competing"]}
        check("both competing values are carried out",
              {"2027-03-01", "2027-04-01"} <= values, values)
        check("each side says where it came from",
              all(entry["provenance_type"] for entry in conflict["competing"]),
              conflict["competing"])
        check("the internal fact_key is not leaked",
              all("fact_key" not in entry for entry in conflict["competing"]))
        check("no winner is named",
              not any(key in conflict for key in
                      ("resolved", "resolution", "winner", "preferred",
                       "chosen", "correct_value")), sorted(conflict))

        subject = next((n for n in payload["nodes"] if str(n["id"]) == str(node)),
                       None)
        check("the disputed node reads CONFLICTING despite a sourced row",
              subject is not None and subject["truth"] == cg.TRUTH_CONFLICTING,
              subject)
        check("the conflict is counted as such",
              payload["truth_counts"][cg.TRUTH_CONFLICTING] >= 1,
              payload["truth_counts"])
    conn.close()


def stage_entity_and_relationships():
    print("\n[entity and relationships]")
    conn, cur = cursor()
    prop = _IDS["a_property"]

    payload = cg.entity(cur, owner_user_id=USER_A, node_id=prop)
    check("the entity is found", payload["denied"] == "", payload["denied"])
    check("the subject is named", payload["entity"]["id"] == prop)
    check("the subject is not repeated inside its own neighbours",
          all(row["id"] != prop for row in payload["related"]))
    check("the walk stayed at depth 1",
          payload["truncated"].get("depth_reached") <= 1, payload["truncated"])
    check("a string id works the same as an int",
          cg.entity(cur, owner_user_id=USER_A, node_id=str(prop)) == payload)

    rels = cg.relationships(cur, owner_user_id=USER_A, node_id=prop)
    check("relationships are found", rels["denied"] == "", rels["denied"])
    check("both directions are represented",
          {row["direction"] for row in rels["relationships"]} == {"in", "out"},
          [(r["relation_type"], r["direction"]) for r in rels["relationships"]])
    check("every relationship names its far end",
          all(row["other"]["id"] != prop for row in rels["relationships"]))
    # A projection, not a second traversal: two walks could disagree, and the
    # visible symptom would be a relationship list the entity screen denies.
    check("it projects the same edges the entity read returned",
          rels["edges"] == payload["edges"])
    check("every edge carries both endpoints and a provenance",
          all(row["source_node_id"] and row["target_node_id"]
              and row["provenance"]["provenance_type"]
              for row in payload["edges"]), payload["edges"])
    check("the edge locator is not leaked",
          all("locator" not in row["provenance"] for row in payload["edges"]))
    conn.close()


def stage_the_surface_is_not_an_oracle():
    """Absent, foreign and out-of-scope must be indistinguishable."""
    print("\n[existence must not leak]")
    conn, cur = cursor()

    absent = cg.entity(cur, owner_user_id=USER_A, node_id=987654321)
    foreign = cg.entity(cur, owner_user_id=USER_A, node_id=_IDS["b_property"])
    blank = cg.entity(cur, owner_user_id=USER_A, node_id="")

    check("an absent node is refused",
          absent["denied"] == cg.DENIED_NODE_NOT_FOUND, absent["denied"])
    check("another member's node is refused",
          foreign["denied"] == cg.DENIED_NODE_NOT_FOUND, foreign["denied"])
    # The property that makes the two above worth having. If these differed by
    # a single byte, an attacker could enumerate node ids by diffing responses.
    check("a real foreign node and a nonexistent one return identical payloads",
          foreign == absent)
    check("a blank id is refused the same way", blank == absent)
    check("a refusal carries no entity and no relationships",
          "entity" not in foreign and "related" not in foreign
          and "relationships" not in foreign, sorted(foreign))

    rel_foreign = cg.relationships(cur, owner_user_id=USER_A,
                                   node_id=_IDS["b_property"])
    check("relationships refuse identically",
          rel_foreign == absent, rel_foreign["denied"])
    conn.close()


def stage_owner_isolation():
    print("\n[owner isolation]")
    conn, cur = cursor()
    a = cg.summary(cur, owner_user_id=USER_A)
    b = cg.summary(cur, owner_user_id=USER_B)

    ids_a = {node["id"] for node in a["nodes"]}
    ids_b = {node["id"] for node in b["nodes"]}
    # B having data of its own is what stops this passing vacuously.
    check("B has a graph of its own", len(ids_b) >= 3, len(ids_b))
    check("no node is shared between owners", not (ids_a & ids_b), ids_a & ids_b)

    refs_a = json.dumps(a)
    check("B's external references never appear in A's payload",
          "bravo" not in refs_a)

    impersonation = cg.summary(cur, owner_user_id=USER_A, actor_user_id=USER_B)
    check("an actor who is not the owner is denied",
          impersonation["denied"] != "", impersonation["denied"])
    check("the denial returns nothing at all",
          impersonation["nodes"] == [] and impersonation["facts"] == []
          and impersonation["edges"] == [])

    entity_cross = cg.entity(cur, owner_user_id=USER_B,
                             node_id=_IDS["a_property"], actor_user_id=USER_B)
    check("B cannot open A's property", entity_cross["denied"] != "",
          entity_cross["denied"])

    check("an unauthenticated owner id gets nothing",
          cg.summary(cur, owner_user_id=0)["denied"] != "")
    conn.close()


def stage_projection_leaks_nothing():
    print("\n[projection]")
    conn, cur = cursor()
    payload = cg.summary(cur, owner_user_id=USER_A)
    conn.close()

    for node in payload["nodes"]:
        check("node output has no owner_user_id", "owner_user_id" not in node)
        check("node output has no node_key", "node_key" not in node)
        check("node output is the declared allowlist plus its derived fields",
              set(node) == set(cg._NODE_FIELDS) | {"id", "truth", "fact_count"},
              sorted(node))
        break  # one is enough; the projection is uniform by construction

    check("no node carries owner_user_id",
          not any("owner_user_id" in node for node in payload["nodes"]))
    check("no fact carries subject_id",
          not any("subject_id" in fact for fact in payload["facts"]))
    check("no edge carries owner_user_id or edge_key",
          not any("owner_user_id" in edge or "edge_key" in edge
                  for edge in payload["edges"]))
    check("stale flags do not carry the subject either",
          not any("subject_id" in entry for entry in payload["stale"]),
          payload["stale"])


# ---------------------------------------------------------------------------
def main() -> int:
    print("PRIVATE OFFICE CAPITAL GRAPH — Batch B item 5")
    print(f"database: {_TMP_DB}")
    # Order-dependent, like the retrieval and substrate suites: every stage
    # below the fixtures reads the graph the fixtures built, and the conflict
    # stage deliberately adds rows the stages after it then observe.
    _FAILURES.clear()
    _IDS.clear()
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    schema.reset_schema_cache()

    stage_build_graphs()
    stage_the_boundary_is_real()
    stage_views_map_to_real_intents()
    stage_truth_states()
    stage_summary_shape()
    stage_no_total()
    stage_entity_and_relationships()
    stage_the_surface_is_not_an_oracle()
    stage_owner_isolation()
    stage_projection_leaks_nothing()
    stage_conflicts_are_never_resolved()

    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_capital_graph_suite():
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
