"""The seller's and the buyer's halves of a Marketplace order after payment.

Until this pack existed there was no way for either of them to say anything: a
paid order's last recorded fact was that it was paid, ``delivered_at`` was read
in two places and written in none, and every settlement sat in
``pending_fulfillment`` forever because nothing could move it on.

The routes are deliberately thin. All of the judgement — which move is legal,
and who is allowed to make it — lives in
:mod:`services.marketplace_order_fulfillment`, so that a second caller (an
admin tool, a carrier webhook, the timeout sweeper) cannot reach a different
answer by taking a different door.

The one thing each route does decide is *nothing about the caller's role*. The
role is resolved from the order record via
:func:`marketplace_order_fulfillment.role_of`, so a seller hitting the buyer's
confirm endpoint resolves to ``seller`` and is refused by the authority map —
rather than resolving to whatever the endpoint's name implies.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services import marketplace_order_fulfillment as order_fulfillment
from services.route_auth import auth_required

LOGGER = logging.getLogger(__name__)

fulfillment_blueprint = Blueprint("pulse_marketplace_fulfillment", __name__)

API_PREFIX = "/api/pulse/marketplace/orders"


def _bot():
    import bot

    return bot


def _json(payload, status: int = 200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _error(message: str, status: int = 400, *, code: str = "", **extra):
    payload = {"ok": False, "message": message, **extra}
    if code:
        payload["error_code"] = code
        payload.setdefault("error", code)
    return _json(payload, status)


def _payload() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _serialize(record) -> dict:
    record = dict(record or {})
    return {
        "seller_transaction_id": record.get("seller_transaction_id"),
        "state": record.get("state"),
        "fulfillment_kind": record.get("fulfillment_kind"),
        "carrier": record.get("carrier") or "",
        "tracking_reference": record.get("tracking_reference") or "",
        "tracking_url": record.get("tracking_url") or "",
        "shipped_at": record.get("shipped_at"),
        "ready_at": record.get("ready_at"),
        "delivered_at": record.get("delivered_at"),
        "completed_at": record.get("completed_at"),
    }


def _act(transaction_id: int, resolve_target):
    """Run one transition on behalf of the signed-in user.

    ``resolve_target`` is handed the record and answers which state this
    endpoint means for *that* order — so the buyer's one "I have it" button
    covers both a delivery and a pickup without the client having to know which
    lane the order took.

    The settlement call is made after the commit and never inside it:
    ``mark_delivered`` opens its own connection, so calling it from inside this
    transaction would have it read a delivery that has not landed yet and, on
    Postgres, block on locks this transaction still holds.

    The buyer's "your order shipped" notification is sent on the same terms and
    for the same reason. This route is the only door into ``shipped`` — the
    sweeper only ever advances *out* of it — so telling the buyer here reaches
    every shipment there is.
    """
    bot = _bot()
    try:
        user = bot.api_account_user()
    except Exception:
        LOGGER.exception("FULFILLMENT_AUTH_LOOKUP_FAILED")
        user = None
    if not user:
        return _error("Login required.", 401, code="LOGIN_REQUIRED")

    body = _payload()
    settle_key = ""
    shipped_buyer = None
    shipped_context = {}
    conn = bot.db()
    try:
        import sqlite3

        try:
            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        cur = conn.cursor()
        record = order_fulfillment.get_fulfillment(cur, transaction_id)
        if record is None:
            # Same answer for an order that does not exist and one that is not
            # this user's: a 404 that distinguishes them is an order-number
            # oracle for anyone with a loop.
            return _error("Order not found.", 404, code=order_fulfillment.NOT_FOUND)
        role = order_fulfillment.role_of(record, user.get("user_id"))
        if not role:
            return _error("Order not found.", 404, code=order_fulfillment.NOT_FOUND)

        target = resolve_target(record)
        if not target:
            return _error("This order cannot be updated from its current state.", 409,
                          code=order_fulfillment.ILLEGAL_TRANSITION,
                          state=record.get("state"))

        # Derived rather than taken from the client: a double-tap on a confirm
        # button is the expected failure here, and the state machine has no
        # repeated move for a client-supplied key to distinguish.
        key = f"{transaction_id}:{target}:{role}"
        try:
            result = order_fulfillment.transition(
                cur, transaction_id, target, actor_role=role,
                actor=f"{role}:{user.get('user_id')}", idempotency_key=key,
                reason=str(body.get("reason") or "")[:500],
                carrier=str(body.get("carrier") or "")[:120],
                tracking_reference=str(body.get("tracking_reference") or "")[:120],
                tracking_url=str(body.get("tracking_url") or "")[:500],
            )
        except order_fulfillment.FulfillmentError as exc:
            status = 403 if exc.code == order_fulfillment.ACTOR_NOT_AUTHORIZED else 409
            return _error(exc.message, status, code=exc.code, state=record.get("state"))
        conn.commit()
        if result["settles_delivery"]:
            settle_key = key
        if target == order_fulfillment.SHIPPED and not result["duplicate"]:
            # Built on the open cursor, after the commit: the row it describes
            # has landed, and a second connection here would be one per
            # shipment. `duplicate` is a re-press of the same button, which
            # moved nothing and so has nothing new to announce.
            shipped = dict(result["fulfillment"] or {})
            shipped_buyer = shipped.get("buyer_user_id")
            shipped_context = bot.marketplace_shipped_email_context(cur, shipped)
        payload = _serialize(result["fulfillment"])
        payload["duplicate"] = result["duplicate"]
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

    if settle_key:
        order_fulfillment.settle_delivery(
            transaction_id, actor=f"{role}:{user.get('user_id')}", idempotency_key=settle_key)
    if shipped_buyer:
        # Not email_only: nothing else tells the buyer their order shipped, so
        # there is no in-app row here to avoid duplicating — unlike the paid
        # order, which the checkout path has already announced.
        bot.emit_payment_notification("order_shipped", shipped_buyer, shipped_context)
    return _json({"ok": True, "fulfillment": payload})


@fulfillment_blueprint.route(f"{API_PREFIX}/<int:transaction_id>/fulfillment", methods=["GET"])
@auth_required
def order_fulfillment_state(transaction_id: int):
    bot = _bot()
    try:
        user = bot.api_account_user()
    except Exception:
        LOGGER.exception("FULFILLMENT_AUTH_LOOKUP_FAILED")
        user = None
    if not user:
        return _error("Login required.", 401, code="LOGIN_REQUIRED")
    conn = bot.db()
    try:
        import sqlite3

        try:
            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        record = order_fulfillment.get_fulfillment(conn.cursor(), transaction_id)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if record is None or not order_fulfillment.role_of(record, user.get("user_id")):
        return _error("Order not found.", 404, code=order_fulfillment.NOT_FOUND)
    return _json({"ok": True, "fulfillment": _serialize(record)})


@fulfillment_blueprint.route(f"{API_PREFIX}/<int:transaction_id>/processing", methods=["POST"])
@auth_required
def order_mark_processing(transaction_id: int):
    return _act(transaction_id, lambda _record: order_fulfillment.PROCESSING)


@fulfillment_blueprint.route(f"{API_PREFIX}/<int:transaction_id>/shipped", methods=["POST"])
@auth_required
def order_mark_shipped(transaction_id: int):
    return _act(transaction_id, lambda _record: order_fulfillment.SHIPPED)


@fulfillment_blueprint.route(f"{API_PREFIX}/<int:transaction_id>/ready", methods=["POST"])
@auth_required
def order_mark_ready(transaction_id: int):
    return _act(transaction_id, lambda _record: order_fulfillment.READY_FOR_PICKUP)


@fulfillment_blueprint.route(f"{API_PREFIX}/<int:transaction_id>/received", methods=["POST"])
@auth_required
def order_confirm_received(transaction_id: int):
    """The buyer's "I have it". One button for both lanes.

    Which state that means is a fact about the order, not about the button, and
    asking the client to know would make the app a second authority on a
    question the server already answers.
    """
    def target(record):
        if record.get("state") == order_fulfillment.READY_FOR_PICKUP:
            return order_fulfillment.PICKED_UP
        return order_fulfillment.DELIVERED

    return _act(transaction_id, target)


@fulfillment_blueprint.route(f"{API_PREFIX}/<int:transaction_id>/complete", methods=["POST"])
@auth_required
def order_complete(transaction_id: int):
    return _act(transaction_id, lambda _record: order_fulfillment.COMPLETED)


def register(app) -> None:
    app.register_blueprint(fulfillment_blueprint)
