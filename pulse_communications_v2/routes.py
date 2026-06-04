"""Pulse Communications 2.0 routes."""

from __future__ import annotations

import logging
import time

from flask import Blueprint, jsonify, render_template, request

from . import flags, service


comm_v2_blueprint = Blueprint("pulse_communications_v2", __name__)
API_PREFIX = "/api/pulse/communications/v2"


def _bot():
    import bot

    return bot


def _current_user():
    return _bot().api_account_user()


def _current_admin():
    try:
        return _bot().admin_current_user()
    except Exception:
        return None


def _json(payload: dict):
    status = int(payload.pop("http_status", 200 if payload.get("ok") else 400) or 200)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response, status


def _timed_json(metric: str, action):
    started = time.perf_counter()
    payload = action()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logging.info(
        "PULSE_COMM_V2_TIMING metric=%s duration_ms=%s method=%s path=%s ok=%s status=%s",
        metric,
        elapsed_ms,
        request.method,
        request.path,
        bool(payload.get("ok")) if isinstance(payload, dict) else False,
        payload.get("status") if isinstance(payload, dict) else "",
    )
    if isinstance(payload, dict):
        payload.setdefault("timing_ms", elapsed_ms)
    return _json(payload)


def _require_user():
    user = _current_user()
    if not user:
        return None, (jsonify({"ok": False, "status": "error", "message": "Login required."}), 401)
    return user, None


@comm_v2_blueprint.get("/pulse/messages-v2")
def messages_v2_page():
    user = _current_user()
    if not user:
        return _bot().redirect(_bot().url_for("login", next="/pulse/messages-v2"))
    return render_template("pulse_messages_v2.html", enabled=flags.is_enabled(), current_user=user)


@comm_v2_blueprint.get(f"{API_PREFIX}/health")
@comm_v2_blueprint.get("/api/pulse/comm/v2/health")
def health():
    return jsonify({"enabled": flags.is_enabled(), "status": "ready" if flags.is_enabled() else "disabled"})


@comm_v2_blueprint.get(f"{API_PREFIX}/diagnostics")
def diagnostics():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _json(service.infrastructure_diagnostics())


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations")
@comm_v2_blueprint.get("/api/pulse/comm/v2/conversations")
def conversations():
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("conversations_list", lambda: service.list_conversations(user["user_id"], {"type": request.args.get("type") or "all"}))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations")
@comm_v2_blueprint.post("/api/pulse/comm/v2/conversations")
def create_conversation():
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.create_conversation(user["user_id"], request.get_json(silent=True) or {}))


@comm_v2_blueprint.post(f"{API_PREFIX}/direct/open")
@comm_v2_blueprint.post("/api/pulse/comm/v2/direct/open")
def open_direct():
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    payload["conversation_type"] = "direct"
    return _json(service.create_conversation(user["user_id"], payload))


@comm_v2_blueprint.post(f"{API_PREFIX}/groups")
@comm_v2_blueprint.post("/api/pulse/comm/v2/groups")
def create_group():
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    payload["conversation_type"] = "group"
    return _json(service.create_conversation(user["user_id"], payload))


@comm_v2_blueprint.post(f"{API_PREFIX}/rooms")
@comm_v2_blueprint.post("/api/pulse/comm/v2/rooms")
def create_room():
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    payload["conversation_type"] = "room"
    return _json(service.create_conversation(user["user_id"], payload))


@comm_v2_blueprint.get(f"{API_PREFIX}/rooms")
def list_rooms():
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.list_conversations(user["user_id"], {"type": "room"}))


@comm_v2_blueprint.post(f"{API_PREFIX}/communities")
def create_community():
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.create_community(user["user_id"], request.get_json(silent=True) or {}))


