#!/usr/bin/env python3
"""Audit Pulse Premium promises against routes, tables, and safe scaffolds."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import premium_capability_engine  # noqa: E402


def ensure_audit_user():
    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO users (user_id, username, display_name, email, account_status, created_at, updated_at)
        VALUES (?, 'premium-audit', 'Premium Audit', 'premium-audit@example.com', 'active', ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET account_status='active', updated_at=excluded.updated_at
        """,
        (1, now, now),
    )
    conn.commit()
    conn.close()


def table_exists(name):
    conn = bot.db()
    cur = conn.cursor()
    try:
        ok = bot.migration_table_exists(cur, name)
    finally:
        conn.close()
    return ok


def route_exists(path):
    for rule in bot.webhook_app.url_map.iter_rules():
        if str(rule.rule) == path:
            return True
        if "<" in str(rule.rule) and path.startswith(str(rule.rule).split("<", 1)[0].rstrip("/")):
            return True
    return False


def service_exists(name):
    try:
        __import__(f"services.{name}", fromlist=["*"])
        return True
    except Exception:
        return False


def main():
    failures = []
    bot.init_db()
    ensure_audit_user()

    registry = premium_capability_engine.capability_registry()
    required_keys = {
        "premium_identity", "creator_ai", "advanced_analytics", "premium_studio",
        "discovery_boosts", "livestream_prestige", "creator_acceleration",
        "audience_intelligence", "retention_prediction", "profile_aura",
        "elite_themes", "premium_filters", "creator_luts", "replay_intelligence",
        "trust_visibility", "creator_energy", "cohosting_future", "elite_rooms_future",
    }
    missing_keys = sorted(required_keys - set(registry))
    if missing_keys:
        failures.append(f"Missing capability keys: {missing_keys}")

    health = premium_capability_engine.capability_health(table_exists, route_exists, service_exists)
    for key, item in health.items():
        if item["status"] in {"active", "scaffolded"} and (item["missing_tables"] or item["missing_routes"] or item["missing_services"]):
            failures.append(f"{key} missing {json.dumps({k: item[k] for k in ['missing_tables','missing_routes','missing_services']})}")

    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = 1

    page_checks = ["/pulse/premium", "/pulse/creator/dashboard", "/pulse/creator/analytics", "/admin/premium-command", "/admin/mobile-audit"]
    for path in page_checks:
        resp = client.get(path)
        if resp.status_code >= 500:
            failures.append(f"{path} returned {resp.status_code}")

    api_checks = [
        ("/api/pulse/premium/identity-effects", "get", None),
        ("/api/pulse/creator-ai/hook", "post", {"text": "Make a reel about safer creator growth", "topic": "creator growth"}),
        ("/api/pulse/creator-ai/caption", "post", {"text": "A lesson about trust-safe growth", "topic": "education"}),
        ("/api/pulse/creator-ai/virality", "post", {"text": "Why comments matter more than empty views?", "topic": "analytics"}),
        ("/api/pulse/creator-ai/live-title", "post", {"text": "Haiti crypto education night", "topic": "Haiti crypto"}),
    ]
    for path, method, payload in api_checks:
        resp = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path)
        data = resp.get_json(silent=True)
        if resp.status_code >= 500 or not isinstance(data, dict) or data.get("ok") is False:
            failures.append(f"{path} failed status={resp.status_code} body={data}")

    premium_page = client.get("/pulse/premium").get_data(as_text=True)
    for bad in ["href='#'", "javascript:void(0)"]:
        if bad in premium_page:
            failures.append(f"/pulse/premium contains dead target {bad}")

    if failures:
        print("Premium foundation audit FAILED")
        for item in failures:
            print(f"- {item}")
        return 1
    print("Premium foundation audit PASS")
    print(json.dumps({"capabilities": len(registry), "health_checked": len(health)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
