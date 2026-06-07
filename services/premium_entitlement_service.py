"""Single source of truth for Premium and Founder entitlements."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from services import db as db_service

FOUNDER_PRICE_CENTS = 499
PREMIUM_VALUE_CENTS = 999
PAYMENT_PROVIDER_ENABLED = os.getenv("PAYMENT_PROVIDER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
STRIPE_FOUNDER_PRICE_ID = os.getenv("STRIPE_FOUNDER_PRICE_ID", "").strip()
STRIPE_PREMIUM_PRICE_ID = os.getenv("STRIPE_PREMIUM_PRICE_ID", "").strip()
STRIPE_PREMIUM_PLUS_PRICE_ID = os.getenv("STRIPE_PREMIUM_PLUS_PRICE_ID", "").strip()

FOUNDER_ENTITLEMENTS = [
    "premium_access",
    "founder_access",
    "founder_badge",
    "founder_hub_access",
    "creator_analytics",
    "creator_studio_pro",
    "ai_creator_assistant",
    "priority_support",
    "priority_verification",
    "premium_profile_themes",
    "premium_upload_limits",
    "early_access_features",
]

PREMIUM_ENTITLEMENTS = [
    "premium_access",
    "creator_analytics",
    "creator_studio_pro",
    "ai_creator_assistant",
    "premium_profile_themes",
    "premium_upload_limits",
]

PLAN_DEFINITIONS = {
    "free": {
        "name": "Free",
        "price_cents": 0,
        "currency": "usd",
        "billing_interval": "month",
        "status": "active",
        "description": "Normal PulseSoc access.",
    },
    "founder_premium": {
        "name": "Founder Premium",
        "price_cents": FOUNDER_PRICE_CENTS,
        "regular_price_cents": PREMIUM_VALUE_CENTS,
        "currency": "usd",
        "billing_interval": "month",
        "status": "active",
        "description": "Lifetime locked Founder pricing for early PulseSoc adopters.",
    },
    "premium_plus": {
        "name": "Premium Plus",
        "price_cents": 999,
        "currency": "usd",
        "billing_interval": "month",
        "status": "coming_soon",
        "description": "Future Premium Plus tier.",
    },
}


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def ensure_founder_schema() -> None:
    """Create Founder membership tables and seed plan metadata."""
    now = _now()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscription_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_key TEXT UNIQUE,
            name TEXT,
            price_cents INTEGER DEFAULT 0,
            regular_price_cents INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'usd',
            billing_interval TEXT DEFAULT 'month',
            status TEXT DEFAULT 'active',
            description TEXT,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plan_key TEXT,
            provider TEXT DEFAULT 'manual',
            provider_subscription_id TEXT,
            status TEXT DEFAULT 'inactive',
            locked_price_cents INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'usd',
            started_at TEXT,
            expires_at TEXT,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, plan_key)
        )
        """
    )
    for column, definition in [
        ("stripe_customer_id", "TEXT"),
        ("stripe_subscription_id", "TEXT"),
        ("stripe_checkout_session_id", "TEXT"),
        ("stripe_price_id", "TEXT"),
        ("stripe_product_id", "TEXT"),
        ("provider_status", "TEXT"),
        ("current_period_start", "TEXT"),
        ("current_period_end", "TEXT"),
        ("cancel_at_period_end", "INTEGER DEFAULT 0"),
        ("canceled_at", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE user_subscriptions ADD COLUMN {column} {definition}")
        except Exception:
            pass
    for column, definition in [
        ("stripe_customer_id", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        except Exception:
            pass
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stripe_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_event_id TEXT UNIQUE,
            event_id TEXT UNIQUE,
            event_type TEXT,
            user_id INTEGER,
            status TEXT,
            error_message TEXT,
            payload_summary TEXT,
            payload_json TEXT,
            created_at TEXT,
            processed_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_entitlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            entitlement_key TEXT,
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT 'manual',
            starts_at TEXT,
            expires_at TEXT,
            metadata_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, entitlement_key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS founder_memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            founder_number INTEGER UNIQUE,
            founder_tier TEXT DEFAULT 'Founder',
            locked_price INTEGER DEFAULT 499,
            currency TEXT DEFAULT 'usd',
            status TEXT DEFAULT 'active',
            activated_at TEXT,
            expires_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS premium_badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            badge_key TEXT,
            badge_label TEXT,
            badge_style TEXT,
            source TEXT DEFAULT 'premium',
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, badge_key)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS founder_wall_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            founder_number INTEGER UNIQUE,
            display_name TEXT,
            headline TEXT,
            avatar_url TEXT,
            status TEXT DEFAULT 'visible',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            badge_key TEXT UNIQUE,
            label TEXT,
            description TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_user_badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            badge_key TEXT,
            granted_by INTEGER,
            created_at TEXT,
            UNIQUE(user_id, badge_key)
        )
        """
    )
    for plan_key, plan in PLAN_DEFINITIONS.items():
        cur.execute(
            """
            INSERT INTO subscription_plans
            (plan_key, name, price_cents, regular_price_cents, currency, billing_interval, status, description, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_key) DO UPDATE SET
                name=excluded.name,
                price_cents=excluded.price_cents,
                regular_price_cents=excluded.regular_price_cents,
                currency=excluded.currency,
                billing_interval=excluded.billing_interval,
                status=excluded.status,
                description=excluded.description,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                plan_key,
                plan["name"],
                int(plan.get("price_cents") or 0),
                int(plan.get("regular_price_cents") or 0),
                plan.get("currency") or "usd",
                plan.get("billing_interval") or "month",
                plan.get("status") or "active",
                plan.get("description") or "",
                json.dumps(plan, default=str),
                now,
                now,
            ),
        )
    cur.execute(
        """
        INSERT INTO pulse_badges (badge_key, label, description, active, created_at)
        VALUES ('founder', 'Founder', 'Early PulseSoc Founder member with lifetime locked Founder pricing.', 1, ?)
        ON CONFLICT(badge_key) DO UPDATE SET
            label=excluded.label,
            description=excluded.description,
            active=1
        """,
        (now,),
    )
    conn.commit()
    conn.close()


