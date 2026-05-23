"""Optional Redis-backed cache and presence helpers.

Local development and deployments without REDIS_URL continue to work with a
small in-memory TTL cache. Redis becomes an accelerator, not a hard dependency.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import suppress

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover - Redis is optional.
    redis = None


_MEMORY = {}
_LOCK = threading.RLock()
_CLIENT = None


def redis_client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    url = os.getenv("REDIS_URL", "").strip()
    if not url or redis is None:
        return None
    try:
        _CLIENT = redis.Redis.from_url(url, decode_responses=True, socket_timeout=1.5, socket_connect_timeout=1.5)
        _CLIENT.ping()
        return _CLIENT
    except Exception:
        _CLIENT = None
        return None


def cache_status():
    client = redis_client()
    if client:
        with suppress(Exception):
            info = client.info(section="memory")
            return {"provider": "redis", "ok": True, "used_memory_human": info.get("used_memory_human")}
    return {"provider": "memory", "ok": True, "items": len(_MEMORY)}


def _serialize(value):
    return json.dumps(value, default=str)


def _deserialize(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def cache_get(key, default=None):
    key = str(key)
    client = redis_client()
    if client:
        try:
            value = client.get(key)
            return default if value is None else _deserialize(value)
        except Exception:
            pass
    now = time.time()
    with _LOCK:
        item = _MEMORY.get(key)
        if not item:
            return default
        expires_at, value = item
        if expires_at and expires_at < now:
            _MEMORY.pop(key, None)
            return default
        return value


def cache_set(key, value, ttl_seconds=60):
    key = str(key)
    ttl_seconds = max(1, int(ttl_seconds or 60))
    client = redis_client()
    if client:
        try:
            client.setex(key, ttl_seconds, _serialize(value))
            return True
        except Exception:
            pass
    with _LOCK:
        _MEMORY[key] = (time.time() + ttl_seconds, value)
    return True


def cache_delete(key):
    key = str(key)
    client = redis_client()
    if client:
        with suppress(Exception):
            client.delete(key)
    with _LOCK:
        _MEMORY.pop(key, None)
    return True


def cache_remember(key, ttl_seconds, producer):
    cached = cache_get(key, None)
    if cached is not None:
        return cached
    value = producer()
    cache_set(key, value, ttl_seconds)
    return value


def increment_counter(key, amount=1, ttl_seconds=60):
    key = str(key)
    client = redis_client()
    if client:
        try:
            value = client.incrby(key, int(amount or 1))
            if ttl_seconds:
                client.expire(key, int(ttl_seconds))
            return int(value)
        except Exception:
            pass
    with _LOCK:
        current = int(cache_get(key, 0) or 0) + int(amount or 1)
        _MEMORY[key] = (time.time() + int(ttl_seconds or 60), current)
        return current


def set_presence(scope, user_id, ttl_seconds=75, metadata=None):
    key = f"presence:{scope}:{int(user_id or 0)}"
    payload = {"user_id": int(user_id or 0), "last_seen": time.time(), "metadata": metadata or {}}
    cache_set(key, payload, ttl_seconds)
    return payload


def get_presence(scope, user_ids=None):
    ids = [int(x) for x in (user_ids or []) if int(x or 0)]
    if not ids:
        return []
    results = []
    for user_id in ids:
        item = cache_get(f"presence:{scope}:{user_id}")
        if item:
            results.append(item)
    return results