@comm_v2_blueprint.post(f"{API_PREFIX}/communities/<int:community_id>/channels")
def create_channel(community_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.create_channel(user["user_id"], community_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/messages")
@comm_v2_blueprint.get("/api/pulse/comm/v2/conversations/<path:conversation_ref>/messages")
def messages(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("selected_thread_messages", lambda: service.list_messages(user["user_id"], conversation_ref, request.args))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/messages")
@comm_v2_blueprint.post("/api/pulse/comm/v2/conversations/<path:conversation_ref>/messages")
def send_message(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("send_message", lambda: service.send_message(user["user_id"], conversation_ref, request.get_json(silent=True) or {}))


@comm_v2_blueprint.post(f"{API_PREFIX}/attachments/upload")
def upload_attachment():
    user, denied = _require_user()
    if denied:
        return denied
    file_storage = request.files.get("file") or request.files.get("attachment")
    conversation_ref = request.form.get("conversation_id") or request.form.get("conversation_ref") or ""
    return _timed_json("attachment_upload", lambda: service.stage_attachment_upload(user["user_id"], file_storage, conversation_ref))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/members")
@comm_v2_blueprint.get("/api/pulse/comm/v2/conversations/<path:conversation_ref>/members")
def members(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.list_members(user["user_id"], conversation_ref))


@comm_v2_blueprint.get(f"{API_PREFIX}/search")
@comm_v2_blueprint.get("/api/pulse/comm/v2/search")
def search_messages():
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("search_messages", lambda: service.search_messages(user["user_id"], request.args.get("q") or request.args.get("query") or "", request.args))


@comm_v2_blueprint.get(f"{API_PREFIX}/people/search")
@comm_v2_blueprint.get("/api/pulse/comm/v2/people/search")
def search_people():
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("search_people", lambda: service.search_people(user["user_id"], request.args.get("q") or request.args.get("query") or "", request.args))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/members")
def add_member(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    return _json(service.add_member(user["user_id"], conversation_ref, int(payload.get("user_id") or payload.get("target_user_id") or 0), payload.get("role") or "member"))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/read")
@comm_v2_blueprint.post("/api/pulse/comm/v2/conversations/<path:conversation_ref>/read")
def read_state(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("read_receipt", lambda: service.mark_read(user["user_id"], conversation_ref))


@comm_v2_blueprint.post(f"{API_PREFIX}/presence/heartbeat")
def presence_heartbeat():
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    return _timed_json("presence_heartbeat", lambda: service.heartbeat(user["user_id"], payload.get("status") or "online"))


@comm_v2_blueprint.post(f"{API_PREFIX}/settings")
def communication_settings():
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.update_settings(user["user_id"], request.get_json(silent=True) or {}))


@comm_v2_blueprint.get(f"{API_PREFIX}/settings")
def communication_settings_read():
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.get_settings(user["user_id"]))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/typing")
def typing(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    return _timed_json("typing_indicator", lambda: service.set_typing(user["user_id"], conversation_ref, bool(payload.get("is_typing", True))))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/presence")
def presence(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.conversation_presence(user["user_id"], conversation_ref))


@comm_v2_blueprint.post(f"{API_PREFIX}/messages/<int:message_id>/reactions")
@comm_v2_blueprint.post("/api/pulse/comm/v2/messages/<int:message_id>/reactions")
def reactions(message_id):
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    return _timed_json("reaction", lambda: service.set_reaction(user["user_id"], message_id, payload.get("reaction") or payload.get("reaction_type") or "heart"))


@comm_v2_blueprint.patch(f"{API_PREFIX}/messages/<int:message_id>")
def edit_message(message_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("edit_message", lambda: service.edit_message(user["user_id"], message_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.delete(f"{API_PREFIX}/messages/<int:message_id>")
def delete_message(message_id):
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    return _timed_json("delete_message", lambda: service.delete_message(user["user_id"], message_id, payload.get("delete_for") or request.args.get("delete_for") or "self"))


@comm_v2_blueprint.post(f"{API_PREFIX}/messages/<int:message_id>/forward")
def forward_message(message_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("forward_message", lambda: service.forward_message(user["user_id"], message_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.post(f"{API_PREFIX}/messages/<int:message_id>/report")
def report_message(message_id):
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    return _json(service.report_message(user["user_id"], message_id, payload.get("reason") or ""))


@comm_v2_blueprint.post(f"{API_PREFIX}/blocks")
def block_user():
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    return _json(service.block_user(user["user_id"], int(payload.get("blocked_user_id") or payload.get("user_id") or 0), payload.get("reason") or ""))


@comm_v2_blueprint.get(f"{API_PREFIX}/moderation")
def moderation_summary():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _json(service.moderation_summary(admin))


@comm_v2_blueprint.post(f"{API_PREFIX}/moderation/messages/<int:message_id>")
def moderate_message(message_id):
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    payload = request.get_json(silent=True) or {}
    return _json(service.moderate_message(admin, message_id, payload.get("action") or "hide", payload.get("reason") or ""))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/voice/start")
@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/video/start")
def phase_two_placeholder(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    if not flags.is_enabled():
        return _json({"ok": False, "status": "disabled", "message": service.DISABLED_MESSAGE, "trace_id": service._trace()})
    return _json({"ok": True, "status": "placeholder", "conversation_id": conversation_ref, "message": "Voice and video are reserved for Phase 2.", "trace_id": service._trace()})


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/live/mux/create")
def create_mux_live(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.create_comm_v2_mux_live_stream(user["user_id"], conversation_ref, request.get_json(silent=True) or {}))


@comm_v2_blueprint.get(f"{API_PREFIX}/live/mux/<path:live_ref>")
def get_mux_live(live_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.get_comm_v2_mux_live_stream(user["user_id"], live_ref))


@comm_v2_blueprint.post(f"{API_PREFIX}/live/mux/<path:live_ref>/disable")
def disable_mux_live(live_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.disable_comm_v2_mux_live_stream(user["user_id"], live_ref))


@comm_v2_blueprint.post(f"{API_PREFIX}/live/mux/webhook")
def mux_live_webhook():
    raw = request.get_data(cache=False) or b""
    verification = service.verify_mux_webhook_signature(raw, request.headers.get("Mux-Signature"))
    if not verification.get("ok"):
        return jsonify({"ok": False, "status": "forbidden", "message": "Mux webhook signature could not be verified."}), 403
    return _json(service.process_mux_webhook(request.get_json(silent=True) or {}))


@comm_v2_blueprint.post(f"{API_PREFIX}/notifications/preview")
def notification_preview():
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.twilio_notification_preview(user["user_id"], request.get_json(silent=True) or {}))


def register(app) -> None:
    app.register_blueprint(comm_v2_blueprint)
