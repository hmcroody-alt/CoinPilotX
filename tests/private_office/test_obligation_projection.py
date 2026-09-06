"""Obligations → Capital Graph projection journeys.

Run either way::

    python -m pytest tests/private_office/test_obligation_projection.py
    python tests/private_office/test_obligation_projection.py

What this file defends
----------------------
The Capital Graph never owns obligations — it is a projection of the record
store, settled by one convergent projector keyed on the supersedes-chain
root. Every promise that architecture makes is exercised here as a member
journey:

* **Open** — a new obligation becomes an ACTIVE LIABILITY node, an OWNS edge
  from the member's PERSON node, and title/kind/amount/currency/due facts,
  each carrying provenance that points back at the record.
* **Unknown amount** — an obligation without an amount projects **no** amount
  fact. Unknown is absent, never zero; there is no zero-valued fact to sum.
* **Revision** — revising the amount updates the *same* logical node (root
  identity survives the record-key churn); exactly one amount fact stays
  ACTIVE and the old one remains readable as history.
* **Resolution** — a resolved obligation is ARCHIVED, not deleted: the node
  survives as history, the OWNS edge and facts are retired.
* **Reopen** — the archived node is reactivated, not duplicated.
* **Idempotent and convergent** — projecting twice changes nothing.
* **Tenant isolation** — one member's obligations never touch another's
  graph.
* **Reconcile** — drift is reported as data and repaired by the same
  projector every other path uses.
* **Sweep degrades** — a failing projection logs and reports, never raises
  into the read path.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="obligation_projection_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from datetime import datetime, timedelta, timezone  # noqa: E402

from services import db  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import graph  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import obligation_projection as projection  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9921
USER_B = 9922

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    text = f"{label}{(' — ' + str(detail)) if detail != '' else ''}"
    _FAILURES.append(text)
    print(f"  FAIL  {text}")
    return False


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    return conn, cur


def _iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _liability_node(cur, owner: int, root_id: int, *,
                    include_inactive: bool = False) -> dict | None:
    rows = graph.list_nodes(cur, owner_user_id=owner,
                            node_types=[model.NODE_LIABILITY],
                            include_inactive=include_inactive, limit=100)
    for row in rows:
        if str(row.get("external_ref") or "") == f"obligation:{root_id}":
            return row
    return None


def _person_node(cur, owner: int) -> dict | None:
    rows = graph.list_nodes(cur, owner_user_id=owner,
                            node_types=[model.NODE_PERSON], limit=10)
    return rows[0] if rows else None


def _node_facts(cur, owner: int, node_id: int, fact_type: str, *,
                include_superseded: bool = False) -> list[dict]:
    return facts.list_facts(
        cur, owner_user_id=owner, subject_type=facts.SUBJECT_NODE,
        subject_id=int(node_id), fact_types=[fact_type],
        include_superseded=include_superseded, limit=50)


def _owns_edges(cur, owner: int, node_row_id: int, *,
                include_inactive: bool = False) -> list[dict]:
    return graph.neighbors(
        cur, owner_user_id=owner, node_id=node_row_id,
        relations=[model.RELATION_OWNS], direction=graph.DIRECTION_IN,
        include_inactive=include_inactive)


def setup_environment() -> None:
    schema.reset_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


# Root ids discovered along the way, shared between stages.
_STATE: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Journey: an open obligation becomes a liability
# ---------------------------------------------------------------------------

def stage_open_journey() -> None:
    print("\n[open journey]")
    conn, cur = _connect()
    try:
        created = records.create_record(
            cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Property tax", obligation_type="TAX", due_at=_iso_in(30),
            amount="4200", currency="usd", domain="FINANCIAL")
        root = int(created["record_id"])
        _STATE["root"] = root

        result = projection.project_user(cur, user_id=USER_A)
        check("projection reports one obligation",
              result.get("ok") is True and result.get("obligations") == 1, result)

        node = _liability_node(cur, USER_A, root)
        check("liability node obligation:{root} is ACTIVE", node is not None
              and node.get("lifecycle_state") == model.LIFECYCLE_ACTIVE, node)
        person = _person_node(cur, USER_A)
        check("person node exists with the owner's ref", person is not None
              and person.get("external_ref") == f"user:{USER_A}", person)
        if node is None or person is None:
            return
        node_id = int(node["id"])
        _STATE["node_id"] = node_id

        edges = _owns_edges(cur, USER_A, node_id)
        check("exactly one ACTIVE OWNS edge points at the liability",
              len(edges) == 1, edges)
        if edges:
            check("the OWNS edge is user-asserted, from the person node",
                  edges[0].get("provenance_type") == model.PROVENANCE_USER_ASSERTED
                  and int(edges[0].get("other_node_id") or 0) == int(person["id"]),
                  edges[0])

        title = _node_facts(cur, USER_A, node_id, projection.FACT_TITLE)
        check("one ACTIVE title fact, 'Property tax'",
              len(title) == 1 and title[0].get("typed_value") == "Property tax",
              title)
        kind = _node_facts(cur, USER_A, node_id, projection.FACT_KIND)
        check("one ACTIVE kind fact, 'TAX'",
              len(kind) == 1 and kind[0].get("typed_value") == "TAX", kind)
        amount = _node_facts(cur, USER_A, node_id, projection.FACT_AMOUNT)
        check("one ACTIVE amount fact, value 4200",
              len(amount) == 1
              and float(amount[0].get("value_number") or 0) == 4200.0, amount)
        currency = _node_facts(cur, USER_A, node_id, projection.FACT_CURRENCY)
        check("one ACTIVE currency fact, 'USD'",
              len(currency) == 1 and currency[0].get("typed_value") == "USD",
              currency)
        due = _node_facts(cur, USER_A, node_id, projection.FACT_DUE_AT)
        check("one ACTIVE due_at fact exists", len(due) == 1, due)
        if amount:
            prov = amount[0].get("provenance") or {}
            check("amount fact provenance names the obligation record",
                  str(prov.get("source_type") or "") == "obligation"
                  and str(prov.get("source_id") or "") == str(root), prov)
    finally:
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Journey: an unknown amount is absent, never zero
# ---------------------------------------------------------------------------

def stage_unknown_amount_journey() -> None:
    print("\n[unknown amount journey]")
    conn, cur = _connect()
    try:
        created = records.create_record(
            cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Estate legal fees", obligation_type="LEGAL",
            domain="FINANCIAL")
        root = int(created["record_id"])

        projection.project_user(cur, user_id=USER_A)
        node = _liability_node(cur, USER_A, root)
        check("the amount-less obligation still projects a node",
              node is not None
              and node.get("lifecycle_state") == model.LIFECYCLE_ACTIVE, node)
        if node is None:
            return
        amount = _node_facts(cur, USER_A, int(node["id"]), projection.FACT_AMOUNT)
        check("no amount fact exists — unknown is absent", len(amount) == 0, amount)
        check("in particular there is no zero-valued amount fact",
              not any(float(row.get("value_number") or 0) == 0.0
                      for row in amount), amount)
        currency = _node_facts(cur, USER_A, int(node["id"]),
                               projection.FACT_CURRENCY)
        check("no currency fact rides along without an amount",
              len(currency) == 0, currency)
    finally:
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Journey: a revision keeps one logical node
# ---------------------------------------------------------------------------

def stage_revision_journey() -> None:
    print("\n[revision journey]")
    conn, cur = _connect()
    try:
        root = _STATE.get("root")
        node_id = _STATE.get("node_id")
        if not root or not node_id:
            check("open journey left a root to revise", False)
            return

        before = graph.list_nodes(cur, owner_user_id=USER_A,
                                  node_types=[model.NODE_LIABILITY],
                                  include_inactive=True, limit=100)
        revised = records.revise_record(
            cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
            record_id=root, amount="4600")
        check("the record store superseded the old row",
              revised["status"] == records.STATUS_REVISED
              and revised["supersedes_id"] == root, revised)

        projection.project_user(cur, user_id=USER_A)

        node = _liability_node(cur, USER_A, root)
        check("the SAME logical node survives the revision",
              node is not None and int(node["id"]) == int(node_id), node)
        after = graph.list_nodes(cur, owner_user_id=USER_A,
                                 node_types=[model.NODE_LIABILITY],
                                 include_inactive=True, limit=100)
        check("no sibling liability node was minted",
              len(after) == len(before),
              f"{len(before)} -> {len(after)}")

        active = _node_facts(cur, USER_A, node_id, projection.FACT_AMOUNT)
        check("exactly one ACTIVE amount fact, value 4600",
              len(active) == 1
              and float(active[0].get("value_number") or 0) == 4600.0, active)
        history = _node_facts(cur, USER_A, node_id, projection.FACT_AMOUNT,
                              include_superseded=True)
        check("the old amount remains readable as history",
              any(float(row.get("value_number") or 0) == 4200.0
                  for row in history
                  if row.get("lifecycle_state") != model.LIFECYCLE_ACTIVE),
              history)
    finally:
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Journey: resolution archives; reopening reactivates
# ---------------------------------------------------------------------------

def stage_resolution_and_reopen_journey() -> None:
    print("\n[resolution + reopen journey]")
    conn, cur = _connect()
    try:
        root = _STATE.get("root")
        node_id = _STATE.get("node_id")
        if not root or not node_id:
            check("open journey left a root to resolve", False)
            return
        current = records.list_records(
            cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
            statuses=["OPEN"], limit=50)
        active_id = next((int(r["id"]) for r in current
                          if r.get("title") == "Property tax"), 0)
        check("the active revision of the obligation is findable",
              active_id > 0, current)
        if not active_id:
            return

        records.update_record(
            cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
            record_id=active_id, status="RESOLVED")
        projection.project_user(cur, user_id=USER_A)

        node = _liability_node(cur, USER_A, root, include_inactive=True)
        check("a resolved obligation is ARCHIVED, not deleted",
              node is not None
              and node.get("lifecycle_state") == model.LIFECYCLE_ARCHIVED, node)
        check("the archived node is invisible to ACTIVE-only reads",
              _liability_node(cur, USER_A, root) is None)
        edges = _owns_edges(cur, USER_A, node_id)
        check("the OWNS edge is retired with it", len(edges) == 0, edges)
        amount = _node_facts(cur, USER_A, node_id, projection.FACT_AMOUNT)
        check("the amount fact is retired with it", len(amount) == 0, amount)

        # `reopen=True` is required now that the transition engine treats
        # leaving a closing status as a distinct act rather than an ordinary
        # status write. This stage is about what the *projection* does when an
        # obligation comes back, so the intent is stated and the journey is
        # unchanged — the alternative reading, that a resolved obligation
        # should quietly reopen because someone sent "OPEN", is the behaviour
        # the engine exists to refuse.
        records.update_record(
            cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
            record_id=active_id, status="OPEN", reopen=True)
        projection.project_user(cur, user_id=USER_A)
        node = _liability_node(cur, USER_A, root)
        check("reopening reactivates the same node, no duplicate",
              node is not None and int(node["id"]) == int(node_id), node)
        edges = _owns_edges(cur, USER_A, node_id)
        check("the OWNS edge is active again", len(edges) == 1, edges)
        amount = _node_facts(cur, USER_A, node_id, projection.FACT_AMOUNT)
        check("the amount fact is ACTIVE again with its value — a reopened "
              "identical claim must not stay swallowed as history",
              len(amount) == 1
              and float(amount[0].get("value_number") or 0) == 4600.0, amount)
    finally:
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Idempotency + isolation
# ---------------------------------------------------------------------------

def _fingerprint(cur, owner: int) -> tuple:
    nodes = graph.list_nodes(cur, owner_user_id=owner,
                             node_types=[model.NODE_LIABILITY],
                             include_inactive=True, limit=200)
    shape = []
    for node in sorted(nodes, key=lambda r: int(r["id"])):
        rows = facts.list_facts(
            cur, owner_user_id=owner, subject_type=facts.SUBJECT_NODE,
            subject_id=int(node["id"]),
            fact_types=list(projection.PROJECTED_FACT_TYPES), limit=50)
        shape.append((int(node["id"]), str(node.get("lifecycle_state")),
                      tuple(sorted((r["fact_type"], r.get("typed_value"))
                                   for r in rows))))
    return tuple(shape)


def stage_idempotency() -> None:
    print("\n[idempotency]")
    conn, cur = _connect()
    try:
        first = projection.project_user(cur, user_id=USER_A)
        before = _fingerprint(cur, USER_A)
        second = projection.project_user(cur, user_id=USER_A)
        after = _fingerprint(cur, USER_A)
        check("projecting twice reports the same counts", first == second,
              f"{first} vs {second}")
        check("projecting twice changes nothing", before == after)
    finally:
        conn.commit()
        conn.close()


def stage_isolation() -> None:
    print("\n[tenant isolation]")
    conn, cur = _connect()
    try:
        created = records.create_record(
            cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_B,
            title="B's mortgage", obligation_type="LOAN", amount="250000",
            currency="usd", domain="FINANCIAL")
        root_b = int(created["record_id"])
        projection.project_user(cur, user_id=USER_B)

        check("B's liability exists in B's graph",
              _liability_node(cur, USER_B, root_b) is not None)
        check("B's liability does not exist in A's graph",
              _liability_node(cur, USER_A, root_b, include_inactive=True) is None)

        a_nodes = graph.list_nodes(cur, owner_user_id=USER_A,
                                   node_types=[model.NODE_LIABILITY],
                                   include_inactive=True, limit=200)
        check("none of A's liability nodes came from B's records",
              all("mortgage" not in str(n.get("external_ref") or "")
                  for n in a_nodes))
        # Re-projecting A must not disturb B.
        before_b = _fingerprint(cur, USER_B)
        projection.project_user(cur, user_id=USER_A)
        check("re-projecting A leaves B untouched",
              _fingerprint(cur, USER_B) == before_b)
    finally:
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Reconcile + sweep degradation
# ---------------------------------------------------------------------------

def stage_reconcile() -> None:
    print("\n[reconcile]")
    conn, cur = _connect()
    try:
        records.create_record(
            cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
            title="Unprojected insurance premium", obligation_type="INSURANCE",
            amount="1800", currency="usd", domain="FINANCIAL")
        # Deliberately do not project: the mirror is now behind.
        report = check_report = projection.reconcile(cur, user_id=USER_A)
        check("reconcile reports the missing node as drift",
              report["ok"] and any(d.get("field") == "node"
                                   and d.get("projected") == "missing"
                                   for d in report["drift"]), report)

        repaired = projection.reconcile(cur, user_id=USER_A, repair=True)
        check("repair converges through the same projector",
              repaired["ok"] and repaired["repaired"]
              and repaired["drift"] == [], repaired)

        clean = projection.reconcile(cur, user_id=USER_A)
        check("a repaired mirror reconciles clean",
              clean == {"ok": True, "drift": [], "repaired": False}, clean)
        del check_report
    finally:
        conn.commit()
        conn.close()


def stage_sweep_degrades() -> None:
    print("\n[sweep degrades]")
    conn, cur = _connect()
    original = projection.project_user
    try:
        def _boom(cur, *, user_id):  # noqa: ANN001
            raise RuntimeError("substrate rejected a write")
        projection.project_user = _boom
        result = projection.sweep(cur, user_id=USER_A)
        check("a failing projection degrades instead of raising",
              result.get("ok") is False, result)
    finally:
        projection.project_user = original
        conn.close()
    conn, cur = _connect()
    try:
        result = projection.sweep(cur, user_id=USER_A)
        check("a healthy sweep converges again", result.get("ok") is True, result)
    finally:
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

STAGES = (
    stage_open_journey,
    stage_unknown_amount_journey,
    stage_revision_journey,
    stage_resolution_and_reopen_journey,
    stage_idempotency,
    stage_isolation,
    stage_reconcile,
    stage_sweep_degrades,
)


def test_obligation_projection() -> None:
    setup_environment()
    for stage in STAGES:
        stage()
    assert not _FAILURES, "\n".join(_FAILURES)


if __name__ == "__main__":
    setup_environment()
    for stage in STAGES:
        stage()
    if _FAILURES:
        print(f"\n{len(_FAILURES)} FAILURE(S)")
        sys.exit(1)
    print("\nALL STAGES PASSED")
    sys.exit(0)
