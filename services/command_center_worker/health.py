"""Health checks for the PulseSoc Command Center worker skeleton."""

from __future__ import annotations

import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import WorkerConfig
from .redis_manager import redis_health
from services import native_push_readiness

try:
    from sqlalchemy import create_engine, text
except ModuleNotFoundError:  # pragma: no cover - depends on deployment image.
    create_engine = None
    text = None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sqlite_path(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        raw_path = database_url.removeprefix("sqlite:///")
        return str(Path(raw_path).expanduser())
    if database_url.startswith("sqlite://"):
        return database_url.removeprefix("sqlite://")
    return database_url


def check_database(config: WorkerConfig) -> bool:
    if not config.database_url:
        return False
    try:
        if config.database_url.startswith("sqlite:"):
            conn = sqlite3.connect(_sqlite_path(config.database_url), timeout=2)
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()
            return True
        if create_engine is None or text is None:
            return False
        engine = create_engine(config.database_url, pool_pre_ping=True, pool_size=1, max_overflow=0)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
        return True
    except Exception:
        return False


def health_payload(config: WorkerConfig) -> dict:
    redis = redis_health()
    native_push = native_push_readiness.native_push_readiness(initialize_admin=False)
    web_push = {
        "vapid_public_key_loaded": bool(os.getenv("VAPID_PUBLIC_KEY")),
        "vapid_private_key_loaded": bool(os.getenv("VAPID_PRIVATE_KEY")),
        "ready": bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY")),
    }
    expo_push = {
        "server_provider": "expo",
        "sound_configured": bool(os.getenv("PUSH_DEFAULT_SOUND")),
        "badge_enabled": str(os.getenv("PUSH_BADGE_ENABLED", "1")).lower() not in {"0", "false", "off", "no"},
        "ready": True,
    }
    return {
        "service_name": config.service_name,
        "service_role": config.service_role,
        "worker_enabled": config.worker_enabled,
        "database_ok": check_database(config),
        "redis_ok": redis.get("redis_ok"),
        "redis_latency_ms": redis.get("redis_latency_ms"),
        "redis_enabled": bool(redis.get("redis_enabled")),
        "redis_configured": config.redis_configured,
        "internal_auth_configured": config.internal_token_configured,
        "push_readiness": {
            "native": native_push,
            "web_push": web_push,
            "expo": expo_push,
        },
        "heartbeat_seconds": config.heartbeat_seconds,
        "timestamp": utc_timestamp(),
        "version": config.version,
    }
