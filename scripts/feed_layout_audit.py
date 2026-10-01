#!/usr/bin/env python3
"""Audit Pulse feed layout proportions on the rendered homepage."""

from __future__ import annotations

from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def ensure_user() -> int:
    user_id = 986000 + int(time.time()) % 10000
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            user_id,
            f"feed_layout_density_{user_id}",
            "Feed Layout Density Audit",
            f"feed-layout-density-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def main():
    bot.init_db()
    css = (ROOT / "static" / "css" / "pulse_desktop_feed.css").read_text()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.get("/pulse")
    html = response.get_data(as_text=True)
    require(response.status_code == 200, "Pulse homepage renders")
    require("pulse-desktop-layout" in html and "pulse-desktop-center" in html, "desktop feed shell renders")
    require('id="pulseComposer"' in html and "Pulse Composer" in html, "composer exists at feed start")
    require('class="feed"' in html and 'id="feed"' in html, "feed container exists below composer")
    require("data-live-now-hub" not in html, "large Live Now hub is removed from the middle of Home")
    require("Choose File" not in re.sub(r"<input[^>]+type=['\"]file['\"][^>]*>", "", html, flags=re.I), "raw browser upload controls stay hidden")
    require("pulse-action-card-grid" in html and "repeat(8, minmax(0, 1fr))" in (ROOT / "static" / "css" / "pulse_composer_premium.css").read_text(), "composer tools use compact sci-fi action rail")
    require("minmax(170px, var(--pulse-left-rail))" in css and "minmax(0, var(--pulse-feed-column))" in css, "desktop grid prioritizes flexible wide center feed")
    require(".pulse-desktop-center > .layout" in css and ".pulse-desktop-center > .layout > aside.side" in css, "legacy inner side column is removed from desktop center feed")
    require("post_type='live'" in (ROOT / "services" / "live_feed_service.py").read_text(), "active live sessions surface as feed posts instead of a disruptive hub")
    require(".pulse-desktop-center .feed" in css and "gap: 11px !important" in css, "rendered feed spacing is compact")
    print("feed layout audit ok")


if __name__ == "__main__":
    main()
