"""PulseSoc Growth Engine foundation.

Internally this layer provisions advertising and promotion infrastructure.
User-facing surfaces call it the Growth Engine so accounts feel like PulseSoc
is ready to help them grow, not like they had to create an ads product.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from services import db as db_service
from services import pulse_ads_service
from services.schema_guard import run_once_per_process


GROWTH_MODULES = (
    "Overview",
    "Campaigns",
    "Audience",
    "Budget",
    "Performance",
    "Insights",
    "AI Campaigns",
    "Billing",
    "Promotion Credits",
    "Reports",
    "Marketplace Promotion",
    "Music Promotion",
    "Reels Promotion",
    "Video Promotion",
    "Live Promotion",
    "Business Promotion",
    "Event Promotion",
    "Job Promotion",
    "Creator Promotion",
    "Referral Campaigns",
)
AUDIENCE_CATEGORIES = (
    "Technology",
    "Crypto",
    "Music",
    "Gaming",
    "Sports",
    "Education",
    "Travel",
    "Shopping",
    "Business",
    "Fashion",
    "Food",
    "Photography",
    "Automotive",
    "Entertainment",
    "Finance",
    "AI",
    "Science",
)
DEFAULT_PREFERENCES = {
    "auto_recommendations": True,
    "ai_growth_advisor": True,
    "promotion_notifications": True,
    "progressive_modules": True,
    "billing_active": False,
    "advanced_audience_tools": False,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def clean_text(value: Any, limit: int = 240) -> str:
    return pulse_ads_service.clean_text(value, limit)


def json_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _table_exists(cur: Any, table: str) -> bool:
    try:
        if db_service.IS_POSTGRES:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (table,))
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(cur.fetchone())
    except Exception:
        return False


def _count(cur: Any, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(cur, table):
        return 0
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
        row = cur.fetchone()
        return int(row["total"] if hasattr(row, "keys") else row[0])
    except Exception:
        return 0


def _hash_secret(secret: str) -> str:
    salt = os.getenv("PULSESOC_GROWTH_KEY_SALT") or os.getenv("SESSION_SECRET") or "pulsesoc-growth-local"
    return hashlib.sha256(f"{salt}:{secret}".encode("utf-8")).hexdigest()


def _public_id(prefix: str, user_id: int) -> str:
    digest = hashlib.sha256(f"{prefix}:{user_id}:{os.getenv('SESSION_SECRET','local')}".encode("utf-8")).hexdigest()[:18]
    return f"{prefix}_{digest}"


@run_once_per_process
def ensure_schema(conn: Any | None = None) -> None:
    own_conn = conn is None
    conn = conn or db_service.connect()
    cur = conn.cursor()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_ad_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            business_name TEXT NOT NULL,
            business_email TEXT,
            business_phone TEXT,
            business_website TEXT,
            business_type TEXT,
            status TEXT DEFAULT 'pending_verification',
            verification_status TEXT DEFAULT 'unverified',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_ad_wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            currency TEXT DEFAULT 'usd',
            available_balance_cents INTEGER DEFAULT 0,
            pending_balance_cents INTEGER DEFAULT 0,
            promotional_credits_cents INTEGER DEFAULT 0,
            bonus_credits_cents INTEGER DEFAULT 0,
            refund_credits_cents INTEGER DEFAULT 0,
            lifetime_funded_cents INTEGER DEFAULT 0,
            lifetime_spent_cents INTEGER DEFAULT 0,
            reserved_budget_cents INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(account_id, currency)
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_ad_wallet_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            campaign_id INTEGER,
            creative_id INTEGER,
            transaction_type TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            currency TEXT DEFAULT 'usd',
            status TEXT DEFAULT 'posted',
            idempotency_key TEXT UNIQUE,
            description TEXT,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_ad_billing_profiles (
            account_id INTEGER PRIMARY KEY,
            wallet_balance_cents INTEGER DEFAULT 0,
            spend_limit_cents INTEGER DEFAULT 0,
            billing_status TEXT DEFAULT 'not_configured',
            stripe_customer_id TEXT,
            funding_status TEXT DEFAULT 'prepared',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT UNIQUE,
            user_id INTEGER NOT NULL UNIQUE,
            default_ad_account_id INTEGER,
            status TEXT DEFAULT 'ready',
            lifecycle_stage TEXT DEFAULT 'provisioned',
            growth_score INTEGER DEFAULT 0,
            trust_score INTEGER DEFAULT 0,
            risk_level TEXT DEFAULT 'normal',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_workspaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            growth_account_id INTEGER NOT NULL,
            workspace_name TEXT NOT NULL,
            modules_json TEXT DEFAULT '[]',
            unlocked_modules_json TEXT DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            growth_account_id INTEGER NOT NULL,
            currency TEXT DEFAULT 'usd',
            status TEXT DEFAULT 'inactive',
            credits_cents INTEGER DEFAULT 0,
            coupons_cents INTEGER DEFAULT 0,
            referral_bonus_cents INTEGER DEFAULT 0,
            lifetime_spend_cents INTEGER DEFAULT 0,
            lifetime_refunds_cents INTEGER DEFAULT 0,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            growth_account_id INTEGER NOT NULL,
            entry_type TEXT NOT NULL,
            amount_cents INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'usd',
            status TEXT DEFAULT 'posted',
            idempotency_key TEXT UNIQUE,
            description TEXT,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_audience_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            categories_json TEXT DEFAULT '[]',
            profile_json TEXT DEFAULT '{}',
            privacy_mode TEXT DEFAULT 'aggregate_only',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_audience_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            model_version TEXT DEFAULT 'v1',
            learning_state TEXT DEFAULT 'cold_start',
            signals_json TEXT DEFAULT '{}',
            updated_at TEXT,
            created_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_creator_growth_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            creator_type TEXT DEFAULT 'creator',
            growth_stage TEXT DEFAULT 'ready',
            recommendations_json TEXT DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_promotion_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            growth_account_id INTEGER NOT NULL,
            source_type TEXT,
            source_id TEXT,
            action TEXT,
            status TEXT DEFAULT 'recorded',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_billing_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            growth_account_id INTEGER NOT NULL,
            status TEXT DEFAULT 'inactive',
            provider TEXT DEFAULT '',
            provider_customer_hash TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            preferences_json TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_ai_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            session_public_id TEXT UNIQUE,
            status TEXT DEFAULT 'ready',
            last_prompt_at TEXT,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_analytics_containers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            container_public_id TEXT UNIQUE,
            conversion_tracking_id TEXT UNIQUE,
            status TEXT DEFAULT 'ready',
            metrics_json TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key_scope TEXT DEFAULT 'promotion_internal',
            key_prefix TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            rotated_at TEXT,
            UNIQUE(user_id, key_scope)
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            growth_score INTEGER DEFAULT 0,
            score_factors_json TEXT DEFAULT '{}',
            calculated_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_trust_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            trust_source TEXT DEFAULT 'user_trust_engine',
            trust_score INTEGER DEFAULT 0,
            trust_level TEXT DEFAULT 'baseline',
            updated_at TEXT,
            created_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_risk_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            risk_level TEXT DEFAULT 'normal',
            risk_score INTEGER DEFAULT 0,
            fraud_flags_json TEXT DEFAULT '[]',
            updated_at TEXT,
            created_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pulse_growth_provisioning_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            status TEXT DEFAULT 'ok',
            details_json TEXT DEFAULT '{}',
            created_at TEXT
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_growth_accounts_user ON pulse_growth_accounts(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_growth_ledger_user_created ON pulse_growth_ledger(user_id, created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_growth_history_user_created ON pulse_growth_promotion_history(user_id, created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_growth_log_user_created ON pulse_growth_provisioning_log(user_id, created_at)")
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def _log(cur: Any, user_id: int, action: str, status: str = "ok", details: dict[str, Any] | None = None) -> None:
    cur.execute(
        """
        INSERT INTO pulse_growth_provisioning_log (user_id, action, status, details_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(user_id), clean_text(action, 80), clean_text(status, 40), json_text(details or {}), now_iso()),
    )


