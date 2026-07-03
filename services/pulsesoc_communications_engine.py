"""PulseSoc Communications Engine foundation.

This module owns call state, participant validation, LiveKit readiness, and
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
ACTIVE_STATUSES = {"created", "ringing", "connecting", "connected", "reconnecting"}
FINAL_STATUSES = {"ended", "missed", "declined", "failed", "canceled"}
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


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value or {}, separators=(",", ":"), sort_keys=True)
    except Exception:
        return "{}"


def _row(row: Any) -> dict[str, Any]:
    return dict(row or {})


def _err(message: str, status: int = 400, code: str = "error", **extra: Any) -> dict[str, Any]:
    payload = {"ok": False, "status": code, "message": message, "http_status": status, "trace_id": _trace()}
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


def ensure_schema(cur: Any) -> None:
    for sql in CALL_TABLES:
        cur.execute(sql)
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


def _require_livekit() -> dict[str, Any] | None:
    status = livekit_config_status()
    if status["configured"]:
        return None
    logging.info("PULSESOC_CALL_CONFIG_MISSING missing=%s", ",".join(status.get("missing") or []))
    return _err(
        "Calling is temporarily unavailable. Please try again later.",
        503,
        "config_missing",
        provider="livekit",
        livekit=status,
    )


def _generate_livekit_token(room_name: str, user_id: int, call_type: str = "audio") -> dict[str, Any]:
    missing = _require_livekit()
    if missing:
        return missing
    api_key = os.getenv("LIVEKIT_API_KEY", "").strip()
    api_secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
    now = int(time.time())
    grants = {
        "roomJoin": True,
        "room": room_name,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
    }
    if call_type == "audio":
        grants["canPublishSources"] = ["microphone"]
    payload = {
        "iss": api_key,
        "sub": f"user-{int(user_id)}",
        "name": f"PulseSoc member {int(user_id)}",
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
        "livekit_url": os.getenv("LIVEKIT_URL", "").strip(),
        "room_name": room_name,
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
        "duration_seconds": int(call.get("duration_seconds") or 0),
        "end_reason": call.get("end_reason") or "",
        "participants": participants,
        "participant": me,
        "livekit": livekit_config_status(),
    }
    if include_token and user_id:
        payload["join"] = _generate_livekit_token(payload["room_name"], int(user_id), payload["call_type"])
    return payload


def _event(cur: Any, call_id: int, user_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    cur.execute(
        """
        INSERT INTO communication_call_events (call_id, user_id, event_type, event_payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(call_id), int(user_id or 0), str(event_type or ""), _json_dumps(payload), _now()),
    )


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
        updated += 1
    return updated


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
                source_type="call",
                source_id=str(call.get("public_id") or call.get("id") or ""),
                title=f"Incoming PulseSoc {call.get('call_type') or 'audio'} call",
                body=f"{actor_name or 'Someone'} is calling you.",
                preview="Incoming call",
                deep_link=f"/pulse/messages/{conversation_id}?call_id={call.get('public_id') or call.get('id')}",
                metadata={
                    "conversation_id": conversation_id,
                    "call_id": call.get("public_id") or call.get("id"),
                    "call_type": call.get("call_type") or "audio",
                    "sound_key": "call",
                    "vibration": [120, 80, 120, 80, 240],
                },
                category="calls",
                priority="urgent",
                urgency="immediate",
                channels=channels,
                dedupe_key=f"incoming-call:{call.get('public_id') or call.get('id')}:{recipient_id}",
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
                "source_type": "call",
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
        provider_missing = _require_livekit()
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
            VALUES (?, ?, ?, 'livekit', ?, ?, 'ringing', ?, ?, ?, ?)
            """,
            (public_id, conversation_id, room_name, call_type, call_scope, int(user_id), _json_dumps(metadata), now, now),
        )
        call_id = int(cur.lastrowid)
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
        caller_name = comm_service._user_summary(cur, int(user_id)).get("display_name") or "Someone"
        notifications = _notify_incoming_call(cur, call, int(user_id), recipient_ids, caller_name)
        conn.commit()
        serialized = _serialize_call(cur, call, int(user_id), include_token=True)
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
        token = _generate_livekit_token(call.get("room_name") or "", int(user_id), call.get("call_type") or "audio")
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
        token = _generate_livekit_token(refreshed.get("room_name") or "", int(user_id), refreshed.get("call_type") or "audio")
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
        _mark_missed_stale_calls_cur(cur)
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
        if missed:
            conn.commit()
        placeholders = ",".join(["?"] * len(ACTIVE_STATUSES))
        cur.execute(
            f"""
            SELECT c.*
            FROM communication_calls c
            JOIN communication_call_participants p ON p.call_id=c.id
            WHERE p.user_id=? AND c.status IN ({placeholders})
            ORDER BY c.id DESC
            """,
            (int(user_id), *sorted(ACTIVE_STATUSES)),
        )
        return _ok({"calls": [_serialize_call(cur, dict(row), int(user_id)) for row in cur.fetchall()], "missed_marked": missed})
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
        cur.execute(
            f"UPDATE communication_call_participants SET {column}=?, last_seen_at=?, updated_at=? WHERE call_id=? AND user_id=?",
            (int(value), now, now, int(call["id"]), int(user_id)),
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


def admin_call_detail(call_ref: str | int) -> dict[str, Any]:
    conn, cur = _open_db()
    try:
        call = _get_call(cur, call_ref)
        if not call:
            return _err("Call not found.", 404, "missing_call")
        cur.execute("SELECT * FROM communication_call_events WHERE call_id=? ORDER BY id DESC LIMIT 50", (int(call["id"]),))
        events = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM communication_call_quality_reports WHERE call_id=? ORDER BY id DESC LIMIT 50", (int(call["id"]),))
        quality = [dict(row) for row in cur.fetchall()]
        return _ok({"call": _serialize_call(cur, call, 0), "events": events, "quality_reports": quality, "livekit": livekit_config_status()})
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
    }
    if not status.get("configured"):
        return {
            "ok": False,
            "status": "config_missing",
            "message": "Calling is temporarily unavailable. Please try again later.",
            "can_generate_token": False,
            "can_create_test_room": False,
            "can_cleanup_test_room": False,
            "http_status": 200,
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
