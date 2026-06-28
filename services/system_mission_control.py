"""PulseSoc system mission-control state.

This service turns operational status into safe, aggregate mission-control
signals for user and admin dashboards. It intentionally exposes configured or
missing states, counts, scores, and redacted health summaries only.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from services import db as db_service


STRICT_STATES = {
    "READY",
    "ACTION REQUIRED",
    "REVIEW",
    "WARNING",
    "OFFLINE",
    "LIMITED",
    "BETA",
    "ADMIN",
}


SYSTEM_MODULES: tuple[dict[str, Any], ...] = (
    {"key": "feed", "label": "Feed Intelligence", "route": "/dashboard/system/feed", "admin_route": "/admin/system/feed", "tables": ("posts", "post_comments", "post_reactions"), "accent": "emerald"},
    {"key": "messenger", "label": "Messenger Intelligence", "route": "/dashboard/system/messenger", "admin_route": "/admin/system/messenger", "tables": ("conversations", "private_messages", "message_delivery_receipts"), "accent": "cyan"},
    {"key": "live", "label": "Live Network", "route": "/dashboard/system/live", "admin_route": "/admin/system/live", "tables": ("live_streams", "live_events"), "accent": "red"},
    {"key": "radio", "label": "Radio Intelligence", "route": "/dashboard/system/radio", "admin_route": "/admin/system/radio", "tables": ("pulse_music_tracks", "radio_play_events", "audio_tracks"), "accent": "purple"},
    {"key": "marketplace", "label": "Marketplace Intelligence", "route": "/dashboard/system/marketplace", "admin_route": "/admin/system/marketplace", "tables": ("marketplace_listings", "marketplace_orders", "marketplace_transactions"), "accent": "gold"},
    {"key": "notifications", "label": "Notification Intelligence", "route": "/dashboard/system/notifications", "admin_route": "/admin/system/notifications", "tables": ("notifications", "notification_delivery_logs", "user_device_tokens"), "accent": "cyan"},
    {"key": "ai", "label": "AI Intelligence", "route": "/dashboard/system/ai", "admin_route": "/admin/system/ai", "tables": ("ai_conversations", "command_center_ai_events", "ai_recommendations"), "accent": "purple"},
    {"key": "scam-shield", "label": "Scam Shield", "route": "/dashboard/system/scam-shield", "admin_route": "/admin/system/scam-shield", "tables": ("security_events", "command_center_security_events"), "accent": "emerald"},
    {"key": "advertising", "label": "Advertising Intelligence", "route": "/dashboard/system/advertising", "admin_route": "/admin/system/advertising", "tables": ("pulse_ad_campaigns", "pulse_ad_events", "pulse_ad_clicks"), "accent": "gold"},
    {"key": "creator", "label": "Creator Intelligence", "route": "/dashboard/system/creator", "admin_route": "/admin/system/creator", "tables": ("posts", "pulse_reels", "videos", "statuses"), "accent": "blue"},
)

MODULES_BY_KEY = {module["key"]: module for module in SYSTEM_MODULES}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _row_value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return default


def _table_exists(cur: Any, table: str) -> bool:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _column_exists(cur: Any, table: str, column: str) -> bool:
    if not _table_exists(cur, table):
        return False
    try:
        if db_service.IS_POSTGRES:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name=%s",
                (table, column),
            )
            return bool(cur.fetchone())
        cur.execute(f"PRAGMA table_info({table})")
        return any(_row_value(row, "name", 1) == column for row in cur.fetchall())
    except Exception:
        return False


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        return _safe_int(_row_value(cur.fetchone(), "total", 0), 0)
    except Exception:
        return 0


def _module_count(cur: Any, tables: tuple[str, ...]) -> int:
    return sum(_count(cur, table) for table in tables)


def _env_ready(*names: str) -> bool:
    return all(bool(os.getenv(name, "").strip()) for name in names)


def _module_score(signal_count: int, required_tables: int, available_tables: int, external_ready: bool = True) -> int:
    base = 44 + min(30, signal_count // 3)
    if required_tables:
        base += int((available_tables / required_tables) * 18)
    if external_ready:
        base += 8
    return max(20, min(99, base))


def _state_for_score(score: int, *, external_ready: bool = True) -> str:
    if score >= 86:
        return "READY"
    if not external_ready and score < 68:
        return "LIMITED"
    if score >= 68:
        return "BETA"
    if score >= 45:
        return "ACTION REQUIRED"
    return "WARNING"


def build_system_state(
    conn: Any,
    *,
    admin: bool = False,
    db_diag: dict[str, Any] | None = None,
    command_center_diag: dict[str, Any] | None = None,
    realtime_diag: dict[str, Any] | None = None,
    provider_health: dict[str, Any] | None = None,
    notification_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cur = conn.cursor()
    db_diag = db_diag or {}
    command_center_diag = command_center_diag or {}
    realtime_diag = realtime_diag or {}
    provider_health = provider_health or {}
    notification_health = notification_health or {}

    external = {
        "database": bool(db_diag.get("connected")),
        "command_center": bool(command_center_diag.get("enabled")),
        "redis": bool(os.getenv("REDIS_URL")),
        "r2": _env_ready("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"),
        "mux": _env_ready("MUX_TOKEN_ID", "MUX_TOKEN_SECRET"),
        "stripe": _env_ready("STRIPE_SECRET_KEY"),
        "brevo": _env_ready("BREVO_API_KEY"),
        "openai": _env_ready("OPENAI_API_KEY"),
        "livekit": _env_ready("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"),
        "vapid": _env_ready("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"),
    }
    modules: list[dict[str, Any]] = []
    for module in SYSTEM_MODULES:
        tables = tuple(module["tables"])
        available_tables = sum(1 for table in tables if _table_exists(cur, table))
        signal_count = _module_count(cur, tables)
        external_ready = True
        if module["key"] == "live":
            external_ready = external["mux"] or external["livekit"]
        elif module["key"] == "radio":
            external_ready = external["r2"] or _table_exists(cur, "pulse_music_tracks")
        elif module["key"] == "marketplace":
            external_ready = external["stripe"] or _table_exists(cur, "marketplace_orders")
        elif module["key"] == "notifications":
            external_ready = external["vapid"] or bool(notification_health)
        elif module["key"] == "ai":
            external_ready = external["openai"] or _table_exists(cur, "command_center_ai_events")
        score = _module_score(signal_count, len(tables), available_tables, external_ready)
        state = _state_for_score(score, external_ready=external_ready)
        modules.append(
            {
                **module,
                "state": state,
                "score": score,
                "signal_count": signal_count,
                "available_tables": available_tables,
                "required_tables": len(tables),
                "latency_ms": _safe_int(db_diag.get("latency_ms"), 0) + max(1, 100 - score),
                "prediction": "Stable" if score >= 80 else "Needs attention",
                "recommendation": "Keep monitoring." if score >= 80 else "Review provider readiness, queue health, and table coverage.",
                "admin_route": module["admin_route"] if admin else "",
            }
        )

    platform_score = int(sum(module["score"] for module in modules) / max(1, len(modules)))
    infrastructure_score = int(
        (
            (95 if external["database"] else 35)
            + (86 if command_center_diag.get("enabled") else 64)
            + (82 if external["redis"] else 62)
            + min(96, 60 + len([ready for ready in external.values() if ready]) * 4)
        )
        / 4
    )
    performance_index = max(35, min(99, int((platform_score + infrastructure_score) / 2)))
    security_status = "READY" if _count(cur, "command_center_security_events", "lower(COALESCE(status,'')) IN ('critical','open','pending')") == 0 else "WARNING"
    connected_services = sum(1 for ready in external.values() if ready)
    queued_jobs = _count(cur, "notification_delivery_logs", "lower(COALESCE(status,'')) IN ('queued','retry','pending')") + _count(cur, "command_center_ai_events", "lower(COALESCE(status,'')) IN ('queued','pending','created')")
    failed_jobs = _count(cur, "notification_delivery_logs", "lower(COALESCE(status,'')) IN ('failed','error','not_configured')")
    prediction_confidence = max(52, min(96, platform_score - (8 if queued_jobs > 100 else 0)))

    timeline = [
        {"time": "now", "event": "Mission Control synchronized aggregate subsystem state.", "state": "READY"},
        {"time": "-1m", "event": f"Realtime transport: {realtime_diag.get('transport') or 'polling fallback'} with {int(realtime_diag.get('active_connections') or 0)} active connections.", "state": "BETA"},
        {"time": "-3m", "event": f"Delivery queues scanned: {queued_jobs} queued, {failed_jobs} failed or not configured.", "state": "WARNING" if failed_jobs else "READY"},
        {"time": "-5m", "event": f"Database health checked with {db_diag.get('latency_ms') or 0} ms latency.", "state": "READY" if external["database"] else "OFFLINE"},
    ]
    quick_actions = [
        {"label": "Create Content", "route": "/pulse"},
        {"label": "Go Live", "route": "/pulse/live"},
        {"label": "Open UNDX", "route": "/pulse/premium/undx"},
        {"label": "Run Safety Scan", "route": "/dashboard/intelligence/safety-scan"},
        {"label": "System Diagnostics", "route": "/admin/system" if admin else "/dashboard/system"},
        {"label": "Mission Dashboard", "route": "/dashboard"},
    ]
    if admin:
        quick_actions.extend(
            [
                {"label": "Developer Console", "route": "/admin/system-audit"},
                {"label": "Emergency Recovery", "route": "/admin/data-recovery"},
                {"label": "Launch Readiness", "route": "/admin/launch-readiness"},
                {"label": "Worker Health", "route": "/admin/pulse-worker-health"},
            ]
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "admin": bool(admin),
        "core": {
            "label": "PulseSoc Core",
            "overall_health": platform_score,
            "learning_activity": _count(cur, "ai_recommendations") + _count(cur, "command_center_ai_events"),
            "realtime_signals": int(realtime_diag.get("events_per_minute") or 0),
            "connected_services": connected_services,
            "autonomous_tasks": queued_jobs,
            "ai_agents_online": 1 if external["openai"] else 0,
            "prediction_confidence": prediction_confidence,
            "network_integrity": infrastructure_score,
            "security_status": security_status,
            "performance_index": performance_index,
            "platform_readiness": min(99, int((platform_score + performance_index + prediction_confidence) / 3)),
        },
        "scores": {
            "platform": platform_score,
            "ai": next((module["score"] for module in modules if module["key"] == "ai"), 0),
            "creator": next((module["score"] for module in modules if module["key"] == "creator"), 0),
            "economy": next((module["score"] for module in modules if module["key"] == "marketplace"), 0),
            "media": next((module["score"] for module in modules if module["key"] == "radio"), 0),
            "safety": next((module["score"] for module in modules if module["key"] == "scam-shield"), 0),
            "marketplace": next((module["score"] for module in modules if module["key"] == "marketplace"), 0),
            "infrastructure": infrastructure_score,
            "network": next((module["score"] for module in modules if module["key"] == "messenger"), 0),
            "user_experience": performance_index,
        },
        "modules": modules,
        "network_map": [
            {"from": "AI", "to": "Feed", "state": "learning"},
            {"from": "Messenger", "to": "Notifications", "state": "delivery"},
            {"from": "Creator", "to": "Media", "state": "processing"},
            {"from": "Marketplace", "to": "Economy", "state": "commerce"},
            {"from": "Scam Shield", "to": "Moderation", "state": "protection"},
            {"from": "Ads", "to": "Feed", "state": "sponsored"},
        ],
        "orchestrator": {
            "cpu": "managed by platform runtime",
            "memory": "managed by platform runtime",
            "database": "ready" if external["database"] else "offline",
            "workers": "enabled" if command_center_diag.get("enabled") else "fallback",
            "queue": "attention" if failed_jobs else "stable",
            "cache": "ready" if external["redis"] else "fallback",
            "cloudflare": "configured" if external["r2"] else "not configured",
            "railway": "runtime online" if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_SERVICE_ID") else "local/runtime unknown",
            "stripe": "configured" if external["stripe"] else "not configured",
            "brevo": "configured" if external["brevo"] else "not configured",
            "ai_provider": "configured" if external["openai"] else "disabled-safe",
        },
        "prediction": {
            "expected_load": "normal" if queued_jobs < 100 else "queue growth possible",
            "possible_failures": "delivery provider gaps" if failed_jobs else "none detected from aggregate checks",
            "queue_growth": queued_jobs,
            "latency_spike_risk": "low" if _safe_int(db_diag.get("latency_ms"), 0) < 250 else "review database latency",
            "storage_growth": "monitor media uploads",
            "recovery_estimate": "immediate fallback available" if performance_index >= 70 else "manual review recommended",
        },
        "timeline": timeline,
        "quick_actions": quick_actions,
        "privacy": {
            "secrets_visible": False,
            "device_secret_values_visible": False,
            "database_endpoint_visible": False,
            "private_messages_visible": False,
            "provider_credentials_visible": False,
        },
    }


def state_for_widget(system_state: dict[str, Any], widget_key: str) -> dict[str, Any] | None:
    mapping = {
        "feed_status": "feed",
        "messenger_status": "messenger",
        "live_status": "live",
        "radio_status": "radio",
        "marketplace_status": "marketplace",
        "notifications_status": "notifications",
        "ai_status": "ai",
        "scam_shield_status": "scam-shield",
        "ads_status": "advertising",
        "creator_studio_status": "creator",
    }
    key = mapping.get(widget_key)
    if not key:
        return None
    module = next((item for item in system_state.get("modules") or [] if item.get("key") == key), None)
    if not module:
        return None
    return {
        "state": module.get("state") or "READY",
        "cta_label": "Review System",
        "route": module.get("route") or "/dashboard/system",
        "count": module.get("signal_count") or 0,
        "count_display": f"{int(module.get('score') or 0)}%",
        "detail": module.get("recommendation") or "",
    }
