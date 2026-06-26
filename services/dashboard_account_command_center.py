"""Backend-managed PulseSoc dashboard Account Command Center.

This module owns the data and permission rules behind the Dashboard account
cards. It is intentionally additive: existing profile, security, and
verification routes keep working, while this layer provides consistent state,
audit records, and safe API payloads.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from services import db as db_service


VERIFICATION_STATUSES = {
    "not_started",
    "draft",
    "submitted",
    "in_review",
    "needs_more_info",
    "approved",
    "rejected",
    "suspended",
    "appealed",
}
VERIFICATION_TYPES = {"identity", "blue_check", "business", "government_id"}
ACCOUNT_HEALTH_STATUSES = {"secure", "watch", "restricted", "suspended"}
SETTING_KEYS = {
    "profile_visibility": {"public", "private"},
    "message_requests": {"everyone", "followers", "none"},
    "notifications_enabled": {"true", "false"},
    "status_replies": {"everyone", "followers", "none"},
    "ads_personalization": {"true", "false"},
    "reduced_motion": {"true", "false", "system"},
    "sci_fi_intensity": {"low", "medium", "high"},
    "language": {"en", "es", "fr", "ht"},
    "timezone": None,
}
RESERVED_USERNAMES = {
    "admin",
    "administrator",
    "moderator",
    "owner",
    "root",
    "security",
    "support",
    "system",
    "pulse",
    "pulsesoc",
    "staff",
    "official",
    "help",
    "null",
    "undefined",
}


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _row_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _table_exists(cur: Any, table: str) -> bool:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _columns(cur: Any, table: str) -> set[str]:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=?", (table,))
            return {str(_row_dict(row).get("column_name") or row[0]) for row in cur.fetchall()}
        cur.execute(f"PRAGMA table_info({table})")
        return {str(_row_dict(row).get("name") or row[1]) for row in cur.fetchall()}
    except Exception:
        return set()


def _add_column(cur: Any, table: str, name: str, definition: str) -> None:
    if name in _columns(cur, table):
        return
    if db_service.IS_POSTGRES:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {definition}")
    else:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def ensure_schema(conn: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            actor_user_id INTEGER,
            action TEXT,
            before_json TEXT,
            after_json TEXT,
            ip_hash TEXT,
            user_agent_hash TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS account_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            actor_user_id INTEGER,
            action TEXT,
            target_type TEXT,
            target_id TEXT,
            details_json TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            verification_type TEXT,
            status TEXT DEFAULT 'submitted',
            notes TEXT,
            reviewed_by INTEGER,
            created_at TEXT,
            reviewed_at TEXT
        )
        """
    )
    for name, definition in (
        ("request_payload_json", "TEXT"),
        ("decision_reason", "TEXT"),
        ("appeal_of_request_id", "INTEGER"),
        ("submitted_at", "TEXT"),
        ("decision_at", "TEXT"),
        ("updated_at", "TEXT"),
    ):
        _add_column(cur, "verification_requests", name, definition)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            user_id INTEGER,
            document_type TEXT,
            storage_path TEXT,
            original_filename TEXT,
            mime_type TEXT,
            file_size INTEGER DEFAULT 0,
            checksum TEXT,
            moderation_status TEXT DEFAULT 'private_review',
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS account_health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_type TEXT,
            severity TEXT,
            status TEXT DEFAULT 'open',
            public_summary TEXT,
            internal_note TEXT,
            actor_user_id INTEGER,
            expires_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS account_strikes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            policy_category TEXT,
            severity TEXT,
            status TEXT DEFAULT 'active',
            public_summary TEXT,
            internal_note TEXT,
            appeal_status TEXT,
            expires_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS account_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            policy_category TEXT,
            status TEXT DEFAULT 'active',
            public_summary TEXT,
            internal_note TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS account_restrictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            restriction_type TEXT,
            status TEXT DEFAULT 'active',
            public_summary TEXT,
            internal_note TEXT,
            expires_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS security_login_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_type TEXT,
            device_label TEXT,
            ip_hash TEXT,
            user_agent_hash TEXT,
            country TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS security_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            device_hash TEXT,
            device_label TEXT,
            platform TEXT,
            push_enabled INTEGER DEFAULT 0,
            trusted INTEGER DEFAULT 0,
            last_seen_at TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS active_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            session_hash TEXT,
            device_label TEXT,
            ip_hash TEXT,
            user_agent_hash TEXT,
            revoked_at TEXT,
            last_seen_at TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            setting_key TEXT,
            setting_value TEXT,
            updated_at TEXT,
            UNIQUE(user_id, setting_key)
        )
        """
    )
    for table, cols in {
        "profile_audit_logs": ("user_id", "created_at"),
        "verification_requests": ("user_id", "status"),
        "verification_documents": ("user_id", "request_id"),
        "account_health_events": ("user_id", "status"),
        "account_strikes": ("user_id", "status"),
        "account_warnings": ("user_id", "status"),
        "account_restrictions": ("user_id", "status"),
        "user_settings": ("user_id", "setting_key"),
    }.items():
        if not _table_exists(cur, table):
            continue
        for col in cols:
            try:
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON {table} ({col})")
            except Exception:
                pass
    conn.commit()


def safe_username(username: str) -> tuple[bool, str]:
    value = str(username or "").strip().lstrip("@")
    if not value:
        return True, ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", value):
        return False, "Use 3-40 letters, numbers, dots, underscores, or dashes for your handle."
    lowered = value.lower()
    if lowered in RESERVED_USERNAMES or lowered.startswith(("admin.", "support.", "security.")):
        return False, "That username is reserved for PulseSoc operations."
    return True, value


def profile_change_allowed(conn: Any, user_id: int, action: str = "profile_updated") -> tuple[bool, str]:
    ensure_schema(conn)
    cur = conn.cursor()
    since = (datetime.utcnow() - timedelta(hours=1)).isoformat(timespec="seconds")
    cur.execute(
        "SELECT COUNT(*) AS total FROM profile_audit_logs WHERE user_id=? AND action=? AND created_at>=?",
        (int(user_id), action, since),
    )
    row = _row_dict(cur.fetchone())
    if _safe_int(row.get("total"), 0) >= 12:
        return False, "Profile changes are temporarily rate limited. Try again later."
    return True, ""


def profile_snapshot(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "display_name": str(user.get("display_name") or "")[:120],
        "username": str(user.get("username") or "")[:80],
        "bio": str(user.get("bio") or "")[:1000],
        "avatar_url_present": bool(user.get("avatar_url")),
        "cover_url_present": bool(user.get("cover_url") or user.get("banner_url")),
        "profile_visibility": str(user.get("profile_visibility") or "public")[:40],
    }


def record_profile_audit(
    conn: Any,
    *,
    user_id: int,
    actor_user_id: int,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip_hash: str = "",
    user_agent_hash: str = "",
) -> None:
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO profile_audit_logs (user_id, actor_user_id, action, before_json, after_json, ip_hash, user_agent_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            int(actor_user_id or user_id),
            str(action or "profile_updated")[:80],
            json.dumps(before or {}, default=str)[:4000],
            json.dumps(after or {}, default=str)[:4000],
            str(ip_hash or "")[:120],
            str(user_agent_hash or "")[:120],
            _now(),
        ),
    )
    record_account_audit(conn, user_id=user_id, actor_user_id=actor_user_id or user_id, action=action, target_type="profile", target_id=str(user_id), details={"profile_audit": True})


def record_account_audit(
    conn: Any,
    *,
    user_id: int,
    actor_user_id: int,
    action: str,
    target_type: str,
    target_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO account_audit_logs (user_id, actor_user_id, action, target_type, target_id, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            int(actor_user_id or user_id),
            str(action or "")[:120],
            str(target_type or "")[:80],
            str(target_id or "")[:120],
            json.dumps(details or {}, default=str)[:4000],
            _now(),
        ),
    )


def get_settings(conn: Any, user_id: int) -> dict[str, str]:
    ensure_schema(conn)
    defaults = {
        "profile_visibility": "public",
        "message_requests": "everyone",
        "notifications_enabled": "true",
        "status_replies": "everyone",
        "ads_personalization": "true",
        "reduced_motion": "system",
        "sci_fi_intensity": "medium",
        "language": "en",
        "timezone": "America/New_York",
    }
    cur = conn.cursor()
    cur.execute("SELECT setting_key, setting_value FROM user_settings WHERE user_id=?", (int(user_id),))
    for row in cur.fetchall():
        item = _row_dict(row)
        key = str(item.get("setting_key") or "")
        if key in defaults:
            defaults[key] = str(item.get("setting_value") or defaults[key])[:120]
    return defaults


def update_settings(conn: Any, user_id: int, payload: dict[str, Any], actor_user_id: int | None = None) -> dict[str, Any]:
    ensure_schema(conn)
    cur = conn.cursor()
    existing = get_settings(conn, user_id)
    changed: dict[str, str] = {}
    for key, allowed in SETTING_KEYS.items():
        if key not in payload:
            continue
        value = str(payload.get(key) or "").strip()[:120]
        if allowed is not None and value not in allowed:
            raise ValueError(f"Invalid setting value for {key}.")
        if key == "timezone" and not re.fullmatch(r"[A-Za-z0-9_./+-]{1,80}", value):
            raise ValueError("Invalid timezone.")
        changed[key] = value
    now = _now()
    for key, value in changed.items():
        cur.execute(
            """
            INSERT INTO user_settings (user_id, setting_key, setting_value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=excluded.updated_at
            """,
            (int(user_id), key, value, now),
        )
    if changed:
        record_account_audit(conn, user_id=user_id, actor_user_id=actor_user_id or user_id, action="settings_updated", target_type="settings", details={"changed_keys": sorted(changed)})
    conn.commit()
    updated = get_settings(conn, user_id)
    return {"ok": True, "settings": updated, "changed": changed, "previous": existing}


def submit_verification_request(conn: Any, user_id: int, verification_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_schema(conn)
    verification_type = str(verification_type or "identity").strip().lower()
    if verification_type not in VERIFICATION_TYPES:
        raise ValueError("Choose a valid verification type.")
    payload = payload or {}
    now = _now()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO verification_requests
        (user_id, verification_type, status, notes, request_payload_json, submitted_at, created_at, updated_at)
        VALUES (?, ?, 'submitted', ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            verification_type,
            str(payload.get("public_note") or "")[:1000],
            json.dumps({k: str(v)[:500] for k, v in payload.items() if k not in {"document", "file"}}, default=str)[:3000],
            now,
            now,
            now,
        ),
    )
    request_id = _safe_int(getattr(cur, "lastrowid", 0), 0)
    record_account_audit(conn, user_id=user_id, actor_user_id=user_id, action="verification_submitted", target_type="verification_request", target_id=str(request_id), details={"verification_type": verification_type})
    conn.commit()
    return {"ok": True, "request_id": request_id, "status": "submitted", "verification_type": verification_type}


def appeal_verification_request(conn: Any, user_id: int, request_id: int, appeal_note: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT * FROM verification_requests WHERE id=? AND user_id=? LIMIT 1", (int(request_id), int(user_id)))
    existing = _row_dict(cur.fetchone())
    if not existing:
        raise ValueError("Verification request not found.")
    if str(existing.get("status") or "") not in {"rejected", "suspended", "needs_more_info"}:
        raise ValueError("This verification request is not appealable yet.")
    now = _now()
    cur.execute(
        """
        INSERT INTO verification_requests
        (user_id, verification_type, status, notes, appeal_of_request_id, submitted_at, created_at, updated_at)
        VALUES (?, ?, 'appealed', ?, ?, ?, ?, ?)
        """,
        (int(user_id), existing.get("verification_type") or "identity", str(appeal_note or "")[:1000], int(request_id), now, now, now),
    )
    appeal_id = _safe_int(getattr(cur, "lastrowid", 0), 0)
    record_account_audit(conn, user_id=user_id, actor_user_id=user_id, action="verification_appealed", target_type="verification_request", target_id=str(appeal_id), details={"appeal_of_request_id": int(request_id)})
    conn.commit()
    return {"ok": True, "request_id": appeal_id, "status": "appealed"}


def admin_decide_verification(conn: Any, request_id: int, reviewer_id: int, decision: str, reason: str = "") -> dict[str, Any]:
    ensure_schema(conn)
    decision = str(decision or "").strip().lower()
    if decision == "needs_changes":
        decision = "needs_more_info"
    if decision not in {"approved", "rejected", "needs_more_info", "suspended"}:
        raise ValueError("Choose a valid verification decision.")
    cur = conn.cursor()
    cur.execute("SELECT * FROM verification_requests WHERE id=? LIMIT 1", (int(request_id),))
    row = _row_dict(cur.fetchone())
    if not row:
        raise ValueError("Verification request not found.")
    now = _now()
    cur.execute(
        """
        UPDATE verification_requests
        SET status=?, reviewed_by=?, reviewed_at=?, decision_at=?, decision_reason=?, updated_at=?
        WHERE id=?
        """,
        (decision, int(reviewer_id or 0), now, now, str(reason or "")[:1000], now, int(request_id)),
    )
    if decision == "approved":
        cur.execute("UPDATE users SET verified_badge=1, updated_at=? WHERE user_id=?", (now, int(row.get("user_id") or 0)))
    elif decision in {"rejected", "suspended"}:
        cur.execute("UPDATE users SET updated_at=? WHERE user_id=?", (now, int(row.get("user_id") or 0)))
    record_account_audit(conn, user_id=_safe_int(row.get("user_id"), 0), actor_user_id=reviewer_id, action=f"verification_{decision}", target_type="verification_request", target_id=str(request_id), details={"reason_public": bool(reason)})
    conn.commit()
    return {"ok": True, "request_id": int(request_id), "status": decision}


def add_account_health_event(
    conn: Any,
    *,
    user_id: int,
    actor_user_id: int,
    event_type: str,
    severity: str,
    public_summary: str,
    internal_note: str = "",
    table: str = "account_health_events",
) -> dict[str, Any]:
    ensure_schema(conn)
    if table not in {"account_health_events", "account_strikes", "account_warnings", "account_restrictions"}:
        raise ValueError("Invalid health table.")
    severity = str(severity or "low").lower()
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "low"
    now = _now()
    cur = conn.cursor()
    if table == "account_strikes":
        cur.execute(
            "INSERT INTO account_strikes (user_id, policy_category, severity, public_summary, internal_note, appeal_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'available', ?, ?)",
            (int(user_id), str(event_type or "policy")[:80], severity, str(public_summary or "")[:1000], str(internal_note or "")[:1000], now, now),
        )
    elif table == "account_warnings":
        cur.execute(
            "INSERT INTO account_warnings (user_id, policy_category, public_summary, internal_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(user_id), str(event_type or "policy")[:80], str(public_summary or "")[:1000], str(internal_note or "")[:1000], now, now),
        )
    elif table == "account_restrictions":
        cur.execute(
            "INSERT INTO account_restrictions (user_id, restriction_type, public_summary, internal_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(user_id), str(event_type or "restriction")[:80], str(public_summary or "")[:1000], str(internal_note or "")[:1000], now, now),
        )
    else:
        cur.execute(
            "INSERT INTO account_health_events (user_id, event_type, severity, public_summary, internal_note, actor_user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(user_id), str(event_type or "health")[:80], severity, str(public_summary or "")[:1000], str(internal_note or "")[:1000], int(actor_user_id or 0), now, now),
        )
    item_id = _safe_int(getattr(cur, "lastrowid", 0), 0)
    record_account_audit(conn, user_id=user_id, actor_user_id=actor_user_id, action=f"{table}_created", target_type=table, target_id=str(item_id), details={"severity": severity})
    conn.commit()
    return {"ok": True, "id": item_id}


def _count(cur: Any, table: str, where: str, params: tuple[Any, ...]) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        return _safe_int(_row_dict(cur.fetchone()).get("total"), 0)
    except Exception:
        return 0


def _latest_verification(cur: Any, user_id: int) -> dict[str, Any]:
    if not _table_exists(cur, "verification_requests"):
        return {}
    cur.execute("SELECT * FROM verification_requests WHERE user_id=? ORDER BY COALESCE(updated_at, reviewed_at, created_at) DESC, id DESC LIMIT 1", (int(user_id),))
    return _row_dict(cur.fetchone())


def _health_summary(cur: Any, user_id: int) -> dict[str, Any]:
    warnings = _count(cur, "account_warnings", "user_id=? AND COALESCE(status,'active')='active'", (int(user_id),))
    strikes = _count(cur, "account_strikes", "user_id=? AND COALESCE(status,'active')='active'", (int(user_id),))
    restrictions = _count(cur, "account_restrictions", "user_id=? AND COALESCE(status,'active')='active'", (int(user_id),))
    security_alerts = _count(cur, "user_security_events", "user_id=? AND event_type IN ('suspicious_login','failed_login_burst')", (int(user_id),))
    score = max(0, 100 - warnings * 5 - strikes * 18 - restrictions * 35 - min(security_alerts, 5) * 4)
    status = "secure"
    if restrictions:
        status = "restricted"
    elif strikes or score < 65:
        status = "watch"
    return {
        "status": status,
        "score": score,
        "warnings": warnings,
        "strikes": strikes,
        "restrictions": restrictions,
        "security_alerts": security_alerts,
        "appeals_available": strikes + restrictions,
    }


def build_account_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    cur = conn.cursor()
    user_id = _safe_int(user.get("user_id"), 0)
    latest_verification = _latest_verification(cur, user_id)
    verification_status = str(latest_verification.get("status") or ("approved" if _safe_int(user.get("verified_badge"), 0) else "not_started")).lower()
    if verification_status not in VERIFICATION_STATUSES:
        verification_status = "submitted" if verification_status in {"pending", "review"} else "not_started"
    health = _health_summary(cur, user_id)
    settings = get_settings(conn, user_id)
    profile_complete = bool(user.get("display_name") and user.get("username") and user.get("avatar_url"))
    trusted_devices = _count(cur, "user_trusted_devices", "user_id=?", (user_id,))
    recovery_codes = _count(cur, "user_recovery_codes", "user_id=? AND used_at IS NULL", (user_id,))
    suspicious = health["security_alerts"]
    security_score = max(0, min(100, 45 + (15 if user.get("email_verified") else 0) + (15 if user.get("phone_verified") else 0) + (15 if user.get("two_factor_enabled") else 0) + (10 if recovery_codes else 0) + (5 if trusted_devices else 0) - min(suspicious, 5) * 8))
    security_status = "secure" if security_score >= 75 else "watch" if security_score >= 45 else "action"
    return {
        "profile": {
            "state": "ON" if profile_complete else "ACTION",
            "status": "complete" if profile_complete else "needs_attention",
            "route": "/dashboard/account/profile",
            "fields": {
                "display_name": bool(user.get("display_name")),
                "username": bool(user.get("username")),
                "avatar": bool(user.get("avatar_url")),
                "banner": bool(user.get("cover_url") or user.get("banner_url")),
                "visibility": str(user.get("profile_visibility") or settings.get("profile_visibility") or "public"),
            },
        },
        "verification": {
            "state": "ON" if verification_status == "approved" else "REVIEW" if verification_status in {"submitted", "in_review", "appealed"} else "WARNING" if verification_status == "suspended" else "ACTION",
            "status": verification_status,
            "request_id": _safe_int(latest_verification.get("id"), 0),
            "route": "/dashboard/account/verification",
        },
        "account_health": {
            "state": "ON" if health["status"] == "secure" else "WARNING",
            "route": "/dashboard/account/health",
            **health,
        },
        "security": {
            "state": "ON" if security_status == "secure" else "ACTION",
            "status": security_status,
            "score": security_score,
            "trusted_devices": trusted_devices,
            "recovery_codes_ready": recovery_codes > 0,
            "two_factor_enabled": bool(user.get("two_factor_enabled")),
            "route": "/dashboard/account/security",
        },
        "settings": {
            "state": "ON",
            "status": "server_managed",
            "route": "/dashboard/account/settings",
            "settings": settings,
        },
    }


def state_for_widget(account_state: dict[str, Any], widget_key: str) -> dict[str, Any] | None:
    if widget_key == "advanced_security":
        base = dict(account_state.get("security") or {})
        base["route"] = "/dashboard/account/security"
        return base
    return account_state.get(widget_key)
