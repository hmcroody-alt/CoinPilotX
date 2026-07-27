"""Pulse Communications 2.0 service layer."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from typing import Any

from . import flags, infrastructure, twilio_service
from .models import ensure_schema


DISABLED_MESSAGE = "Pulse Communications 2.0 is not public yet."
ALLOWED_CONVERSATION_TYPES = {"direct", "group", "room", "community_channel"}
ALLOWED_MESSAGE_TYPES = {"text", "image", "gif", "video", "audio", "voice", "file", "media", "system"}
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


def _bot():
    import bot

    return bot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trace() -> str:
    return secrets.token_hex(6)


def _dispatch_command_center_async(method_name: str, *args, **kwargs) -> bool:
    try:
        from services import command_center_client

        if not command_center_client.is_enabled():
            return False
        method = getattr(command_center_client, method_name, None)
        if not callable(method):
            return False

        def run_dispatch():
            try:
                result = method(*args, **kwargs)
                if not result.get("ok"):
                    logging.info(
                        "COMM_V2_COMMAND_CENTER_DISPATCH_FAILED method=%s reason=%s",
                        method_name,
                        result.get("reason") or "unknown",
                    )
            except Exception as exc:
                logging.info("COMM_V2_COMMAND_CENTER_DISPATCH_SKIPPED method=%s error=%s", method_name, exc.__class__.__name__)

        threading.Thread(target=run_dispatch, name=f"comm-v2-{method_name}", daemon=True).start()
        return True
    except Exception as exc:
        logging.info("COMM_V2_COMMAND_CENTER_DISPATCH_UNAVAILABLE method=%s error=%s", method_name, exc.__class__.__name__)
        return False


def _public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def _clean(value: Any, limit: int = 2000) -> str:
    return re.sub(r"<[^>]*>", "", str(value or "")).strip()[:limit]


def _preview_label(message_type: str = "", fallback: str = "Attachment") -> str:
    value = str(message_type or "").strip().lower()
    if value in {"voice", "audio", "voice_note"}:
        return "Voice message"
    if value in {"video", "reel"}:
        return "Video"
    if value in {"image", "photo", "gif"}:
        return "Photo"
    if value in {"file", "document"}:
        return "File"
    if "call" in value:
        return "Missed call"
    if value and value != "text":
        return "Attachment"
    return fallback


def _preview_has_local_path(value: Any) -> bool:
    text = str(value or "")
    if not text:
        return False
    return bool(re.search(r"(?:^|[\s\"'(])(?:file://)?(?:/Users/|/home/|/var/|/private/|/tmp/|[A-Za-z]:\\|\\\\)[^\s\"'<>]+", text, re.I)
                or re.search(r"(?:CoinPilotX|Desktop)[\\/][^\s\"'<>]+", text, re.I))


def _safe_preview(value: Any = "", message_type: str = "", fallback: str = "") -> str:
    label = _preview_label(message_type, fallback or "Attachment")
    text = _clean(value, 240)
    if not text:
        return "" if (str(message_type or "").lower() in {"", "text"} and not fallback) else label
    if _preview_has_local_path(text):
        return label
    return text


def _extract_urls(value: Any) -> list[str]:
    text = str(value or "")
    urls = re.findall(r"https?://[^\s<>\")']+", text, flags=re.I)
    clean_urls: list[str] = []
    seen = set()
    for raw in urls:
        url = raw.rstrip(".,;:!?")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        clean_urls.append(url[:500])
    return clean_urls


def _message_security_classification(body: str) -> dict:
    text = _clean(body, 4000)
    if not text:
        return {"risky": False, "score": 0, "severity": "Low", "reasons": [], "link_scan": {"urls_detected": 0, "domains": [], "flags": []}, "keyword_hits": []}
    try:
        from services.command_center_worker import security_engine

        scored = security_engine.score_event("phishing_link", {"body": text, "message": text})
        reasons = scored.get("reasons") or []
        risky = bool(reasons)
        return {
            "risky": risky,
            "score": int(scored.get("score") or 0) if risky else 0,
            "severity": (scored.get("severity") or "High") if risky else "Low",
            "reasons": reasons,
            "link_scan": scored.get("link_scan") or {"urls_detected": 0, "domains": [], "flags": []},
            "keyword_hits": scored.get("keyword_hits") or [],
        }
    except Exception:
        lowered = text.lower()
        suspicious = any(token in lowered for token in ("connect wallet", "seed phrase", "claim airdrop", "verify wallet", "urgent login"))
        suspicious = suspicious or bool(re.search(r"https?://[^\s]+(?:walletconnect|airdrop|verify|signin|password|bonus|giveaway)", lowered))
        return {"risky": suspicious, "score": 76 if suspicious else 0, "severity": "High" if suspicious else "Low", "reasons": ["suspicious_link"] if suspicious else [], "link_scan": {"urls_detected": len(re.findall(r"https?://", text)), "domains": [], "flags": []}, "keyword_hits": []}


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _safe_int_list(values: Any) -> list[int]:
    out: list[int] = []
    for item in values or []:
        try:
            value = int(item or 0)
        except Exception:
            continue
        if value:
            out.append(value)
    return out


def _row(row) -> dict:
    return dict(row or {})


def _disabled(action: str) -> dict | None:
    if flags.is_enabled():
        return None
    return {"ok": False, "status": "disabled", "message": DISABLED_MESSAGE, "action": action, "enabled": False, "trace_id": _trace()}


def _ok(data: dict | None = None, message: str = "") -> dict:
    payload = {"ok": True, "status": "ready", "enabled": True, "trace_id": _trace()}
    if message:
        payload["message"] = message
    if data:
        payload.update(data)
    return payload


def _err(message: str, status: int = 400, code: str = "error") -> dict:
    return {"ok": False, "status": code, "message": message, "http_status": status, "trace_id": _trace()}


def _open_db():
    bot = _bot()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    _ensure_schema_ready(bot, cur, conn)
    return conn, cur


def _ensure_schema_ready(bot, cur, conn) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        started = datetime.now(timezone.utc)
        ensure_schema(cur)
        _ensure_columns(bot, cur, conn)
        conn.commit()
        _SCHEMA_READY = True
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        logging.info("PULSE_COMM_V2_SCHEMA_READY duration_ms=%s", elapsed_ms)


def _ensure_columns(bot, cur, conn) -> None:
    add_missing = getattr(bot, "add_columns_if_missing", None)
    table_columns = getattr(bot, "migration_table_columns", None)
    if not add_missing:
        return

    def add(cur, table, columns, conn=None):
        existing = table_columns(cur, table) if table_columns else set()
        missing = [(name, definition) for name, definition in columns if name not in existing]
        if missing:
            add_missing(cur, table, missing, conn=conn)

    add(cur, "comm_v2_conversations", [
        ("public_id", "TEXT"),
        ("conversation_type", "TEXT"),
        ("title", "TEXT"),
        ("description", "TEXT"),
        ("owner_user_id", "INTEGER"),
        ("created_by_user_id", "INTEGER"),
        ("direct_key", "TEXT"),
        ("community_id", "INTEGER"),
        ("channel_id", "INTEGER"),
        ("privacy", "TEXT DEFAULT 'private'"),
        ("visibility", "TEXT DEFAULT 'members'"),
        ("status", "TEXT DEFAULT 'active'"),
        ("is_discoverable", "INTEGER DEFAULT 0"),
        ("member_count", "INTEGER DEFAULT 0"),
        ("last_message_id", "INTEGER DEFAULT 0"),
        ("last_message_at", "TEXT"),
        ("last_activity_at", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_participants", [
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("role", "TEXT DEFAULT 'member'"),
        ("membership_state", "TEXT DEFAULT 'active'"),
        ("joined_at", "TEXT"),
        ("left_at", "TEXT"),
        ("muted_until", "TEXT"),
        ("pinned_at", "TEXT"),
        ("pinned_rank", "INTEGER DEFAULT 0"),
        ("notifications_level", "TEXT DEFAULT 'all'"),
        ("last_seen_at", "TEXT"),
        ("last_read_message_id", "INTEGER DEFAULT 0"),
        ("last_read_at", "TEXT"),
        ("unread_count", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_messages", [
        ("public_id", "TEXT"),
        ("conversation_id", "INTEGER"),
        ("sender_user_id", "INTEGER"),
        ("message_type", "TEXT DEFAULT 'text'"),
        ("body", "TEXT"),
        ("reply_to_message_id", "INTEGER DEFAULT 0"),
        ("thread_root_message_id", "INTEGER DEFAULT 0"),
        ("client_message_id", "TEXT"),
        ("delivery_status", "TEXT DEFAULT 'sent'"),
        ("moderation_status", "TEXT DEFAULT 'approved'"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("edited_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_attachments", [
        ("attachment_public_id", "TEXT"),
        ("message_id", "INTEGER"),
        ("conversation_id", "INTEGER"),
        ("media_upload_id", "INTEGER"),
        ("uploader_user_id", "INTEGER"),
        ("media_type", "TEXT"),
        ("storage_provider", "TEXT"),
        ("storage_key", "TEXT"),
        ("url", "TEXT"),
        ("cdn_url", "TEXT"),
        ("playback_url", "TEXT"),
        ("thumbnail_url", "TEXT"),
        ("mime_type", "TEXT"),
        ("file_size", "INTEGER DEFAULT 0"),
        ("file_size_bytes", "INTEGER DEFAULT 0"),
        ("duration_seconds", "REAL DEFAULT 0"),
        ("waveform_json", "TEXT"),
        ("voice_note", "INTEGER DEFAULT 0"),
        ("width", "INTEGER DEFAULT 0"),
        ("height", "INTEGER DEFAULT 0"),
        ("mux_asset_id", "TEXT"),
        ("mux_playback_id", "TEXT"),
        ("mux_status", "TEXT"),
        ("scan_status", "TEXT DEFAULT 'approved'"),
        ("created_at", "TEXT"),
    ], conn=conn)
    add(cur, "chat_media_uploads", [
        ("duration_seconds", "REAL DEFAULT 0"),
        ("waveform_json", "TEXT"),
        ("voice_note", "INTEGER DEFAULT 0"),
    ], conn=conn)
    add(cur, "comm_v2_live_streams", [
        ("public_id", "TEXT"),
        ("conversation_id", "INTEGER"),
        ("creator_user_id", "INTEGER"),
        ("mux_live_stream_id", "TEXT"),
        ("mux_stream_key", "TEXT"),
        ("mux_playback_id", "TEXT"),
        ("mux_live_status", "TEXT"),
        ("mux_recording_asset_id", "TEXT"),
        ("mux_recording_playback_id", "TEXT"),
        ("ingest_url", "TEXT"),
        ("rtmp_url", "TEXT"),
        ("playback_url", "TEXT"),
        ("status", "TEXT DEFAULT 'created'"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("ended_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_message_reactions", [
        ("message_id", "INTEGER"),
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("reaction_type", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_read_receipts", [
        ("message_id", "INTEGER"),
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("delivered_at", "TEXT"),
        ("seen_at", "TEXT"),
        ("read_at", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_user_settings", [
        ("user_id", "INTEGER"),
        ("presence_privacy", "TEXT DEFAULT 'everyone'"),
        ("read_receipts_enabled", "INTEGER DEFAULT 1"),
        ("message_preview_privacy", "TEXT DEFAULT 'show'"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_conversation_settings", [
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("notification_json", "TEXT"),
        ("appearance_json", "TEXT"),
        ("privacy_json", "TEXT"),
        ("media_json", "TEXT"),
        ("accessibility_json", "TEXT"),
        ("productivity_json", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_presence", [
        ("user_id", "INTEGER"),
        ("status", "TEXT DEFAULT 'offline'"),
        ("last_seen_at", "TEXT"),
        ("active_until", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_message_deletions", [
        ("message_id", "INTEGER"),
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_typing", [
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("is_typing", "INTEGER DEFAULT 1"),
        ("expires_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_reports", [
        ("conversation_id", "INTEGER"),
        ("message_id", "INTEGER"),
        ("reporter_user_id", "INTEGER"),
        ("reported_user_id", "INTEGER DEFAULT 0"),
        ("reason", "TEXT"),
        ("status", "TEXT DEFAULT 'open'"),
        ("created_at", "TEXT"),
        ("reviewed_at", "TEXT"),
        ("reviewed_by_admin_id", "INTEGER DEFAULT 0"),
    ], conn=conn)
    add(cur, "comm_v2_blocks", [
        ("blocker_user_id", "INTEGER"),
        ("blocked_user_id", "INTEGER"),
        ("reason", "TEXT"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_moderation_events", [
        ("conversation_id", "INTEGER"),
        ("message_id", "INTEGER"),
        ("actor_user_id", "INTEGER DEFAULT 0"),
        ("admin_user_id", "INTEGER DEFAULT 0"),
        ("target_user_id", "INTEGER DEFAULT 0"),
        ("event_type", "TEXT"),
        ("reason", "TEXT"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_conversation_items", [
        ("public_id", "TEXT"),
        ("conversation_id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("item_type", "TEXT"),
        ("title", "TEXT"),
        ("body", "TEXT"),
        ("status", "TEXT DEFAULT 'active'"),
        ("due_at", "TEXT"),
        ("completed_at", "TEXT"),
        ("metadata_json", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_communities", [
        ("public_id", "TEXT"),
        ("name", "TEXT"),
        ("slug", "TEXT"),
        ("description", "TEXT"),
        ("owner_user_id", "INTEGER"),
        ("privacy", "TEXT DEFAULT 'public'"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)
    add(cur, "comm_v2_channels", [
        ("public_id", "TEXT"),
        ("community_id", "INTEGER"),
        ("conversation_id", "INTEGER DEFAULT 0"),
        ("name", "TEXT"),
        ("slug", "TEXT"),
        ("description", "TEXT"),
        ("channel_type", "TEXT DEFAULT 'text'"),
        ("visibility", "TEXT DEFAULT 'members'"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ], conn=conn)


def ensure_v2_schema(cur) -> tuple[str, ...]:
    return ensure_schema(cur)


def _user_summary(cur, user_id: int) -> dict:
    cur.execute("SELECT user_id, username, display_name, avatar_url FROM users WHERE user_id=? LIMIT 1", (int(user_id),))
    item = _row(cur.fetchone())
    return {
        "user_id": int(item.get("user_id") or user_id or 0),
        "display_name": item.get("display_name") or item.get("username") or f"Member {user_id}",
        "username": item.get("username") or "",
        "avatar_url": item.get("avatar_url") or "",
    }


def _participant_ids(cur, conversation_id: int) -> list[int]:
    cur.execute(
        "SELECT DISTINCT user_id FROM comm_v2_participants WHERE conversation_id=? AND membership_state='active' AND COALESCE(left_at,'')=''",
        (int(conversation_id),),
    )
    return sorted({int(row["user_id"]) for row in cur.fetchall() if int(row["user_id"] or 0)})


def _user_presence_by_ids(cur, user_ids: list[int], viewer_user_id: int = 0) -> dict[int, dict]:
    """Read presence for a set of users from the unified presence service.

    Messenger does not compute presence itself. It previously read
    user_presence.status directly, which was only ever written with 'online'
    and never reset, so every user who had loaded a page once appeared online
    forever. Liveness now comes from services.presence_service, which derives
    it from unexpired heartbeats and applies the viewer's privacy permissions.
    """
    ids = sorted({int(user_id) for user_id in user_ids if int(user_id or 0)})
    if not ids:
        return {}
    try:
        from services import presence_service

        mapping = presence_service.presence_for(cur, int(viewer_user_id or 0), ids)
        return {
            int(uid): {
                "user_id": int(uid),
                "status": item.get("status") or "offline",
                "last_seen_at": item.get("last_seen_at") or "",
                "last_seen_text": item.get("last_seen_text") or "",
                "last_active_at": item.get("last_seen_at") or "",
                "updated_at": item.get("last_seen_at") or "",
                "activity": item.get("activity") or "idle",
                "activity_context": item.get("activity_context") or "",
                "devices": int(item.get("devices") or 0),
                "active_now": bool(item.get("online")),
                "available": True,
            }
            for uid, item in mapping.items()
        }
    except Exception as exc:
        logging.info("COMM_V2_USER_PRESENCE_LOOKUP_SKIPPED error=%s", exc.__class__.__name__)
        # Fail closed: an error must never be rendered as "online".
        return {}


def _settings(cur, user_id: int) -> dict:
    cur.execute("SELECT * FROM comm_v2_user_settings WHERE user_id=? LIMIT 1", (int(user_id),))
    row = _row(cur.fetchone())
    if not row:
        return {"presence_privacy": "everyone", "read_receipts_enabled": 1, "message_preview_privacy": "show"}
    return {
        "presence_privacy": row.get("presence_privacy") or "everyone",
        "read_receipts_enabled": 1 if int(row.get("read_receipts_enabled") or 0) else 0,
        "message_preview_privacy": row.get("message_preview_privacy") or "show",
    }


CONTROL_SETTING_DEFAULTS = {
    "notifications": {
        "mute_choice": "off",
        "sound": "pulse_beam",
        "lock_screen": True,
        "message_preview": True,
        "mentions": True,
        "reactions": True,
        "typing": True,
        "read_receipts": True,
    },
    "appearance": {
        "theme": "dark_galaxy",
        "wallpaper": "deep_space",
        "bubble_color": "cyan",
        "font_size": "medium",
        "density": "balanced",
        "animation_level": "balanced",
        "reduce_particles": False,
        "high_contrast": False,
    },
    "privacy": {
        "read_receipts": True,
        "typing_indicator": True,
        "online_status": True,
        "last_seen": True,
        "message_preview": True,
        "disappearing_messages": "off",
        "privacy_lock": False,
        "hidden_conversation": False,
    },
    "media": {
        "auto_download_photos": True,
        "auto_download_videos": False,
        "auto_download_voice": True,
        "upload_quality": "standard",
        "auto_save_camera": False,
    },
    "accessibility": {
        "large_text": False,
        "reduce_motion": False,
        "high_contrast": False,
        "voice_reader": False,
        "speech_to_text": False,
        "text_to_speech": False,
        "haptic_feedback": True,
    },
    "productivity": {
        "favorite": False,
        "reminder": "off",
    },
}

CONTROL_SETTING_ALLOWED = {
    "notifications": {
        "mute_choice": {"off", "1_hour", "8_hours", "today", "1_week", "forever"},
        "sound": {"pulse_beam", "soft_orbit", "deep_signal", "crystal_ping", "silent"},
        "lock_screen": "bool",
        "message_preview": "bool",
        "mentions": "bool",
        "reactions": "bool",
        "typing": "bool",
        "read_receipts": "bool",
    },
    "appearance": {
        "theme": {"dark_galaxy", "pulse_green", "deep_space", "nebula", "cyber_night", "solar_flame", "ocean_signal", "royal_purple", "haiti_night", "creator_gold"},
        "wallpaper": {"default", "deep_space", "neon_planet", "galaxy_grid", "pulse_horizon", "alien_city", "cosmic_ocean", "aurora_signal", "dark_nebula", "star_tunnel", "minimal_black"},
        "bubble_color": {"cyan", "purple", "rose", "orange", "green", "gold", "blue"},
        "font_size": {"small", "medium", "large", "extra_large"},
        "density": {"compact", "balanced", "relaxed"},
        "animation_level": {"full", "balanced", "reduced", "off"},
        "reduce_particles": "bool",
        "high_contrast": "bool",
    },
    "privacy": {
        "read_receipts": "bool",
        "typing_indicator": "bool",
        "online_status": "bool",
        "last_seen": "bool",
        "message_preview": "bool",
        "disappearing_messages": {"off", "24_hours", "7_days", "30_days"},
        "privacy_lock": "bool",
        "hidden_conversation": "bool",
    },
    "media": {
        "auto_download_photos": "bool",
        "auto_download_videos": "bool",
        "auto_download_voice": "bool",
        "upload_quality": {"standard", "high", "original"},
        "auto_save_camera": "bool",
    },
    "accessibility": {
        "large_text": "bool",
        "reduce_motion": "bool",
        "high_contrast": "bool",
        "voice_reader": "bool",
        "speech_to_text": "bool",
        "text_to_speech": "bool",
        "haptic_feedback": "bool",
    },
    "productivity": {
        "favorite": "bool",
        "reminder": {"off", "today", "tomorrow", "next_week"},
    },
}


def _control_defaults() -> dict:
    return json.loads(json.dumps(CONTROL_SETTING_DEFAULTS))


def _merge_control_settings(row: dict | None = None) -> dict:
    settings = _control_defaults()
    row = row or {}
    for section in CONTROL_SETTING_DEFAULTS:
        stored = _json_loads(row.get(f"{section[:-1] if section == 'notifications' else section}_json"), None)
        if stored is None:
            stored = _json_loads(row.get(f"{section}_json"), {})
        if isinstance(stored, dict):
            settings[section].update({key: value for key, value in stored.items() if key in settings[section]})
    return settings


def _load_conversation_settings(cur, conversation_id: int, user_id: int) -> tuple[dict, dict]:
    cur.execute(
        "SELECT * FROM comm_v2_conversation_settings WHERE conversation_id=? AND user_id=? LIMIT 1",
        (int(conversation_id), int(user_id)),
    )
    row = _row(cur.fetchone())
    return row, _merge_control_settings(row)


def _coerce_control_value(section: str, key: str, value: Any) -> tuple[bool, Any, str]:
    allowed = (CONTROL_SETTING_ALLOWED.get(section) or {}).get(key)
    if not allowed:
        return False, None, "unsupported_setting"
    if allowed == "bool":
        if isinstance(value, str):
            normalized = value.strip().lower() not in {"0", "false", "off", "no", "disabled"}
        else:
            normalized = bool(value)
        return True, normalized, ""
    normalized = _clean(value, 80).lower()
    if normalized not in allowed:
        return False, None, "invalid_value"
    return True, normalized, ""


def _mute_until_for_choice(choice: str) -> tuple[str, str]:
    normalized = str(choice or "off").lower()
    now_dt = datetime.now(timezone.utc)
    if normalized == "off":
        return "", "all"
    if normalized == "1_hour":
        return (now_dt + timedelta(hours=1)).isoformat(timespec="seconds"), "muted"
    if normalized == "8_hours":
        return (now_dt + timedelta(hours=8)).isoformat(timespec="seconds"), "muted"
    if normalized == "today":
        end = now_dt.replace(hour=23, minute=59, second=59, microsecond=0)
        if end <= now_dt:
            end = now_dt + timedelta(hours=8)
        return end.isoformat(timespec="seconds"), "muted"
    if normalized == "1_week":
        return (now_dt + timedelta(days=7)).isoformat(timespec="seconds"), "muted"
    if normalized == "forever":
        return (now_dt + timedelta(days=3650)).isoformat(timespec="seconds"), "muted"
    return "", "all"


def _save_conversation_settings(cur, conversation_id: int, user_id: int, settings: dict) -> None:
    now = _now()
    cur.execute(
        """
        INSERT OR IGNORE INTO comm_v2_conversation_settings
        (conversation_id, user_id, notification_json, appearance_json, privacy_json, media_json, accessibility_json, productivity_json, created_at, updated_at)
        VALUES (?, ?, '{}', '{}', '{}', '{}', '{}', '{}', ?, ?)
        """,
        (int(conversation_id), int(user_id), now, now),
    )
    cur.execute(
        """
        UPDATE comm_v2_conversation_settings
        SET notification_json=?, appearance_json=?, privacy_json=?, media_json=?, accessibility_json=?, productivity_json=?, updated_at=?
        WHERE conversation_id=? AND user_id=?
        """,
        (
            json.dumps(settings.get("notifications") or {}, default=str)[:6000],
            json.dumps(settings.get("appearance") or {}, default=str)[:6000],
            json.dumps(settings.get("privacy") or {}, default=str)[:6000],
            json.dumps(settings.get("media") or {}, default=str)[:6000],
            json.dumps(settings.get("accessibility") or {}, default=str)[:6000],
            json.dumps(settings.get("productivity") or {}, default=str)[:6000],
            now,
            int(conversation_id),
            int(user_id),
        ),
    )


def _touch_presence(cur, user_id: int, status: str = "online") -> dict:
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    active_until = (now_dt + timedelta(seconds=90)).isoformat(timespec="seconds")
    normalized = "online" if status in {"online", "active", "active_now"} else "offline"
    cur.execute(
        """
        INSERT OR IGNORE INTO comm_v2_presence (user_id, status, last_seen_at, active_until, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(user_id), normalized, now, active_until, now),
    )
    cur.execute(
        "UPDATE comm_v2_presence SET status=?, last_seen_at=?, active_until=?, updated_at=? WHERE user_id=?",
        (normalized, now, active_until, now, int(user_id)),
    )
    return {"user_id": int(user_id), "status": normalized, "last_seen_at": now, "active_until": active_until}


