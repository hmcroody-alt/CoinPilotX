"""Marketplace returns & disputes — the Phase 6 route pack.

This is a net-new domain: before this pack, a search of the codebase finds no
returns model, route, or state machine anywhere (recorded in
`INBOX_MOCK_DATA_GAPS`). The mobile inbox already stubs a returns tab; this
gives it something true to render.

State machines
--------------

Return:   opened → awaiting_seller → (awaiting_buyer ⇄ awaiting_seller)
                → under_review → resolved_refund | resolved_replacement
                | resolved_rejected → closed
          Either side may add messages/evidence while awaiting_*; escalation
          moves it to under_review (admin surface resolves it).

Dispute:  A dispute is a return that has been escalated, or a standalone
          escalation on an order. Same review pipeline.

Evidence
--------

A return freezes its context at open time: the listing snapshot, the
transaction row, and the desired resolution are copied into the return's
`evidence_json`. The seller editing the listing afterwards cannot rewrite what
the buyer bought. Media evidence rides the existing R2 upload path — clients
upload first, then attach URLs here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

LOGGER = logging.getLogger(__name__)

returns_blueprint = Blueprint("pulse_marketplace_returns", __name__)

API_PREFIX = "/api/pulse/marketplace/returns"

OPEN_WINDOW_DAYS = 30

RETURN_REASONS = {
    "not_received", "not_as_described", "damaged", "wrong_item",
    "quality", "changed_mind", "other",
}
RESOLUTIONS = {"refund", "replacement", "partial_refund"}

# States a buyer/seller message is allowed in. Terminal states are read-only.
ACTIVE_STATES = {"opened", "awaiting_seller", "awaiting_buyer", "under_review"}
TERMINAL_STATES = {"resolved_refund", "resolved_replacement", "resolved_rejected", "closed"}

_SCHEMA_READY = False


def _bot():
    import bot

    return bot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(payload, status: int = 200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _error(message: str, status: int = 400, **extra):
    return _json({"ok": False, "message": message, **extra}, status)


def _require_user():
    try:
        user = _bot().api_account_user()
    except Exception:
        LOGGER.exception("RETURNS_AUTH_LOOKUP_FAILED")
        user = None
    if not user:
        return None, _error("Login required.", 401)
    return user, None


def _with_db(handler):
    bot = _bot()
    conn = bot.db()
    try:
        try:
            import sqlite3

            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        cur = conn.cursor()
        _ensure_schema(cur)
        result = handler(cur, conn)
        conn.commit()
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_schema(cur) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS marketplace_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER,
            listing_id INTEGER,
            buyer_user_id INTEGER,
            seller_user_id INTEGER,
            reason TEXT,
            explanation TEXT,
            desired_resolution TEXT DEFAULT 'refund',
            state TEXT DEFAULT 'opened',
            evidence_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS marketplace_return_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id INTEGER,
            actor_user_id INTEGER,
            actor_role TEXT,
            event_type TEXT,
            body TEXT,
            media_json TEXT,
            from_state TEXT,
            to_state TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marketplace_returns_buyer ON marketplace_returns(buyer_user_id, state)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marketplace_returns_seller ON marketplace_returns(seller_user_id, state)"
    )
    _SCHEMA_READY = True


def _load(cur, return_id: int) -> dict:
    cur.execute("SELECT * FROM marketplace_returns WHERE id=? LIMIT 1", (return_id,))
    return dict(cur.fetchone() or {})


def _role(ret: dict, viewer: int) -> str:
    if viewer == int(ret.get("buyer_user_id") or 0):
        return "buyer"
    if viewer == int(ret.get("seller_user_id") or 0):
        return "seller"
    return ""


def _log_event(cur, return_id: int, actor: int, role: str, event_type: str,
               body: str = "", media=None, from_state: str = "", to_state: str = "") -> None:
    cur.execute(
        """
        INSERT INTO marketplace_return_events
            (return_id, actor_user_id, actor_role, event_type, body, media_json,
             from_state, to_state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (return_id, actor, role, event_type, body,
         json.dumps(media or [], default=str), from_state, to_state, _now()),
    )


def _set_state(cur, ret: dict, to_state: str, actor: int, role: str, note: str = "") -> None:
    cur.execute(
        "UPDATE marketplace_returns SET state=?, updated_at=? WHERE id=?",
        (to_state, _now(), int(ret["id"])),
    )
    _log_event(cur, int(ret["id"]), actor, role, "state_change",
               body=note, from_state=ret.get("state") or "", to_state=to_state)


