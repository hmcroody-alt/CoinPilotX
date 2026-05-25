#!/usr/bin/env python3
"""Audit Pulse homepage upload controls and modern high-density feed layout contracts."""

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
    user_id = 974000 + int(time.time()) % 10000
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
            f"feed_layout_{user_id}",
            "Feed Layout Audit",
            f"feed-layout-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text()
    desktop_css = (ROOT / "static" / "css" / "pulse_desktop_feed.css").read_text()
    picker_js = (ROOT / "static" / "js" / "pulse_media_picker.js").read_text()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.get("/pulse")
    html = response.get_data(as_text=True)
    require(response.status_code == 200, "Pulse homepage renders")

    visible_copy = re.sub(r"<input[^>]+type=['\"]file['\"][^>]*>", "", html, flags=re.I)
    require("Choose File" not in visible_copy and "Choose Files" not in visible_copy, "rendered Pulse homepage exposes no native Choose File copy")
    require("pulse_media_picker.js" in html and "window.PulseMediaPicker" in picker_js, "PulseMediaPicker loads on homepage")
    require("input[type=file],.pulse-native-file-input" in source and "opacity:0!important" in source, "native file inputs are globally hidden behind custom controls")
    require('data-pulse-media-picker="composer"' in html and "data-pulse-media-trigger" in html and 'data-expand-composer="pulseComposer"' in html, "composer uses unified media picker trigger")
    for label in ["Media", "Reel", "Live", "Music", "AI", "Audience", "Publish"]:
        require(label in html, f"composer action exists: {label}")
    require("smart-compose-icon" not in html[html.find("pulseComposer") : html.find("pulseComposer") + 1200], "composer no longer has duplicate tiny upload icon")

    feed_widths = [int(v) for v in re.findall(r"--pulse-feed-column:\s*clamp\((\d+)px", desktop_css)]
    text_widths = [int(v) for v in re.findall(r"--pulse-text-column:\s*(\d+)px", desktop_css)]
    require(max(feed_widths or [0]) >= 1360 and max(text_widths or [0]) >= 1280, "desktop center feed target is wide enough for modern social cards")
    require("minmax(0, var(--pulse-feed-column))" in desktop_css, "desktop grid lets the feed dominate without horizontal scroll")
    require(".pulse-desktop-center > .layout" in desktop_css and ".pulse-desktop-center > .layout > aside.side" in desktop_css, "desktop center feed is not squeezed by legacy inner side rail")
    require(".composer.card" in desktop_css and "min-height: 72px" in desktop_css, "composer is compact while staying readable")
    require(".post.card" in desktop_css and "padding: clamp(14px" in desktop_css, "post cards use compact vertical padding")
    require("contain-intrinsic-size: 430px" in desktop_css, "post cards reserve compact scroll height")
    require("-webkit-line-clamp: 7" in desktop_css, "long post text is clamped for scan-friendly density")
    require("max-height: min(62vh, 620px)" in desktop_css, "feed media is wide without becoming overly tall")
    require(".composer-primary-actions" in desktop_css and "repeat(6, minmax(92px" in desktop_css, "desktop composer action row stays single-line")
    require("@media (max-width: 1023px)" in desktop_css and ".composer-primary-actions" in desktop_css and "overflow-x: auto" in desktop_css, "mobile composer action row stays responsive")

    require("data-live-now-hub" in html and "Realtime Pulse Discovery" in html, "Live Now card renders on homepage")
    live = client.get("/api/pulse/live-now")
    live_payload = live.get_json() or {}
    require(live.status_code == 200 and live_payload.get("ok") and isinstance(live_payload.get("items"), list), "Live Now card has real data endpoint")
    require("No creators live yet. Start the first broadcast." in html or "Pulse Live is ready" in html or "Watch Live" in html, "Live Now has useful empty/live state")
    print("pulse feed layout audit ok")


if __name__ == "__main__":
    main()
