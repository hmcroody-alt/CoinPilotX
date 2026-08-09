"""PulseSoc Communications Engine foundation.

This module owns call state, participant validation, RTC provider readiness, and
notification hooks for Messenger audio/video calls. It intentionally returns
explicit config_missing states when provider credentials are absent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse
from typing import Any

from pulse_communications_v2 import service as comm_service
from services import pulsesoc_notification_system


CALL_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS communication_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        public_id TEXT UNIQUE,
        conversation_id INTEGER,
        room_name TEXT UNIQUE,
        provider TEXT DEFAULT 'livekit',
        call_type TEXT,
        call_scope TEXT,
        status TEXT,
        created_by_user_id INTEGER,
        started_at TEXT,
        answered_at TEXT,
        ended_at TEXT,
        duration_seconds INTEGER DEFAULT 0,
        end_reason TEXT,
        metadata_json TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS communication_call_participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        role TEXT,
        status TEXT,
        muted_audio INTEGER DEFAULT 0,
        muted_video INTEGER DEFAULT 0,
        screen_sharing INTEGER DEFAULT 0,
        joined_at TEXT,
        left_at TEXT,
        last_seen_at TEXT,
        device_info_json TEXT,
        metadata_json TEXT,
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(call_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS communication_call_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id INTEGER,
        user_id INTEGER,
        event_type TEXT,
        event_payload_json TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS communication_call_quality_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        latency_ms INTEGER,
        jitter_ms INTEGER,
        packet_loss REAL,
        bitrate_audio INTEGER,
        bitrate_video INTEGER,
        fps REAL,
        resolution TEXT,
        network_type TEXT,
        device_info_json TEXT,
        quality_score REAL,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS communication_call_device_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id INTEGER,
        user_id INTEGER,
        device_id TEXT,
        platform TEXT,
        browser TEXT,
        permissions_json TEXT,
        connection_state TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """,
)

