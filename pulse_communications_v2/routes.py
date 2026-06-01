"""Pulse Communications 2.0 route blueprint."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .flags import PULSE_COMMUNICATIONS_V2_ENABLED
from . import service


comm_v2_blueprint = Blueprint("pulse_communications_v2", __name__)


@comm_v2_blueprint.get("/api/pulse/comm/v2/health")
def health():
    if not PULSE_COMMUNICATIONS_V2_ENABLED:
        return jsonify({"enabled": False, "status": "disabled"})
    return jsonify({"enabled": True, "status": "ready"})


def _bot():
    import bot

    return bot


def _current_user():
    return _bot().api_account_user()


def _json(payload: dict):
    status = int(payload.pop("http_status", 200 if payload.get("ok") else 400) or 200)
    return jsonify(payload), status


@comm_v2_blueprint.get("/api/pulse/comm/v2/conversations")
def conversations():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    return _json(service.list_conversations(user["user_id"], {"type": request.args.get("type") or "all"}))


@comm_v2_blueprint.post("/api/pulse/comm/v2/conversations")
def create_conversation():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    return _json(service.create_conversation(user["user_id"], request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/pulse/comm/v2/direct/open")
def open_direct():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    payload["conversation_type"] = "direct"
    return _json(service.create_conversation(user["user_id"], payload))


@comm_v2_blueprint.post("/api/pulse/comm/v2/rooms")
def create_room():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    payload["conversation_type"] = "room"
    return _json(service.create_conversation(user["user_id"], payload))


@comm_v2_blueprint.post("/api/pulse/comm/v2/groups")
def create_group():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    payload["conversation_type"] = "group"
    return _json(service.create_conversation(user["user_id"], payload))


@comm_v2_blueprint.get("/api/pulse/comm/v2/conversations/<path:conversation_ref>/messages")
def messages(conversation_ref):
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    return _json(service.list_messages(user["user_id"], conversation_ref, request.args))


@comm_v2_blueprint.post("/api/pulse/comm/v2/conversations/<path:conversation_ref>/messages")
def send_message(conversation_ref):
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    return _json(service.send_message(user["user_id"], conversation_ref, request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/pulse/comm/v2/conversations/<path:conversation_ref>/read")
def read_state(conversation_ref):
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    return _json(service.mark_read(user["user_id"], conversation_ref))


@comm_v2_blueprint.get("/api/pulse/comm/v2/conversations/<path:conversation_ref>/members")
def members(conversation_ref):
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    return _json(service.list_members(user["user_id"], conversation_ref))


@comm_v2_blueprint.post("/api/pulse/comm/v2/messages/<int:message_id>/reactions")
def reactions(message_id):
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    return _json(service.set_reaction(user["user_id"], message_id, payload.get("reaction") or payload.get("reaction_type") or "heart"))


@comm_v2_blueprint.post("/api/pulse/comm/v2/conversations/<path:conversation_ref>/pin")
def pin_message(conversation_ref):
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    payload = request.get_json(silent=True) or {}
    return _json(service.pin_message(user["user_id"], conversation_ref, payload.get("message_id") or 0, payload.get("pinned", True)))


@comm_v2_blueprint.get("/api/pulse/comm/v2/search")
def search():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "status": "error", "message": "Login required."}), 401
    return _json(service.search_messages(user["user_id"], request.args.get("q") or request.args.get("query") or ""))


def register(app) -> None:
    app.register_blueprint(comm_v2_blueprint)
