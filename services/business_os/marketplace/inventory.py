"""Business OS — Marketplace INVENTORY: adjustments with reason codes + overview.

Closes the Phase 0 item "add adjustment records (reason enum, before/after,
actor)" and gives Phase 4's Inventory Overview tabs a reservation-aware backend.

What already exists and is NOT duplicated:

  * ``service.set_inventory`` — owner sets an absolute quantity (audited);
  * ``orders.pay_order`` / ``offers.accept_offer`` — the two guarded atomic
    decrements that move stock for money/holds.

What this module adds, additively:

  * ``adjust_inventory`` — seller-facing RELATIVE adjustment (+found/-damaged…)
    or absolute recount, always with a REQUIRED reason code from a curated
    enum, an optional note, and an append-only
    ``business_os_mkt_inventory_adjustments`` record carrying before/after and
    the actor. The relative path uses the same guarded-atomic UPDATE shape as
    ``pay_order`` (cannot race below zero); unlimited (NULL) inventory refuses
    relative deltas honestly instead of inventing a number.
  * ``list_adjustments`` — the history view, seller-scoped.
  * ``inventory_overview`` — the tabs' projection: per-product on-hand qty plus
    ``held_qty`` (active offer reservations that took a hard hold), and honest
    bucket counts (tracked / unlimited / out_of_stock / low_stock). Display
    layers render these; nothing is fabricated for empty catalogs.

Flag-gated by ``BUSINESS_OS_MARKETPLACE``; account-hold beats every write.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace.service import MarketplaceError


ADJUSTMENT_REASONS = {
    "recount",            # physical count correction (absolute set)
    "found",              # stock located (+)
    "damaged",            # write-off (-)
    "lost",               # shrinkage (-)
    "returned_to_stock",  # merchandise back from a return (+)
    "correction",         # data-entry fix (either sign)
}

MAX_NOTE_LEN = 1000
DEFAULT_LOW_STOCK_THRESHOLD = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def ensure_schema(conn=None) -> None:
    """Idempotent DDL (no migration framework — same contract as every pack)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_mkt_inventory_adjustments ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "product_id TEXT NOT NULL, "
            "seller_user_id TEXT NOT NULL, "
            "delta INTEGER, "
            "before_qty INTEGER, "
            "after_qty INTEGER, "
            "reason TEXT NOT NULL, "
            "note TEXT, "
            "actor TEXT NOT NULL, "
            "created_at TEXT NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_invadj_product "
            "ON business_os_mkt_inventory_adjustments (product_id, id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_busos_mkt_invadj_seller "
            "ON business_os_mkt_inventory_adjustments (seller_user_id, id)")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


# --- internal ----------------------------------------------------------------
def _owned_product(conn, product_id: Any, seller_user_id: Any) -> dict:
    row = _row(conn.execute(
        "SELECT * FROM business_os_mkt_products WHERE product_id = ?",
        (str(product_id),)).fetchone())
    if row is None or row.get("seller_user_id") != _svc._sid(seller_user_id):
        raise MarketplaceError("Product not found.", 404, "not_found")
    return row


def _clean_note(note: Any) -> Optional[str]:
    if note is None or not str(note).strip():
        return None
    text = str(note).strip()
    if len(text) > MAX_NOTE_LEN:
        raise MarketplaceError("note is too long.", 400, "note_too_long")
    return text


def _record(conn, *, product_id, seller_user_id, delta, before_qty, after_qty,
            reason, note, actor) -> dict:
    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO business_os_mkt_inventory_adjustments "
        "(product_id, seller_user_id, delta, before_qty, after_qty, reason, note, "
        "actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(product_id), _svc._sid(seller_user_id), delta, before_qty, after_qty,
         reason, note, _svc._sid(actor), now))
    return {"adjustment_id": cur.lastrowid, "product_id": str(product_id),
            "delta": delta, "before_qty": before_qty, "after_qty": after_qty,
            "reason": reason, "note": note, "actor": _svc._sid(actor),
            "created_at": now}


