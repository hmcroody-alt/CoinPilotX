"""PulseSoc reliability helpers for health checks and provider safety snapshots."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from services import db as db_service


PROVIDER_REQUIREMENTS = {
    "brevo_email": ("BREVO_API_KEY", "BREVO_SENDER_EMAIL"),
    "brevo_sms": ("BREVO_API_KEY", "BREVO_SMS_SENDER"),
    "stripe": ("STRIPE_SECRET_KEY",),
    "livekit": ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"),
    "web_push": ("WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_PRIVATE_KEY", "WEB_PUSH_SUBJECT"),
    "fcm": ("FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY"),
    "apns": ("APNS_TEAM_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY", "APNS_BUNDLE_ID"),
    "crypto_market": ("COINGECKO_API_KEY", "COINMARKETCAP_API_KEY"),
    "ai": ("OPENAI_API_KEY", "PULSE_AI_ROUTER_URL"),
    "r2_media": ("R2_BUCKET_NAME", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT_URL"),
}


def _configured(keys: tuple[str, ...]) -> tuple[bool, list[str]]:
    missing = [key for key in keys if not os.getenv(key, "").strip()]
    return not missing, missing


def provider_config_snapshot() -> dict[str, dict[str, Any]]:
    """Return config readiness without touching external providers or exposing secrets."""
    snapshot: dict[str, dict[str, Any]] = {}
    for provider, keys in PROVIDER_REQUIREMENTS.items():
        configured, missing = _configured(keys)
        snapshot[provider] = {
            "configured": configured,
            "status": "ready" if configured else "config_missing",
            "missing": missing,
            "critical_to_startup": False,
            "failure_policy": "skip_or_retry_safely",
        }
    return snapshot


def readiness_snapshot() -> dict[str, Any]:
    db_health = db_service.health_check()
    return {
        "ok": bool(db_health.get("connected")),
        "status": "ready" if db_health.get("connected") else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": {
            "connected": bool(db_health.get("connected")),
            "engine": db_health.get("db_engine"),
            "latency_ms": db_health.get("latency_ms"),
            "error": db_health.get("error") or "",
        },
        "optional_providers_block_startup": False,
    }


def deep_health_snapshot() -> dict[str, Any]:
    readiness = readiness_snapshot()
    return {
        **readiness,
        "providers": provider_config_snapshot(),
        "public_health_policy": {
            "live_endpoint": "process_only",
            "ready_endpoint": "database_only",
            "optional_provider_failures": "do_not_mark_app_down",
        },
    }
