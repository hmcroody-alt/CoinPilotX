"""Founder identity + LEGACY premium tables, and a compatibility shim.

.. warning::

   This module is **no longer the premium authority**. The canonical premium
   entitlement system is :mod:`services.business_os.entitlements`; see
   ``entitlements/premium.py`` for the single resolver.

What still legitimately lives here:

* **Founder identity** — ``founder_memberships``, founder NUMBER allocation,
  the founder wall, badges. The founder number is permanent and never reassigned,
  so this table remains its owner.
* **Legacy premium tables** — ``user_entitlements`` / ``premium_entitlements``
  reads and writes, kept working for accounts that predate canonical grants.

What is now a shim:

* :func:`is_premium_user` delegates to the canonical resolver. It keeps no
  policy of its own.

The raw legacy readers survive as ``_is_premium_user_raw`` /
``_is_founder_member_raw`` purely so the canonical parity check has an
independent legacy answer to compare against. Do not gate features on them.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from services import db as db_service
from services.schema_guard import run_once_per_process

_log = logging.getLogger("services.premium_entitlement_service")

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


@run_once_per_process
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


# --- canonical provider bridge (App Store / Play convergence) ----------------
# Apple StoreKit verification (`services.business_os.entitlements.iap_apple`)
# and the ASSN v2 webhook write ONLY to the canonical ``business_os_ent_grants``
# store — there is no legacy dual-write like the Stripe path has. Under the
# default ``BUSINESS_OS_ENTITLEMENTS=off`` flag, legacy is authoritative, so an
# App Store purchase would grant an entitlement nothing reads and the buyer
# would see no Premium. This bridge makes the LEGACY read path consult the
# canonical store for PROVIDER-sourced premium grants, evaluated LIVE (expiry,
# grace and revocation are re-checked on every read, never copied as a boolean),
# so a refunded or lapsed Apple subscription loses Premium through the same
# path that granted it.
#
# Deliberately scoped to provider sources: admin/promotion/trial canonical
# grants keep their "flag off = zero behaviour change" semantics until the flag
# is advanced (see tests/business_os/test_premium_canonical.py PREM-002/009).
_CANONICAL_PREMIUM_KEY = "premium.access"
_CANONICAL_PROVIDER_SOURCES = {"apple_app_store", "google_play"}
# Legacy keys whose truth may be satisfied by a canonical provider grant. Only
# the umbrella membership key is bridged; per-feature legacy keys stay legacy.
_BRIDGED_LEGACY_KEYS = {"premium_access"}


def _canonical_provider_premium(user_id: int) -> bool:
    """Live canonical answer for provider (Apple/Google IAP) premium grants.

    Returns True only when a grant from a provider source currently resolves to
    allowed under canonical precedence (active / in grace / grandfathered). A
    suspension on ANY grant for the key denies, mirroring canonical rule 1.
    Best-effort: if the canonical package or table is unavailable this returns
    False and legacy behaviour is unchanged.
    """
    try:
        from services.business_os.entitlements import service as _canon
    except Exception:  # noqa: BLE001 — canonical package absent
        return False
    try:
        conn = db_service.connect()
        try:
            grants = _canon._fetch_grants(
                conn, "user", _canon._sid(int(user_id or 0)), _CANONICAL_PREMIUM_KEY)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — table not created yet, engine hiccup
        return False
    if not grants:
        return False
    now = _canon._utc_now()
    try:
        if _canon._resolve(grants, now)["mode"] == "suspended":
            return False
        provider_grants = [
            g for g in grants
            if str(g.get("source") or "") in _CANONICAL_PROVIDER_SOURCES
        ]
        if not provider_grants:
            return False
        return bool(_canon._resolve(provider_grants, now)["allowed"])
    except Exception:  # noqa: BLE001
        _log.exception("canonical provider bridge failed user=%s", user_id)
        return False


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
    if row and _active_window(dict(row)):
        return True
    # No live legacy row: consult the canonical store for provider-sourced
    # grants (Apple/Google purchases write canonical-only). See bridge above.
    if key in _BRIDGED_LEGACY_KEYS:
        return _canonical_provider_premium(user_id)
    return False


def grant_entitlement(user_id: int, key: str, source: str = "admin_grant", starts_at: str = "", ends_at: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_founder_schema()
    now = _now()
    conn = db_service.connect()
    cur = conn.cursor()
    # Idempotent on (user_id, entitlement_key). The sibling tables below already
    # upsert; this one used a blind INSERT, so a repeated grant — a StoreKit
    # "Restore Purchases" tap, a replayed webhook, a double-clicked admin button
    # — appended a NEW row every time. Five restores produced five grants for the
    # same entitlement. ``premium_entitlements`` has no unique constraint we can
    # rely on across SQLite and Postgres, so this is an explicit
    # select-then-update-or-insert rather than an ON CONFLICT clause.
    cur.execute(
        "SELECT id FROM premium_entitlements WHERE user_id=? AND entitlement_key=? "
        "ORDER BY id DESC LIMIT 1",
        (int(user_id), key),
    )
    existing = cur.fetchone()
    if existing:
        entitlement_id = int(dict(existing).get("id") or 0)
        cur.execute(
            """
            UPDATE premium_entitlements
            SET status='active', source=?, starts_at=?, ends_at=?, metadata_json=?, updated_at=?
            WHERE id=?
            """,
            (source, starts_at or now, ends_at or "", json.dumps(metadata or {}, default=str), now, entitlement_id),
        )
    else:
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

    # Allocation policy. Applies ONLY to brand-new founders: an existing founder
    # being re-granted (reactivation, replayed webhook, admin repair) must never
    # be refused, because refusing would strip a member who already holds a
    # number. Sequential numbering is not a cap — it only guarantees uniqueness —
    # so the cap is enforced here, explicitly, at the moment of issue.
    if not existing:
        try:
            from services.business_os.entitlements import premium as _premium
            allowed, deny_reason = _premium.founder_allocation_available()
        except Exception:  # noqa: BLE001 — policy module absent => historic behaviour
            allowed, deny_reason = True, ""
        if not allowed:
            conn.close()
            return {"ok": False, "user_id": int(user_id), "error": deny_reason,
                    "founder_number": 0}

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
    _project_founder_to_canonical(user_id, founder_number, founder_tier, source)
    return {"ok": True, "user_id": int(user_id), "founder_number": founder_number, "founder_tier": founder_tier, "entitlements": list(FOUNDER_ENTITLEMENTS)}


def _project_founder_to_canonical(user_id: int, founder_number: int,
                                  founder_tier: str, source: str) -> None:
    """Mirror a founder membership into the canonical entitlement system.

    A Founder is three separate facts that were previously fused into one legacy
    row, and keeping them distinct is what makes the migration safe:

    1. **Identity** — the founder NUMBER and badge. Stays in
       ``founder_memberships``; that table owns the number, which is permanent
       and never reassigned to anyone else.
    2. **Billing** — the grandfathered $4.99 locked price. Also stays in the
       legacy subscription row; canonical does not re-price anyone.
    3. **Entitlement** — actual Premium access. This is the part that becomes
       canonical, granted on the existing ``pulse_premium_grandfathered`` plan
       with status ``grandfathered`` so it never expires and is never swept up
       by renewal/expiry reconciliation.

    Best-effort and non-fatal: a founder must still be granted in the legacy
    system even if canonical projection fails, since under the default flag mode
    legacy is what actually gates access. The failure is logged, and the parity
    report will show the account as ``backfill_grant`` so it is not lost.
    """
    try:
        from services.business_os.entitlements import premium as _premium
        from services.business_os.entitlements import schema as _schema
        from services.business_os.entitlements import service as _ent
    except Exception:  # noqa: BLE001
        return
    try:
        _schema.ensure_ready()
        # ``source_reference`` is the permanent founder number, so re-granting an
        # existing founder updates the SAME canonical grant rather than stacking
        # a second one. Provenance maps to the canonical vocabulary: a Stripe
        # webhook-driven founder is 'stripe', a manual admin grant is 'admin'.
        canonical_source = "stripe" if str(source).startswith("stripe") else "admin"
        _ent.sync_subscription_entitlements(
            int(user_id), _premium.FOUNDER_PLAN_KEY,
            status="grandfathered", source=canonical_source,
            source_reference=f"founder:{int(founder_number)}",
            subject_type="user", actor=f"founder_grant:{source}",
        )
    except Exception:  # noqa: BLE001
        _log.exception("canonical founder projection failed user=%s", user_id)


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


def _is_premium_user_raw(user_id: int) -> bool:
    """LEGACY premium truth, read straight from the legacy tables + user columns.

    This is the pre-canonicalization implementation, preserved verbatim and made
    private. It is retained for exactly one reason: the canonical resolver's
    parity check needs a genuinely INDEPENDENT legacy answer to compare against.
    If parity compared canonical to the public ``is_premium_user`` — which now
    delegates to canonical — every user would report "in sync" and the
    split-brain this migration exists to close would be invisible.

    Do not call this to gate a feature. Call :func:`is_premium_user`.

    One deliberate exception to "independent": ``has_entitlement`` bridges the
    umbrella ``premium_access`` key to canonical PROVIDER grants (Apple/Google
    IAP), because those providers write canonical-only and must be effective
    under the default flag. See ``_canonical_provider_premium``.
    """
    if has_entitlement(user_id, "premium_access"):
        return True
    if _is_founder_member_raw(user_id):
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


def _is_founder_member_raw(user_id: int) -> bool:
    """LEGACY founder truth from ``founder_memberships``. Private; see above."""
    return bool(founder_membership(user_id))


def is_premium_user(user_id: int) -> bool:
    """COMPATIBILITY SHIM — resolves through the canonical entitlement system.

    ``services.business_os.entitlements`` is the single premium authority. This
    function survives only so the ~30 existing call sites keep working; it adds
    no policy of its own. Under ``BUSINESS_OS_ENTITLEMENTS=off`` the canonical
    resolver returns the legacy answer, so behaviour is unchanged until the flag
    is deliberately advanced.

    Falls back to the raw legacy reader if the canonical package cannot be
    imported, so a packaging problem degrades to the old behaviour rather than
    locking every premium user out.
    """
    try:
        from services.business_os.entitlements import premium as _premium
    except Exception:  # noqa: BLE001
        return _is_premium_user_raw(user_id)
    return _premium.is_premium(user_id)


def is_founder_member(user_id: int) -> bool:
    """Founder membership. Founder identity remains recorded in
    ``founder_memberships`` (that table owns the founder NUMBER, which is
    permanent and never reassigned); the canonical system owns the resulting
    premium ENTITLEMENT. The two answer different questions and both are needed.
    """
    return _is_founder_member_raw(user_id)


# CamelCase aliases requested by product spec.
isPremiumUser = is_premium_user
isFounderMember = is_founder_member
getUserEntitlements = get_user_entitlements
grantFounderMembership = grant_founder_membership
revokePremiumAccess = revoke_premium_access
assignFounderNumber = assign_founder_number