CALL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_communication_calls_conversation_status ON communication_calls(conversation_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_communication_calls_creator_created ON communication_calls(created_by_user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_communication_participants_user_status ON communication_call_participants(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_communication_events_call_created ON communication_call_events(call_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_communication_quality_call_user ON communication_call_quality_reports(call_id, user_id, created_at)",
)

VALID_CALL_TYPES = {"audio", "video"}
VALID_CALL_SCOPES = {"direct", "group", "live", "room"}
ACTIVE_STATUSES = {"created", "ringing", "accepted", "connecting", "connected", "active", "reconnecting"}
FINAL_STATUSES = {"ended", "missed", "declined", "failed", "canceled", "cancelled", "expired", "rejected", "disconnected"}
ALLOWED_TRANSITIONS = {
    "created": {"ringing", "connecting", "declined", "missed", "canceled", "failed"},
    "ringing": {"accepted", "connecting", "declined", "missed", "canceled", "failed"},
    "accepted": {"connecting", "connected", "failed", "ended"},
    "connecting": {"connected", "failed", "ended"},
    "connected": {"reconnecting", "ended", "failed"},
    "reconnecting": {"connected", "ended", "failed"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trace() -> str:
    return secrets.token_hex(6)


ERROR_CATALOG = {
    "config_missing": (
        "LIVEKIT_CONFIG_MISSING",
        "Calling provider is not configured",
        "PulseSoc is missing one or more LiveKit provider settings.",
        "Check LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET in the active deployment.",
    ),
    "livekit_token_failed": (
        "LIVEKIT_TOKEN_FAILED",
        "Call token could not be generated",
        "PulseSoc could not create a LiveKit access token for this call.",
        "Run the Calls Command Center config test and verify the LiveKit key and secret.",
    ),
    "missing_conversation": (
        "MISSING_CONVERSATION",
        "No conversation selected",
        "PulseSoc could not identify which conversation should receive this call.",
        "Open a conversation and try the call again.",
    ),
    "missing_recipient": (
        "RECIPIENT_OFFLINE",
        "No recipient is available",
        "This conversation does not currently have another active recipient to call.",
        "Choose a direct or group conversation with another member.",
    ),
    "self_call_blocked": (
        "SELF_CALL_BLOCKED",
        "Self-calls are not allowed",
        "PulseSoc blocked a call where the caller and recipient are the same user.",
        "Choose another recipient.",
    ),
    "invalid_recipient": (
        "RECIPIENT_NOT_IN_CONVERSATION",
        "Recipient is not in this conversation",
        "PulseSoc blocked the call because one recipient is not an active participant.",
        "Refresh the conversation members and try again.",
    ),
    "blocked": (
        "RECIPIENT_BLOCKED",
        "Call blocked",
        "This call is blocked by the conversation safety or block settings.",
        "Review the conversation privacy and block settings.",
    ),
    "active_call_exists": (
        "CALL_ALREADY_ACTIVE",
        "A call is already active",
        "This conversation already has an active call.",
        "Join, end, or wait for the current call to finish.",
    ),
    "missing_call": (
        "CALL_NOT_FOUND",
        "Call not found",
        "PulseSoc could not find this call record.",
        "Refresh Messenger and try again.",
    ),
    "forbidden": (
        "CALL_ACCESS_DENIED",
        "Call access denied",
        "This account is not allowed to access that call.",
        "Use the account that belongs to the conversation.",
    ),
    "not_participant": (
        "CALL_PARTICIPANT_REQUIRED",
        "Call participant required",
        "Only invited call participants can access this call.",
        "Start a new call from the conversation.",
    ),
    "call_final": (
        "CALL_ALREADY_ENDED",
        "Call already ended",
        "This call is no longer active.",
        "Start a new call if you still need to connect.",
    ),
    "server_error": (
        "BACKEND_EXCEPTION",
        "Call backend error",
        "PulseSoc hit an unexpected backend error while handling the call.",
        "Open Calls Command Center and search the correlation ID in logs.",
    ),
}


def _error_details(code: str, message: str, correlation_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    error_code, title, description, remediation = ERROR_CATALOG.get(
        code,
        ("UNKNOWN_ERROR", "Call could not start", message or "PulseSoc could not complete this call request.", "Try again, then inspect the correlation ID if it repeats."),
    )
    details = {
        "error_code": error_code,
        "error_title": title,
        "error_description": description,
        "remediation": remediation,
        "correlation_id": correlation_id,
    }
    details.update(overrides or {})
    return details


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value or {}, separators=(",", ":"), sort_keys=True)
    except Exception:
        return "{}"


def _row(row: Any) -> dict[str, Any]:
    return dict(row or {})


def _err(message: str, status: int = 400, code: str = "error", **extra: Any) -> dict[str, Any]:
    correlation_id = str(extra.pop("correlation_id", "") or _trace())
    detail_overrides = extra.pop("error_overrides", None)
    details = _error_details(code, message, correlation_id, detail_overrides if isinstance(detail_overrides, dict) else None)
    payload = {
        "ok": False,
        "status": code,
        "message": message,
        "http_status": status,
        "trace_id": correlation_id,
        **details,
    }
    payload.update(extra)
    return payload


def _ok(data: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    payload = {"ok": True, "status": "ready", "trace_id": _trace()}
    if data:
        payload.update(data)
    payload.update(extra)
    return payload


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _open_db():
    conn, cur = comm_service._open_db()
    ensure_schema(cur)
    return conn, cur


CALL_COMPAT_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "communication_calls": (
        ("public_id", "TEXT"),
        ("conversation_id", "INTEGER"),
        ("room_name", "TEXT"),
        ("provider", "TEXT DEFAULT 'livekit'"),
        ("call_type", "TEXT"),
        ("call_scope", "TEXT"),
        ("status", "TEXT"),
        ("created_by_user_id", "INTEGER"),
        ("started_at", "TEXT"),
        ("answered_at", "TEXT"),
        ("ended_at", "TEXT"),
        ("duration_seconds", "INTEGER DEFAULT 0"),
        ("end_reason", "TEXT"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ),
    "communication_call_participants": (
        ("call_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("role", "TEXT"),
        ("status", "TEXT"),
        ("muted_audio", "INTEGER DEFAULT 0"),
        ("muted_video", "INTEGER DEFAULT 0"),
        ("screen_sharing", "INTEGER DEFAULT 0"),
        ("joined_at", "TEXT"),
        ("left_at", "TEXT"),
        ("last_seen_at", "TEXT"),
        ("device_info_json", "TEXT"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ),
    "communication_call_events": (
        ("call_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("event_type", "TEXT"),
        ("event_payload_json", "TEXT"),
        ("created_at", "TEXT"),
    ),
    "communication_call_quality_reports": (
        ("call_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("latency_ms", "INTEGER"),
        ("jitter_ms", "INTEGER"),
        ("packet_loss", "REAL"),
        ("bitrate_audio", "INTEGER"),
        ("bitrate_video", "INTEGER"),
        ("fps", "REAL"),
        ("resolution", "TEXT"),
        ("network_type", "TEXT"),
        ("device_info_json", "TEXT"),
        ("quality_score", "REAL"),
        ("created_at", "TEXT"),
    ),
    "communication_call_device_sessions": (
        ("call_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("device_id", "TEXT"),
        ("platform", "TEXT"),
        ("browser", "TEXT"),
        ("permissions_json", "TEXT"),
        ("connection_state", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ),
}


def _table_columns(cur: Any, table: str) -> set[str]:
    try:
        from services import db as db_service

        if getattr(db_service, "IS_POSTGRES", False):
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=?
                """,
                (table,),
            )
            return {str(row["column_name"]) for row in cur.fetchall()}
    except Exception:
        return set()
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row["name"]) for row in cur.fetchall()}


def _ensure_compat_columns(cur: Any) -> None:
    for table, columns in CALL_COMPAT_COLUMNS.items():
        existing = _table_columns(cur, table)
        if not existing:
            continue
        for name, definition in columns:
            if name in existing:
                continue
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            existing.add(name)


def ensure_schema(cur: Any) -> None:
    for sql in CALL_TABLES:
        cur.execute(sql)
    _ensure_compat_columns(cur)
    for sql in CALL_INDEXES:
        cur.execute(sql)


def livekit_config_status() -> dict[str, Any]:
    required = {
        "LIVEKIT_URL": os.getenv("LIVEKIT_URL", "").strip(),
        "LIVEKIT_API_KEY": os.getenv("LIVEKIT_API_KEY", "").strip(),
        "LIVEKIT_API_SECRET": os.getenv("LIVEKIT_API_SECRET", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    return {
        "configured": not missing,
        "missing": missing,
        "url_configured": bool(required["LIVEKIT_URL"]),
        "webhook_secret_configured": bool(os.getenv("LIVEKIT_WEBHOOK_SECRET", "").strip()),
        "turn_configured": bool(os.getenv("TURN_SERVER_URL", "").strip()),
        "stun_configured": bool(os.getenv("STUN_SERVER_URL", "").strip()),
    }


def rtc_provider() -> str:
    """Return the explicitly selected provider; fail safe to the proven path."""
    value = os.getenv("RTC_PROVIDER", "livekit").strip().lower()
    return value if value in {"livekit", "agora"} else "livekit"


def agora_config_status() -> dict[str, Any]:
    required = {
        "AGORA_APP_ID": os.getenv("AGORA_APP_ID", "").strip(),
        "AGORA_APP_CERTIFICATE": os.getenv("AGORA_APP_CERTIFICATE", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    return {
        "configured": not missing,
        "missing": missing,
        "app_id_configured": bool(required["AGORA_APP_ID"]),
        "certificate_configured": bool(required["AGORA_APP_CERTIFICATE"]),
        "token_ttl_seconds": max(300, min(int(os.getenv("AGORA_TOKEN_TTL_SECONDS", "3600") or 3600), 86400)),
    }


def _require_agora() -> dict[str, Any] | None:
    status = agora_config_status()
    if status["configured"]:
        return None
    logging.info("PULSESOC_AGORA_CONFIG_MISSING missing=%s", ",".join(status.get("missing") or []))
    return _err("Calling provider is not configured.", 503, "config_missing", provider="agora", agora=status)


def _require_rtc_provider(provider: str | None = None) -> dict[str, Any] | None:
    return _require_agora() if (provider or rtc_provider()) == "agora" else _require_livekit()


def _agora_uid(user_id: int) -> int:
    uid = int(user_id)
    if uid <= 0 or uid > 0xFFFFFFFF:
        raise ValueError("PulseSoc user id cannot be represented as an Agora numeric UID")
    return uid


def _generate_agora_token(room_name: str, user_id: int, call_type: str = "audio", participant_role: str = "member") -> dict[str, Any]:
    missing = _require_agora()
    if missing:
        return missing
    # Imported only on the Agora path so LiveKit rollback never depends on this package.
    try:
        from agora_token_builder import RtcTokenBuilder
    except ImportError:
        return _err("Agora token generation is unavailable.", 503, "agora_token_builder_missing", provider="agora")
    app_id = os.getenv("AGORA_APP_ID", "").strip()
    certificate = os.getenv("AGORA_APP_CERTIFICATE", "").strip()
    ttl = agora_config_status()["token_ttl_seconds"]
    expires_at = int(time.time()) + int(ttl)
    uid = _agora_uid(user_id)
    # Calls and authorized live stage participants publish. Passive live viewers
    # are minted separately with the subscriber role by the Live service.
    token = RtcTokenBuilder.buildTokenWithUid(app_id, certificate, room_name, uid, 1, expires_at)
    normalized_call_type = "video" if str(call_type).strip().lower() == "video" else "audio"
    return {
        "ok": True,
        "provider": "agora",
        "token": token,
        "app_id": app_id,
        "channel_name": room_name,
        "room_name": room_name,
        "room_type": f"{normalized_call_type}_call",
        "uid": uid,
        "participant_identity": f"user-{uid}",
        "participant_role": str(participant_role or "member").strip().lower(),
        "can_publish": True,
        "can_subscribe": True,
        "can_publish_sources": ["microphone", "camera"] if normalized_call_type == "video" else ["microphone"],
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(timespec="seconds"),
        **_realtime_audio_v2_status(normalized_call_type),
    }


def _generate_rtc_token(provider: str, room_name: str, user_id: int, call_type: str = "audio", participant_role: str = "member") -> dict[str, Any]:
    if provider == "agora":
        return _generate_agora_token(room_name, user_id, call_type, participant_role)
    return _generate_livekit_token(room_name, user_id, call_type, participant_role)


def livekit_hd_quality_policy() -> dict[str, Any]:
    return {
        "provider": "livekit",
        "adaptive_stream": True,
        "dynacast": True,
        "simulcast": True,
        "video_codec": "vp8",
        "audio_capture": {
            "echo_cancellation": True,
            "noise_suppression": True,
            "auto_gain_control": True,
        },
        "video_calls": {
            "default": "1280x720@30",
            "ideal_width": 1280,
            "ideal_height": 720,
            "ideal_fps": 30,
            "max_width": 1920,
            "max_height": 1080,
            "bitrate_bps": 2_500_000,
            "subscription": "high layer for active full-screen remote video; medium layer when minimized",
        },
        "live_hosts": {
            "default": "auto_hd",
            "preferred": "1920x1080@30",
            "fallbacks": ["1280x720@30", "960x540@24", "640x480@20"],
            "bitrate_bps": 4_200_000,
        },
        "layers": {
            "low": "180p",
            "medium": "360p",
            "call_high": "540p",
            "live_high": "720p",
        },
        "mux_egress": {
            "rule": "Mux quality depends on the LiveKit input track; verify LiveKit published resolution before tuning replay output.",
        },
    }


def _require_livekit() -> dict[str, Any] | None:
    status = livekit_config_status()
    if status["configured"]:
        return None
    logging.info("PULSESOC_CALL_CONFIG_MISSING missing=%s", ",".join(status.get("missing") or []))
    return _err(
        "Calling provider is not configured.",
        503,
        "config_missing",
        provider="livekit",
        livekit=status,
        error_overrides={
            "missing": status.get("missing") or [],
            "provider": "livekit",
        },
    )


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _realtime_audio_v2_status(call_type: str) -> dict[str, bool]:
    platform_enabled = _env_enabled("REALTIME_AUDIO_PLATFORM_V2_ENABLED", False)
    feature_flag = "REALTIME_VIDEO_CALLS_V2_ENABLED" if str(call_type).strip().lower() == "video" else "REALTIME_AUDIO_CALLS_V2_ENABLED"
    return {
        "realtime_audio_v2_enabled": platform_enabled and _env_enabled(feature_flag, False),
        "realtime_audio_v2_fallback_enabled": _env_enabled("REALTIME_AUDIO_V2_FALLBACK_ENABLED", True),
    }


def _generate_livekit_token(
    room_name: str,
    user_id: int,
    call_type: str = "audio",
    participant_role: str = "member",
) -> dict[str, Any]:
    missing = _require_livekit()
    if missing:
        return missing
    api_key = os.getenv("LIVEKIT_API_KEY", "").strip()
    api_secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
    now = int(time.time())
    normalized_call_type = "video" if str(call_type).strip().lower() == "video" else "audio"
    room_type = "video_call" if normalized_call_type == "video" else "audio_call"
    normalized_role = str(participant_role or "member").strip().lower()
    if normalized_role not in {"caller", "callee"}:
        normalized_role = "member"
    publish_sources = ["microphone", "camera"] if normalized_call_type == "video" else ["microphone"]
    grants = {
        "roomJoin": True,
        "room": room_name,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
        "canPublishSources": publish_sources,
    }
    participant_identity = f"user-{int(user_id)}"
    payload = {
        "iss": api_key,
        "sub": participant_identity,
        "name": f"PulseSoc member {int(user_id)}",
        "metadata": _json_dumps({
            "room_type": room_type,
            "participant_role": normalized_role,
            "authenticated_user_id": int(user_id),
        }),
        "nbf": now - 10,
        "exp": now + 60 * 60,
        "video": grants,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_base64url(_json_dumps(header).encode())}.{_base64url(_json_dumps(payload).encode())}"
    signature = hmac.new(api_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return {
        "ok": True,
        "provider": "livekit",
        "token": f"{signing_input}.{_base64url(signature)}",
        "livekit_url": _livekit_ws_url(),
        "room_name": room_name,
        "room_type": room_type,
        "participant_identity": participant_identity,
        "participant_role": normalized_role,
        "can_publish": True,
        "can_subscribe": True,
        "can_publish_sources": publish_sources,
        **_realtime_audio_v2_status(normalized_call_type),
        "expires_at": datetime.fromtimestamp(payload["exp"], timezone.utc).isoformat(timespec="seconds"),
    }


def _generate_livekit_admin_token(room_name: str) -> dict[str, Any]:
    missing = _require_livekit()
    if missing:
        return missing
    api_key = os.getenv("LIVEKIT_API_KEY", "").strip()
    api_secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
    now = int(time.time())
    payload = {
        "iss": api_key,
        "sub": "pulsesoc-admin-config-check",
        "nbf": now - 10,
        "exp": now + 5 * 60,
        "video": {
            "roomCreate": True,
            "roomList": True,
            "roomAdmin": True,
            "room": room_name,
        },
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_base64url(_json_dumps(header).encode())}.{_base64url(_json_dumps(payload).encode())}"
    signature = hmac.new(api_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return {"ok": True, "token": f"{signing_input}.{_base64url(signature)}", "room_name": room_name}


def _livekit_http_url() -> str:
    raw = os.getenv("LIVEKIT_URL", "").strip().rstrip("/")
    parsed = urlparse(raw)
    scheme = "https" if parsed.scheme == "wss" else "http" if parsed.scheme == "ws" else parsed.scheme
    return urlunparse((scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _livekit_ws_url() -> str:
    """Client-facing LiveKit endpoint, always a WebSocket scheme.

    The LiveKit client SDKs (livekit-client / @livekit/react-native) expect a
    ws:// or wss:// URL and refuse to connect to an http(s) endpoint. Operators
    routinely paste the https:// dashboard URL into LIVEKIT_URL, which then
    silently breaks every call with no server-side error. Normalize
    https->wss and http->ws (and default a bare host to wss) so that common
    misconfiguration cannot take calling down. ws/wss pass through unchanged.
    """
    raw = os.getenv("LIVEKIT_URL", "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if not parsed.netloc:
        # Bare host with no scheme lands entirely in `path`; assume secure ws.
        return f"wss://{raw}"
    if scheme == "https":
        scheme = "wss"
    elif scheme == "http":
        scheme = "ws"
    elif scheme not in ("ws", "wss"):
        scheme = "wss"
    return urlunparse((scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _livekit_room_connectivity_check() -> dict[str, Any]:
    room_name = f"pulsesoc-config-check-{secrets.token_hex(6)}"
    token_payload = _generate_livekit_admin_token(room_name)
    if not token_payload.get("ok"):
        return {
            "can_create_test_room": False,
            "can_cleanup_test_room": False,
            "provider_error": token_payload.get("status") or "config_missing",
        }
    try:
        import requests
    except Exception:
        return {
            "can_create_test_room": False,
            "can_cleanup_test_room": False,
            "provider_error": "requests_unavailable",
        }
    base_url = _livekit_http_url()
    headers = {
        "Authorization": f"Bearer {token_payload['token']}",
        "Content-Type": "application/json",
    }
    body = {"name": room_name, "empty_timeout": 60, "max_participants": 2}
    try:
        create = requests.post(
            f"{base_url}/twirp/livekit.RoomService/CreateRoom",
            json=body,
            headers=headers,
            timeout=6,
        )
        if create.status_code >= 300:
            return {
                "can_create_test_room": False,
                "can_cleanup_test_room": False,
                "provider_error": f"create_room_http_{create.status_code}",
            }
        cleanup = requests.post(
            f"{base_url}/twirp/livekit.RoomService/DeleteRoom",
            json={"room": room_name},
            headers=headers,
            timeout=6,
        )
        return {
            "can_create_test_room": True,
            "can_cleanup_test_room": cleanup.status_code < 300,
            "provider_error": "" if cleanup.status_code < 300 else f"delete_room_http_{cleanup.status_code}",
        }
    except requests.RequestException as exc:
        return {
            "can_create_test_room": False,
            "can_cleanup_test_room": False,
            "provider_error": exc.__class__.__name__,
        }


def _conversation_participants(cur: Any, conversation_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT p.user_id, p.role, COALESCE(u.display_name,u.username,'Pulse member') AS display_name
        FROM comm_v2_participants p
        LEFT JOIN users u ON u.user_id=p.user_id
        WHERE p.conversation_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
        ORDER BY p.id ASC
        """,
        (int(conversation_id),),
    )
    return [dict(row) for row in cur.fetchall()]


def _participant_allowed(cur: Any, conversation_id: int, user_id: int) -> bool:
    cur.execute(
        """
        SELECT 1 FROM comm_v2_participants
        WHERE conversation_id=? AND user_id=? AND membership_state='active' AND COALESCE(left_at,'')=''
        LIMIT 1
        """,
        (int(conversation_id), int(user_id)),
    )
    return cur.fetchone() is not None


def _call_ref_where(call_ref: str | int) -> tuple[str, tuple[Any, ...]]:
    text = str(call_ref or "").strip()
    if text.isdigit():
        return "id=?", (int(text),)
    return "public_id=?", (text,)


def _get_call(cur: Any, call_ref: str | int) -> dict[str, Any]:
    where, params = _call_ref_where(call_ref)
    cur.execute(f"SELECT * FROM communication_calls WHERE {where} LIMIT 1", params)
    return _row(cur.fetchone())


def _inserted_call_id(cur: Any, public_id: str) -> int:
    call_id = int(getattr(cur, "lastrowid", None) or 0)
    if call_id:
        return call_id
    cur.execute("SELECT id FROM communication_calls WHERE public_id=? LIMIT 1", (public_id,))
    row = _row(cur.fetchone())
    if row.get("id"):
        return int(row["id"])
    raise RuntimeError("communication_calls insert did not return an id")


def _serialize_call(cur: Any, call: dict[str, Any], user_id: int = 0, include_token: bool = False) -> dict[str, Any]:
    call_id = int(call.get("id") or 0)
    cur.execute(
        """
        SELECT p.*, COALESCE(u.display_name,u.username,'Pulse member') AS display_name, COALESCE(u.avatar_url,'') AS avatar_url
        FROM communication_call_participants p
        LEFT JOIN users u ON u.user_id=p.user_id
        WHERE p.call_id=?
        ORDER BY p.id ASC
        """,
        (call_id,),
    )
    participants = [dict(row) for row in cur.fetchall()]
    me = next((item for item in participants if int(item.get("user_id") or 0) == int(user_id or 0)), {}) if user_id else {}
    payload = {
        "call_id": call_id,
        "public_id": call.get("public_id"),
        "conversation_id": int(call.get("conversation_id") or 0),
        "room_name": call.get("room_name") or "",
        "provider": call.get("provider") or "livekit",
        "call_type": call.get("call_type") or "audio",
        "call_scope": call.get("call_scope") or "direct",
        "status": call.get("status") or "created",
        "created_by_user_id": int(call.get("created_by_user_id") or 0),
        "started_at": call.get("started_at") or "",
        "answered_at": call.get("answered_at") or "",
        "ended_at": call.get("ended_at") or "",
        "created_at": call.get("created_at") or "",
        "updated_at": call.get("updated_at") or "",
        "duration_seconds": int(call.get("duration_seconds") or 0),
        "end_reason": call.get("end_reason") or "",
        "participants": participants,
        "participant": me,
        "livekit": livekit_config_status(),
        "agora": agora_config_status(),
    }
    if include_token and user_id:
        payload["join"] = _generate_rtc_token(
            payload["provider"],
            payload["room_name"],
            int(user_id),
            payload["call_type"],
            str(me.get("role") or "member"),
        )
    return payload


def _event(cur: Any, call_id: int, user_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    cur.execute(
        """
        INSERT INTO communication_call_events (call_id, user_id, event_type, event_payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(call_id), int(user_id or 0), str(event_type or ""), _json_dumps(payload), _now()),
    )


def _call_participant_user_ids(cur: Any, call_id: int) -> list[int]:
    cur.execute("SELECT user_id FROM communication_call_participants WHERE call_id=? ORDER BY id ASC", (int(call_id),))
    ids = []
    for row in cur.fetchall():
        item = _row(row)
        user_id = int(item.get("user_id") or 0)
        if user_id:
            ids.append(user_id)
    return sorted(set(ids))


def _emit_call_sync_event(
    cur: Any,
    call: dict[str, Any],
    event_type: str,
    actor_user_id: int = 0,
    recipient_user_ids: list[int] | tuple[int, ...] | None = None,
    status: str = "",
    reason: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    call = dict(call or {})
    call_id = int(call.get("id") or 0)
    public_id = str(call.get("public_id") or call_id or "")
    if not call_id:
        return
    recipients = list(recipient_user_ids or _call_participant_user_ids(cur, call_id))
    event_type = str(event_type or "call_updated")[:80]
    now = _now()
    conversation_id = int(call.get("conversation_id") or 0)
    target_url = f"/pulse/calls/{public_id}" if public_id else f"/pulse/messages/{conversation_id}?tab=calls"
    metadata = {
        "domain": "communications",
        "category": "calls",
        "event_type": event_type,
        "entity_type": "call",
        "entity_id": public_id,
        "actor_id": int(actor_user_id or 0),
        "timestamp": now,
        "sync_cursor_key": f"{event_type}:call:{public_id}:{now}",
        "call_id": public_id,
        "internal_call_id": call_id,
        "conversation_id": conversation_id,
        "call_type": str(call.get("call_type") or "audio")[:40],
        "status": str(status or call.get("status") or "")[:80],
        "reason": str(reason or call.get("end_reason") or "")[:120],
        "invalidates": ["activity", "notifications", "calls", "messenger"],
    }
    metadata.update(dict(extra or {}))
    titles = {
        "call_started": "Call started",
        "call_accepted": "Call accepted",
        "call_declined": "Call declined",
        "call_ended": "Call ended",
        "call_missed": "Call missed",
        "call_failed": "Call failed",
    }
    bodies = {
        "call_started": "A PulseSoc call started.",
        "call_accepted": "A PulseSoc call was accepted.",
        "call_declined": "A PulseSoc call was declined.",
        "call_ended": "A PulseSoc call ended.",
        "call_missed": "A PulseSoc call was missed.",
        "call_failed": "A PulseSoc call failed.",
    }
    for recipient_id in recipients:
        if not recipient_id:
            continue
        try:
            cur.execute(
                """
                INSERT INTO pulse_notifications
                (user_id, actor_user_id, type, title, body, entity_type, entity_id, deep_link, target_url,
                 is_read, delivery_status, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, 'call', ?, ?, ?, 0, 'created', ?, ?)
                """,
                (
                    int(recipient_id),
                    int(actor_user_id or 0),
                    event_type,
                    titles.get(event_type, "Call updated"),
                    bodies.get(event_type, "PulseSoc call state changed."),
                    public_id,
                    target_url,
                    target_url,
                    _json_dumps({**metadata, "recipient_user_id": int(recipient_id)}),
                    now,
                ),
            )
            notification_id = int(getattr(cur, "lastrowid", 0) or 0)
            cur.execute(
                """
                INSERT INTO pulse_notification_deliveries
                (notification_id, user_id, channel, provider, status, created_at, sent_at)
                VALUES (?, ?, 'in_app', 'pulse', 'created', ?, ?)
                """,
                (notification_id, int(recipient_id), now, now),
            )
        except Exception as exc:
            logging.debug("PULSESOC_CALL_SYNC_EVENT_SKIPPED call_id=%s type=%s recipient=%s error=%s", call_id, event_type, recipient_id, exc.__class__.__name__)


def _recipient_online_state(cur: Any, user_id: int) -> dict[str, Any]:
    """Best-effort recipient availability used only for call diagnostics."""
    now = _now()
    try:
        cur.execute(
            """
            SELECT status, last_seen_at, active_until, updated_at
            FROM comm_v2_presence
            WHERE user_id=?
            LIMIT 1
            """,
            (int(user_id),),
        )
        row = _row(cur.fetchone())
        if row:
            active_until = str(row.get("active_until") or "")
            return {
                "tracked": True,
                "online": str(row.get("status") or "").lower() == "online" and (not active_until or active_until >= now),
                "status": row.get("status") or "offline",
                "last_seen_at": row.get("last_seen_at") or "",
                "source": "comm_v2_presence",
            }
    except Exception:
        pass
    try:
        cur.execute(
            """
            SELECT status, last_seen_at, last_active_at, updated_at
            FROM user_presence
            WHERE user_id=?
            LIMIT 1
            """,
            (int(user_id),),
        )
        row = _row(cur.fetchone())
        if row:
            status = str(row.get("status") or "").lower()
            return {
                "tracked": True,
                "online": status in {"online", "active", "active_now"},
                "status": row.get("status") or "offline",
                "last_seen_at": row.get("last_seen_at") or row.get("last_active_at") or "",
                "source": "user_presence",
            }
    except Exception:
        pass
    return {"tracked": False, "online": False, "status": "unknown", "last_seen_at": "", "source": ""}


def _transition(cur: Any, call: dict[str, Any], new_status: str, user_id: int = 0, reason: str = "") -> dict[str, Any]:
    current = str(call.get("status") or "created")
    new_status = str(new_status or "").strip().lower()
    if current in FINAL_STATUSES and new_status != current:
        return _err("Ended calls cannot be changed.", 409, "call_final")
    if new_status != current and new_status not in ALLOWED_TRANSITIONS.get(current, set()) and new_status not in FINAL_STATUSES:
        return _err(f"Invalid call transition: {current} to {new_status}.", 409, "invalid_transition")
    now = _now()
    updates = ["status=?", "updated_at=?"]
    values: list[Any] = [new_status, now]
    if new_status in {"connected", "accepted"} and not call.get("answered_at"):
        updates.append("answered_at=?")
        values.append(now)
    if new_status in FINAL_STATUSES:
        updates.append("ended_at=?")
        updates.append("end_reason=?")
        values.extend([now, reason or new_status])
        start_text = call.get("answered_at") or call.get("started_at") or call.get("created_at")
        try:
            start = datetime.fromisoformat(str(start_text or "").replace("Z", "+00:00"))
            duration = max(0, int((datetime.now(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()))
            updates.append("duration_seconds=?")
            values.append(duration)
        except Exception:
            pass
    values.append(int(call["id"]))
    cur.execute(f"UPDATE communication_calls SET {', '.join(updates)} WHERE id=?", values)
    _event(cur, int(call["id"]), int(user_id or 0), new_status, {"from": current, "to": new_status, "reason": reason})
    return {"ok": True, "status": new_status}


def _safe_transition(cur: Any, call: dict[str, Any], new_status: str, user_id: int = 0, reason: str = "") -> dict[str, Any]:
    result = _transition(cur, call, new_status, user_id, reason)
    if result.get("ok"):
        return _get_call(cur, call.get("public_id") or call.get("id") or "")
    return call


def _participant_for_call(cur: Any, call_id: int, user_id: int) -> dict[str, Any]:
    cur.execute(
        "SELECT * FROM communication_call_participants WHERE call_id=? AND user_id=? LIMIT 1",
        (int(call_id), int(user_id)),
    )
    return _row(cur.fetchone())


def _require_call_access(cur: Any, user_id: int, call_ref: str | int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    call = _get_call(cur, call_ref)
    if not call:
        return {}, {}, _err("Call not found.", 404, "missing_call")
    if not _participant_allowed(cur, int(call.get("conversation_id") or 0), int(user_id)):
        return call, {}, _err("You do not have access to this call.", 403, "forbidden")
    participant = _participant_for_call(cur, int(call["id"]), int(user_id))
    if not participant:
        return call, {}, _err("Only call participants can access this call.", 403, "not_participant")
    return call, participant, None


def _mark_missed_stale_calls_cur(cur: Any, timeout_seconds: int = 45) -> int:
    threshold = time.time() - max(5, int(timeout_seconds or 45))
    cur.execute("SELECT * FROM communication_calls WHERE status='ringing' ORDER BY id ASC")
    updated = 0
    for row in cur.fetchall():
        call = dict(row)
        try:
            created_ts = datetime.fromisoformat(str(call.get("created_at") or "").replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if created_ts > threshold:
            continue
        cur.execute(
            "SELECT user_id FROM communication_call_participants WHERE call_id=? AND role='callee' AND status='ringing'",
            (int(call["id"]),),
        )
        recipients = [int(item["user_id"]) for item in cur.fetchall()]
        _transition(cur, call, "missed", int(call.get("created_by_user_id") or 0), "ring_timeout")
        cur.execute(
            "UPDATE communication_call_participants SET status='missed', left_at=?, updated_at=? WHERE call_id=? AND role='callee' AND status='ringing'",
            (_now(), _now(), int(call["id"])),
        )
        caller_name = comm_service._user_summary(cur, int(call.get("created_by_user_id") or 0)).get("display_name") or "Someone"
        for recipient_id in recipients:
            _notify_missed_call(cur, call, int(call.get("created_by_user_id") or 0), recipient_id, caller_name)
        _emit_call_sync_event(cur, _get_call(cur, call.get("public_id") or call.get("id") or ""), "call_missed", int(call.get("created_by_user_id") or 0), [int(call.get("created_by_user_id") or 0), *recipients], status="missed", reason="ring_timeout")
        updated += 1
    return updated


def _expire_stale_active_calls_cur(cur: Any) -> int:
    """End non-ringing sessions that can no longer prove current call activity."""
    timeouts = {
        "created": int(os.getenv("PULSESOC_CALL_CREATED_STALE_SECONDS", "60") or 60),
        "accepted": int(os.getenv("PULSESOC_CALL_CONNECTING_STALE_SECONDS", "120") or 120),
        "connecting": int(os.getenv("PULSESOC_CALL_CONNECTING_STALE_SECONDS", "120") or 120),
        "reconnecting": int(os.getenv("PULSESOC_CALL_RECONNECTING_STALE_SECONDS", "180") or 180),
        "connected": int(os.getenv("PULSESOC_CALL_CONNECTED_STALE_SECONDS", "21600") or 21600),
        "active": int(os.getenv("PULSESOC_CALL_CONNECTED_STALE_SECONDS", "21600") or 21600),
    }
    placeholders = ",".join(["?"] * len(timeouts))
    cur.execute(
        f"SELECT * FROM communication_calls WHERE status IN ({placeholders}) ORDER BY id ASC",
        tuple(timeouts),
    )
    now_ts = time.time()
    now = _now()
    updated = 0
    for row in cur.fetchall():
        call = dict(row)
        status = str(call.get("status") or "created").lower()
        timestamp = call.get("updated_at") or call.get("answered_at") or call.get("started_at") or call.get("created_at")
        try:
            activity_ts = datetime.fromisoformat(str(timestamp or "").replace("Z", "+00:00")).timestamp()
        except Exception:
            activity_ts = 0
        if activity_ts and now_ts - activity_ts <= max(30, int(timeouts.get(status) or 120)):
            continue
        _transition(cur, call, "expired", 0, f"stale_{status}_timeout")
        cur.execute(
            """
            UPDATE communication_call_participants
            SET status=CASE WHEN status IN ('joined','ringing') THEN 'left' ELSE status END,
                left_at=CASE WHEN status IN ('joined','ringing') THEN ? ELSE left_at END,
                updated_at=?
            WHERE call_id=?
            """,
            (now, now, int(call["id"])),
        )
        refreshed = _get_call(cur, call.get("public_id") or call.get("id") or "")
        _emit_call_sync_event(cur, refreshed, "call_expired", 0, status="expired", reason=f"stale_{status}_timeout")
        updated += 1
    return updated


def _publish_call_realtime(
    cur: Any,
    call: dict[str, Any],
    actor_id: int,
    recipient_id: int,
    event_type: str,
    notification_result: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from services import realtime_engine

        call_payload = _serialize_call(cur, call, int(recipient_id))
        payload = {
            "conversation_id": int(call.get("conversation_id") or 0),
            "call_id": call.get("public_id") or call.get("id"),
            "public_id": call.get("public_id") or "",
            "sender_user_id": int(actor_id or 0),
            "caller_user_id": int(actor_id or 0),
            "recipient_user_id": int(recipient_id),
            "call": call_payload,
            "notification": (notification_result or {}).get("notification") or {},
            "notification_id": int((notification_result or {}).get("notification_id") or 0),
            "delivery_jobs": (notification_result or {}).get("delivery_jobs") or [],
            "push_policy": (policy or {}).get("reason") or "deliver",
            "suppress_push": bool((policy or {}).get("suppress_push")),
        }
        channels = [
            (f"comm_v2:user:{int(recipient_id)}", event_type),
            (f"comm_v2:user:{int(recipient_id)}", "communication_call_incoming"),
            (f"comm_v2:user:{int(recipient_id)}", "call_started"),
            (f"cc:user:{int(recipient_id)}", event_type),
            (f"pulse:user:{int(recipient_id)}", "notification_created"),
        ]
        for channel, kind in channels:
            realtime_engine.publish_event(channel, kind, payload)
        _event(cur, int(call["id"]), int(recipient_id), "incoming_call_realtime_emitted", {"channels": len(channels), "event_type": event_type})
        return {"ok": True, "published": len(channels)}
    except Exception as exc:
        logging.warning(
            "PULSESOC_CALL_REALTIME_PUBLISH_FAILED call_id=%s recipient=%s error=%s",
            call.get("id"),
            recipient_id,
            exc.__class__.__name__,
        )
        _event(cur, int(call["id"]), int(recipient_id), "incoming_call_realtime_failed", {"error": exc.__class__.__name__})
        return {"ok": False, "status": "realtime_failed", "message": exc.__class__.__name__}


def _notify_incoming_call(cur: Any, call: dict[str, Any], actor_id: int, recipients: list[int], actor_name: str = "") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    conversation_id = int(call.get("conversation_id") or 0)
    for recipient_id in recipients:
        if int(recipient_id) == int(actor_id):
            continue
        try:
            policy = comm_service._participant_push_policy(cur, int(recipient_id), int(actor_id), conversation_id)
            if policy.get("skip"):
                _event(cur, int(call["id"]), int(recipient_id), "incoming_call_notification_skipped", policy)
                continue
            channels = ["in_app", "call"] if policy.get("suppress_push") else ["in_app", "push", "call"]
            result = pulsesoc_notification_system.intake_event(
                event_type="incoming_call",
                recipient_user_id=int(recipient_id),
                actor_user_id=int(actor_id),
                source_type="communication_call",
                source_id=str(call.get("public_id") or call.get("id") or ""),
                title=f"{actor_name or 'Someone'} is Pulsing You",
                body="Video Connection" if str(call.get("call_type") or "audio").lower() == "video" else "Voice Connection",
                preview="Incoming voice call" if str(call.get("call_type") or "audio").lower() != "video" else "Incoming video call",
                deep_link=f"/pulse/messages/{conversation_id}?call_id={call.get('public_id') or call.get('id')}",
                metadata={
                    "conversation_id": conversation_id,
                    "call_id": call.get("public_id") or call.get("id"),
                    "call_type": call.get("call_type") or "audio",
                    "source_type": "communication_call",
                    "source_id": str(call.get("public_id") or call.get("id") or ""),
                    "sound_key": "call",
                    "vibration": [120, 80, 120, 80, 240],
                },
                category="calls",
                priority="urgent",
                urgency="immediate",
                channels=channels,
                dedupe_key=f"incoming-call:{call.get('public_id') or call.get('id')}:{recipient_id}",
            )
            realtime = _publish_call_realtime(cur, call, int(actor_id), int(recipient_id), "incoming_call", result, policy)
            result = {**result, "realtime": realtime, "push_policy": policy.get("reason") or "deliver"}
            _event(
                cur,
                int(call["id"]),
                int(recipient_id),
                "incoming_call_delivery_attempt",
                {
                    "notification_id": result.get("notification_id") or 0,
                    "suppressed": bool(result.get("suppressed")),
                    "deduped": bool(result.get("deduped")),
                    "delivery_jobs": result.get("delivery_jobs") or [],
                    "realtime": realtime,
                    "policy": policy,
                },
            )
            results.append(result)
        except Exception as exc:
            logging.warning("PULSESOC_CALL_INCOMING_NOTIFICATION_FAILED call_id=%s recipient=%s error=%s", call.get("id"), recipient_id, exc)
            _event(cur, int(call["id"]), int(recipient_id), "incoming_call_notification_failed", {"error": exc.__class__.__name__})
    return results


def _notify_missed_call(cur: Any, call: dict[str, Any], actor_id: int, recipient_id: int, actor_name: str = "") -> dict[str, Any]:
    try:
        return pulsesoc_notification_system.notify_missed_call(
            recipient_user_id=int(recipient_id),
            actor_user_id=int(actor_id),
            conversation_id=int(call.get("conversation_id") or 0),
            call_id=call.get("public_id") or call.get("id"),
            actor_name=actor_name or "Someone",
            metadata={
                "source_type": "communication_call",
                "call_type": call.get("call_type") or "audio",
                "sound_key": "missed_call",
                "vibration": [180, 120, 180],
            },
        )
    except Exception as exc:
        logging.warning("PULSESOC_CALL_MISSED_NOTIFICATION_FAILED call_id=%s recipient=%s error=%s", call.get("id"), recipient_id, exc)
        _event(cur, int(call["id"]), int(recipient_id), "missed_call_notification_failed", {"error": exc.__class__.__name__})
        return {"ok": False, "status": "notification_failed", "message": exc.__class__.__name__}


def start_call(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    disabled = comm_service._disabled("start_call")
    if disabled:
        return disabled
    call_type = str(payload.get("call_type") or "audio").strip().lower()
    if call_type not in VALID_CALL_TYPES:
        return _err("Unsupported call type.", 400, "invalid_call_type")
    conversation_ref = payload.get("conversation_id") or payload.get("conversation_ref") or payload.get("thread_id")
    if not conversation_ref:
        return _err("conversation_id is required.", 400, "missing_conversation")
    conn, cur = _open_db()
    try:
        conversation, access = comm_service._conversation_access(cur, int(user_id), conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403, access)
        selected_provider = rtc_provider()
        provider_missing = _require_rtc_provider(selected_provider)
        if provider_missing:
            return provider_missing
        conversation_id = int(conversation["id"])
        participants = _conversation_participants(cur, conversation_id)
        participant_ids = [int(item["user_id"]) for item in participants]
        recipient_ids = [int(value) for value in payload.get("recipient_user_ids") or [] if int(value or 0)]
        if not recipient_ids:
            recipient_ids = [value for value in participant_ids if value != int(user_id)]
        recipient_ids = sorted(set(recipient_ids))
        if not recipient_ids:
            return _err("Choose at least one call recipient.", 400, "missing_recipient")
        if int(user_id) in recipient_ids:
            return _err("You cannot call yourself.", 400, "self_call_blocked")
        invalid = [value for value in recipient_ids if value not in participant_ids]
        if invalid:
            return _err("Every recipient must be a conversation participant.", 403, "invalid_recipient")
        if comm_service._blocked_between(cur, int(user_id), recipient_ids):
            return _err("Calls are blocked for this conversation.", 403, "blocked")
        placeholders = ",".join(["?"] * len(ACTIVE_STATUSES))
        cur.execute(
            f"""
            SELECT * FROM communication_calls
            WHERE conversation_id=? AND status IN ({placeholders})
            ORDER BY id DESC LIMIT 1
            """,
            (conversation_id, *sorted(ACTIVE_STATUSES)),
        )
        existing = _row(cur.fetchone())
        if existing:
            return _err("An active call already exists for this conversation.", 409, "active_call_exists", call=_serialize_call(cur, existing, int(user_id)))
        now = _now()
        public_id = f"call_{secrets.token_urlsafe(10)}"
        room_name = f"pulsesoc-{public_id}"
        call_scope = str(payload.get("call_scope") or ("direct" if conversation.get("conversation_type") == "direct" else "group")).strip().lower()
        if call_scope not in VALID_CALL_SCOPES:
            call_scope = "direct" if conversation.get("conversation_type") == "direct" else "group"
        metadata = {"recipient_user_ids": recipient_ids, "conversation_type": conversation.get("conversation_type") or "direct"}
        cur.execute(
            """
            INSERT INTO communication_calls
            (public_id, conversation_id, room_name, provider, call_type, call_scope, status, created_by_user_id, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'ringing', ?, ?, ?, ?)
            """,
            (public_id, conversation_id, room_name, selected_provider, call_type, call_scope, int(user_id), _json_dumps(metadata), now, now),
        )
        call_id = _inserted_call_id(cur, public_id)
        cur.execute(
            """
            INSERT INTO communication_call_participants
            (call_id, user_id, role, status, muted_audio, muted_video, joined_at, last_seen_at, device_info_json, created_at, updated_at)
            VALUES (?, ?, 'caller', 'joined', 0, ?, ?, ?, ?, ?, ?)
            """,
            (call_id, int(user_id), 1 if call_type == "audio" else 0, now, now, _json_dumps(payload.get("device_info") or {}), now, now),
        )
        for recipient_id in recipient_ids:
            cur.execute(
                """
                INSERT INTO communication_call_participants
                (call_id, user_id, role, status, muted_audio, muted_video, device_info_json, created_at, updated_at)
                VALUES (?, ?, 'callee', 'ringing', 0, 0, '{}', ?, ?)
                """,
                (call_id, int(recipient_id), now, now),
            )
            _event(cur, call_id, int(recipient_id), "participant_invited", {"call_type": call_type})
        _event(cur, call_id, int(user_id), "call_created", {"room_name": room_name, "call_type": call_type})
        _event(cur, call_id, int(user_id), "ringing_started", {"recipient_user_ids": recipient_ids})
        cur.execute("SELECT * FROM communication_calls WHERE id=? LIMIT 1", (call_id,))
        call = _row(cur.fetchone())
        _emit_call_sync_event(cur, call, "call_started", int(user_id), [int(user_id), *recipient_ids], status="ringing")
        serialized = _serialize_call(cur, call, int(user_id), include_token=True)
        join = serialized.get("join") if isinstance(serialized.get("join"), dict) else {}
        if not join.get("ok") or not join.get("token") or (selected_provider == "livekit" and not join.get("livekit_url")) or (selected_provider == "agora" and not join.get("app_id")):
            _event(
                cur,
                call_id,
                int(user_id),
                f"{selected_provider}_token_failed",
                {
                    "token_status": join.get("status") or "missing_join_payload",
                    "token_error_code": join.get("error_code") or "",
                    "token_trace_id": join.get("trace_id") or join.get("correlation_id") or "",
                },
            )
            _transition(cur, call, "failed", int(user_id), f"{selected_provider}_token_failed")
            _emit_call_sync_event(cur, _get_call(cur, public_id), "call_failed", int(user_id), [int(user_id), *recipient_ids], status="failed", reason=f"{selected_provider}_token_failed")
            conn.commit()
            return _err(
                "Call token could not be generated.",
                503,
                f"{selected_provider}_token_failed",
                call=_serialize_call(cur, _get_call(cur, public_id), int(user_id)),
                provider=selected_provider,
                error_overrides={
                    "token_status": join.get("status") or "missing_join_payload",
                    "provider": selected_provider,
                },
            )
        caller_name = comm_service._user_summary(cur, int(user_id)).get("display_name") or "Someone"
        notifications = _notify_incoming_call(cur, call, int(user_id), recipient_ids, caller_name)
        _event(cur, call_id, int(user_id), "call_start_response_ready", {"notifications": len(notifications), "join_token": True})
        conn.commit()
        serialized["notifications"] = notifications
        return _ok({"call": serialized, **serialized})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def join_token(user_id: int, call_ref: str | int) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call, participant, denied = _require_call_access(cur, int(user_id), call_ref)
        if denied:
            return denied
        if str(call.get("status") or "") in FINAL_STATUSES:
            return _err("This call has ended.", 409, "call_final")
        token = _generate_rtc_token(
            str(call.get("provider") or "livekit"),
            call.get("room_name") or "",
            int(user_id),
            call.get("call_type") or "audio",
            str(participant.get("role") or "member"),
        )
        if not token.get("ok"):
            return token
        now = _now()
        cur.execute(
            "UPDATE communication_call_participants SET status='joined', joined_at=COALESCE(NULLIF(joined_at,''), ?), last_seen_at=?, updated_at=? WHERE call_id=? AND user_id=?",
            (now, now, now, int(call["id"]), int(user_id)),
        )
        if str(call.get("status") or "") in {"ringing", "accepted"}:
            _transition(cur, call, "connecting", int(user_id), "participant_joining")
        _event(cur, int(call["id"]), int(user_id), "joined", {"token_issued": True})
        conn.commit()
        return _ok({"call": _serialize_call(cur, _get_call(cur, call_ref), int(user_id)), "join": token})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def accept_call(user_id: int, call_ref: str | int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call, participant, denied = _require_call_access(cur, int(user_id), call_ref)
        if denied:
            return denied
        if str(call.get("status") or "") in FINAL_STATUSES:
            return _err("This call has ended.", 409, "call_final")
        now = _now()
        cur.execute(
            "UPDATE communication_call_participants SET status='joined', joined_at=COALESCE(NULLIF(joined_at,''), ?), last_seen_at=?, device_info_json=?, updated_at=? WHERE call_id=? AND user_id=?",
            (now, now, _json_dumps((payload or {}).get("device_info") or {}), now, int(call["id"]), int(user_id)),
        )
        transition = _transition(cur, call, "accepted", int(user_id), "accepted")
        if not transition.get("ok"):
            return transition
        _event(cur, int(call["id"]), int(user_id), "accepted", {})
        refreshed = _get_call(cur, call_ref)
        _emit_call_sync_event(cur, refreshed, "call_accepted", int(user_id), status="accepted")
        token = _generate_rtc_token(
            str(refreshed.get("provider") or "livekit"),
            refreshed.get("room_name") or "",
            int(user_id),
            refreshed.get("call_type") or "audio",
            str(participant.get("role") or "member"),
        )
        if not token.get("ok"):
            return token
        _transition(cur, refreshed, "connecting", int(user_id), "accepted_joining")
        conn.commit()
        return _ok({"call": _serialize_call(cur, _get_call(cur, call_ref), int(user_id)), "join": token})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_ring_seen(user_id: int, call_ref: str | int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call, participant, denied = _require_call_access(cur, int(user_id), call_ref)
        if denied:
            return denied
        if str(call.get("status") or "") in FINAL_STATUSES:
            return _err("This call has ended.", 409, "call_final")
        if str(participant.get("role") or "") != "callee":
            return _err("Only the recipient can acknowledge incoming ringing.", 403, "not_callee")
        now = _now()
        cur.execute(
            """
            UPDATE communication_call_participants
            SET last_seen_at=?, device_info_json=?, updated_at=?
            WHERE call_id=? AND user_id=?
            """,
            (now, _json_dumps((payload or {}).get("device_info") or {}), now, int(call["id"]), int(user_id)),
        )
        _event(cur, int(call["id"]), int(user_id), "incoming_call_overlay_opened", payload or {})
        conn.commit()
        return _ok({"call": _serialize_call(cur, _get_call(cur, call_ref), int(user_id)), "message": "Incoming call ring acknowledged."})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def decline_call(user_id: int, call_ref: str | int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call, participant, denied = _require_call_access(cur, int(user_id), call_ref)
        if denied:
            return denied
        now = _now()
        cur.execute("UPDATE communication_call_participants SET status='declined', left_at=?, updated_at=? WHERE call_id=? AND user_id=?", (now, now, int(call["id"]), int(user_id)))
        _event(cur, int(call["id"]), int(user_id), "declined", payload or {})
        cur.execute("SELECT COUNT(*) AS active FROM communication_call_participants WHERE call_id=? AND status IN ('joined','ringing')", (int(call["id"]),))
        active = int(_row(cur.fetchone()).get("active") or 0)
        if active <= 1:
            _transition(cur, call, "declined", int(user_id), "declined")
        _emit_call_sync_event(cur, _get_call(cur, call_ref), "call_declined", int(user_id), status="declined")
        conn.commit()
        return _ok({"call": _serialize_call(cur, _get_call(cur, call_ref), int(user_id))})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def end_call(user_id: int, call_ref: str | int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call, participant, denied = _require_call_access(cur, int(user_id), call_ref)
        if denied:
            return denied
        now = _now()
        cur.execute("UPDATE communication_call_participants SET status='left', left_at=?, updated_at=? WHERE call_id=? AND user_id=?", (now, now, int(call["id"]), int(user_id)))
        _event(cur, int(call["id"]), int(user_id), "left", payload or {})
        cur.execute("SELECT COUNT(*) AS active FROM communication_call_participants WHERE call_id=? AND status IN ('joined','ringing')", (int(call["id"]),))
        active = int(_row(cur.fetchone()).get("active") or 0)
        if active == 0 or int(call.get("created_by_user_id") or 0) == int(user_id):
            _transition(cur, call, "ended", int(user_id), (payload or {}).get("reason") or "ended_by_participant")
        _emit_call_sync_event(cur, _get_call(cur, call_ref), "call_ended", int(user_id), status="ended", reason=(payload or {}).get("reason") or "ended_by_participant")
        conn.commit()
        return _ok({"call": _serialize_call(cur, _get_call(cur, call_ref), int(user_id))})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def call_status(user_id: int, call_ref: str | int) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        changed = _mark_missed_stale_calls_cur(cur) + _expire_stale_active_calls_cur(cur)
        if changed:
            conn.commit()
        call = _get_call(cur, call_ref)
        if not call:
            return _err("Call not found.", 404, "missing_call")
        if not _participant_allowed(cur, int(call.get("conversation_id") or 0), int(user_id)):
            return _err("You do not have access to this call.", 403, "forbidden")
        return _ok({"call": _serialize_call(cur, call, int(user_id))})
    finally:
        conn.close()


def active_calls(user_id: int) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        missed = _mark_missed_stale_calls_cur(cur)
        expired = _expire_stale_active_calls_cur(cur)
        if missed or expired:
            conn.commit()
        placeholders = ",".join(["?"] * len(ACTIVE_STATUSES))
        cur.execute(
            f"""
            SELECT c.*
            FROM communication_calls c
            JOIN communication_call_participants p ON p.call_id=c.id
            WHERE p.user_id=?
              AND p.status IN ('joined','ringing')
              AND c.status IN ({placeholders})
            ORDER BY c.id DESC
            """,
            (int(user_id), *sorted(ACTIVE_STATUSES)),
        )
        return _ok({"calls": [_serialize_call(cur, dict(row), int(user_id)) for row in cur.fetchall()], "missed_marked": missed, "expired_marked": expired})
    finally:
        conn.close()


def submit_quality_report(user_id: int, call_ref: str | int, payload: dict[str, Any]) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call = _get_call(cur, call_ref)
        if not call:
            return _err("Call not found.", 404, "missing_call")
        if not _participant_allowed(cur, int(call.get("conversation_id") or 0), int(user_id)):
            return _err("You do not have access to this call.", 403, "forbidden")
        cur.execute(
            """
            INSERT INTO communication_call_quality_reports
            (call_id, user_id, latency_ms, jitter_ms, packet_loss, bitrate_audio, bitrate_video, fps, resolution, network_type, device_info_json, quality_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(call["id"]),
                int(user_id),
                int(payload.get("latency_ms") or 0),
                int(payload.get("jitter_ms") or 0),
                float(payload.get("packet_loss") or 0),
                int(payload.get("bitrate_audio") or 0),
                int(payload.get("bitrate_video") or 0),
                float(payload.get("fps") or 0),
                str(payload.get("resolution") or "")[:80],
                str(payload.get("network_type") or "")[:80],
                _json_dumps(payload.get("device_info") or {}),
                float(payload.get("quality_score") or 0),
                _now(),
            ),
        )
        _event(cur, int(call["id"]), int(user_id), "quality_report", {"quality_score": payload.get("quality_score")})
        conn.commit()
        return _ok({"message": "Quality report saved."})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_connected(user_id: int, call_ref: str | int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call, participant, denied = _require_call_access(cur, int(user_id), call_ref)
        if denied:
            return denied
        if str(call.get("status") or "") in FINAL_STATUSES:
            return _err("This call has ended.", 409, "call_final")
        now = _now()
        cur.execute(
            "UPDATE communication_call_participants SET status='joined', last_seen_at=?, device_info_json=?, updated_at=? WHERE call_id=? AND user_id=?",
            (now, _json_dumps((payload or {}).get("device_info") or {}), now, int(call["id"]), int(user_id)),
        )
        refreshed = call
        if str(refreshed.get("status") or "") in {"ringing", "accepted"}:
            refreshed = _safe_transition(cur, refreshed, "connecting", int(user_id), "client_joined_room")
        if str(refreshed.get("status") or "") == "connecting":
            refreshed = _safe_transition(cur, refreshed, "connected", int(user_id), "client_connected")
        _event(cur, int(call["id"]), int(user_id), "client_connected", payload or {})
        conn.commit()
        return _ok({"call": _serialize_call(cur, _get_call(cur, call_ref), int(user_id))})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def call_events(user_id: int, call_ref: str | int) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call, participant, denied = _require_call_access(cur, int(user_id), call_ref)
        if denied:
            return denied
        cur.execute(
            "SELECT id, user_id, event_type, event_payload_json, created_at FROM communication_call_events WHERE call_id=? ORDER BY id ASC LIMIT 200",
            (int(call["id"]),),
        )
        events = []
        for row in cur.fetchall():
            item = dict(row)
            item["event_payload"] = json.loads(item.pop("event_payload_json") or "{}")
            events.append(item)
        return _ok({"call": _serialize_call(cur, call, int(user_id)), "events": events})
    finally:
        conn.close()


def conversation_calls(user_id: int, conversation_ref: str | int, limit: int = 40) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        conversation, access = comm_service._conversation_access(cur, int(user_id), conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403, access)
        conversation_id = int(conversation["id"])
        cur.execute(
            """
            SELECT * FROM communication_calls
            WHERE conversation_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (conversation_id, max(1, min(int(limit or 40), 100))),
        )
        return _ok({"conversation_id": conversation_id, "calls": [_serialize_call(cur, dict(row), int(user_id)) for row in cur.fetchall()]})
    finally:
        conn.close()


def update_participant_control(user_id: int, call_ref: str | int, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    action = str(action or "").strip().lower()
    control_map = {
        "mute-audio": ("muted_audio", 1, "muted_audio"),
        "unmute-audio": ("muted_audio", 0, "unmuted_audio"),
        "enable-video": ("muted_video", 0, "video_enabled"),
        "disable-video": ("muted_video", 1, "video_disabled"),
        "screen-share-start": ("screen_sharing", 1, "screen_share_started"),
        "screen-share-stop": ("screen_sharing", 0, "screen_share_stopped"),
        "switch-camera": (None, None, "camera_switched"),
        "speaker": (None, None, "speaker_changed"),
        "minimize": (None, None, "call_minimized"),
        "restore": (None, None, "call_restored"),
        "visibility": (None, None, "client_visibility_changed"),
    }
    if action not in control_map:
        return _err("Unsupported call control.", 400, "unsupported_control")
    conn, cur = _open_db()
    try:
        call, participant, denied = _require_call_access(cur, int(user_id), call_ref)
        if denied:
            return denied
        if str(call.get("status") or "") in FINAL_STATUSES:
            return _err("This call has ended.", 409, "call_final")
        column, value, event_type = control_map[action]
        now = _now()
        if column:
            cur.execute(
                f"UPDATE communication_call_participants SET {column}=?, last_seen_at=?, updated_at=? WHERE call_id=? AND user_id=?",
                (int(value), now, now, int(call["id"]), int(user_id)),
            )
        else:
            cur.execute(
                "UPDATE communication_call_participants SET last_seen_at=?, updated_at=? WHERE call_id=? AND user_id=?",
                (now, now, int(call["id"]), int(user_id)),
            )
        _event(cur, int(call["id"]), int(user_id), event_type, payload or {})
        conn.commit()
        return _ok({"call": _serialize_call(cur, _get_call(cur, call_ref), int(user_id)), "control": action})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def livekit_webhook(headers: dict[str, Any], raw_body: bytes, payload: dict[str, Any]) -> dict[str, Any]:
    secret = os.getenv("LIVEKIT_WEBHOOK_SECRET", "").strip()
    if not secret:
        return _err("LIVEKIT_WEBHOOK_SECRET is not configured; webhook processing is disabled.", 503, "config_missing")
    provided = str(headers.get("X-LiveKit-Signature") or headers.get("X-PulseSoc-Signature") or headers.get("Authorization") or "").strip()
    digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).hexdigest()
    accepted = {digest, f"sha256={digest}", f"Bearer {digest}"}
    if provided not in accepted:
        return _err("LiveKit webhook signature could not be verified.", 403, "forbidden")
    room_name = str(payload.get("room", {}).get("name") if isinstance(payload.get("room"), dict) else payload.get("room_name") or "").strip()
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM communication_calls WHERE room_name=? LIMIT 1", (room_name,))
        call = _row(cur.fetchone())
        call_id = int(call.get("id") or 0)
        _event(cur, call_id, 0, "provider_webhook_received", {"room_name": room_name, "event": payload.get("event") or payload.get("type")})
        if call and str(payload.get("event") or payload.get("type") or "").lower() in {"room_finished", "room_ended"}:
            _transition(cur, call, "ended", 0, "provider_room_ended")
        conn.commit()
        return _ok({"message": "Webhook recorded.", "call_id": call_id})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recent_calls(limit: int = 40) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM communication_calls ORDER BY id DESC LIMIT ?", (max(1, min(int(limit or 40), 100)),))
        return _ok({"calls": [_serialize_call(cur, dict(row), 0) for row in cur.fetchall()], "livekit": livekit_config_status()})
    finally:
        conn.close()


def _decode_payload(value: Any) -> dict[str, Any]:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _decode_quality_report(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row or {})
    item["device_info"] = _decode_payload(item.get("device_info_json") or "{}")
    return item


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _quality_summary(quality: list[dict[str, Any]]) -> dict[str, Any]:
    latest = quality[0] if quality else {}
    livekit = (latest.get("device_info") or {}).get("livekit_quality") or {}
    capture_width = _safe_int(livekit.get("capture_width"))
    capture_height = _safe_int(livekit.get("capture_height"))
    rendered_width = _safe_int(livekit.get("rendered_width"))
    rendered_height = _safe_int(livekit.get("rendered_height"))
    return {
        "has_reports": bool(quality),
        "latest_report_at": latest.get("created_at") or "",
        "quality_score": latest.get("quality_score") or 0,
        "resolution": latest.get("resolution") or "",
        "capture_resolution": livekit.get("capture_resolution") or (f"{capture_width}x{capture_height}" if capture_width and capture_height else ""),
        "rendered_resolution": livekit.get("rendered_resolution") or (f"{rendered_width}x{rendered_height}" if rendered_width and rendered_height else ""),
        "capture_fps": livekit.get("capture_fps") or 0,
        "fps": latest.get("fps") or max(_safe_float(livekit.get("inbound_fps")), _safe_float(livekit.get("outbound_fps"))),
        "video_bitrate_kbps": latest.get("bitrate_video") or max(_safe_int(livekit.get("inbound_bitrate_kbps")), _safe_int(livekit.get("outbound_bitrate_kbps"))),
        "latency_ms": latest.get("latency_ms") or livekit.get("rtt_ms") or 0,
        "jitter_ms": latest.get("jitter_ms") or livekit.get("jitter_ms") or 0,
        "packet_loss": latest.get("packet_loss") or livekit.get("packet_loss") or 0,
        "codec": livekit.get("codec") or "",
        "remote_quality_intent": livekit.get("remote_quality_intent") or "",
        "hd_capture_observed": capture_width >= 1280 and capture_height >= 720,
        "hd_render_observed": rendered_width >= 1280 and rendered_height >= 720,
    }


def _call_last_error(cur: Any, call_id: int) -> str:
    cur.execute(
        """
        SELECT event_type, event_payload_json
        FROM communication_call_events
        WHERE call_id=? AND (event_type LIKE '%failed%' OR event_type LIKE '%error%' OR event_payload_json LIKE '%error%')
        ORDER BY id DESC LIMIT 1
        """,
        (int(call_id),),
    )
    row = _row(cur.fetchone())
    if not row:
        return ""
    payload = _decode_payload(row.get("event_payload_json"))
    return str(payload.get("error") or payload.get("message") or row.get("event_type") or "")[:220]


def _serialize_admin_call(cur: Any, call: dict[str, Any]) -> dict[str, Any]:
    data = _serialize_call(cur, call, 0)
    participants = data.get("participants") or []
    caller = next((item for item in participants if str(item.get("role") or "") == "caller"), None)
    callees = [item for item in participants if str(item.get("role") or "") == "callee"]
    data["caller"] = caller or {}
    data["callees"] = callees
    data["last_error"] = _call_last_error(cur, int(call.get("id") or 0))
    return data


def admin_calls_list(kind: str = "recent", limit: int = 60) -> dict[str, Any]:
    kind = str(kind or "recent").lower()
    limit_value = max(1, min(int(limit or 60), 200))
    status_map = {
        "active": tuple(sorted(ACTIVE_STATUSES)),
        "failed": ("failed",),
        "missed": ("missed",),
    }
    conn, cur = _open_db()
    try:
        if kind in status_map:
            statuses = status_map[kind]
            placeholders = ",".join(["?"] * len(statuses))
            cur.execute(
                f"SELECT * FROM communication_calls WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                (*statuses, limit_value),
            )
        else:
            cur.execute("SELECT * FROM communication_calls ORDER BY id DESC LIMIT ?", (limit_value,))
        return _ok({"kind": kind, "calls": [_serialize_admin_call(cur, dict(row)) for row in cur.fetchall()], "livekit": livekit_config_status()})
    finally:
        conn.close()


def calls_dashboard_summary() -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")

        def count(where: str = "", params: tuple[Any, ...] = ()) -> int:
            cur.execute(f"SELECT COUNT(*) AS total FROM communication_calls {where}", params)
            return int(_row(cur.fetchone()).get("total") or 0)

        active_total = count(f"WHERE status IN ({','.join(['?'] * len(ACTIVE_STATUSES))})", tuple(sorted(ACTIVE_STATUSES)))
        calls_today = count("WHERE created_at>=?", (day_start,))
        failed_total = count("WHERE status='failed'")
        missed_total = count("WHERE status='missed'")
        cur.execute("SELECT AVG(duration_seconds) AS avg_duration FROM communication_calls WHERE COALESCE(duration_seconds,0)>0")
        avg_duration = int(float(_row(cur.fetchone()).get("avg_duration") or 0))
        cur.execute("SELECT AVG(quality_score) AS avg_quality FROM communication_call_quality_reports")
        avg_quality = round(float(_row(cur.fetchone()).get("avg_quality") or 0), 2)
        cur.execute(
            """
            SELECT event_type, event_payload_json, created_at
            FROM communication_call_events
            WHERE event_type LIKE '%failed%' OR event_type LIKE '%error%' OR event_payload_json LIKE '%error%'
            ORDER BY id DESC LIMIT 1
            """
        )
        error_row = _row(cur.fetchone())
        error_payload = _decode_payload(error_row.get("event_payload_json"))
        last_error = {
            "event_type": error_row.get("event_type") or "",
            "message": str(error_payload.get("error") or error_payload.get("message") or "")[:220],
            "created_at": error_row.get("created_at") or "",
        } if error_row else {}
        delivery_counts: dict[str, int] = {}
        try:
            cur.execute(
                """
                SELECT COALESCE(j.status,'unknown') AS status, COUNT(*) AS total
                FROM notification_delivery_jobs j
                JOIN notifications n ON n.id=j.notification_id
                WHERE COALESCE(n.source_type,'') IN ('communication_call','call')
                GROUP BY COALESCE(j.status,'unknown')
                """
            )
            delivery_counts = {str(row["status"]): int(row["total"] or 0) for row in cur.fetchall()}
        except Exception:
            delivery_counts = {}
        return _ok({
            "summary": {
                "livekit_config": "Configured" if livekit_config_status().get("configured") else "Missing",
                "active_calls": active_total,
                "calls_today": calls_today,
                "failed_calls": failed_total,
                "missed_calls": missed_total,
                "average_duration_seconds": avg_duration,
                "average_quality": avg_quality,
                "notification_delivery": delivery_counts,
                "last_error": last_error,
            },
            "livekit": livekit_config_status(),
        })
    finally:
        conn.close()


def call_timeline(call_ref: str | int) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call = _get_call(cur, call_ref)
        if not call:
            return _err("Call not found.", 404, "missing_call")
        cur.execute("SELECT * FROM communication_call_events WHERE call_id=? ORDER BY id ASC LIMIT 300", (int(call["id"]),))
        events = []
        for row in cur.fetchall():
            item = dict(row)
            item["event_payload"] = _decode_payload(item.pop("event_payload_json", "") or "")
            events.append(item)
        return _ok({"call": _serialize_admin_call(cur, call), "events": events, "livekit": livekit_config_status()})
    finally:
        conn.close()


def call_inspector(call_ref: str | int) -> dict[str, Any]:
    detail = admin_call_detail(call_ref)
    if not detail.get("ok"):
        return detail
    delivery = call_delivery_diagnostics(call_ref)
    timeline = call_timeline(call_ref)
    detail["delivery"] = delivery if delivery.get("ok") else {}
    detail["timeline"] = timeline.get("events") if timeline.get("ok") else []
    return detail


def admin_force_end_call(call_ref: str | int, admin_user_id: int = 0, reason: str = "admin_force_end") -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call = _get_call(cur, call_ref)
        if not call:
            return _err("Call not found.", 404, "missing_call")
        if str(call.get("status") or "") in FINAL_STATUSES:
            return _ok({"message": "Call was already final.", "call": _serialize_admin_call(cur, call)})
        updated = _transition(cur, call, "ended", int(admin_user_id or 0), reason or "admin_force_end")
        if not updated.get("ok"):
            return updated
        now = _now()
        cur.execute(
            """
            UPDATE communication_call_participants
            SET status=CASE WHEN status IN ('joined','ringing','invited') THEN 'left' ELSE status END,
                left_at=COALESCE(NULLIF(left_at,''), ?),
                updated_at=?
            WHERE call_id=?
            """,
            (now, now, int(call["id"])),
        )
        _event(cur, int(call["id"]), int(admin_user_id or 0), "admin_force_end", {"reason": reason or "admin_force_end"})
        conn.commit()
        return _ok({"message": "Call ended by admin.", "call": _serialize_admin_call(cur, _get_call(cur, call_ref))})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def call_delivery_diagnostics(call_ref: str | int) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call = _get_call(cur, call_ref)
        if not call:
            return _err("Call not found.", 404, "missing_call")
        try:
            pulsesoc_notification_system.ensure_schema(conn)
        except Exception:
            logging.warning("PULSESOC_CALL_DELIVERY_NOTIFICATION_SCHEMA_CHECK_FAILED call_ref=%s", call_ref)
        call_id = int(call.get("id") or 0)
        public_id = str(call.get("public_id") or call_id)
        conversation_id = int(call.get("conversation_id") or 0)
        cur.execute("SELECT * FROM communication_call_participants WHERE call_id=? ORDER BY id ASC", (call_id,))
        participants = [dict(row) for row in cur.fetchall()]
        caller_id = int(call.get("created_by_user_id") or 0)
        callees = [item for item in participants if str(item.get("role") or "") == "callee"]
        cur.execute(
            """
            SELECT * FROM notifications
            WHERE source_id IN (?, ?) AND type IN ('incoming_call','missed_call')
              AND COALESCE(source_type,'') IN ('communication_call','call')
            ORDER BY id DESC
            """,
            (public_id, str(call_id)),
        )
        notifications = [dict(row) for row in cur.fetchall()]
        notification_ids = [int(item.get("id") or 0) for item in notifications if int(item.get("id") or 0)]
        delivery_jobs: list[dict[str, Any]] = []
        if notification_ids:
            placeholders = ",".join(["?"] * len(notification_ids))
            cur.execute(
                f"SELECT * FROM notification_delivery_jobs WHERE notification_id IN ({placeholders}) ORDER BY id DESC",
                tuple(notification_ids),
            )
            delivery_jobs = [dict(row) for row in cur.fetchall()]
        device_status: dict[str, Any] = {}
        for participant in callees:
            recipient_id = int(participant.get("user_id") or 0)
            policy = comm_service._participant_push_policy(cur, recipient_id, caller_id, conversation_id)
            counts = {"notification_device_tokens": 0, "push_subscriptions": 0}
            try:
                cur.execute("SELECT COUNT(*) AS total FROM notification_device_tokens WHERE user_id=? AND enabled=1 AND deleted_at IS NULL", (recipient_id,))
                counts["notification_device_tokens"] = int(_row(cur.fetchone()).get("total") or 0)
            except Exception:
                counts["notification_device_tokens"] = 0
            try:
                cur.execute("SELECT COUNT(*) AS total FROM push_subscriptions WHERE user_id=? AND COALESCE(is_active, active, 1)=1", (recipient_id,))
                counts["push_subscriptions"] = int(_row(cur.fetchone()).get("total") or 0)
            except Exception:
                counts["push_subscriptions"] = 0
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM communication_call_events
                WHERE call_id=? AND user_id=? AND event_type='incoming_call_overlay_opened'
                """,
                (call_id, recipient_id),
            )
            overlay_opened = int(_row(cur.fetchone()).get("total") or 0) > 0
            presence = _recipient_online_state(cur, recipient_id)
            recipient_jobs = [job for job in delivery_jobs if int(job.get("recipient_user_id") or job.get("user_id") or 0) == recipient_id]
            recipient_notifications = [note for note in notifications if int(note.get("recipient_user_id") or note.get("user_id") or 0) == recipient_id]
            device_status[str(recipient_id)] = {
                "recipient_user_id": recipient_id,
                "participant_created": True,
                "participant_status": participant.get("status") or "",
                "recipient_online": presence,
                "recipient_overlay_opened": overlay_opened,
                "incoming_notification_created": any((note.get("type") or note.get("notification_type")) == "incoming_call" for note in recipient_notifications),
                "missed_notification_created": any((note.get("type") or note.get("notification_type")) == "missed_call" for note in recipient_notifications),
                "push_job_created": any((job.get("channel") == "push") for job in recipient_jobs),
                "call_job_created": any((job.get("channel") == "call") for job in recipient_jobs),
                "push_job_statuses": [
                    {
                        "id": int(job.get("id") or 0),
                        "channel": job.get("channel") or "",
                        "status": job.get("status") or "",
                        "provider": job.get("provider") or "",
                        "failed_reason": job.get("failed_reason") or job.get("failure_reason") or "",
                    }
                    for job in recipient_jobs
                ],
                "recipient_push_token_exists": bool(counts["notification_device_tokens"] or counts["push_subscriptions"]),
                "recipient_device_counts": counts,
                "recipient_muted_conversation": bool(policy.get("suppress_push")),
                "recipient_blocked_caller": policy.get("reason") == "blocked",
                "recipient_policy": policy,
            }
        cur.execute(
            "SELECT event_type, event_payload_json, user_id, created_at FROM communication_call_events WHERE call_id=? ORDER BY id DESC LIMIT 40",
            (call_id,),
        )
        events = []
        last_error = ""
        realtime_emitted = False
        realtime_failed = False
        for row in cur.fetchall():
            item = dict(row)
            payload = json.loads(item.pop("event_payload_json") or "{}")
            item["event_payload"] = payload
            if item.get("event_type") == "incoming_call_realtime_emitted":
                realtime_emitted = True
            if item.get("event_type") == "incoming_call_realtime_failed":
                realtime_failed = True
            if not last_error and ("failed" in str(item.get("event_type") or "") or payload.get("error")):
                last_error = str(payload.get("error") or item.get("event_type") or "")[:200]
            events.append(item)
        return _ok({
            "call": _serialize_call(cur, call, 0),
            "diagnostics": {
                "call_created": True,
                "caller_participant": any(int(item.get("user_id") or 0) == caller_id and str(item.get("role") or "") == "caller" for item in participants),
                "callee_participants": len(callees),
                "incoming_notification_created": any((note.get("type") or note.get("notification_type")) == "incoming_call" for note in notifications),
                "push_job_created": any(job.get("channel") == "push" for job in delivery_jobs),
                "call_job_created": any(job.get("channel") == "call" for job in delivery_jobs),
                "livekit_configured": bool(livekit_config_status().get("configured")),
                "realtime_event_emitted": realtime_emitted,
                "realtime_event_failed": realtime_failed,
                "recipient_online": any(bool(item.get("recipient_online", {}).get("online")) for item in device_status.values()),
                "recipient_overlay_opened": any(bool(item.get("recipient_overlay_opened")) for item in device_status.values()),
                "media_tracks_published": "provider_event_required",
                "last_call_error": last_error,
            },
            "recipient_delivery": device_status,
            "notifications": notifications,
            "delivery_jobs": delivery_jobs,
            "events": events,
            "livekit": livekit_config_status(),
        })
    finally:
        conn.close()


def admin_call_detail(call_ref: str | int) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call = _get_call(cur, call_ref)
        if not call:
            return _err("Call not found.", 404, "missing_call")
        cur.execute("SELECT * FROM communication_call_events WHERE call_id=? ORDER BY id DESC LIMIT 50", (int(call["id"]),))
        events = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM communication_call_quality_reports WHERE call_id=? ORDER BY id DESC LIMIT 50", (int(call["id"]),))
        quality = [_decode_quality_report(dict(row)) for row in cur.fetchall()]
        return _ok({
            "call": _serialize_call(cur, call, 0),
            "events": events,
            "quality_reports": quality,
            "quality_summary": _quality_summary(quality),
            "livekit": livekit_config_status(),
            "hd_quality_policy": livekit_hd_quality_policy(),
        })
    finally:
        conn.close()


def test_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    status = livekit_config_status()
    missing = list(status.get("missing") or [])
    base = {
        "provider": "livekit",
        "configured": bool(status.get("configured")),
        "url_present": bool(status.get("url_configured")),
        "api_key_present": "LIVEKIT_API_KEY" not in missing,
        "api_secret_present": "LIVEKIT_API_SECRET" not in missing,
        "webhook_secret_present": bool(status.get("webhook_secret_configured")),
        "turn_present": bool(status.get("turn_configured")),
        "stun_present": bool(status.get("stun_configured")),
        "missing": missing,
        "safe_mode": "" if status.get("configured") else "config_missing",
        "provider_ready": bool(status.get("configured")),
        "livekit": status,
        "hd_quality_policy": livekit_hd_quality_policy(),
    }
    if not status.get("configured"):
        error = _err(
            "Calling provider is not configured.",
            200,
            "config_missing",
            error_overrides={"missing": missing, "provider": "livekit"},
        )
        return {
            **error,
            "can_generate_token": False,
            "can_create_test_room": False,
            "can_cleanup_test_room": False,
            **base,
        }

    room_name = f"pulsesoc-config-token-{secrets.token_hex(6)}"
    token = _generate_livekit_token(room_name, 0, "audio")
    connectivity = _livekit_room_connectivity_check()
    return _ok({
        **base,
        "can_generate_token": bool(token.get("ok") and token.get("token")),
        "can_create_test_room": bool(connectivity.get("can_create_test_room")),
        "can_cleanup_test_room": bool(connectivity.get("can_cleanup_test_room")),
        "provider_error": connectivity.get("provider_error") or "",
        "room_check": {
            "attempted": True,
            "created": bool(connectivity.get("can_create_test_room")),
            "cleaned_up": bool(connectivity.get("can_cleanup_test_room")),
        },
    })


def admin_livekit_quality_test(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    config = test_config(payload or {})
    policy = livekit_hd_quality_policy()
    configured = bool(config.get("configured"))
    can_generate = bool(config.get("can_generate_token"))
    can_create = bool(config.get("can_create_test_room"))
    return _ok({
        "provider": "livekit",
        "configured": configured,
        "provider_ready": bool(configured and can_generate and can_create),
        "can_generate_token": can_generate,
        "can_create_test_room": can_create,
        "can_cleanup_test_room": bool(config.get("can_cleanup_test_room")),
        "provider_error": config.get("provider_error") or "",
        "missing": config.get("missing") or [],
        "hd_quality_policy": policy,
        "browser_quality_test": {
            "requires_browser_camera": True,
            "call_capture_target": policy["video_calls"]["default"],
            "live_capture_preferred": policy["live_hosts"]["preferred"],
            "notes": [
                "Actual capture width, rendered width, fps, bitrate, RTT, jitter, and packet loss are reported by active calls through the quality endpoint.",
                "Mux replay quality must be checked after LiveKit publishes an HD input track.",
            ],
        },
    })


def mark_missed_stale_calls(timeout_seconds: int = 45) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        updated = _mark_missed_stale_calls_cur(cur, timeout_seconds)
        conn.commit()
        return _ok({"missed_calls": updated})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
