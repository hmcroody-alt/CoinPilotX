"""PulseSoc server-authoritative security core.

This module keeps hot-path checks fast and framework-light. It is designed to
sit at the Flask request boundary and enforce zero-trust rules before route
handlers touch high-risk actions.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from . import cache_engine


_RATE_BUCKETS: dict[str, list[float]] = defaultdict(list)
_NONCES: dict[str, float] = {}
_TRUST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

SCRIPT_RE = re.compile(r"<\s*script|javascript:|onerror\s*=|onload\s*=|data:text/html", re.I)
MAX_NONCE_AGE_SECONDS = 120


@dataclass(frozen=True)
class RateRule:
    limit: int
    window_seconds: int
    action: str
    severity: str = "medium"


HIGH_RISK_RATE_RULES: tuple[tuple[str, RateRule], ...] = (
    ("/api/mobile/auth/login", RateRule(10, 300, "auth_login")),
    ("/api/pulse/mobile/auth/login", RateRule(10, 300, "auth_login")),
    ("/api/mobile/auth/register", RateRule(6, 300, "auth_signup")),
    ("/api/pulse/mobile/auth/register", RateRule(6, 300, "auth_signup")),
    ("/api/mobile/auth/recover", RateRule(5, 600, "auth_recover")),
    ("/api/pulse/mobile/auth/recover", RateRule(5, 600, "auth_recover")),
    ("/api/mobile/auth/reset-password", RateRule(5, 600, "auth_reset")),
    ("/api/pulse/mobile/auth/reset-password", RateRule(5, 600, "auth_reset")),
    ("/api/pulse/media/upload", RateRule(18, 300, "media_upload")),
    ("/api/pulse/media/mux/direct-upload", RateRule(12, 300, "mux_direct_upload")),
    ("/api/pulse/reels/create", RateRule(12, 300, "reel_create")),
    ("/api/pulse/posts", RateRule(30, 300, "post_create")),
    ("/api/pulse/live", RateRule(24, 300, "live_action", "high")),
    ("/api/pulse/communications", RateRule(90, 60, "messaging")),
    ("/api/pulse/messages", RateRule(90, 60, "messaging")),
    ("/api/pulse/ads", RateRule(40, 300, "ads_action", "high")),
    ("/api/pulseshell/validate", RateRule(90, 60, "pulseshell_validate")),
    ("/api/create-checkout-session", RateRule(8, 300, "checkout", "high")),
    ("/create-checkout-session", RateRule(8, 300, "checkout", "high")),
)

STRICT_JSON_FIELDS: dict[str, set[str]] = {
    "/api/mobile/auth/login": {"identifier", "email", "username", "password", "remember", "device_id", "device_label", "platform", "source", "preferred_language", "language"},
    "/api/pulse/mobile/auth/login": {"identifier", "email", "username", "password", "remember", "device_id", "device_label", "platform", "source", "preferred_language", "language"},
    "/api/mobile/auth/register": {"full_name", "display_name", "username", "email", "password", "phone", "country", "email_opt_in", "sms_opt_in", "age_confirmed", "device_id", "device_label", "platform", "source", "preferred_language", "language"},
    "/api/pulse/mobile/auth/register": {"full_name", "display_name", "username", "email", "password", "phone", "country", "email_opt_in", "sms_opt_in", "age_confirmed", "device_id", "device_label", "platform", "source", "preferred_language", "language"},
    "/api/mobile/auth/refresh": {"refresh_token", "device_id", "device_label", "platform", "source"},
    "/api/pulse/mobile/auth/refresh": {"refresh_token", "device_id", "device_label", "platform", "source"},
    "/api/mobile/auth/logout": {"refresh_token", "device_id"},
    "/api/pulse/mobile/auth/logout": {"refresh_token", "device_id"},
    "/api/pulseshell/validate": {"module", "action", "request_id", "timestamp", "nonce", "payload"},
}

PULSESHELL_ALLOWED_ACTIONS = {
    "device.getInfo",
    "performance.getMode",
    "performance.setMode",
    "safeArea.getInsets",
    "deepLinks.open",
    "live.startHostSession",
    "push.registerDevice",
    "share.openNativeShareSheet",
    "haptics.impact",
    "permissions.check",
    "permissions.request",
    "camera.requestPermission",
    "microphone.requestPermission",
    "filePicker.open",
}

KILL_SWITCH_ENV = {
    "signup": "PULSESOC_DISABLE_SIGNUP",
    "live": "PULSESOC_DISABLE_LIVE",
    "cohost": "PULSESOC_DISABLE_COHOST",
    "payments": "PULSESOC_FREEZE_PAYMENTS",
    "messaging": "PULSESOC_THROTTLE_MESSAGING",
    "uploads": "PULSESOC_DISABLE_UPLOADS",
}


def _now() -> float:
    return time.time()


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def device_fingerprint(user_agent: str = "", device_id: str = "", salt: str = "") -> str:
    salt = salt or os.getenv("PULSESOC_SECURITY_SALT") or os.getenv("ANALYTICS_SALT") or "pulsesoc-security"
    raw = f"{salt}:{device_id or ''}:{user_agent or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_device_trust(user_id: int, device_hash: str, trust: dict[str, Any], ttl_seconds: int = 60) -> None:
    cache_engine.cache_set(f"pulse-security:trust:{int(user_id or 0)}:{device_hash}", dict(trust or {}), ttl_seconds)
    _TRUST_CACHE[f"{int(user_id or 0)}:{device_hash}"] = (_now() + max(1, int(ttl_seconds or 60)), dict(trust or {}))


def get_device_trust(user_id: int, device_hash: str) -> dict[str, Any]:
    cached = cache_engine.cache_get(f"pulse-security:trust:{int(user_id or 0)}:{device_hash}")
    if isinstance(cached, dict):
        return dict(cached)
    key = f"{int(user_id or 0)}:{device_hash}"
    expires, value = _TRUST_CACHE.get(key, (0, {}))
    if expires < _now():
        _TRUST_CACHE.pop(key, None)
        return {}
    return dict(value)


def rate_rule_for(path: str, method: str) -> RateRule | None:
    if str(method or "").upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    normalized = str(path or "")
    for prefix, rule in HIGH_RISK_RATE_RULES:
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return rule
    if normalized.startswith("/api/"):
        return RateRule(180, 60, "api_mutation", "low")
    return None


def rate_limited(*, path: str, method: str, ip_hash: str, user_id: int = 0, device_hash: str = "") -> dict[str, Any]:
    rule = rate_rule_for(path, method)
    if not rule:
        return {"limited": False}
    now = _now()
    keys = [
        f"ip:{ip_hash}:{rule.action}:{path}",
        f"user:{int(user_id or 0)}:{rule.action}:{path}" if user_id else "",
        f"device:{device_hash}:{rule.action}:{path}" if device_hash else "",
    ]
    for key in [item for item in keys if item]:
        cache_key = f"pulse-security:rate:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
        cached_bucket = cache_engine.cache_get(cache_key, None)
        bucket = cached_bucket if isinstance(cached_bucket, list) else _RATE_BUCKETS.get(key, [])
        bucket = [float(stamp) for stamp in bucket if now - float(stamp) < rule.window_seconds]
        if len(bucket) >= rule.limit:
            _RATE_BUCKETS[key] = bucket
            cache_engine.cache_set(cache_key, bucket, rule.window_seconds)
            return {
                "limited": True,
                "action": rule.action,
                "severity": rule.severity,
                "limit": rule.limit,
                "window_seconds": rule.window_seconds,
                "retry_after": max(1, int(rule.window_seconds - (now - bucket[0])) if bucket else rule.window_seconds),
            }
        bucket.append(now)
        _RATE_BUCKETS[key] = bucket
        cache_engine.cache_set(cache_key, bucket, rule.window_seconds)
    return {"limited": False, "action": rule.action}


def kill_switch_for(path: str) -> dict[str, Any]:
    checks = (
        ("signup", path in {"/signup", "/api/mobile/auth/register", "/api/pulse/mobile/auth/register"}),
        ("live", path == "/pulse/live" or path.startswith("/pulse/live/") or path.startswith("/api/pulse/live")),
        ("cohost", "cohost" in path.lower() or "co-host" in path.lower()),
        ("payments", "checkout" in path or "payment" in path or "stripe" in path),
        ("messaging", path.startswith("/api/pulse/messages") or path.startswith("/api/pulse/communications")),
        ("uploads", path.startswith("/api/pulse/media/upload") or path.startswith("/api/media/upload")),
    )
    for key, applies in checks:
        env_name = KILL_SWITCH_ENV[key]
        if applies and _truthy_env(env_name):
            return {"blocked": True, "switch": key, "env": env_name}
    return {"blocked": False}


def validate_json_shape(path: str, payload: Any) -> dict[str, Any]:
    allowed = STRICT_JSON_FIELDS.get(str(path or ""))
    if not allowed or not isinstance(payload, dict):
        return {"ok": True}
    unknown = sorted(str(key) for key in payload.keys() if str(key) not in allowed)
    if unknown:
        return {"ok": False, "unknown_fields": unknown[:20]}
    return {"ok": True}


def suspicious_payload_text(value: str) -> bool:
    return bool(SCRIPT_RE.search(str(value or "")))


def consume_nonce(scope: str, nonce: str, timestamp: Any) -> dict[str, Any]:
    nonce = str(nonce or "").strip()
    if not nonce or len(nonce) < 12 or len(nonce) > 160:
        return {"ok": False, "reason": "invalid_nonce"}
    try:
        ts = int(float(timestamp or 0))
    except Exception:
        return {"ok": False, "reason": "invalid_timestamp"}
    now = int(_now())
    if abs(now - ts) > MAX_NONCE_AGE_SECONDS:
        return {"ok": False, "reason": "expired_timestamp"}
    key = f"{scope}:{nonce}"
    cache_key = f"pulse-security:nonce:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
    if cache_engine.cache_get(cache_key):
        return {"ok": False, "reason": "replay_detected"}
    _purge_nonces()
    if key in _NONCES:
        return {"ok": False, "reason": "replay_detected"}
    _NONCES[key] = now + MAX_NONCE_AGE_SECONDS
    cache_engine.cache_set(cache_key, {"used_at": now}, MAX_NONCE_AGE_SECONDS)
    return {"ok": True}


def _purge_nonces() -> None:
    now = _now()
    for key, expires in list(_NONCES.items())[:1000]:
        if expires < now:
            _NONCES.pop(key, None)


def validate_pulseshell_call(*, user_id: int, module: str, action: str, request_id: str, timestamp: Any, nonce: str, ip_hash: str, user_agent: str) -> dict[str, Any]:
    key = f"{module}.{action}"
    if not user_id:
        return {"ok": False, "status": 401, "message": "Login required.", "reason": "login_required"}
    if key not in PULSESHELL_ALLOWED_ACTIONS:
        return {"ok": False, "status": 403, "message": "PulseShell action is not allowed.", "reason": "action_not_allowed"}
    nonce_result = consume_nonce(f"pulseshell:{int(user_id)}:{key}", nonce, timestamp)
    if not nonce_result.get("ok"):
        return {"ok": False, "status": 409, "message": "PulseShell request could not be verified.", "reason": nonce_result.get("reason")}
    signature = hmac.new(
        (os.getenv("PULSESOC_BRIDGE_SIGNING_SECRET") or os.getenv("SESSION_SECRET") or "pulsesoc-bridge").encode("utf-8"),
        f"{user_id}:{key}:{request_id}:{timestamp}:{nonce}:{ip_hash}:{user_agent[:120]}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "ok": True,
        "status": 200,
        "signature": signature,
        "expires_in": MAX_NONCE_AGE_SECONDS,
        "server_authoritative": True,
    }


def security_headers() -> dict[str, str]:
    return {
        "X-PulseSoc-Security": "server-authoritative",
        "X-PulseSoc-Client-Trust": "none",
    }
