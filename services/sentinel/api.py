"""Sentinel backend API contract (Stage 29) — a Flask Blueprint, READ-ONLY.

DELIBERATELY NOT REGISTERED with bot.py in V1. Wiring requires one line in
the app factory region (documented in docs/sentinel/architecture.md):

    from services.sentinel.api import sentinel_bp
    webhook_app.register_blueprint(sentinel_bp)

Reasons for shipping unwired: bot.py is under concurrent change and is
protected by the audio diff gate; registering a new privileged surface is an
owner decision, not something the foundation commit does implicitly (SC10).

Every endpoint is admin-session-gated and read-only. There is no mutation
endpoint in this contract; enforcement actions are not HTTP-triggerable.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from services.sentinel import (events, incidents, killswitches, observability,
                               providers, store)

sentinel_bp = Blueprint("sentinel", __name__, url_prefix="/api/sentinel")


def _admin_guard():
    """Reuse the platform's admin session convention; fail closed."""
    if not session.get("admin_user_id") and not session.get("is_admin"):
        return jsonify({"ok": False, "error": "admin session required"}), 403
    return None


@sentinel_bp.before_request
def _guard():
    denied = _admin_guard()
    if denied is not None:
        return denied
    if killswitches.emergency_killed():
        return jsonify({"ok": False, "error": "sentinel emergency kill switch active"}), 503
    return None


@sentinel_bp.get("/health")
def health():
    return jsonify({"ok": True, "health": observability.self_health()})


@sentinel_bp.get("/switches")
def switches():
    return jsonify({"ok": True, "switches": killswitches.switch_state()})


@sentinel_bp.get("/events")
def list_events():
    category = request.args.get("category") or None
    limit = request.args.get("limit", "100")
    try:
        rows = events.recent(category=category, limit=int(limit))
    except ValueError:
        rows = events.recent(category=category, limit=100)
    return jsonify({"ok": True, "events": rows})


@sentinel_bp.get("/incidents")
def list_incidents():
    return jsonify({"ok": True, "incidents": incidents.list_open()})


@sentinel_bp.get("/incidents/<incident_key>")
def get_incident(incident_key: str):
    found = incidents.get(incident_key)
    if not found:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "incident": found})


@sentinel_bp.get("/providers")
def provider_health():
    return jsonify({"ok": True, "providers": providers.health_table()})


@sentinel_bp.get("/metrics")
def metrics():
    hours = request.args.get("hours", "24")
    try:
        data = observability.summary(hours=int(hours))
    except ValueError:
        data = observability.summary()
    return jsonify({"ok": True, "metrics": data})


def init_sentinel(app=None) -> None:
    """Explicit opt-in wiring helper: ensures schema then registers the
    blueprint. Called by the owner, never automatically."""
    store.ensure_schema()
    if app is not None:
        app.register_blueprint(sentinel_bp)
