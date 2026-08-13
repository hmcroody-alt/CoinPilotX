"""Sentinel backend API contract (Stage 29) — a Flask Blueprint, READ-ONLY.

DELIBERATELY NOT REGISTERED with bot.py in V1. Wiring requires one line in
the app factory region (documented in docs/sentinel/architecture.md):

    from services.sentinel.api import sentinel_bp
    webhook_app.register_blueprint(sentinel_bp)

Mounted at /api/admin/sentinel (Mission 2): the path itself states the
privilege level.

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

sentinel_bp = Blueprint("sentinel", __name__, url_prefix="/api/admin/sentinel")


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


@sentinel_bp.get("/summary")
def summary():
    """Owner status contract (Mission 2): overall_status, incident counts,
    per-domain statuses, stale signal count, deployment SHA."""
    return jsonify({"ok": True, "summary": observability.owner_summary()})


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


# --- Mission 4: external intelligence read APIs (Stage 27) ------------------
# All read-only. No endpoint here can call a provider, dismiss a finding,
# upgrade a dependency, or change enforcement — those are not HTTP verbs.


@sentinel_bp.get("/threat-intelligence")
def threat_intelligence():
    """Fresh external threat matches (MALICIOUS/SUSPICIOUS observations),
    disagreement preserved per provider."""
    from services.sentinel import store as store_mod
    limit_raw = request.args.get("limit", "100")
    try:
        limit = max(1, min(int(limit_raw), 500))
    except ValueError:
        limit = 100
    with store_mod.connection(None) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT observation_id, provider_id, provider_capability, "
            "indicator_type, indicator_digest, finding_type, verdict, severity, "
            "confidence, source_trust, fetched_at, expires_at "
            "FROM sentinel_external_observations "
            "WHERE verdict IN ('MALICIOUS','SUSPICIOUS') "
            "ORDER BY id DESC LIMIT ?", (limit,))
        rows = [{"observation_id": r[0], "provider_id": r[1],
                 "capability": r[2], "indicator_type": r[3],
                 "indicator_digest": r[4], "finding_type": r[5],
                 "verdict": r[6], "severity": r[7], "confidence": r[8],
                 "source_trust": r[9], "fetched_at": r[10],
                 "expires_at": r[11]} for r in cur.fetchall()]
    return jsonify({"ok": True, "matches": rows,
                    "note": "external verdicts are evidence, not enforcement"})


@sentinel_bp.get("/vulnerabilities")
def vulnerabilities():
    from services.sentinel import supply_chain
    priority = request.args.get("priority") or None
    return jsonify({"ok": True,
                    "findings": supply_chain.findings(priority=priority),
                    "counts": supply_chain.summary_counts()})


@sentinel_bp.get("/supply-chain")
def supply_chain_view():
    from services.sentinel import supply_chain
    ecosystem = request.args.get("ecosystem") or None
    return jsonify({"ok": True,
                    "inventory": supply_chain.inventory(ecosystem=ecosystem),
                    "inventory_staleness_days": supply_chain.inventory_staleness_days(),
                    "counts": supply_chain.summary_counts()})


@sentinel_bp.get("/providers/<provider_id>/health")
def external_provider_health(provider_id: str):
    from services.sentinel import external_providers
    if provider_id not in external_providers.PROVIDERS:
        return jsonify({"ok": False,
                        "error": f"unknown provider {provider_id!r}"}), 404
    row = external_providers.provider_row(provider_id)
    if row is None:
        external_providers.ensure_registered()
        row = external_providers.provider_row(provider_id)
    return jsonify({"ok": True, "provider": row,
                    "circuits": [cb for cb in external_providers.open_circuits()
                                 if cb.get("provider_id") == provider_id],
                    "note": "CONFIGURED is not FUNCTIONAL; never called means "
                            "unknown, not healthy (Stage 2/32)"})


@sentinel_bp.get("/external-observations/<observation_id>")
def external_observation(observation_id: str):
    from services.sentinel import external_observations
    row = external_observations.get(observation_id)
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "observation": row})


def init_sentinel(app=None) -> None:
    """Explicit opt-in wiring helper: ensures schema then registers the
    blueprint. Called by the owner, never automatically."""
    store.ensure_schema()
    if app is not None:
        app.register_blueprint(sentinel_bp)
