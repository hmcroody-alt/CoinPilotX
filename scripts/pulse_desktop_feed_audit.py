#!/usr/bin/env python3
"""Audit the adaptive desktop Pulse feed shell and mobile fallback structure."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user():
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (940001,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
            (940001, "pulse_desktop_audit", "Pulse Desktop Audit", "pulse-desktop-audit@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
        )
    conn.commit()
    conn.close()
    return 940001


def main():
    bot.init_db()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.get("/pulse")
    html = response.get_data(as_text=True)
    expect(response.status_code == 200, "Pulse page responds", html[:300])
    for token in [
        "pulse_desktop_feed.css",
        "pulse-desktop-topbar",
        "pulse-desktop-left",
        "pulse-desktop-center",
        "pulse-desktop-right",
        "pulse-status-rail",
        "mobile-bottom-nav",
        "pulse-media-wrap",
    ]:
        expect(token in html, f"desktop/mobile shell contains {token}")
    css = (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")
    for token in ["grid-template-columns", "--pulse-feed-column", "position: sticky", "@media (max-width: 1023px)"]:
        expect(token in css, f"desktop CSS contains {token}")
    print("pulse desktop feed audit ok")


if __name__ == "__main__":
    main()

