"""Bridge to the existing Security Center (Stage 20, fixed in Mission 2).

The platform already has canonical security primitives (`security_events`,
`auth_events`, `admin_audit_logs`). Sentinel does NOT build a second security
center: this bridge READS those stores and maps rows into the canonical
SentinelEventV1 envelope, deduplicated by source row id, so correlation and
evidence work over one unified stream.

Mission 2 fixes, verified against the live bot.py writers:
- security_events stores its payload in ``details_json`` (the Mission 1
  SELECT asked for ``details`` and silently returned nothing),
- auth_events event types are the REAL ones bot.py writes (``login_success``,
  ``forgot_password_invalid_email``, …), not guessed names,
- timestamps are ISO with a 'T' at the source and are normalised to the
  canonical ``YYYY-MM-DD HH:MM:SS`` so window comparisons work,
- unmapped event types are SKIPPED and counted, never guessed (SC15),
- raw IPs never enter sentinel storage — they are hashed into network refs.

Strictly read-only against the source tables.
"""

from __future__ import annotations

import hashlib
import json
import os

from services.sentinel import events, store
from services.sentinel.identity import SENTINEL_INGEST

# security_events.event_type → (sentinel category, event_type, severity).
# Types confirmed against bot.py log_security_event call sites.
_SECURITY_EVENT_MAP = {
    "failed_login_burst": ("SECURITY", "failed_login_burst", "medium"),
    "refresh_token_reuse": ("SECURITY", "refresh_token_reuse", "high"),
    "refresh_device_mismatch": ("SECURITY", "refresh_device_mismatch", "high"),
    "sessions_revoked": ("SESSION", "sessions_revoked", "info"),
    "unusual_device": ("SECURITY", "unusual_device", "medium"),
    "unusual_country": ("SECURITY", "unusual_country", "medium"),
    "brute_force": ("SECURITY", "brute_force", "high"),
    "pro_access_activated": ("PAYMENT", "pro_access_activated", "info"),
}

# auth_events.event_type → (category, event_type, severity). Types confirmed
# against bot.py log_auth_event call sites. Emails in this table are already
# masked/hashed at the source.
_AUTH_EVENT_MAP = {
    "login_failed": ("AUTH", "login_failed", "low"),
    "mobile_login_failed": ("AUTH", "login_failed", "low"),
    "login_success": ("AUTH", "login_succeeded", "info"),
    "mobile_login_success": ("AUTH", "login_succeeded", "info"),
    "login_blocked": ("AUTH", "login_blocked", "medium"),
    "login_restricted": ("AUTH", "login_restricted", "medium"),
    "login_unconfirmed": ("AUTH", "login_unconfirmed", "low"),
    "login_challenge_required": ("AUTH", "login_challenge_required", "low"),
    "signup_started": ("AUTH", "signup_started", "info"),
    "signup_completed": ("AUTH", "signup_completed", "info"),
    "signup_failed": ("AUTH", "signup_failed", "low"),
    "signup_duplicate": ("AUTH", "signup_duplicate", "low"),
    "forgot_password_token_created": ("AUTH", "password_reset_requested", "info"),
    "forgot_password_invalid_email": ("AUTH", "password_reset_invalid_email", "low"),
    "forgot_password_no_match": ("AUTH", "password_reset_no_match", "low"),
    "forgot_password_request_failed": ("AUTH", "password_reset_failed", "medium"),
    "unverified_email_change_failed": ("AUTH", "email_change_failed", "medium"),
    "unverified_email_changed": ("AUTH", "email_changed", "info"),
}


def _rows(cur, sql: str, params=()):
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception:
        return []  # source table absent on fresh DBs — bridge is best-effort


def _ts(raw) -> str:
    """Normalise source timestamps (ISO 'T' form) to canonical form."""
    text = str(raw or "").replace("T", " ")[:19]
    return text or events._utcnow()


def _net_ref(ip) -> str | None:
    """Raw IPs never enter sentinel storage: hash to a stable network ref."""
    ip = str(ip or "").strip()
    if not ip:
        return None
    return "network:" + hashlib.sha256(ip.encode()).hexdigest()[:16]


def _payload(raw, cap: int = 500) -> dict:
    try:
        if isinstance(raw, str) and raw.strip().startswith("{"):
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"details": str(data)[:cap]}
    except Exception:
        pass
    return {"details": str(raw or "")[:cap]}


_ENV = os.getenv("RAILWAY_ENVIRONMENT_NAME", "") or os.getenv("FLASK_ENV", "")


