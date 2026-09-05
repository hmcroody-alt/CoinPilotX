"""The Portfolio → Capital Graph projection.

Authority, stated once
----------------------
The PulseSoc Portfolio (``portfolio_items`` + legacy ``manual_portfolio``) is
the ledger: it owns holdings, lots, and cost basis. The market-data service
owns prices. This module owns neither — it owns the *projection*: an
evidence-backed mirror of the ledger inside the member's private capital
graph, plus a read-time valuation of that mirror. Nothing here can create a
holding, change a quantity, or invent a cost basis; edits route through
``services.portfolio_service`` and arrive here as "this user's projection is
behind" events (see ``services.portfolio_events``).

Convergence instead of deltas
-----------------------------
:func:`project_user` re-projects the member's whole portfolio from current
ledger state every time it runs. It never applies an event's contents — events
carry none — so running it twice, out of order, or after missing a dozen
events all land on the same answer. That is what makes the outbox's delivery
guarantees cheap: idempotency and ordering are structural, not promised.

What the projection writes (through canonical writers only)
-----------------------------------------------------------
* one PERSON node for the member (``user:{id}``, GENERAL/CONFIDENTIAL),
* one ASSET node per held symbol (``portfolio:{SYMBOL}``,
  FINANCIAL/CONFIDENTIAL) — lots are the ledger's rows; the graph shows the
  aggregate with the lot count as a fact,
* an OWNS edge between them, provenance USER_ASSERTED (the ledger is
  member-entered; the projection must not launder that into
  PROVIDER_ASSERTED),
* facts in the ``portfolio.*`` namespace: quantity, lot count, asset name,
  and — only when every lot states a basis — cost basis. A partial basis is
  not summed into a fake whole and a missing basis is superseded, never
  fabricated, matching ``portfolio_service._value_holding``.

A changed value supersedes its predecessor (``facts.supersede_facts``): the
old quantity is the ledger's previous state, not a second opinion for the
contradiction engine to weigh. A sold symbol retires the OWNS edge and
archives the node — archived, not deleted, so "you held this until March"
remains answerable; a re-buy reactivates both.

Valuation happens at read time
------------------------------
:func:`portfolio_view` prices the projection with Decimal arithmetic against
``market_data.live_market_board`` and reports the provider's own
``observed_epoch``/``age_seconds`` so the caller can label freshness honestly.
An unpriced asset has value ``None`` — never zero — and totals are only
reported complete when every asset priced. No price is ever stored as a fact:
storing one would stamp a USER_ASSERTED observation time onto a provider
number and make the graph disagree with the board it copied from.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from services import market_data, portfolio_events
from services.private_office import audit as _audit
from services.private_office import facts as _facts
from services.private_office import graph as _graph
from services.private_office import model as _model
from services.private_office import schema as _schema

LOGGER = logging.getLogger("private_office.portfolio_projection")

#: The projection's fact namespace. Nothing else writes ``portfolio.*`` facts,
#: which is what makes `supersede_facts` safe to aim at it.
FACT_QUANTITY = "portfolio.quantity"
FACT_LOT_COUNT = "portfolio.lot_count"
FACT_COST_BASIS = "portfolio.cost_basis"
FACT_ASSET_NAME = "portfolio.asset_name"

PROJECTED_FACT_TYPES: tuple[str, ...] = (
    FACT_QUANTITY, FACT_LOT_COUNT, FACT_COST_BASIS, FACT_ASSET_NAME,
)

#: ASSET nodes this module created are recognisable by this external_ref
#: prefix, which is how a re-projection finds its own prior work without ever
#: touching nodes other writers made.
ASSET_REF_PREFIX = "portfolio:"

PURPOSE = "system_maintenance"

#: More symbols than this in one portfolio and the projection stops early and
#: says so, rather than half-writing. FREE tier caps holdings at 3; this is
#: generous for any real member.
MAX_ASSETS = 200


def _person_ref(owner: int) -> str:
    return f"user:{owner}"


def _asset_ref(symbol: str) -> str:
    return f"{ASSET_REF_PREFIX}{symbol}"


def _clean_symbol(value: object) -> str:
    text = str(value or "").upper().strip()[:16]
    return text if text and _audit._SAFE_OBJECT_ID.match(text) else ""


def _provenance(owner: int, symbol: str = "") -> _facts.ProvenanceRef:
    return _facts.ProvenanceRef(
        source_type="portfolio",
        source_id=_person_ref(owner),
        locator=f"symbol:{symbol}" if symbol else "",
        confidence=1.0,
    )


def read_ledger(cur, *, user_id: int) -> dict[str, dict]:
    """Current holdings aggregated per symbol, straight from the ledger tables.

    Reads the same two tables ``portfolio_service.calculate_user_portfolio``
    reads, deliberately without its market enrichment: a projector that calls
    a third-party price API to decide what the member *holds* would fail to
    project whenever the provider is slow.

    Returns ``{symbol: {"quantity", "lot_count", "cost_basis", "basis_complete",
    "name"}}``. ``cost_basis`` is only trustworthy when ``basis_complete``.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {}
    cur.execute(
        "SELECT symbol, coin_name, amount, average_buy_price "
        "FROM portfolio_items WHERE user_id=? ORDER BY id",
        (owner,),
    )
    lots = [dict(row) for row in cur.fetchall()]
    known = {_clean_symbol(lot.get("symbol")) for lot in lots}
    try:
        cur.execute(
            "SELECT asset, amount FROM manual_portfolio "
            "WHERE user_id=? AND amount > 0 ORDER BY asset",
            (owner,),
        )
        for row in cur.fetchall():
            data = dict(row)
            symbol = _clean_symbol(data.get("asset"))
            if symbol and symbol not in known:
                # Legacy rows carry no basis — an absence, not a zero, same as
                # `calculate_user_portfolio` treats them.
                lots.append({"symbol": symbol, "coin_name": symbol,
                             "amount": data.get("amount"), "average_buy_price": None,
                             "legacy": True})
                known.add(symbol)
    except Exception:
        # The legacy table is optional; its absence is not a failed ledger read.
        pass

    holdings: dict[str, dict] = {}
    for lot in lots:
        symbol = _clean_symbol(lot.get("symbol"))
        if not symbol:
            continue
        amount = float(lot.get("amount") or 0)
        basis = lot.get("average_buy_price")
        basis_value = float(basis or 0)
        entry = holdings.setdefault(symbol, {
            "quantity": 0.0, "lot_count": 0, "cost_basis": 0.0,
            "basis_complete": True, "name": "",
        })
        entry["quantity"] += amount
        entry["lot_count"] += 1
        if basis_value > 0 and not lot.get("legacy"):
            entry["cost_basis"] += amount * basis_value
        else:
            # One lot without a basis makes the aggregate basis unknowable.
            # Summing the lots that do have one would report a fake, low basis
            # and a fake, high profit.
            entry["basis_complete"] = False
        name = str(lot.get("coin_name") or "").strip()
        if name and not entry["name"]:
            entry["name"] = name
    return holdings


