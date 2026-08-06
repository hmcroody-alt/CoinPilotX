"""Business OS — Marketplace SELLER REPUTATION: verified-transaction ratings.

Phase 8 of the Store OS plan. The mission spec's hard rule: reputation comes
from REAL transactions only — no drive-by ratings, no seller-fabricated
stars. Enforced structurally:

  * a rating requires an order row, must be written by THAT order's buyer,
    and only after the transaction is real (order reached fulfilled /
    completed / refunded — money was captured; a merely-created or just-paid
    order is not ratable yet, 409 ``not_ratable``);
  * one rating per order (UNIQUE), append-only — no edits, duplicates 409;
  * a foreign order answers 404 (existence not leaked);
  * the aggregate is honest: a seller with no ratings has ``average: None``
    ("no ratings yet"), never a fabricated 0 or 5 (Section 15
    zero-vs-unavailable applied to reputation);
  * account-hold gate on writing; audit row per rating.

Flag-gated by ``BUSINESS_OS_MARKETPLACE``. Additive only: one new table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.marketplace import service as _svc
from services.business_os.marketplace.service import MarketplaceError


# The transaction is "verified" once money was captured and the order ran its
# course far enough that the buyer has something real to rate. A refunded
# order was still a real transaction — negative experiences count.
RATABLE_ORDER_STATUSES = {"fulfilled", "completed", "refunded"}

COMMENT_MAX = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}


def ensure_schema() -> None:
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS business_os_mkt_seller_ratings ("
            "rating_id TEXT PRIMARY KEY, "
            "order_id TEXT NOT NULL UNIQUE, "
            "seller_user_id TEXT NOT NULL, "
            "buyer_user_id TEXT NOT NULL, "
            "rating INTEGER NOT NULL, "
            "comment TEXT, "
            "created_at TEXT NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_busos_ratings_seller "
            "ON business_os_mkt_seller_ratings (seller_user_id, created_at)")
        conn.commit()
    finally:
        conn.close()


# --- verbs -------------------------------------------------------------------
def rate_order(order_id: Any, buyer_user_id: Any, *, rating: Any,
               comment: Optional[str] = None,
               context: Optional[dict] = None, conn=None) -> dict:
    """The buyer of a verified transaction rates the seller, once."""
    _svc._require_enabled()
    _svc._require_not_held(buyer_user_id, context)
    if isinstance(rating, bool) or not isinstance(rating, int) or \
            rating < 1 or rating > 5:
        raise MarketplaceError("rating must be an integer 1..5.", 400,
                               "invalid_rating")
    if comment is not None and len(str(comment)) > COMMENT_MAX:
        raise MarketplaceError("comment is too long.", 400, "comment_too_long")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        order = _row(conn.execute(
            "SELECT * FROM business_os_mkt_orders WHERE order_id = ?",
            (str(order_id),)).fetchone())
        if order is None or order["buyer_user_id"] != _svc._sid(buyer_user_id):
            raise MarketplaceError("Order not found.", 404, "not_found")
        if order["status"] not in RATABLE_ORDER_STATUSES:
            raise MarketplaceError(
                "Order is not ratable yet.", 409, "not_ratable")
        existing = conn.execute(
            "SELECT rating_id FROM business_os_mkt_seller_ratings "
            "WHERE order_id = ?", (order["order_id"],)).fetchone()
        if existing is not None:
            raise MarketplaceError("Order already rated.", 409, "already_rated")
        rid = "mkrate_" + uuid.uuid4().hex
        conn.execute(
            "INSERT INTO business_os_mkt_seller_ratings "
            "(rating_id, order_id, seller_user_id, buyer_user_id, rating, "
            "comment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rid, order["order_id"], order["seller_user_id"],
             _svc._sid(buyer_user_id), rating, comment, _now_iso()))
        _svc._audit(conn, subject_type="seller_rating", subject_ref=rid,
                    action="rating_create", actor=buyer_user_id,
                    after={"order_id": order["order_id"],
                           "seller_user_id": order["seller_user_id"],
                           "rating": rating})
        if owned:
            conn.commit()
        return {"rating_id": rid, "order_id": order["order_id"],
                "seller_user_id": order["seller_user_id"],
                "rating": rating, "comment": comment}
    finally:
        if owned:
            conn.close()


# --- projections -------------------------------------------------------------
def seller_reputation(seller_user_id: Any, conn=None) -> dict:
    """Aggregate for the seller profile / trust badges. HONEST empty state:
    no ratings -> ``count: 0, average: None`` — "no ratings yet", never a
    fabricated number."""
    _svc._require_enabled()
    sid = _svc._sid(seller_user_id)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = [_row(r) for r in conn.execute(
            "SELECT rating, COUNT(*) AS n FROM business_os_mkt_seller_ratings "
            "WHERE seller_user_id = ? GROUP BY rating", (sid,)).fetchall()]
        distribution = {i: 0 for i in range(1, 6)}
        total, weighted = 0, 0
        for r in rows:
            distribution[int(r["rating"])] = int(r["n"])
            total += int(r["n"])
            weighted += int(r["rating"]) * int(r["n"])
        return {"seller_user_id": sid, "count": total,
                "average": round(weighted / total, 2) if total else None,
                "distribution": distribution}
    finally:
        if owned:
            conn.close()


def list_ratings(seller_user_id: Any, *, limit: int = 50, conn=None) -> list:
    """Public-facing rating rows for a seller: rating + comment + date only —
    buyer identity is NOT exposed."""
    _svc._require_enabled()
    sid = _svc._sid(seller_user_id)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return [{"rating_id": r["rating_id"], "rating": int(r["rating"]),
                 "comment": r["comment"], "created_at": r["created_at"]}
                for r in (_row(x) for x in conn.execute(
                    "SELECT rating_id, rating, comment, created_at "
                    "FROM business_os_mkt_seller_ratings "
                    "WHERE seller_user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (sid, int(limit))).fetchall())]
    finally:
        if owned:
            conn.close()