def plan_definitions() -> dict[str, dict[str, Any]]:
    return dict(PLAN_DEFINITIONS)


def payment_config_status() -> dict[str, Any]:
    return {
        "payment_provider_enabled": PAYMENT_PROVIDER_ENABLED,
        "stripe_founder_price_configured": bool(STRIPE_FOUNDER_PRICE_ID),
        "stripe_premium_price_configured": bool(STRIPE_PREMIUM_PRICE_ID),
        "stripe_premium_plus_price_configured": bool(STRIPE_PREMIUM_PLUS_PRICE_ID),
    }


def stripe_founder_ready() -> bool:
    return bool(PAYMENT_PROVIDER_ENABLED and STRIPE_FOUNDER_PRICE_ID)


def update_founder_stripe_subscription(
    user_id: int,
    *,
    stripe_customer_id: str = "",
    stripe_subscription_id: str = "",
    stripe_checkout_session_id: str = "",
    stripe_price_id: str = "",
    stripe_product_id: str = "",
    provider_status: str = "",
    status: str = "active",
    current_period_start: str = "",
    current_period_end: str = "",
    cancel_at_period_end: bool = False,
    canceled_at: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_founder_schema()
    now = _now()
    payload = metadata or {}
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_subscriptions
        (user_id, plan_key, provider, provider_subscription_id, status, locked_price_cents, currency,
         started_at, expires_at, metadata_json, created_at, updated_at, stripe_customer_id,
         stripe_subscription_id, stripe_checkout_session_id, stripe_price_id, stripe_product_id,
         provider_status, current_period_start, current_period_end, cancel_at_period_end, canceled_at)
        VALUES (?, 'founder_premium', 'stripe', ?, ?, ?, 'usd', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, plan_key) DO UPDATE SET
            provider='stripe',
            provider_subscription_id=COALESCE(NULLIF(excluded.provider_subscription_id, ''), user_subscriptions.provider_subscription_id),
            status=excluded.status,
            locked_price_cents=excluded.locked_price_cents,
            currency='usd',
            started_at=COALESCE(user_subscriptions.started_at, excluded.started_at),
            expires_at=excluded.expires_at,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at,
            stripe_customer_id=COALESCE(NULLIF(excluded.stripe_customer_id, ''), user_subscriptions.stripe_customer_id),
            stripe_subscription_id=COALESCE(NULLIF(excluded.stripe_subscription_id, ''), user_subscriptions.stripe_subscription_id),
            stripe_checkout_session_id=COALESCE(NULLIF(excluded.stripe_checkout_session_id, ''), user_subscriptions.stripe_checkout_session_id),
            stripe_price_id=COALESCE(NULLIF(excluded.stripe_price_id, ''), user_subscriptions.stripe_price_id),
            stripe_product_id=COALESCE(NULLIF(excluded.stripe_product_id, ''), user_subscriptions.stripe_product_id),
            provider_status=excluded.provider_status,
            current_period_start=excluded.current_period_start,
            current_period_end=excluded.current_period_end,
            cancel_at_period_end=excluded.cancel_at_period_end,
            canceled_at=excluded.canceled_at
        """,
        (
            int(user_id),
            stripe_subscription_id,
            status,
            FOUNDER_PRICE_CENTS,
            now,
            current_period_end or "",
            json.dumps(payload, default=str),
            now,
            now,
            stripe_customer_id or "",
            stripe_subscription_id or "",
            stripe_checkout_session_id or "",
            stripe_price_id or STRIPE_FOUNDER_PRICE_ID,
            stripe_product_id or "",
            provider_status or status,
            current_period_start or "",
            current_period_end or "",
            1 if cancel_at_period_end else 0,
            canceled_at or "",
        ),
    )
    cur.execute(
        """
        UPDATE users SET
            stripe_customer_id=COALESCE(NULLIF(?, ''), stripe_customer_id),
            stripe_subscription_id=COALESCE(NULLIF(?, ''), stripe_subscription_id),
            provider_subscription_id=COALESCE(NULLIF(?, ''), provider_subscription_id),
            payment_provider='stripe',
            subscription_status=?,
            subscription_expires_at=COALESCE(NULLIF(?, ''), subscription_expires_at),
            updated_at=?
        WHERE user_id=?
        """,
        (
            stripe_customer_id or "",
            stripe_subscription_id or "",
            stripe_subscription_id or "",
            status,
            current_period_end or "",
            now,
            int(user_id),
        ),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "user_id": int(user_id), "status": status, "stripe_subscription_id": stripe_subscription_id}


def _next_founder_number(cur, user_id: int) -> int:
    cur.execute("SELECT founder_number FROM founder_memberships WHERE user_id=? LIMIT 1", (int(user_id),))
    existing = cur.fetchone()
    if existing and int(dict(existing).get("founder_number") or 0):
        return int(dict(existing).get("founder_number") or 0)
    cur.execute("SELECT COALESCE(MAX(founder_number), 0) + 1 AS next_number FROM founder_memberships")
    return int(dict(cur.fetchone() or {}).get("next_number") or 1)


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
    ensure_founder_schema()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, entitlement_key, status, starts_at, expires_at AS ends_at
        FROM user_entitlements
        WHERE user_id=? AND entitlement_key=? AND status='active'
        ORDER BY id DESC LIMIT 1
        """,
        (int(user_id or 0), key),
    )
    row = cur.fetchone()
    if not row:
        cur.execute(
            """
            SELECT user_id, entitlement_key, status, starts_at, ends_at
            FROM premium_entitlements
            WHERE user_id=? AND entitlement_key=? AND status='active'
            ORDER BY id DESC LIMIT 1
            """,
            (int(user_id or 0), key),
        )
        row = cur.fetchone()
    conn.close()
    return bool(row and _active_window(dict(row)))


