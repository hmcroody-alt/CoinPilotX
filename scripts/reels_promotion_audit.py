#!/usr/bin/env python3
"""Audit PulseSoc Reels and universal promotion wiring.

The checks use authenticated Flask test clients and isolated audit rows. They
verify owner-only promotion controls, backend eligibility, safe unavailable
states, and the non-owner restrictions requested for Reels promotion work.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///coinpilotx.db")

import bot  # noqa: E402
from services import pulsesoc_promotions  # noqa: E402


REPORT = ROOT / "reports" / "reels_promotion_audit.json"


class Audit:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, condition: bool, name: str, details: str = "") -> None:
        self.results.append({"name": name, "passed": bool(condition), "details": details})
        print(("ok - " if condition else "FAIL - ") + name + (f": {details}" if details else ""))
        if not condition:
            raise AssertionError(f"{name}: {details}")

    def write(self) -> None:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({"ok": True, "checks": self.results}, indent=2), encoding="utf-8")


def client_for(user_id: int):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def route_exists(path: str, method: str = "GET") -> bool:
    for rule in bot.webhook_app.url_map.iter_rules():
        if rule.rule == path and method.upper() in rule.methods:
            return True
    return False


def seed() -> dict:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    suffix = int(time.time() * 1000) % 1_000_000_000
    owner_id = 9_880_000_000 + suffix
    viewer_id = owner_id + 1
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    for uid, name in ((owner_id, "reels_promo_owner"), (viewer_id, "reels_promo_viewer")):
        cur.execute(
            """
            INSERT OR IGNORE INTO users
            (user_id, username, display_name, email, signup_time, onboarding_complete, account_status)
            VALUES (?, ?, ?, ?, ?, 1, 'active')
            """,
            (uid, f"{name}_{suffix}", f"{name} {suffix}", f"{name}-{suffix}@example.test", now),
        )
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, media_type, media_url, public_url, mime_type, moderation_status, processing_status, created_at, updated_at)
        VALUES (?, 'video', '/static/audit/reel.mp4', '/static/audit/reel.mp4', 'video/mp4', 'approved', 'ready', ?, ?)
        """,
        (owner_id, now, now),
    )
    media_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_posts
        (user_id, post_type, title, body, media_ids_json, tags_json, visibility, moderation_status, status, created_at, updated_at)
        VALUES (?, 'video', 'Audit Reel', 'Audit reel caption', ?, '["audit"]', 'public', 'approved', 'published', ?, ?)
        """,
        (owner_id, json.dumps([media_id]), now, now),
    )
    post_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_reels
        (post_id, user_id, category, caption, video_url, poster_url, ai_tags_json, safety_score, educational_value, reel_score, processing_status, transcoding_status, moderation_status, status, created_at, updated_at)
        VALUES (?, ?, 'Education', 'Audit reel caption', '/static/audit/reel.mp4', '', '["audit"]', 95, 80, 40, 'ready', 'ready', 'approved', 'active', ?, ?)
        """,
        (post_id, owner_id, now, now),
    )
    reel_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_videos
        (owner_user_id, source_type, source_id, media_id, title, description, media_url, playback_url, visibility, moderation_status, status, processing_status, created_at, updated_at)
        VALUES (?, 'feed_video', ?, ?, 'Audit Video', 'Audit video description', '/static/audit/video.mp4', '/static/audit/video.mp4', 'public', 'approved', 'active', 'ready', ?, ?)
        """,
        (owner_id, str(post_id), media_id, now, now),
    )
    video_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO marketplace_listings
        (seller_user_id, title, description, category, price_label, status, approval_status, safety_score, created_at, updated_at)
        VALUES (?, 'Audit Listing', 'Audit listing description', 'Education', 'Request access', 'active', 'review_ready', 0, ?, ?)
        """,
        (owner_id, now, now),
    )
    listing_id = int(cur.lastrowid)
    conn.commit()
    pulsesoc_promotions.ensure_tables(conn)
    conn.close()
    return {
        "owner_id": owner_id,
        "viewer_id": viewer_id,
        "media_id": media_id,
        "post_id": post_id,
        "reel_id": reel_id,
        "video_id": video_id,
        "listing_id": listing_id,
    }


