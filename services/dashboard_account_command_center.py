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
STATE_LABELS = {"READY", "ACTION", "REVIEW", "WARNING", "LOCKED", "PREMIUM", "BETA", "PARTIAL", "ADMIN"}
SETTING_KEYS = {
    "profile_visibility": {"public", "private"},
    "message_requests": {"everyone", "followers", "none"},
    "notifications_enabled": {"true", "false"},
    "status_replies": {"everyone", "followers", "none"},
    "ads_personalization": {"true", "false"},
    "reduced_motion": {"true", "false", "system"},
    "sci_fi_intensity": {"low", "medium", "high"},
    "language": {"en", "es", "fr", "ht", "pt", "de", "it", "ar"},
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

ACCOUNT_SUBSYSTEMS: tuple[dict[str, Any], ...] = (
    {
        "key": "profile",
        "label": "Profile Control",
        "admin_label": "Profile Manager",
        "route": "/dashboard/account/profile",
        "admin_route": "/admin/account-command/profile",
        "actions": ("Manage Profile", "Improve Profile", "Preview Public Profile", "Review Profile Health"),
        "monitors": ("profile_completion", "profile_visits", "search_appearances", "username_risk", "impersonation_risk"),
        "protects": ("public_identity", "profile_media", "reserved_usernames", "unsafe_bio_content"),
        "recovers": ("profile_change_history", "avatar_banner_history", "admin_revert_queue"),
    },
    {
        "key": "verification",
        "label": "Verification Center",
        "admin_label": "Verification Queue",
        "route": "/dashboard/account/verification",
        "admin_route": "/admin/account-command/verification",
        "actions": ("Continue Verification", "Review Verification", "Submit Appeal", "View Verification Timeline"),
        "monitors": ("verification_status", "document_uploads", "review_queue", "badge_state"),
        "protects": ("private_documents", "badge_integrity", "review_permissions"),
        "recovers": ("appeal_flow", "needs_more_info_flow", "badge_revoke_history"),
    },
    {
        "key": "account_health",
        "label": "Account Health",
        "admin_label": "Account Health Manager",
        "route": "/dashboard/account/health",
        "admin_route": "/admin/account-command/account-health",
        "actions": ("View Account Health", "Fix Account Issues", "Review Restrictions", "Submit Appeal"),
        "monitors": ("trust_score", "warnings", "strikes", "restrictions", "appeals"),
        "protects": ("reporter_identity", "moderator_notes", "restriction_state"),
        "recovers": ("appeal_history", "restriction_expiration", "recovery_plan"),
    },
    {
        "key": "security",
        "label": "Security Center",
        "admin_label": "Security Center",
        "route": "/dashboard/account/security",
        "admin_route": "/admin/account-command/security",
        "actions": ("Secure Account", "Manage Security", "Review Security Risks", "Revoke Sessions"),
        "monitors": ("password_state", "2fa", "trusted_devices", "login_history", "suspicious_activity"),
        "protects": ("sessions", "devices", "security_events", "sensitive_actions"),
        "recovers": ("session_revoke", "force_logout", "recovery_methods"),
    },
    {
        "key": "settings",
        "label": "Settings Center",
        "admin_label": "Settings Manager",
        "route": "/dashboard/account/settings",
        "admin_route": "/admin/account-command/settings",
        "actions": ("Manage Settings", "Review Privacy", "Tune Experience", "Manage Notifications"),
        "monitors": ("privacy_settings", "notification_settings", "ads_privacy", "accessibility", "conflicts"),
        "protects": ("private_settings", "notification_consent", "ads_privacy_choices"),
        "recovers": ("safe_defaults", "settings_audit", "conflict_resolution"),
    },
    {
        "key": "advanced_security",
        "label": "Advanced Protection",
        "admin_label": "Advanced Security Manager",
        "route": "/dashboard/account/advanced-security",
        "admin_route": "/admin/account-command/advanced-security",
        "actions": ("Harden Security", "Review Protection", "Enable Stronger Security"),
        "monitors": ("2fa_strength", "passkey_readiness", "high_risk_actions", "recovery_protection"),
        "protects": ("high_risk_changes", "trusted_device_model", "recovery_surface"),
        "recovers": ("hardening_plan", "device_retrust", "admin_review"),
    },
    {
        "key": "identity_protection",
        "label": "Identity Protection",
        "admin_label": "Identity Protection Manager",
        "route": "/dashboard/account/identity-protection",
        "admin_route": "/admin/account-command/identity-protection",
        "actions": ("Protect Identity", "Review Identity Risk", "Report Impersonation"),
        "monitors": ("username_similarity", "avatar_similarity", "fake_account_risk", "badge_protection"),
        "protects": ("personal_identity", "brand_identity", "badge_identity"),
        "recovers": ("identity_lock", "impersonation_report", "admin_identity_review"),
    },
    {
        "key": "session_intelligence",
        "label": "Session Review",
        "admin_label": "Session Intelligence Manager",
        "route": "/dashboard/account/session-intelligence",
        "admin_route": "/admin/account-command/session-intelligence",
        "actions": ("Review Sessions", "Manage Sessions", "End Suspicious Session"),
        "monitors": ("active_sessions", "session_trust", "last_active", "impossible_travel"),
        "protects": ("session_credentials_hidden", "suspicious_sessions", "device_context"),
        "recovers": ("session_kill", "session_trust", "session_rename"),
    },
    {
        "key": "device_intelligence",
        "label": "Device Review",
        "admin_label": "Device Intelligence Manager",
        "route": "/dashboard/account/device-intelligence",
        "admin_route": "/admin/account-command/device-intelligence",
        "actions": ("Manage Devices", "Trust Device", "Remove Device", "Review Device Risk"),
        "monitors": ("known_devices", "stale_devices", "push_registration_health", "platform_version"),
        "protects": ("push_registration_health", "device_private_data", "trusted_devices"),
        "recovers": ("device_revoke", "device_retrust", "stale_cleanup"),
    },
    {
        "key": "security_timeline",
        "label": "Security Timeline",
        "admin_label": "Security Timeline Manager",
        "route": "/dashboard/account/security-timeline",
        "admin_route": "/admin/account-command/security-timeline",
        "actions": ("View Timeline", "Review Events", "Investigate Activity"),
        "monitors": ("logins", "password_changes", "2fa_changes", "device_changes", "admin_actions"),
        "protects": ("safe_user_timeline", "admin_detail_gate", "sensitive_event_redaction"),
        "recovers": ("event_review", "timeline_audit", "support_context"),
    },
    {
        "key": "threat_detection",
        "label": "Threat Alerts",
        "admin_label": "Threat Detection Manager",
        "route": "/dashboard/account/threat-detection",
        "admin_route": "/admin/account-command/threat-detection",
        "actions": ("View Alerts", "Review Threats", "Investigate Risk"),
        "monitors": ("threat_level", "suspicious_login", "device_risk", "profile_impersonation", "scam_risk"),
        "protects": ("account_access", "profile_identity", "risk_alerts"),
        "recovers": ("risk_recommendations", "admin_threat_review", "alert_resolution"),
    },
    {
        "key": "login_analytics",
        "label": "Login History",
        "admin_label": "Login Analytics Manager",
        "route": "/dashboard/account/login-analytics",
        "admin_route": "/admin/account-command/login-analytics",
        "actions": ("Review Logins", "View Login Patterns", "Investigate Login Risk"),
        "monitors": ("login_history", "failed_login_count", "new_device_count", "region_safety", "risk_trend"),
        "protects": ("login_privacy", "ip_hashes", "safe_region_display"),
        "recovers": ("login_review", "failed_login_cooldown", "security_recommendations"),
    },
)

ACCOUNT_SUBSYSTEM_MAP = {item["key"]: item for item in ACCOUNT_SUBSYSTEMS}


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
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _columns(cur: Any, table: str) -> set[str]:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (table,))
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
        CREATE TABLE IF NOT EXISTS account_system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            subsystem_key TEXT,
            event_type TEXT,
            severity TEXT DEFAULT 'low',
            public_summary TEXT,
            status TEXT DEFAULT 'open',
            source TEXT,
            created_at TEXT,
            resolved_at TEXT
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
        "account_system_events": ("user_id", "subsystem_key"),
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
    cur.execute("SELECT preferred_language FROM users WHERE user_id=? LIMIT 1", (int(user_id),))
    user_row = cur.fetchone()
    user_language = str((_row_dict(user_row).get("preferred_language") if user_row else "") or (user_row[0] if user_row else "") or "").strip().lower()
    if user_language in SETTING_KEYS["language"]:
        defaults["language"] = user_language
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
    if "language" in changed:
        cur.execute("UPDATE users SET preferred_language=?, updated_at=? WHERE user_id=?", (changed["language"], now, int(user_id)))
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


