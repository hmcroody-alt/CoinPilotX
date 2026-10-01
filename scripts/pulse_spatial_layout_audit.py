#!/usr/bin/env python3
"""Audit Pulse adaptive desktop spatial layout and mobile fallback."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user() -> int:
    conn = bot.db()
    cur = conn.cursor()
    user_id = 940041
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
    if not cur.fetchone():
        cur.execute(
            """
            INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                user_id,
                "pulse_spatial_audit",
                "Pulse Spatial Audit",
                "pulse-spatial-audit@example.test",
                bot.datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
    conn.commit()
    conn.close()
    return user_id


def route_ok(client, path: str) -> None:
    response = client.get(path)
    body = response.get_data(as_text=True)
    expect(response.status_code < 500, f"{path} route does not 500", body[:240])


def main() -> None:
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
        "pulse-desktop-layout",
        "pulse-desktop-left",
        "pulse-desktop-center",
        "pulse-desktop-right",
        "data-desktop-intel-card",
        "data-desktop-trending",
        "data-desktop-spaces",
        "data-desktop-live-activity",
        "data-desktop-educators",
        "data-post-type",
        "data-media-count",
        "data-engagement-heat",
        "data-open-media-lightbox",
        "pulse-media-lightbox",
        "desktopFeedInsightHtml",
        "mobile-bottom-nav",
    ]:
        expect(token in html, f"Pulse HTML contains {token}")

    css_path = ROOT / "static/css/pulse_desktop_feed.css"
    css = css_path.read_text(encoding="utf-8")
    for token in [
        "--pulse-left-rail",
        "--pulse-right-rail",
        "--pulse-feed-column",
        "--pulse-cinematic-column",
        "grid-template-columns",
        "clamp(",
        "@media (min-width: 1500px)",
        "@media (min-width: 1024px) and (max-width: 1279px)",
        "@media (max-width: 1023px)",
        ".post.has-media",
        ".post.is-text",
        ".post.type-scam-report",
        ".post.is-discussion",
        ".post.is-trending",
        ".desktop-feed-intel-card",
        ".desktop-signal-pill",
        ".desktop-intel-row.is-live",
        ".pulse-media-lightbox",
        ".reaction-pill:hover",
        "content-visibility",
        "overflow-x",
    ]:
        expect(token in css, f"Desktop spatial CSS contains {token}")

    route_ok(client, "/pulse")
    route_ok(client, "/pulse/reels")
    route_ok(client, "/pulse/messages")
    print("pulse spatial layout audit ok")


if __name__ == "__main__":
    main()
