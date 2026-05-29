#!/usr/bin/env python3
"""Audit that UNDX lives only inside Pulse Premium."""

from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


AUDIT_USER_ID = 1


def require(condition, message, detail=""):
    if not condition:
        raise AssertionError(f"{message}: {detail}")
    print(f"ok - {message}")


def ensure_audit_user():
    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO users (user_id, username, display_name, email, account_status, created_at, updated_at)
        VALUES (?, 'undx-audit', 'UNDX Audit', 'undx-audit@example.com', 'active', ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET account_status='active', updated_at=excluded.updated_at
        """,
        (AUDIT_USER_ID, now, now),
    )
    conn.commit()
    conn.close()


def login_test_user(client):
    with client.session_transaction() as sess:
        sess["account_user_id"] = AUDIT_USER_ID


def page_html(client, path):
    response = client.get(path)
    require(response.status_code == 200, f"{path} returns 200", response.status_code)
    return response.get_data(as_text=True)


def main():
    bot.init_db()
    ensure_audit_user()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    feed_css = (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")

    require("pulse_undx_core_homepage_html" not in source, "UNDX feed helper removed")
    require("__UNDX_CORE__" not in source, "Pulse feed UNDX placeholder removed")
    require(".replace(\"__UNDX_CORE__\"" not in source, "Pulse feed no longer renders UNDX")
    require("pulse-undx-core" not in feed_css, "feed stylesheet no longer contains UNDX card styles")

    client = bot.webhook_app.test_client()
    login_test_user(client)
    feed_html = page_html(client, "/pulse")
    premium_html = page_html(client, "/pulse/premium")
    undx_html = page_html(client, "/pulse/premium/undx")

    require("data-undx-premium-entry" not in feed_html, "UNDX entry absent from Pulse feed")
    require("UNDX Core: Build Beyond the Known" not in feed_html, "old UNDX feed headline absent")

    for token in [
        "data-undx-premium-entry",
        "Enter UNDX",
        "Open the Unknown Destination intelligence layer.",
        "/pulse/premium/undx",
    ]:
        require(token in premium_html, f"Pulse Premium contains {token}")

    for token in [
        "data-undx-core-page",
        "UNDX Core",
        "Unknown Destination X — the premium intelligence layer powering the future of CoinPilotXAI.",
        "Premium Intelligence Layer",
    ]:
        require(token in undx_html, f"UNDX page contains {token}")

    print("undx premium placement audit ok")


if __name__ == "__main__":
    main()