def _presence_visible(cur, viewer_user_id: int, target_user_id: int) -> bool:
    if int(viewer_user_id) == int(target_user_id):
        return True
    privacy = (_settings(cur, int(target_user_id)).get("presence_privacy") or "everyone").lower()
    if privacy == "nobody":
        return False
    if privacy == "contacts":
        cur.execute(
            """
            SELECT 1
            FROM comm_v2_participants a
            JOIN comm_v2_participants b ON b.conversation_id=a.conversation_id
            WHERE a.user_id=? AND b.user_id=?
              AND a.membership_state='active' AND b.membership_state='active'
              AND COALESCE(a.left_at,'')='' AND COALESCE(b.left_at,'')=''
            LIMIT 1
            """,
            (int(viewer_user_id), int(target_user_id)),
        )
        return cur.fetchone() is not None
    return True


def _read_receipts_allowed(cur, user_id: int, conversation_id: int = 0) -> bool:
    if conversation_id:
        _, conversation_settings = _load_conversation_settings(cur, int(conversation_id), int(user_id))
        if (conversation_settings.get("privacy") or {}).get("read_receipts") is False:
            return False
    return bool(int(_settings(cur, user_id).get("read_receipts_enabled") or 0))


def _blocked_between(cur, user_id: int, other_ids: list[int]) -> bool:
    ids = [int(x) for x in other_ids if int(x or 0) != int(user_id)]
    if not ids:
        return False
    placeholders = ",".join(["?"] * len(ids))
    cur.execute(
        f"""
        SELECT id FROM comm_v2_blocks
        WHERE status='active'
          AND ((blocker_user_id=? AND blocked_user_id IN ({placeholders}))
            OR (blocked_user_id=? AND blocker_user_id IN ({placeholders})))
        LIMIT 1
        """,
        (int(user_id), *ids, int(user_id), *ids),
    )
    return cur.fetchone() is not None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _participant_push_policy(cur, recipient_id: int, sender_id: int, conversation_id: int) -> dict:
    if _blocked_between(cur, int(recipient_id), [int(sender_id)]):
        return {"skip": True, "suppress_push": True, "reason": "blocked"}
    cur.execute(
        """
        SELECT muted_until, notifications_level, last_seen_at, last_read_at
        FROM comm_v2_participants
        WHERE conversation_id=? AND user_id=? AND membership_state='active' AND COALESCE(left_at,'')=''
        LIMIT 1
        """,
        (int(conversation_id), int(recipient_id)),
    )
    participant = _row(cur.fetchone())
    if not participant:
        return {"skip": True, "suppress_push": True, "reason": "not_participant"}
    level = str(participant.get("notifications_level") or "all").lower()
    now_dt = datetime.now(timezone.utc)
    muted_until = _parse_dt(participant.get("muted_until"))
    if level in {"none", "off", "muted", "silent"} or (muted_until and muted_until > now_dt):
        return {"skip": False, "suppress_push": True, "reason": "muted"}
    _, conversation_settings = _load_conversation_settings(cur, int(conversation_id), int(recipient_id))
    notifications = conversation_settings.get("notifications") or {}
    if notifications.get("lock_screen") is False:
        return {"skip": False, "suppress_push": True, "reason": "lock_screen_disabled"}
    return {"skip": False, "suppress_push": False, "reason": "deliver"}


def _message_preview_hidden(cur, user_id: int, conversation_id: int = 0) -> bool:
    if conversation_id:
        _, conversation_settings = _load_conversation_settings(cur, int(conversation_id), int(user_id))
        notifications = conversation_settings.get("notifications") or {}
        privacy = conversation_settings.get("privacy") or {}
        if notifications.get("message_preview") is False or privacy.get("message_preview") is False:
            return True
    value = str(_settings(cur, int(user_id)).get("message_preview_privacy") or "show").lower()
    return value in {"hide", "hidden", "private", "generic", "off"}


