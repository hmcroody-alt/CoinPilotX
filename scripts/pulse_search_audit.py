#!/usr/bin/env python3
"""Audit Pulse desktop/mobile search activation and API grouping."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def seed_search_data() -> int:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    user_id = 960501
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
        (user_id, "pulse_search_creator", "Pulse Search Creator", "pulse-search@example.test", now),
    )
    cur.execute(
        "UPDATE users SET username=?, display_name=? WHERE user_id=?",
        ("pulse_search_creator", "Pulse Search Creator", user_id),
    )
    cur.execute(
        """
        INSERT INTO pulse_posts (user_id, public_player_id, post_type, body, title, tags_json, visibility, moderation_status, status, created_at, updated_at)
        VALUES (?, 'pulse_search_creator', 'text', 'Searchable wallet safety signal for Pulse search audit.', 'Wallet Safety Search Signal', ?, 'public', 'approved', 'published', ?, ?)
        """,
        (user_id, bot.json.dumps(["wallet", "safety", "search"]), now, now),
    )
    post_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO pulse_comments (post_id, user_id, body, moderation_status, created_at) VALUES (?, ?, 'Searchable comment about wallet safety.', 'approved', ?)",
        (post_id, user_id, now),
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO pulse_groups (id, owner_user_id, slug, name, description, group_type, category, status, member_count, created_at, updated_at)
        VALUES (960501, ?, 'wallet-safety-search-lab', 'Wallet Safety Search Lab', 'Searchable group for wallet safety education.', 'public', 'Safety', 'active', 42, ?, ?)
        """,
        (user_id, now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_reels (user_id, category, caption, video_url, status, created_at, updated_at)
        VALUES (?, 'Safety', 'Searchable wallet safety reel', 'https://cdn.coinpilotx.app/audit/search-reel.mp4', 'active', ?, ?)
        """,
        (user_id, now, now),
    )
    cur.execute(
        """
        INSERT INTO pulse_status (user_id, status_type, body, visibility, created_at, expires_at)
        VALUES (?, 'image', 'Searchable wallet safety media status', 'public', ?, ?)
        """,
        (user_id, now, now),
    )
    conn.commit()
    conn.close()
    return user_id


def main() -> None:
    bot.init_db()
    user_id = seed_search_data()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css = (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")
    for token in [
        "/api/pulse/search",
        "data-pulse-search-input",
        "pulseMobileSearch",
        "pulseSearchOverlay",
        "data-pulse-search-results",
        "runPulseSearch",
        "pulseRecentSearches",
    ]:
        expect(token in source, f"Pulse search UI/API contract present: {token}")
    for token in [
        ".pulse-search-overlay",
        ".pulse-search-panel",
        ".pulse-search-result",
        "@media (max-width: 900px)",
    ]:
        expect(token in css, f"Pulse search responsive style present: {token}")

    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    response = client.get("/api/pulse/search?q=wallet%20safety&limit=6")
    data = response.get_json(silent=True) or {}
    expect(response.status_code == 200 and data.get("ok") is True, "search API returns stable JSON", str(data))
    results = data.get("results") or {}
    for key in ["posts", "creators", "comments", "groups", "rooms", "reels", "statuses"]:
        expect(key in results and isinstance(results[key], list), f"search result group returned: {key}", str(data))
    expect(any("Wallet Safety" in (item.get("title") or "") for item in results.get("posts") or []), "post search returns seeded result", str(results.get("posts")))
    expect(any("Search Lab" in (item.get("title") or "") for item in results.get("groups") or []), "group search returns seeded result", str(results.get("groups")))
    expect(any((item.get("type") or "") == "reel" for item in results.get("reels") or []), "reel search returns structured result", str(results.get("reels")))
    expect(any((item.get("type") or "") == "status" for item in results.get("statuses") or []), "status/media search returns structured result", str(results.get("statuses")))

    empty = client.get("/api/pulse/search?q=zzzz-no-pulse-result-zzzz&limit=4").get_json(silent=True) or {}
    expect(empty.get("ok") is True and isinstance(empty.get("total"), int), "empty search returns clean JSON", str(empty))
    print("pulse search audit ok")


if __name__ == "__main__":
    main()