def _safe_ratio(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, round((done / total) * 100)))


def _event_count(cur: Any, user_id: int, subsystem_key: str = "", severity: str = "") -> int:
    if not _table_exists(cur, "account_system_events"):
        return 0
    where = ["user_id=?", "COALESCE(status,'open')='open'"]
    params: list[Any] = [int(user_id)]
    if subsystem_key:
        where.append("subsystem_key=?")
        params.append(subsystem_key)
    if severity:
        where.append("severity=?")
        params.append(severity)
    return _count(cur, "account_system_events", " AND ".join(where), tuple(params))


def record_account_system_event(
    conn: Any,
    *,
    user_id: int,
    subsystem_key: str,
    event_type: str,
    public_summary: str,
    severity: str = "low",
    source: str = "account_command_center",
) -> None:
    ensure_schema(conn)
    if subsystem_key not in ACCOUNT_SUBSYSTEM_MAP:
        subsystem_key = "account_health"
    severity = str(severity or "low").lower()
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "low"
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO account_system_events
        (user_id, subsystem_key, event_type, severity, public_summary, status, source, created_at)
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
        """,
        (
            int(user_id),
            subsystem_key,
            str(event_type or "account_event")[:100],
            severity,
            str(public_summary or "")[:1000],
            str(source or "account_command_center")[:80],
            _now(),
        ),
    )
    record_account_audit(
        conn,
        user_id=user_id,
        actor_user_id=user_id,
        action="account_system_event_created",
        target_type="account_system_event",
        details={"subsystem_key": subsystem_key, "event_type": event_type, "severity": severity},
    )


def _recent_audit_events(cur: Any, user_id: int, limit: int = 8) -> list[dict[str, str]]:
    if not _table_exists(cur, "account_audit_logs"):
        return []
    try:
        cur.execute(
            """
            SELECT action, target_type, created_at
            FROM account_audit_logs
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(user_id), max(1, min(int(limit), 20))),
        )
        return [
            {
                "action": str(_row_dict(row).get("action") or "")[:120],
                "target_type": str(_row_dict(row).get("target_type") or "")[:80],
                "created_at": str(_row_dict(row).get("created_at") or "")[:40],
            }
            for row in cur.fetchall()
        ]
    except Exception:
        return []


