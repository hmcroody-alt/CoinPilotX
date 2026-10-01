#!/usr/bin/env python3
"""Audit PulseSoc Live injection into the Reels feed."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service, live_feed_service  # noqa: E402


REPORT_PATH = ROOT / "reports" / "live_inside_reels_audit.json"


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def ensure_user(cur) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur.execute("SELECT user_id FROM users WHERE email=? LIMIT 1", ("live-reels-audit@example.com",))
    row = cur.fetchone()
    if row:
        return int(row["user_id"])
    cur.execute(
        "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
        ("livereelsaudit", "Live Reels Audit", "live-reels-audit@example.com", now, now),
    )
    return int(cur.lastrowid)


def create_live_session() -> tuple[int, int, int, str]:
    now = datetime.utcnow().isoformat(timespec="seconds")
    playback_url = "https://live.coinpilotxai.app/hls/reels-phase1-audit.m3u8"
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    user_id = ensure_user(cur)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
        (user_id,title,category,thumbnail_url,audience,status,publish_state,stream_key,viewer_count,created_at,started_at,
         stream_uuid,hls_url,playback_url,webrtc_room_id,stream_health,engagement_score,is_live,updated_at)
        VALUES (?, 'Reels Native Live Audit', 'Creator QA', '', 'public', 'live', 'live', 'reels_live_key',
                777, ?, ?, 'reels-native-live-audit', ?, ?, 'pulse-webrtc-reels-audit', 'stable', 999, 1, ?)
        """,
        (user_id, now, now, playback_url, playback_url, now),
    )
    live_id = int(cur.lastrowid)
    post_id = live_feed_service.ensure_live_feed_post(
        cur,
        user_id=user_id,
        live_id=live_id,
        title="Reels Native Live Audit",
        category="Creator QA",
        playback_url=playback_url,
        viewer_count=777,
    )
    cur.execute("UPDATE pulse_live_sessions SET feed_post_id=? WHERE id=?", (post_id, live_id))
    cur.execute(
        "INSERT INTO pulse_live_chat (live_id,user_id,body,message_type,moderation_status,pinned,created_at) VALUES (?, ?, 'Reels audit chat preview is live.', 'text', 'approved', 0, ?)",
        (live_id, user_id, now),
    )
    conn.commit()
    conn.close()
    return user_id, live_id, post_id, playback_url


def static_audit() -> dict:
    source = (ROOT / "bot.py").read_text()
    media_renderer = (ROOT / "static/js/pulse_media_renderer.js").read_text()
    checks = {
        "backend_adapter_exists": "def pulse_live_reel_items" in source,
        "backend_merger_exists": "def pulse_merge_live_reel_items" in source,
        "active_live_query_uses_live_sessions": "FROM pulse_live_sessions l" in source and "PULSE_LIVE_REELS_QUERY_FAILED" in source,
        "playable_live_required": "COALESCE(l.playback_url,'')!=''" in source and "COALESCE(l.mux_playback_id,'')!=''" in source,
        "live_renderer_exists": "function liveReelHtml(reel)" in source,
        "live_card_identity": "reel-live-card" in source and "data-content-type=\"live\"" in source,
        "live_autoplay_media": "data-live-reel-media" in source and "data-reel-media data-reel-id" in source,
        "live_join_wired": "data-live-join-reel" in source and "/api/pulse/live/${encodeURIComponent(liveId)}/join" in source,
        "live_react_wired": "data-live-reaction" in source and "/api/pulse/live/${encodeURIComponent(liveId)}/react" in source,
        "live_share_wired": "data-live-share-reel" in source and "navigator.share" in source,
        "chat_preview_present": "data-live-chat-preview" in source and "live_chat_preview" in source,
        "viewer_count_present": "data-live-viewer-count" in source,
        "regular_reel_view_bypassed_for_live": "card.dataset.contentType==='live'||card.dataset.liveReelId" in source,
        "normalizer_skips_live_cards": "card.dataset.contentType==='live'||card.classList.contains('reel-live-card')" in source,
        "media_renderer_skips_live_remix": 'card?.dataset?.contentType === "live"' in media_renderer and 'card.classList.contains("reel-live-card")' in media_renderer,
        "no_public_loginexus_label": "LogiNexus" not in source[source.find("function liveReelHtml"):source.find("function reelHtml(reel)")],
    }
    for label, passed in checks.items():
        require(passed, label.replace("_", " "))
    return checks


def dynamic_audit(user_id: int, live_id: int, playback_url: str) -> dict:
    helper_items = bot.pulse_live_reel_items(viewer_user_id=user_id, lane="live", limit=6)
    helper_live = [item for item in helper_items if int(item.get("live_session_id") or 0) == live_id]
    require(helper_live, "live helper returns created active live session")
    require((helper_live[0].get("live") or {}).get("playback_url") == playback_url, "live helper preserves playback URL")
    require(helper_live[0].get("live_chat_preview"), "live helper returns real chat preview")

    payload = bot.pulse_reel_feed_payload(viewer_user_id=user_id, lane="live", limit=8)
    live_reels = [item for item in payload.get("reels", []) if int(item.get("live_session_id") or 0) == live_id]
    require(live_reels, "Reels payload includes active live item")
    require(live_reels[0].get("content_type") == "live", "Reels live item is typed as live")
    require((live_reels[0].get("live") or {}).get("playback_url") == playback_url, "Reels live item carries playback URL")

    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = user_id
    api_payload = client.get("/api/pulse/reels/feed?tab=live&limit=8").get_json() or {}
    api_live = [item for item in api_payload.get("reels", []) if int(item.get("live_session_id") or 0) == live_id]
    require(api_payload.get("ok"), "Reels feed API returns ok")
    require(api_live, "Reels feed API returns active live item")

    join_payload = client.post(f"/api/pulse/live/{live_id}/join", json={"source": "reels"}).get_json() or {}
    require(join_payload.get("ok"), "Live join endpoint accepts Reels viewer")
    require(int(join_payload.get("viewer_count") or 0) >= 1, "Live join endpoint returns real viewer count")

    react_payload = client.post(f"/api/pulse/live/{live_id}/react", json={"reaction_type": "🔥", "source": "reels"}).get_json() or {}
    require(react_payload.get("ok"), "Live reaction endpoint accepts Reels reaction")
    require(react_payload.get("reaction_type") == "🔥", "Live reaction endpoint preserves reaction type")

    return {
        "helper_live_count": len(helper_items),
        "payload_live_count": len(live_reels),
        "api_live_count": len(api_live),
        "join_viewer_count": join_payload.get("viewer_count"),
        "reaction_id": react_payload.get("reaction_id"),
    }


def main():
    bot.init_db()
    user_id, live_id, post_id, playback_url = create_live_session()
    static = static_audit()
    dynamic = dynamic_audit(user_id, live_id, playback_url)
    report = {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "user_id": user_id,
        "live_id": live_id,
        "post_id": post_id,
        "static": static,
        "dynamic": dynamic,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"live inside reels audit ok -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
