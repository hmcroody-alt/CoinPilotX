#!/usr/bin/env python3
"""Audit the PulseSoc Command Center Redis integration layer."""

from __future__ import annotations

import json
import os
import py_compile
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_TOKEN = "command-center-redis-audit-token"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "bot.py",
        ROOT / "services" / "command_center_worker" / "redis_manager.py",
        ROOT / "services" / "command_center_worker" / "health.py",
        ROOT / "services" / "command_center_worker" / "presence.py",
        ROOT / "services" / "command_center_worker" / "messaging.py",
        ROOT / "services" / "command_center_worker" / "notifications.py",
        ROOT / "services" / "command_center_worker" / "realtime_transport.py",
        ROOT / "services" / "command_center_worker" / "app.py",
        Path(__file__).resolve(),
    ]
    compiled = []
    for target in targets:
        py_compile.compile(str(target), doraise=True)
        compiled.append(str(target.relative_to(ROOT)))
    return compiled


def configure_env(db_path: Path, redis_url: str = "") -> None:
    os.environ["PULSESOC_SERVICE_NAME"] = "command-center-worker"
    os.environ["PULSESOC_SERVICE_ROLE"] = "worker"
    os.environ["COMMAND_CENTER_WORKER_ENABLED"] = "true"
    os.environ["COMMAND_CENTER_INTERNAL_TOKEN"] = AUDIT_TOKEN
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    if redis_url:
        os.environ["REDIS_URL"] = redis_url
    else:
        os.environ.pop("REDIS_URL", None)


def prepare_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comm_v2_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            membership_state TEXT DEFAULT 'active',
            left_at TEXT,
            unread_count INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at, unread_count) VALUES (77, 55, 'active', '', 3)"
    )
    conn.execute(
        "INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at, unread_count) VALUES (77, 44, 'active', '', 0)"
    )
    conn.commit()
    conn.close()


def audit_missing_redis_fallback(db_path: Path) -> dict:
    configure_env(db_path, "")
    sys.path.insert(0, str(ROOT))
    from services.command_center_worker import redis_manager
    from services.command_center_worker.config import load_config
    from services.command_center_worker.health import health_payload
    from services.command_center_worker.presence import update_presence

    redis_manager.reset_memory()
    health = health_payload(load_config())
    expect(health.get("redis_enabled") is False, "missing Redis should be disabled")
    expect(health.get("redis_ok") is False, "missing Redis should not report ok")
    expect(redis_manager.safe_get("anything", "fallback") == "fallback", "safe_get should fall back when Redis missing")
    expect(redis_manager.safe_set("anything", {"ok": True}) is False, "safe_set should not write when Redis missing")
    presence = update_presence(55, "online", "audit", "desktop")
    expect(presence.get("status") == "online", "presence should still persist without Redis")
    rate = redis_manager.rate_limit_login("audit@example.invalid")
    expect(rate.get("allowed") is True and rate.get("redis") is False, "rate limit should fail open without Redis")
    return {"health": health, "presence": presence, "rate": rate}