def sync_security_events(limit: int = 500, conn=None) -> dict:
    """Pull recent platform security/auth/admin events into the Sentinel
    stream. Idempotent: dedupe_key is the source table + row id, so
    re-running never duplicates. Unmapped types are skipped and counted."""
    limit = max(1, min(int(limit), 2000))
    ingested = deduped = skipped = 0

    def _emit(c, ev) -> None:
        nonlocal ingested, deduped
        if events.ingest(ev, conn=c):
            ingested += 1
        else:
            deduped += 1

    with store.connection(conn) as c:
        cur = c.cursor()

        # --- security_events (payload lives in details_json) ---
        for row in _rows(cur,
                         "SELECT id, event_type, user_id, ip_address, path, status, "
                         "details_json, created_at FROM security_events "
                         "ORDER BY id DESC LIMIT ?", (limit,)):
            mapped = _SECURITY_EVENT_MAP.get(str(row[1] or ""))
            if not mapped:
                skipped += 1
                continue
            category, event_type, severity = mapped
            user_id = str(row[2] or "")
            payload = _payload(row[6])
            payload["path"] = str(row[4] or "")[:240]
            payload["status"] = str(row[5] or "")[:80]
            _emit(c, events.Event(
                category=category, event_type=event_type, severity=severity,
                actor_id=f"user:{user_id}" if user_id not in ("", "0") else SENTINEL_INGEST.actor_id,
                actor_type="USER" if user_id not in ("", "0") else "SERVICE",
                source="bridge.security_events",
                source_system="pulsesoc", source_component="security_center_bridge",
                source_event_id=f"security_events:{row[0]}",
                source_trust="AUTHORITATIVE", environment=_ENV,
                subject_type="user", subject_id=user_id or None,
                network_ref=_net_ref(row[3]),
                occurred_at=_ts(row[7]),
                correlation_keys=tuple(k for k in (
                    f"user:{user_id}" if user_id not in ("", "0") else None,
                    _net_ref(row[3]),
                    f"route:{str(row[4] or '')[:120]}" if row[4] else None) if k),
                payload=payload,
                dedupe_key=f"security_events:{row[0]}"))

        # --- auth_events (emails already masked/hashed at the source) ---
        for row in _rows(cur,
                         "SELECT id, event_type, user_id, email_hash, status, severity, "
                         "ip_address, country, device, route, created_at FROM auth_events "
                         "ORDER BY id DESC LIMIT ?", (limit,)):
            mapped = _AUTH_EVENT_MAP.get(str(row[1] or ""))
            if not mapped:
                skipped += 1
                continue
            category, event_type, severity = mapped
            user_id = str(row[2] or "")
            email_hash = str(row[3] or "")
            subject_id = user_id if user_id not in ("", "0") else (email_hash or None)
            _emit(c, events.Event(
                category=category, event_type=event_type, severity=severity,
                actor_id=f"user:{user_id}" if user_id not in ("", "0") else SENTINEL_INGEST.actor_id,
                actor_type="USER" if user_id not in ("", "0") else "SERVICE",
                source="bridge.auth_events",
                source_system="pulsesoc", source_component="security_center_bridge",
                source_event_id=f"auth_events:{row[0]}",
                source_trust="AUTHORITATIVE", environment=_ENV,
                subject_type="user" if user_id not in ("", "0") else "email_hash",
                subject_id=subject_id,
                network_ref=_net_ref(row[6]),
                device_ref=f"device:{str(row[8])[:60]}" if row[8] else None,
                occurred_at=_ts(row[10]),
                correlation_keys=tuple(k for k in (
                    f"user:{user_id}" if user_id not in ("", "0") else None,
                    f"email_hash:{email_hash}" if email_hash else None,
                    _net_ref(row[6]),
                    f"route:{str(row[9] or '')[:120]}" if row[9] else None) if k),
                payload={"status": str(row[4] or "")[:40],
                         "source_severity": str(row[5] or "")[:20],
                         "country": str(row[7] or "")[:40]},
                dedupe_key=f"auth_events:{row[0]}"))

        # --- admin_audit_logs (every admin action is attributable) ---
        for row in _rows(cur,
                         "SELECT id, admin_user_id, action, target_type, target_id, "
                         "ip_hash, created_at FROM admin_audit_logs "
                         "ORDER BY id DESC LIMIT ?", (limit,)):
            admin_id = str(row[1] or "")
            _emit(c, events.Event(
                category="ADMIN", event_type="admin_action", severity="info",
                actor_id=f"admin:{admin_id}" if admin_id not in ("", "0") else SENTINEL_INGEST.actor_id,
                actor_type="ADMIN" if admin_id not in ("", "0") else "SERVICE",
                source="bridge.admin_audit_logs",
                source_system="pulsesoc", source_component="security_center_bridge",
                source_event_id=f"admin_audit_logs:{row[0]}",
                source_trust="AUTHORITATIVE", environment=_ENV,
                subject_type=str(row[3] or "") or None,
                subject_id=str(row[4] or "") or None,
                occurred_at=_ts(row[6]),
                correlation_keys=tuple(k for k in (
                    f"admin:{admin_id}" if admin_id not in ("", "0") else None,
                    f"action:{str(row[2] or '')[:80]}" if row[2] else None) if k),
                payload={"action": str(row[2] or "")[:120],
                         "target_type": str(row[3] or "")[:60],
                         "target_id": str(row[4] or "")[:60],
                         "ip_hash": str(row[5] or "")[:64]},
                dedupe_key=f"admin_audit_logs:{row[0]}"))

    return {"ingested": ingested, "deduped": deduped, "skipped": skipped}
