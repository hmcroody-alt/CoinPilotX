"""Portfolio → Capital Graph projection journeys.

Run either way::

    python -m pytest tests/private_office/test_portfolio_projection.py
    python tests/private_office/test_portfolio_projection.py

What this file defends
----------------------
The Capital Graph never owns holdings — it is a projection of the Portfolio
ledger, delivered through the ``portfolio_outbox`` and settled by one
convergent projector. Every promise that architecture makes is exercised here
as a member journey:

* **Add** — a new lot becomes an ACTIVE asset node, an OWNS edge, and
  quantity/lot-count/name/basis facts, each carrying provenance.
* **Edit** — a changed amount supersedes the old quantity fact; exactly one
  stays ACTIVE, and the old value remains readable as history.
* **Lots aggregate** — two BTC lots are one node with summed quantity, never
  two nodes.
* **Basis honesty** — one basis-less lot makes the aggregate basis unknowable,
  so the previously-known basis fact is retired. Never fabricated, never
  partial, never stale.
* **Remove** — a sold symbol is ARCHIVED, not deleted: the node survives as
  history, the OWNS edge and facts are retired.
* **Re-buy** — the archived node is reactivated, not duplicated.
* **Idempotent and convergent** — projecting twice changes nothing; N pending
  events are settled by one projection; a failed projection marks events
  FAILED and degrades instead of raising.
* **Tenant isolation** — one member's journeys never touch another's graph.
* **Prices live at read time** — two reads against different market boards
  give different values with zero new events and zero price facts stored.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="portfolio_projection_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ.setdefault("PORTFOLIO_CAPITAL_PROJECTION_ENABLED", "1")

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from services import db  # noqa: E402
from services import market_data  # noqa: E402
from services import portfolio_events  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import graph  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import portfolio_projection as projection  # noqa: E402
from services.private_office import schema  # noqa: E402

USER_A = 9911
USER_B = 9912

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
    portfolio_events.ensure_outbox_schema(cur)
    return conn, cur


def _board(symbols: dict[str, float]) -> dict:
    return {
        "source": "coingecko", "observed_epoch": 1_756_700_000,
        "age_seconds": 12, "warning": "",
        "markets": [
            {"symbol": symbol, "price": price, "change_24h": 1.5}
            for symbol, price in symbols.items()
        ],
    }


def _add_lot(cur, user_id: int, symbol: str, name: str, amount: float,
             basis: object) -> int:
    cur.execute(
        "INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, "
        "average_buy_price) VALUES (?,?,?,?,?)",
        (user_id, symbol, name, amount, basis))
    row_id = int(cur.lastrowid)
    portfolio_events.enqueue(cur, user_id=user_id,
                             event_type=portfolio_events.EVENT_HOLDING_ADDED,
                             item_id=row_id, symbol=symbol)
    return row_id


def _asset_node(cur, owner: int, symbol: str, *,
                include_inactive: bool = False) -> dict | None:
    rows = graph.list_nodes(cur, owner_user_id=owner,
                            node_types=[model.NODE_ASSET],
                            include_inactive=include_inactive, limit=100)
    for row in rows:
        if str(row.get("external_ref") or "") == f"portfolio:{symbol}":
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


def _owns_edges(cur, owner: int, asset_row_id: int, *,
                include_inactive: bool = False) -> list[dict]:
    return graph.neighbors(
        cur, owner_user_id=owner, node_id=asset_row_id,
        relations=[model.RELATION_OWNS], direction=graph.DIRECTION_IN,
        include_inactive=include_inactive)


def setup_environment() -> None:
    schema.reset_schema_cache()
    portfolio_events.reset_outbox_schema_cache()
    conn, cur = _connect()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS portfolio_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
        "symbol TEXT NOT NULL, coin_name TEXT NOT NULL DEFAULT '', "
        "amount REAL NOT NULL DEFAULT 0, average_buy_price REAL)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Journey: add a holding
# ---------------------------------------------------------------------------

def stage_add_journey() -> None:
    print("\n[add journey]")
    conn, cur = _connect()
    try:
        _add_lot(cur, USER_A, "BTC", "Bitcoin", 0.5, 40000.0)
        result = projection.drain(cur, user_id=USER_A)
        check("drain settles the ADDED event", result == {"ok": True, "processed": 1}, result)

        node = _asset_node(cur, USER_A, "BTC")
        check("asset node portfolio:BTC is ACTIVE", node is not None
              and node.get("lifecycle_state") == model.LIFECYCLE_ACTIVE, node)
        person = _person_node(cur, USER_A)
        check("person node exists with the owner's ref", person is not None
              and person.get("external_ref") == f"user:{USER_A}", person)
        if node is None or person is None:
            return

        edges = _owns_edges(cur, USER_A, int(node["id"]))
        check("exactly one ACTIVE OWNS edge points at the asset",
              len(edges) == 1, edges)
        if edges:
            check("the OWNS edge is user-asserted, from the person node",
                  edges[0].get("provenance_type") == model.PROVENANCE_USER_ASSERTED
                  and int(edges[0].get("other_node_id") or 0) == int(person["id"]),
                  edges[0])

        node_id = int(node["id"])
        qty = _node_facts(cur, USER_A, node_id, projection.FACT_QUANTITY)
        check("one ACTIVE quantity fact, value 0.5",
              len(qty) == 1 and float(qty[0].get("value_number") or 0) == 0.5, qty)
        lots = _node_facts(cur, USER_A, node_id, projection.FACT_LOT_COUNT)
        check("one ACTIVE lot_count fact, value 1",
              len(lots) == 1 and float(lots[0].get("value_number") or 0) == 1.0, lots)
        name = _node_facts(cur, USER_A, node_id, projection.FACT_ASSET_NAME)
        check("one ACTIVE asset_name fact, 'Bitcoin'",
              len(name) == 1 and name[0].get("typed_value") == "Bitcoin", name)
        basis = _node_facts(cur, USER_A, node_id, projection.FACT_COST_BASIS)
        check("one ACTIVE cost_basis fact, value 20000 (0.5 × 40000)",
              len(basis) == 1 and float(basis[0].get("value_number") or 0) == 20000.0,
              basis)
        if qty:
            prov = qty[0].get("provenance") or {}
            check("quantity fact provenance names the portfolio ledger",
                  "portfolio" in str(prov.get("source_type") or "").lower()
                  or "portfolio" in str(prov.get("source_ref") or "").lower(), prov)

        status = portfolio_events.sync_status(cur, user_id=USER_A)
        check("outbox is settled after the drain",
              status.get("pending") == 0 and status.get("failed") == 0, status)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: edit the holding
# ---------------------------------------------------------------------------

def stage_edit_journey() -> None:
    print("\n[edit journey]")
    conn, cur = _connect()
    try:
        cur.execute(
            "UPDATE portfolio_items SET amount = 0.8 "
            "WHERE user_id = ? AND symbol = 'BTC'", (USER_A,))
        portfolio_events.enqueue(
            cur, user_id=USER_A,
            event_type=portfolio_events.EVENT_HOLDING_UPDATED, symbol="BTC")
        result = projection.drain(cur, user_id=USER_A)
        check("drain settles the UPDATED event",
              result == {"ok": True, "processed": 1}, result)

        node = _asset_node(cur, USER_A, "BTC")
        if not check("asset node still ACTIVE after the edit", node is not None, node):
            return
        node_id = int(node["id"])
        qty = _node_facts(cur, USER_A, node_id, projection.FACT_QUANTITY)
        check("exactly one ACTIVE quantity fact after the edit",
              len(qty) == 1, qty)
        check("the ACTIVE quantity is the new value 0.8",
              bool(qty) and float(qty[0].get("value_number") or 0) == 0.8, qty)
        history = _node_facts(cur, USER_A, node_id, projection.FACT_QUANTITY,
                              include_superseded=True)
        check("the old quantity 0.5 survives as superseded history",
              any(float(f.get("value_number") or 0) == 0.5
                  and f.get("lifecycle_state") != model.LIFECYCLE_ACTIVE
                  for f in history), history)
        check("basis follows the ledger, 0.8 × 40000 = 32000",
              (lambda rows: len(rows) == 1
               and float(rows[0].get("value_number") or 0) == 32000.0)(
                  _node_facts(cur, USER_A, node_id, projection.FACT_COST_BASIS)))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: a second lot aggregates, and a basis-less lot retires the basis
# ---------------------------------------------------------------------------

def stage_lots_and_basis_honesty() -> None:
    print("\n[lots aggregate, basis honesty]")
    conn, cur = _connect()
    try:
        _add_lot(cur, USER_A, "BTC", "Bitcoin", 0.2, 50000.0)
        projection.drain(cur, user_id=USER_A)

        active_assets = [r for r in graph.list_nodes(
            cur, owner_user_id=USER_A, node_types=[model.NODE_ASSET], limit=100)
            if str(r.get("external_ref") or "").startswith("portfolio:BTC")]
        check("two BTC lots are still one asset node", len(active_assets) == 1,
              active_assets)
        if not active_assets:
            return
        node_id = int(active_assets[0]["id"])
        qty = _node_facts(cur, USER_A, node_id, projection.FACT_QUANTITY)
        check("quantity is the lot sum 1.0",
              len(qty) == 1 and float(qty[0].get("value_number") or 0) == 1.0, qty)
        lots = _node_facts(cur, USER_A, node_id, projection.FACT_LOT_COUNT)
        check("lot_count is 2",
              len(lots) == 1 and float(lots[0].get("value_number") or 0) == 2.0, lots)
        basis = _node_facts(cur, USER_A, node_id, projection.FACT_COST_BASIS)
        check("basis is the lot sum 42000 (32000 + 10000)",
              len(basis) == 1 and float(basis[0].get("value_number") or 0) == 42000.0,
              basis)

        # Now a lot with no stated basis: the aggregate becomes unknowable and
        # the previously-known basis fact must be retired, not kept partial.
        _add_lot(cur, USER_A, "BTC", "Bitcoin", 0.1, None)
        projection.drain(cur, user_id=USER_A)
        basis = _node_facts(cur, USER_A, node_id, projection.FACT_COST_BASIS)
        check("a basis-less lot retires the basis fact entirely",
              len(basis) == 0, basis)
        qty = _node_facts(cur, USER_A, node_id, projection.FACT_QUANTITY)
        check("quantity still aggregates the basis-less lot, 1.1",
              len(qty) == 1
              and abs(float(qty[0].get("value_number") or 0) - 1.1) < 1e-9, qty)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Journey: sell everything, then buy back in
# ---------------------------------------------------------------------------

def stage_remove_and_rebuy() -> None:
    print("\n[remove journey, re-buy reactivation]")
    conn, cur = _connect()
    try:
        node_before = _asset_node(cur, USER_A, "BTC")
        if not check("precondition: BTC node ACTIVE before the sale",
                     node_before is not None):
            return
        node_id = int(node_before["id"])

        cur.execute("DELETE FROM portfolio_items WHERE user_id = ? AND symbol = 'BTC'",
                    (USER_A,))
        portfolio_events.enqueue(
            cur, user_id=USER_A,
            event_type=portfolio_events.EVENT_HOLDING_REMOVED, symbol="BTC")
        projection.drain(cur, user_id=USER_A)

        check("sold node is gone from ACTIVE reads",
              _asset_node(cur, USER_A, "BTC") is None)
        archived = _asset_node(cur, USER_A, "BTC", include_inactive=True)
        check("sold node is ARCHIVED, not deleted",
              archived is not None
              and archived.get("lifecycle_state") == model.LIFECYCLE_ARCHIVED,
              archived)
        check("OWNS edge is retired, not deleted",
              _owns_edges(cur, USER_A, node_id) == []
              and len(_owns_edges(cur, USER_A, node_id, include_inactive=True)) == 1)
        for fact_type in projection.PROJECTED_FACT_TYPES:
            check(f"{fact_type} has no ACTIVE fact after the sale",
                  _node_facts(cur, USER_A, node_id, fact_type) == [])

        # Buy back in: the archived node reactivates rather than duplicating.
        _add_lot(cur, USER_A, "BTC", "Bitcoin", 0.3, 60000.0)
        projection.drain(cur, user_id=USER_A)
        node_after = _asset_node(cur, USER_A, "BTC")
        check("re-buy reactivates the same node",
              node_after is not None and int(node_after["id"]) == node_id,
              node_after)
        all_btc = [r for r in graph.list_nodes(
            cur, owner_user_id=USER_A, node_types=[model.NODE_ASSET],
            include_inactive=True, limit=100)
            if str(r.get("external_ref") or "") == "portfolio:BTC"]
        check("no duplicate BTC node was created", len(all_btc) == 1, all_btc)
        qty = _node_facts(cur, USER_A, node_id, projection.FACT_QUANTITY)
        check("re-buy quantity fact is the new 0.3",
              len(qty) == 1 and float(qty[0].get("value_number") or 0) == 0.3, qty)
        check("re-buy restores exactly one ACTIVE OWNS edge",
              len(_owns_edges(cur, USER_A, node_id)) == 1)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Idempotency, convergence, failure degradation
# ---------------------------------------------------------------------------

def _graph_fingerprint(cur, owner: int) -> tuple:
    nodes = graph.list_nodes(cur, owner_user_id=owner, include_inactive=True,
                             limit=500)
    rows = facts.list_facts(cur, owner_user_id=owner, include_superseded=True,
                            limit=500)
    return (
        tuple(sorted((int(n["id"]), str(n.get("lifecycle_state"))) for n in nodes)),
        tuple(sorted((int(f["id"]), str(f.get("lifecycle_state"))) for f in rows)),
    )


def stage_idempotency_and_convergence() -> None:
    print("\n[idempotency, convergence]")
    conn, cur = _connect()
    try:
        before = _graph_fingerprint(cur, USER_A)
        result = projection.project_user(cur, user_id=USER_A)
        check("re-projecting a settled state reports ok", result.get("ok") is True,
              result)
        check("re-projection changes nothing — same nodes, same facts",
              _graph_fingerprint(cur, USER_A) == before)
        check("drain with nothing pending is a no-op",
              projection.drain(cur, user_id=USER_A) == {"ok": True, "processed": 0})

        # Three rapid-fire events settle under one projection.
        for _ in range(3):
            portfolio_events.enqueue(
                cur, user_id=USER_A,
                event_type=portfolio_events.EVENT_HOLDING_UPDATED, symbol="BTC")
        result = projection.drain(cur, user_id=USER_A)
        check("one drain settles all three pending events",
              result == {"ok": True, "processed": 3}, result)
        status = portfolio_events.sync_status(cur, user_id=USER_A)
        check("nothing left pending after the convergent drain",
              status.get("pending") == 0, status)
        conn.commit()
    finally:
        conn.close()


def stage_failure_degrades() -> None:
    print("\n[failure marks FAILED, never raises]")
    conn, cur = _connect()
    original = projection.project_user
    try:
        portfolio_events.enqueue(
            cur, user_id=USER_A,
            event_type=portfolio_events.EVENT_HOLDING_UPDATED, symbol="BTC")

        def _boom(cur, *, user_id):
            raise RuntimeError("projector wiring failure")

        projection.project_user = _boom
        result = projection.drain(cur, user_id=USER_A)
        check("a projector crash degrades to ok:False, no raise",
              result == {"ok": False, "processed": 0}, result)
        status = portfolio_events.sync_status(cur, user_id=USER_A)
        check("the crashed event is marked FAILED and visible in sync status",
              status.get("failed") == 1 and status.get("pending") == 0, status)

        projection.project_user = original
        portfolio_events.enqueue(
            cur, user_id=USER_A,
            event_type=portfolio_events.EVENT_HOLDING_UPDATED, symbol="BTC")
        result = projection.drain(cur, user_id=USER_A)
        check("recovery: the next event drains cleanly",
              result == {"ok": True, "processed": 1}, result)
        conn.commit()
    finally:
        projection.project_user = original
        conn.close()


# ---------------------------------------------------------------------------
# Reconcile: drift is reported, repair converges
# ---------------------------------------------------------------------------

def stage_reconcile() -> None:
    print("\n[reconcile]")
    conn, cur = _connect()
    try:
        result = projection.reconcile(cur, user_id=USER_A)
        check("a settled graph reconciles with zero drift",
              result.get("ok") is True and result.get("drift") == [], result)

        # A ledger write that never produced an event — the drift reconcile exists for.
        cur.execute(
            "INSERT INTO portfolio_items (user_id, symbol, coin_name, amount, "
            "average_buy_price) VALUES (?,?,?,?,?)",
            (USER_A, "ETH", "Ethereum", 2.0, 3000.0))
        result = projection.reconcile(cur, user_id=USER_A)
        check("an event-less ledger write is reported as drift",
              any(d.get("symbol") == "ETH" for d in (result.get("drift") or [])),
              result)
        result = projection.reconcile(cur, user_id=USER_A, repair=True)
        check("repair converges — drift empty after re-projection",
              result.get("repaired") is True and result.get("drift") == [], result)
        check("repair created the missing ETH node",
              _asset_node(cur, USER_A, "ETH") is not None)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

def stage_tenant_isolation() -> None:
    print("\n[tenant isolation]")
    conn, cur = _connect()
    try:
        check("all of A's journeys left B's graph empty",
              graph.list_nodes(cur, owner_user_id=USER_B, include_inactive=True,
                               limit=500) == [])
        check("and B holds no facts",
              facts.list_facts(cur, owner_user_id=USER_B,
                               include_superseded=True, limit=500) == [])

        _add_lot(cur, USER_B, "DOGE", "Dogecoin", 1000.0, 0.1)
        projection.drain(cur, user_id=USER_B)
        check("B's own projection lands in B's graph",
              _asset_node(cur, USER_B, "DOGE") is not None)
        check("B's projection did not reach into A's graph",
              _asset_node(cur, USER_A, "DOGE", include_inactive=True) is None)
        check("A's BTC is invisible to B",
              _asset_node(cur, USER_B, "BTC", include_inactive=True) is None)

        view = projection.portfolio_view(cur, owner_user_id=USER_A,
                                         actor_user_id=USER_B)
        check("B reading A's portfolio view is denied by name",
              view.get("ok") is False
              and (view.get("denied") or {}).get("reason") == "actor_is_not_owner",
              view)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Prices live at read time — never stored, never events
# ---------------------------------------------------------------------------

def stage_prices_read_time_only() -> None:
    print("\n[live prices at read time]")
    conn, cur = _connect()
    original = market_data.live_market_board
    try:
        cur.execute("SELECT COUNT(*) FROM portfolio_outbox")
        outbox_before = int(cur.fetchone()[0])
        facts_before = _graph_fingerprint(cur, USER_A)

        market_data.live_market_board = lambda **kwargs: _board(
            {"BTC": 60000.0, "ETH": 3500.0})
        first = projection.portfolio_view(cur, owner_user_id=USER_A,
                                          actor_user_id=USER_A)
        market_data.live_market_board = lambda **kwargs: _board(
            {"BTC": 62000.0, "ETH": 3500.0})
        second = projection.portfolio_view(cur, owner_user_id=USER_A,
                                           actor_user_id=USER_A)

        def _value(view, symbol):
            for row in view.get("assets") or view.get("records") or []:
                if row.get("symbol") == symbol:
                    return row.get("value")
            return None

        check("first read prices BTC at the first board (0.3 × 60000)",
              _value(first, "BTC") == 18000.0, _value(first, "BTC"))
        check("second read moves with the market, no re-projection",
              _value(second, "BTC") is not None
              and _value(second, "BTC") != _value(first, "BTC"),
              (_value(first, "BTC"), _value(second, "BTC")))

        cur.execute("SELECT COUNT(*) FROM portfolio_outbox")
        check("reads created no outbox events",
              int(cur.fetchone()[0]) == outbox_before)
        check("reads wrote no nodes and no facts",
              _graph_fingerprint(cur, USER_A) == facts_before)
        all_rows = facts.list_facts(cur, owner_user_id=USER_A,
                                    include_superseded=True, limit=500)
        check("no price was ever stored as a fact",
              all(("price" not in str(f.get("fact_type") or "")
                   and "value" != str(f.get("fact_type") or "")) for f in all_rows))
        conn.commit()
    finally:
        market_data.live_market_board = original
        conn.close()


# ---------------------------------------------------------------------------

def main() -> int:
    print("portfolio projection journeys @", _TMP_DB)
    setup_environment()
    stage_add_journey()
    stage_edit_journey()
    stage_lots_and_basis_honesty()
    stage_remove_and_rebuy()
    stage_idempotency_and_convergence()
    stage_failure_degrades()
    stage_reconcile()
    stage_tenant_isolation()
    stage_prices_read_time_only()
    print(f"\n{'FAILED' if _FAILURES else 'OK'} — {len(_FAILURES)} failure(s)")
    for line in _FAILURES:
        print("  *", line)
    return 1 if _FAILURES else 0


def test_portfolio_projection_journeys() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
