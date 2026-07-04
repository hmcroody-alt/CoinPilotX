"""Pulse Communications 2.0 routes."""

from __future__ import annotations

import logging
import json
import os
import time

from flask import Blueprint, Response, jsonify, redirect, render_template, request, stream_with_context

from . import flags, service
from services import pulsesoc_communications_engine as call_engine
from services import pulsesoc_reliability


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


def _redact_for_log(value, depth: int = 0):
    if depth > 5:
        return "[truncated]"
    secret_keys = {"password", "token", "secret", "api_key", "authorization", "cookie", "stream_key"}
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            key_text = str(key)
            if any(secret in key_text.lower() for secret in secret_keys):
                output[key_text] = "[redacted]"
            else:
                output[key_text] = _redact_for_log(item, depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_redact_for_log(item, depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        return value if len(value) <= 500 else f"{value[:500]}...[truncated]"
    return value


def _request_debug_context(metric: str) -> dict:
    user = None
    try:
        user = _current_user()
    except Exception:
        user = None
    payload = request.get_json(silent=True) if request.content_type and "json" in request.content_type.lower() else None
    if payload is None and request.form:
        payload = dict(request.form)
    payload = payload if isinstance(payload, dict) else {}
    conversation_id = payload.get("conversation_id") or payload.get("conversation_ref") or payload.get("thread_id")
    if not conversation_id and request.view_args:
        conversation_id = request.view_args.get("conversation_id")
    return {
        "metric": metric,
        "method": request.method,
        "path": request.path,
        "remote_addr": request.headers.get("X-Forwarded-For") or request.remote_addr or "",
        "user_id": (user or {}).get("user_id") if isinstance(user, dict) else None,
        "account_id": (user or {}).get("account_id") if isinstance(user, dict) else None,
        "conversation_id": conversation_id,
        "recipient_user_ids": payload.get("recipient_user_ids") if isinstance(payload.get("recipient_user_ids"), list) else [],
        "call_type": payload.get("call_type") or request.args.get("call_type") or "",
        "content_type": request.content_type or "",
        "payload": _redact_for_log(payload),
        "railway_service": os.getenv("RAILWAY_SERVICE_NAME", ""),
        "railway_deployment": os.getenv("RAILWAY_DEPLOYMENT_ID", ""),
        "git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA", "") or os.getenv("GIT_SHA", ""),
    }


def _timed_json(metric: str, action):
    started = time.perf_counter()
    trace_id = service._trace()
    try:
        payload = action()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logging.info(
            "PULSE_COMM_V2_TIMING metric=%s duration_ms=%s method=%s path=%s ok=%s status=%s trace_id=%s",
            metric,
            elapsed_ms,
            request.method,
            request.path,
            bool(payload.get("ok")) if isinstance(payload, dict) else False,
            payload.get("status") if isinstance(payload, dict) else "",
            payload.get("trace_id") if isinstance(payload, dict) else trace_id,
        )
        if isinstance(payload, dict):
            payload.setdefault("timing_ms", elapsed_ms)
            if str(metric or "").startswith(("api_call", "conversation_call")) and payload.get("ok") is False:
                logging.warning(
                    "PULSESOC_CALL_ROUTE_FAILED metric=%s status=%s error_code=%s correlation_id=%s path=%s",
                    metric,
                    payload.get("status") or "",
                    payload.get("error_code") or "",
                    payload.get("correlation_id") or payload.get("trace_id") or trace_id,
                    request.path,
                )
        return _json(payload)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        context = _request_debug_context(metric)
        logging.exception(
            "PULSE_COMM_V2_ROUTE_EXCEPTION metric=%s duration_ms=%s method=%s path=%s trace_id=%s content_type=%s error_type=%s request_context=%s",
            metric,
            elapsed_ms,
            request.method,
            request.path,
            trace_id,
            request.content_type or "",
            type(exc).__name__,
            json.dumps(context, default=str, sort_keys=True),
        )
        call_route = str(metric or "").startswith(("api_call", "conversation_call", "admin_call"))
        if call_route:
            payload = call_engine._err(
                "Call backend error.",
                500,
                "server_error",
                correlation_id=trace_id,
                error_overrides={"exception_type": type(exc).__name__},
            )
            payload["timing_ms"] = elapsed_ms
            return _json(payload)
        return _json({
            "ok": False,
            "status": "server_error",
            "message": "Messenger request failed.",
            "error_code": "BACKEND_EXCEPTION",
            "error_title": "Messenger backend error",
            "error_description": "PulseSoc hit an unexpected backend error while handling this Messenger request.",
            "remediation": "Refresh Messenger and retry. If it repeats, inspect the correlation ID in logs.",
            "correlation_id": trace_id,
            "trace_id": trace_id,
            "http_status": 500,
            "timing_ms": elapsed_ms,
        })


def _require_user():
    user = _current_user()
    if not user:
        return None, (jsonify({"ok": False, "status": "error", "message": "Login required."}), 401)
    return user, None


@comm_v2_blueprint.get("/pulse/messages-v2")
def messages_v2_page():
    user = _current_user()
    if not user:
        return _bot().redirect(_bot().url_for("login_page", next="/pulse/messages-v2"))
    try:
        from services import command_center_client

        ai_enabled = bool(command_center_client.is_enabled() and command_center_client.ai_enabled())
    except Exception:
        ai_enabled = False
    return render_template(
        "pulse_messages_v2.html",
        enabled=flags.is_enabled(),
        current_user=user,
        ai_enabled=ai_enabled,
        initial_conversation_id=int(request.args.get("conversation") or 0),
    )


@comm_v2_blueprint.get(f"{API_PREFIX}/health")
@comm_v2_blueprint.get("/api/pulse/comm/v2/health")
def health():
    return jsonify({"enabled": flags.is_enabled(), "status": "ready" if flags.is_enabled() else "disabled"})


@comm_v2_blueprint.get("/health/live")
def health_live():
    return jsonify({
        "ok": True,
        "status": "alive",
        "service": "coinpilotx-web",
        "optional_provider_failures_block_startup": False,
    })


@comm_v2_blueprint.get("/health/ready")
def health_ready():
    payload = pulsesoc_reliability.readiness_snapshot()
    return jsonify(payload), 200 if payload.get("ok") else 503


@comm_v2_blueprint.get("/admin/health/deep")
def admin_health_deep():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return jsonify(pulsesoc_reliability.deep_health_snapshot())


@comm_v2_blueprint.get("/admin/pulse-ai/learning")
def admin_pulse_ai_learning_page():
    admin = _current_admin()
    if not admin:
        return _bot().redirect(_bot().url_for("admin_login_page", next=request.path))
    from services import pulse_ai_service

    return render_template("admin_pulse_ai_learning_center.html", admin=admin, dashboard=pulse_ai_service.admin_learning_dashboard())


@comm_v2_blueprint.get("/api/admin/pulse-ai/health")
def api_admin_pulse_ai_health():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    from services import pulse_ai_service

    return _json(pulse_ai_service.status())


@comm_v2_blueprint.get("/api/admin/pulse-ai/learning")
def api_admin_pulse_ai_learning():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    from services import pulse_ai_service

    return _json(pulse_ai_service.admin_learning_dashboard())


def _render_pulse_signal_surface(surface_key):
    user = _current_user()
    if not user:
        return _bot().redirect(_bot().url_for("login_page", next=request.path))
    from services import pulsesoc_intelligence_engine

    surface = pulsesoc_intelligence_engine.user_surface(surface_key)
    return render_template(
        "pulsesoc_intelligence_center.html",
        current_user=user,
        surface=surface,
        initial_state=pulsesoc_intelligence_engine.user_surface_state(int(user["user_id"]), surface["key"]),
    )


@comm_v2_blueprint.get("/pulse/intelligence")
@comm_v2_blueprint.get("/pulse/signals")
@comm_v2_blueprint.get("/pulse/alerts")
def pulse_alerts_page():
    return _render_pulse_signal_surface("alerts")


@comm_v2_blueprint.get("/pulse/forecasts")
def pulse_forecasts_page():
    return _render_pulse_signal_surface("forecasts")


@comm_v2_blueprint.get("/pulse/briefing")
def pulse_daily_briefing_page():
    return _render_pulse_signal_surface("briefing")


@comm_v2_blueprint.get("/pulse/settings/intelligence")
@comm_v2_blueprint.get("/pulse/settings/signals")
def pulse_signal_preferences_page():
    return _render_pulse_signal_surface("preferences")


@comm_v2_blueprint.get("/pulse/signals/<string:signal_key>")
def pulse_signal_stream_page(signal_key):
    from services import pulsesoc_intelligence_engine

    if signal_key not in pulsesoc_intelligence_engine.USER_SURFACES or signal_key in {"alerts", "forecasts", "briefing", "preferences"}:
        return _bot().redirect("/pulse/intelligence")
    return _render_pulse_signal_surface(signal_key)


@comm_v2_blueprint.get("/api/pulse/intelligence/state")
def api_galaxy_intelligence_state():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulsesoc_intelligence_engine

        return pulsesoc_intelligence_engine.user_surface_state(
            int(user["user_id"]),
            request.args.get("view") or "alerts",
            int(request.args.get("limit") or 40),
        )

    return _timed_json("galaxy_intelligence_state", run)


@comm_v2_blueprint.patch("/api/pulse/intelligence/streams/<path:stream_key>")
def api_galaxy_intelligence_stream_update(stream_key):
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulsesoc_intelligence_engine

        return pulsesoc_intelligence_engine.update_stream(int(user["user_id"]), stream_key, request.get_json(silent=True) or {})

    return _timed_json("galaxy_intelligence_stream_update", run)


@comm_v2_blueprint.post("/api/pulse/intelligence/feedback")
def api_galaxy_intelligence_feedback():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulsesoc_intelligence_engine

        return pulsesoc_intelligence_engine.record_feedback(int(user["user_id"]), request.get_json(silent=True) or {})

    return _timed_json("galaxy_intelligence_feedback", run)


@comm_v2_blueprint.get("/admin/intelligence")
def admin_galaxy_intelligence_page():
    admin = _current_admin()
    if not admin:
        return _bot().redirect(_bot().url_for("admin_login_page", next=request.path))
    from services import pulsesoc_intelligence_engine

    return render_template(
        "admin_galaxy_intelligence_center.html",
        admin=admin,
        dashboard=pulsesoc_intelligence_engine.admin_dashboard(request.args.get("stream") or ""),
        collect_result={},
    )


@comm_v2_blueprint.get("/api/admin/intelligence/health")
def api_admin_galaxy_intelligence_health():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    from services import pulsesoc_intelligence_engine

    return _json(pulsesoc_intelligence_engine.health())


@comm_v2_blueprint.get("/api/admin/intelligence/state")
def api_admin_galaxy_intelligence_state():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    from services import pulsesoc_intelligence_engine

    return _json(pulsesoc_intelligence_engine.admin_dashboard(request.args.get("stream") or ""))


@comm_v2_blueprint.post("/api/admin/intelligence/collect")
def api_admin_galaxy_intelligence_collect():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403

    def run():
        from services.intelligence_collectors import run_collectors

        payload = request.get_json(silent=True) or {}
        return run_collectors(
            stream_key=payload.get("stream_key") or "pulsesoc_discoveries",
            all_streams=bool(payload.get("all_streams")),
            dry_run=bool(payload.get("dry_run", True)),
            limit=int(payload.get("limit") or 20),
            target_user_id=int(payload.get("target_user_id") or 0),
            deliver=bool(payload.get("deliver")),
        )

    return _timed_json("admin_galaxy_intelligence_collect", run)


def _admin_can_mass_send(admin: dict) -> bool:
    role = str((admin or {}).get("role") or (admin or {}).get("admin_role") or "").strip().lower()
    if role in {"readonly", "read_only", "viewer", "support", "analyst"}:
        return False
    if bool((admin or {}).get("readonly") or (admin or {}).get("read_only")):
        return False
    return True


def _log_intelligence_admin_action(admin: dict, action: str, metadata: dict) -> None:
    try:
        _bot().log_admin_audit(
            (admin or {}).get("id") or (admin or {}).get("user_id") or 0,
            action,
            "intelligence_delivery",
            str((metadata or {}).get("event_id") or (metadata or {}).get("job_id") or ""),
            metadata,
        )
    except Exception:
        logging.info("INTELLIGENCE_ADMIN_AUDIT_SKIPPED action=%s", action)


@comm_v2_blueprint.post("/api/admin/intelligence/delivery/test")
def api_admin_intelligence_delivery_test():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403

    def run():
        from services import pulsesoc_intelligence_engine

        payload = request.get_json(silent=True) or {}
        target_user_id = int(payload.get("target_user_id") or (admin.get("user_id") or admin.get("id") or 0) or 0)
        result = pulsesoc_intelligence_engine.send_test_alert(
            int(admin.get("user_id") or admin.get("id") or 0),
            target_user_id=target_user_id,
            stream_key=payload.get("stream_key") or "pulsesoc_discoveries",
        )
        _log_intelligence_admin_action(admin, "intelligence_test_alert", {"target_user_id": target_user_id, "stream_key": payload.get("stream_key") or "pulsesoc_discoveries", "event_id": result.get("event_id")})
        return result

    return _timed_json("admin_intelligence_delivery_test", run)


@comm_v2_blueprint.post("/api/admin/intelligence/delivery/send")
def api_admin_intelligence_delivery_send():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "subscribers").strip().lower()
    if mode in {"all", "subscribers"} and not _admin_can_mass_send(admin):
        return jsonify({"ok": False, "error": "mass_send_forbidden", "message": "This admin role cannot mass-send Intelligence alerts."}), 403

    def run():
        from services import pulsesoc_intelligence_engine

        result = pulsesoc_intelligence_engine.admin_send_event(payload)
        _log_intelligence_admin_action(admin, "intelligence_manual_send", {"mode": mode, "event_id": payload.get("event_id"), "result": result})
        return result

    return _timed_json("admin_intelligence_delivery_send", run)


