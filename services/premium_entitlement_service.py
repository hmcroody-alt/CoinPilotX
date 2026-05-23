"""Single source of truth for Premium entitlements."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from services import db as db_service


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _active_window(row: dict[str, Any]) -> bool:
    now = _now()
    starts = row.get("starts_at") or ""
    ends = row.get("ends_at") or row.get("expires_at") or ""
    if starts and starts > now:
        return False
    if ends and ends < now:
        return False
    return True


def has_entitlement(user_id: int, key: str) -> bool:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM premium_entitlements
        WHERE user_id=? AND entitlement_key=? AND status='active'
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id or 0), key),
    )
    row = cur.fetchone()
    conn.close()
    return bool(row and _active_window(dict(row)))


def grant_entitlement(user_id: int, key: str, source: str = "admin_grant", starts_at: str = "", ends_at: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _now()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO premium_entitlements
        (user_id, entitlement_key, status, source, starts_at, ends_at, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)
        """,
        (int(user_id), key, source, starts_at or now, ends_at or "", json.dumps(metadata or {}, default=str), now, now),
    )
    entitlement_id = cur.lastrowid
    cur.execute(
        """
        INSERT INTO pulse_premium_entitlements
        (user_id, entitlement_key, source, status, starts_at, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
        ON CONFLICT(user_id, entitlement_key) DO UPDATE SET
            source=excluded.source,
            status='active',
            starts_at=excluded.starts_at,
            expires_at=excluded.expires_at,
            updated_at=excluded.updated_at
        """,
        (int(user_id), key, source, starts_at or now, ends_at or "", now, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "entitlement_id": entitlement_id, "user_id": int(user_id), "entitlement_key": key}


def revoke_entitlement(user_id: int, key: str, reason: str = "") -> dict[str, Any]:
    now = _now()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE premium_entitlements SET status='revoked', metadata_json=?, updated_at=? WHERE user_id=? AND entitlement_key=? AND status='active'",
        (json.dumps({"reason": reason}, default=str), now, int(user_id), key),
    )
    cur.execute(
        "UPDATE pulse_premium_entitlements SET status='revoked', updated_at=? WHERE user_id=? AND entitlement_key=?",
        (now, int(user_id), key),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "user_id": int(user_id), "entitlement_key": key, "revoked": True}


def sync_subscription_entitlements(user_id: int, plan_key: str = "pulse_premium", status: str = "active", period_end: str = "", source: str = "subscription") -> dict[str, Any]:
    now = _now()
    active = status in {"active", "trialing"}
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO subscriptions
        (user_id, plan_key, provider, status, current_period_end, created_at, updated_at)
        VALUES (?, ?, 'stripe', ?, ?, ?, ?)
        """,
        (int(user_id), plan_key, status, period_end or "", now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_subscriptions
        (user_id, plan_key, status, provider, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, 'stripe', ?, ?, ?)
        """,
        (int(user_id), plan_key, status, period_end or "", now, now),
    )
    conn.commit()
    conn.close()
    if active:
        return grant_entitlement(user_id, plan_key, source=source, ends_at=period_end, metadata={"subscription_status": status})
    return revoke_entitlement(user_id, plan_key, reason=f"subscription_{status}")
