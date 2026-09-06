"""Integrity diagnostics — can it actually see damage, and does it stay silent about repair.

Run either way::

    python -m pytest tests/private_office/test_integrity.py
    python tests/private_office/test_integrity.py

What this file defends
----------------------
A diagnostic is worth exactly as much as its willingness to say "no". The
failure mode this suite exists for is a checker that reports ``healthy: true``
over a store it never looked at — which is indistinguishable, on a screen, from
a store that is genuinely sound.

So every structural check is exercised by *deliberately corrupting the store
underneath it*, using direct SQL rather than the writers, because the writers
are the very things that make these faults impossible. A test that could only
produce the fault through the writer would be testing the writer.

Four properties, each a journey and then a mutation:

* **A clean store is reported clean, and the report says how much it examined.**
  ``healthy: true`` over zero rows is not the same claim as ``healthy: true``
  over four hundred, and the payload must let a reader tell them apart.
* **Damage is found.** A cross-owner edge, an orphan endpoint, a duplicate node
  key and unrecognised vocabulary each produce a named finding.
* **Skipped is not clean.** A check that cannot run drops ``healthy`` and lands
  in ``inconclusive``; it never passes silently.
* **It fixes nothing.** After a full diagnose over a corrupted store, the
  corruption is still there. This is asserted by re-reading the rows, not by
  trusting the module's own docstring.

And the shared rules: owner isolation with a denied shape rather than a thin
payload, and no foreign identifier in a finding.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="capital_integrity_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from services import db  # noqa: E402
from services.private_office import graph  # noqa: E402
from services.private_office import integrity  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9981
USER_B = 9982

#: A third member whose rows are never corrupted by any stage. The
#: skipped-is-not-clean stage needs a store with *zero* findings, because the
#: property it proves — that an unrun check alone drops ``healthy`` — is
#: invisible over a store that is already unhealthy for other reasons. Running
#: it against USER_A would make it pass whether the module honoured the rule or
#: not, which is exactly the vacuity the mutation battery exists to catch.
CLEAN_OWNER = 9983

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


def _node(cur, owner: int, node_type: str, ref: str) -> int:
    made = graph.upsert_node(
        cur, owner_user_id=owner, node_type=node_type, external_ref=ref,
        sensitivity=model.SENSITIVITY_CONFIDENTIAL,
        domain=model.DOMAIN_FINANCIAL, actor_user_id=owner)
    return int(made["node_id"])


def _edge(cur, owner: int, source: int, relation: str, target: int) -> int:
    made = graph.record_edge(
        cur, owner_user_id=owner, source=source, relation_type=relation,
        target=target, provenance_type=model.PROVENANCE_USER_ASSERTED,
        actor_user_id=owner)
    return int(made["edge_id"])


def _diagnose(cur, owner: int = USER_A, actor: int | None = None) -> dict:
    return integrity.diagnose(
        cur, owner_user_id=owner,
        actor_user_id=owner if actor is None else actor)


def _findings_for(payload: dict, name: str) -> list[dict]:
    return [f for f in payload.get("findings") or []
            if f.get("check") == name]


def setup_environment() -> None:
    schema.reset_schema_cache()
    conn, cur = _connect()
    # The crypto ledger the projection check reconciles against. Created here
    # because without it that check is genuinely inconclusive — which is correct
    # behaviour, but would make every stage below assert against a report that
    # is one check short, and the clean-store stage in particular is meant to
    # prove that all six ran.
    cur.execute(
        "CREATE TABLE IF NOT EXISTS portfolio_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
        "symbol TEXT NOT NULL, coin_name TEXT NOT NULL DEFAULT '', "
        "amount REAL NOT NULL DEFAULT 0, average_buy_price REAL)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Journey: a sound store, and a report that says what it looked at
# ---------------------------------------------------------------------------

def stage_clean_store() -> None:
    print("\n[clean store]")
    conn, cur = _connect()
    try:
        person = _node(cur, USER_A, model.NODE_PERSON, "member-a")
        house = _node(cur, USER_A, model.NODE_PROPERTY, "house-a")
        _edge(cur, USER_A, person, model.RELATION_OWNS, house)

        payload = _diagnose(cur)
        check("a sound store answers ok", payload.get("ok") is True, payload)
        check("and is reported healthy", payload.get("healthy") is True,
              payload.get("findings"))
        check("with no findings", payload["findings"] == [],
              payload["findings"])
        check("every declared check ran",
              payload["totals"]["checks_run"] == payload["totals"]["checks_total"],
              payload["totals"])
        check("no check is inconclusive",
              payload["totals"]["inconclusive"] == [], payload["totals"])
        # The distinction the whole surface turns on: "clean" over nothing and
        # "clean" over real rows must not render identically.
        check("the report states how many nodes it examined",
              payload["examined"]["nodes"] >= 2, payload["examined"])
        check("the report states how many edges it examined",
              payload["examined"]["edges"] >= 1, payload["examined"])
        check("the checks map names every declared check",
              set(payload["checks"]) == set(integrity.CHECKS),
              sorted(payload["checks"]))
        check("the payload states that it repairs nothing",
              "repair" in (payload.get("basis") or {}),
              sorted(payload.get("basis") or {}))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: damage the writers make impossible, introduced by hand
# ---------------------------------------------------------------------------

def stage_cross_owner_edge() -> None:
    """An edge owned by A whose endpoint belongs to B.

    ``record_edge`` refuses to create this, which is exactly why it is written
    with raw SQL here: the check exists for the state the writer cannot reach —
    a bad migration, a restored backup, a direct fix in a console.
    """
    print("\n[cross-owner edge]")
    conn, cur = _connect()
    try:
        a_person = _node(cur, USER_A, model.NODE_PERSON, "cross-a")
        a_asset = _node(cur, USER_A, model.NODE_ASSET, "cross-asset-a")
        b_asset = _node(cur, USER_B, model.NODE_ASSET, "cross-asset-b")
        edge_id = _edge(cur, USER_A, a_person, model.RELATION_OWNS, a_asset)

        # Repoint A's edge at B's node, behind the writer's back.
        cur.execute(
            f"UPDATE {schema.EDGES_TABLE} SET target_node_id = ? WHERE id = ?",
            [b_asset, edge_id])

        payload = _diagnose(cur)
        found = _findings_for(payload, "cross_owner_edges")
        check("the escaped endpoint is found", len(found) >= 1,
              payload["findings"])
        check("the finding names the edge", any(
            f["subject"] == "edge" and f["subject_id"] == edge_id
            for f in found), found)
        check("it is classed as an invariant violation, not drift",
              all(f["severity"] == "invariant" for f in found), found)
        check("the store is no longer called healthy",
              payload["healthy"] is False, payload["totals"])
        check("the invariant count agrees with the findings",
              payload["totals"]["invariant_violations"] >= 1,
              payload["totals"])
        # The one thing a diagnostic must not do while proving a leak.
        blob = str(payload)
        check("the finding never names the other owner",
              str(USER_B) not in blob, blob[:400])
        check("the finding never quotes the foreign node's reference",
              "cross-asset-b" not in blob, blob[:400])
        conn.commit()
    finally:
        conn.close()


def stage_orphan_edge() -> None:
    print("\n[orphan endpoint]")
    conn, cur = _connect()
    try:
        person = _node(cur, USER_A, model.NODE_PERSON, "orphan-a")
        doomed = _node(cur, USER_A, model.NODE_PROPERTY, "orphan-target")
        edge_id = _edge(cur, USER_A, person, model.RELATION_OWNS, doomed)
        cur.execute(f"DELETE FROM {schema.NODES_TABLE} WHERE id = ?", [doomed])

        payload = _diagnose(cur)
        found = _findings_for(payload, "orphan_edges")
        check("the dangling endpoint is found", len(found) >= 1,
              payload["findings"])
        check("the finding names the edge", any(
            f["subject_id"] == edge_id for f in found), found)
        # Pointing at nothing and pointing at somebody else call for different
        # responses, so they must not collapse into one check.
        check("an orphan is not reported as a cross-owner fault",
              not any(f["subject_id"] == edge_id
                      for f in _findings_for(payload, "cross_owner_edges")),
              payload["findings"])
        check("the store is not healthy", payload["healthy"] is False)
        conn.commit()
    finally:
        conn.close()


def stage_duplicate_node_key() -> None:
    """The check whose whole point is that the database forbids it.

    ``UNIQUE(owner_user_id, node_key)`` makes this unreachable — until a
    migration rebuilds the table without the constraint, at which point every
    reader silently starts picking whichever row it saw first.
    """
    print("\n[duplicate node key]")
    conn, cur = _connect()
    try:
        original = _node(cur, USER_A, model.NODE_CONTRACT, "dup-contract")
        cur.execute(
            f"SELECT node_key, node_type, sensitivity, domain, lifecycle_state,"
            f" created_at, updated_at FROM {schema.NODES_TABLE} WHERE id = ?",
            [original])
        row = dict(cur.fetchone())

        # The duplicate cannot be inserted while the constraint stands — the
        # attempt raises, which is the database doing its job. So the stage
        # reproduces the scenario the check is actually for: a migration that
        # rebuilds the table and loses the UNIQUE clause. The table is renamed
        # aside, recreated without it, refilled, and restored afterwards.
        cur.execute(f"ALTER TABLE {schema.NODES_TABLE} "
                    f"RENAME TO nodes_with_constraint")
        cur.execute(
            f"CREATE TABLE {schema.NODES_TABLE} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "owner_user_id INTEGER NOT NULL, node_key TEXT NOT NULL, "
            "node_type TEXT NOT NULL, external_ref TEXT NOT NULL DEFAULT '', "
            "lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE', "
            "sensitivity TEXT NOT NULL, domain TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        cur.execute(f"INSERT INTO {schema.NODES_TABLE} "
                    f"SELECT * FROM nodes_with_constraint")
        cur.execute(
            f"INSERT INTO {schema.NODES_TABLE} "
            f"(owner_user_id, node_key, node_type, external_ref, "
            f" lifecycle_state, sensitivity, domain, created_at, updated_at) "
            f"VALUES (?,?,?,?,?,?,?,?,?)",
            [USER_A, row["node_key"], row["node_type"], "",
             row["lifecycle_state"], row["sensitivity"], row["domain"],
             row["created_at"], row["updated_at"]])
        smuggled = int(cur.lastrowid)

        payload = _diagnose(cur)
        found = _findings_for(payload, "duplicate_node_identity")
        check("the duplicate key is found", len(found) >= 1,
              payload["findings"])
        check("the finding names the second row", any(
            f["subject_id"] == smuggled for f in found), found)
        check("it is classed as an invariant violation",
              all(f["severity"] == "invariant" for f in found), found)
        check("the store is not healthy", payload["healthy"] is False)

        cur.execute(f"DELETE FROM {schema.NODES_TABLE} WHERE id = ?",
                    [smuggled])
        cur.execute(f"DROP TABLE {schema.NODES_TABLE}")
        cur.execute(f"ALTER TABLE nodes_with_constraint "
                    f"RENAME TO {schema.NODES_TABLE}")
        conn.commit()
    finally:
        conn.close()


def stage_unknown_vocabulary() -> None:
    print("\n[unrecognised vocabulary]")
    conn, cur = _connect()
    try:
        node_id = _node(cur, USER_A, model.NODE_ASSET, "vocab-a")
        cur.execute(
            f"UPDATE {schema.NODES_TABLE} SET sensitivity = ? WHERE id = ?",
            ["ULTRA_SECRET", node_id])

        payload = _diagnose(cur)
        found = _findings_for(payload, "unknown_vocabulary")
        check("a sensitivity no gate can rank is found", len(found) >= 1,
              payload["findings"])
        check("the finding names the node", any(
            f["subject_id"] == node_id for f in found), found)
        check("the finding names the field, not the value",
              all("ULTRA_SECRET" not in str(f["detail"]) for f in found),
              found)
        check("the store is not healthy", payload["healthy"] is False)

        cur.execute(
            f"UPDATE {schema.NODES_TABLE} SET sensitivity = ? WHERE id = ?",
            [model.SENSITIVITY_CONFIDENTIAL, node_id])
        conn.commit()
    finally:
        conn.close()


def stage_edge_into_retired_node() -> None:
    print("\n[active edge into a retired node]")
    conn, cur = _connect()
    try:
        person = _node(cur, USER_A, model.NODE_PERSON, "retire-a")
        holding = _node(cur, USER_A, model.NODE_ASSET, "retire-asset")
        edge_id = _edge(cur, USER_A, person, model.RELATION_OWNS, holding)
        cur.execute(
            f"UPDATE {schema.NODES_TABLE} SET lifecycle_state = ? WHERE id = ?",
            [model.LIFECYCLE_ARCHIVED, holding])

        payload = _diagnose(cur)
        found = _findings_for(payload, "edges_into_retired_nodes")
        check("the stale relationship is found", len(found) >= 1,
              payload["findings"])
        check("the finding names the edge",
              any(f["subject_id"] == edge_id for f in found), found)
        # Drift, not an invariant break: this state is reachable through the
        # writers and is a housekeeping matter, not a corruption.
        check("it is classed as drift rather than an invariant violation",
              all(f["severity"] == "drift" for f in found), found)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: the two refusals
# ---------------------------------------------------------------------------

def stage_skipped_is_not_clean() -> None:
    """A check that cannot run must not be reported as one that passed."""
    print("\n[skipped is not clean]")
    conn, cur = _connect()
    original = integrity._portfolio.reconcile
    try:
        person = _node(cur, CLEAN_OWNER, model.NODE_PERSON, "skip-owner")
        thing = _node(cur, CLEAN_OWNER, model.NODE_ASSET, "skip-asset")
        _edge(cur, CLEAN_OWNER, person, model.RELATION_OWNS, thing)
        check("the stage's own store is sound before the check is disabled",
              _diagnose(cur, CLEAN_OWNER)["healthy"] is True,
              _diagnose(cur, CLEAN_OWNER)["findings"])

        integrity._portfolio.reconcile = (
            lambda *a, **k: {"ok": False, "drift": [], "repaired": False})
        payload = _diagnose(cur, CLEAN_OWNER)
        check("the refused check is named inconclusive",
              payload["checks"]["portfolio_projection_drift"] == "inconclusive",
              payload["checks"])
        check("and appears in the inconclusive list",
              "portfolio_projection_drift"
              in payload["totals"]["inconclusive"], payload["totals"])
        check("a store with an unrun check is never called healthy",
              payload["healthy"] is False, payload["totals"])
        check("checks_run drops below checks_total",
              payload["totals"]["checks_run"]
              < payload["totals"]["checks_total"], payload["totals"])
        check("and the result is not called complete",
              payload["totals"]["complete"] is False, payload["totals"])

        def _explode(*_a, **_k):
            raise RuntimeError("reconcile is broken")

        integrity._portfolio.reconcile = _explode
        payload = _diagnose(cur, CLEAN_OWNER)
        check("a check that raises is caught, not propagated",
              payload.get("ok") is True, payload)
        check("and is also reported inconclusive rather than clean",
              payload["checks"]["portfolio_projection_drift"] == "inconclusive",
              payload["checks"])
        conn.commit()
    finally:
        integrity._portfolio.reconcile = original
        conn.close()


def stage_owner_isolation() -> None:
    print("\n[owner isolation]")
    conn, cur = _connect()
    try:
        payload = integrity.diagnose(
            cur, owner_user_id=USER_A, actor_user_id=USER_B)
        check("a stranger is refused", payload.get("ok") is False, payload)
        check("with the shared reason spelling",
              (payload.get("denied") or {}).get("reason")
              == integrity.DENIED_NOT_OWNER, payload.get("denied"))
        # The refusal must not be readable as a clean bill of health.
        check("the refusal does not claim health",
              payload.get("healthy") is None, payload)
        check("the refusal carries no findings",
              payload["findings"] == [], payload)
        check("the refusal carries no check results",
              payload["checks"] == {}, payload)

        zero = integrity.diagnose(cur, owner_user_id=0, actor_user_id=0)
        check("owner 0 is refused rather than served an empty report",
              zero.get("ok") is False, zero)
        conn.commit()
    finally:
        conn.close()


def stage_it_repairs_nothing() -> None:
    """The load-bearing promise, asserted against the rows rather than the prose."""
    print("\n[repairs nothing]")
    conn, cur = _connect()
    try:
        person = _node(cur, USER_A, model.NODE_PERSON, "norepair-a")
        target = _node(cur, USER_A, model.NODE_PROPERTY, "norepair-target")
        edge_id = _edge(cur, USER_A, person, model.RELATION_OWNS, target)
        cur.execute(f"DELETE FROM {schema.NODES_TABLE} WHERE id = ?", [target])

        before = _diagnose(cur)
        check("the fault is found the first time",
              any(f["subject_id"] == edge_id
                  for f in _findings_for(before, "orphan_edges")),
              before["findings"])

        cur.execute(
            f"SELECT COUNT(*) AS n FROM {schema.EDGES_TABLE} WHERE id = ?",
            [edge_id])
        still_there = int(dict(cur.fetchone())["n"])
        check("the broken edge was not deleted by the diagnostic",
              still_there == 1, still_there)
        cur.execute(
            f"SELECT COUNT(*) AS n FROM {schema.NODES_TABLE} WHERE id = ?",
            [target])
        check("the missing node was not recreated by the diagnostic",
              int(dict(cur.fetchone())["n"]) == 0)

        after = _diagnose(cur)
        check("and the fault is still there on the second read",
              any(f["subject_id"] == edge_id
                  for f in _findings_for(after, "orphan_edges")),
              after["findings"])
        check("the payload says so in words a client can render",
              "not been altered" in str(after["basis"]["repair"]).lower()
              or "diagnostic only" in str(after["basis"]["repair"]).lower(),
              after["basis"]["repair"])
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §138 — the mutations. Each must be caught by a named value failure.
# ---------------------------------------------------------------------------

def stage_mutation_battery() -> None:
    """Prove the stages above are load-bearing rather than decorative.

    Each mutation is a plausible "simplification" of the module. A mutation that
    survives means the stage that should have caught it is asserting on
    something other than the value that matters.
    """
    print("\n[mutation battery]")
    original_healthy_source = integrity.diagnose

    def _run(label: str, patch, stage) -> None:
        before = len(_FAILURES)
        undo = patch()
        try:
            stage()
        except Exception as exc:  # noqa: BLE001
            # A crash is not a catch. §138 is explicit: the mutation must be
            # detected by an assertion on a value, because a traceback proves
            # only that the code was reachable.
            _FAILURES.append(
                f"mutation {label} crashed instead of failing a check: {exc!r}")
            print(f"  FAIL  mutation {label} crashed rather than being caught")
            undo()
            return
        undo()
        caught = len(_FAILURES) > before
        if caught:
            # Roll the deliberate failures back off the ledger — they were the
            # expected outcome, not a defect in the suite.
            del _FAILURES[before:]
            print(f"  PASS  mutation {label} was caught")
        else:
            _FAILURES.append(f"mutation {label} survived — the stage is vacuous")
            print(f"  FAIL  mutation {label} survived")

    # A — healthy ignores whether checks were skipped.
    def patch_a():
        src = integrity.diagnose

        def lenient(cur, *, owner_user_id, actor_user_id):
            out = src(cur, owner_user_id=owner_user_id,
                      actor_user_id=actor_user_id)
            if out.get("ok"):
                out["healthy"] = not out["findings"]
            return out
        integrity.diagnose = lenient
        return lambda: setattr(integrity, "diagnose", src)

    _run("A (healthy ignores skipped checks)", patch_a, stage_skipped_is_not_clean)

    # B — a cross-owner endpoint is treated as an ordinary orphan.
    def patch_b():
        src = integrity._edge_checks

        def blind(cur, *, owner):
            found, seen = src(cur, owner=owner)
            return ([f for f in found if f["check"] != "cross_owner_edges"],
                    seen)
        integrity._edge_checks = blind
        return lambda: setattr(integrity, "_edge_checks", src)

    _run("B (cross-owner edges dropped)", patch_b, stage_cross_owner_edge)

    # C — the finding proves the leak by quoting the other owner.
    def patch_c():
        src = integrity._finding

        def chatty(check_, subject, subject_id, detail):
            out = src(check_, subject, subject_id, detail)
            if check_ == "cross_owner_edges":
                out["detail"] = f"{detail} (owner {USER_B})"
            return out
        integrity._finding = chatty
        return lambda: setattr(integrity, "_finding", src)

    _run("C (finding names the foreign owner)", patch_c, stage_cross_owner_edge)

    # D — the refusal is a clean report with the data removed.
    def patch_d():
        src = integrity._denied
        integrity._denied = lambda reason: dict(
            src(reason), healthy=True)
        return lambda: setattr(integrity, "_denied", src)

    _run("D (refusal claims health)", patch_d, stage_owner_isolation)

    # E — every finding is drift, so nothing ever escalates.
    def patch_e():
        src = integrity._finding

        def flattened(check_, subject, subject_id, detail):
            return dict(src(check_, subject, subject_id, detail),
                        severity="drift")
        integrity._finding = flattened
        return lambda: setattr(integrity, "_finding", src)

    _run("E (invariant violations demoted to drift)", patch_e,
         stage_cross_owner_edge)

    integrity.diagnose = original_healthy_source


STAGES = (
    stage_clean_store,
    stage_cross_owner_edge,
    stage_orphan_edge,
    stage_duplicate_node_key,
    stage_unknown_vocabulary,
    stage_edge_into_retired_node,
    stage_skipped_is_not_clean,
    stage_owner_isolation,
    stage_it_repairs_nothing,
    stage_mutation_battery,
)


def test_integrity_suite():
    """pytest entry point. One test so a stage failure names the stage."""
    assert main() == 0


def main() -> int:
    _FAILURES.clear()
    print("PRIVATE OFFICE — CAPITAL INTEGRITY DIAGNOSTICS")
    setup_environment()
    for stage in STAGES:
        stage()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAILED — {len(_FAILURES)} check(s):")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("ALL STAGES PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