def _conversation_access(cur, user_id: int, conversation_ref: int | str, join_public: bool = False) -> tuple[dict, str]:
    ref = str(conversation_ref or "").strip()
    if ref.startswith("public-"):
        ref = ref[7:]
    if ref.isdigit():
        cur.execute("SELECT * FROM comm_v2_conversations WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(ref),))
    else:
        cur.execute("SELECT * FROM comm_v2_conversations WHERE public_id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (ref,))
    conversation = _row(cur.fetchone())
    if not conversation:
        return {}, "missing"
    conversation_id = int(conversation["id"])
    cur.execute(
        "SELECT * FROM comm_v2_participants WHERE conversation_id=? AND user_id=? AND membership_state='active' AND COALESCE(left_at,'')='' LIMIT 1",
        (conversation_id, int(user_id)),
    )
    participant = _row(cur.fetchone())
    is_public_room = conversation.get("conversation_type") == "room" and conversation.get("privacy") == "public"
    if not participant and is_public_room and join_public:
        _add_participant(cur, conversation_id, int(user_id), "member")
        participant = {"role": "member"}
    if not participant and not is_public_room:
        return conversation, "denied"
    if _blocked_between(cur, user_id, _participant_ids(cur, conversation_id)):
        return conversation, "blocked"
    return conversation, "ok"


def _add_participant(cur, conversation_id: int, user_id: int, role: str = "member") -> None:
    now = _now()
    cur.execute(
        """
        INSERT OR IGNORE INTO comm_v2_participants
        (conversation_id, user_id, role, membership_state, joined_at, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?, ?)
        """,
        (int(conversation_id), int(user_id), role, now, now, now),
    )
    cur.execute(
        """
        UPDATE comm_v2_participants
        SET membership_state='active', left_at='', role=COALESCE(NULLIF(role,''), ?), updated_at=?
        WHERE conversation_id=? AND user_id=?
        """,
        (role, now, int(conversation_id), int(user_id)),
    )
    cur.execute(
        "UPDATE comm_v2_conversations SET member_count=(SELECT COUNT(*) FROM comm_v2_participants WHERE conversation_id=? AND membership_state='active' AND COALESCE(left_at,'')=''), updated_at=? WHERE id=?",
        (int(conversation_id), now, int(conversation_id)),
    )


def _conversation_payload(cur, conversation: dict, viewer_user_id: int) -> dict:
    conversation_id = int(conversation.get("id") or 0)
    cur.execute(
        "SELECT unread_count, last_read_message_id, role, muted_until, pinned_at, pinned_rank FROM comm_v2_participants WHERE conversation_id=? AND user_id=? LIMIT 1",
        (conversation_id, int(viewer_user_id)),
    )
    mine = _row(cur.fetchone())
    cur.execute(
        """
        SELECT p.user_id, COALESCE(u.display_name,u.username,'Pulse member') AS display_name, COALESCE(u.avatar_url,'') AS avatar_url
        FROM comm_v2_participants p
        LEFT JOIN users u ON u.user_id=p.user_id
        WHERE p.conversation_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
        ORDER BY p.id ASC LIMIT 6
        """,
        (conversation_id,),
    )
    members = [dict(row) for row in cur.fetchall()]
    title = conversation.get("title") or ""
    if conversation.get("conversation_type") == "direct":
        others = [m for m in members if int(m.get("user_id") or 0) != int(viewer_user_id)]
        if others:
            title = others[0].get("display_name") or title
    avatar_url = next((m.get("avatar_url") or "" for m in members if int(m.get("user_id") or 0) != int(viewer_user_id)), "")
    return {
        "id": conversation_id,
        "conversation_id": conversation_id,
        "public_id": conversation.get("public_id") or "",
        "conversation_type": conversation.get("conversation_type") or "direct",
        "title": title or "Untitled chat",
        "avatar_url": avatar_url,
        "description": conversation.get("description") or "",
        "privacy": conversation.get("privacy") or "private",
        "visibility": conversation.get("visibility") or "members",
        "member_count": int(conversation.get("member_count") or len(members) or 0),
        "last_message_id": int(conversation.get("last_message_id") or 0),
        "last_message_at": conversation.get("last_message_at") or "",
        "last_activity_at": conversation.get("last_activity_at") or conversation.get("updated_at") or conversation.get("created_at") or "",
        "unread_count": int(mine.get("unread_count") or 0),
        "last_read_message_id": int(mine.get("last_read_message_id") or 0),
        "role": mine.get("role") or ("viewer" if conversation.get("privacy") == "public" else ""),
        "pinned": bool(mine.get("pinned_at")),
        "muted": bool(mine.get("muted_until") and str(mine.get("muted_until")) > _now()),
        "participants_preview": members,
    }


def _conversation_payloads(cur, conversations: list[dict], viewer_user_id: int) -> list[dict]:
    if not conversations:
        return []
    conversation_ids = [int(item.get("id") or 0) for item in conversations if int(item.get("id") or 0)]
    placeholders = ",".join(["?"] * len(conversation_ids))
    mine_by_conversation: dict[int, dict] = {}
    preview_by_conversation: dict[int, list[dict]] = {conversation_id: [] for conversation_id in conversation_ids}
    if conversation_ids:
        cur.execute(
            f"""
            SELECT conversation_id, unread_count, last_read_message_id, role, muted_until, pinned_at, pinned_rank
            FROM comm_v2_participants
            WHERE user_id=? AND conversation_id IN ({placeholders})
            """,
            (int(viewer_user_id), *conversation_ids),
        )
        mine_by_conversation = {int(row["conversation_id"]): dict(row) for row in cur.fetchall()}
        cur.execute(
            f"""
            SELECT p.conversation_id, p.user_id,
                   COALESCE(u.display_name,u.username,'Pulse member') AS display_name,
                   COALESCE(u.avatar_url,'') AS avatar_url
            FROM comm_v2_participants p
            LEFT JOIN users u ON u.user_id=p.user_id
            WHERE p.conversation_id IN ({placeholders})
              AND p.membership_state='active'
              AND COALESCE(p.left_at,'')=''
            ORDER BY p.conversation_id, p.id ASC
            """,
            tuple(conversation_ids),
        )
        for row in cur.fetchall():
            conversation_id = int(row["conversation_id"])
            if len(preview_by_conversation.setdefault(conversation_id, [])) < 6:
                preview_by_conversation[conversation_id].append({
                    "user_id": int(row["user_id"] or 0),
                    "display_name": row["display_name"] or "Pulse member",
                    "avatar_url": row["avatar_url"] or "",
                })
    latest_by_conversation: dict[int, dict] = {}
    last_message_ids = [int(item.get("last_message_id") or 0) for item in conversations if int(item.get("last_message_id") or 0)]
    if last_message_ids:
        message_placeholders = ",".join(["?"] * len(last_message_ids))
        cur.execute(
            f"SELECT id, conversation_id, message_type, body FROM comm_v2_messages WHERE id IN ({message_placeholders})",
            tuple(last_message_ids),
        )
        latest_by_conversation = {int(row["conversation_id"]): dict(row) for row in cur.fetchall()}
    out = []
    presence_by_user = _user_presence_by_ids(
        cur,
        [int(member.get("user_id") or 0) for members in preview_by_conversation.values() for member in members],
        viewer_user_id=int(viewer_user_id or 0),
    )
    for conversation in conversations:
        conversation_id = int(conversation.get("id") or 0)
        mine = mine_by_conversation.get(conversation_id, {})
        members = preview_by_conversation.get(conversation_id, [])
        latest = latest_by_conversation.get(conversation_id, {})
        latest_type = latest.get("message_type") or "text"
        latest_preview = _safe_preview(latest.get("body") or "", latest_type)
        title = conversation.get("title") or ""
        if conversation.get("conversation_type") == "direct":
            others = [m for m in members if int(m.get("user_id") or 0) != int(viewer_user_id)]
            if others:
                title = others[0].get("display_name") or title
        peer_id = 0
        if conversation.get("conversation_type") == "direct":
            peer_id = next((int(m.get("user_id") or 0) for m in members if int(m.get("user_id") or 0) != int(viewer_user_id)), 0)
        peer_presence = presence_by_user.get(peer_id, {}) if peer_id else {}
        avatar_url = next((m.get("avatar_url") or "" for m in members if int(m.get("user_id") or 0) != int(viewer_user_id)), "")
        out.append({
            "id": conversation_id,
            "conversation_id": conversation_id,
            "public_id": conversation.get("public_id") or "",
            "conversation_type": conversation.get("conversation_type") or "direct",
            "title": title or "Untitled chat",
            "avatar_url": avatar_url,
            "description": conversation.get("description") or "",
            "privacy": conversation.get("privacy") or "private",
            "visibility": conversation.get("visibility") or "members",
            "member_count": int(conversation.get("member_count") or len(members) or 0),
            "last_message_id": int(conversation.get("last_message_id") or 0),
            "last_message_at": conversation.get("last_message_at") or "",
            "last_message": latest_preview,
            "last_message_preview": latest_preview,
            "last_message_type": latest_type,
            "last_activity_at": conversation.get("last_activity_at") or conversation.get("updated_at") or conversation.get("created_at") or "",
            "unread_count": int(mine.get("unread_count") or 0),
            "last_read_message_id": int(mine.get("last_read_message_id") or 0),
            "role": mine.get("role") or ("viewer" if conversation.get("privacy") == "public" else ""),
            "pinned": bool(mine.get("pinned_at")),
            "muted": bool(mine.get("muted_until") and str(mine.get("muted_until")) > _now()),
            "participants_preview": members,
            "peer_user_id": peer_id,
            "presence": peer_presence if peer_presence.get("available") else {},
        })
    return out


def create_conversation(user_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("create_conversation")
    if disabled:
        return disabled
    payload = payload or {}
    conversation_type = _clean(payload.get("conversation_type") or payload.get("type") or "direct", 40).lower()
    if conversation_type not in ALLOWED_CONVERSATION_TYPES:
        return _err("Choose a supported conversation type.", 400, "invalid_type")
    conn, cur = _open_db()
    try:
        now = _now()
        if conversation_type == "direct":
            target_id = int(payload.get("target_user_id") or payload.get("user_id") or 0)
            if not target_id or target_id == int(user_id):
                return _err("Choose another member to message.", 400, "invalid_recipient")
            cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (target_id,))
            if not cur.fetchone():
                return _err("That member was not found.", 404, "missing_user")
            if _blocked_between(cur, user_id, [target_id]):
                return _err("This direct message is unavailable.", 403, "blocked")
            direct_key = ":".join(str(x) for x in sorted([int(user_id), target_id]))
            cur.execute("SELECT * FROM comm_v2_conversations WHERE direct_key=? AND COALESCE(deleted_at,'')='' LIMIT 1", (direct_key,))
            existing = _row(cur.fetchone())
            if existing:
                conversation_id = int(existing["id"])
                _add_participant(cur, conversation_id, int(user_id), "member")
                _add_participant(cur, conversation_id, target_id, "member")
                cur.execute(
                    """
                    UPDATE comm_v2_conversations
                    SET status='active', updated_at=?, last_activity_at=COALESCE(NULLIF(last_activity_at,''), ?)
                    WHERE id=?
                    """,
                    (now, now, conversation_id),
                )
                conn.commit()
                cur.execute("SELECT * FROM comm_v2_conversations WHERE id=? LIMIT 1", (conversation_id,))
                return _ok({"conversation": _conversation_payload(cur, _row(cur.fetchone()), user_id), "conversation_id": conversation_id}, "Direct message ready.")
            cur.execute(
                """
                INSERT INTO comm_v2_conversations
                (public_id, conversation_type, title, owner_user_id, created_by_user_id, direct_key, privacy, visibility, status, member_count, created_at, updated_at, last_activity_at)
                VALUES (?, 'direct', '', ?, ?, ?, 'private', 'members', 'active', 0, ?, ?, ?)
                """,
                (_public_id("dm"), int(user_id), int(user_id), direct_key, now, now, now),
            )
            conversation_id = int(cur.lastrowid)
            _add_participant(cur, conversation_id, int(user_id), "member")
            _add_participant(cur, conversation_id, target_id, "member")
        elif conversation_type == "group":
            title = _clean(payload.get("title") or "Group chat", 120)
            participant_ids = [int(x) for x in payload.get("participant_ids") or payload.get("member_ids") or [] if int(x or 0)]
            participant_ids = sorted({int(user_id), *participant_ids})
            if len(participant_ids) < 2:
                return _err("Add at least one other member to create a group.", 400, "too_few_members")
            if _blocked_between(cur, user_id, participant_ids):
                return _err("One or more members cannot be added to this group.", 403, "blocked")
            cur.execute(
                """
                INSERT INTO comm_v2_conversations
                (public_id, conversation_type, title, owner_user_id, created_by_user_id, privacy, visibility, status, member_count, created_at, updated_at, last_activity_at)
                VALUES (?, 'group', ?, ?, ?, 'private', 'members', 'active', 0, ?, ?, ?)
                """,
                (_public_id("grp"), title, int(user_id), int(user_id), now, now, now),
            )
            conversation_id = int(cur.lastrowid)
            for member_id in participant_ids:
                _add_participant(cur, conversation_id, member_id, "owner" if member_id == int(user_id) else "member")
        elif conversation_type == "room":
            title = _clean(payload.get("title") or payload.get("name") or "Pulse room", 120)
            privacy = _clean(payload.get("privacy") or "public", 20).lower()
            privacy = "private" if privacy == "private" else "public"
            cur.execute(
                """
                INSERT INTO comm_v2_conversations
                (public_id, conversation_type, title, description, owner_user_id, created_by_user_id, privacy, visibility, status, is_discoverable, member_count, created_at, updated_at, last_activity_at)
                VALUES (?, 'room', ?, ?, ?, ?, ?, ?, 'active', ?, 0, ?, ?, ?)
                """,
                (_public_id("room"), title, _clean(payload.get("description") or "", 500), int(user_id), int(user_id), privacy, "public" if privacy == "public" else "members", 1 if privacy == "public" else 0, now, now, now),
            )
            conversation_id = int(cur.lastrowid)
            _add_participant(cur, conversation_id, int(user_id), "owner")
        else:
            community_id = int(payload.get("community_id") or 0)
            title = _clean(payload.get("title") or payload.get("name") or "community-channel", 120)
            cur.execute(
                """
                INSERT INTO comm_v2_conversations
                (public_id, conversation_type, title, owner_user_id, created_by_user_id, community_id, privacy, visibility, status, is_discoverable, member_count, created_at, updated_at, last_activity_at)
                VALUES (?, 'community_channel', ?, ?, ?, ?, 'private', 'members', 'active', 0, 0, ?, ?, ?)
                """,
                (_public_id("chan"), title, int(user_id), int(user_id), community_id, now, now, now),
            )
            conversation_id = int(cur.lastrowid)
            _add_participant(cur, conversation_id, int(user_id), "owner")
        cur.execute("SELECT * FROM comm_v2_conversations WHERE id=?", (conversation_id,))
        conversation = _conversation_payload(cur, _row(cur.fetchone()), user_id)
        conn.commit()
        return _ok({"conversation": conversation, "conversation_id": conversation_id}, "Conversation ready.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_conversations(user_id: int, filters: dict | None = None) -> dict:
    disabled = _disabled("list_conversations")
    if disabled:
        return disabled
    filters = filters or {}
    kind = _clean(filters.get("type") or "all", 40).lower()
    conn, cur = _open_db()
    try:
        params: list[Any] = [int(user_id)]
        type_clause = ""
        if kind in {"direct", "group", "room", "community_channel"}:
            type_clause = "AND c.conversation_type=?"
            params.append(kind)
        cur.execute(
            f"""
            SELECT c.*
            FROM comm_v2_conversations c
            LEFT JOIN comm_v2_participants p ON p.conversation_id=c.id AND p.user_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            LEFT JOIN comm_v2_participants mine_any ON mine_any.conversation_id=c.id AND mine_any.user_id=?
            WHERE COALESCE(c.deleted_at,'')='' AND c.status='active'
              AND COALESCE(mine_any.membership_state,'')!='archived'
              AND (p.id IS NOT NULL OR (c.conversation_type='room' AND c.privacy='public' AND c.is_discoverable=1))
              {type_clause}
            ORDER BY CASE WHEN COALESCE(p.pinned_at,'')!='' THEN 0 ELSE 1 END,
                     COALESCE(p.pinned_at,c.last_activity_at,c.updated_at,c.created_at) DESC, c.id DESC
            LIMIT 120
            """,
            tuple([int(user_id), *params]),
        )
        items = _conversation_payloads(cur, [_row(row) for row in cur.fetchall()], user_id)
        return _ok({"items": items, "conversations": items})
    finally:
        conn.close()


def send_message(user_id: int, conversation_ref: int | str, payload: dict | None = None) -> dict:
    disabled = _disabled("send_message")
    if disabled:
        return disabled
    payload = payload or {}
    body = _clean(payload.get("body") or payload.get("message") or payload.get("content") or "", 4000)
    message_type = _clean(payload.get("message_type") or payload.get("type") or "text", 40).lower()
    if message_type not in ALLOWED_MESSAGE_TYPES:
        message_type = "text"
    media_ids = [int(x) for x in payload.get("media_ids") or payload.get("attachment_media_ids") or [] if int(x or 0)]
    attachment_ids = _message_attachment_ids(payload)
    max_attachments = int(os.getenv("COMM_V2_MAX_ATTACHMENTS", "8") or 8)
    if len(media_ids) + len(attachment_ids) > max_attachments:
        return _err(f"Send up to {max_attachments} attachments at once.", 400, "too_many_attachments")
    if not body and not media_ids and not attachment_ids:
        return _err("Write a message or attach a file before sending.", 400, "empty_message")
    conn, cur = _open_db()
    step = "open_db"
    message_id = 0
    try:
        step = "conversation_access"
        conversation, access = _conversation_access(cur, user_id, conversation_ref, join_public=True)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access == "denied":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        if access == "blocked":
            return _err("Messaging is unavailable for this conversation.", 403, "blocked")
        conversation_id = int(conversation["id"])
        step = "validate_attachments"
        valid_media_ids, media_error = _validate_message_media_ids(cur, user_id, conversation_id, media_ids)
        if media_error:
            return media_error
        media_ids = valid_media_ids
        valid_attachment_ids, attachment_error = _validate_foundation_attachment_ids(cur, user_id, conversation_id, attachment_ids)
        if attachment_error:
            return attachment_error
        attachment_ids = valid_attachment_ids
        client_id = _clean(payload.get("client_message_id") or "", 120)
        if client_id:
            cur.execute(
                "SELECT * FROM comm_v2_messages WHERE conversation_id=? AND sender_user_id=? AND client_message_id=? AND COALESCE(deleted_at,'')='' LIMIT 1",
                (conversation_id, int(user_id), client_id),
            )
            existing = _row(cur.fetchone())
            if existing:
                return _ok({"message": _message_payload(cur, existing, user_id), "message_id": int(existing["id"]), "idempotent": True})
        reply_to = int(payload.get("reply_to_message_id") or payload.get("reply_to_id") or 0)
        thread_root = int(payload.get("thread_root_message_id") or 0)
        if reply_to and not thread_root:
            thread_root = reply_to
        now = _now()
        step = "insert_message"
        logging.info(
            "COMM_V2_SEND_STEP step=%s user_id=%s conversation_id=%s message_type=%s body_len=%s media_ids=%s client_message_id=%s",
            step,
            int(user_id),
            conversation_id,
            message_type,
            len(body or ""),
            media_ids,
            client_id,
        )
        cur.execute(
            """
            INSERT INTO comm_v2_messages
            (public_id, conversation_id, sender_user_id, message_type, body, reply_to_message_id, thread_root_message_id, client_message_id, delivery_status, moderation_status, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sent', 'approved', ?, ?, ?)
            """,
            (_public_id("msg"), conversation_id, int(user_id), message_type, body, reply_to, thread_root, client_id, json.dumps(payload.get("metadata") or {}, default=str)[:4000], now, now),
        )
        message_id = int(cur.lastrowid)
        step = "attach_media"
        attachments = _attach_media(cur, user_id, conversation_id, message_id, media_ids)
        attachments.extend(_attach_foundation_media(cur, user_id, conversation_id, message_id, attachment_ids))
        step = "update_conversation"
        cur.execute(
            "UPDATE comm_v2_conversations SET last_message_id=?, last_message_at=?, last_activity_at=?, updated_at=? WHERE id=?",
            (message_id, now, now, now, conversation_id),
        )
        step = "update_participants"
        cur.execute(
            """
            UPDATE comm_v2_participants
            SET unread_count=CASE WHEN user_id=? THEN 0 ELSE COALESCE(unread_count,0)+1 END,
                last_seen_at=CASE WHEN user_id=? THEN ? ELSE last_seen_at END,
                updated_at=?
            WHERE conversation_id=? AND membership_state='active'
            """,
            (int(user_id), int(user_id), now, now, conversation_id),
        )
        step = "mark_read"
        mark_read(user_id, conversation_id, existing_conn=(conn, cur), commit=False)
        step = "message_payload"
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=?", (message_id,))
        message = _message_payload(cur, _row(cur.fetchone()), user_id)
        if attachments:
            message["attachments"] = attachments
        security_classification = _message_security_classification(body)
        if security_classification.get("risky"):
            message["pulse_shield"] = {
                "flagged": True,
                "severity": security_classification.get("severity") or "High",
                "score": int(security_classification.get("score") or 0),
                "reasons": security_classification.get("reasons") or [],
            }
        step = "commit"
        conn.commit()
        if security_classification.get("risky"):
            _dispatch_command_center_async(
                "enqueue_security_event",
                {
                    "event_type": "phishing_link",
                    "user_id": int(user_id),
                    "actor_id": int(user_id),
                    "payload": {
                        "surface": "messages",
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "body_preview": _safe_preview(body, "text", "")[:240],
                        "classification": security_classification,
                    },
                },
                idempotency_key=f"message-shield-{conversation_id}-{message_id}",
            )
        command_center_recipient_ids = [uid for uid in _participant_ids_for_side_effects(conversation_id) if int(uid) != int(user_id)]
        _dispatch_command_center_async(
            "enqueue_message_event",
            "message_created",
            conversation_id,
            message_id,
            int(user_id),
            None,
            {
                "type": "message_created",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "client_temp_id": message.get("client_temp_id") or message.get("client_message_id") or "",
                "client_message_id": message.get("client_message_id") or "",
                "sender_id": int(user_id),
                "recipient_ids": command_center_recipient_ids,
                "body": message.get("body") or "",
                "message_type": message.get("message_type") or "text",
                "created_at": message.get("created_at") or now,
                "attachment_count": len(attachments or []),
                "sender_display_name": message.get("sender_display_name") or (message.get("sender") or {}).get("display_name") or "",
                "sender_avatar": message.get("sender_avatar") or (message.get("sender") or {}).get("avatar_url") or "",
                "delivery_state": message.get("delivery_state") or "sent",
                "message": message,
            },
            idempotency_key=f"message-created-{conversation_id}-{message_id}",
        )
        side_effects = _dispatch_message_side_effects(user_id, conversation_id, message)
        logging.info(
            "COMM_V2_SEND_COMPLETE user_id=%s conversation_id=%s message_id=%s attachment_count=%s side_effects=%s",
            int(user_id),
            conversation_id,
            message_id,
            len(attachments or []),
            side_effects,
        )
        return _ok({"message": message, "message_id": message_id, "conversation_id": conversation_id}, "Message sent.")
    except Exception as exc:
        conn.rollback()
        logging.exception(
            "COMM_V2_SEND_FAILED step=%s user_id=%s conversation_ref=%s message_id=%s message_type=%s body_len=%s media_ids=%s payload_keys=%s error_type=%s",
            step,
            int(user_id or 0),
            conversation_ref,
            message_id,
            message_type,
            len(body or ""),
            media_ids,
            sorted((payload or {}).keys()),
            type(exc).__name__,
        )
        raise
    finally:
        conn.close()


def _validate_message_media_ids(cur, user_id: int, conversation_id: int, media_ids: list[int]) -> tuple[list[int], dict | None]:
    ids = [int(x) for x in (media_ids or []) if int(x or 0)]
    if not ids:
        return [], None
    unique_ids = []
    seen = set()
    for media_id in ids:
        if media_id not in seen:
            unique_ids.append(media_id)
            seen.add(media_id)
    placeholders = ",".join(["?"] * len(unique_ids))
    cur.execute(
        f"""
        SELECT *
        FROM chat_media_uploads
        WHERE id IN ({placeholders})
          AND uploader_user_id=?
          AND COALESCE(deleted_at,'')=''
          AND COALESCE(moderation_status,'approved')!='blocked'
        """,
        (*unique_ids, int(user_id)),
    )
    rows = {_row(row).get("id"): _row(row) for row in cur.fetchall()}
    missing = [media_id for media_id in unique_ids if media_id not in rows]
    if missing:
        logging.warning(
            "COMM_V2_ATTACHMENT_INVALID user_id=%s conversation_id=%s missing_media_ids=%s requested_media_ids=%s",
            int(user_id),
            int(conversation_id),
            missing,
            unique_ids,
        )
        return [], _err("Attachment invalid or expired. Please upload it again.", 400, "attachment_invalid")
    invalid = []
    for media_id, media in rows.items():
        availability_error = str(media.get("availability_error") or media.get("error_message") or "")
        verification = str(media.get("verification_status") or "verified").lower()
        processing = str(media.get("processing_status") or "ready").lower()
        has_deliverable_url = any(media.get(key) for key in ("media_url", "public_url", "cdn_url", "playback_url", "storage_key", "object_key"))
        if verification in {"failed", "blocked"} or processing in {"failed", "blocked"} or (availability_error and not has_deliverable_url):
            invalid.append({"media_id": int(media_id), "verification": verification, "processing": processing, "availability_error": availability_error[:160]})
    if invalid:
        logging.warning(
            "COMM_V2_ATTACHMENT_VERIFY_FAILED user_id=%s conversation_id=%s invalid=%s",
            int(user_id),
            int(conversation_id),
            invalid,
        )
        return [], _err("Attachment could not be verified. Please upload it again.", 400, "attachment_verification_failed")
    return unique_ids, None


def _message_attachment_ids(payload: dict | None) -> list[int]:
    payload = payload or {}
    raw = payload.get("attachment_ids")
    if raw is None:
        raw = payload.get("attachments")
    if raw is None:
        raw = payload.get("message_attachment_ids")
    if isinstance(raw, (str, int)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    seen = set()
    for item in raw:
        value = item.get("attachment_id") if isinstance(item, dict) else item
        try:
            attachment_id = int(value or 0)
        except (TypeError, ValueError):
            attachment_id = 0
        if attachment_id and attachment_id not in seen:
            ids.append(attachment_id)
            seen.add(attachment_id)
    return ids


def _validate_foundation_attachment_ids(cur, user_id: int, conversation_id: int, attachment_ids: list[int]) -> tuple[list[int], dict | None]:
    ids = [int(x) for x in (attachment_ids or []) if int(x or 0)]
    if not ids:
        return [], None
    unique_ids = []
    seen = set()
    for attachment_id in ids:
        if attachment_id not in seen:
            unique_ids.append(attachment_id)
            seen.add(attachment_id)
    placeholders = ",".join(["?"] * len(unique_ids))
    cur.execute(
        f"""
        SELECT id, upload_status, processing_status, error_code
        FROM message_attachments
        WHERE id IN ({placeholders})
          AND sender_id=?
          AND conversation_id=?
          AND conversation_model='comm_v2'
          AND COALESCE(deleted_at,'')=''
        """,
        (*unique_ids, int(user_id), int(conversation_id)),
    )
    rows = {_row(row).get("id"): _row(row) for row in cur.fetchall()}
    missing = [attachment_id for attachment_id in unique_ids if attachment_id not in rows]
    if missing:
        logging.warning(
            "COMM_V2_FOUNDATION_ATTACHMENT_INVALID user_id=%s conversation_id=%s missing_attachment_ids=%s requested_attachment_ids=%s",
            int(user_id),
            int(conversation_id),
            missing,
            unique_ids,
        )
        return [], _err("Attachment invalid or expired. Please upload it again.", 400, "attachment_invalid")
    invalid = []
    for attachment_id, row in rows.items():
        upload_status = str(row.get("upload_status") or "").lower()
        processing_status = str(row.get("processing_status") or "").lower()
        if upload_status != "uploaded" or processing_status in {"failed", "blocked"}:
            invalid.append({"attachment_id": int(attachment_id), "upload_status": upload_status, "processing_status": processing_status})
    if invalid:
        logging.warning(
            "COMM_V2_FOUNDATION_ATTACHMENT_VERIFY_FAILED user_id=%s conversation_id=%s invalid=%s",
            int(user_id),
            int(conversation_id),
            invalid,
        )
        return [], _err("Attachment could not be verified. Please upload it again.", 400, "attachment_verification_failed")
    return unique_ids, None


def _dispatch_message_side_effects(user_id: int, conversation_id: int, message: dict) -> dict:
    results = {"notifications": "skipped", "realtime": "skipped"}
    realtime_payloads: list[dict] = []
    push_trace_id = _trace()
    try:
        recipient_ids = [uid for uid in _participant_ids_for_side_effects(conversation_id) if int(uid) != int(user_id)]
        logging.info(
            "PUSH_TRACE stage=message_side_effects_start %s",
            json.dumps(
                {
                    "push_trace_id": push_trace_id,
                    "sender_user_id": int(user_id or 0),
                    "conversation_id": int(conversation_id or 0),
                    "message_id": int(message.get("id") or 0),
                    "recipient_count": len(recipient_ids),
                },
                default=str,
                sort_keys=True,
            )[:1200],
        )
        if recipient_ids:
            from services import notification_service

            sender_name = "PulseSoc"
            policy_conn, policy_cur = _open_db()
            try:
                sender = _user_summary(policy_cur, int(user_id))
                sender_name = sender.get("display_name") or "PulseSoc"
            finally:
                policy_conn.close()
            preview = _safe_preview(message.get("body") or "", message.get("message_type") or "")
            for recipient_id in recipient_ids[:25]:
                policy_conn, policy_cur = _open_db()
                try:
                    policy = _participant_push_policy(policy_cur, int(recipient_id), int(user_id), int(conversation_id))
                    hide_preview = _message_preview_hidden(policy_cur, int(recipient_id), int(conversation_id))
                finally:
                    policy_conn.close()
                if policy.get("skip"):
                    logging.info(
                        "PUSH_TRACE stage=recipient_skipped %s",
                        json.dumps(
                            {
                                "push_trace_id": push_trace_id,
                                "recipient_user_id": int(recipient_id),
                                "sender_user_id": int(user_id or 0),
                                "conversation_id": int(conversation_id or 0),
                                "message_id": int(message.get("id") or 0),
                                "reason": policy.get("reason"),
                            },
                            default=str,
                            sort_keys=True,
                        )[:1200],
                    )
                    continue
                recipient_message = _side_effect_message_payload(int(recipient_id), int(message.get("id") or 0)) or message
                message_id = int(message.get("id") or 0)
                unread = _chat_unread_count_for_user(int(recipient_id))
                deep_link = f"/pulse/messages/{int(conversation_id)}"
                mobile_deep_link = f"pulse://pulse/messages-v2?conversation={int(conversation_id)}"
                title = sender_name if not hide_preview else "New message"
                body = preview[:220] if not hide_preview else "Open PulseSoc to view."
                if (message.get("pulse_shield") or {}).get("flagged"):
                    title = "Pulse Shield"
                    body = "A message needs review before you open it."
                push_metadata = {
                    "conversation_id": int(conversation_id),
                    "conversationId": int(conversation_id),
                    "message_id": message_id,
                    "messageId": message_id,
                    "sender_id": int(user_id),
                    "senderId": int(user_id),
                    "sender_name": sender_name,
                    "message_preview": preview[:220],
                    "preview_text": preview[:220],
                    "type": "message",
                    "push_type": "chat_message",
                    "url": deep_link,
                    "web_url": deep_link,
                    "deepLink": mobile_deep_link,
                    "deep_link": deep_link,
                    "target_url": deep_link,
                    "mobile_deep_link": mobile_deep_link,
                    "native_url": mobile_deep_link,
                    "app_url": mobile_deep_link,
                    "route": "messages",
                    "screen": "Messages",
                    "badge": int(unread or 0),
                    "chat_unread_count": int(unread or 0),
                    "unread_count": int(unread or 0),
                    "privacy_preview_hidden": bool(hide_preview),
                    "suppress_push": bool(policy.get("suppress_push")),
                    "push_policy": policy.get("reason") or "deliver",
                    "push_trace_id": push_trace_id,
                }
                logging.info(
                    "PUSH_TRACE stage=recipient_policy %s",
                    json.dumps(
                        {
                            "push_trace_id": push_trace_id,
                            "recipient_user_id": int(recipient_id),
                            "sender_user_id": int(user_id or 0),
                            "conversation_id": int(conversation_id or 0),
                            "message_id": message_id,
                            "policy": policy.get("reason") or "deliver",
                            "suppress_push": bool(policy.get("suppress_push")),
                            "privacy_preview_hidden": bool(hide_preview),
                        },
                        default=str,
                        sort_keys=True,
                    )[:1200],
                )
                note = notification_service.create_pulse_notification(
                    int(recipient_id),
                    note_type="voice_message" if message.get("message_type") == "voice" else "message",
                    title=title,
                    body=body,
                    actor_user_id=int(user_id),
                    entity_type="comm_v2_message",
                    entity_id=message_id,
                    deep_link=deep_link,
                    metadata=push_metadata,
                )
                push_result = {"ok": False, "status": "suppressed", "reason": policy.get("reason") or "unknown"}
                logging.info(
                    "PUSH_TRACE stage=notification_created %s",
                    json.dumps(
                        {
                            "push_trace_id": push_trace_id,
                            "recipient_user_id": int(recipient_id),
                            "conversation_id": int(conversation_id),
                            "message_id": message_id,
                            "notification_id": int(note.get("notification_id") or 0),
                        },
                        default=str,
                        sort_keys=True,
                    )[:1200],
                )
                push_result = note.get("push") or push_result
                realtime_payloads.append({
                    "recipient_user_id": int(recipient_id),
                    "conversation_id": int(conversation_id),
                    "message_id": message_id,
                    "sender_user_id": int(user_id),
                    "message": recipient_message,
                    "notification": {
                        "id": int(note.get("notification_id") or 0),
                        "type": "voice_message" if message.get("message_type") == "voice" else "message",
                        "category": "messages",
                        "title": title,
                        "body": body,
                        "deep_link": deep_link,
                        "target_url": deep_link,
                        "read": False,
                        "status": "unread",
                        "created_at": _now(),
                        "push_policy": policy.get("reason") or "deliver",
                        "push_sent": bool(push_result.get("ok")) and int(push_result.get("sent") or 0) > 0,
                    },
                    "chat_unread_count": int(unread or 0),
                    "unread_count": int(unread or 0),
                    "conversation": _side_effect_conversation_payload(int(recipient_id), int(conversation_id)),
                })
            results["notifications"] = f"created:{len(recipient_ids[:25])}"
    except Exception as exc:
        logging.exception("COMM_V2_NOTIFICATION_DISPATCH_FAILED conversation_id=%s message_id=%s error_type=%s", conversation_id, message.get("id"), type(exc).__name__)
        results["notifications"] = "failed"
    try:
        from services import realtime_engine

        realtime_engine.publish_event(
            f"comm_v2:conversation:{int(conversation_id)}",
            "message_created",
            {"conversation_id": int(conversation_id), "message": message},
        )
        realtime_engine.publish_event(
            f"cc:conversation:{int(conversation_id)}",
            "message_created",
            {"conversation_id": int(conversation_id), "message": message, "recipient_ids": [int(item["recipient_user_id"]) for item in realtime_payloads]},
        )
        for payload in realtime_payloads:
            user_message_event = {
                "conversation_id": int(payload["conversation_id"]),
                "message_id": int(payload["message_id"]),
                "recipient_user_id": int(payload["recipient_user_id"]),
                "sender_user_id": int(payload["sender_user_id"]),
                "message": payload.get("message") or {},
                "conversation": payload.get("conversation") or {},
                "chat_unread_count": int(payload.get("chat_unread_count") or 0),
                "unread_count": int(payload.get("unread_count") or 0),
                "notification": payload.get("notification") or {},
            }
            realtime_engine.publish_event(
                f"comm_v2:user:{int(payload['recipient_user_id'])}",
                "message_notification",
                payload,
            )
            realtime_engine.publish_event(
                f"comm_v2:user:{int(payload['recipient_user_id'])}",
                "message_created",
                user_message_event,
            )
            realtime_engine.publish_event(
                f"cc:user:{int(payload['recipient_user_id'])}",
                "message_created",
                user_message_event,
            )
            realtime_engine.publish_event(
                f"pulse:user:{int(payload['recipient_user_id'])}",
                "notification_created",
                payload,
            )
            realtime_engine.publish_event(
                f"comm_v2:user:{int(payload['recipient_user_id'])}",
                "unread_count_updated",
                {
                    "conversation_id": int(conversation_id),
                    "chat_unread_count": int(payload.get("chat_unread_count") or 0),
                    "unread_count": int(payload.get("unread_count") or 0),
                },
            )
        results["realtime"] = f"published:{2 + len(realtime_payloads) * 5}"
    except Exception as exc:
        logging.exception("COMM_V2_REALTIME_BROADCAST_FAILED conversation_id=%s message_id=%s error_type=%s", conversation_id, message.get("id"), type(exc).__name__)
        results["realtime"] = "failed"
    return results


def _dispatch_push_alert_async(notification_service, recipient_id: int, title: str, body: str, metadata: dict, push_trace_id: str, conversation_id: int, message_id: int) -> None:
    def run_delivery():
        logging.info(
            "PUSH_TRACE stage=send_push_start %s",
            json.dumps(
                {
                    "push_trace_id": push_trace_id,
                    "recipient_user_id": int(recipient_id),
                    "conversation_id": int(conversation_id),
                    "message_id": int(message_id),
                },
                default=str,
                sort_keys=True,
            )[:1200],
        )
        try:
            result = notification_service.send_push_alert(int(recipient_id), title, body, metadata)
        except Exception as exc:
            result = {"ok": False, "failed": 1, "error_type": type(exc).__name__}
        logging.info(
            "PUSH_TRACE stage=send_push_complete %s",
            json.dumps(
                {
                    "push_trace_id": push_trace_id,
                    "recipient_user_id": int(recipient_id),
                    "conversation_id": int(conversation_id),
                    "message_id": int(message_id),
                    "ok": bool(result.get("ok")),
                    "sent": int(result.get("sent") or 0),
                    "failed": int(result.get("failed") or 0),
                    "skipped": int(result.get("skipped") or 0),
                    "error_type": result.get("error_type") or "",
                },
                default=str,
                sort_keys=True,
            )[:1200],
        )

    threading.Thread(target=run_delivery, name=f"comm-v2-push-{conversation_id}-{message_id}-{recipient_id}", daemon=True).start()


def _side_effect_conversation_payload(user_id: int, conversation_id: int) -> dict:
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_conversations WHERE id=? AND COALESCE(deleted_at,'')=''", (int(conversation_id),))
        row = _row(cur.fetchone())
        return _conversation_payload(cur, row, int(user_id)) if row else {}
    except Exception:
        logging.exception("COMM_V2_SIDE_EFFECT_CONVERSATION_PAYLOAD_FAILED user_id=%s conversation_id=%s", user_id, conversation_id)
        return {}
    finally:
        conn.close()


def _side_effect_message_payload(user_id: int, message_id: int) -> dict:
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')=''", (int(message_id),))
        row = _row(cur.fetchone())
        return _message_payload(cur, row, int(user_id)) if row else {}
    except Exception:
        logging.exception("COMM_V2_SIDE_EFFECT_MESSAGE_PAYLOAD_FAILED user_id=%s message_id=%s", user_id, message_id)
        return {}
    finally:
        conn.close()


def _participant_ids_for_side_effects(conversation_id: int) -> list[int]:
    conn, cur = _open_db()
    try:
        return _participant_ids(cur, int(conversation_id))
    finally:
        conn.close()


def _chat_unread_count_for_user(user_id: int) -> int:
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN COALESCE(unread_count,0) > 0 THEN unread_count ELSE 0 END),0) AS total
            FROM comm_v2_participants
            WHERE user_id=? AND membership_state='active' AND COALESCE(left_at,'')=''
            """,
            (int(user_id),),
        )
        return int(_row(cur.fetchone()).get("total") or 0)
    except Exception:
        return 0
    finally:
        conn.close()


def _realtime_channels_for_user(user_id: int, args) -> list[str]:
    conversation_ref = args.get("conversation_id") or args.get("conversation") or ""
    channels = [f"comm_v2:user:{int(user_id)}", f"pulse:user:{int(user_id)}", f"cc:user:{int(user_id)}"]
    if conversation_ref:
        conn, cur = _open_db()
        try:
            conversation, access = _conversation_access(cur, user_id, conversation_ref, join_public=False)
            if access == "ok" and conversation:
                conversation_id = int(conversation["id"])
                channels.extend([f"comm_v2:conversation:{conversation_id}", f"cc:conversation:{conversation_id}"])
        finally:
            conn.close()
    return channels


def poll_realtime_events(user_id: int, args) -> dict:
    disabled = _disabled("realtime")
    if disabled:
        return disabled
    from services import realtime_engine

    after_id = int(args.get("after_id") or args.get("since_id") or 0)
    limit = max(1, min(int(args.get("limit") or 80), 160))
    events = []
    transport = "local_polling"
    try:
        from services import command_center_client

        shared = command_center_client.get_realtime_events(int(user_id), after_id=after_id, limit=limit)
        if shared.get("available") and shared.get("ok"):
            events = shared.get("events") or []
            transport = str(shared.get("transport") or "command_center_polling")
    except Exception as exc:
        logging.info("COMM_V2_SHARED_REALTIME_POLL_SKIPPED user_id=%s error_type=%s", int(user_id), exc.__class__.__name__)
    if not events:
        events = realtime_engine.poll_events_for_channels(_realtime_channels_for_user(user_id, args), after_id=after_id, limit=limit)
    latest_event_id = max([after_id, *[int(item.get("id") or 0) for item in events]], default=after_id)
    unread = _chat_unread_count_for_user(int(user_id))
    return _ok({
        "events": events[-limit:],
        "latest_event_id": latest_event_id,
        "chat_unread_count": int(unread or 0),
        "unread_count": int(unread or 0),
        "poll_interval_ms": 3000,
        "transport": transport,
    })


def stream_realtime_events(user_id: int, args) -> dict:
    disabled = _disabled("realtime_stream")
    if disabled:
        return disabled
    from services import realtime_engine

    after_id = int(args.get("after_id") or args.get("since_id") or 0)
    limit = max(1, min(int(args.get("limit") or 80), 160))
    timeout_seconds = max(1.0, min(float(args.get("timeout") or 3), 5.0))
    channels = _realtime_channels_for_user(user_id, args)
    events = realtime_engine.wait_events(channels, after_id=after_id, limit=limit, timeout_seconds=timeout_seconds)
    latest_event_id = max([after_id, *[int(item.get("id") or 0) for item in events]], default=after_id)
    unread = _chat_unread_count_for_user(int(user_id))
    return _ok({
        "events": events[-limit:],
        "latest_event_id": latest_event_id,
        "chat_unread_count": int(unread or 0),
        "unread_count": int(unread or 0),
        "poll_interval_ms": 12000,
        "stream": True,
    })


def _attach_media(cur, user_id: int, conversation_id: int, message_id: int, media_ids: list[int]) -> list[dict]:
    out = []
    max_attachments = int(os.getenv("COMM_V2_MAX_ATTACHMENTS", "8") or 8)
    for media_id in media_ids[:max_attachments]:
        logging.info(
            "COMM_V2_ATTACHMENT_STEP step=load_media user_id=%s conversation_id=%s message_id=%s media_id=%s",
            int(user_id),
            int(conversation_id),
            int(message_id),
            int(media_id),
        )
        cur.execute(
            "SELECT * FROM chat_media_uploads WHERE id=? AND uploader_user_id=? AND COALESCE(deleted_at,'')='' LIMIT 1",
            (int(media_id), int(user_id)),
        )
        media = _row(cur.fetchone())
        if not media:
            logging.warning("COMM_V2_ATTACHMENT_MISSING user_id=%s conversation_id=%s message_id=%s media_id=%s", int(user_id), int(conversation_id), int(message_id), int(media_id))
            continue
        logging.info(
            "COMM_V2_ATTACHMENT_STEP step=prepare_media user_id=%s conversation_id=%s message_id=%s media_id=%s media_type=%s mime_type=%s processing=%s verification=%s",
            int(user_id),
            int(conversation_id),
            int(message_id),
            int(media_id),
            media.get("media_type") or "",
            media.get("mime_type") or "",
            media.get("processing_status") or "",
            media.get("verification_status") or "",
        )
        media = _prepare_attachment_media(cur, media, media_id)
        now = _now()
        logging.info(
            "COMM_V2_ATTACHMENT_STEP step=insert_attachment user_id=%s conversation_id=%s message_id=%s media_id=%s url=%s cdn=%s playback=%s",
            int(user_id),
            int(conversation_id),
            int(message_id),
            int(media_id),
            bool(media.get("url") or media.get("media_url") or media.get("public_url")),
            bool(media.get("cdn_url") or media.get("valid_url")),
            bool(media.get("playback_url")),
        )
        cur.execute(
            """
            INSERT INTO comm_v2_attachments
            (attachment_public_id, message_id, conversation_id, media_upload_id, uploader_user_id, media_type, storage_provider, storage_key, url, cdn_url, playback_url, thumbnail_url, mime_type, file_size, file_size_bytes, duration_seconds, waveform_json, voice_note, width, height, mux_asset_id, mux_playback_id, mux_status, scan_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _public_id("att"),
                int(message_id),
                int(conversation_id),
                int(media_id),
                int(user_id),
                media.get("media_type") or "file",
                media.get("storage_provider") or "",
                media.get("storage_key") or media.get("object_key") or "",
                media.get("url") or media.get("media_url") or media.get("public_url") or media.get("cdn_url") or "",
                media.get("cdn_url") or media.get("valid_url") or media.get("media_url") or "",
                media.get("playback_url") or "",
                media.get("thumbnail_url") or media.get("poster_url") or "",
                media.get("mime_type") or "",
                int(media.get("file_size") or media.get("file_size_bytes") or 0),
                int(media.get("file_size_bytes") or 0),
                float(media.get("duration_seconds") or media.get("duration") or 0),
                media.get("waveform_json") or "",
                1 if int(media.get("voice_note") or 0) else 0,
                int(media.get("width") or 0),
                int(media.get("height") or 0),
                media.get("mux_asset_id") or "",
                media.get("mux_playback_id") or "",
                media.get("mux_status") or "",
                media.get("moderation_status") or "approved",
                now,
            ),
        )
        logging.info("COMM_V2_ATTACHMENT_STEP step=link_upload user_id=%s conversation_id=%s message_id=%s media_id=%s", int(user_id), int(conversation_id), int(message_id), int(media_id))
        cur.execute(
            "UPDATE chat_media_uploads SET message_id=?, context_type='pulse_comm_v2', context_id=? WHERE id=?",
            (int(message_id), str(conversation_id), int(media_id)),
        )
        attachment_id = int(cur.lastrowid)
        out.append(_attachment_payload(_row({**media, "id": attachment_id, "media_upload_id": media_id})))
    return out


def _attach_foundation_media(cur, user_id: int, conversation_id: int, message_id: int, attachment_ids: list[int]) -> list[dict]:
    out = []
    max_attachments = int(os.getenv("COMM_V2_MAX_ATTACHMENTS", "8") or 8)
    for foundation_id in attachment_ids[:max_attachments]:
        cur.execute(
            """
            SELECT *
            FROM message_attachments
            WHERE id=? AND sender_id=? AND conversation_id=? AND conversation_model='comm_v2'
              AND upload_status='uploaded' AND COALESCE(deleted_at,'')=''
            LIMIT 1
            """,
            (int(foundation_id), int(user_id), int(conversation_id)),
        )
        media = _row(cur.fetchone())
        if not media:
            logging.warning("COMM_V2_FOUNDATION_ATTACHMENT_MISSING user_id=%s conversation_id=%s message_id=%s attachment_id=%s", int(user_id), int(conversation_id), int(message_id), int(foundation_id))
            continue
        raw_type = str(media.get("media_type") or "file").lower()
        media_type = "image" if raw_type == "photo" else "voice" if raw_type == "voice" else raw_type
        download_url = f"/api/messages/media/{int(foundation_id)}/download"
        duration_seconds = max(0.0, float(media.get("duration_ms") or 0) / 1000.0)
        attachment_public_id = _public_id("att")
        now = _now()
        cur.execute(
            """
            INSERT INTO comm_v2_attachments
            (attachment_public_id, message_id, conversation_id, media_upload_id, uploader_user_id, media_type, storage_provider, storage_key, url, cdn_url, playback_url, thumbnail_url, mime_type, file_size, file_size_bytes, duration_seconds, waveform_json, voice_note, width, height, mux_asset_id, mux_playback_id, mux_status, scan_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attachment_public_id,
                int(message_id),
                int(conversation_id),
                int(foundation_id),
                int(user_id),
                media_type,
                "messenger_media_foundation",
                "",
                download_url,
                "",
                download_url if media_type in {"video", "voice", "audio"} else "",
                "",
                media.get("mime_type") or "",
                int(media.get("size_bytes") or 0),
                int(media.get("size_bytes") or 0),
                duration_seconds,
                media.get("waveform_json") or "",
                1 if media_type == "voice" else 0,
                int(media.get("width") or 0),
                int(media.get("height") or 0),
                "",
                "",
                "",
                "approved",
                now,
            ),
        )
        attachment_row = {
            "id": int(cur.lastrowid),
            "attachment_public_id": attachment_public_id,
            "message_id": int(message_id),
            "conversation_id": int(conversation_id),
            "media_upload_id": int(foundation_id),
            "uploader_user_id": int(user_id),
            "media_type": media_type,
            "storage_provider": "messenger_media_foundation",
            "storage_key": "",
            "url": download_url,
            "cdn_url": "",
            "playback_url": download_url if media_type in {"video", "voice", "audio"} else "",
            "thumbnail_url": "",
            "mime_type": media.get("mime_type") or "",
            "file_size": int(media.get("size_bytes") or 0),
            "file_size_bytes": int(media.get("size_bytes") or 0),
            "duration_seconds": duration_seconds,
            "waveform_json": media.get("waveform_json") or "",
            "voice_note": 1 if media_type == "voice" else 0,
            "width": int(media.get("width") or 0),
            "height": int(media.get("height") or 0),
            "foundation_attachment_id": int(foundation_id),
        }
        cur.execute(
            "UPDATE message_attachments SET message_id=?, upload_status='attached', updated_at=? WHERE id=?",
            (int(message_id), now, int(foundation_id)),
        )
        out.append(_attachment_payload(attachment_row))
    return out


def _prepare_attachment_media(cur, media: dict, media_id: int) -> dict:
    try:
        from services import media_service
    except Exception:
        media_service = None
    resolved = media_service.resolve_media(media, check_remote=False) if media_service else {}
    out = {
        **media,
        "url": resolved.get("media_url") or media.get("media_url") or media.get("public_url") or media.get("cdn_url") or "",
        "cdn_url": resolved.get("valid_url") or media.get("cdn_url") or media.get("public_url") or media.get("media_url") or "",
        "playback_url": resolved.get("playback_url") or media.get("playback_url") or "",
        "thumbnail_url": resolved.get("thumbnail_url") or media.get("thumbnail_url") or media.get("poster_url") or "",
        "mux_asset_id": resolved.get("mux_asset_id") or media.get("mux_asset_id") or "",
        "mux_playback_id": resolved.get("mux_playback_id") or media.get("mux_playback_id") or "",
        "mux_status": resolved.get("mux_status") or media.get("mux_status") or "",
    }
    if (out.get("media_type") or "").lower() == "video" and media_service and not out.get("mux_playback_id"):
        source = out.get("cdn_url") or out.get("url")
        mux = media_service.create_mux_asset_from_url(source, trace_id=_trace(), media_id=int(media_id))
        if mux.get("ok"):
            playback = media_service.mux_playback_urls(mux.get("playback_id") or "")
            out.update({
                "mux_asset_id": mux.get("asset_id") or "",
                "mux_playback_id": mux.get("playback_id") or "",
                "mux_status": mux.get("status") or "created",
                "playback_url": playback.get("hls_url") or out.get("playback_url") or "",
                "thumbnail_url": playback.get("thumbnail_url") or out.get("thumbnail_url") or "",
            })
            cur.execute(
                "UPDATE chat_media_uploads SET mux_asset_id=?, mux_playback_id=?, mux_status=?, playback_url=?, thumbnail_url=COALESCE(NULLIF(thumbnail_url,''), ?) WHERE id=?",
                (out["mux_asset_id"], out["mux_playback_id"], out["mux_status"], out["playback_url"], out["thumbnail_url"], int(media_id)),
            )
        else:
            logging.info("COMM_V2_MUX_ASSET_SKIPPED media_id=%s status=%s", int(media_id), mux.get("status") or "unknown")
    return out


def _attachment_payload(row: dict) -> dict:
    mux_playback_id = row.get("mux_playback_id") or ""
    playback_url = row.get("playback_url") or ""
    if mux_playback_id and not playback_url:
        try:
            from services import media_service

            playback_url = media_service.mux_playback_urls(mux_playback_id).get("hls_url") or ""
        except Exception:
            playback_url = ""
    cdn_url = row.get("cdn_url") or row.get("valid_url") or row.get("media_url") or row.get("public_url") or row.get("url") or ""
    url = playback_url if (row.get("media_type") or "").lower() == "video" and playback_url else (row.get("url") or cdn_url)
    try:
        waveform = json.loads(row.get("waveform_json") or "[]")
    except Exception:
        waveform = []
    return {
        "id": int(row.get("id") or row.get("media_upload_id") or 0),
        "attachment_id": int(row.get("id") or 0),
        "attachment_public_id": row.get("attachment_public_id") or "",
        "media_upload_id": int(row.get("media_upload_id") or row.get("id") or 0),
        "media_type": row.get("media_type") or "file",
        "url": url,
        "cdn_url": cdn_url,
        "playback_url": playback_url,
        "thumbnail_url": row.get("thumbnail_url") or row.get("poster_url") or "",
        "mime_type": row.get("mime_type") or "",
        "file_size": int(row.get("file_size") or row.get("file_size_bytes") or 0),
        "file_size_bytes": int(row.get("file_size_bytes") or 0),
        "duration_seconds": float(row.get("duration_seconds") or row.get("duration") or 0),
        "waveform": waveform if isinstance(waveform, list) else [],
        "voice_note": bool(int(row.get("voice_note") or 0)),
        "storage_provider": row.get("storage_provider") or "",
        "storage_key": row.get("storage_key") or row.get("object_key") or "",
        "mux_asset_id": row.get("mux_asset_id") or "",
        "mux_playback_id": mux_playback_id,
        "mux_status": row.get("mux_status") or "",
    }


def _voice_upload_metadata(payload: dict | None) -> dict:
    payload = payload or {}
    kind = _clean(payload.get("attachment_kind") or payload.get("kind") or "", 40).lower()
    is_voice = kind in {"voice", "voice_note", "audio_note"}
    try:
        duration = max(0.0, float(payload.get("duration_seconds") or payload.get("duration") or 0))
    except Exception:
        duration = 0.0
    waveform_raw = payload.get("waveform_json") or payload.get("waveform") or "[]"
    waveform = []
    try:
        candidate = json.loads(waveform_raw) if isinstance(waveform_raw, str) else waveform_raw
        if isinstance(candidate, list):
            waveform = [max(0, min(100, int(float(value)))) for value in candidate[:80]]
    except Exception:
        waveform = []
    return {"is_voice": is_voice, "duration_seconds": duration, "waveform": waveform}


def _validate_voice_upload(file_storage, metadata: dict) -> dict:
    if not metadata.get("is_voice"):
        return {"ok": True}
    mime = (getattr(file_storage, "mimetype", "") or "").lower()
    name = (getattr(file_storage, "filename", "") or "").lower()
    allowed_mimes = {
        "audio/webm",
        "audio/ogg",
        "application/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/aac",
        "audio/mp4a-latm",
        "audio/wav",
        "audio/x-wav",
        "audio/x-m4a",
        "audio/m4a",
        "application/octet-stream",
        "video/webm",
    }
    allowed_ext = (".webm", ".ogg", ".oga", ".m4a", ".mp3", ".aac", ".wav")
    if mime and mime not in allowed_mimes and not mime.startswith("audio/"):
        logging.warning(
            "COMM_V2_VOICE_MIME_REJECTED filename=%s mime_type=%s duration_seconds=%s",
            name,
            mime,
            metadata.get("duration_seconds") or 0,
        )
        return _err("That recording format is not supported. Try recording again.", 400, "unsupported_voice_mime")
    if name and not name.endswith(allowed_ext):
        logging.warning(
            "COMM_V2_VOICE_EXTENSION_REJECTED filename=%s mime_type=%s duration_seconds=%s",
            name,
            mime,
            metadata.get("duration_seconds") or 0,
        )
        return _err("Voice notes must be audio recordings.", 400, "unsupported_voice_extension")
    duration = float(metadata.get("duration_seconds") or 0)
    max_duration = int(os.getenv("COMM_V2_VOICE_MAX_SECONDS", "300") or 300)
    if duration <= 0:
        return _err("Record a voice note before sending.", 400, "missing_voice_duration")
    if duration > max_duration:
        return _err(f"Voice notes can be up to {max_duration // 60} minutes.", 400, "voice_duration_exceeded")
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = int(file_storage.stream.tell() or 0)
        file_storage.stream.seek(0)
    except Exception:
        size = 0
    max_bytes = int(float(os.getenv("COMM_V2_VOICE_MAX_MB", os.getenv("MEDIA_UPLOAD_MAX_AUDIO_MB", "15"))) * 1024 * 1024)
    if size and size > max_bytes:
        return _err("Voice note is too large. Record a shorter note and try again.", 400, "voice_size_exceeded")
    return {"ok": True}


def _validate_attachment_upload(file_storage, metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    name = (getattr(file_storage, "filename", "") or "").lower()
    mime = (getattr(file_storage, "mimetype", "") or "").lower()
    blocked_ext = (".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".js", ".jar", ".msi", ".ps1", ".sh")
    if name.endswith(blocked_ext):
        return _err("That file type is blocked for safety.", 400, "blocked_attachment_type")
    kind = _clean(metadata.get("attachment_kind") or metadata.get("kind") or "", 40).lower()
    if not kind:
        kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "file"
    limits = {
        "image": int(float(os.getenv("COMM_V2_IMAGE_MAX_MB", "25")) * 1024 * 1024),
        "video": int(float(os.getenv("COMM_V2_VIDEO_MAX_MB", "250")) * 1024 * 1024),
        "audio": int(float(os.getenv("COMM_V2_AUDIO_MAX_MB", "25")) * 1024 * 1024),
        "voice_note": int(float(os.getenv("COMM_V2_VOICE_MAX_MB", "15")) * 1024 * 1024),
        "file": int(float(os.getenv("COMM_V2_FILE_MAX_MB", "50")) * 1024 * 1024),
    }
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = int(file_storage.stream.tell() or 0)
        file_storage.stream.seek(0)
    except Exception:
        size = 0
    limit = limits.get(kind, limits["file"])
    if size and size > limit:
        return _err(f"Attachment is too large. Limit: {max(1, round(limit / 1024 / 1024))} MB.", 400, "attachment_size_exceeded")
    return {"ok": True, "attachment_kind": kind, "mime_type": mime, "virus_scan": "pending_hook", "moderation_scan": "pending_hook"}


def _message_payload(cur, message: dict, viewer_user_id: int) -> dict:
    message_id = int(message.get("id") or 0)
    cur.execute("SELECT * FROM comm_v2_attachments WHERE message_id=? ORDER BY id ASC", (message_id,))
    attachments = [_attachment_payload(_row(row)) for row in cur.fetchall()]
    cur.execute("SELECT reaction_type, COUNT(*) AS total FROM comm_v2_message_reactions WHERE message_id=? GROUP BY reaction_type", (message_id,))
    reactions = [{"reaction_type": row["reaction_type"], "count": int(row["total"] or 0)} for row in cur.fetchall()]
    cur.execute("SELECT reaction_type FROM comm_v2_message_reactions WHERE message_id=? AND user_id=? LIMIT 1", (message_id, int(viewer_user_id)))
    mine = _row(cur.fetchone())
    receipt_state = message.get("delivery_status") or "sent"
    if int(message.get("sender_user_id") or 0) == int(viewer_user_id):
        cur.execute("SELECT COUNT(*) AS total FROM comm_v2_read_receipts WHERE message_id=? AND COALESCE(seen_at,'')!=''", (message_id,))
        if int(_row(cur.fetchone()).get("total") or 0):
            receipt_state = "seen"
        else:
            cur.execute("SELECT COUNT(*) AS total FROM comm_v2_read_receipts WHERE message_id=? AND COALESCE(delivered_at,'')!=''", (message_id,))
            if int(_row(cur.fetchone()).get("total") or 0):
                receipt_state = "delivered"
    sender = _user_summary(cur, int(message.get("sender_user_id") or 0))
    reply_preview = None
    if int(message.get("reply_to_message_id") or 0):
        cur.execute("SELECT id, sender_user_id, body, message_type FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message.get("reply_to_message_id") or 0),))
        reply = _row(cur.fetchone())
        if reply:
            reply_type = reply.get("message_type") or "text"
            reply_preview = {"id": int(reply.get("id") or 0), "sender": _user_summary(cur, int(reply.get("sender_user_id") or 0)), "body": _safe_preview(reply.get("body") or "", reply_type), "message_type": reply_type}
    message_type = message.get("message_type") or "text"
    metadata = _json_loads(message.get("metadata_json"), {}) or {}
    pinned_by = _safe_int_list(metadata.get("pinned_by_user_ids"))
    return {
        "id": message_id,
        "message_id": message_id,
        "public_id": message.get("public_id") or "",
        "conversation_id": int(message.get("conversation_id") or 0),
        "sender_id": int(message.get("sender_user_id") or 0),
        "sender_user_id": int(message.get("sender_user_id") or 0),
        "sender_display_name": sender.get("display_name") or "",
        "sender_avatar": sender.get("avatar_url") or "",
        "sender": sender,
        "is_mine": int(message.get("sender_user_id") or 0) == int(viewer_user_id),
        "message_type": message_type,
        "body": _safe_preview(message.get("body") or "", message_type),
        "reply_to_message_id": int(message.get("reply_to_message_id") or 0),
        "thread_root_message_id": int(message.get("thread_root_message_id") or 0),
        "client_message_id": message.get("client_message_id") or "",
        "client_temp_id": message.get("client_message_id") or "",
        "delivery_status": receipt_state,
        "delivery_state": receipt_state,
        "moderation_status": message.get("moderation_status") or "approved",
        "reply_preview": reply_preview,
        "attachments": attachments,
        "reactions": reactions,
        "my_reaction": mine.get("reaction_type") or "",
        "created_at": message.get("created_at") or "",
        "updated_at": message.get("updated_at") or "",
        "edited_at": message.get("edited_at") or "",
        "is_edited": bool(message.get("edited_at")),
        "pinned": int(viewer_user_id) in pinned_by,
    }


def _message_payloads(cur, message_rows: list[dict], viewer_user_id: int) -> list[dict]:
    if not message_rows:
        return []
    message_ids = [int(item.get("id") or 0) for item in message_rows if int(item.get("id") or 0)]
    sender_ids = sorted({int(item.get("sender_user_id") or 0) for item in message_rows if int(item.get("sender_user_id") or 0)})
    attachment_map: dict[int, list[dict]] = {message_id: [] for message_id in message_ids}
    reaction_map: dict[int, list[dict]] = {message_id: [] for message_id in message_ids}
    mine_map: dict[int, str] = {}
    receipt_map: dict[int, str] = {}
    reply_map: dict[int, dict] = {}
    sender_map: dict[int, dict] = {}
    if message_ids:
        placeholders = ",".join(["?"] * len(message_ids))
        cur.execute(f"SELECT * FROM comm_v2_attachments WHERE message_id IN ({placeholders}) ORDER BY message_id, id ASC", tuple(message_ids))
        for row in cur.fetchall():
            item = _row(row)
            attachment_map.setdefault(int(item.get("message_id") or 0), []).append(_attachment_payload(item))
        cur.execute(
            f"""
            SELECT message_id, reaction_type, COUNT(*) AS total
            FROM comm_v2_message_reactions
            WHERE message_id IN ({placeholders})
            GROUP BY message_id, reaction_type
            """,
            tuple(message_ids),
        )
        for row in cur.fetchall():
            reaction_map.setdefault(int(row["message_id"]), []).append({"reaction_type": row["reaction_type"], "count": int(row["total"] or 0)})
        cur.execute(
            f"SELECT message_id, reaction_type FROM comm_v2_message_reactions WHERE user_id=? AND message_id IN ({placeholders})",
            (int(viewer_user_id), *message_ids),
        )
        mine_map = {int(row["message_id"]): row["reaction_type"] or "" for row in cur.fetchall()}
        cur.execute(
            f"""
            SELECT message_id,
                   MAX(CASE WHEN COALESCE(seen_at,'')!='' THEN 1 ELSE 0 END) AS seen,
                   MAX(CASE WHEN COALESCE(delivered_at,'')!='' THEN 1 ELSE 0 END) AS delivered
            FROM comm_v2_read_receipts
            WHERE message_id IN ({placeholders})
            GROUP BY message_id
            """,
            tuple(message_ids),
        )
        for row in cur.fetchall():
            receipt_map[int(row["message_id"])] = "seen" if int(row["seen"] or 0) else "delivered" if int(row["delivered"] or 0) else "sent"
        reply_ids = sorted({int(item.get("reply_to_message_id") or 0) for item in message_rows if int(item.get("reply_to_message_id") or 0)})
        if reply_ids:
            reply_placeholders = ",".join(["?"] * len(reply_ids))
            cur.execute(f"SELECT id, sender_user_id, body, message_type FROM comm_v2_messages WHERE id IN ({reply_placeholders})", tuple(reply_ids))
            for row in cur.fetchall():
                reply = _row(row)
                reply_type = reply.get("message_type") or "text"
                reply_map[int(reply.get("id") or 0)] = {"id": int(reply.get("id") or 0), "sender_user_id": int(reply.get("sender_user_id") or 0), "body": _safe_preview(reply.get("body") or "", reply_type), "message_type": reply_type}
    if sender_ids:
        placeholders = ",".join(["?"] * len(sender_ids))
        cur.execute(
            f"SELECT user_id, username, display_name, avatar_url FROM users WHERE user_id IN ({placeholders})",
            tuple(sender_ids),
        )
        sender_map = {
            int(row["user_id"]): {
                "user_id": int(row["user_id"] or 0),
                "display_name": row["display_name"] or row["username"] or f"Member {row['user_id']}",
                "username": row["username"] or "",
                "avatar_url": row["avatar_url"] or "",
            }
            for row in cur.fetchall()
        }
    out = []
    for message in message_rows:
        message_id = int(message.get("id") or 0)
        sender_user_id = int(message.get("sender_user_id") or 0)
        reply_preview = reply_map.get(int(message.get("reply_to_message_id") or 0))
        if reply_preview:
            reply_preview = {**reply_preview, "sender": sender_map.get(int(reply_preview.get("sender_user_id") or 0), {"display_name": f"Member {reply_preview.get('sender_user_id')}"})}
        message_type = message.get("message_type") or "text"
        metadata = _json_loads(message.get("metadata_json"), {}) or {}
        pinned_by = _safe_int_list(metadata.get("pinned_by_user_ids"))
        out.append({
            "id": message_id,
            "message_id": message_id,
            "public_id": message.get("public_id") or "",
            "conversation_id": int(message.get("conversation_id") or 0),
            "sender_id": sender_user_id,
            "sender_user_id": sender_user_id,
            "sender_display_name": (sender_map.get(sender_user_id) or {}).get("display_name") or f"Member {sender_user_id}",
            "sender_avatar": (sender_map.get(sender_user_id) or {}).get("avatar_url") or "",
            "sender": sender_map.get(sender_user_id) or {
                "user_id": sender_user_id,
                "display_name": f"Member {sender_user_id}",
                "username": "",
                "avatar_url": "",
            },
            "is_mine": sender_user_id == int(viewer_user_id),
            "message_type": message_type,
            "body": _safe_preview(message.get("body") or "", message_type),
            "reply_to_message_id": int(message.get("reply_to_message_id") or 0),
            "thread_root_message_id": int(message.get("thread_root_message_id") or 0),
            "client_message_id": message.get("client_message_id") or "",
            "client_temp_id": message.get("client_message_id") or "",
            "delivery_status": receipt_map.get(message_id, message.get("delivery_status") or "sent") if sender_user_id == int(viewer_user_id) else message.get("delivery_status") or "sent",
            "delivery_state": receipt_map.get(message_id, message.get("delivery_status") or "sent") if sender_user_id == int(viewer_user_id) else message.get("delivery_status") or "sent",
            "moderation_status": message.get("moderation_status") or "approved",
            "reply_preview": reply_preview,
            "attachments": attachment_map.get(message_id, []),
            "reactions": reaction_map.get(message_id, []),
            "my_reaction": mine_map.get(message_id, ""),
            "created_at": message.get("created_at") or "",
            "updated_at": message.get("updated_at") or "",
            "edited_at": message.get("edited_at") or "",
            "is_edited": bool(message.get("edited_at")),
            "pinned": int(viewer_user_id) in pinned_by,
        })
    return out


def list_messages(user_id: int, conversation_ref: int | str, filters: dict | None = None) -> dict:
    disabled = _disabled("list_messages")
    if disabled:
        return disabled
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 40), 80))
    fetch_limit = limit + 1
    before_id = int(filters.get("before_id") or 0)
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access == "denied":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        if access == "blocked":
            return _err("Messaging is unavailable for this conversation.", 403, "blocked")
        conversation_id = int(conversation["id"])
        if before_id:
            cur.execute(
                """
                SELECT m.* FROM comm_v2_messages m
                LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=?
                WHERE m.conversation_id=? AND m.id<? AND COALESCE(m.deleted_at,'')='' AND d.id IS NULL
                ORDER BY m.id DESC LIMIT ?
                """,
                (int(user_id), conversation_id, before_id, fetch_limit),
            )
        else:
            cur.execute(
                """
                SELECT m.* FROM comm_v2_messages m
                LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=?
                WHERE m.conversation_id=? AND COALESCE(m.deleted_at,'')='' AND d.id IS NULL
                ORDER BY m.id DESC LIMIT ?
                """,
                (int(user_id), conversation_id, fetch_limit),
            )
        fetched = [_row(row) for row in cur.fetchall()]
        has_older = len(fetched) > limit
        fetched = fetched[:limit]
        raw_messages = list(reversed(fetched))
        messages = _message_payloads(cur, raw_messages, user_id)
        now = _now()
        oldest_message_id = int(raw_messages[0].get("id") or 0) if raw_messages else 0
        latest_incoming = next(
            (message for message in reversed(raw_messages) if int(message.get("sender_user_id") or 0) != int(user_id)),
            None,
        )
        typing = typing_state(user_id, conversation_id, existing_conn=(conn, cur)).get("typing") or []
        read_state_committed = False
        try:
            for message in raw_messages:
                if int(message.get("sender_user_id") or 0) != int(user_id):
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO comm_v2_read_receipts
                        (message_id, conversation_id, user_id, delivered_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (int(message.get("id") or 0), conversation_id, int(user_id), now, now, now),
                    )
                    cur.execute(
                        "UPDATE comm_v2_read_receipts SET delivered_at=COALESCE(NULLIF(delivered_at,''), ?), updated_at=? WHERE message_id=? AND user_id=?",
                        (now, now, int(message.get("id") or 0), int(user_id)),
                    )
            mark_read(user_id, conversation_id, existing_conn=(conn, cur), commit=False)
            conn.commit()
            read_state_committed = True
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logging.info(
                "COMM_V2_READ_STATE_DEFERRED user_id=%s conversation_id=%s error=%s",
                int(user_id),
                conversation_id,
                exc.__class__.__name__,
            )
        if latest_incoming and read_state_committed:
            _dispatch_command_center_async(
                "enqueue_message_delivered",
                conversation_id,
                int(latest_incoming.get("id") or 0),
                int(user_id),
                int(latest_incoming.get("sender_user_id") or 0),
            )
            _dispatch_command_center_async(
                "enqueue_message_read",
                conversation_id,
                int(latest_incoming.get("id") or 0),
                int(user_id),
                int(latest_incoming.get("sender_user_id") or 0),
            )
        return _ok({
            "conversation": _conversation_payload(cur, conversation, user_id),
            "messages": messages,
            "typing": typing,
            "has_older": has_older,
            "oldest_message_id": oldest_message_id,
            "limit": limit,
        })
    finally:
        conn.close()


def ai_context_for_conversation(user_id: int, conversation_ref: int | str, limit: int = 30) -> dict:
    disabled = _disabled("ai_context")
    if disabled:
        return disabled
    limit = max(1, min(int(limit or 30), 60))
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access == "denied":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        if access == "blocked":
            return _err("Messaging is unavailable for this conversation.", 403, "blocked")
        conversation_id = int(conversation["id"])
        cur.execute(
            """
            SELECT id, sender_user_id, message_type, body, created_at
            FROM comm_v2_messages
            WHERE conversation_id=? AND COALESCE(deleted_at,'')=''
            ORDER BY id DESC LIMIT ?
            """,
            (conversation_id, limit),
        )
        rows = list(reversed([_row(row) for row in cur.fetchall()]))
        messages = []
        for row in rows:
            message_type = str(row.get("message_type") or "text").lower()
            body = _safe_preview(row.get("body") or "", message_type, "")
            if not body and message_type != "text":
                body = _preview_label(message_type, "Attachment")
            messages.append({
                "message_id": int(row.get("id") or 0),
                "role": "me" if int(row.get("sender_user_id") or 0) == int(user_id) else "member",
                "message_type": message_type[:30],
                "body": body[:500],
                "created_at": row.get("created_at") or "",
            })
        return _ok({
            "conversation_id": conversation_id,
            "conversation": _conversation_payload(cur, conversation, user_id),
            "messages": messages,
            "limit": limit,
        })
    finally:
        conn.close()


def search_messages(user_id: int, query: str = "", filters: dict | None = None) -> dict:
    disabled = _disabled("search_messages")
    if disabled:
        return disabled
    query = _clean(query, 200)
    if not query:
        return _ok({"messages": [], "items": []})
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 25), 50))
    conn, cur = _open_db()
    try:
        conversation_ref = filters.get("conversation_id") or filters.get("conversation_ref") or filters.get("thread_id") or ""
        conversation_id = 0
        if conversation_ref:
            conversation, access = _conversation_access(cur, user_id, conversation_ref)
            if access == "missing":
                return _err("Conversation not found.", 404, "not_found")
            if access != "ok":
                return _err("You do not have access to this conversation.", 403, "forbidden")
            conversation_id = int(conversation["id"])
        conversation_clause = "AND m.conversation_id=?" if conversation_id else ""
        params = [int(user_id), f"%{query}%"]
        if conversation_id:
            params.append(conversation_id)
        params.append(limit)
        cur.execute(
            f"""
            SELECT DISTINCT m.*
            FROM comm_v2_messages m
            JOIN comm_v2_conversations c ON c.id=m.conversation_id
            LEFT JOIN comm_v2_participants p ON p.conversation_id=c.id AND p.user_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            WHERE COALESCE(m.deleted_at,'')='' AND COALESCE(c.deleted_at,'')='' AND c.status='active'
              AND m.body LIKE ?
              {conversation_clause}
              AND (p.id IS NOT NULL OR (c.conversation_type='room' AND c.privacy='public' AND c.is_discoverable=1))
            ORDER BY m.id DESC
            LIMIT ?
            """,
            params,
        )
        items = _message_payloads(cur, [_row(row) for row in cur.fetchall()], user_id)
        return _ok({"messages": items, "items": items, "query": query, "conversation_id": conversation_id or ""})
    finally:
        conn.close()


def search_people(user_id: int, query: str = "", filters: dict | None = None) -> dict:
    disabled = _disabled("search_people")
    if disabled:
        return disabled
    query = _clean(query, 160)
    if len(query) < 2:
        return _ok({"people": [], "items": [], "query": query})
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 12), 25))
    like = f"%{query.lower()}%"
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            SELECT user_id, username, display_name, avatar_url,
                   CASE WHEN LOWER(COALESCE(email,'')) LIKE ? THEN 1 ELSE 0 END AS matched_email
            FROM users
            WHERE user_id!=?
              AND COALESCE(account_status,'active')!='deleted'
              AND (
                LOWER(COALESCE(display_name,'')) LIKE ?
                OR LOWER(COALESCE(username,'')) LIKE ?
                OR LOWER(COALESCE(email,'')) LIKE ?
              )
            ORDER BY
              CASE WHEN LOWER(COALESCE(username,''))=? THEN 0
                   WHEN LOWER(COALESCE(display_name,''))=? THEN 1
                   WHEN LOWER(COALESCE(username,'')) LIKE ? THEN 2
                   ELSE 3 END,
              COALESCE(display_name, username, 'Pulse member') ASC
            LIMIT ?
            """,
            (like, int(user_id), like, like, like, query.lower(), query.lower(), f"{query.lower()}%", limit),
        )
        items = []
        for row in cur.fetchall():
            item = dict(row)
            items.append({
                "user_id": int(item.get("user_id") or 0),
                "display_name": item.get("display_name") or item.get("username") or "Pulse member",
                "username": item.get("username") or "",
                "avatar_url": item.get("avatar_url") or "",
                "matched_email": bool(item.get("matched_email")),
            })
        return _ok({"people": items, "items": items, "query": query})
    finally:
        conn.close()


def mark_read(user_id: int, conversation_ref: int | str, existing_conn=None, commit: bool = True) -> dict:
    disabled = _disabled("mark_read")
    if disabled:
        return disabled
    own_conn = existing_conn is None
    conn, cur = existing_conn or _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        conversation_id = int(conversation["id"])
        cur.execute("SELECT COALESCE(MAX(id),0) AS max_id FROM comm_v2_messages WHERE conversation_id=? AND COALESCE(deleted_at,'')=''", (conversation_id,))
        max_id = int(_row(cur.fetchone()).get("max_id") or 0)
        now = _now()
        cur.execute(
            "UPDATE comm_v2_participants SET last_read_message_id=?, last_read_at=?, unread_count=0, last_seen_at=?, updated_at=? WHERE conversation_id=? AND user_id=?",
            (max_id, now, now, now, conversation_id, int(user_id)),
        )
        if _read_receipts_allowed(cur, user_id, conversation_id):
            cur.execute("SELECT id FROM comm_v2_messages WHERE conversation_id=? AND id<=? AND sender_user_id!=? AND COALESCE(deleted_at,'')=''", (conversation_id, max_id, int(user_id)))
            for row in cur.fetchall():
                cur.execute(
                    """
                    INSERT OR IGNORE INTO comm_v2_read_receipts
                    (message_id, conversation_id, user_id, delivered_at, seen_at, read_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (int(row["id"]), conversation_id, int(user_id), now, now, now, now, now),
                )
                cur.execute(
                    "UPDATE comm_v2_read_receipts SET delivered_at=COALESCE(NULLIF(delivered_at,''), ?), seen_at=?, read_at=?, updated_at=? WHERE message_id=? AND user_id=?",
                    (now, now, now, now, int(row["id"]), int(user_id)),
                )
        if commit:
            conn.commit()
            if max_id:
                _dispatch_command_center_async(
                    "enqueue_message_read",
                    conversation_id,
                    max_id,
                    int(user_id),
                    0,
                )
                try:
                    from services import realtime_engine

                    payload = {
                        "conversation_id": int(conversation_id),
                        "message_id": int(max_id),
                        "reader_user_id": int(user_id),
                        "user_id": int(user_id),
                        "read_at": now,
                    }
                    realtime_engine.publish_event(f"comm_v2:conversation:{int(conversation_id)}", "message_read", payload)
                    realtime_engine.publish_event(f"cc:conversation:{int(conversation_id)}", "message_read", payload)
                    realtime_engine.publish_event(
                        f"comm_v2:user:{int(user_id)}",
                        "unread_count_updated",
                        {"conversation_id": int(conversation_id), "unread_count": 0, "chat_unread_count": _chat_unread_count_for_user(int(user_id))},
                    )
                    for recipient_id in _participant_ids(cur, int(conversation_id)):
                        if int(recipient_id) == int(user_id):
                            continue
                        user_payload = {**payload, "recipient_user_id": int(recipient_id)}
                        realtime_engine.publish_event(f"comm_v2:user:{int(recipient_id)}", "message_read", user_payload)
                        realtime_engine.publish_event(f"cc:user:{int(recipient_id)}", "message_read", user_payload)
                except Exception:
                    logging.info("COMM_V2_READ_REALTIME_SKIPPED conversation_id=%s user_id=%s", conversation_id, user_id)
        return _ok({"conversation_id": conversation_id, "last_read_message_id": max_id})
    finally:
        if own_conn:
            conn.close()


def toggle_pin(user_id: int, conversation_ref: int | str) -> dict:
    disabled = _disabled("toggle_pin")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        conversation_id = int(conversation["id"])
        cur.execute("SELECT pinned_at FROM comm_v2_participants WHERE conversation_id=? AND user_id=? LIMIT 1", (conversation_id, int(user_id)))
        pinned = not bool(_row(cur.fetchone()).get("pinned_at"))
        now = _now()
        cur.execute(
            "UPDATE comm_v2_participants SET pinned_at=?, pinned_rank=?, updated_at=? WHERE conversation_id=? AND user_id=?",
            (now if pinned else "", 1 if pinned else 0, now, conversation_id, int(user_id)),
        )
        conn.commit()
        return _ok({"conversation_id": conversation_id, "pinned": pinned, "message": "Chat pinned." if pinned else "Chat unpinned."})
    finally:
        conn.close()


def mark_unread(user_id: int, conversation_ref: int | str) -> dict:
    disabled = _disabled("mark_unread")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        conversation_id = int(conversation["id"])
        cur.execute("SELECT COALESCE(MAX(id),0) AS max_id FROM comm_v2_messages WHERE conversation_id=? AND COALESCE(deleted_at,'')=''", (conversation_id,))
        latest_message_id = int(_row(cur.fetchone()).get("max_id") or 0)
        now = _now()
        cur.execute(
            "UPDATE comm_v2_participants SET unread_count=MAX(COALESCE(unread_count,0),1), last_read_message_id=MAX(0,?-1), updated_at=? WHERE conversation_id=? AND user_id=?",
            (latest_message_id, now, conversation_id, int(user_id)),
        )
        conn.commit()
        return _ok({"conversation_id": conversation_id, "unread_count": 1, "message": "Chat marked unread."})
    finally:
        conn.close()


def toggle_mute(user_id: int, conversation_ref: int | str, minutes: int = 8 * 60) -> dict:
    disabled = _disabled("toggle_mute")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        conversation_id = int(conversation["id"])
        cur.execute("SELECT muted_until, notifications_level FROM comm_v2_participants WHERE conversation_id=? AND user_id=? LIMIT 1", (conversation_id, int(user_id)))
        participant = _row(cur.fetchone())
        currently_muted = bool(participant.get("muted_until") and str(participant.get("muted_until")) > _now()) or str(participant.get("notifications_level") or "").lower() in {"none", "off", "muted", "silent"}
        now_dt = datetime.now(timezone.utc)
        muted_until = "" if currently_muted else (now_dt + timedelta(minutes=max(5, min(int(minutes or 480), 60 * 24 * 30)))).isoformat(timespec="seconds")
        now = _now()
        cur.execute(
            "UPDATE comm_v2_participants SET muted_until=?, notifications_level=?, updated_at=? WHERE conversation_id=? AND user_id=?",
            (muted_until, "all" if currently_muted else "muted", now, conversation_id, int(user_id)),
        )
        conn.commit()
        muted = not currently_muted
        return _ok({"conversation_id": conversation_id, "muted": muted, "muted_until": muted_until}, "Conversation muted." if muted else "Conversation unmuted.")
    finally:
        conn.close()


def archive_conversation(user_id: int, conversation_ref: int | str) -> dict:
    disabled = _disabled("archive_conversation")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        conversation_id = int(conversation["id"])
        now = _now()
        cur.execute(
            """
            UPDATE comm_v2_participants
            SET membership_state='archived', unread_count=0, left_at='', updated_at=?
            WHERE conversation_id=? AND user_id=?
            """,
            (now, conversation_id, int(user_id)),
        )
        conn.commit()
        return _ok({"conversation_id": conversation_id, "archived": True}, "Conversation archived.")
    finally:
        conn.close()


def heartbeat(user_id: int, status: str = "online") -> dict:
    disabled = _disabled("heartbeat")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        presence = _touch_presence(cur, user_id, status)
        conn.commit()
        return _ok({"presence": presence}, "Presence updated.")
    finally:
        conn.close()


def update_settings(user_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("update_settings")
    if disabled:
        return disabled
    payload = payload or {}
    privacy = _clean(payload.get("presence_privacy") or payload.get("presence") or "everyone", 20).lower()
    if privacy not in {"everyone", "contacts", "nobody"}:
        return _err("Choose a valid presence privacy setting.", 400, "invalid_presence_privacy")
    read_receipts_enabled = 1 if payload.get("read_receipts_enabled", payload.get("read_receipts", True)) not in {False, 0, "0", "false", "off", "no"} else 0
    preview_privacy = _clean(payload.get("message_preview_privacy") or payload.get("message_previews") or "show", 20).lower()
    if preview_privacy not in {"show", "hide"}:
        return _err("Choose a valid message preview privacy setting.", 400, "invalid_message_preview_privacy")
    conn, cur = _open_db()
    try:
        now = _now()
        cur.execute(
            """
            INSERT OR IGNORE INTO comm_v2_user_settings (user_id, presence_privacy, read_receipts_enabled, message_preview_privacy, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(user_id), privacy, read_receipts_enabled, preview_privacy, now),
        )
        cur.execute(
            "UPDATE comm_v2_user_settings SET presence_privacy=?, read_receipts_enabled=?, message_preview_privacy=?, updated_at=? WHERE user_id=?",
            (privacy, read_receipts_enabled, preview_privacy, now, int(user_id)),
        )
        conn.commit()
        return _ok({"settings": {"presence_privacy": privacy, "read_receipts_enabled": bool(read_receipts_enabled), "message_preview_privacy": preview_privacy}}, "Communication settings saved.")
    finally:
        conn.close()


def get_settings(user_id: int) -> dict:
    disabled = _disabled("get_settings")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        settings = _settings(cur, user_id)
        return _ok({
            "settings": {
                "presence_privacy": settings.get("presence_privacy") or "everyone",
                "read_receipts_enabled": bool(settings.get("read_receipts_enabled", 1)),
                "message_preview_privacy": settings.get("message_preview_privacy") or "show",
            }
        })
    finally:
        conn.close()


def _conversation_control_stats(cur, conversation: dict, user_id: int) -> dict:
    conversation_id = int(conversation.get("id") or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS total
        FROM comm_v2_participants
        WHERE conversation_id=? AND membership_state='active' AND COALESCE(left_at,'')=''
        """,
        (conversation_id,),
    )
    member_count = int(_row(cur.fetchone()).get("total") or conversation.get("member_count") or 0)
    cur.execute(
        """
        SELECT unread_count, role, muted_until, notifications_level, pinned_at, membership_state
        FROM comm_v2_participants
        WHERE conversation_id=? AND user_id=? LIMIT 1
        """,
        (conversation_id, int(user_id)),
    )
    mine = _row(cur.fetchone())
    cur.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(COALESCE(NULLIF(file_size_bytes,0), file_size, 0)), 0) AS bytes
        FROM comm_v2_attachments
        WHERE conversation_id=? AND COALESCE(scan_status,'approved')!='blocked'
        """,
        (conversation_id,),
    )
    media = _row(cur.fetchone())
    cur.execute(
        """
        SELECT
          SUM(CASE WHEN LOWER(COALESCE(media_type,'')) IN ('image','photo','gif') OR LOWER(COALESCE(mime_type,'')) LIKE 'image/%' THEN 1 ELSE 0 END) AS photos,
          SUM(CASE WHEN LOWER(COALESCE(media_type,''))='video' OR LOWER(COALESCE(mime_type,'')) LIKE 'video/%' THEN 1 ELSE 0 END) AS videos,
          SUM(CASE WHEN LOWER(COALESCE(media_type,'')) IN ('voice','audio') OR LOWER(COALESCE(mime_type,'')) LIKE 'audio/%' THEN 1 ELSE 0 END) AS voice,
          SUM(CASE WHEN LOWER(COALESCE(media_type,'')) NOT IN ('image','photo','gif','video','voice','audio') AND LOWER(COALESCE(mime_type,'')) NOT LIKE 'image/%' AND LOWER(COALESCE(mime_type,'')) NOT LIKE 'video/%' AND LOWER(COALESCE(mime_type,'')) NOT LIKE 'audio/%' THEN 1 ELSE 0 END) AS files
        FROM comm_v2_attachments
        WHERE conversation_id=? AND COALESCE(scan_status,'approved')!='blocked'
        """,
        (conversation_id,),
    )
    media_types = _row(cur.fetchone())
    cur.execute(
        """
        SELECT COUNT(*) AS total
        FROM comm_v2_messages
        WHERE conversation_id=? AND COALESCE(deleted_at,'')='' AND message_type IN ('file','media','image','video','audio','voice')
        """,
        (conversation_id,),
    )
    media_message_count = int(_row(cur.fetchone()).get("total") or 0)
    cur.execute(
        "SELECT body FROM comm_v2_messages WHERE conversation_id=? AND COALESCE(deleted_at,'')='' AND body LIKE '%http%' ORDER BY id DESC LIMIT 250",
        (conversation_id,),
    )
    link_count = sum(len(_extract_urls(row["body"] or "")) for row in cur.fetchall())
    participant_ids = _participant_ids(cur, conversation_id)
    online_count = 0
    activity_status = "Offline"
    if participant_ids:
        # Presence for every participant comes from the one unified service so
        # the control centre can never disagree with the thread header or the
        # conversation list. The previous implementation counted rows in
        # comm_v2_presence directly, which is a second, independently-aged
        # presence store; the mission forbids subsystem-local presence logic.
        peer_ids = [int(pid) for pid in participant_ids if int(pid) != int(user_id)]
        presence_map = _user_presence_by_ids(cur, peer_ids, viewer_user_id=int(user_id))
        online_count = sum(1 for item in presence_map.values() if item.get("active_now"))
        if conversation.get("conversation_type") == "direct" and peer_ids:
            peer_presence = presence_map.get(peer_ids[0], {})
            if peer_presence.get("active_now"):
                activity_status = "Online"
            else:
                # Prefer the real last-seen sentence over a vague "Recently
                # active" so the control centre matches the spec's wording.
                activity_status = peer_presence.get("last_seen_text") or "Offline"
        else:
            activity_status = "Online" if online_count > 0 else "Offline"
    connection = "Connected"
    return {
        "encrypted": "Protected",
        "security_label": "Secured session",
        "members": member_count,
        "media_files": int(media.get("total") or 0) or media_message_count,
        "storage_used_bytes": int(media.get("bytes") or 0),
        "photos": int(media_types.get("photos") or 0),
        "videos": int(media_types.get("videos") or 0),
        "voice": int(media_types.get("voice") or 0),
        "files": int(media_types.get("files") or 0),
        "links": int(link_count or 0),
        "messages": _conversation_message_count(cur, conversation_id),
        "unread": int(mine.get("unread_count") or 0),
        "connection": connection,
        "activity_status": activity_status,
        "online_count": online_count,
        "role": mine.get("role") or "member",
        "pinned": bool(mine.get("pinned_at")),
        "muted": bool(mine.get("muted_until") and str(mine.get("muted_until")) > _now()) or str(mine.get("notifications_level") or "").lower() in {"none", "off", "muted", "silent"},
    }


def _conversation_message_count(cur, conversation_id: int) -> int:
    cur.execute(
        "SELECT COUNT(*) AS total FROM comm_v2_messages WHERE conversation_id=? AND COALESCE(deleted_at,'')=''",
        (int(conversation_id),),
    )
    return int(_row(cur.fetchone()).get("total") or 0)


def conversation_control_center(user_id: int, conversation_ref: int | str) -> dict:
    disabled = _disabled("conversation_control_center")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        _touch_presence(cur, user_id, "online")
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access != "ok":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        conversation_id = int(conversation["id"])
        payload = _conversation_payload(cur, conversation, user_id)
        cur.execute(
            """
            SELECT p.user_id, p.role, p.joined_at, p.last_seen_at,
                   COALESCE(u.display_name,u.username,'Pulse member') AS display_name,
                   COALESCE(u.avatar_url,'') AS avatar_url
            FROM comm_v2_participants p
            LEFT JOIN users u ON u.user_id=p.user_id
            WHERE p.conversation_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            ORDER BY CASE p.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'moderator' THEN 2 ELSE 3 END, p.id ASC
            LIMIT 24
            """,
            (conversation_id,),
        )
        members = [dict(row) for row in cur.fetchall()]
        # Pass the viewer. Without it privacy is evaluated against an anonymous
        # reader, so any member using contacts-only visibility reads as offline
        # even to the people they actually share this conversation with.
        presence_by_user = _user_presence_by_ids(cur, [int(member.get("user_id") or 0) for member in members], viewer_user_id=int(user_id))
        for member in members:
            presence = presence_by_user.get(int(member.get("user_id") or 0), {})
            member["presence"] = presence.get("status") or "offline"
            # One source for "is this person live": the service's own flag. The
            # old expression also re-derived it from the status string, which is
            # a second inference path that can drift from the first.
            member["active_now"] = bool(presence.get("active_now"))
        _, settings = _load_conversation_settings(cur, conversation_id, user_id)
        participant_stats = _conversation_control_stats(cur, conversation, user_id)
        actor_role = str(participant_stats.get("role") or "member").lower()
        payload.update({
            "is_group": payload.get("conversation_type") in {"group", "room", "community_channel"},
            "is_admin": actor_role in {"owner", "admin", "moderator"},
            "viewer_role": actor_role,
            "members": members,
            "stats": participant_stats,
            "settings": settings,
            "capabilities": {
                "search": True,
                "members": True,
                "shared_media": True,
                "message_stats": True,
                "pin": True,
                "archive": True,
                "mark_unread": True,
                "mute": True,
                "report": True,
                "block": payload.get("conversation_type") == "direct",
                "voice_call": True,
                "video_call": True,
                "effects": False,
                "export_chat": True,
                "schedule_message": False,
                "privacy_lock": False,
                "disappearing_messages": False,
            },
        })
        conn.commit()
        return _ok({"conversation": payload, "settings": settings, "stats": participant_stats})
    finally:
        conn.close()


def update_conversation_control_center(user_id: int, conversation_ref: int | str, payload: dict | None = None) -> dict:
    disabled = _disabled("update_conversation_control_center")
    if disabled:
        return disabled
    payload = payload or {}
    section = _clean(payload.get("section") or "", 40).lower()
    key = _clean(payload.get("key") or "", 80).lower()
    if section not in CONTROL_SETTING_ALLOWED:
        return _err("Choose a supported settings section.", 400, "invalid_section")
    if key not in CONTROL_SETTING_ALLOWED[section]:
        return _err("Choose a supported setting.", 400, "invalid_setting")
    ok, value, reason = _coerce_control_value(section, key, payload.get("value"))
    if not ok:
        return _err("Choose a valid setting value.", 400, reason or "invalid_value")
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access != "ok":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        conversation_id = int(conversation["id"])
        _, settings = _load_conversation_settings(cur, conversation_id, user_id)
        settings[section][key] = value
        if key == "message_preview" and section in {"notifications", "privacy"}:
            settings["notifications"]["message_preview"] = bool(value)
            settings["privacy"]["message_preview"] = bool(value)
        if key == "read_receipts" and section in {"notifications", "privacy"}:
            settings["notifications"]["read_receipts"] = bool(value)
            settings["privacy"]["read_receipts"] = bool(value)
        if section == "notifications" and key == "mute_choice":
            muted_until, notifications_level = _mute_until_for_choice(str(value))
            cur.execute(
                """
                UPDATE comm_v2_participants
                SET muted_until=?, notifications_level=?, updated_at=?
                WHERE conversation_id=? AND user_id=?
                """,
                (muted_until, notifications_level, _now(), conversation_id, int(user_id)),
            )
        if section == "privacy" and key in {"read_receipts", "message_preview", "online_status", "last_seen"}:
            current = _settings(cur, user_id)
            if key == "read_receipts":
                current["read_receipts_enabled"] = 1 if value else 0
            if key == "message_preview":
                current["message_preview_privacy"] = "show" if value else "hide"
            if key in {"online_status", "last_seen"}:
                both_visible = bool(settings["privacy"].get("online_status", True)) and bool(settings["privacy"].get("last_seen", True))
                current["presence_privacy"] = "everyone" if both_visible else "nobody"
            cur.execute(
                """
                INSERT OR IGNORE INTO comm_v2_user_settings (user_id, presence_privacy, read_receipts_enabled, message_preview_privacy, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    current.get("presence_privacy") or "everyone",
                    int(current.get("read_receipts_enabled", 1) or 0),
                    current.get("message_preview_privacy") or "show",
                    _now(),
                ),
            )
            cur.execute(
                "UPDATE comm_v2_user_settings SET presence_privacy=?, read_receipts_enabled=?, message_preview_privacy=?, updated_at=? WHERE user_id=?",
                (current.get("presence_privacy") or "everyone", int(current.get("read_receipts_enabled", 1) or 0), current.get("message_preview_privacy") or "show", _now(), int(user_id)),
            )
        _save_conversation_settings(cur, conversation_id, user_id, settings)
        conn.commit()
        return _ok({"conversation_id": conversation_id, "section": section, "key": key, "value": value, "settings": settings}, "Conversation setting saved.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _control_conversation(cur, user_id: int, conversation_ref: int | str) -> tuple[dict, int, dict | None]:
    conversation, access = _conversation_access(cur, user_id, conversation_ref)
    if access == "missing":
        return {}, 0, _err("Conversation not found.", 404, "not_found")
    if access != "ok":
        return {}, 0, _err("You do not have access to this conversation.", 403, "forbidden")
    return conversation, int(conversation["id"]), None


def _attachment_filter_clause(kind: str) -> str:
    normalized = str(kind or "").lower()
    if normalized in {"photo", "photos", "image", "images"}:
        return "AND (LOWER(COALESCE(a.media_type,'')) IN ('image','photo','gif') OR LOWER(COALESCE(a.mime_type,'')) LIKE 'image/%')"
    if normalized in {"video", "videos"}:
        return "AND (LOWER(COALESCE(a.media_type,''))='video' OR LOWER(COALESCE(a.mime_type,'')) LIKE 'video/%')"
    if normalized in {"voice", "voices", "audio"}:
        return "AND (LOWER(COALESCE(a.media_type,'')) IN ('voice','audio') OR LOWER(COALESCE(a.mime_type,'')) LIKE 'audio/%')"
    if normalized in {"file", "files"}:
        return "AND LOWER(COALESCE(a.mime_type,'')) NOT LIKE 'image/%' AND LOWER(COALESCE(a.mime_type,'')) NOT LIKE 'video/%' AND LOWER(COALESCE(a.mime_type,'')) NOT LIKE 'audio/%'"
    return ""


def _attachment_payload(row: dict) -> dict:
    url = row.get("playback_url") or row.get("cdn_url") or row.get("url") or row.get("thumbnail_url") or ""
    return {
        "id": int(row.get("id") or 0),
        "message_id": int(row.get("message_id") or 0),
        "media_type": row.get("media_type") or "file",
        "mime_type": row.get("mime_type") or "",
        "file_size_bytes": int(row.get("file_size_bytes") or row.get("file_size") or 0),
        "duration_seconds": float(row.get("duration_seconds") or 0),
        "url": url,
        "thumbnail_url": row.get("thumbnail_url") or "",
        "created_at": row.get("created_at") or row.get("message_created_at") or "",
        "sender_user_id": int(row.get("sender_user_id") or 0),
        "sender_display_name": row.get("sender_display_name") or "Pulse member",
        "body_preview": _safe_preview(row.get("body") or "", row.get("message_type") or "", "")[:180],
    }


def conversation_control_media(user_id: int, conversation_ref: int | str, filters: dict | None = None) -> dict:
    disabled = _disabled("conversation_control_media")
    if disabled:
        return disabled
    filters = filters or {}
    kind = _clean(filters.get("kind") or filters.get("type") or "all", 30).lower()
    limit = max(1, min(int(filters.get("limit") or 60), 120))
    conn, cur = _open_db()
    try:
        conversation, conversation_id, error = _control_conversation(cur, user_id, conversation_ref)
        if error:
            return error
        clause = _attachment_filter_clause(kind)
        cur.execute(
            f"""
            SELECT a.*, m.body, m.message_type, m.created_at AS message_created_at, m.sender_user_id,
                   COALESCE(u.display_name,u.username,'Pulse member') AS sender_display_name
            FROM comm_v2_attachments a
            JOIN comm_v2_messages m ON m.id=a.message_id
            LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=?
            LEFT JOIN users u ON u.user_id=m.sender_user_id
            WHERE a.conversation_id=?
              AND COALESCE(a.scan_status,'approved')!='blocked'
              AND COALESCE(m.deleted_at,'')=''
              AND d.id IS NULL
              {clause}
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (int(user_id), conversation_id, limit),
        )
        items = [_attachment_payload(_row(row)) for row in cur.fetchall()]
        return _ok({"conversation": _conversation_payload(cur, conversation, user_id), "items": items, "kind": kind, "count": len(items)})
    finally:
        conn.close()


def conversation_control_links(user_id: int, conversation_ref: int | str, filters: dict | None = None) -> dict:
    disabled = _disabled("conversation_control_links")
    if disabled:
        return disabled
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 80), 160))
    conn, cur = _open_db()
    try:
        conversation, conversation_id, error = _control_conversation(cur, user_id, conversation_ref)
        if error:
            return error
        cur.execute(
            """
            SELECT m.id, m.body, m.created_at, m.sender_user_id,
                   COALESCE(u.display_name,u.username,'Pulse member') AS sender_display_name
            FROM comm_v2_messages m
            LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=?
            LEFT JOIN users u ON u.user_id=m.sender_user_id
            WHERE m.conversation_id=? AND COALESCE(m.deleted_at,'')='' AND d.id IS NULL AND m.body LIKE '%http%'
            ORDER BY m.id DESC LIMIT ?
            """,
            (int(user_id), conversation_id, limit),
        )
        links = []
        for row in cur.fetchall():
            item = _row(row)
            for url in _extract_urls(item.get("body") or ""):
                parsed = urlparse(url)
                links.append({
                    "message_id": int(item.get("id") or 0),
                    "url": url,
                    "domain": parsed.netloc,
                    "created_at": item.get("created_at") or "",
                    "sender_user_id": int(item.get("sender_user_id") or 0),
                    "sender_display_name": item.get("sender_display_name") or "Pulse member",
                })
        return _ok({"conversation": _conversation_payload(cur, conversation, user_id), "items": links[:limit], "count": len(links[:limit])})
    finally:
        conn.close()


