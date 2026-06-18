#!/usr/bin/env python3
"""Audit the unified PulseSoc Home Status command-center flow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"
FEED_CSS = ROOT / "static/css/pulse_desktop_feed.css"
STATUS_VIEWER = ROOT / "static/js/pulse_status_viewer.js"
CAMERA_ENGINE = ROOT / "static/js/pulse_camera_engine.js"
HOME_CORE = ROOT / "static/js/pulse_home_core.js"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    status_css = STATUS_CSS.read_text(encoding="utf-8")
    feed_css = FEED_CSS.read_text(encoding="utf-8")
    viewer = STATUS_VIEWER.read_text(encoding="utf-8")
    camera = CAMERA_ENGINE.read_text(encoding="utf-8")
    home_core = HOME_CORE.read_text(encoding="utf-8")

    expect("data-status-home-create" in bot, "Home status rail exposes an Add Status command")
    expect("pulse_status_home_creator_html" in bot and "id='pulseStatusViewer'" in bot and "id='pulseStatusForm'" in bot, "Home renders the in-place Status creator DOM")
    expect("__PULSE_STATUS_HOME_CREATOR__" in bot, "Home shell injects the Status creator markup")
    expect("data-status-command-center-wired" not in bot, "audit is checking source rather than runtime-only state")
    expect("initPulseStatusHomeCommandCenter" in bot, "Home status command center capture handler is installed")
    expect("bindStatusHome" in home_core and "statusPublish" in home_core, "Active Home core wires Status create and publish")
    expect("PulseUploadManager.upload" in home_core and 'api("/api/pulse/status"' in home_core, "Active Home core uses real upload and Status APIs")
    expect("openStatusModePicker()" in bot, "Home Add Status opens the in-place creator")
    expect("/pulse?create_status=1" in bot and "statusParams.has('create_status')" in bot, "Primary navigation opens Status creation on Home")
    expect("openStatusViewerFeed('global'" in bot, "Home status cards open the immersive viewer")
    expect("hydrateStatusRail().then" in bot and "URLSearchParams(location.search)" in bot, "Home supports status deep links")
    expect("openStatusCameraCreator" in bot and "capture','environment" in bot, "Camera status capture stays in the Home creator workflow")
    expect("closeStatusViewerNow(statusStoryViewer)" in bot, "Home viewer close delegates to shared timer/media cleanup")
    expect("closeStatusViewerNow" in viewer and "window.PulseStatusViewer" in viewer, "shared viewer exposes close cleanup")
    expect("clearStoryTimers()" in viewer and "media.pause()" in viewer, "shared close stops timers and media")
    expect(".pulse-status-story-nav" in status_css and "display: none !important" in status_css, "middle next/back buttons are hidden")
    expect("object-fit: contain" in status_css, "story viewer preserves media aspect ratio")
    expect("grid-auto-columns: minmax(152px, 188px)" in feed_css or "grid-auto-columns: minmax(152px, 188px)" in status_css, "Home previews are longer and clearer")
    expect('location.href = `/pulse?status=${encodeURIComponent(data.status?.id || "")}`' in camera, "camera Status publish returns to Home deep link")
    expect("COALESCE(s.visibility,'public')='public'" in bot and "s.user_id=?" in bot and "followers" in bot, "Status rail preserves backend audience filtering")
    expect("clean_html(payload.get(\"body\")" in bot and "clean_status_color" in bot, "Status creation sanitizes text and style metadata")
    expect("pulse_status_grouped_items_for_lane" in bot and "rail_items" in bot and "live_unseen_creator_grouped_engagement" in bot, "Home rail uses grouped creator-first intelligent ranking")
    expect("author_live" in bot and "has-unseen" in home_core and "pulse-status-ring-progress" in status_css, "Status rail exposes live/unseen neon ring states")
    expect("owner_analytics" in bot and "completion_rate" in bot and "pulse_status_shares" in bot, "Creator-only analytics include views, completion, replies, reactions, and shares")
    expect("pulse_status_row_for_viewer" in bot and "/api/pulse/status/<int:status_id>/share" in bot, "Status interaction APIs enforce viewer privacy before metrics mutate")
    expect("reportStoryCompletion" in viewer and "navigator.sendBeacon" in viewer and "watch_ms" in viewer, "Shared viewer reports completion without blocking close or navigation")
    expect("https://stream.mux.com/${media.mux_playback_id}.m3u8" in viewer and "object-fit: contain !important" in status_css, "Viewer preserves aspect ratio and prefers canonical Mux playback")
    expect("status_activity" in bot and "feed_context_only_no_insertions" in bot, "Feed receives status discovery signals without inserting status spam")
    expect("will-change: opacity, transform" in status_css and "pointer-events: none !important" in status_css, "Viewer uses GPU-friendly transitions and avoids hidden touch blockers")

    print("pulse home status command-center audit ok")


if __name__ == "__main__":
    main()