def _project_fact(cur, *, owner: int, node_id: int, fact_type: str,
                  value: object, value_type: str, symbol: str) -> None:
    """Record one projected fact and supersede whatever it replaces."""
    written = _facts.record_fact(
        cur,
        owner_user_id=owner,
        subject_type=_facts.SUBJECT_NODE,
        subject_id=node_id,
        fact_type=fact_type,
        value=value,
        value_type=value_type,
        provenance_type=_model.PROVENANCE_USER_ASSERTED,
        provenance=_provenance(owner, symbol),
        sensitivity=_model.SENSITIVITY_CONFIDENTIAL,
        domain=_model.DOMAIN_FINANCIAL,
        actor_user_id=owner,
        purpose=PURPOSE,
    )
    _facts.supersede_facts(
        cur, owner_user_id=owner, subject_type=_facts.SUBJECT_NODE,
        subject_id=node_id, fact_type=fact_type,
        keep_fact_id=int(written.get("fact_id") or 0),
        actor_user_id=owner, purpose=PURPOSE,
    )


def _retire_fact(cur, *, owner: int, node_id: int, fact_type: str) -> None:
    """Supersede every ACTIVE row of one projected fact type, keeping none."""
    _facts.supersede_facts(
        cur, owner_user_id=owner, subject_type=_facts.SUBJECT_NODE,
        subject_id=node_id, fact_type=fact_type, keep_fact_id=0,
        actor_user_id=owner, purpose=PURPOSE,
    )