def grant_entitlement(user_id: int, key: str, source: str = "admin_grant", starts_at: str = "", ends_at: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_founder_schema()
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
        INSERT INTO user_entitlements
        (user_id, entitlement_key, status, source, starts_at, expires_at, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, entitlement_key) DO UPDATE SET
            status='active',
            source=excluded.source,
            starts_at=excluded.starts_at,
            expires_at=excluded.expires_at,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (int(user_id), key, source, starts_at or now, ends_at or "", json.dumps(metadata or {}, default=str), now, now),
    )
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
    ensure_founder_schema()
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
    cur.execute(
        "UPDATE user_entitlements SET status='revoked', metadata_json=?, updated_at=? WHERE user_id=? AND entitlement_key=?",
        (json.dumps({"reason": reason}, default=str), now, int(user_id), key),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "user_id": int(user_id), "entitlement_key": key, "revoked": True}


def sync_subscription_entitlements(user_id: int, plan_key: str = "pulse_premium", status: str = "active", period_end: str = "", source: str = "subscription") -> dict[str, Any]:
    ensure_founder_schema()
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


def assign_founder_number(user_id: int) -> int:
    ensure_founder_schema()
    conn = db_service.connect()
    cur = conn.cursor()
    next_number = _next_founder_number(cur, user_id)
    conn.close()
    return next_number