def conversation_control_pins(user_id: int, conversation_ref: int | str, filters: dict | None = None) -> dict:
    disabled = _disabled("conversation_control_pins")
    if disabled:
        return disabled
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 50), 100))
    conn, cur = _open_db()
    try:
        conversation, conversation_id, error = _control_conversation(cur, user_id, conversation_ref)
        if error:
            return error
        cur.execute(
            """
            SELECT m.*
            FROM comm_v2_messages m
            LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=?
            WHERE m.conversation_id=? AND COALESCE(m.deleted_at,'')='' AND d.id IS NULL
              AND COALESCE(m.metadata_json,'') LIKE '%pinned_by_user_ids%'
            ORDER BY m.id DESC LIMIT ?
            """,
            (int(user_id), conversation_id, limit * 3),
        )
        rows = []
        for row in cur.fetchall():
            message = _row(row)
            metadata = _json_loads(message.get("metadata_json"), {}) or {}
            pinned_by = _safe_int_list(metadata.get("pinned_by_user_ids"))
            if int(user_id) in pinned_by:
                rows.append(message)
            if len(rows) >= limit:
                break
        return _ok({"conversation": _conversation_payload(cur, conversation, user_id), "items": _message_payloads(cur, rows, user_id), "count": len(rows)})
    finally:
        conn.close()


