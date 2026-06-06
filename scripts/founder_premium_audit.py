#!/usr/bin/env python3
"""Audit Founder Premium plans, entitlements, and safe activation gates."""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402
from services import premium_entitlement_service as founder_service  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def table_columns(cur, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


def ensure_test_user(email: str) -> int:
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE email=? LIMIT 1", (email,))
    row = cur.fetchone()
    if row:
        conn.close()
        return int(dict(row).get("user_id") or 0)
    columns = table_columns(cur, "users")
    now = datetime.utcnow().isoformat(timespec="seconds")
    payload = {
        "email": email,
        "username": "founder_audit_user",
        "display_name": "Founder Audit User",
        "password_hash": "audit-only",
        "created_at": now,
        "updated_at": now,
    }
    usable = {key: value for key, value in payload.items() if key in columns}
    cur.execute(
        f"INSERT INTO users ({', '.join(usable)}) VALUES ({', '.join(['?'] * len(usable))})",
        tuple(usable.values()),
    )
    conn.commit()
    user_id = int(cur.lastrowid or 0)
    conn.close()
    return user_id


def main() -> None:
    bot.init_db()
    founder_service.ensure_founder_schema()

    plans = founder_service.plan_definitions()
    assert_true(plans["free"]["price_cents"] == 0, "Free plan must be $0.")
    assert_true(plans["founder_premium"]["price_cents"] == 499, "Founder Premium must be $4.99.")
    assert_true(plans["founder_premium"]["regular_price_cents"] == 999, "Founder regular value must be $9.99.")
    assert_true(plans["premium_plus"]["status"] == "coming_soon", "Premium Plus must remain coming soon.")

    conn = db_service.connect()
    cur = conn.cursor()
    for table in [
        "subscription_plans",
        "user_subscriptions",
        "user_entitlements",
        "founder_memberships",
        "premium_badges",
        "founder_wall_entries",
    ]:
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
        assert_true(bool(cur.fetchone()), f"{table} table missing.")
    conn.close()

    uid = ensure_test_user("founder.audit@example.test")
    founder_service.revoke_premium_access(uid, 0, "audit_reset")
    assert_true(not founder_service.is_founder_member(uid), "Audit user should not start as Founder.")

    grant = founder_service.grant_founder_membership(uid, 0, "audit_grant")
    number = int(grant.get("founder_number") or 0)
    assert_true(number > 0, "Founder number was not assigned.")
    assert_true(founder_service.is_founder_member(uid), "Founder member check failed after grant.")
    entitlements = founder_service.get_user_entitlements(uid)
    for key in founder_service.FOUNDER_ENTITLEMENTS:
        assert_true(entitlements.get(key), f"Missing active Founder entitlement: {key}")

    second_grant = founder_service.grant_founder_membership(uid, 0, "audit_second_grant")
    assert_true(int(second_grant.get("founder_number") or 0) == number, "Founder number changed on second grant.")

    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT badge_key FROM pulse_user_badges WHERE user_id=? AND badge_key='founder'", (uid,))
    assert_true(bool(cur.fetchone()), "Founder badge was not linked to Pulse identity badges.")
    conn.close()

    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert_true("Founder Access Available — Checkout Coming Online" in source, "Founder checkout CTA missing.")
    assert_true("grant_founder" in source, "Admin Founder grant action missing.")
    assert_true("self_founder" not in source, "Self Founder grant path should not exist.")

    founder_service.revoke_premium_access(uid, 0, "audit_revoke")
    assert_true(not founder_service.is_founder_member(uid), "Founder member check stayed active after revoke.")
    assert_true(not founder_service.get_user_entitlements(uid).get("founder_access"), "Founder access entitlement stayed active after revoke.")

    print("FOUNDER_PREMIUM_AUDIT_PASS")


if __name__ == "__main__":
    main()
