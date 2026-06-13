#!/usr/bin/env python3
"""Audit the desktop PulseSoc Videos page wiring and API contract."""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def extract_function(source: str, name: str) -> str:
    match = re.search(rf"^def {re.escape(name)}\([^)]*\):\n", source, re.MULTILINE)
    require(match is not None, f"{name} function missing")
    start = match.start()
    next_match = re.search(r"^def |\n@webhook_app\.route", source[match.end() :], re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(source)
    return source[start:end]


def run_static_audit() -> list[str]:
    source = BOT.read_text()
    page = extract_function(source, "pulse_videos_page")
    api = extract_function(source, "api_pulse_videos")
    checks = []

    required_page_markers = [
        "data-videos-desktop-page",
        "featuredVideoHero",
        "renderFeaturedVideo",
        "renderVideoSidebars",
        "videoFollowButton",
        "data-video-category",
        "data-filter-category",
        "data-filter-sort",
        "data-video-view",
        "data-video-search-input",
        "body:has([data-videos-desktop-page]) .wrap",
        "/api/pulse/follows/toggle",
    ]
    for marker in required_page_markers:
        require(marker in page, f"missing Videos page marker: {marker}")
        checks.append(f"page marker present: {marker}")

    for forbidden in ["v.title||'PulseSoc Video'", "v.title||\"PulseSoc Video\"", "Untitled Video"]:
        require(forbidden not in page, f"placeholder fallback still present in Videos page: {forbidden}")
    checks.append("placeholder title fallbacks removed from Videos page")

    required_api_markers = [
        "category =",
        "sort =",
        "query =",
        "upload_date =",
        "duration_filter =",
        "safe_only =",
        "owner_follower_count",
        "viewer_follows_owner",
        "moderation_status",
    ]
    for marker in required_api_markers:
        require(marker in api, f"missing Videos API marker: {marker}")
        checks.append(f"api marker present: {marker}")

    schema_section = source[source.find("CREATE TABLE IF NOT EXISTS pulse_videos") : source.find("CREATE TABLE IF NOT EXISTS pulse_video_views")]
    require("moderation_status TEXT DEFAULT 'approved'" in schema_section, "pulse_videos moderation_status migration missing")
    checks.append("pulse_videos moderation_status migration present")
    return checks


def run_api_audit() -> list[str]:
    sys.path.insert(0, str(ROOT))
    import bot  # noqa: PLC0415

    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(pulse_videos)")
    columns = {row["name"] for row in cur.fetchall()}
    require("moderation_status" in columns, "pulse_videos.moderation_status missing after init_db")
    cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1")
    user = cur.fetchone()
    conn.close()
    require(user is not None, "no user available for authenticated Videos API audit")

    with bot.webhook_app.test_client() as client:
        with client.session_transaction() as session:
            session["account_user_id"] = int(user["user_id"])
        response = client.get(
            "/api/pulse/videos?tab=all&limit=8&sort=recent&safe=1",
            headers={"Accept": "application/json"},
        )
        require(response.status_code == 200, f"Videos API returned {response.status_code}")
        payload = response.get_json() or {}
        require(payload.get("ok") is True, "Videos API did not return ok=true")
        require(isinstance(payload.get("videos"), list), "Videos API videos is not a list")
        if payload["videos"]:
            sample = payload["videos"][0]
            for field in ["id", "title", "permalink", "owner_name", "owner_follower_count", "viewer_follows_owner"]:
                require(field in sample, f"Videos API sample missing {field}")
    return [
        "init_db adds pulse_videos.moderation_status",
        "authenticated /api/pulse/videos returns 200",
        "Videos API includes creator and follow metadata",
    ]


def main() -> int:
    checks = run_static_audit() + run_api_audit()
    print("PulseSoc desktop Videos page audit PASS")
    for check in checks:
        print(f"- {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
