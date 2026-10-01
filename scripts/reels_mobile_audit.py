#!/usr/bin/env python3
"""Audit Pulse Reels immersive mobile/desktop shell and adaptive media hooks."""

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
    conn = bot.db(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (940005,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
            (940005, "reels_mobile_audit", "Reels Mobile Audit", "reels-mobile-audit@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
        )
    conn.commit(); conn.close()
    return 940005


def main():
    bot.init_db()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    response = client.get("/pulse/reels?tab=for_you")
    html = response.get_data(as_text=True)
    expect(response.status_code == 200, "Reels page responds", html[:300])
    for token in [
        "pulse_reels_experience.css",
        "reels-shell",
        "reels-immersive",
        "reel-actions",
        "reel-caption",
        "data-reels-fullscreen",
        "data-reels-topbar",
        "playsinline",
        "preload=\"metadata\"",
    ]:
        expect(token in html, f"Reels HTML contains {token}")
    expect("<a class='icon-btn reel-search-button'" not in html, "Reels viewer search button is removed")
    for token in ["data-reels-title>Reels", "['following','Following']", "['for_you','For You']", "['trending','Trending']"]:
        expect(token in html, f"Reels mobile top nav contains {token}")
    expect("reelsLoadVersion" in html, "Stale Reels lane responses cannot overwrite the active tab")
    for token in ["Adaptive " + "playback", "poster " + "first", "HLS enabled", "CDN ready", "ffmpeg processing", "Mux playback", "Media diagnostics"]:
        expect(token not in html, f"internal Reel text is hidden: {token}")
    api = client.get("/api/pulse/reels/feed?tab=for_you&limit=3")
    payload = api.get_json() or {}
    expect(api.status_code == 200 and payload.get("ok") is True, "Reels feed API returns ok", api.get_data(as_text=True)[:300])
    css = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8")
    for token in ["100dvh", "object-fit:cover", "data-orientation", "prefers-reduced-motion", "--reels-tabs-height"]:
        expect(token in css or token in html, f"Reels adaptive CSS contains {token}")
    print("reels mobile audit ok")


if __name__ == "__main__":
    main()