def audit_memory_redis_layer(db_path: Path) -> dict:
    configure_env(db_path, "memory://")
    sys.path.insert(0, str(ROOT))

    from services.command_center_worker import redis_manager
    from services.command_center_worker.config import load_config
    from services.command_center_worker.health import health_payload
    from services.command_center_worker.messaging import get_conversation_state, get_unread_counts, set_typing
    from services.command_center_worker.notifications import accept_notification_event, get_recent_notifications, get_unread_count
    from services.command_center_worker.presence import get_presence, update_presence
    from services.command_center_worker.realtime_transport import connect_user, poll_user_events, publish_event

    redis_manager.reset_memory()
    health = health_payload(load_config())
    expect(health.get("redis_enabled") is True, "memory Redis should be enabled")
    expect(health.get("redis_ok") is True, "memory Redis should be ok")
    expect(isinstance(health.get("redis_latency_ms"), (int, float)), "Redis latency should be numeric when enabled")

    presence = update_presence(55, "online", "audit", "desktop")
    cached_presence = redis_manager.safe_get("presence:user:55")
    expect(isinstance(cached_presence, dict), "presence cache missing")
    expect(cached_presence.get("status") == "online", "presence cache status mismatch")
    expect(get_presence(55).get("cache") == "redis", "presence should read Redis first")

    typing = set_typing(77, 44, {"display_name": "Audit User", "token": "must-not-store"})
    expect(typing.get("accepted") is True, "typing event should be accepted")
    typing_key = redis_manager.safe_get("typing:77:44")
    expect(isinstance(typing_key, dict) and typing_key.get("is_typing") is True, "typing TTL key missing")
    state = get_conversation_state(77, viewer_user_id=55)
    expect(state.get("typing") and state["typing"][0].get("source") == "redis", "conversation typing should come from Redis")

    unread = get_unread_counts(55)
    expect(unread.get("total_unread") == 3, "unread DB total mismatch")
    unread_cached = redis_manager.safe_get("unread:user:55")
    expect(isinstance(unread_cached, dict) and unread_cached.get("total_unread") == 3, "unread cache missing")

    note = accept_notification_event(55, "status_reaction", "Audit notification", "Body", actor_id=44, payload={"safe": True})
    expect(note.get("accepted") is True, "notification event should be accepted")
    count = get_unread_count(55)
    recent = get_recent_notifications(55)
    expect(count.get("unread_count") >= 1, "notification unread count missing")
    expect(recent.get("notifications"), "notification recent list missing")
    expect(isinstance(redis_manager.safe_get("notifications:user:55"), dict), "notification cache missing")

    connect = connect_user(55, "redis-audit-session", "desktop", [77])
    expect(connect.get("connected") is True, "realtime connection not registered")
    expect(isinstance(redis_manager.safe_get("connection:55:redis-audit-session"), dict), "connection registry missing")
    event = publish_event("message_created", {"conversation_id": 77, "message_id": 901}, conversation_id=77, actor_id=44)
    expect(event.get("accepted") is True, "realtime event not accepted")
    polled = poll_user_events(55, after_id=0)
    expect(polled.get("events"), "Redis realtime event fanout missing")

    first_login = redis_manager.rate_limit_login("redis-audit")
    for _ in range(8):
        last_login = redis_manager.rate_limit_login("redis-audit")
    expect(first_login.get("redis") is True, "Redis-backed rate limit did not use Redis")
    expect(last_login.get("allowed") is False, "Redis-backed rate limit did not enforce window")

    redis_manager.safe_set("ttl:audit", "yes", ttl_seconds=1)
    expect(redis_manager.safe_get("ttl:audit") == "yes", "TTL value not stored")
    time.sleep(1.1)
    expect(redis_manager.safe_get("ttl:audit") is None, "TTL value did not expire")

    serialized = json.dumps(
        {
            "health": health,
            "presence": cached_presence,
            "typing": typing_key,
            "unread": unread_cached,
            "recent": recent,
            "connection": redis_manager.safe_get("connection:55:redis-audit-session"),
            "polled": polled,
        },
        sort_keys=True,
    )
    expect(AUDIT_TOKEN not in serialized, "internal token leaked into Redis audit payloads")
    expect("must-not-store" not in serialized, "sensitive typing payload leaked into Redis")
    expect(str(db_path) not in serialized, "database path leaked into Redis payloads")

    return {
        "health": health,
        "presence_cache": cached_presence,
        "typing_cache": typing_key,
        "unread_cache": unread_cached,
        "notification_count": count,
        "connection": connect,
        "polled_events": len(polled.get("events") or []),
        "rate_limited": last_login,
    }


def main() -> int:
    compiled = compile_targets()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "redis_audit.db"
        prepare_db(db_path)
        missing = audit_missing_redis_fallback(db_path)
        memory = audit_memory_redis_layer(db_path)
    report = {
        "ok": True,
        "compiled": compiled,
        "redis_missing_fallback_safe": missing["health"].get("redis_enabled") is False,
        "redis_health_ok": memory["health"].get("redis_ok") is True,
        "presence_cache": memory["presence_cache"].get("status") == "online",
        "typing_ttl": memory["typing_cache"].get("is_typing") is True,
        "unread_cache": memory["unread_cache"].get("total_unread") == 3,
        "notification_cache": memory["notification_count"].get("unread_count") >= 1,
        "realtime_registry": memory["connection"].get("connected") is True,
        "event_fanout": memory["polled_events"] >= 1,
        "rate_limit_storage": memory["rate_limited"].get("allowed") is False,
        "no_secrets": True,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