# --- writes ------------------------------------------------------------------
def adjust_inventory(seller_user_id: Any, product_id: Any, *,
                     delta: Any = None, set_qty: Any = None, reason: str,
                     note: Any = None, context: Optional[dict] = None,
                     conn=None) -> dict:
    """One governed mutation, two modes (exactly one must be given):

    * ``delta`` — relative change. Guarded-atomic: the UPDATE itself enforces
      ``inventory_qty + delta >= 0`` so two concurrent writers cannot race the
      count below zero (rowcount tells the loser, who gets a 409). Refused on
      unlimited (NULL) inventory — there is no number to adjust.
    * ``set_qty`` — absolute recount to a non-negative integer. (Switching a
      digital product to UNLIMITED stays with ``service.set_inventory`` — that
      is a tracking-mode change, not a countable adjustment, and it is already
      audited there.)

    ``reason`` is REQUIRED and must come from ``ADJUSTMENT_REASONS``. Every call
    appends an adjustment record (before/after/actor) and an audit row.
    """
    _svc._require_enabled()
    _svc._require_not_held(seller_user_id, context)
    if reason not in ADJUSTMENT_REASONS:
        raise MarketplaceError(
            f"reason must be one of {sorted(ADJUSTMENT_REASONS)}.",
            400, "invalid_reason")
    note_text = _clean_note(note)
    has_delta = delta is not None
    has_set = set_qty is not None
    if has_delta == has_set:
        raise MarketplaceError("Give exactly one of delta or set_qty.",
                               400, "invalid_adjustment")
    if has_delta:
        if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
            raise MarketplaceError("delta must be a non-zero integer.",
                                   400, "invalid_adjustment")
    else:
        if isinstance(set_qty, bool) or not isinstance(set_qty, int) or set_qty < 0:
            raise MarketplaceError("set_qty must be a non-negative integer.",
                                   400, "invalid_adjustment")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        product = _owned_product(conn, product_id, seller_user_id)
        before = product.get("inventory_qty")
        now = _now_iso()
        if has_delta:
            if before is None:
                raise MarketplaceError(
                    "This product tracks unlimited inventory; use set_qty to "
                    "start tracking a finite count.", 409, "unlimited_inventory")
            cur = conn.execute(
                "UPDATE business_os_mkt_products SET inventory_qty = inventory_qty + ?, "
                "updated_at = ? WHERE product_id = ? "
                "AND inventory_qty IS NOT NULL AND inventory_qty + ? >= 0",
                (delta, now, str(product_id), delta))
            if getattr(cur, "rowcount", 0) != 1:
                raise MarketplaceError(
                    "Adjustment would take inventory below zero.",
                    409, "insufficient_inventory")
            after = int(before) + int(delta)
        else:
            conn.execute(
                "UPDATE business_os_mkt_products SET inventory_qty = ?, "
                "updated_at = ? WHERE product_id = ?",
                (set_qty, now, str(product_id)))
            after = set_qty
        record = _record(conn, product_id=product_id,
                         seller_user_id=seller_user_id,
                         delta=delta if has_delta else None,
                         before_qty=before, after_qty=after, reason=reason,
                         note=note_text, actor=seller_user_id)
        _svc._audit(conn, subject_type="product", subject_ref=product_id,
                    action="inventory_adjust", actor=seller_user_id,
                    reason=reason,
                    before={"inventory_qty": before},
                    after={"inventory_qty": after, "delta": record["delta"],
                           "note": note_text})
        if owned:
            conn.commit()
        return record
    finally:
        if owned:
            conn.close()


# --- reads -------------------------------------------------------------------
def list_adjustments(seller_user_id: Any, *, product_id: Any = None,
                     limit: int = 200, conn=None) -> list:
    """History view, seller-scoped. A product_id the seller does not own answers
    404 (existence not leaked), matching every other read in the package."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if product_id is not None:
            _owned_product(conn, product_id, seller_user_id)
            return [_row(r) for r in conn.execute(
                "SELECT * FROM business_os_mkt_inventory_adjustments "
                "WHERE product_id = ? ORDER BY id DESC LIMIT ?",
                (str(product_id), int(limit))).fetchall()]
        return [_row(r) for r in conn.execute(
            "SELECT * FROM business_os_mkt_inventory_adjustments "
            "WHERE seller_user_id = ? ORDER BY id DESC LIMIT ?",
            (_svc._sid(seller_user_id), int(limit))).fetchall()]
    finally:
        if owned:
            conn.close()


def inventory_overview(seller_user_id: Any, *,
                       low_stock_threshold: int = DEFAULT_LOW_STOCK_THRESHOLD,
                       conn=None) -> dict:
    """The Inventory Overview tabs' data source. Reservation-aware: ``held_qty``
    counts ACTIVE offer reservations that took a hard hold (their quantity is
    already out of ``inventory_qty`` — surfacing it explains 'where did my
    stock go'). Buckets are honest; an empty catalog is an empty list, not a
    fabricated zero row."""
    _svc._require_enabled()
    if isinstance(low_stock_threshold, bool) or not isinstance(low_stock_threshold, int) \
            or low_stock_threshold < 1:
        raise MarketplaceError("low_stock_threshold must be a positive integer.",
                               400, "invalid_threshold")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = [_row(r) for r in conn.execute(
            "SELECT product_id, title, status, fulfillment_type, inventory_qty "
            "FROM business_os_mkt_products WHERE seller_user_id = ? "
            "AND status != 'archived' ORDER BY created_at DESC",
            (_svc._sid(seller_user_id),)).fetchall()]
        holds: dict = {}
        try:
            for h in conn.execute(
                    "SELECT r.product_id AS pid, "
                    "COALESCE(SUM(r.quantity), 0) AS held "
                    "FROM business_os_mkt_offer_reservations r "
                    "JOIN business_os_mkt_products p ON p.product_id = r.product_id "
                    "WHERE p.seller_user_id = ? AND r.status = 'active' "
                    "AND r.inventory_held = 1 GROUP BY r.product_id",
                    (_svc._sid(seller_user_id),)).fetchall():
                hr = _row(h)
                holds[str(hr["pid"])] = int(hr["held"] or 0)
        except Exception:
            # Offers pack not initialised in this deployment — holds are simply
            # absent, not fabricated.
            holds = {}
        out_rows = []
        counts = {"tracked": 0, "unlimited": 0, "out_of_stock": 0, "low_stock": 0}
        for p in rows:
            qty = p.get("inventory_qty")
            held = holds.get(str(p["product_id"]), 0)
            bucket = "unlimited"
            if qty is not None:
                counts["tracked"] += 1
                if int(qty) == 0:
                    bucket = "out_of_stock"
                    counts["out_of_stock"] += 1
                elif int(qty) <= low_stock_threshold:
                    bucket = "low_stock"
                    counts["low_stock"] += 1
                else:
                    bucket = "in_stock"
            else:
                counts["unlimited"] += 1
            out_rows.append({"product_id": p["product_id"], "title": p.get("title"),
                             "status": p.get("status"),
                             "fulfillment_type": p.get("fulfillment_type"),
                             "on_hand_qty": qty, "held_qty": held,
                             "bucket": bucket})
        return {"seller_user_id": _svc._sid(seller_user_id),
                "low_stock_threshold": low_stock_threshold,
                "counts": counts, "products": out_rows,
                "product_count": len(out_rows)}
    finally:
        if owned:
            conn.close()