def assert_json(response, name: str) -> dict:
    payload = response.get_json(silent=True) or {}
    if not payload:
        raise AssertionError(f"{name}: empty JSON response {response.status_code}")
    return payload


def main() -> None:
    audit = Audit()
    bot.init_db()
    data = seed()
    owner = client_for(data["owner_id"])
    viewer = client_for(data["viewer_id"])

    for path, method in (
        ("/pulse/reels", "GET"),
        ("/api/reels", "POST"),
        ("/api/reels/<int:reel_id>/like", "POST"),
        ("/api/reels/<int:reel_id>/comments", "GET"),
        ("/api/reels/<int:reel_id>/comments", "POST"),
        ("/api/reels/<int:reel_id>", "PATCH"),
        ("/api/reels/<int:reel_id>", "DELETE"),
        ("/api/reels/<int:reel_id>/save", "POST"),
        ("/api/reels/<int:reel_id>/not-interested", "POST"),
        ("/api/reels/<int:reel_id>/follow-creator", "POST"),
        ("/api/reels/<int:reel_id>/audio", "POST"),
        ("/api/promotions/eligibility", "GET"),
        ("/api/promotions", "GET"),
        ("/api/promotions", "POST"),
        ("/api/reels/<int:reel_id>/promotions", "GET"),
        ("/api/reels/<int:reel_id>/promotions", "POST"),
    ):
        audit.check(route_exists(path, method), f"{method} {path} route exists")

    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    promotion_js = (ROOT / "static/js/pulsesoc_promotions.js").read_text(encoding="utf-8")
    reels_html = owner.get("/pulse/reels").get_data(as_text=True)
    videos_html = owner.get("/pulse/videos").get_data(as_text=True)
    marketplace_html = owner.get("/pulse/marketplace").get_data(as_text=True)
    audit.check("data-reels-bottom-create" in reels_html, "Reels bottom plus opens in-page creator")
    audit.check("data-reel-menu-action=\"edit\"" in source and "data-promote-content=\"reel\"" in source, "Reel owner menu has edit/delete/promote/audio controls")
    audit.check("Remix editing backend is not configured" in source, "Unsupported Remix action is disabled with a reason")
    audit.check("Reel templates backend is not configured" in source and "Reel draft storage is not configured" in source, "Reel creator unsupported templates/drafts are safely disabled")
    audit.check("data-promote-content='video'" in videos_html or 'data-promote-content=\"video\"' in videos_html, "Videos owner sheet is wired to promotions")
    audit.check("data-promote-content='marketplace_listing'" in marketplace_html, "Marketplace owner listing is wired to promotions")
    audit.check("Estimated reach" in promotion_js and "No approved forecasting provider is configured" in promotion_js, "Promotion UI does not fabricate reach")
    audit.check("/api/promotions/eligibility" in promotion_js and "/api/promotions" in promotion_js, "Promotion modal calls backend APIs")

    eligibility = assert_json(owner.get(f"/api/promotions/eligibility?content_type=reel&content_id={data['reel_id']}"), "owner eligibility")
    audit.check(eligibility.get("ok") and eligibility.get("eligible"), "Owner can load Reel promotion eligibility", str(eligibility)[:300])
    audit.check(eligibility.get("estimated_reach") is None and eligibility.get("forecasting_state") == "unavailable", "Eligibility uses safe unavailable forecasting state")
    non_owner_eligibility = viewer.get(f"/api/promotions/eligibility?content_type=reel&content_id={data['reel_id']}")
    audit.check(non_owner_eligibility.status_code == 403, "Non-owner cannot load Reel promotion eligibility")

    custom_audience = owner.post(
        "/api/promotions",
        json={
            "content_type": "reel",
            "content_id": data["reel_id"],
            "goal": "more_views",
            "audience": {"type": "custom"},
            "budget": {"type": "total", "amount_cents": 500},
            "duration": {"days": 3},
        },
    )
    audit.check(custom_audience.status_code == 400, "Custom promotion audiences are rejected until configured")

    created = owner.post(
        "/api/promotions",
        json={
            "content_type": "reel",
            "content_id": data["reel_id"],
            "goal": "more_views",
            "audience": {"type": "automatic"},
            "budget": {"type": "total", "amount_cents": 500},
            "duration": {"days": 3},
            "launch": False,
        },
    )
    created_payload = assert_json(created, "create promotion")
    audit.check(created.status_code == 200 and created_payload.get("promotion", {}).get("status") == "draft", "Owner can save a real draft promotion")
    promotion_id = int(created_payload["promotion"]["promotion_id"])
    analytics = assert_json(owner.get(f"/api/promotions/{promotion_id}/analytics"), "promotion analytics")
    audit.check(analytics.get("available") is False and analytics.get("metrics") is None, "Promotion analytics stay unavailable until real campaign exists")
    non_owner_patch = viewer.patch(f"/api/promotions/{promotion_id}", json={"goal": "more_engagement"})
    audit.check(non_owner_patch.status_code == 404, "Non-owner cannot update another user's promotion")

    for content_type, content_id in (
        ("video", data["video_id"]),
        ("marketplace_listing", data["listing_id"]),
        ("post", data["post_id"]),
    ):
        response = assert_json(owner.get(f"/api/promotions/eligibility?content_type={content_type}&content_id={content_id}"), f"{content_type} eligibility")
        audit.check(response.get("ok") and response.get("eligible"), f"{content_type} promotion eligibility is backend-wired")

    update = owner.patch(
        f"/api/reels/{data['reel_id']}",
        json={"title": "Audit Reel Updated", "caption": "Updated caption", "hashtags": "#audit #updated", "visibility": "public"},
    )
    audit.check(update.status_code == 200, "Owner can edit Reel metadata", update.get_data(as_text=True)[:300])
    viewer_update = viewer.patch(f"/api/reels/{data['reel_id']}", json={"caption": "bad"})
    audit.check(viewer_update.status_code == 403, "Non-owner cannot edit Reel")
    invalid_audio = owner.post(f"/api/reels/{data['reel_id']}/audio", json={"track_id": 0, "start_seconds": 10, "end_seconds": 2})
    audit.check(invalid_audio.status_code == 400, "Invalid Reel audio timing is rejected")
    owner_audio = assert_json(owner.post(f"/api/reels/{data['reel_id']}/audio", json={"track_id": 0, "volume": 0.75}), "owner audio")
    audit.check(owner_audio.get("ok") and owner_audio.get("audio", {}).get("track_id") == 0, "Owner can select original Reel audio")
    viewer_audio = viewer.post(f"/api/reels/{data['reel_id']}/audio", json={"track_id": 0})
    audit.check(viewer_audio.status_code == 403, "Non-owner cannot change Reel audio")
    not_interested = viewer.post(f"/api/reels/{data['reel_id']}/not-interested", json={})
    audit.check(not_interested.status_code == 200, "Non-owner Not Interested action is wired")
    create_without_video = owner.post("/api/reels", json={"caption": "No video"})
    audit.check(create_without_video.status_code == 400 and "Choose a video" in create_without_video.get_data(as_text=True), "Reel create does not fake upload success")

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pulse_content_promotion_audit WHERE promotion_id=?", (promotion_id,))
    audit.check(int(cur.fetchone()[0] or 0) >= 1, "Promotion changes are audited")
    conn.close()

    audit.write()
    print(f"reels promotion audit ok: {REPORT}")


if __name__ == "__main__":
    main()
