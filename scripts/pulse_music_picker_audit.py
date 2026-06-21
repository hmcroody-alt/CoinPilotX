#!/usr/bin/env python3
"""Audit Pulse shared music picker and AI suggestion APIs."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    home_core = (ROOT / "static" / "js" / "pulse_home_core.js").read_text(encoding="utf-8")
    for token, label in [
        ("/api/pulse/music/search", "shared music search API exists"),
        ("/api/pulse/music/ai-suggest", "AI music suggestion API exists"),
        ("pulseMusicPicker", "composer music picker exists"),
        ("data-composer-music-search", "picker search form exists"),
        ("mood", "mood filter exists"),
        ("genre", "genre filter exists"),
        ("length", "duration filter exists"),
        ("data-select-composer-track", "track select control exists"),
        ("music_track_id:composerMusicTrackId", "composer sends selected music track"),
        ("data-use-library-music=\"reel\"", "public music library wires Use in Reel"),
        ("data-use-library-music=\"video\"", "public music library wires Use in Video"),
        ("data-use-library-music=\"status\"", "public music library wires Use in Status"),
        ("pulseSelectedMusicTrackId", "music library persists selected track for handoff"),
        ("adoptIncomingReelMusic", "Reels consumes selected library music"),
        ("adoptIncomingStatusMusic", "Status consumes selected library music"),
        ("composerMusicAutofocus", "Video composer opens when music is handed off"),
        ("data-browse-composer-music", "composer music picker exposes approved-track browse action"),
        ("proof verified", "picker shows license proof status"),
        ("payload.get(\"music_track_id\")", "Reel create API accepts shared music track field"),
        ("Creator-safe sounds", "normal Home runtime labels the creator-safe sounds panel"),
        ("openComposerMusicPanel", "normal Home runtime opens music through one wired helper"),
        ("pulseBasePostHtml", "normal Home runtime renders attached post music"),
        ("data-toggle-post-music", "normal Home runtime exposes playable post music"),
        ("video.muted=true", "normal Home runtime mutes post video before attached music plays"),
    ]:
        require(token in source, label)
    for token, label in [
        ("function composerHasMedia()", "composer detects photo or video uploads"),
        ('selectedType === "video" || composerHasMedia()', "music action appears for photo and video content"),
        ('data-remove-composer-music', "selected music can be removed before publish"),
        ('music_track_id: composerMusicTrackId', "photo and video publish payload includes approved track id"),
        ('music_track_id:selectedSoundId', "Reel upload sends selected approved track id"),
        ('Creator-safe sounds', "core Home runtime labels the creator-safe sounds panel"),
        ('function openComposerMusicPanel()', "core Home runtime opens music through one wired helper"),
        ('className = "pulse-composer-music-modal"', "Home music picker uses a viewport-owned modal"),
        ('composerMusicReturnFocus.blur()', "Home music picker dismisses an active mobile keyboard"),
        ('[data-close-composer-music]")?.focus', "Home music picker avoids auto-focusing a text input"),
        ('pulse-music-picker-open', "Home music picker locks background scrolling while open"),
        ('function renderPostMusic(card, post)', "core Home runtime renders attached post music"),
        ('video.defaultMuted = true', "core Home runtime defaults attached post video audio to muted"),
    ]:
        require(token in home_core or token in source, label)
    composer_css = (ROOT / "static" / "css" / "pulse_composer_premium.css").read_text(encoding="utf-8")
    for token, label in [
        ('.pulse-composer-music-modal', "composer music modal has dedicated styling"),
        ('position: fixed !important', "composer music modal is fixed to the viewport"),
        ('z-index: 10080 !important', "composer music modal renders above Home controls"),
        ('overscroll-behavior: contain', "composer music sheet contains touch scrolling"),
    ]:
        require(token in composer_css, label)
    feed_engine = (ROOT / "services" / "pulse_feed_engine.py").read_text(encoding="utf-8")
    for token, label in [
        ("def _music_for_posts(post_ids):", "feed hydrates attached music in one query"),
        ('\"music\": display_music', "feed post contract includes attached music"),
        ("COALESCE(at.approved_by_admin,0)=1", "feed rechecks current admin approval before playback"),
        ("COALESCE(at.removed_at,'')=''", "feed suppresses tracks removed after attachment"),
    ]:
        require(token in feed_engine, label)
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = 1
    response = client.post("/api/pulse/music/ai-suggest", json={"mood": "cinematic", "topic": "market lesson", "length": 30})
    payload = response.get_json() or {}
    require(response.status_code == 200 and payload.get("ok") and payload.get("items"), "AI music suggestion returns approved tracks", response.get_data(as_text=True)[:300])
    require(all(item.get("is_creator_safe") for item in payload.get("items") or []), "AI suggestions are creator-safe")
    track = (payload.get("items") or [])[0]
    created = bot.pulse_feed_engine.create_post(1, "Temporary music hydration audit", "text", enqueue_background=False)
    post_id = int(created.get("post_id") or 0)
    require(post_id > 0, "temporary feed post created for music hydration")
    try:
        conn = bot.db()
        cur = conn.cursor()
        attached = bot.pulse_attach_music_to_content(
            cur,
            content_type="post",
            content_id=post_id,
            track_id=track.get("track_id") or track.get("id"),
            user_id=1,
        )
        conn.commit()
        conn.close()
        require(attached.get("ok"), "approved music attaches to a feed post")
        hydrated = bot.pulse_feed_engine.get_post(post_id, viewer_user_id=1, include_private=True) or {}
        music = hydrated.get("music") or {}
        require(music.get("is_creator_safe") and music.get("audio_url"), "feed rehydrates playable creator-safe music")
    finally:
        conn = bot.db()
        cur = conn.cursor()
        cur.execute("DELETE FROM pulse_content_music WHERE content_id=? AND content_type IN ('post','video')", (post_id,))
        cur.execute("DELETE FROM pulse_posts WHERE id=?", (post_id,))
        conn.commit()
        conn.close()
    print("pulse music picker audit ok")


if __name__ == "__main__":
    main()