def _projected_asset_nodes(cur, *, owner: int) -> dict[str, dict]:
    """This owner's projection-made ASSET nodes by symbol, any lifecycle."""
    rows = _graph.list_nodes(
        cur, owner_user_id=owner, node_types=[_model.NODE_ASSET],
        include_inactive=True, limit=500,
    )
    out: dict[str, dict] = {}
    for row in rows:
        ref = str(row.get("external_ref") or "")
        if ref.startswith(ASSET_REF_PREFIX):
            out[ref[len(ASSET_REF_PREFIX):]] = row
    return out


def _ensure_active(cur, *, owner: int, node: dict) -> None:
    if str(node.get("lifecycle_state") or "") != _model.LIFECYCLE_ACTIVE:
        _graph.set_node_lifecycle(
            cur, owner_user_id=owner, node_id=int(node["id"]),
            lifecycle_state=_model.LIFECYCLE_ACTIVE,
            actor_user_id=owner, purpose=PURPOSE,
        )


def project_user(cur, *, user_id: int) -> dict:
    """Re-project one member's whole portfolio into their capital graph.

    Full-state and convergent: reads the ledger, makes the graph match, and
    returns ``{"ok", "assets", "retired", "skipped"}``. Raises only on the
    substrate invariants the canonical writers enforce — a rejected write is a
    projector bug, and surfacing it beats a mirror that silently diverges.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "assets": 0, "retired": 0, "skipped": 0}
    _schema.require_private_schema(cur)

    holdings = read_ledger(cur, user_id=owner)
    skipped = 0
    if len(holdings) > MAX_ASSETS:
        skipped = len(holdings) - MAX_ASSETS
        holdings = dict(sorted(holdings.items())[:MAX_ASSETS])

    person = _graph.upsert_node(
        cur, owner_user_id=owner, node_type=_model.NODE_PERSON,
        external_ref=_person_ref(owner),
        sensitivity=_model.SENSITIVITY_CONFIDENTIAL,
        domain=_model.DOMAIN_GENERAL, actor_user_id=owner, purpose=PURPOSE,
    )
    person_node = _graph.get_node(cur, owner_user_id=owner,
                                  node_id=person["node_id"])
    if person_node is not None:
        _ensure_active(cur, owner=owner, node=person_node)

    existing = _projected_asset_nodes(cur, owner=owner)

    for symbol, entry in sorted(holdings.items()):
        node_row = existing.get(symbol)
        if node_row is None:
            created = _graph.upsert_node(
                cur, owner_user_id=owner, node_type=_model.NODE_ASSET,
                external_ref=_asset_ref(symbol),
                sensitivity=_model.SENSITIVITY_CONFIDENTIAL,
                domain=_model.DOMAIN_FINANCIAL, actor_user_id=owner,
                purpose=PURPOSE,
            )
            node_row = _graph.get_node(cur, owner_user_id=owner,
                                       node_id=created["node_id"])
            if node_row is None:
                continue
        # resolve/upsert finds archived nodes but never reactivates them; a
        # re-buy must, or the node stays invisible to every ACTIVE-only read.
        _ensure_active(cur, owner=owner, node=node_row)
        node_id = int(node_row["id"])

        # record_edge dedupes on its key and re-activates a retired edge, so
        # this is one call for "own it, still own it, and own it again".
        _graph.record_edge(
            cur, owner_user_id=owner, source=int(person["node_id"]),
            relation_type=_model.RELATION_OWNS, target=node_id,
            provenance_type=_model.PROVENANCE_USER_ASSERTED,
            provenance=_provenance(owner, symbol),
            actor_user_id=owner, purpose=PURPOSE,
        )

        _project_fact(cur, owner=owner, node_id=node_id,
                      fact_type=FACT_QUANTITY, value=entry["quantity"],
                      value_type=_model.VALUE_NUMBER, symbol=symbol)
        _project_fact(cur, owner=owner, node_id=node_id,
                      fact_type=FACT_LOT_COUNT, value=entry["lot_count"],
                      value_type=_model.VALUE_NUMBER, symbol=symbol)
        _project_fact(cur, owner=owner, node_id=node_id,
                      fact_type=FACT_ASSET_NAME,
                      value=entry["name"] or symbol,
                      value_type=_model.VALUE_STRING, symbol=symbol)
        if entry["basis_complete"] and entry["cost_basis"] > 0:
            _project_fact(cur, owner=owner, node_id=node_id,
                          fact_type=FACT_COST_BASIS, value=entry["cost_basis"],
                          value_type=_model.VALUE_MONEY, symbol=symbol)
        else:
            # An unknowable basis retires any previously-known one rather than
            # leaving a stale number ACTIVE — never fabricate, never linger.
            _retire_fact(cur, owner=owner, node_id=node_id,
                         fact_type=FACT_COST_BASIS)

    retired = 0
    for symbol, node_row in sorted(existing.items()):
        if symbol in holdings:
            continue
        node_id = int(node_row["id"])
        still_active = (
            str(node_row.get("lifecycle_state") or "") == _model.LIFECYCLE_ACTIVE)
        for edge in _graph.neighbors(
            cur, owner_user_id=owner, node_id=node_id,
            relations=[_model.RELATION_OWNS], direction=_graph.DIRECTION_IN,
        ):
            _graph.retire_edge(cur, owner_user_id=owner,
                               edge_id=int(edge["id"]),
                               actor_user_id=owner, purpose=PURPOSE)
        for fact_type in PROJECTED_FACT_TYPES:
            _retire_fact(cur, owner=owner, node_id=node_id,
                         fact_type=fact_type)
        if still_active:
            _graph.set_node_lifecycle(
                cur, owner_user_id=owner, node_id=node_id,
                lifecycle_state=_model.LIFECYCLE_ARCHIVED,
                actor_user_id=owner, purpose=PURPOSE,
            )
            retired += 1

    return {"ok": True, "assets": len(holdings), "retired": retired,
            "skipped": skipped}


# ---------------------------------------------------------------------------
# Delivery — the consumer side of services.portfolio_events
# ---------------------------------------------------------------------------
def drain(cur, *, user_id: int) -> dict:
    """Settle this user's pending outbox rows against one re-projection.

    Runs on the caller's cursor/transaction. Every pending row is settled by
    the same single :func:`project_user` run — that is the whole point of a
    convergent consumer. Failure marks the claimed rows FAILED (visible in
    ``sync_status``) and reports ``ok: False``; it never raises, because the
    read paths that sweep lazily must degrade to "older but true", not break.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "processed": 0}
    if not portfolio_events.projection_enabled():
        return {"ok": True, "processed": 0}
    pending = portfolio_events.pending_events(cur, user_id=owner)
    if not pending:
        return {"ok": True, "processed": 0}
    ids = [int(event["id"]) for event in pending]
    try:
        project_user(cur, user_id=owner)
    except Exception as exc:  # noqa: BLE001 — reads degrade, never break
        LOGGER.warning("PORTFOLIO_PROJECTION_FAILED user=%s error=%s", owner, exc)
        portfolio_events.mark_failed(cur, user_id=owner, event_ids=ids,
                                     error=str(exc)[:400])
        return {"ok": False, "processed": 0}
    portfolio_events.mark_processed(cur, user_id=owner, event_ids=ids)
    return {"ok": True, "processed": len(ids)}


