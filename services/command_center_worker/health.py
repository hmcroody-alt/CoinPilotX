"""Health checks for the PulseSoc Command Center worker skeleton."""

from __future__ import annotations

import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .config import WorkerConfig

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


def check_redis(config: WorkerConfig) -> bool | None:
    if not config.redis_url:
        return None
    try:
        parsed = urlparse(config.redis_url)
        host = parsed.hostname
        port = parsed.port or 6379
        if not host:
            return False
        with socket.create_connection((host, port), timeout=1.5) as sock:
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            response = sock.recv(64)
        return b"PONG" in response
    except Exception:
        return False


def health_payload(config: WorkerConfig) -> dict:
    return {
        "service_name": config.service_name,
        "service_role": config.service_role,
        "worker_enabled": config.worker_enabled,
        "database_ok": check_database(config),
        "redis_ok": check_redis(config),
        "redis_configured": config.redis_configured,
        "internal_auth_configured": config.internal_token_configured,
        "heartbeat_seconds": config.heartbeat_seconds,
        "timestamp": utc_timestamp(),
        "version": config.version,
    }