def conversation_control_export(user_id: int, conversation_ref: int | str, filters: dict | None = None) -> dict:
    disabled = _disabled("conversation_control_export")
    if disabled:
        return disabled
    filters = filters or {}
    limit = max(1, min(int(filters.get("limit") or 500), 1000))
    conn, cur = _open_db()
    try:
        conversation, conversation_id, error = _control_conversation(cur, user_id, conversation_ref)
        if error:
            return error
        cur.execute(
            """
            SELECT m.id, m.sender_user_id, COALESCE(u.display_name,u.username,'Pulse member') AS sender_display_name,
                   m.message_type, m.body, m.created_at, m.edited_at
            FROM comm_v2_messages m
            LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=?
            LEFT JOIN users u ON u.user_id=m.sender_user_id
            WHERE m.conversation_id=? AND COALESCE(m.deleted_at,'')='' AND d.id IS NULL
            ORDER BY m.id ASC LIMIT ?
            """,
            (int(user_id), conversation_id, limit),
        )
        messages = []
        for row in cur.fetchall():
            item = _row(row)
            messages.append({
                "id": int(item.get("id") or 0),
                "sender_user_id": int(item.get("sender_user_id") or 0),
                "sender_display_name": item.get("sender_display_name") or "Pulse member",
                "message_type": item.get("message_type") or "text",
                "body": _safe_preview(item.get("body") or "", item.get("message_type") or "", "")[:4000],
                "created_at": item.get("created_at") or "",
                "edited_at": item.get("edited_at") or "",
            })
        return _ok({
            "conversation": _conversation_payload(cur, conversation, user_id),
            "export": {
                "conversation_id": conversation_id,
                "generated_at": _now(),
                "message_count": len(messages),
                "messages": messages,
            },
            "filename": f"pulsesoc-conversation-{conversation_id}.json",
        })
    finally:
        conn.close()


