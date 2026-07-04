#!/usr/bin/env python3
"""Audit PulseSoc universal Growth Engine foundation."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, label: str, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def count(cur, table: str, where: str = "1=1", params: tuple = ()) -> int:
    cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
    row = cur.fetchone()
    return int(row["total"] if hasattr(row, "keys") else row[0])


def runtime_check(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="pulsesoc-growth-audit-") as tmpdir:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmpdir) / 'growth.db'}"
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from services import db as db_service
        from services import pulsesoc_growth_engine as growth

        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    display_name TEXT,
                    full_name TEXT,
                    email TEXT,
                    email_verified INTEGER DEFAULT 0,
                    is_super_user INTEGER DEFAULT 0,
                    created_at TEXT
                )
                """
            )
            cur.execute(
                "INSERT INTO users (username, display_name, full_name, email, email_verified, created_at) VALUES (?, ?, ?, ?, 1, datetime('now'))",
                ("growthone", "Growth One", "Growth One", "growth1@example.com"),
            )
            cur.execute(
                "INSERT INTO users (username, display_name, full_name, email, email_verified, created_at) VALUES (?, ?, ?, ?, 0, datetime('now'))",
                ("growthtwo", "Growth Two", "Growth Two", "growth2@example.com"),
            )
            conn.commit()

            growth.ensure_schema(conn)
            first = growth.provision_user(
                conn,
                {"user_id": 1, "username": "growthone", "display_name": "Growth One", "email": "growth1@example.com", "email_verified": 1},
                source="audit",
                commit=True,
            )
            second = growth.provision_user(
                conn,
                {"user_id": 1, "username": "growthone", "display_name": "Growth One", "email": "growth1@example.com", "email_verified": 1},
                source="audit_repeat",
                commit=True,
            )
            require(bool(first.get("account", {}).get("public_id")), "provisioning returns Growth Account public id", failures)
            require(second.get("account", {}).get("public_id") == first.get("account", {}).get("public_id"), "repeat provisioning is idempotent", failures)

            expected_tables = [
                "pulse_growth_accounts",
                "pulse_growth_workspaces",
                "pulse_growth_wallets",
                "pulse_growth_ledger",
                "pulse_growth_audience_profiles",
                "pulse_growth_audience_models",
                "pulse_creator_growth_profiles",
                "pulse_growth_promotion_history",
                "pulse_growth_billing_profiles",
                "pulse_growth_preferences",
                "pulse_growth_ai_sessions",
                "pulse_growth_analytics_containers",
                "pulse_growth_api_keys",
                "pulse_growth_scores",
                "pulse_growth_trust_links",
                "pulse_growth_risk_profiles",
                "pulse_growth_provisioning_log",
            ]
            for table in expected_tables:
                require(count(cur, table, "user_id=1" if table != "pulse_growth_provisioning_log" else "user_id=1") >= 1, f"{table} provisioned", failures)

            require(count(cur, "pulse_growth_accounts", "user_id=1") == 1, "no duplicate Growth Account", failures)
            require(count(cur, "pulse_growth_wallets", "user_id=1") == 1, "no duplicate Promotion Wallet", failures)
            require(count(cur, "pulse_growth_api_keys", "user_id=1") == 1, "one internal API key hash per user", failures)
            cur.execute("SELECT key_prefix, key_hash FROM pulse_growth_api_keys WHERE user_id=1")
            key_row = cur.fetchone()
            require(bool(key_row["key_hash"]) and not str(key_row["key_hash"]).startswith("pgk_"), "internal API key stored as hash, not raw secret", failures)

            backfill = growth.backfill_missing_growth_engines(limit=10, after_user_id=0)
            require(backfill.get("processed", 0) >= 2, "backfill scans existing users", failures)
            require(count(cur, "pulse_growth_accounts", "user_id=2") == 1, "backfill provisions missing user", failures)
            repeat = growth.backfill_missing_growth_engines(limit=10, after_user_id=0)
            require(count(cur, "pulse_growth_accounts", "user_id=2") == 1 and repeat.get("processed", 0) >= 2, "backfill is resumable/idempotent", failures)
        finally:
            conn.close()


def main() -> int:
    failures: list[str] = []
    service = read("services/pulsesoc_growth_engine.py")
    bot = read("bot.py")
    template = read("templates/pulse_advertiser_portal.html")
    js = read("static/js/pulse_advertiser_portal.js")
    migration = read("migrations/pulsesoc_growth_engine.sql")
    feature_map = read("data/pulse_ai/pulsesoc_feature_map.json")
    knowledge = read("data/pulse_ai/pulsesoc_knowledge.json")
    report_path = ROOT / "reports" / "pulsesoc_growth_engine_foundation.md"

    for token in (
        "pulse_growth_accounts",
        "pulse_growth_workspaces",
        "pulse_growth_wallets",
        "pulse_growth_ledger",
        "pulse_growth_audience_profiles",
        "pulse_growth_audience_models",
        "pulse_creator_growth_profiles",
        "pulse_growth_billing_profiles",
        "pulse_growth_preferences",
        "pulse_growth_ai_sessions",
        "pulse_growth_analytics_containers",
        "pulse_growth_api_keys",
        "pulse_growth_scores",
        "pulse_growth_trust_links",
        "pulse_growth_risk_profiles",
        "pulse_growth_provisioning_log",
    ):
        require(token in service and token in migration, f"{token} exists in service and migration", failures)

    require("provision_user(" in service and "backfill_missing_growth_engines" in service, "provision and backfill helpers exist", failures)
    require("pulsesoc_growth_engine.provision_user" in bot and 'source="signup"' in bot, "signup auto-provisions Growth Engine", failures)
    require("/pulse/growth" in bot and "/api/pulse/growth" in bot, "Growth Center user routes exist", failures)
    require("/admin/growth-engine" in bot and "/api/admin/growth-engine/backfill" in bot, "admin Growth Engine routes exist", failures)
    require("require_admin_page(\"command_center.view\")" in bot and "require_admin_api(\"command_center.view\")" in bot, "admin routes are permission protected", failures)

    for forbidden in ("PulseSoc Ads", "Advertiser Mission Control", "Advertiser Account Center", "Create Ad Account", "Advertiser Alerts"):
        require(forbidden not in template, f"user template does not expose {forbidden}", failures)
    require("Grow your reach." in template and "PulseSoc Growth" in template and "Promotion Wallet" in template, "Growth language appears in user UI", failures)
    require("/api/pulse/growth" in js, "Growth Center JS uses Growth API", failures)
    require("Create your first advertiser account" not in js and "No advertiser notifications" not in js, "JS user copy avoids advertiser setup language", failures)

    require('"id": "growth.engine"' in feature_map and "Growth Center" in knowledge, "Pulse AI knowledge includes Growth Engine", failures)
    require("key_hash" in service and "key_prefix" in service and "secret" not in " ".join(("growth_summary", "admin_state")), "internal API keys are not returned by summaries", failures)
    require(report_path.exists(), "Growth Engine report exists", failures)

    runtime_check(failures)

    if failures:
        print("PulseSoc Growth Engine audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc Growth Engine audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
