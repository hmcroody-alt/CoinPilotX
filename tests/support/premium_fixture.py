"""Give a test user genuine Premium in a suite's own throwaway database.

Why this exists
---------------
``alert_engine.evaluate_alert_rule`` gained a delivery-time premium hard-lock:
before a rule is evaluated at all, the owner must hold
``CAP_CRYPTO_ADVANCED_ALERTS``. That gate applies to *every* rule type, and it
fails closed — an unknown user, a missing table, or any resolution error all
resolve to "not premium", and the rule is skipped rather than triggered.

That is correct production behaviour. It is also invisible to a test suite that
builds its own three-column ``users`` table, because the gate cannot distinguish
"this account's subscription lapsed" from "this database has no premium schema".
Both simply stop delivering. Seven alert suites were written before the gate
existed and assert on triggering, so without a premium owner they now assert
against a gate they were never trying to exercise.

Why a real grant rather than a patch
------------------------------------
The obvious shortcut is to monkeypatch ``_advanced_alerts_capability`` to return
True. That would turn these suites green while removing the only place the alert
engine's real entitlement resolution is exercised end to end — and this gate is
fail-closed, so the failure mode it protects against (silently delivering to a
lapsed account) is exactly the one a patched gate would stop detecting.

So this writes a real ``premium_access`` entitlement row and lets the production
chain resolve it: ``has_crypto_capability`` -> entitlements facade -> legacy
``is_premium_user`` -> ``has_entitlement``. If that chain breaks, these suites
break with it, which is the point.

Usage: call :func:`grant_premium` after the suite has created its user rows.
"""

from __future__ import annotations

from typing import Iterable

# The columns ``_is_premium_user_raw`` selects from ``users``. A suite that
# built a minimal table is missing most of them, and a missing column raises
# rather than reading as NULL.
_USER_PREMIUM_COLUMNS = (
    ("premium_status", "TEXT"),
    ("subscription_status", "TEXT"),
    ("lifetime_premium", "INTEGER DEFAULT 0"),
    ("premium_glow_manual_grant", "INTEGER DEFAULT 0"),
    ("trial_end_date", "TEXT"),
    ("pro_expires_at", "TEXT"),
)

# ``has_entitlement`` reads ``user_entitlements`` first and falls back to
# ``premium_entitlements``. Both must exist or the lookup raises and the facade
# logs a failure and denies.
#
# The two tables spell the expiry column differently — ``user_entitlements``
# uses ``expires_at`` (aliased to ends_at in the query), ``premium_entitlements``
# uses ``ends_at``. Mirroring that here rather than picking one keeps the
# fixture honest against the real read path.
_ENTITLEMENT_TABLES = {
    "user_entitlements": "expires_at",
    "premium_entitlements": "ends_at",
}

_ENTITLEMENT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    entitlement_key TEXT,
    status TEXT DEFAULT 'active',
    source TEXT,
    starts_at TEXT,
    {expiry} TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""


def ensure_premium_schema(cur) -> None:
    """Create the tables/columns the premium read path needs. Idempotent."""
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, email TEXT)")
    for table, expiry in _ENTITLEMENT_TABLES.items():
        cur.execute(_ENTITLEMENT_TABLE_SQL.format(table=table, expiry=expiry))
        # A suite whose bootstrap already made one of these tables may have
        # built it without the expiry column the read path selects.
        columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        for column in ("entitlement_key", "status", "starts_at", expiry, "source"):
            if column not in columns:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    existing = {row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()}
    for column, decl in _USER_PREMIUM_COLUMNS:
        if column not in existing:
            # Only reachable for a suite-local table; the real schema already
            # has every one of these.
            cur.execute(f"ALTER TABLE users ADD COLUMN {column} {decl}")


def grant_premium(user_ids: Iterable[int] | int, *, key: str = "premium_access") -> None:
    """Make ``user_ids`` genuinely premium in the currently configured database.

    Writes an open-ended active entitlement (no ``starts_at``/``ends_at``, which
    ``_active_window`` reads as "always in force") and sets the legacy
    ``premium_status`` column, so the account resolves as premium through either
    the canonical or the legacy branch of the facade regardless of how the
    ``BUSINESS_OS_ENTITLEMENTS`` flag is set for the run.
    """
    if isinstance(user_ids, int):
        user_ids = [user_ids]

    from services import db as db_service

    conn = db_service.connect()
    try:
        cur = conn.cursor()
        ensure_premium_schema(cur)
        for uid in user_ids:
            uid = int(uid)
            cur.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,)
            )
            cur.execute(
                "UPDATE users SET premium_status='active', subscription_status='active' "
                "WHERE user_id=?",
                (uid,),
            )
            for table in _ENTITLEMENT_TABLES:
                cur.execute(
                    f"DELETE FROM {table} WHERE user_id=? AND entitlement_key=?",
                    (uid, key),
                )
                cur.execute(
                    f"INSERT INTO {table} (user_id, entitlement_key, status, source) "
                    "VALUES (?, ?, 'active', 'test-fixture')",
                    (uid, key),
                )
        conn.commit()
    finally:
        conn.close()