def conversation_control_action(user_id: int, conversation_ref: int | str, payload: dict | None = None) -> dict:
    disabled = _disabled("conversation_control_action")
    if disabled:
        return disabled
    payload = payload or {}
    action = _clean(payload.get("action") or "", 60).lower().replace("_", "-")
    conn, cur = _open_db()
    try:
        conversation, conversation_id, error = _control_conversation(cur, user_id, conversation_ref)
        if error:
            return error
        now = _now()
        if action == "report-conversation":
            reason = _clean(payload.get("reason") or "Reported from Conversation Control Center", 500)
            target_id = _conversation_peer_user_id(cur, conversation_id, user_id)
            cur.execute(
                "INSERT INTO comm_v2_reports (conversation_id, message_id, reporter_user_id, reported_user_id, reason, status, created_at) VALUES (?, 0, ?, ?, ?, 'open', ?)",
                (conversation_id, int(user_id), int(target_id or 0), reason, now),
            )
            report_id = int(cur.lastrowid)
            cur.execute(
                "INSERT INTO comm_v2_moderation_events (conversation_id, actor_user_id, target_user_id, event_type, reason, created_at) VALUES (?, ?, ?, 'conversation_reported', ?, ?)",
                (conversation_id, int(user_id), int(target_id or 0), reason, now),
            )
            conn.commit()
            return _ok({"conversation_id": conversation_id, "report_id": report_id}, "Conversation sent to moderation.")
        if action == "block-user":
            if conversation.get("conversation_type") != "direct":
                return _err("Block is available from direct conversations only.", 400, "group_block_not_supported")
            target_id = _conversation_peer_user_id(cur, conversation_id, user_id)
            if not target_id:
                return _err("No peer is available to block.", 400, "missing_peer")
            conn.commit()
            return block_user(user_id, target_id, payload.get("reason") or "Blocked from Conversation Control Center")
        if action == "clear-conversation":
            cur.execute(
                """
                INSERT OR IGNORE INTO comm_v2_message_deletions (message_id, conversation_id, user_id, deleted_at)
                SELECT id, conversation_id, ?, ? FROM comm_v2_messages
                WHERE conversation_id=? AND COALESCE(deleted_at,'')=''
                """,
                (int(user_id), now, conversation_id),
            )
            cur.execute("UPDATE comm_v2_participants SET unread_count=0, last_read_at=?, updated_at=? WHERE conversation_id=? AND user_id=?", (now, now, conversation_id, int(user_id)))
            conn.commit()
            return _ok({"conversation_id": conversation_id}, "Conversation cleared on this device and account.")
        if action == "delete-conversation":
            cur.execute("UPDATE comm_v2_participants SET membership_state='deleted', left_at=?, unread_count=0, updated_at=? WHERE conversation_id=? AND user_id=?", (now, now, conversation_id, int(user_id)))
            conn.commit()
            return _ok({"conversation_id": conversation_id, "deleted": True}, "Conversation removed from your inbox.")
        if action == "leave-group":
            if conversation.get("conversation_type") == "direct":
                return _err("Direct conversations cannot be left. Use Archive or Block.", 400, "not_group")
            cur.execute("UPDATE comm_v2_participants SET membership_state='left', left_at=?, unread_count=0, updated_at=? WHERE conversation_id=? AND user_id=?", (now, now, conversation_id, int(user_id)))
            conn.commit()
            return _ok({"conversation_id": conversation_id, "left": True}, "You left the conversation.")
        if action == "delete-media":
            cur.execute(
                """
                INSERT OR IGNORE INTO comm_v2_message_deletions (message_id, conversation_id, user_id, deleted_at)
                SELECT DISTINCT m.id, m.conversation_id, ?, ?
                FROM comm_v2_messages m
                JOIN comm_v2_attachments a ON a.message_id=m.id
                WHERE m.conversation_id=? AND COALESCE(m.deleted_at,'')=''
                """,
                (int(user_id), now, conversation_id),
            )
            conn.commit()
            return _ok({"conversation_id": conversation_id}, "Shared media hidden from your conversation view.")
        if action == "reset-settings":
            defaults = _control_defaults()
            _save_conversation_settings(cur, conversation_id, user_id, defaults)
            cur.execute("UPDATE comm_v2_participants SET muted_until='', notifications_level='all', updated_at=? WHERE conversation_id=? AND user_id=?", (now, conversation_id, int(user_id)))
            conn.commit()
            return _ok({"conversation_id": conversation_id, "settings": defaults}, "Conversation settings reset.")
        if action in {"create-note", "create-task"}:
            body = _clean(payload.get("body") or payload.get("title") or "", 1000)
            if not body:
                return _err("Add text before saving.", 400, "empty_item")
            item_type = "task" if action == "create-task" else "note"
            cur.execute(
                """
                INSERT INTO comm_v2_conversation_items
                (public_id, conversation_id, user_id, item_type, title, body, status, due_at, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, '{}', ?, ?)
                """,
                (_public_id("item"), conversation_id, int(user_id), item_type, body[:120], body, payload.get("due_at") or "", now, now),
            )
            conn.commit()
            return _ok({"conversation_id": conversation_id, "item_id": int(cur.lastrowid), "item_type": item_type}, "Saved to this conversation.")
        return _err("Choose a supported control action.", 400, "unsupported_action")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _conversation_peer_user_id(cur, conversation_id: int, viewer_user_id: int) -> int:
    cur.execute(
        """
        SELECT user_id FROM comm_v2_participants
        WHERE conversation_id=? AND user_id!=? AND membership_state='active' AND COALESCE(left_at,'')=''
        ORDER BY id ASC LIMIT 1
        """,
        (int(conversation_id), int(viewer_user_id)),
    )
    return int(_row(cur.fetchone()).get("user_id") or 0)