def _serialize(cur, ret: dict, viewer: int, with_events: bool = False) -> dict:
    payload = {
        "id": int(ret["id"]),
        "transaction_id": int(ret.get("transaction_id") or 0),
        "listing_id": int(ret.get("listing_id") or 0),
        "buyer_user_id": int(ret.get("buyer_user_id") or 0),
        "seller_user_id": int(ret.get("seller_user_id") or 0),
        "reason": ret.get("reason") or "other",
        "explanation": ret.get("explanation") or "",
        "desired_resolution": ret.get("desired_resolution") or "refund",
        "state": ret.get("state") or "opened",
        "viewer_role": _role(ret, viewer),
        "created_at": ret.get("created_at") or "",
        "updated_at": ret.get("updated_at") or "",
    }
    if with_events:
        cur.execute(
            "SELECT * FROM marketplace_return_events WHERE return_id=? ORDER BY id ASC LIMIT 500",
            (int(ret["id"]),),
        )
        payload["events"] = [
            {
                "id": int(e["id"]),
                "actor_user_id": int(e["actor_user_id"] or 0),
                "actor_role": e["actor_role"] or "",
                "event_type": e["event_type"] or "",
                "body": e["body"] or "",
                "media": json.loads(e["media_json"] or "[]"),
                "from_state": e["from_state"] or "",
                "to_state": e["to_state"] or "",
                "created_at": e["created_at"] or "",
            }
            for e in (dict(r) for r in cur.fetchall())
        ]
        evidence = {}
        try:
            evidence = json.loads(ret.get("evidence_json") or "{}")
        except Exception:
            pass
        payload["evidence"] = evidence
    return payload


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@returns_blueprint.route(API_PREFIX, methods=["POST"])
def return_open():
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    transaction_id = int(payload.get("transaction_id") or 0)
    reason = str(payload.get("reason") or "other")
    if reason not in RETURN_REASONS:
        reason = "other"
    explanation = bot.clean_html(str(payload.get("explanation") or ""))[:2000]
    desired = str(payload.get("desired_resolution") or "refund")
    if desired not in RESOLUTIONS:
        desired = "refund"
    media = payload.get("media") if isinstance(payload.get("media"), list) else []
    media = [str(m)[:500] for m in media][:10]
    if not transaction_id:
        return _error("Choose the purchase this return is about.", 400)

    def handler(cur, conn):
        buyer_id = int(user["user_id"])
        cur.execute("SELECT * FROM seller_transactions WHERE id=? LIMIT 1", (transaction_id,))
        tx = dict(cur.fetchone() or {})
        if not tx or int(tx.get("buyer_user_id") or 0) != buyer_id:
            return _error("Purchase not found.", 404)
        if tx.get("item_type") != "marketplace_product":
            return _error("Returns apply to marketplace purchases only.", 400)
        cur.execute(
            "SELECT id FROM marketplace_returns WHERE transaction_id=? AND buyer_user_id=? LIMIT 1",
            (transaction_id, buyer_id),
        )
        existing = dict(cur.fetchone() or {})
        if existing:
            # Idempotent open: the second tap lands on the first return.
            ret = _load(cur, int(existing["id"]))
            return _json({"ok": True, "already_open": True,
                          "return": _serialize(cur, ret, buyer_id, with_events=True)})
        listing_id = int(tx.get("item_id") or 0)
        cur.execute("SELECT * FROM marketplace_listings WHERE id=? LIMIT 1", (listing_id,))
        listing = dict(cur.fetchone() or {})
        # Evidence bundle frozen at open time.
        evidence = {
            "listing_snapshot": {
                k: listing.get(k) for k in (
                    "id", "title", "description", "short_description", "price_label",
                    "currency", "category", "delivery_type", "cover_image_url",
                )
            } if listing else {},
            "transaction": {
                k: tx.get(k) for k in (
                    "id", "amount_cents", "currency", "status", "created_at",
                )
            },
            "buyer_media": media,
        }
        now = _now()
        cur.execute(
            """
            INSERT INTO marketplace_returns
                (transaction_id, listing_id, buyer_user_id, seller_user_id, reason,
                 explanation, desired_resolution, state, evidence_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_seller', ?, ?, ?)
            """,
            (transaction_id, listing_id, buyer_id, int(tx.get("seller_user_id") or 0),
             reason, explanation, desired, json.dumps(evidence, default=str), now, now),
        )
        return_id = int(cur.lastrowid)
        _log_event(cur, return_id, buyer_id, "buyer", "opened",
                   body=explanation, media=media, from_state="", to_state="awaiting_seller")
        ret = _load(cur, return_id)
        return _json({"ok": True, "return": _serialize(cur, ret, buyer_id, with_events=True)})

    return _with_db(handler)


@returns_blueprint.route(API_PREFIX, methods=["GET"])
def return_list():
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    role = request.args.get("role") or "buyer"

    def handler(cur, conn):
        viewer = int(user["user_id"])
        column = "seller_user_id" if role == "seller" else "buyer_user_id"
        cur.execute(
            f"SELECT * FROM marketplace_returns WHERE {column}=? ORDER BY id DESC LIMIT 100",
            (viewer,),
        )
        items = [_serialize(cur, dict(r), viewer) for r in cur.fetchall()]
        return _json({"ok": True, "returns": items,
                      "open_count": sum(1 for r in items if r["state"] in ACTIVE_STATES)})

    return _with_db(handler)