def _select_one(cur: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    cur.execute(sql, params)
    return row_to_dict(cur.fetchone())


def _ensure_ad_account(cur: Any, user: dict[str, Any]) -> int:
    user_id = int(user.get("user_id") or user.get("id") or 0)
    existing = _select_one(cur, "SELECT id FROM pulse_ad_accounts WHERE owner_user_id=? ORDER BY id ASC LIMIT 1", (user_id,))
    if existing:
        return int(existing.get("id") or 0)
    now = now_iso()
    name = clean_text(user.get("display_name") or user.get("full_name") or user.get("username") or "PulseSoc Growth Workspace", 120)
    email = clean_text(user.get("email"), 160)
    cur.execute(
        """
        INSERT INTO pulse_ad_accounts
        (owner_user_id, business_name, business_email, business_phone, business_website, business_type, status, verification_status, created_at, updated_at)
        VALUES (?, ?, ?, '', '', 'growth_engine', 'pending_verification', 'unverified', ?, ?)
        """,
        (user_id, f"{name} Growth", email, now, now),
    )
    account_id = int(cur.lastrowid or 0)
    _log(cur, user_id, "growth_ad_account_created", details={"account_id": account_id})
    return account_id


def _ensure_ad_wallet(cur: Any, user_id: int, ad_account_id: int) -> None:
    now = now_iso()
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_ad_wallets
        (account_id, currency, available_balance_cents, pending_balance_cents, promotional_credits_cents,
         bonus_credits_cents, refund_credits_cents, lifetime_funded_cents, lifetime_spent_cents,
         reserved_budget_cents, created_at, updated_at)
        VALUES (?, 'usd', 0, 0, 0, 0, 0, 0, 0, 0, ?, ?)
        """,
        (ad_account_id, now, now),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_ad_billing_profiles
        (account_id, wallet_balance_cents, spend_limit_cents, billing_status, funding_status, created_at, updated_at)
        VALUES (?, 0, 0, 'inactive', 'prepared', ?, ?)
        """,
        (ad_account_id, now, now),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_ad_wallet_transactions
        (account_id, transaction_type, amount_cents, currency, status, idempotency_key, description, metadata_json, created_at)
        VALUES (?, 'growth_wallet_provisioned', 0, 'usd', 'posted', ?, 'Promotion wallet provisioned.', '{}', ?)
        """,
        (ad_account_id, f"growth-wallet-provisioned:{user_id}:{ad_account_id}", now),
    )


def _growth_score(cur: Any, user: dict[str, Any]) -> tuple[int, dict[str, int]]:
    user_id = int(user.get("user_id") or user.get("id") or 0)
    factors = {
        "profile": 20 if (user.get("display_name") or user.get("full_name")) else 8,
        "trust": 20 if int(user.get("email_verified") or 0) else 10,
        "activity": min(20, _count(cur, "pulse_posts", "user_id=?", (user_id,)) * 2 + _count(cur, "pulse_videos", "owner_user_id=?", (user_id,)) * 3),
        "age": 10,
        "verification": 20 if int(user.get("is_super_user") or 0) else 8,
        "consistency": 12,
    }
    return max(1, min(100, sum(factors.values()))), factors


def _creator_type(cur: Any, user_id: int) -> str:
    if _count(cur, "pulse_music_tracks", "owner_user_id=?", (user_id,)) > 0:
        return "musician"
    if _count(cur, "marketplace_products", "seller_user_id=?", (user_id,)) > 0:
        return "marketplace_seller"
    if _count(cur, "pulse_videos", "owner_user_id=?", (user_id,)) > 0:
        return "creator"
    return "creator"


def provision_user(conn: Any, user: dict[str, Any], *, source: str = "runtime", commit: bool = True) -> dict[str, Any]:
    ensure_schema(conn)
    cur = conn.cursor()
    user_id = int(user.get("user_id") or user.get("id") or 0)
    if user_id <= 0:
        raise ValueError("Growth Engine provisioning requires user_id.")
    now = now_iso()
    ad_account_id = _ensure_ad_account(cur, user)
    _ensure_ad_wallet(cur, user_id, ad_account_id)
    score, factors = _growth_score(cur, user)
    public_id = _public_id("grw", user_id)
    metadata = {
        "source": source,
        "internal_ad_account_id": ad_account_id,
        "user_facing_name": "Growth Engine",
        "external_terms": ["Growth Center", "Promotion Wallet", "Pulse AI Growth Advisor"],
    }
    cur.execute(
        """
        INSERT INTO pulse_growth_accounts
        (public_id, user_id, default_ad_account_id, status, lifecycle_stage, growth_score, trust_score, risk_level, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, 'ready', 'provisioned', ?, ?, 'normal', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            default_ad_account_id=excluded.default_ad_account_id,
            growth_score=excluded.growth_score,
            trust_score=excluded.trust_score,
            updated_at=excluded.updated_at
        """,
        (public_id, user_id, ad_account_id, score, factors.get("trust", 0), json_text(metadata), now, now),
    )
    account = _select_one(cur, "SELECT * FROM pulse_growth_accounts WHERE user_id=?", (user_id,))
    growth_account_id = int(account.get("id") or 0)
    cur.execute(
        """
        INSERT INTO pulse_growth_workspaces
        (user_id, growth_account_id, workspace_name, modules_json, unlocked_modules_json, created_at, updated_at)
        VALUES (?, ?, 'Campaign Workspace', ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET growth_account_id=excluded.growth_account_id, modules_json=excluded.modules_json, updated_at=excluded.updated_at
        """,
        (user_id, growth_account_id, json_text(list(GROWTH_MODULES)), json_text(["Overview", "Promote Post", "Promote Reel", "Promote Marketplace", "Promote Business"]), now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_growth_wallets
        (user_id, growth_account_id, currency, status, credits_cents, coupons_cents, referral_bonus_cents, lifetime_spend_cents, lifetime_refunds_cents, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'usd', 'inactive', 0, 0, 0, 0, 0, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET growth_account_id=excluded.growth_account_id, updated_at=excluded.updated_at
        """,
        (user_id, growth_account_id, json_text({"billing_profile": "inactive", "funding": "prepared"}), now, now),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_growth_ledger
        (user_id, growth_account_id, entry_type, amount_cents, currency, status, idempotency_key, description, metadata_json, created_at)
        VALUES (?, ?, 'wallet_provisioned', 0, 'usd', 'posted', ?, 'Promotion wallet provisioned.', ?, ?)
        """,
        (user_id, growth_account_id, f"growth-ledger-provisioned:{user_id}", json_text({"source": source}), now),
    )
    cur.execute(
        """
        INSERT INTO pulse_growth_audience_profiles
        (user_id, categories_json, profile_json, privacy_mode, created_at, updated_at)
        VALUES (?, ?, ?, 'aggregate_only', ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET categories_json=excluded.categories_json, updated_at=excluded.updated_at
        """,
        (user_id, json_text(list(AUDIENCE_CATEGORIES)), json_text({"private_data_exposed": False}), now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_growth_audience_models
        (user_id, model_version, learning_state, signals_json, updated_at, created_at)
        VALUES (?, 'v1', 'cold_start', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at
        """,
        (user_id, json_text({"private_conversation_learning": False, "aggregate_only": True}), now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_creator_growth_profiles
        (user_id, creator_type, growth_stage, recommendations_json, created_at, updated_at)
        VALUES (?, ?, 'ready', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET creator_type=excluded.creator_type, updated_at=excluded.updated_at
        """,
        (user_id, _creator_type(cur, user_id), json_text(["Complete your profile", "Post consistently", "Use Pulse AI Growth Advisor"]), now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_growth_billing_profiles
        (user_id, growth_account_id, status, provider, provider_customer_hash, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'inactive', '', '', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET growth_account_id=excluded.growth_account_id, updated_at=excluded.updated_at
        """,
        (user_id, growth_account_id, json_text({"live_charging": False}), now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_growth_preferences
        (user_id, preferences_json, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at
        """,
        (user_id, json_text(DEFAULT_PREFERENCES), now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_growth_ai_sessions
        (user_id, session_public_id, status, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'ready', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at
        """,
        (user_id, _public_id("gai", user_id), json_text({"assistant": "Pulse AI Growth Advisor"}), now, now),
    )
    conversion_id = _public_id("pxg", user_id)
    cur.execute(
        """
        INSERT INTO pulse_growth_analytics_containers
        (user_id, container_public_id, conversion_tracking_id, status, metrics_json, created_at, updated_at)
        VALUES (?, ?, ?, 'ready', ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at
        """,
        (user_id, _public_id("gac", user_id), conversion_id, json_text({"reach": 0, "impressions": 0, "clicks": 0, "conversions": 0}), now, now),
    )
    secret = f"pgk_{secrets.token_urlsafe(24)}"
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_growth_api_keys
        (user_id, key_scope, key_prefix, key_hash, status, created_at, rotated_at)
        VALUES (?, 'promotion_internal', ?, ?, 'active', ?, ?)
        """,
        (user_id, secret[:10], _hash_secret(secret), now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_growth_scores
        (user_id, growth_score, score_factors_json, calculated_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET growth_score=excluded.growth_score, score_factors_json=excluded.score_factors_json, calculated_at=excluded.calculated_at, updated_at=excluded.updated_at
        """,
        (user_id, score, json_text(factors), now, now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_growth_trust_links
        (user_id, trust_source, trust_score, trust_level, updated_at, created_at)
        VALUES (?, 'user_trust_engine', ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET trust_score=excluded.trust_score, trust_level=excluded.trust_level, updated_at=excluded.updated_at
        """,
        (user_id, factors.get("trust", 0), "verified" if factors.get("trust", 0) >= 20 else "baseline", now, now),
    )
    risk = max(0, 30 - factors.get("trust", 0))
    cur.execute(
        """
        INSERT INTO pulse_growth_risk_profiles
        (user_id, risk_level, risk_score, fraud_flags_json, updated_at, created_at)
        VALUES (?, ?, ?, '[]', ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET risk_level=excluded.risk_level, risk_score=excluded.risk_score, updated_at=excluded.updated_at
        """,
        (user_id, "normal" if risk < 30 else "review", risk, now, now),
    )
    if _count(cur, "pulse_growth_promotion_history", "user_id=? AND source_type='growth_engine' AND action='provisioned'", (user_id,)) == 0:
        cur.execute(
            """
            INSERT INTO pulse_growth_promotion_history
            (user_id, growth_account_id, source_type, source_id, action, status, metadata_json, created_at)
            VALUES (?, ?, 'growth_engine', ?, 'provisioned', 'recorded', ?, ?)
            """,
            (user_id, growth_account_id, public_id, json_text({"source": source}), now),
        )
    _log(cur, user_id, "growth_engine_provisioned", details={"source": source, "growth_account_id": growth_account_id, "ad_account_id": ad_account_id})
    if commit:
        conn.commit()
    return growth_summary(conn, user_id)


def growth_summary(conn: Any, user_id: int) -> dict[str, Any]:
    cur = conn.cursor()
    account = _select_one(cur, "SELECT * FROM pulse_growth_accounts WHERE user_id=?", (int(user_id),))
    wallet = _select_one(cur, "SELECT * FROM pulse_growth_wallets WHERE user_id=?", (int(user_id),))
    analytics = _select_one(cur, "SELECT * FROM pulse_growth_analytics_containers WHERE user_id=?", (int(user_id),))
    scores = _select_one(cur, "SELECT * FROM pulse_growth_scores WHERE user_id=?", (int(user_id),))
    preferences = _select_one(cur, "SELECT * FROM pulse_growth_preferences WHERE user_id=?", (int(user_id),))
    return {
        "account": {
            "public_id": account.get("public_id"),
            "status": account.get("status") or "ready",
            "lifecycle_stage": account.get("lifecycle_stage") or "provisioned",
            "default_workspace_id": account.get("default_ad_account_id"),
        },
        "wallet": {
            "status": wallet.get("status") or "inactive",
            "credits_cents": int(wallet.get("credits_cents") or 0),
            "currency": wallet.get("currency") or "usd",
        },
        "analytics": {
            "container_public_id": analytics.get("container_public_id"),
            "conversion_tracking_id": analytics.get("conversion_tracking_id"),
            "status": analytics.get("status") or "ready",
        },
        "growth_score": int(scores.get("growth_score") or account.get("growth_score") or 0),
        "preferences": json.loads(preferences.get("preferences_json") or "{}") if preferences else dict(DEFAULT_PREFERENCES),
        "modules": list(GROWTH_MODULES),
        "audience_categories": list(AUDIENCE_CATEGORIES),
    }


def build_growth_state(conn: Any, user: dict[str, Any]) -> dict[str, Any]:
    user_id = int(user.get("user_id") or user.get("id") or 0)
    if user_id <= 0:
        raise ValueError("Growth Center requires a valid user account.")
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM pulse_growth_accounts WHERE user_id=? LIMIT 1",
            (user_id,),
        )
        provisioned = bool(cur.fetchone())
    except Exception:
        provisioned = False
    summary = (
        growth_summary(conn, user_id)
        if provisioned
        else provision_user(conn, user, source="growth_center_fallback", commit=True)
    )
    from services import pulse_advertiser_portal

    portal = pulse_advertiser_portal.portal_summary(conn, user_id)
    return {
        "growth": summary,
        "portal": portal,
        "hero": {
            "title": "Grow your reach.",
            "body": "PulseSoc is ready whenever you want to promote your content, business, music, marketplace listings, events, or live streams.",
            "cta": "Explore Growth",
        },
    }


def backfill_missing_growth_engines(limit: int = 500, after_user_id: int = 0) -> dict[str, Any]:
    ensure_schema()
    conn = db_service.connect()
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, username, display_name, full_name, email, email_verified, created_at FROM users WHERE user_id>? ORDER BY user_id ASC LIMIT ?",
            (int(after_user_id or 0), max(1, min(int(limit or 500), 5000))),
        )
        rows = [row_to_dict(row) for row in cur.fetchall()]
        processed = 0
        created = 0
        errors: list[dict[str, Any]] = []
        last_user_id = int(after_user_id or 0)
        for user in rows:
            user_id = int(user.get("user_id") or 0)
            last_user_id = max(last_user_id, user_id)
            try:
                before = _count(cur, "pulse_growth_accounts", "user_id=?", (user_id,))
                provision_user(conn, user, source="backfill", commit=False)
                after = _count(cur, "pulse_growth_accounts", "user_id=?", (user_id,))
                processed += 1
                if before == 0 and after > 0:
                    created += 1
            except Exception as exc:
                errors.append({"user_id": user_id, "error": str(exc)[:300]})
                _log(cur, user_id, "growth_backfill_failed", "failed", {"error": str(exc)[:300]})
        conn.commit()
        return {
            "ok": not errors,
            "processed": processed,
            "created": created,
            "errors": errors[:25],
            "next_cursor": last_user_id,
            "has_more": len(rows) >= max(1, min(int(limit or 500), 5000)),
        }
    finally:
        conn.close()


def admin_state(conn: Any) -> dict[str, Any]:
    ensure_schema(conn)
    cur = conn.cursor()
    return {
        "metrics": {
            "growth_accounts": _count(cur, "pulse_growth_accounts"),
            "workspaces": _count(cur, "pulse_growth_workspaces"),
            "wallets": _count(cur, "pulse_growth_wallets"),
            "audience_profiles": _count(cur, "pulse_growth_audience_profiles"),
            "creator_profiles": _count(cur, "pulse_creator_growth_profiles"),
            "analytics_containers": _count(cur, "pulse_growth_analytics_containers"),
            "internal_api_keys": _count(cur, "pulse_growth_api_keys", "status='active'"),
            "risk_profiles": _count(cur, "pulse_growth_risk_profiles"),
            "provisioning_logs": _count(cur, "pulse_growth_provisioning_log"),
        },
        "modules": list(GROWTH_MODULES),
        "security": {
            "provider_secrets_visible": False,
            "raw_targeting_visible": False,
            "internal_api_keys_returned": False,
            "billing_default": "inactive",
        },
    }