def founder_tier_for_number(number: int) -> str:
    if int(number or 0) <= 100:
        return "Founder 100"
    if int(number or 0) <= 1000:
        return "Founder 1000"
    return "Founder"


def grant_founder_membership(user_id: int, actor_id: int = 0, source: str = "admin_manual") -> dict[str, Any]:
    ensure_founder_schema()
    now = _now()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT founder_number, founder_tier FROM founder_memberships WHERE user_id=? LIMIT 1", (int(user_id),))
    existing = cur.fetchone()
    founder_number = int((dict(existing).get("founder_number") if existing else 0) or _next_founder_number(cur, user_id))
    founder_tier = (dict(existing).get("founder_tier") if existing else "") or founder_tier_for_number(founder_number)
    metadata = {"source": source, "actor_id": int(actor_id or 0), "founder_number": founder_number, "founder_tier": founder_tier}
    cur.execute(
        """
        INSERT INTO founder_memberships
        (user_id, founder_number, founder_tier, locked_price, currency, status, activated_at, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'usd', 'active', ?, '', ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            founder_tier=excluded.founder_tier,
            locked_price=excluded.locked_price,
            currency=excluded.currency,
            status='active',
            activated_at=COALESCE(founder_memberships.activated_at, excluded.activated_at),
            expires_at='',
            updated_at=excluded.updated_at
        """,
        (int(user_id), founder_number, founder_tier, FOUNDER_PRICE_CENTS, now, now, now),
    )
    cur.execute(
        """
        INSERT INTO user_subscriptions
        (user_id, plan_key, provider, status, locked_price_cents, currency, started_at, expires_at, metadata_json, created_at, updated_at)
        VALUES (?, 'founder_premium', 'manual', 'active', ?, 'usd', ?, '', ?, ?, ?)
        ON CONFLICT(user_id, plan_key) DO UPDATE SET
            provider='manual',
            status='active',
            locked_price_cents=excluded.locked_price_cents,
            currency='usd',
            started_at=COALESCE(user_subscriptions.started_at, excluded.started_at),
            expires_at='',
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (int(user_id), FOUNDER_PRICE_CENTS, now, json.dumps(metadata, default=str), now, now),
    )
    cur.execute(
        """
        INSERT INTO premium_badges
        (user_id, badge_key, badge_label, badge_style, source, status, created_at, updated_at)
        VALUES (?, 'founder_badge', ?, 'founder_gold', ?, 'active', ?, ?)
        ON CONFLICT(user_id, badge_key) DO UPDATE SET
            badge_label=excluded.badge_label,
            badge_style=excluded.badge_style,
            source=excluded.source,
            status='active',
            updated_at=excluded.updated_at
        """,
        (int(user_id), f"Founder #{founder_number}", source, now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_user_badges (user_id, badge_key, granted_by, created_at)
        VALUES (?, 'founder', ?, ?)
        ON CONFLICT(user_id, badge_key) DO UPDATE SET
            granted_by=excluded.granted_by,
            created_at=excluded.created_at
        """,
        (int(user_id), int(actor_id or 0), now),
    )
    cur.execute(
        """
        INSERT INTO founder_wall_entries
        (user_id, founder_number, display_name, headline, avatar_url, status, created_at, updated_at)
        SELECT user_id, ?, COALESCE(display_name, username, email, 'PulseSoc Founder'), 'PulseSoc Founder Member', COALESCE(avatar_url,''), 'visible', ?, ?
        FROM users WHERE user_id=?
        ON CONFLICT(user_id) DO UPDATE SET
            founder_number=excluded.founder_number,
            display_name=excluded.display_name,
            status='visible',
            updated_at=excluded.updated_at
        """,
        (founder_number, now, now, int(user_id)),
    )
    cur.execute(
        """
        UPDATE users SET
            plan='premium',
            subscription_plan='founder_premium',
            subscription_status='active',
            premium_status='founder',
            is_pro=1,
            lifetime_premium=1,
            premium_glow_manual_grant=1,
            premium_mark_override=1,
            premium_mark_type='founder',
            updated_at=?
        WHERE user_id=?
        """,
        (now, int(user_id)),
    )
    conn.commit()
    conn.close()
    for entitlement in FOUNDER_ENTITLEMENTS:
        grant_entitlement(user_id, entitlement, source=source, metadata=metadata)
    return {"ok": True, "user_id": int(user_id), "founder_number": founder_number, "founder_tier": founder_tier, "entitlements": list(FOUNDER_ENTITLEMENTS)}


