"""PulseSoc reliability helpers for health checks and provider safety snapshots."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from services import db as db_service


# A requirement is either a variable name, or a tuple of interchangeable names of
# which any one satisfies it. Aliases are not cosmetic here: services/media_storage.py
# resolves the bucket as `R2_BUCKET or S3_BUCKET` and the credentials as
# `R2_* or AWS_*`, so a readiness row that insists on the R2_ spelling reports
# "config_missing" about storage that is demonstrably working.
#
# `R2_BUCKET_NAME` used to be listed here and in
# services/backend_management_registry.py. No code in this repository reads that
# name - it was never anything but a permanently unmet requirement, which is why
# the R2 provider row could not report ready no matter how the environment was set.
PROVIDER_REQUIREMENTS: dict[str, tuple[Any, ...]] = {
    "brevo_email": ("BREVO_API_KEY", "BREVO_SENDER_EMAIL"),
    "brevo_sms": ("BREVO_API_KEY", "BREVO_SMS_SENDER"),
    "stripe": ("STRIPE_SECRET_KEY",),
    "livekit": ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"),
    "web_push": ("WEB_PUSH_PUBLIC_KEY", "WEB_PUSH_PRIVATE_KEY", "WEB_PUSH_SUBJECT"),
    "fcm": ("FCM_PROJECT_ID", "FCM_CLIENT_EMAIL", "FCM_PRIVATE_KEY"),
    "apns": ("APNS_TEAM_ID", "APNS_KEY_ID", "APNS_PRIVATE_KEY", "APNS_BUNDLE_ID"),
    # CoinGecko serves this application's endpoints without a key
    # (services/market_data.py treats COINGECKO_API_KEY as an optional upgrade),
    # and CoinMarketCap is an independent alternative rather than a co-requirement.
    # Demanding both made a working market feed report config_missing.
    "crypto_market": (("COINGECKO_API_KEY", "COINMARKETCAP_API_KEY"),),
    # undx_router.PROVIDERS routes to whichever of these is configured and falls
    # back across them, so any one key makes the AI layer functional. The previous
    # entry also required PULSE_AI_ROUTER_URL, a name that appears nowhere else in
    # the repository - the AI row could not report ready under any configuration.
    "ai": (
        (
            "OPENAI_API_KEY",
            "CLAUDE_AI_API",
            "Gemini_AI_API",
            "GEMINI_AI_API",
            "DEEPSEEK_AI_API",
            "GROQ_AI_API",
        ),
    ),
    "r2_media": (
        ("R2_BUCKET", "S3_BUCKET"),
        ("R2_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
        ("R2_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
        ("R2_ENDPOINT_URL", "R2_ENDPOINT", "R2_ACCOUNT_ID", "S3_ENDPOINT_URL"),
        "R2_PUBLIC_BASE_URL",
    ),
}


def _requirement_met(requirement: Any) -> bool:
    names = (requirement,) if isinstance(requirement, str) else tuple(requirement)
    return any(os.getenv(name, "").strip() for name in names)


def _requirement_label(requirement: Any) -> str:
    if isinstance(requirement, str):
        return requirement
    return " or ".join(requirement)


def _configured(keys: tuple[Any, ...]) -> tuple[bool, list[str]]:
    missing = [_requirement_label(req) for req in keys if not _requirement_met(req)]
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