def set_typing(user_id: int, conversation_ref: int | str, is_typing: bool = True) -> dict:
    disabled = _disabled("set_typing")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        _, conversation_settings = _load_conversation_settings(cur, int(conversation["id"]), int(user_id))
        if (conversation_settings.get("privacy") or {}).get("typing_indicator") is False:
            is_typing = False
        now_dt = datetime.now(timezone.utc)
        expires = (now_dt + timedelta(seconds=5)).isoformat(timespec="seconds")
        now = now_dt.isoformat(timespec="seconds")
        cur.execute(
            """
            INSERT OR IGNORE INTO comm_v2_typing (conversation_id, user_id, is_typing, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(conversation["id"]), int(user_id), 1 if is_typing else 0, expires, now),
        )
        cur.execute(
            "UPDATE comm_v2_typing SET is_typing=?, expires_at=?, updated_at=? WHERE conversation_id=? AND user_id=?",
            (1 if is_typing else 0, expires, now, int(conversation["id"]), int(user_id)),
        )
        conn.commit()
        _dispatch_command_center_async(
            "enqueue_typing_event",
            int(conversation["id"]),
            int(user_id),
            bool(is_typing),
        )
        try:
            from services import realtime_engine

            sender = _user_summary(cur, int(user_id))
            event_type = "typing_started" if is_typing else "typing_stopped"
            payload = {
                "conversation_id": int(conversation["id"]),
                "user_id": int(user_id),
                "sender_id": int(user_id),
                "display_name": sender.get("display_name") or "",
                "is_typing": bool(is_typing),
                "typing": bool(is_typing),
                "expires_at": expires,
            }
            realtime_engine.publish_event(f"comm_v2:conversation:{int(conversation['id'])}", event_type, payload)
            realtime_engine.publish_event(f"cc:conversation:{int(conversation['id'])}", event_type, payload)
            for recipient_id in _participant_ids(cur, int(conversation["id"])):
                if int(recipient_id) == int(user_id):
                    continue
                user_payload = {**payload, "recipient_user_id": int(recipient_id)}
                realtime_engine.publish_event(f"comm_v2:user:{int(recipient_id)}", event_type, user_payload)
                realtime_engine.publish_event(f"cc:user:{int(recipient_id)}", event_type, user_payload)
        except Exception:
            logging.info("COMM_V2_TYPING_REALTIME_SKIPPED conversation_id=%s user_id=%s", int(conversation["id"]), user_id)
        return _ok({"conversation_id": int(conversation["id"]), "is_typing": bool(is_typing)})
    finally:
        conn.close()


def typing_state(user_id: int, conversation_ref: int | str, existing_conn=None) -> dict:
    disabled = _disabled("typing_state")
    if disabled:
        return disabled
    own_conn = existing_conn is None
    conn, cur = existing_conn or _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        cur.execute(
            """
            SELECT t.user_id, COALESCE(u.display_name,u.username,'Pulse member') AS display_name
            FROM comm_v2_typing t
            LEFT JOIN users u ON u.user_id=t.user_id
            WHERE t.conversation_id=? AND t.user_id!=? AND t.is_typing=1 AND t.expires_at>=?
            ORDER BY t.updated_at DESC LIMIT 8
            """,
            (int(conversation["id"]), int(user_id), _now()),
        )
        return _ok({"typing": [dict(row) for row in cur.fetchall()]})
    finally:
        if own_conn:
            conn.close()


def conversation_presence(user_id: int, conversation_ref: int | str) -> dict:
    disabled = _disabled("conversation_presence")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        _touch_presence(cur, user_id, "online")
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        cur.execute(
            """
            SELECT p.user_id, COALESCE(u.display_name,u.username,'Pulse member') AS display_name
            FROM comm_v2_participants p
            LEFT JOIN users u ON u.user_id=p.user_id
            WHERE p.conversation_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            ORDER BY p.id ASC
            """,
            (int(conversation["id"]),),
        )
        # Liveness and privacy both come from the unified presence service.
        #
        # This used to join comm_v2_presence and compare active_until itself,
        # which made it a second presence implementation that could disagree
        # with the conversation list. It also emitted status="hidden" for
        # blocked or invisible users -- a distinguishable value that let a
        # client detect it had been blocked simply by reading the field. Hidden
        # users are now indistinguishable from offline users, which is the
        # property the presence service guarantees.
        member_rows = [dict(row) for row in cur.fetchall()]
        target_ids = [int(item.get("user_id") or 0) for item in member_rows if int(item.get("user_id") or 0)]
        presence_map = _user_presence_by_ids(cur, target_ids, viewer_user_id=int(user_id))
        presence = []
        for item in member_rows:
            target_id = int(item.get("user_id") or 0)
            state = presence_map.get(target_id) or {}
            presence.append({
                "user_id": target_id,
                "display_name": item.get("display_name") or "Pulse member",
                "status": state.get("status") or "offline",
                "active_now": bool(state.get("active_now")),
                "activity": state.get("activity") or "idle",
                "activity_context": state.get("activity_context") or "",
                "last_seen_at": state.get("last_seen_at") or "",
                "last_seen_text": state.get("last_seen_text") or "",
            })
        typing = typing_state(user_id, int(conversation["id"]), existing_conn=(conn, cur)).get("typing") or []
        conn.commit()
        return _ok({"conversation_id": int(conversation["id"]), "presence": presence, "typing": typing})
    finally:
        conn.close()


def list_members(user_id: int, conversation_ref: int | str) -> dict:
    disabled = _disabled("list_members")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        cur.execute(
            """
            SELECT p.user_id, p.role, p.joined_at, p.last_seen_at, p.last_read_message_id,
                   COALESCE(u.display_name,u.username,'Pulse member') AS display_name,
                   COALESCE(u.avatar_url,'') AS avatar_url
            FROM comm_v2_participants p
            LEFT JOIN users u ON u.user_id=p.user_id
            WHERE p.conversation_id=? AND p.membership_state='active' AND COALESCE(p.left_at,'')=''
            ORDER BY CASE p.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'moderator' THEN 2 ELSE 3 END, p.id ASC
            """,
            (int(conversation["id"]),),
        )
        return _ok({"members": [dict(row) for row in cur.fetchall()], "conversation_id": int(conversation["id"])})
    finally:
        conn.close()


def add_member(user_id: int, conversation_ref: int | str, target_user_id: int, role: str = "member") -> dict:
    disabled = _disabled("add_member")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref)
        if access != "ok":
            return _err("Conversation not found." if access == "missing" else "You do not have access to this conversation.", 404 if access == "missing" else 403)
        cur.execute("SELECT role FROM comm_v2_participants WHERE conversation_id=? AND user_id=? LIMIT 1", (int(conversation["id"]), int(user_id)))
        actor_role = (_row(cur.fetchone()).get("role") or "member").lower()
        if actor_role not in {"owner", "admin", "moderator"}:
            return _err("Only chat moderators can add members.", 403, "forbidden")
        if _blocked_between(cur, user_id, [int(target_user_id)]):
            return _err("That member cannot be added.", 403, "blocked")
        _add_participant(cur, int(conversation["id"]), int(target_user_id), _clean(role, 40) or "member")
        conn.commit()
        return list_members(user_id, int(conversation["id"]))
    finally:
        conn.close()


def set_reaction(user_id: int, message_id: int, reaction_type: str = "heart") -> dict:
    disabled = _disabled("set_reaction")
    if disabled:
        return disabled
    reaction_type = _clean(reaction_type, 40).lower()
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(message["conversation_id"]))
        if access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        now = _now()
        cur.execute("DELETE FROM comm_v2_message_reactions WHERE message_id=? AND user_id=?", (int(message_id), int(user_id)))
        if reaction_type and reaction_type not in {"none", "remove"}:
            cur.execute(
                "INSERT INTO comm_v2_message_reactions (message_id, conversation_id, user_id, reaction_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (int(message_id), int(message["conversation_id"]), int(user_id), reaction_type, now, now),
            )
        conn.commit()
        _dispatch_command_center_async(
            "enqueue_message_event",
            "reaction_removed" if reaction_type in {"", "none", "remove"} else "reaction_added",
            int(message["conversation_id"]),
            int(message_id),
            int(user_id),
            None,
            {"reaction_type": reaction_type},
        )
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=?", (int(message_id),))
        return _ok({"message": _message_payload(cur, _row(cur.fetchone()), user_id)})
    finally:
        conn.close()


def edit_message(user_id: int, message_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("edit_message")
    if disabled:
        return disabled
    payload = payload or {}
    body = _clean(payload.get("body") or payload.get("message") or payload.get("content") or "", 4000)
    if not body:
        return _err("Edited message cannot be empty.", 400, "empty_message")
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        if int(message.get("sender_user_id") or 0) != int(user_id):
            return _err("You can only edit your own messages.", 403, "forbidden")
        created = datetime.fromisoformat(str(message.get("created_at") or _now()))
        if datetime.now(timezone.utc) - created > timedelta(minutes=int(payload.get("edit_window_minutes") or 15)):
            return _err("This message can no longer be edited.", 403, "edit_window_expired")
        now = _now()
        metadata = _json_loads(message.get("metadata_json"), {}) or {}
        history = metadata.get("edit_history") or []
        history.append({"body": message.get("body") or "", "edited_at": now})
        metadata["edit_history"] = history[-5:]
        cur.execute(
            "UPDATE comm_v2_messages SET body=?, metadata_json=?, edited_at=?, updated_at=? WHERE id=?",
            (body, json.dumps(metadata, default=str)[:4000], now, now, int(message_id)),
        )
        conn.commit()
        _dispatch_command_center_async(
            "enqueue_message_event",
            "message_edited",
            int(message["conversation_id"]),
            int(message_id),
            int(user_id),
            None,
            {"edited_at": now},
        )
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message_id),))
        return _ok({"message": _message_payload(cur, _row(cur.fetchone()), user_id)}, "Message edited.")
    finally:
        conn.close()


def delete_message(user_id: int, message_id: int, delete_for: str = "self") -> dict:
    disabled = _disabled("delete_message")
    if disabled:
        return disabled
    delete_for = _clean(delete_for, 40).lower()
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(message["conversation_id"]))
        if access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        now = _now()
        if delete_for in {"everyone", "all"}:
            if int(message.get("sender_user_id") or 0) != int(user_id):
                return _err("You can only delete your own message for everyone.", 403, "forbidden")
            created = datetime.fromisoformat(str(message.get("created_at") or _now()))
            if datetime.now(timezone.utc) - created > timedelta(minutes=30):
                return _err("This message can no longer be deleted for everyone.", 403, "delete_window_expired")
            cur.execute("UPDATE comm_v2_messages SET deleted_at=?, updated_at=? WHERE id=?", (now, now, int(message_id)))
            scope = "everyone"
        else:
            cur.execute(
                """
                INSERT OR IGNORE INTO comm_v2_message_deletions (message_id, conversation_id, user_id, deleted_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(message_id), int(message["conversation_id"]), int(user_id), now),
            )
            scope = "self"
        conn.commit()
        _dispatch_command_center_async(
            "enqueue_message_event",
            "message_deleted",
            int(message["conversation_id"]),
            int(message_id),
            int(user_id),
            None,
            {"delete_for": scope, "deleted_at": now},
        )
        return _ok({"message_id": int(message_id), "delete_for": scope}, "Message deleted.")
    finally:
        conn.close()


def toggle_message_pin(user_id: int, message_id: int) -> dict:
    disabled = _disabled("toggle_message_pin")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(message["conversation_id"]))
        if access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        metadata = _json_loads(message.get("metadata_json"), {}) or {}
        pinned_by = set(_safe_int_list(metadata.get("pinned_by_user_ids")))
        pinned = int(user_id) not in pinned_by
        if pinned:
            pinned_by.add(int(user_id))
        else:
            pinned_by.discard(int(user_id))
        metadata["pinned_by_user_ids"] = sorted(pinned_by)
        metadata["pinned_updated_at"] = _now()
        cur.execute("UPDATE comm_v2_messages SET metadata_json=?, updated_at=? WHERE id=?", (json.dumps(metadata, default=str)[:4000], _now(), int(message_id)))
        conn.commit()
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message_id),))
        return _ok({"message": _message_payload(cur, _row(cur.fetchone()), user_id), "pinned": pinned}, "Message pinned." if pinned else "Message unpinned.")
    finally:
        conn.close()


