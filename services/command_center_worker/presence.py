"""Presence persistence for the PulseSoc Command Center worker."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .redis_manager import safe_delete, safe_get, safe_scan, safe_set

try:
    from sqlalchemy import create_engine, text
except ModuleNotFoundError:  # pragma: no cover - depends on deployment image.
    create_engine = None
    text = None


LOGGER = logging.getLogger(__name__)
VALID_STATUSES = {"online", "away", "offline"}
AWAY_AFTER_MINUTES = 5
OFFLINE_AFTER_MINUTES = 15
PRESENCE_TTL_SECONDS = 20 * 60


class PresenceValidationError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def normalize_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value not in VALID_STATUSES:
        raise PresenceValidationError("invalid_status")
    return value


def validate_user_id(user_id: Any) -> int:
    try:
        value = int(user_id)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise PresenceValidationError("invalid_user_id")
    return value


def _sqlite_path(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return str(Path(database_url.removeprefix("sqlite:///")).expanduser())
    if database_url.startswith("sqlite://"):
        return database_url.removeprefix("sqlite://")
    return database_url


def _database_url() -> str:
    return load_config().database_url or "sqlite:///coinpilotx.db"


def _connect_sqlite(database_url: str):
    conn = sqlite3.connect(_sqlite_path(database_url), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _connect():
    database_url = _database_url()
    if database_url.startswith("sqlite:"):
        return "sqlite", _connect_sqlite(database_url)
    if create_engine is None or text is None:
        raise RuntimeError("sqlalchemy_unavailable")
    engine = create_engine(database_url, pool_pre_ping=True, pool_size=1, max_overflow=0)
    return "sqlalchemy", engine


def _execute(conn, sql: str, params: tuple = ()):
    if hasattr(conn, "execute") and conn.__class__.__name__ == "Engine":
        raise RuntimeError("use_engine_context")
    return conn.execute(sql, params)


def ensure_presence_schema(conn=None) -> bool:
    own_connection = conn is None
    connection_kind = "sqlite"
    engine = None
    if own_connection:
        connection_kind, conn = _connect()
    if connection_kind == "sqlalchemy":
        engine = conn
        try:
            with engine.begin() as db_conn:
                db_conn.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS user_presence (
                        user_id INTEGER PRIMARY KEY,
                        status TEXT NOT NULL DEFAULT 'offline',
                        last_seen_at TEXT,
                        last_active_at TEXT,
                        source TEXT,
                        device_label TEXT,
                        updated_at TEXT
                    )
                    """
                ))
                db_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_presence_user_id ON user_presence(user_id)"))
                db_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_presence_status ON user_presence(status, updated_at)"))
            return True
        finally:
            if engine is not None:
                engine.dispose()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_presence (
                user_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'offline',
                last_seen_at TEXT,
                last_active_at TEXT,
                source TEXT,
                device_label TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_presence_user_id ON user_presence(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_presence_status ON user_presence(status, updated_at)")
        if own_connection:
            conn.commit()
        return True
    finally:
        if own_connection:
            conn.close()


def _row_to_presence(row: Any, user_id: int) -> dict:
    if not row:
        return {
            "user_id": int(user_id),
            "status": "offline",
            "last_seen_at": "",
            "last_active_at": "",
            "source": "",
            "device_label": "",
            "updated_at": "",
        }
    item = dict(row)
    return {
        "user_id": int(item.get("user_id") or user_id),
        "status": item.get("status") or "offline",
        "last_seen_at": item.get("last_seen_at") or "",
        "last_active_at": item.get("last_active_at") or "",
        "source": item.get("source") or "",
        "device_label": item.get("device_label") or "",
        "updated_at": item.get("updated_at") or "",
    }


def _presence_key(user_id: int) -> str:
    return f"presence:user:{int(user_id)}"


def _presence_from_cache(user_id: int) -> dict | None:
    cached = safe_get(_presence_key(user_id))
    if not isinstance(cached, dict):
        return None
    status = str(cached.get("status") or "").lower()
    if status not in VALID_STATUSES:
        return None
    return {
        "user_id": int(cached.get("user_id") or user_id),
        "status": status,
        "last_seen_at": cached.get("last_seen_at") or cached.get("last_seen") or "",
        "last_active_at": cached.get("last_active_at") or "",
        "source": cached.get("source") or "",
        "device_label": cached.get("device_label") or "",
        "updated_at": cached.get("updated_at") or "",
        "cache": "redis",
    }


def _cache_presence(presence: dict) -> bool:
    user_id = int(presence.get("user_id") or 0)
    if user_id <= 0:
        return False
    payload = {
        "user_id": user_id,
        "status": presence.get("status") or "offline",
        "last_seen": presence.get("last_seen_at") or "",
        "last_seen_at": presence.get("last_seen_at") or "",
        "last_active_at": presence.get("last_active_at") or "",
        "source": presence.get("source") or "",
        "device_label": presence.get("device_label") or "",
        "updated_at": presence.get("updated_at") or "",
    }
    return safe_set(_presence_key(user_id), payload, ttl_seconds=PRESENCE_TTL_SECONDS)


def update_presence(user_id, status, source: str = "", device_label: str = "") -> dict:
    normalized_user_id = validate_user_id(user_id)
    normalized_status = normalize_status(status)
    now = iso_now()
    last_seen_at = now
    last_active_at = now if normalized_status in {"online", "away"} else ""
    source = str(source or "")[:80]
    device_label = str(device_label or "")[:120]
    cached_presence = {
        "user_id": normalized_user_id,
        "status": normalized_status,
        "last_seen_at": last_seen_at,
        "last_active_at": last_active_at,
        "source": source,
        "device_label": device_label,
        "updated_at": now,
    }
    _cache_presence(cached_presence)
    connection_kind, conn = _connect()
    if connection_kind == "sqlalchemy":
        engine = conn
        try:
            with engine.begin() as db_conn:
                ensure_presence_schema()
                db_conn.execute(
                    text(
                        """
                        INSERT INTO user_presence (user_id, status, last_seen_at, last_active_at, source, device_label, updated_at)
                        VALUES (:user_id, :status, :last_seen_at, :last_active_at, :source, :device_label, :updated_at)
                        ON CONFLICT (user_id) DO UPDATE SET
                            status=EXCLUDED.status,
                            last_seen_at=EXCLUDED.last_seen_at,
                            last_active_at=COALESCE(NULLIF(EXCLUDED.last_active_at, ''), user_presence.last_active_at),
                            source=EXCLUDED.source,
                            device_label=EXCLUDED.device_label,
                            updated_at=EXCLUDED.updated_at
                        """
                    ),
                    {
                        "user_id": normalized_user_id,
                        "status": normalized_status,
                        "last_seen_at": last_seen_at,
                        "last_active_at": last_active_at,
                        "source": source,
                        "device_label": device_label,
                        "updated_at": now,
                    },
                )
            return get_presence(normalized_user_id)
        finally:
            engine.dispose()
    try:
        ensure_presence_schema(conn)
        conn.execute(
            """
            INSERT INTO user_presence (user_id, status, last_seen_at, last_active_at, source, device_label, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                status=excluded.status,
                last_seen_at=excluded.last_seen_at,
                last_active_at=COALESCE(NULLIF(excluded.last_active_at, ''), user_presence.last_active_at),
                source=excluded.source,
                device_label=excluded.device_label,
                updated_at=excluded.updated_at
            """,
            (normalized_user_id, normalized_status, last_seen_at, last_active_at, source, device_label, now),
        )
        conn.commit()
        return get_presence(normalized_user_id)
    finally:
        conn.close()


def get_presence(user_id) -> dict:
    normalized_user_id = validate_user_id(user_id)
    cached = _presence_from_cache(normalized_user_id)
    if cached:
        return cached
    connection_kind, conn = _connect()
    if connection_kind == "sqlalchemy":
        engine = conn
        try:
            with engine.begin() as db_conn:
                ensure_presence_schema()
                row = db_conn.execute(text("SELECT * FROM user_presence WHERE user_id=:user_id LIMIT 1"), {"user_id": normalized_user_id}).mappings().first()
                return _row_to_presence(row, normalized_user_id)
        finally:
            engine.dispose()
    try:
        ensure_presence_schema(conn)
        row = conn.execute("SELECT * FROM user_presence WHERE user_id=? LIMIT 1", (normalized_user_id,)).fetchone()
        return _row_to_presence(row, normalized_user_id)
    finally:
        conn.close()


def mark_user_online(user_id) -> dict:
    return update_presence(user_id, "online", "worker", "")


def mark_user_away(user_id) -> dict:
    return update_presence(user_id, "away", "worker", "")


def mark_user_offline(user_id) -> dict:
    return update_presence(user_id, "offline", "worker", "")


def _cleanup_status_for(last_active_at: str, current_status: str, now_dt: datetime) -> str:
    parsed = _parse_dt(last_active_at)
    if not parsed:
        return "offline"
    age = now_dt - parsed
    if age >= timedelta(minutes=OFFLINE_AFTER_MINUTES):
        return "offline"
    if age >= timedelta(minutes=AWAY_AFTER_MINUTES) and current_status == "online":
        return "away"
    return current_status


def cleanup_stale_presence() -> dict:
    now_dt = utc_now()
    now = iso_now()
    changed = {"away": 0, "offline": 0}
    for key in safe_scan("presence:user:*", limit=1000):
        cached = safe_get(key)
        if not isinstance(cached, dict):
            continue
        user_id = int(cached.get("user_id") or str(key).rsplit(":", 1)[-1] or 0)
        current_status = str(cached.get("status") or "offline")
        next_status = _cleanup_status_for(cached.get("last_active_at") or cached.get("last_seen_at") or "", current_status, now_dt)
        if next_status != current_status and user_id > 0:
            cached["status"] = next_status
            cached["updated_at"] = now
            safe_set(_presence_key(user_id), cached, ttl_seconds=PRESENCE_TTL_SECONDS)
            changed[next_status] = changed.get(next_status, 0) + 1
        if next_status == "offline" and user_id > 0:
            safe_delete(_presence_key(user_id))
    connection_kind, conn = _connect()
    if connection_kind == "sqlalchemy":
        engine = conn
        try:
            with engine.begin() as db_conn:
                ensure_presence_schema()
                rows = db_conn.execute(text("SELECT user_id, status, last_active_at FROM user_presence WHERE status IN ('online', 'away')")).mappings().all()
                for row in rows:
                    next_status = _cleanup_status_for(row.get("last_active_at") or "", row.get("status") or "offline", now_dt)
                    if next_status != row.get("status"):
                        db_conn.execute(
                            text("UPDATE user_presence SET status=:status, updated_at=:updated_at WHERE user_id=:user_id"),
                            {"status": next_status, "updated_at": now, "user_id": int(row.get("user_id") or 0)},
                        )
                        changed[next_status] = changed.get(next_status, 0) + 1
            return {"ok": True, "changed": changed}
        finally:
            engine.dispose()
    try:
        ensure_presence_schema(conn)
        rows = conn.execute("SELECT user_id, status, last_active_at FROM user_presence WHERE status IN ('online', 'away')").fetchall()
        for row in rows:
            item = dict(row)
            next_status = _cleanup_status_for(item.get("last_active_at") or "", item.get("status") or "offline", now_dt)
            if next_status != item.get("status"):
                conn.execute(
                    "UPDATE user_presence SET status=?, updated_at=? WHERE user_id=?",
                    (next_status, now, int(item.get("user_id") or 0)),
                )
                changed[next_status] = changed.get(next_status, 0) + 1
        conn.commit()
        return {"ok": True, "changed": changed}
    finally:
        conn.close()
