"""Business OS — COMMERCE THREAD LINKS: the Commerce Inbox's server-side join.

Phase 6 of the Store OS plan. The Commerce Inbox mock gaps exist because a
conversation row has no idea which order / return / offer / product it is
about. This module adds that join as a first-class, party-gated link:

  * one new table (``business_os_commerce_thread_links``) mapping a canonical
    business thread to a canonical commerce object — the pulse_* message
    tables and the marketplace tables are both UNTOUCHED;
  * LINKING is gated twice: the actor needs WRITE access to the thread (same
    RBAC as replying) AND must be a PARTY to the commerce object (its buyer /
    seller / owner). You cannot attach someone else's order to your thread —
    a foreign object answers 404 (existence not leaked);
  * linking is idempotent per (thread, type, ref): re-linking returns the
    existing row flagged ``duplicate: True``;
  * READING (``thread_context``) needs read access to the thread and returns
    curated projections only (ids, status, money figures) — parties vouched
    for the link at write time; buyer identity and free-form internals are
    not re-exposed;
  * a commerce subsystem missing from the deployment surfaces an honest 409
    ``unavailable`` on link and ``None`` in context — never a silent fake.

Flag-gated by ``BUSINESS_OS_MESSAGES`` (the thread side is the substrate).
Additive only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.messages import service as _msg
from services.business_os.messages.service import MessageError


# related_type -> (table, id column, party columns)
RELATED_TYPES = {
    "order": ("business_os_mkt_orders", "order_id",
              ("buyer_user_id", "seller_user_id")),
    "return": ("business_os_mkt_returns", "return_id",
               ("buyer_user_id", "seller_user_id")),
    "offer": ("business_os_mkt_offers", "offer_id",
              ("buyer_user_id", "seller_user_id")),
    "product": ("business_os_mkt_products", "product_id",
                ("seller_user_id",)),
}

# Curated context projections — the ONLY fields the inbox ever sees.
_PROJECTIONS = {
    "order": ("order_id", "status", "total_cents", "currency", "created_at"),
    "return": ("return_id", "order_id", "status", "reason", "created_at"),
    "offer": ("offer_id", "product_id", "status", "current_amount_cents",
              "quantity", "expires_at"),
    "product": ("product_id", "title", "status", "price_cents", "currency"),
}


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
            "CREATE TABLE IF NOT EXISTS business_os_commerce_thread_links ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "conversation_id INTEGER NOT NULL, "
            "related_type TEXT NOT NULL, "
            "related_ref TEXT NOT NULL, "
            "created_by TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "UNIQUE (conversation_id, related_type, related_ref))")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_busos_thread_links_conv "
            "ON business_os_commerce_thread_links (conversation_id)")
        conn.commit()
    finally:
        conn.close()


# --- internal ----------------------------------------------------------------
def _fetch_related(conn, related_type: str, related_ref: str) -> Optional[dict]:
    """The commerce object's row, or None if absent. A MISSING TABLE raises
    409 unavailable — the subsystem is not deployed, which is different from
    the object not existing."""
    table, id_col, _parties = RELATED_TYPES[related_type]
    try:
        return _row(conn.execute(
            f"SELECT * FROM {table} WHERE {id_col} = ?",
            (str(related_ref),)).fetchone())
    except Exception:
        raise MessageError(
            f"The {related_type} subsystem is not available here.",
            409, "unavailable")


def _project(related_type: str, row: dict) -> dict:
    return {k: row.get(k) for k in _PROJECTIONS[related_type]}


# --- verbs -------------------------------------------------------------------
def link_thread(conversation_id: Any, actor_user_id: Any, *,
                related_type: str, related_ref: Any,
                context: Optional[dict] = None) -> dict:
    """Attach a commerce object to a business thread the actor can write to,
    provided the actor is a PARTY to that object."""
    _msg._require_enabled()
    _msg._require_not_held(context)
    if related_type not in RELATED_TYPES:
        raise MessageError(
            f"related_type must be one of {sorted(RELATED_TYPES)}.",
            400, "invalid_related_type")
    if related_ref in (None, ""):
        raise MessageError("related_ref is required.", 400, "invalid")
    conn = db.connect()
    try:
        conv = _msg._get_conversation(conn, conversation_id)
        if conv is None or _msg._authorize_write(conn, conv, actor_user_id) is None:
            raise MessageError("Conversation not found.", 404, "not_found")
        obj = _fetch_related(conn, related_type, str(related_ref))
        actor = _msg._sid(actor_user_id)
        _table, _id_col, parties = RELATED_TYPES[related_type]
        if obj is None or all(obj.get(p) != actor for p in parties):
            # Nonexistent and not-yours answer identically.
            raise MessageError(f"{related_type} not found.", 404, "not_found")

        existing = _row(conn.execute(
            "SELECT * FROM business_os_commerce_thread_links "
            "WHERE conversation_id = ? AND related_type = ? AND related_ref = ?",
            (int(conv["id"]), related_type, str(related_ref))).fetchone())
        if existing is not None:
            existing["duplicate"] = True
            return existing
        now = _now_iso()
        cur = conn.execute(
            "INSERT INTO business_os_commerce_thread_links "
            "(conversation_id, related_type, related_ref, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(conv["id"]), related_type, str(related_ref), actor, now))
        conn.commit()
        return {"id": cur.lastrowid, "conversation_id": int(conv["id"]),
                "related_type": related_type, "related_ref": str(related_ref),
                "created_by": actor, "created_at": now, "duplicate": False}
    finally:
        conn.close()


# --- projections -------------------------------------------------------------
def thread_context(conversation_id: Any, requester_user_id: Any) -> dict:
    """Everything commerce this thread is about, as curated projections.
    Requires read access to the thread. A link whose subsystem is gone from
    the deployment reports ``context: None`` (unavailable) — never dropped
    silently, never faked."""
    _msg._require_enabled()
    conn = db.connect()
    try:
        conv = _msg._get_conversation(conn, conversation_id)
        if conv is None or _msg._authorize_read(conn, conv, requester_user_id) is None:
            raise MessageError("Conversation not found.", 404, "not_found")
        out = []
        for link in [_row(r) for r in conn.execute(
                "SELECT * FROM business_os_commerce_thread_links "
                "WHERE conversation_id = ? ORDER BY id",
                (int(conv["id"]),)).fetchall()]:
            try:
                obj = _fetch_related(conn, link["related_type"],
                                     link["related_ref"])
            except MessageError:
                obj = None
                projected = None
            else:
                projected = _project(link["related_type"], obj) if obj else None
            out.append({"related_type": link["related_type"],
                        "related_ref": link["related_ref"],
                        "linked_at": link["created_at"],
                        "context": projected})
        return {"conversation_id": int(conv["id"]), "links": out}
    finally:
        conn.close()