@comm_v2_blueprint.post("/api/admin/intelligence/delivery/process")
def api_admin_intelligence_delivery_process():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403

    def run():
        from services import pulsesoc_intelligence_engine

        payload = request.get_json(silent=True) or {}
        delivery = pulsesoc_intelligence_engine.process_delivery_queue(limit=int(payload.get("limit") or 100))
        digests = pulsesoc_intelligence_engine.process_digest_jobs(limit=int(payload.get("digest_limit") or 50))
        _log_intelligence_admin_action(admin, "intelligence_process_queue", {"delivery": delivery, "digests": digests})
        return {"ok": True, "delivery": delivery, "digests": digests}

    return _timed_json("admin_intelligence_delivery_process", run)


@comm_v2_blueprint.post("/api/admin/intelligence/delivery/digests")
def api_admin_intelligence_delivery_generate_digests():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    if not _admin_can_mass_send(admin):
        return jsonify({"ok": False, "error": "digest_forbidden", "message": "This admin role cannot generate digest delivery."}), 403

    def run():
        from services import pulsesoc_intelligence_engine

        payload = request.get_json(silent=True) or {}
        result = pulsesoc_intelligence_engine.generate_digest_jobs(
            int(payload.get("target_user_id") or 0),
            stream_key=payload.get("stream_key") or "",
            limit=int(payload.get("limit") or 500),
            digest_type=payload.get("digest_type") or "daily",
        )
        _log_intelligence_admin_action(admin, "intelligence_generate_digest", {"payload": payload, "result": result})
        return result

    return _timed_json("admin_intelligence_delivery_digests", run)