def _state_from_score(score: int, *, review: bool = False, warning: bool = False, action: bool = False) -> str:
    if review:
        return "REVIEW"
    if warning or score < 50:
        return "WARNING"
    if action or score < 75:
        return "ACTION"
    return "READY"


def _subsystem_payload(
    *,
    key: str,
    state: str,
    score: int,
    status: str,
    primary_action: str | None = None,
    recommendations: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    protections: list[str] | None = None,
    monitors: list[str] | None = None,
    recovery: list[str] | None = None,
) -> dict[str, Any]:
    spec = ACCOUNT_SUBSYSTEM_MAP[key]
    state = state if state in STATE_LABELS else "PARTIAL"
    return {
        "key": key,
        "label": spec["label"],
        "state": state,
        "status": str(status or state.lower())[:80],
        "score": max(0, min(100, int(score))),
        "route": spec["route"],
        "cta_label": primary_action or spec["actions"][0],
        "actions": list(spec["actions"]),
        "recommendations": [str(item)[:180] for item in (recommendations or [])],
        "metrics": metrics or {},
        "monitors": list(monitors or spec["monitors"]),
        "protections": list(protections or spec["protects"]),
        "recovery": list(recovery or spec["recovers"]),
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
    profile_fields = {
        "display_name": bool(user.get("display_name")),
        "username": bool(user.get("username")),
        "avatar": bool(user.get("avatar_url")),
        "banner": bool(user.get("cover_url") or user.get("banner_url")),
        "bio": bool(user.get("bio")),
    }
    profile_score = _safe_ratio(sum(1 for value in profile_fields.values() if value), len(profile_fields))
    profile_complete = profile_score >= 60
    profile_visits = _count(cur, "profile_views", "profile_user_id=?", (user_id,))
    search_appearances = _count(cur, "search_events", "result_user_id=?", (user_id,))
    trusted_devices = _count(cur, "user_trusted_devices", "user_id=?", (user_id,)) + _count(cur, "security_devices", "user_id=? AND COALESCE(trusted,0)=1", (user_id,))
    known_devices = _count(cur, "security_devices", "user_id=?", (user_id,))
    active_sessions = _count(cur, "active_sessions", "user_id=? AND COALESCE(revoked_at,'')=''", (user_id,))
    login_events = _count(cur, "security_login_events", "user_id=?", (user_id,))
    failed_logins = _count(cur, "security_login_events", "user_id=? AND event_type IN ('login_failed','failed_login')", (user_id,))
    recovery_codes = _count(cur, "user_recovery_codes", "user_id=? AND used_at IS NULL", (user_id,))
    suspicious = health["security_alerts"]
    security_score = max(0, min(100, 45 + (15 if user.get("email_verified") else 0) + (15 if user.get("phone_verified") else 0) + (15 if user.get("two_factor_enabled") else 0) + (10 if recovery_codes else 0) + (5 if trusted_devices else 0) - min(suspicious, 5) * 8))
    security_status = "secure" if security_score >= 75 else "watch" if security_score >= 45 else "action"
    verification_score = 100 if verification_status == "approved" else 55 if verification_status in {"submitted", "in_review", "appealed"} else 25
    identity_risk = 0
    if not user.get("avatar_url"):
        identity_risk += 12
    if not user.get("username"):
        identity_risk += 18
    if verification_status in {"rejected", "suspended"}:
        identity_risk += 30
    session_risk = min(60, suspicious * 12 + failed_logins * 4)
    device_score = max(0, min(100, 72 + min(20, trusted_devices * 6) - max(0, known_devices - trusted_devices) * 6 - suspicious * 8))
    settings_conflicts = 1 if settings.get("notifications_enabled") == "false" and settings.get("message_requests") == "everyone" else 0
    risk_level = "Low"
    if health["status"] == "restricted" or security_score < 45 or suspicious >= 3:
        risk_level = "High"
    elif health["status"] != "secure" or security_score < 75 or identity_risk >= 30:
        risk_level = "Medium"

    recommendations: list[str] = []
    if profile_score < 80:
        recommendations.append("Complete your profile photo, banner, bio, and public handle.")
    if verification_status in {"not_started", "draft", "rejected", "needs_more_info", "suspended"}:
        recommendations.append("Continue verification so your trust signals stay clear.")
    if security_score < 80:
        recommendations.append("Strengthen security with 2FA, trusted devices, and recovery readiness.")
    if health["warnings"] or health["strikes"] or health["restrictions"]:
        recommendations.append("Review account health and resolve warnings, strikes, or restrictions.")
    if settings_conflicts:
        recommendations.append("Review notification and message settings; current choices may reduce important alerts.")
    if not recommendations:
        recommendations.append("Account systems are stable. Review recent activity periodically.")

    subsystems = {
        "profile": _subsystem_payload(
            key="profile",
            state=_state_from_score(profile_score, action=profile_score < 80),
            score=profile_score,
            status="complete" if profile_complete else "needs_attention",
            primary_action="Manage Profile" if profile_complete else "Improve Profile",
            recommendations=[] if profile_score >= 80 else ["Add missing public profile fields.", "Preview your public profile after saving."],
            metrics={"completion": profile_score, "profile_visits": profile_visits, "search_appearances": search_appearances, **profile_fields},
        ),
        "verification": _subsystem_payload(
            key="verification",
            state="READY" if verification_status == "approved" else "REVIEW" if verification_status in {"submitted", "in_review", "appealed"} else "WARNING" if verification_status == "suspended" else "ACTION",
            score=verification_score,
            status=verification_status,
            primary_action="Review Verification" if verification_status in {"submitted", "in_review", "appealed", "approved"} else "Continue Verification",
            recommendations=[] if verification_status == "approved" else ["Submit the right verification request for your account type.", "Upload private documents only through the secure review flow."],
            metrics={"request_id": _safe_int(latest_verification.get("id"), 0), "status": verification_status, "type": latest_verification.get("verification_type") or "identity"},
        ),
        "account_health": _subsystem_payload(
            key="account_health",
            state="READY" if health["status"] == "secure" else "WARNING",
            score=health["score"],
            status=health["status"],
            primary_action="View Account Health" if health["status"] == "secure" else "Fix Account Issues",
            recommendations=[] if health["status"] == "secure" else ["Review visible warnings and restrictions.", "Submit an appeal where available."],
            metrics=health,
        ),
        "security": _subsystem_payload(
            key="security",
            state=_state_from_score(security_score, warning=suspicious > 0, action=security_score < 75),
            score=security_score,
            status=security_status,
            primary_action="Manage Security" if security_score >= 75 else "Secure Account",
            recommendations=[] if security_score >= 80 else ["Enable stronger login protection.", "Review suspicious activity and trusted devices."],
            metrics={"trusted_devices": trusted_devices, "active_sessions": active_sessions, "recovery_codes_ready": recovery_codes > 0, "two_factor_enabled": bool(user.get("two_factor_enabled"))},
        ),
        "settings": _subsystem_payload(
            key="settings",
            state="ACTION" if settings_conflicts else "READY",
            score=92 - settings_conflicts * 12,
            status="server_managed",
            primary_action="Manage Settings" if not settings_conflicts else "Review Privacy",
            recommendations=[] if not settings_conflicts else ["Resolve notification/message setting conflict."],
            metrics={"settings": settings, "conflicts": settings_conflicts},
        ),
        "advanced_security": _subsystem_payload(
            key="advanced_security",
            state=_state_from_score(security_score, warning=suspicious > 1, action=security_score < 85),
            score=max(0, security_score - 5 + (10 if trusted_devices else 0)),
            status="hardening_ready" if security_score >= 75 else "hardening_needed",
            primary_action="Harden Security",
            recommendations=["Review 2FA strength, trusted devices, and high-risk action protection."],
            metrics={"trusted_devices": trusted_devices, "active_sessions": active_sessions, "suspicious_events": suspicious},
        ),
        "identity_protection": _subsystem_payload(
            key="identity_protection",
            state="WARNING" if identity_risk >= 30 else "ACTION" if identity_risk else "READY",
            score=max(0, 100 - identity_risk),
            status="identity_clear" if identity_risk == 0 else "identity_review_recommended",
            primary_action="Protect Identity" if identity_risk else "Review Identity Risk",
            recommendations=[] if identity_risk == 0 else ["Add verified identity signals and profile media.", "Report impersonation if another account is copying you."],
            metrics={"identity_risk": identity_risk, "verified": verification_status == "approved", "avatar_present": bool(user.get("avatar_url"))},
        ),
        "session_intelligence": _subsystem_payload(
            key="session_intelligence",
            state="WARNING" if session_risk >= 35 else "ACTION" if active_sessions > 1 else "READY",
            score=max(0, 100 - session_risk),
            status="sessions_monitored",
            primary_action="Review Sessions" if session_risk < 35 else "End Suspicious Session",
            recommendations=["Review active sessions after new-device logins.", "End sessions you do not recognize."],
            metrics={"active_sessions": active_sessions, "failed_logins": failed_logins, "session_risk": session_risk},
        ),
        "device_intelligence": _subsystem_payload(
            key="device_intelligence",
            state=_state_from_score(device_score, warning=device_score < 60, action=known_devices and trusted_devices == 0),
            score=device_score,
            status="devices_monitored",
            primary_action="Manage Devices",
            recommendations=["Trust devices you recognize and remove stale devices."],
            metrics={"known_devices": known_devices, "trusted_devices": trusted_devices, "push_registration_health": "redacted"},
        ),
        "security_timeline": _subsystem_payload(
            key="security_timeline",
            state="READY" if login_events or _recent_audit_events(cur, user_id, 1) else "ACTION",
            score=88 if login_events else 72,
            status="timeline_available",
            primary_action="View Timeline",
            recommendations=["Review recent security, profile, verification, and device events."],
            metrics={"login_events": login_events, "recent_audit_events": len(_recent_audit_events(cur, user_id, 8))},
        ),
        "threat_detection": _subsystem_payload(
            key="threat_detection",
            state="WARNING" if risk_level == "High" else "ACTION" if risk_level == "Medium" else "READY",
            score=max(0, 100 - max(identity_risk, session_risk) - suspicious * 5),
            status=risk_level.lower(),
            primary_action="View Alerts" if risk_level == "Low" else "Review Threats",
            recommendations=[] if risk_level == "Low" else ["Investigate unusual login or identity activity.", "Harden account protection before sensitive actions."],
            metrics={"risk_level": risk_level, "active_alerts": suspicious + _event_count(cur, user_id), "identity_risk": identity_risk, "session_risk": session_risk},
        ),
        "login_analytics": _subsystem_payload(
            key="login_analytics",
            state="WARNING" if failed_logins >= 5 else "READY" if login_events else "ACTION",
            score=max(0, 92 - min(50, failed_logins * 6)),
            status="login_patterns_available" if login_events else "waiting_for_activity",
            primary_action="Review Logins" if login_events else "View Login Patterns",
            recommendations=["Review new-device and failed-login patterns regularly."],
            metrics={"login_events": login_events, "failed_logins": failed_logins, "new_device_count": max(0, known_devices - trusted_devices)},
        ),
    }
    intelligence = {
        "trust_score": health["score"],
        "account_score": round((health["score"] + profile_score + verification_score) / 3),
        "security_score": security_score,
        "profile_completion": profile_score,
        "verification_status": verification_status,
        "risk_level": risk_level,
        "active_sessions": active_sessions,
        "trusted_devices": trusted_devices,
        "active_alerts": suspicious + _event_count(cur, user_id),
        "recent_security_events": login_events,
        "recommended_next_actions": recommendations[:5],
    }
    result = {
        "intelligence": intelligence,
        "subsystems": subsystems,
        "recent_events": _recent_audit_events(cur, user_id, 8),
        "event_bus": {
            "shared_backend_events": True,
            "audit_layer": True,
            "cross_module_updates": [
                "profile_change_updates_timeline_and_health",
                "verification_decision_updates_health_and_recommendations",
                "new_device_updates_device_session_threat_and_timeline",
                "settings_change_updates_privacy_notifications_and_audit",
            ],
        },
    }
    result.update(subsystems)
    return result


def state_for_widget(account_state: dict[str, Any], widget_key: str) -> dict[str, Any] | None:
    if widget_key in ACCOUNT_SUBSYSTEM_MAP:
        return account_state.get("subsystems", {}).get(widget_key) or account_state.get(widget_key)
    return account_state.get(widget_key)