@returns_blueprint.route(f"{API_PREFIX}/<int:return_id>", methods=["GET"])
def return_detail(return_id: int):
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err

    def handler(cur, conn):
        viewer = int(user["user_id"])
        ret = _load(cur, return_id)
        if not ret or not _role(ret, viewer):
            return _error("Return not found.", 404)
        return _json({"ok": True, "return": _serialize(cur, ret, viewer, with_events=True)})

    return _with_db(handler)


@returns_blueprint.route(f"{API_PREFIX}/<int:return_id>/message", methods=["POST"])
def return_message(return_id: int):
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    body = bot.clean_html(str(payload.get("body") or ""))[:2000]
    media = payload.get("media") if isinstance(payload.get("media"), list) else []
    media = [str(m)[:500] for m in media][:10]
    if not body and not media:
        return _error("A message needs text or media.", 400)

    def handler(cur, conn):
        viewer = int(user["user_id"])
        ret = _load(cur, return_id)
        role = _role(ret, viewer) if ret else ""
        if not ret or not role:
            return _error("Return not found.", 404)
        if (ret.get("state") or "") in TERMINAL_STATES:
            return _error("This return is closed.", 409, state=ret.get("state"))
        _log_event(cur, return_id, viewer, role, "message", body=body, media=media)
        # A reply flips whose turn it is, unless it is already under review.
        state = ret.get("state") or "opened"
        if state in {"opened", "awaiting_seller", "awaiting_buyer"}:
            next_state = "awaiting_buyer" if role == "seller" else "awaiting_seller"
            if next_state != state:
                _set_state(cur, ret, next_state, viewer, role)
        ret = _load(cur, return_id)
        return _json({"ok": True, "return": _serialize(cur, ret, viewer, with_events=True)})

    return _with_db(handler)


@returns_blueprint.route(f"{API_PREFIX}/<int:return_id>/resolve", methods=["POST"])
def return_resolve(return_id: int):
    """Seller-side resolution: accept (refund/replacement) or reject. A
    rejection the buyer disagrees with is what `escalate` is for."""
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    resolution = str(payload.get("resolution") or "")
    note = bot.clean_html(str(payload.get("note") or ""))[:1000]
    state_for = {
        "refund": "resolved_refund",
        "replacement": "resolved_replacement",
        "reject": "resolved_rejected",
    }
    if resolution not in state_for:
        return _error("Resolution must be refund, replacement, or reject.", 400)

    def handler(cur, conn):
        viewer = int(user["user_id"])
        ret = _load(cur, return_id)
        if not ret or _role(ret, viewer) != "seller":
            return _error("Return not found.", 404)
        if (ret.get("state") or "") not in ACTIVE_STATES:
            return _error("This return is already resolved.", 409, state=ret.get("state"))
        if (ret.get("state") or "") == "under_review":
            return _error("This return is under review and will be resolved by PulseSoc.", 409)
        _set_state(cur, ret, state_for[resolution], viewer, "seller", note=note)
        # Money movement (the actual Stripe refund) is an admin/payments
        # concern recorded on the transaction; this records the decision.
        ret = _load(cur, return_id)
        return _json({"ok": True, "return": _serialize(cur, ret, viewer, with_events=True)})

    return _with_db(handler)


@returns_blueprint.route(f"{API_PREFIX}/<int:return_id>/escalate", methods=["POST"])
def return_escalate(return_id: int):
    """Either side moves the return to platform review — this is the dispute."""
    bot = _bot()
    bot.init_db()
    user, err = _require_user()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    note = bot.clean_html(str(payload.get("note") or ""))[:1000]

    def handler(cur, conn):
        viewer = int(user["user_id"])
        ret = _load(cur, return_id)
        role = _role(ret, viewer) if ret else ""
        if not ret or not role:
            return _error("Return not found.", 404)
        state = ret.get("state") or ""
        if state == "under_review":
            return _json({"ok": True, "already_escalated": True,
                          "return": _serialize(cur, ret, viewer, with_events=True)})
        if state in TERMINAL_STATES and state != "resolved_rejected":
            return _error("This return is closed.", 409, state=state)
        _set_state(cur, ret, "under_review", viewer, role, note=note)
        _log_event(cur, return_id, viewer, role, "escalated", body=note)
        ret = _load(cur, return_id)
        return _json({"ok": True, "return": _serialize(cur, ret, viewer, with_events=True)})

    return _with_db(handler)


def register(app) -> None:
    app.register_blueprint(returns_blueprint)