@comm_v2_blueprint.post("/api/admin/intelligence/delivery/cancel")
def api_admin_intelligence_delivery_cancel():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403

    def run():
        from services import pulsesoc_intelligence_engine

        payload = request.get_json(silent=True) or {}
        result = pulsesoc_intelligence_engine.cancel_delivery_job(int(payload.get("job_id") or 0))
        _log_intelligence_admin_action(admin, "intelligence_cancel_delivery", {"job_id": payload.get("job_id"), "result": result})
        return result

    return _timed_json("admin_intelligence_delivery_cancel", run)


@comm_v2_blueprint.get("/api/admin/intelligence/delivery/logs")
def api_admin_intelligence_delivery_logs():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    from services import pulsesoc_intelligence_engine

    return _json(pulsesoc_intelligence_engine.delivery_diagnostics(int(request.args.get("limit") or 50)))


@comm_v2_blueprint.get("/api/admin/intelligence/cadence/status")
def api_admin_intelligence_cadence_status():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    from services import pulsesoc_intelligence_engine

    return _json({"ok": True, "cadence": pulsesoc_intelligence_engine.cadence_status()})


@comm_v2_blueprint.post("/api/admin/intelligence/cadence/send-now")
def api_admin_intelligence_cadence_send_now():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    payload = request.get_json(silent=True) or {}
    target_user_id = int(payload.get("target_user_id") or 0)
    if not target_user_id and not _admin_can_mass_send(admin):
        return jsonify({"ok": False, "error": "cadence_send_forbidden", "message": "This admin role cannot send cadence alerts to subscribers."}), 403

    def run():
        from services import pulsesoc_intelligence_engine

        result = pulsesoc_intelligence_engine.run_alert_cadence(
            force=True,
            target_user_id=target_user_id,
            limit=int(payload.get("limit") or 500),
        )
        _log_intelligence_admin_action(admin, "intelligence_cadence_send_now", {"target_user_id": target_user_id, "result": result, "event_id": result.get("event_id")})
        return result

    return _timed_json("admin_intelligence_cadence_send_now", run)


