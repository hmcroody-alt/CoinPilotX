"""Safe Redis integration helpers for Command Center worker services."""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
from collections import defaultdict
from typing import Any, Iterator

try:
    import redis
except ModuleNotFoundError:  # pragma: no cover - depends on deployment image.
    redis = None


_redis_client = None
_redis_url_seen = ""
_memory_lock = threading.RLock()
_memory_store: dict[str, tuple[Any, float | None]] = {}
_memory_channels: dict[str, list[Any]] = defaultdict(list)


class MemoryPubSub:
    def __init__(self):
        self.channels: list[str] = []

    def subscribe(self, *channels: str) -> None:
        self.channels.extend(str(channel) for channel in channels if channel)

    def listen(self) -> Iterator[dict[str, Any]]:
        for channel in list(self.channels):
            for item in list(_memory_channels.get(channel, [])):
                yield {"type": "message", "channel": channel, "data": item}

    def close(self) -> None:
        self.channels = []


class MemoryRedis:
    def ping(self) -> bool:
        return True

    def get(self, key: str):
        with _memory_lock:
            item = _memory_store.get(key)
            if not item:
                return None
            value, expires_at = item
            if expires_at and expires_at <= time.time():
                _memory_store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ex: int | None = None):
        expires_at = time.time() + int(ex) if ex else None
        with _memory_lock:
            _memory_store[key] = (value, expires_at)
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        with _memory_lock:
            for key in keys:
                if key in _memory_store:
                    _memory_store.pop(key, None)
                    removed += 1
        return removed

    def publish(self, channel: str, message: Any) -> int:
        with _memory_lock:
            _memory_channels[str(channel)].append(message)
        return 1

    def pubsub(self):
        return MemoryPubSub()

    def scan_iter(self, match: str = "*", count: int = 100):
        with _memory_lock:
            keys = list(_memory_store.keys())
        for key in keys:
            self.get(key)
            if fnmatch.fnmatch(key, match):
                yield key

    def incr(self, key: str):
        value = self.get(key)
        try:
            next_value = int(value or 0) + 1
        except (TypeError, ValueError):
            next_value = 1
        self.set(key, str(next_value))
        return next_value

    def expire(self, key: str, seconds: int):
        with _memory_lock:
            if key not in _memory_store:
                return False
            value, _expires_at = _memory_store[key]
            _memory_store[key] = (value, time.time() + int(seconds))
        return True


def redis_url() -> str:
    return os.getenv("REDIS_URL", "").strip()


def redis_enabled() -> bool:
    return bool(redis_url())


def get_redis():
    global _redis_client, _redis_url_seen
    url = redis_url()
    if not url:
        return None
    if url == "memory://":
        if not isinstance(_redis_client, MemoryRedis):
            _redis_client = MemoryRedis()
            _redis_url_seen = url
        return _redis_client
    if redis is None:
        return None
    if _redis_client is not None and _redis_url_seen == url:
        return _redis_client
    try:
        _redis_client = redis.Redis.from_url(url, socket_connect_timeout=1.0, socket_timeout=1.0, decode_responses=True)
        _redis_url_seen = url
        return _redis_client
    except Exception:
        _redis_client = None
        _redis_url_seen = url
        return None


def redis_health() -> dict[str, Any]:
    if not redis_enabled():
        return {"redis_enabled": False, "redis_ok": False, "redis_latency_ms": None}
    client = get_redis()
    if client is None:
        return {"redis_enabled": True, "redis_ok": False, "redis_latency_ms": None}
    started = time.perf_counter()
    try:
        ok = bool(client.ping())
        latency = round((time.perf_counter() - started) * 1000, 2)
        return {"redis_enabled": True, "redis_ok": ok, "redis_latency_ms": latency}
    except Exception:
        return {"redis_enabled": True, "redis_ok": False, "redis_latency_ms": None}


def _safe_key(key: str) -> str:
    return str(key or "").strip()[:240]


def safe_get(key: str, default: Any = None) -> Any:
    client = get_redis()
    if client is None:
        return default
    try:
        value = client.get(_safe_key(key))
        if value is None:
            return default
        if isinstance(value, bytes):
            value = value.decode("utf-8", "ignore")
        try:
            return json.loads(value)
        except Exception:
            return value
    except Exception:
        return default


def safe_set(key: str, value: Any, ttl_seconds: int | None = None) -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        if isinstance(value, (dict, list, tuple, bool, int, float)) or value is None:
            payload = json.dumps(value, separators=(",", ":"), ensure_ascii=True, default=str)
        else:
            payload = str(value)
        client.set(_safe_key(key), payload, ex=int(ttl_seconds) if ttl_seconds else None)
        return True
    except Exception:
        return False


def safe_delete(*keys: str) -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        client.delete(*[_safe_key(key) for key in keys if key])
        return True
    except Exception:
        return False


def safe_publish(channel: str, message: Any) -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=True, default=str)
        client.publish(_safe_key(channel), payload)
        return True
    except Exception:
        return False


def safe_subscribe(*channels: str):
    client = get_redis()
    if client is None:
        return None
    try:
        pubsub = client.pubsub()
        pubsub.subscribe(*[_safe_key(channel) for channel in channels if channel])
        return pubsub
    except Exception:
        return None


def safe_scan(pattern: str, limit: int = 200) -> list[str]:
    client = get_redis()
    if client is None:
        return []
    keys: list[str] = []
    try:
        for raw_key in client.scan_iter(match=_safe_key(pattern), count=max(10, min(int(limit or 200), 500))):
            key = raw_key.decode("utf-8", "ignore") if isinstance(raw_key, bytes) else str(raw_key)
            keys.append(key)
            if len(keys) >= limit:
                break
    except Exception:
        return []
    return keys


def safe_rate_limit(key: str, limit: int, window_seconds: int) -> dict[str, Any]:
    client = get_redis()
    if client is None:
        return {"ok": True, "allowed": True, "redis": False, "count": 0, "limit": int(limit or 0)}
    safe_key = f"rate:{_safe_key(key)}"
    try:
        count = int(client.incr(safe_key))
        if count == 1:
            client.expire(safe_key, int(window_seconds or 60))
        return {"ok": True, "allowed": count <= int(limit or 1), "redis": True, "count": count, "limit": int(limit or 1)}
    except Exception:
        return {"ok": True, "allowed": True, "redis": False, "count": 0, "limit": int(limit or 0)}


def rate_limit_login(identifier: str) -> dict[str, Any]:
    return safe_rate_limit(f"login:{identifier}", limit=8, window_seconds=60)


def rate_limit_messages(user_id: int) -> dict[str, Any]:
    return safe_rate_limit(f"messages:{int(user_id or 0)}", limit=40, window_seconds=60)


def rate_limit_comments(user_id: int) -> dict[str, Any]:
    return safe_rate_limit(f"comments:{int(user_id or 0)}", limit=30, window_seconds=60)


def rate_limit_dm_spam(user_id: int) -> dict[str, Any]:
    return safe_rate_limit(f"dm_spam:{int(user_id or 0)}", limit=20, window_seconds=300)


def rate_limit_typing(user_id: int, conversation_id: int) -> dict[str, Any]:
    return safe_rate_limit(f"typing:{int(user_id or 0)}:{int(conversation_id or 0)}", limit=1, window_seconds=1)


def reset_memory() -> None:
    global _redis_client, _redis_url_seen
    with _memory_lock:
        _memory_store.clear()
        _memory_channels.clear()
    if redis_url() == "memory://":
        _redis_client = MemoryRedis()
        _redis_url_seen = "memory://"
