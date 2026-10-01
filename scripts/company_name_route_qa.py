#!/usr/bin/env python3
"""Route QA for the public company-name correction."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OLD_CORE = "Coin" + "PilotXAI"
OLD_TOKENS = (
    OLD_CORE + " Inc.",
    OLD_CORE,
    "Coin" + "Pilot XAI",
    "Coin" + "pilotxai",
)
LOWERCASE_TECHNICAL_ALLOWLIST = (
    "coin" + "pilotxai.app",
    "coin" + "pilotxai@gmail.com",
    "coin" + "pilotxai_session_id",
    "coin" + "pilotxai_last_visit_day",
    "coin" + "pilotxai-inc",
    "coin" + "pilotxai-alert",
    "coin" + "pilotxai-og.png",
    "og-coin" + "pilotxai.png",
    "coin" + "pilotxai-share-card.svg",
)

DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 Chrome/126 Safari/537.36"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


def expect(condition: bool, label: str, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"ok - {label}")


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def seed_user(bot, user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    cols = table_columns(cur, "users")
    values = {
        "user_id": user_id,
        "username": "brand_route_qa",
        "display_name": "Brand Route QA",
        "full_name": "Brand Route QA",
        "email": "brand-route-qa@example.com",
        "password_hash": "x",
        "signup_time": now,
        "created_at": now,
        "account_status": "active",
        "email_verified": 1,
    }
    data = {key: value for key, value in values.items() if key in cols}
    cur.execute(f"INSERT INTO users ({', '.join(data)}) VALUES ({', '.join(['?'] * len(data))})", tuple(data.values()))
    conn.commit()
    conn.close()


def cleanup_user(bot, user_id: int) -> None:
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def line_has_allowed_lowercase_old(line: str) -> bool:
    return any(token in line for token in LOWERCASE_TECHNICAL_ALLOWLIST)


def assert_no_old_public_text(path: str, html: str) -> None:
    for token in OLD_TOKENS:
        if token == "coin" + "pilotxai":
            for line in html.splitlines():
                if token in line and not line_has_allowed_lowercase_old(line):
                    raise AssertionError(f"{path} contains old lowercase public company text")
            continue
        if token in html:
            raise AssertionError(f"{path} contains old public company text")


def main() -> int:
    import bot  # noqa: WPS433

    user_id = 9926072402
    bot.init_db()
    seed_user(bot, user_id)
    client = bot.webhook_app.test_client()
    routes = [
        ("/pulse", True, "Home"),
        ("/pulse/reels", True, "Reels"),
        ("/pulse/premium", True, "Premium"),
        ("/billing/portal", True, "Billing"),
        ("/pulse/notifications", True, "Notifications"),
        ("/login", False, "Login"),
        ("/signup", False, "Register"),
        ("/pulse/profile", True, "Profile"),
        ("/pulse/settings", True, "Settings"),
        ("/admin/login", False, "Admin Login"),
        ("/admin", False, "Admin"),
    ]
    try:
        for ua_name, user_agent in (("desktop", DESKTOP_UA), ("mobile", MOBILE_UA)):
            for path, authenticated, label in routes:
                with client.session_transaction() as sess:
                    if authenticated:
                        sess["account_user_id"] = user_id
                        sess["user_id"] = user_id
                    else:
                        sess.pop("account_user_id", None)
                        sess.pop("user_id", None)
                response = client.get(path, headers={"User-Agent": user_agent})
                expect(response.status_code in {200, 302, 303, 403}, f"{ua_name} {label} route resolves", f"{path} -> {response.status_code}")
                html = response.get_data(as_text=True)
                assert_no_old_public_text(f"{ua_name} {path}", html)
        print("company name route QA ok")
        return 0
    finally:
        cleanup_user(bot, user_id)


if __name__ == "__main__":
    raise SystemExit(main())