def _sse_response(generator):
    response = Response(stream_with_context(generator()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _main_app_sse_allowed() -> bool:
    """Keep long-lived browser streams off the main web workers by default."""
    return os.getenv("PULSE_MAIN_APP_SSE_ALLOWED", "").strip().lower() in {"1", "true", "yes", "on"}


def _polling_fallback_response(reason: str = "main_app_worker_protection"):
    response = Response(status=204)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Pulse-Realtime-Transport"] = "polling"
    response.headers["X-Pulse-SSE-Disabled-Reason"] = reason
    return response


@comm_v2_blueprint.get(f"{API_PREFIX}/diagnostics")
def diagnostics():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _json(service.infrastructure_diagnostics())


@comm_v2_blueprint.get("/api/pulse-ai/conversation")
def pulse_ai_conversation():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.get_conversation(user["user_id"], int(request.args.get("limit") or 80))

    return _timed_json("pulse_ai_conversation", run)


@comm_v2_blueprint.post("/api/pulse-ai/message")
def pulse_ai_message():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.send_message(user["user_id"], request.get_json(silent=True) or {})

    return _timed_json("pulse_ai_message", run)


@comm_v2_blueprint.post("/api/pulse-ai/reset")
def pulse_ai_reset():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.reset_conversation(user["user_id"])

    return _timed_json("pulse_ai_reset", run)


@comm_v2_blueprint.get("/api/pulse-ai/status")
def pulse_ai_status():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.status()

    return _timed_json("pulse_ai_status", run)


@comm_v2_blueprint.get("/api/pulse-ai/settings")
def pulse_ai_settings():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.get_settings(user["user_id"])

    return _timed_json("pulse_ai_settings", run)


@comm_v2_blueprint.patch("/api/pulse-ai/settings")
def pulse_ai_settings_update():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.update_settings(user["user_id"], request.get_json(silent=True) or {})

    return _timed_json("pulse_ai_settings_update", run)


@comm_v2_blueprint.post("/api/pulse-ai/feedback")
def pulse_ai_feedback():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.record_feedback(user["user_id"], request.get_json(silent=True) or {})

    return _timed_json("pulse_ai_feedback", run)


@comm_v2_blueprint.post("/api/pulse-ai/memory/clear")
def pulse_ai_clear_memory():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.clear_memory(user["user_id"])

    return _timed_json("pulse_ai_clear_memory", run)


@comm_v2_blueprint.get("/api/pulse-ai/memory/export")
def pulse_ai_export_memory():
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import pulse_ai_service

        return pulse_ai_service.export_memory(user["user_id"])

    return _timed_json("pulse_ai_export_memory", run)


@comm_v2_blueprint.get(f"{API_PREFIX}/realtime/stream")
@comm_v2_blueprint.get("/api/pulse/comm/v2/realtime/stream")
def realtime_stream():
    user, denied = _require_user()
    if denied:
        return denied
    if not _main_app_sse_allowed():
        return _polling_fallback_response()
    if os.getenv("PULSE_COMM_V2_SSE_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return _polling_fallback_response("sse_flag_disabled")
    args = dict(request.args)

    def generate():
        payload = service.stream_realtime_events(user["user_id"], args)
        if not payload.get("ok"):
            yield "event: error\n"
            yield f"data: {json.dumps(payload, default=str)}\n\n"
            return
        yield "event: pulse\n"
        yield f"data: {json.dumps(payload, default=str)}\n\n"

    return _sse_response(generate)


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


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/ai/summary")
def ai_summary(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import command_center_client

        body = request.get_json(silent=True) if request.is_json else {}
        body = body if isinstance(body, dict) else {}
        context = service.ai_context_for_conversation(
            user["user_id"],
            conversation_ref,
            limit=int(request.args.get("limit") or body.get("limit") or 30),
        )
        if not context.get("ok"):
            return context
        if not command_center_client.is_enabled() or not command_center_client.ai_enabled():
            return {"ok": True, "available": False, "status": "disabled", "reason": "ai_disabled", "message": "AI analysis is not enabled.", "trace_id": service._trace()}
        return command_center_client.request_chat_summary(
            context.get("conversation_id"),
            user["user_id"],
            context.get("messages") or [],
            event_id=f"comm-ai-summary-{context.get('conversation_id')}-{user['user_id']}",
        )

    return _timed_json("ai_chat_summary", run)


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/ai/smart-replies")
def ai_smart_replies(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied

    def run():
        from services import command_center_client

        context = service.ai_context_for_conversation(user["user_id"], conversation_ref, limit=12)
        if not context.get("ok"):
            return context
        if not command_center_client.is_enabled() or not command_center_client.ai_enabled():
            return {"ok": True, "available": False, "status": "disabled", "reason": "ai_disabled", "message": "AI suggestions are not enabled.", "trace_id": service._trace()}
        return command_center_client.request_smart_replies(
            context.get("conversation_id"),
            user["user_id"],
            context.get("messages") or [],
            event_id=f"comm-ai-replies-{context.get('conversation_id')}-{user['user_id']}",
        )

    return _timed_json("ai_smart_replies", run)


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/messages")
@comm_v2_blueprint.post("/api/pulse/comm/v2/conversations/<path:conversation_ref>/messages")
def send_message(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("send_message", lambda: service.send_message(user["user_id"], conversation_ref, request.get_json(silent=True) or {}))


@comm_v2_blueprint.get(f"{API_PREFIX}/realtime")
@comm_v2_blueprint.get("/api/pulse/comm/v2/realtime")
def realtime_events():
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("realtime_delivery", lambda: service.poll_realtime_events(user["user_id"], request.args))


@comm_v2_blueprint.post(f"{API_PREFIX}/attachments/upload")
def upload_attachment():
    user, denied = _require_user()
    if denied:
        return denied
    file_storage = request.files.get("file") or request.files.get("attachment")
    conversation_ref = request.form.get("conversation_id") or request.form.get("conversation_ref") or ""
    metadata = {
        "attachment_kind": request.form.get("attachment_kind") or request.form.get("kind") or "",
        "duration_seconds": request.form.get("duration_seconds") or "",
        "waveform_json": request.form.get("waveform_json") or request.form.get("waveform") or "",
    }
    return _timed_json("attachment_upload", lambda: service.stage_attachment_upload(user["user_id"], file_storage, conversation_ref, metadata))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/members")
@comm_v2_blueprint.get("/api/pulse/comm/v2/conversations/<path:conversation_ref>/members")
def members(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _json(service.list_members(user["user_id"], conversation_ref))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/control-center")
def conversation_control_center(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("conversation_control_center", lambda: service.conversation_control_center(user["user_id"], conversation_ref))


@comm_v2_blueprint.patch(f"{API_PREFIX}/conversations/<path:conversation_ref>/control-center")
def update_conversation_control_center(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("conversation_control_center_update", lambda: service.update_conversation_control_center(user["user_id"], conversation_ref, request.get_json(silent=True) or {}))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/control-center/media")
def conversation_control_media(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("conversation_control_media", lambda: service.conversation_control_media(user["user_id"], conversation_ref, request.args))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/control-center/links")
def conversation_control_links(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("conversation_control_links", lambda: service.conversation_control_links(user["user_id"], conversation_ref, request.args))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/control-center/pins")
def conversation_control_pins(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("conversation_control_pins", lambda: service.conversation_control_pins(user["user_id"], conversation_ref, request.args))


@comm_v2_blueprint.get(f"{API_PREFIX}/conversations/<path:conversation_ref>/control-center/export")
def conversation_control_export(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("conversation_control_export", lambda: service.conversation_control_export(user["user_id"], conversation_ref, request.args))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/control-center/action")
def conversation_control_action(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("conversation_control_action", lambda: service.conversation_control_action(user["user_id"], conversation_ref, request.get_json(silent=True) or {}))


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


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/pin")
def pin_conversation(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("pin_conversation", lambda: service.toggle_pin(user["user_id"], conversation_ref))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/unread")
def unread_conversation(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("mark_unread", lambda: service.mark_unread(user["user_id"], conversation_ref))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/mute")
def mute_conversation(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("mute_conversation", lambda: service.toggle_mute(user["user_id"], conversation_ref))


@comm_v2_blueprint.post(f"{API_PREFIX}/conversations/<path:conversation_ref>/archive")
def archive_conversation(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("archive_conversation", lambda: service.archive_conversation(user["user_id"], conversation_ref))


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


@comm_v2_blueprint.post(f"{API_PREFIX}/messages/<int:message_id>/pin")
def pin_message(message_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("pin_message", lambda: service.toggle_message_pin(user["user_id"], message_id))


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
def start_conversation_call(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    if not flags.is_enabled():
        return _json({"ok": False, "status": "disabled", "message": service.DISABLED_MESSAGE, "trace_id": service._trace()})
    payload = request.get_json(silent=True) or {}
    payload["conversation_id"] = conversation_ref
    payload["call_type"] = "video" if request.path.endswith("/video/start") else "audio"
    return _timed_json("conversation_call_start", lambda: call_engine.start_call(user["user_id"], payload))


@comm_v2_blueprint.post("/api/calls/start")
def api_start_call():
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_start", lambda: call_engine.start_call(user["user_id"], request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/accept")
def api_accept_call(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_accept", lambda: call_engine.accept_call(user["user_id"], call_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/ring-seen")
def api_ring_seen(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_ring_seen", lambda: call_engine.mark_ring_seen(user["user_id"], call_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/decline")
def api_decline_call(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_decline", lambda: call_engine.decline_call(user["user_id"], call_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/end")
def api_end_call(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_end", lambda: call_engine.end_call(user["user_id"], call_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/join-token")
def api_call_join_token(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_join_token", lambda: call_engine.join_token(user["user_id"], call_id))


@comm_v2_blueprint.get("/api/calls/<path:call_id>/status")
def api_call_status(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_status", lambda: call_engine.call_status(user["user_id"], call_id))


@comm_v2_blueprint.get("/api/calls/active")
def api_active_calls():
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_active_calls", lambda: call_engine.active_calls(user["user_id"]))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/quality")
def api_call_quality(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_quality", lambda: call_engine.submit_quality_report(user["user_id"], call_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/connected")
def api_call_connected(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_connected", lambda: call_engine.mark_connected(user["user_id"], call_id, request.get_json(silent=True) or {}))


@comm_v2_blueprint.get("/api/calls/<path:call_id>/events")
def api_call_events(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_events", lambda: call_engine.call_events(user["user_id"], call_id))


@comm_v2_blueprint.get("/api/conversations/<path:conversation_ref>/calls")
def api_conversation_calls(conversation_ref):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_conversation_calls", lambda: call_engine.conversation_calls(user["user_id"], conversation_ref, int(request.args.get("limit") or 40)))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/mute-audio")
def api_call_mute_audio(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_mute_audio", lambda: call_engine.update_participant_control(user["user_id"], call_id, "mute-audio", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/unmute-audio")
def api_call_unmute_audio(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_unmute_audio", lambda: call_engine.update_participant_control(user["user_id"], call_id, "unmute-audio", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/enable-video")
def api_call_enable_video(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_enable_video", lambda: call_engine.update_participant_control(user["user_id"], call_id, "enable-video", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/disable-video")
def api_call_disable_video(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_disable_video", lambda: call_engine.update_participant_control(user["user_id"], call_id, "disable-video", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/switch-camera")
def api_call_switch_camera(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_switch_camera", lambda: call_engine.update_participant_control(user["user_id"], call_id, "switch-camera", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/speaker")
def api_call_speaker(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_speaker", lambda: call_engine.update_participant_control(user["user_id"], call_id, "speaker", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/minimize")
def api_call_minimize(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_minimize", lambda: call_engine.update_participant_control(user["user_id"], call_id, "minimize", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/restore")
def api_call_restore(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_restore", lambda: call_engine.update_participant_control(user["user_id"], call_id, "restore", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/visibility")
def api_call_visibility(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_visibility", lambda: call_engine.update_participant_control(user["user_id"], call_id, "visibility", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/screen-share/start")
def api_call_screen_share_start(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_screen_share_start", lambda: call_engine.update_participant_control(user["user_id"], call_id, "screen-share-start", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/calls/<path:call_id>/screen-share/stop")
def api_call_screen_share_stop(call_id):
    user, denied = _require_user()
    if denied:
        return denied
    return _timed_json("api_call_screen_share_stop", lambda: call_engine.update_participant_control(user["user_id"], call_id, "screen-share-stop", request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/livekit/webhook")
def api_livekit_webhook():
    raw = request.get_data(cache=True) or b""
    return _json(call_engine.livekit_webhook(dict(request.headers), raw, request.get_json(silent=True) or {}))


def _admin_calls_page(view: str = "recent", call_id: str = "", panel: str = "overview", test_result: dict | None = None):
    admin = _current_admin()
    if not admin:
        return _bot().redirect(_bot().url_for("admin_login_page", next=request.path))
    summary = call_engine.calls_dashboard_summary()
    calls = call_engine.admin_calls_list(view, int(request.args.get("limit") or 60))
    detail = {}
    delivery = {}
    timeline = {}
    inspector = {}
    if call_id:
        if panel == "delivery":
            delivery = call_engine.call_delivery_diagnostics(call_id)
            detail = call_engine.admin_call_detail(call_id)
        elif panel == "timeline":
            timeline = call_engine.call_timeline(call_id)
            detail = call_engine.admin_call_detail(call_id)
        elif panel == "inspector":
            inspector = call_engine.call_inspector(call_id)
            detail = inspector
        else:
            detail = call_engine.admin_call_detail(call_id)
    return render_template(
        "admin_calls_command_center.html",
        admin=admin,
        view=view,
        panel=panel,
        summary=summary,
        calls=calls,
        detail=detail,
        delivery=delivery,
        timeline=timeline,
        inspector=inspector,
        test_result=test_result or {},
    )


@comm_v2_blueprint.get("/admin/calls")
def admin_calls_page():
    return _admin_calls_page("recent")


@comm_v2_blueprint.get("/admin/calls/recent")
def admin_calls_recent_page():
    return _admin_calls_page("recent")


@comm_v2_blueprint.get("/admin/calls/active")
def admin_calls_active_page():
    return _admin_calls_page("active")


@comm_v2_blueprint.get("/admin/calls/failed")
def admin_calls_failed_page():
    return _admin_calls_page("failed")


@comm_v2_blueprint.get("/admin/calls/missed")
def admin_calls_missed_page():
    return _admin_calls_page("missed")


@comm_v2_blueprint.route("/admin/calls/test-config", methods=["GET", "POST"])
def admin_calls_test_config_page():
    admin = _current_admin()
    if not admin:
        return _bot().redirect(_bot().url_for("admin_login_page", next=request.path))
    result = call_engine.test_config({}) if request.method == "POST" else {}
    return _admin_calls_page("recent", panel="test-config", test_result=result)


@comm_v2_blueprint.route("/admin/calls/quality-test", methods=["GET", "POST"])
def admin_calls_quality_test_page():
    admin = _current_admin()
    if not admin:
        return _bot().redirect(_bot().url_for("admin_login_page", next=request.path))
    result = call_engine.admin_livekit_quality_test({}) if request.method == "POST" else {}
    return _admin_calls_page("recent", panel="quality-test", test_result=result)


@comm_v2_blueprint.get("/admin/calls/<path:call_id>/timeline")
def admin_calls_timeline_page(call_id):
    return _admin_calls_page("recent", call_id, "timeline")


@comm_v2_blueprint.get("/admin/calls/<path:call_id>/delivery")
def admin_calls_delivery_page(call_id):
    return _admin_calls_page("recent", call_id, "delivery")


@comm_v2_blueprint.get("/admin/calls/<path:call_id>/inspector")
def admin_calls_inspector_page(call_id):
    return _admin_calls_page("recent", call_id, "inspector")


@comm_v2_blueprint.post("/admin/calls/<path:call_id>/force-end")
def admin_calls_force_end_page(call_id):
    admin = _current_admin()
    if not admin:
        return _bot().redirect(_bot().url_for("admin_login_page", next=request.path))
    call_engine.admin_force_end_call(call_id, int(admin.get("id") or 0), "admin_command_center_force_end")
    return redirect(f"/admin/calls/{call_id}/inspector")


@comm_v2_blueprint.get("/admin/calls/<path:call_id>")
def admin_calls_detail_page(call_id):
    return _admin_calls_page("recent", call_id, "detail")


@comm_v2_blueprint.get("/api/admin/calls/recent")
def api_admin_recent_calls():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_recent_calls", lambda: call_engine.recent_calls(int(request.args.get("limit") or 40)))


@comm_v2_blueprint.get("/api/admin/calls/active")
def api_admin_active_calls():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_active_calls", lambda: call_engine.admin_calls_list("active", int(request.args.get("limit") or 60)))


@comm_v2_blueprint.get("/api/admin/calls/failed")
def api_admin_failed_calls():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_failed_calls", lambda: call_engine.admin_calls_list("failed", int(request.args.get("limit") or 60)))


@comm_v2_blueprint.get("/api/admin/calls/<path:call_id>/delivery")
def api_admin_call_delivery(call_id):
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_call_delivery", lambda: call_engine.call_delivery_diagnostics(call_id))


@comm_v2_blueprint.get("/api/admin/calls/<path:call_id>/timeline")
def api_admin_call_timeline(call_id):
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_call_timeline", lambda: call_engine.call_timeline(call_id))


@comm_v2_blueprint.get("/api/admin/calls/<path:call_id>/inspector")
def api_admin_call_inspector(call_id):
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_call_inspector", lambda: call_engine.call_inspector(call_id))


@comm_v2_blueprint.post("/api/admin/calls/<path:call_id>/force-end")
def api_admin_call_force_end(call_id):
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_call_force_end", lambda: call_engine.admin_force_end_call(call_id, int(admin.get("id") or 0), "admin_api_force_end"))


@comm_v2_blueprint.get("/api/admin/calls/<path:call_id>")
def api_admin_call_detail(call_id):
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_call_detail", lambda: call_engine.admin_call_detail(call_id))


@comm_v2_blueprint.post("/api/admin/calls/test-config")
def api_admin_call_test_config():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_call_test_config", lambda: call_engine.test_config(request.get_json(silent=True) or {}))


@comm_v2_blueprint.post("/api/admin/calls/quality-test")
def api_admin_call_quality_test():
    admin = _current_admin()
    if not admin:
        return jsonify({"ok": False, "status": "error", "message": "Admin access required."}), 403
    return _timed_json("admin_call_quality_test", lambda: call_engine.admin_livekit_quality_test(request.get_json(silent=True) or {}))


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