def revoke_premium_access(user_id: int, actor_id: int = 0, reason: str = "admin_revoke") -> dict[str, Any]:
    ensure_founder_schema()
    now = _now()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("UPDATE founder_memberships SET status='revoked', updated_at=? WHERE user_id=?", (now, int(user_id)))
    cur.execute("UPDATE founder_wall_entries SET status='hidden', updated_at=? WHERE user_id=?", (now, int(user_id)))
    cur.execute("UPDATE premium_badges SET status='revoked', updated_at=? WHERE user_id=?", (now, int(user_id)))
    cur.execute("DELETE FROM pulse_user_badges WHERE user_id=? AND badge_key='founder'", (int(user_id),))
    cur.execute("UPDATE user_subscriptions SET status='revoked', metadata_json=?, updated_at=? WHERE user_id=?", (json.dumps({"reason": reason, "actor_id": actor_id}, default=str), now, int(user_id)))
    cur.execute("UPDATE user_entitlements SET status='revoked', metadata_json=?, updated_at=? WHERE user_id=?", (json.dumps({"reason": reason, "actor_id": actor_id}, default=str), now, int(user_id)))
    cur.execute("UPDATE premium_entitlements SET status='revoked', metadata_json=?, updated_at=? WHERE user_id=?", (json.dumps({"reason": reason, "actor_id": actor_id}, default=str), now, int(user_id)))
    cur.execute("UPDATE pulse_premium_entitlements SET status='revoked', updated_at=? WHERE user_id=?", (now, int(user_id)))
    cur.execute(
        """
        UPDATE users SET subscription_status='inactive', premium_status='inactive', is_pro=0,
            lifetime_premium=0, premium_glow_manual_grant=0, premium_mark_override=0, updated_at=?
        WHERE user_id=?
        """,
        (now, int(user_id)),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "user_id": int(user_id), "revoked": True}


def founder_membership(user_id: int) -> dict[str, Any]:
    ensure_founder_schema()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM founder_memberships WHERE user_id=? AND status='active' LIMIT 1", (int(user_id or 0),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def founder_wall(limit: int = 12) -> list[dict[str, Any]]:
    ensure_founder_schema()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM founder_wall_entries
        WHERE status='visible'
        ORDER BY founder_number ASC
        LIMIT ?
        """,
        (int(limit or 12),),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def get_user_entitlements(user_id: int) -> dict[str, bool]:
    ensure_founder_schema()
    keys = list(dict.fromkeys(FOUNDER_ENTITLEMENTS + PREMIUM_ENTITLEMENTS))
    return {key: has_entitlement(user_id, key) for key in keys}


def is_premium_user(user_id: int) -> bool:
    if has_entitlement(user_id, "premium_access"):
        return True
    if is_founder_member(user_id):
        return True
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT premium_status, subscription_status, lifetime_premium, premium_glow_manual_grant FROM users WHERE user_id=? LIMIT 1", (int(user_id or 0),))
    row = dict(cur.fetchone() or {})
    conn.close()
    return bool(
        int(row.get("lifetime_premium") or 0)
        or int(row.get("premium_glow_manual_grant") or 0)
        or str(row.get("premium_status") or "").lower() in {"active", "founder", "lifetime", "trial"}
        or str(row.get("subscription_status") or "").lower() in {"active", "trialing"}
    )


def is_founder_member(user_id: int) -> bool:
    return bool(founder_membership(user_id))


# CamelCase aliases requested by product spec.
isPremiumUser = is_premium_user
isFounderMember = is_founder_member
getUserEntitlements = get_user_entitlements
grantFounderMembership = grant_founder_membership
revokePremiumAccess = revoke_premium_access
assignFounderNumber = assign_founder_number