def process_pending(user_id: int) -> dict:
    """:func:`drain` on its own connection — the post-commit kick target.

    ``portfolio_service`` calls this after its own commit, best-effort. It
    opens, drains, commits, closes, and never raises: the mutation it follows
    has already succeeded and must stay succeeded.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "processed": 0}
    try:
        from services import db as _db
        conn = _db.connect()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("PORTFOLIO_PROJECTION_CONNECT_FAILED user=%s error=%s",
                       owner, exc)
        return {"ok": False, "processed": 0}
    try:
        cur = conn.cursor()
        if not _schema.ensure_private_schema(cur):
            return {"ok": False, "processed": 0}
        result = drain(cur, user_id=owner)
        conn.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("PORTFOLIO_PROJECTION_KICK_FAILED user=%s error=%s",
                       owner, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "processed": 0}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Read — valuation of the projection, priced at read time
# ---------------------------------------------------------------------------
def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _active_fact(facts_rows: list[dict], fact_type: str) -> dict | None:
    for row in facts_rows:
        if row.get("fact_type") == fact_type:
            return row
    return None


def portfolio_view(cur, *, owner_user_id: int, actor_user_id: int) -> dict:
    """The projected portfolio, valued with Decimal math and honest freshness.

    Owner-only, like every other capital read: a caller who is not the owner
    gets the denied shape, not a thinner portfolio. Sweeps the outbox first so
    the view reflects any change whose post-commit kick was missed.

    ``totals`` carries ``complete`` — False whenever any asset is unpriced or
    any quantity fact is missing — and per-asset rows carry ``None`` (never 0)
    for unknown values, matching ``portfolio_service._value_holding``.
    """
    owner = int(owner_user_id or 0)
    actor = int(actor_user_id or 0)
    if owner <= 0 or actor != owner:
        _audit.record_denied(
            cur, actor_user_id=actor, owner_user_id=owner,
            object_type="PORTFOLIO_VIEW", purpose="user_request",
        )
        return {"ok": False, "denied": {"reason": "actor_is_not_owner"},
                "assets": [], "totals": {}, "sync": {}, "prices": {}}

    sweep = drain(cur, user_id=owner)

    nodes = [
        row for row in _graph.list_nodes(
            cur, owner_user_id=owner, node_types=[_model.NODE_ASSET],
            domains=[_model.DOMAIN_FINANCIAL],
            sensitivity_ceiling=_model.SENSITIVITY_CONFIDENTIAL, limit=500,
        )
        if str(row.get("external_ref") or "").startswith(ASSET_REF_PREFIX)
    ]

    try:
        board = market_data.live_market_board(limit=80)
    except Exception as exc:  # noqa: BLE001 — an unpriced view is still a view
        LOGGER.warning("PORTFOLIO_VIEW_BOARD_UNAVAILABLE error=%s", exc)
        board = {"source": "unavailable", "observed_epoch": None,
                 "age_seconds": None, "warning":
                 "Live data source is not connected yet.", "markets": []}
    price_by_symbol = {
        str(row.get("symbol") or "").upper(): row
        for row in board.get("markets", [])
    }

    assets: list[dict] = []
    total_value = Decimal("0")
    total_cost = Decimal("0")
    unpriced: list[str] = []
    cost_known = 0
    for node in sorted(nodes, key=lambda row: str(row.get("external_ref"))):
        node_id = int(node["id"])
        symbol = str(node.get("external_ref") or "")[len(ASSET_REF_PREFIX):]
        rows = _facts.list_facts(
            cur, owner_user_id=owner, subject_type=_facts.SUBJECT_NODE,
            subject_id=node_id, fact_types=list(PROJECTED_FACT_TYPES),
            domains=[_model.DOMAIN_FINANCIAL],
            sensitivity_ceiling=_model.SENSITIVITY_CONFIDENTIAL, limit=20,
        )
        quantity_fact = _active_fact(rows, FACT_QUANTITY)
        basis_fact = _active_fact(rows, FACT_COST_BASIS)
        name_fact = _active_fact(rows, FACT_ASSET_NAME)
        lots_fact = _active_fact(rows, FACT_LOT_COUNT)

        quantity = _decimal((quantity_fact or {}).get("value_number"))
        basis = _decimal((basis_fact or {}).get("value_number"))
        market_row = price_by_symbol.get(symbol)
        price = _decimal(market_row.get("price")) if market_row else None

        value = quantity * price if (quantity is not None and price is not None) else None
        if value is None:
            unpriced.append(symbol)
        else:
            total_value += value
        pnl = value - basis if (value is not None and basis is not None) else None
        if pnl is not None:
            total_cost += basis
            cost_known += 1

        assets.append({
            "node_id": node_id,
            "symbol": symbol,
            "name": str((name_fact or {}).get("value") or symbol),
            "quantity": float(quantity) if quantity is not None else None,
            "lot_count": int((lots_fact or {}).get("value_number") or 0),
            "cost_basis": float(basis) if basis is not None else None,
            "price": float(price) if price is not None else None,
            "value": float(value) if value is not None else None,
            "pnl_value": float(pnl) if pnl is not None else None,
            "priced": price is not None,
            "change_24h": market_row.get("change_24h") if market_row else None,
            # The projection's own freshness, distinct from the price's: when
            # the member last changed this holding vs. when the market last
            # answered.
            "projected_at": (quantity_fact or {}).get("observed_at"),
            "freshness": (quantity_fact or {}).get("freshness"),
            "evidence": {
                "fact_ids": [int(row["id"]) for row in rows],
                "provenance": (quantity_fact or {}).get("provenance"),
            },
        })

    complete = not unpriced and all(a["quantity"] is not None for a in assets)
    total_pnl = (total_value - total_cost) if (complete and cost_known == len(assets)
                                              and assets) else None
    return {
        "ok": True,
        "assets": assets,
        "totals": {
            "value": float(total_value) if complete and assets else None,
            "cost": float(total_cost) if cost_known else None,
            "pnl_value": float(total_pnl) if total_pnl is not None else None,
            "complete": complete,
            "assets": len(assets),
            "priced": len(assets) - len(unpriced),
            "unpriced_symbols": unpriced,
            "basis_known": cost_known,
        },
        "prices": {
            "source": board.get("source"),
            "observed_epoch": board.get("observed_epoch"),
            "age_seconds": board.get("age_seconds"),
            "warning": board.get("warning"),
        },
        "sync": dict(portfolio_events.sync_status(cur, user_id=owner),
                     swept=sweep.get("processed", 0)),
    }


# ---------------------------------------------------------------------------
# Reconciliation — prove the mirror matches the ledger, and repair it
# ---------------------------------------------------------------------------
def reconcile(cur, *, user_id: int, repair: bool = False) -> dict:
    """Compare the ledger against the projection, symbol by symbol.

    Drift is reported as data — ``{"symbol", "field", "ledger", "projected"}``
    — and, with ``repair=True``, fixed by the same :func:`project_user` every
    other path uses, then re-checked. There is no separate repair writer to
    drift from the projector.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "drift": [], "repaired": False}
    _schema.require_private_schema(cur)

    ledger = read_ledger(cur, user_id=owner)
    projected = _projected_asset_nodes(cur, owner=owner)

    drift: list[dict] = []
    for symbol, entry in sorted(ledger.items()):
        node = projected.get(symbol)
        if node is None or str(node.get("lifecycle_state")) != _model.LIFECYCLE_ACTIVE:
            drift.append({"symbol": symbol, "field": "node",
                          "ledger": "held", "projected": "missing"})
            continue
        rows = _facts.list_facts(
            cur, owner_user_id=owner, subject_type=_facts.SUBJECT_NODE,
            subject_id=int(node["id"]), fact_types=[FACT_QUANTITY], limit=5,
        )
        fact = _active_fact(rows, FACT_QUANTITY)
        recorded = (fact or {}).get("value_number")
        if recorded is None or abs(float(recorded) - float(entry["quantity"])) > 1e-9:
            drift.append({"symbol": symbol, "field": "quantity",
                          "ledger": entry["quantity"], "projected": recorded})
    for symbol, node in sorted(projected.items()):
        if symbol not in ledger and (
            str(node.get("lifecycle_state")) == _model.LIFECYCLE_ACTIVE
        ):
            drift.append({"symbol": symbol, "field": "node",
                          "ledger": "not_held", "projected": "active"})

    repaired = False
    if drift and repair:
        project_user(cur, user_id=owner)
        repaired = True
        follow_up = reconcile(cur, user_id=owner, repair=False)
        return {"ok": True, "drift": follow_up["drift"], "repaired": True,
                "drift_before_repair": drift}
    return {"ok": True, "drift": drift, "repaired": repaired}