def forward_message(user_id: int, message_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("forward_message")
    if disabled:
        return disabled
    payload = payload or {}
    targets = [int(x) for x in payload.get("conversation_ids") or payload.get("target_conversation_ids") or [] if int(x or 0)]
    targets = sorted(set(targets))[:10]
    if not targets:
        return _err("Choose at least one conversation to forward to.", 400, "missing_targets")
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(message_id),))
        source = _row(cur.fetchone())
        if not source:
            return _err("Message not found.", 404, "not_found")
        _, source_access = _conversation_access(cur, user_id, int(source["conversation_id"]))
        if source_access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        created = []
        for target in targets:
            conversation, access = _conversation_access(cur, user_id, target, join_public=True)
            if access != "ok":
                continue
            now = _now()
            metadata = _json_loads(source.get("metadata_json"), {}) or {}
            metadata.update({"forwarded_from_message_id": int(message_id), "forwarded_at": now})
            cur.execute(
                """
                INSERT INTO comm_v2_messages
                (public_id, conversation_id, sender_user_id, message_type, body, delivery_status, moderation_status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'sent', 'approved', ?, ?, ?)
                """,
                (_public_id("msg"), int(conversation["id"]), int(user_id), source.get("message_type") or "text", source.get("body") or "", json.dumps(metadata, default=str)[:4000], now, now),
            )
            new_id = int(cur.lastrowid)
            cur.execute("SELECT * FROM comm_v2_attachments WHERE message_id=? ORDER BY id ASC", (int(message_id),))
            for attachment in cur.fetchall():
                item = _row(attachment)
                cur.execute(
                    """
                    INSERT INTO comm_v2_attachments
                    (attachment_public_id, message_id, conversation_id, media_upload_id, uploader_user_id, media_type, storage_provider, storage_key, url, cdn_url, playback_url, thumbnail_url, mime_type, file_size, file_size_bytes, width, height, mux_asset_id, mux_playback_id, mux_status, scan_status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_public_id("att"), new_id, int(conversation["id"]), int(item.get("media_upload_id") or 0), int(user_id), item.get("media_type") or "file", item.get("storage_provider") or "", item.get("storage_key") or "", item.get("url") or "", item.get("cdn_url") or "", item.get("playback_url") or "", item.get("thumbnail_url") or "", item.get("mime_type") or "", int(item.get("file_size") or 0), int(item.get("file_size_bytes") or 0), int(item.get("width") or 0), int(item.get("height") or 0), item.get("mux_asset_id") or "", item.get("mux_playback_id") or "", item.get("mux_status") or "", item.get("scan_status") or "approved", now),
                )
            cur.execute("UPDATE comm_v2_conversations SET last_message_id=?, last_message_at=?, last_activity_at=?, updated_at=? WHERE id=?", (new_id, now, now, now, int(conversation["id"])))
            created.append(new_id)
        conn.commit()
        return _ok({"forwarded_message_ids": created, "count": len(created)}, "Message forwarded.")
    finally:
        conn.close()


def report_message(user_id: int, message_id: int, reason: str = "") -> dict:
    disabled = _disabled("report_message")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(message["conversation_id"]))
        if access != "ok":
            return _err("You do not have access to this message.", 403, "forbidden")
        now = _now()
        cur.execute(
            "INSERT INTO comm_v2_reports (conversation_id, message_id, reporter_user_id, reported_user_id, reason, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (int(message["conversation_id"]), int(message_id), int(user_id), int(message.get("sender_user_id") or 0), _clean(reason, 500), now),
        )
        cur.execute(
            "INSERT INTO comm_v2_moderation_events (conversation_id, message_id, actor_user_id, target_user_id, event_type, reason, created_at) VALUES (?, ?, ?, ?, 'message_reported', ?, ?)",
            (int(message["conversation_id"]), int(message_id), int(user_id), int(message.get("sender_user_id") or 0), _clean(reason, 500), now),
        )
        conn.commit()
        return _ok({"report_id": int(cur.lastrowid)}, "Report sent to moderation.")
    finally:
        conn.close()


def block_user(user_id: int, blocked_user_id: int, reason: str = "") -> dict:
    disabled = _disabled("block_user")
    if disabled:
        return disabled
    if not blocked_user_id or int(blocked_user_id) == int(user_id):
        return _err("Choose a member to block.", 400, "invalid_user")
    conn, cur = _open_db()
    try:
        now = _now()
        cur.execute(
            "INSERT OR IGNORE INTO comm_v2_blocks (blocker_user_id, blocked_user_id, reason, status, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?)",
            (int(user_id), int(blocked_user_id), _clean(reason, 500), now, now),
        )
        cur.execute(
            "UPDATE comm_v2_blocks SET status='active', reason=?, updated_at=? WHERE blocker_user_id=? AND blocked_user_id=?",
            (_clean(reason, 500), now, int(user_id), int(blocked_user_id)),
        )
        conn.commit()
        return _ok({"blocked_user_id": int(blocked_user_id)}, "Member blocked.")
    finally:
        conn.close()


def infrastructure_diagnostics() -> dict:
    return {
        "ok": True,
        "status": "diagnostic",
        "enabled": flags.is_enabled(),
        "trace_id": _trace(),
        "diagnostics": infrastructure.diagnostics(),
    }


def stage_attachment_upload(user_id: int, file_storage, conversation_ref: int | str = "", metadata: dict | None = None) -> dict:
    disabled = _disabled("stage_attachment_upload")
    if disabled:
        return disabled
    if not file_storage:
        return _err("Choose an attachment to upload.", 400, "missing_file")
    voice_meta = _voice_upload_metadata(metadata)
    attachment_validation = _validate_attachment_upload(file_storage, metadata)
    if attachment_validation.get("ok") is False:
        return attachment_validation
    validation = _validate_voice_upload(file_storage, voice_meta)
    if validation.get("ok") is False:
        return validation
    context_id = "draft"
    if conversation_ref:
        conn, cur = _open_db()
        try:
            conversation, access = _conversation_access(cur, user_id, conversation_ref, join_public=True)
            if access == "missing":
                return _err("Conversation not found.", 404, "not_found")
            if access != "ok":
                return _err("You do not have access to this conversation.", 403, "forbidden")
            context_id = str(int(conversation["id"]))
        finally:
            conn.close()
    try:
        from services import upload_progress_service

        payload, status = upload_progress_service.stage_upload(
            int(user_id),
            file_storage,
            context_type="pulse_comm_v2",
            context_id=context_id,
        )
    except Exception:
        logging.exception("COMM_V2_ATTACHMENT_UPLOAD_FAILED user_id=%s", int(user_id or 0))
        return _err("Attachment upload could not be completed.", 500, "upload_failed")
    payload = payload or {}
    media = payload.get("media") or {}
    media_id = int(media.get("id") or media.get("media_id") or payload.get("media_id") or 0)
    if payload.get("ok") and media_id:
        duration_seconds = float(voice_meta.get("duration_seconds") or 0)
        waveform_json = json.dumps(voice_meta.get("waveform") or [])
        voice_note = 1 if voice_meta.get("is_voice") else 0
        conn, cur = _open_db()
        try:
            cur.execute(
                """
                UPDATE chat_media_uploads
                SET duration_seconds=?, waveform_json=?, voice_note=?
                WHERE id=? AND uploader_user_id=?
                """,
                (duration_seconds, waveform_json, voice_note, media_id, int(user_id)),
            )
            conn.commit()
        finally:
            conn.close()
        media["duration_seconds"] = duration_seconds
        media["waveform_json"] = waveform_json
        media["waveform"] = voice_meta.get("waveform") or []
        media["voice_note"] = bool(voice_note)
        media["media_type"] = "audio" if voice_note else media.get("media_type") or "file"
        payload["media"] = media
    payload.setdefault("http_status", status)
    if payload.get("ok") and payload.get("media"):
        payload["attachment_support"] = {
            "images": True,
            "files": True,
            "audio_voice_notes": True,
            "video_messages": "mux_preferred",
        }
    return payload


def create_comm_v2_mux_live_stream(user_id: int, conversation_ref: int | str, payload: dict | None = None) -> dict:
    disabled = _disabled("create_comm_v2_mux_live_stream")
    if disabled:
        return disabled
    payload = payload or {}
    conn, cur = _open_db()
    try:
        conversation, access = _conversation_access(cur, user_id, conversation_ref, join_public=True)
        if access == "missing":
            return _err("Conversation not found.", 404, "not_found")
        if access != "ok":
            return _err("You do not have access to this conversation.", 403, "forbidden")
        try:
            from services import mux_live_service

            mux = mux_live_service.create_mux_live_stream(
                title=_clean(payload.get("title") or conversation.get("title") or "Pulse Live Room", 180),
                record=bool(payload.get("record", True)),
                low_latency=bool(payload.get("low_latency", True)),
                metadata={"source": "pulse_comm_v2", "conversation_id": str(conversation["id"])},
            )
        except Exception:
            logging.exception("COMM_V2_MUX_LIVE_CREATE_FAILED user_id=%s conversation_id=%s", int(user_id), int(conversation["id"]))
            return _err("Mux live stream could not be created.", 500, "mux_live_failed")
        if not mux.get("ok"):
            return _err(mux.get("message") or "Mux Live is not configured yet.", 503, mux.get("status") or "mux_not_ready")
        now = _now()
        cur.execute(
            """
            INSERT INTO comm_v2_live_streams
            (public_id, conversation_id, creator_user_id, mux_live_stream_id, mux_stream_key, mux_playback_id, mux_live_status, mux_recording_asset_id, ingest_url, rtmp_url, playback_url, status, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
            """,
            (
                _public_id("live"),
                int(conversation["id"]),
                int(user_id),
                mux.get("mux_live_stream_id") or "",
                mux.get("mux_stream_key") or "",
                mux.get("mux_playback_id") or "",
                mux.get("mux_live_status") or "",
                mux.get("mux_recording_asset_id") or "",
                mux.get("ingest_url") or "",
                mux.get("rtmp_url") or "",
                mux.get("playback_url") or "",
                json.dumps({"provider": "mux", "raw_status": mux.get("mux_live_status") or ""})[:2000],
                now,
                now,
            ),
        )
        live_id = int(cur.lastrowid)
        conn.commit()
        return _ok({"live_stream": _live_stream_payload({**mux, "id": live_id, "creator_user_id": user_id}, include_stream_key=True)}, "Live room foundation created.")
    finally:
        conn.close()


def _find_live_stream(cur, live_ref: int | str) -> dict:
    live_ref = str(live_ref or "").strip()
    if live_ref.isdigit():
        cur.execute("SELECT * FROM comm_v2_live_streams WHERE id=? LIMIT 1", (int(live_ref),))
    else:
        cur.execute("SELECT * FROM comm_v2_live_streams WHERE public_id=? OR mux_live_stream_id=? LIMIT 1", (live_ref, live_ref))
    return _row(cur.fetchone())


def get_comm_v2_mux_live_stream(user_id: int, live_ref: int | str) -> dict:
    disabled = _disabled("get_comm_v2_mux_live_stream")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        live = _find_live_stream(cur, live_ref)
        if not live:
            return _err("Live stream not found.", 404, "not_found")
        conversation, access = _conversation_access(cur, user_id, int(live.get("conversation_id") or 0), join_public=True)
        if access != "ok":
            return _err("You do not have access to this live room.", 403, "forbidden")
        include_key = int(live.get("creator_user_id") or 0) == int(user_id)
        return _ok({"live_stream": _live_stream_payload(live, include_stream_key=include_key), "conversation_id": int(conversation.get("id") or 0)})
    finally:
        conn.close()


def disable_comm_v2_mux_live_stream(user_id: int, live_ref: int | str) -> dict:
    disabled = _disabled("disable_comm_v2_mux_live_stream")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        live = _find_live_stream(cur, live_ref)
        if not live:
            return _err("Live stream not found.", 404, "not_found")
        if int(live.get("creator_user_id") or 0) != int(user_id):
            return _err("Only the live room host can disable this stream.", 403, "forbidden")
        try:
            from services import mux_live_service

            mux = mux_live_service.disable_mux_live_stream(live.get("mux_live_stream_id") or "")
        except Exception:
            logging.exception("COMM_V2_MUX_LIVE_DISABLE_FAILED user_id=%s live_id=%s", int(user_id), int(live.get("id") or 0))
            mux = {"ok": False}
        now = _now()
        cur.execute(
            "UPDATE comm_v2_live_streams SET status='disabled', mux_live_status=?, updated_at=?, ended_at=COALESCE(ended_at, ?) WHERE id=?",
            (mux.get("mux_live_status") or "disabled", now, now, int(live["id"])),
        )
        conn.commit()
        return _ok({"live_stream_id": int(live["id"]), "mux": {"ok": bool(mux.get("ok")), "status": mux.get("mux_live_status") or "disabled"}}, "Live room disabled.")
    finally:
        conn.close()


def verify_mux_webhook_signature(payload: bytes, signature_header: str | None) -> dict:
    try:
        from services import mux_live_service

        return mux_live_service.verify_mux_webhook_signature(payload, signature_header)
    except Exception:
        logging.exception("COMM_V2_MUX_WEBHOOK_VERIFY_FAILED")
        return {"ok": False, "message": "Mux webhook verification failed."}


def process_mux_webhook(payload: dict) -> dict:
    event_type = _clean(payload.get("type") or "", 120)
    data = payload.get("data") or {}
    mux_live_stream_id = data.get("id") or data.get("live_stream_id") or ""
    if not mux_live_stream_id:
        return {"ok": True, "status": "ignored", "event_type": event_type, "message": "No live stream id in event."}
    conn, cur = _open_db()
    try:
        live = _find_live_stream(cur, mux_live_stream_id)
        if not live:
            return {"ok": True, "status": "unmatched", "event_type": event_type}
        now = _now()
        updates = {
            "video.live_stream.connected": "connected",
            "video.live_stream.disconnected": "disconnected",
            "video.live_stream.created": data.get("status") or "created",
            "video.asset.ready": "recording_ready",
            "video.asset.errored": "recording_error",
        }
        cur.execute(
            """
            UPDATE comm_v2_live_streams
            SET mux_live_status=?, mux_recording_asset_id=COALESCE(NULLIF(?, ''), mux_recording_asset_id), updated_at=?
            WHERE id=?
            """,
            (updates.get(event_type) or data.get("status") or event_type, data.get("asset_id") or data.get("id") or "", now, int(live["id"])),
        )
        conn.commit()
        return {"ok": True, "status": "processed", "event_type": event_type, "live_stream_id": int(live["id"])}
    finally:
        conn.close()


def _live_stream_payload(row: dict, *, include_stream_key: bool = False) -> dict:
    playback_id = row.get("mux_playback_id") or ""
    playback_url = row.get("playback_url") or ""
    if playback_id and not playback_url:
        try:
            from services import mux_live_service

            playback_url = mux_live_service.playback_url(playback_id)
        except Exception:
            playback_url = ""
    payload = {
        "id": int(row.get("id") or 0),
        "public_id": row.get("public_id") or "",
        "conversation_id": int(row.get("conversation_id") or 0),
        "creator_user_id": int(row.get("creator_user_id") or 0),
        "provider": "mux",
        "mux_live_stream_id": row.get("mux_live_stream_id") or "",
        "mux_playback_id": playback_id,
        "mux_live_status": row.get("mux_live_status") or row.get("status") or "",
        "mux_recording_asset_id": row.get("mux_recording_asset_id") or "",
        "mux_recording_playback_id": row.get("mux_recording_playback_id") or "",
        "playback_url": playback_url,
        "ingest_url": row.get("ingest_url") or row.get("rtmp_url") or "",
        "status": row.get("status") or "created",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }
    if include_stream_key:
        payload["mux_stream_key"] = row.get("mux_stream_key") or ""
    return payload


def twilio_notification_preview(user_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("twilio_notification_preview")
    if disabled:
        return disabled
    payload = payload or {}
    kind = _clean(payload.get("type") or "message_alert", 80)
    to_number = _clean(payload.get("to") or payload.get("to_number") or "", 80)
    if kind == "sms_verification":
        result = twilio_service.send_sms_verification(to_number, payload.get("code") or "000000", user_id=user_id)
    elif kind == "room_invite":
        result = twilio_service.send_room_invite_alert(to_number, payload.get("room_title") or "Pulse Room", payload.get("inviter") or "", user_id=user_id)
    elif kind == "security_alert":
        result = twilio_service.send_security_alert(to_number, payload.get("alert") or "Pulse security event", user_id=user_id)
    else:
        result = twilio_service.send_message_alert(to_number, payload.get("preview") or "Pulse message", user_id=user_id)
    result.setdefault("diagnostics", twilio_service.diagnostics())
    result.setdefault("trace_id", _trace())
    result.setdefault("enabled", flags.is_enabled())
    return result


def create_community(user_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("create_community")
    if disabled:
        return disabled
    payload = payload or {}
    name = _clean(payload.get("name") or "Pulse Community", 120)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or f"community-{secrets.token_hex(4)}"
    conn, cur = _open_db()
    try:
        now = _now()
        base_slug = slug
        counter = 1
        while True:
            cur.execute("SELECT id FROM comm_v2_communities WHERE slug=? LIMIT 1", (slug,))
            if not cur.fetchone():
                break
            counter += 1
            slug = f"{base_slug}-{counter}"[:90]
        cur.execute(
            "INSERT INTO comm_v2_communities (public_id, name, slug, description, owner_user_id, privacy, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (_public_id("com"), name, slug, _clean(payload.get("description") or "", 500), int(user_id), _clean(payload.get("privacy") or "public", 20), now, now),
        )
        community_id = int(cur.lastrowid)
        conn.commit()
        return _ok({"community": {"id": community_id, "name": name, "slug": slug}})
    finally:
        conn.close()


def create_channel(user_id: int, community_id: int, payload: dict | None = None) -> dict:
    disabled = _disabled("create_channel")
    if disabled:
        return disabled
    payload = payload or {}
    name = _clean(payload.get("name") or "general", 80)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or f"channel-{secrets.token_hex(4)}"
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_communities WHERE id=? AND owner_user_id=? AND COALESCE(deleted_at,'')='' LIMIT 1", (int(community_id), int(user_id)))
        community = _row(cur.fetchone())
        if not community:
            return _err("Community not found or not manageable.", 404, "not_found")
        convo = create_conversation(user_id, {"conversation_type": "community_channel", "title": name, "community_id": int(community_id)})
        if not convo.get("ok"):
            return convo
        base_slug = slug
        counter = 1
        while True:
            cur.execute("SELECT id FROM comm_v2_channels WHERE community_id=? AND slug=? LIMIT 1", (int(community_id), slug))
            if not cur.fetchone():
                break
            counter += 1
            slug = f"{base_slug}-{counter}"[:90]
        now = _now()
        cur.execute(
            "INSERT INTO comm_v2_channels (public_id, community_id, conversation_id, name, slug, description, channel_type, visibility, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (_public_id("ch"), int(community_id), int(convo.get("conversation_id") or 0), name, slug, _clean(payload.get("description") or "", 500), _clean(payload.get("channel_type") or "text", 40), _clean(payload.get("visibility") or "members", 40), now, now),
        )
        channel_id = int(cur.lastrowid)
        conn.commit()
        return _ok({"channel": {"id": channel_id, "name": name, "slug": slug, "conversation_id": int(convo.get("conversation_id") or 0)}})
    finally:
        conn.close()


def moderation_summary(admin_user: dict | None = None) -> dict:
    disabled = _disabled("moderation_summary")
    if disabled:
        return disabled
    conn, cur = _open_db()
    try:
        cur.execute("SELECT COUNT(*) AS total FROM comm_v2_reports WHERE status='open'")
        open_reports = int(_row(cur.fetchone()).get("total") or 0)
        cur.execute("SELECT COUNT(*) AS total FROM comm_v2_blocks WHERE status='active'")
        active_blocks = int(_row(cur.fetchone()).get("total") or 0)
        cur.execute(
            """
            SELECT r.*, COALESCE(u.display_name,u.username,'Member') AS reporter_name
            FROM comm_v2_reports r
            LEFT JOIN users u ON u.user_id=r.reporter_user_id
            ORDER BY r.id DESC LIMIT 25
            """
        )
        reports = [dict(row) for row in cur.fetchall()]
        return _ok({"moderation": {"open_reports": open_reports, "active_blocks": active_blocks, "recent_reports": reports, "admin": bool(admin_user)}})
    finally:
        conn.close()


def moderate_message(admin_user: dict, message_id: int, action: str, reason: str = "") -> dict:
    disabled = _disabled("moderate_message")
    if disabled:
        return disabled
    action = _clean(action, 40).lower()
    if action not in {"approve", "hide", "delete"}:
        return _err("Choose a moderation action.", 400, "invalid_action")
    conn, cur = _open_db()
    try:
        cur.execute("SELECT * FROM comm_v2_messages WHERE id=? LIMIT 1", (int(message_id),))
        message = _row(cur.fetchone())
        if not message:
            return _err("Message not found.", 404, "not_found")
        now = _now()
        status = "approved" if action == "approve" else "hidden"
        deleted_at = now if action == "delete" else (message.get("deleted_at") or "")
        cur.execute("UPDATE comm_v2_messages SET moderation_status=?, deleted_at=?, updated_at=? WHERE id=?", (status, deleted_at, now, int(message_id)))
        cur.execute(
            "INSERT INTO comm_v2_moderation_events (conversation_id, message_id, admin_user_id, target_user_id, event_type, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(message["conversation_id"]), int(message_id), int((admin_user or {}).get("id") or 0), int(message.get("sender_user_id") or 0), f"message_{action}", _clean(reason, 500), now),
        )
        conn.commit()
        return _ok({"message_id": int(message_id), "action": action})
    finally:
        conn.close()
