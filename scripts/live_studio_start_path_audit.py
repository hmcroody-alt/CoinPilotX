#!/usr/bin/env python3
"""Audit that PulseSoc Live Studio is the only official start-live surface."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
HOME_JS = (ROOT / "static/js/pulse_home_core.js").read_text(encoding="utf-8")
STUDIO_JS = (ROOT / "static/js/pulse_live_studio_runtime.js").read_text(encoding="utf-8")
STUDIO_CSS = (ROOT / "static/css/pulse_live_studio.css").read_text(encoding="utf-8")
NATIVE_APP = (ROOT / "mobile/pulse-react-native/App.tsx").read_text(encoding="utf-8")
NATIVE_LIVE = (ROOT / "mobile/pulse-react-native/components/NativeLiveBroadcast.tsx").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main() -> int:
    require('@webhook_app.route("/pulse/live/studio", methods=["GET"])' in BOT, "Studio setup route exists")
    require('@webhook_app.route("/pulse/live/studio/<int:stream_id>", methods=["GET"])' in BOT, "Studio broadcast cockpit route exists")
    require("data-studio-desktop-layout='command-center'" in BOT, "desktop Studio command-center layout is explicit")
    require("data-studio-mobile-layout='vertical-cockpit'" in BOT, "mobile Studio vertical cockpit layout is explicit")
    require("data-mobile-studio-controls" in BOT and "data-mobile-studio-drawers" in BOT, "mobile Studio controls and swipe-style drawers exist")
    require("studio-mobile-drawers" in STUDIO_CSS and "100dvh" in STUDIO_CSS and "env(safe-area-inset-bottom)" in STUDIO_CSS, "mobile Studio CSS is full-screen and safe-area aware")
    require("studio-workspace" in STUDIO_CSS and "studio-sidebar" in STUDIO_CSS and "studio-chat-panel" in STUDIO_CSS, "desktop Studio command-center regions remain styled")

    require("LiveStudioCameraOwner" in STUDIO_JS and "data-live-camera-owner='LiveStudioCameraOwner'" in BOT, "LiveStudioCameraOwner owns host camera publishing")
    require("LiveHostPublisher" not in STUDIO_JS, "legacy host camera owner name is removed from runtime")
    require("createLocalTracks" in STUDIO_JS and "browser-publish" in STUDIO_JS, "host LiveKit publishing remains in Studio runtime")

    setup_block = BOT[BOT.find("def pulse_live_page"):BOT.find("def pulse_live_studio_page")]
    reels_block = BOT[BOT.find("def pulse_reels_page"):BOT.find("def pulse_live_page")]
    require("getUserMedia" not in setup_block and "livePreviewVideo" not in setup_block, "Studio setup route does not start camera preview")
    require("reelLiveStream" not in reels_block and "reelLivePreview" not in reels_block, "Reels does not own a direct Live host camera stream")
    require("pulseApi('/api/pulse/live/start'" not in reels_block and 'pulseApi("/api/pulse/live/start"' not in reels_block, "Reels does not create live sessions directly")

    require('href="/pulse/live/studio?context_type=home">Go Live' in BOT, "Home composer Go Live routes to Studio")
    require('href="/pulse/live/studio?context_type=home" data-composer-live' in BOT, "Home composer Live button routes to Studio")
    require("context_type=reels" in BOT and "liveStudioUrlFromReels" in BOT, "Reels Go Live preserves Studio context")
    require("/pulse/live/studio?context_type=status" in BOT and "/pulse/live/studio?context_type=status" in HOME_JS, "Status Live routes to Studio")
    require("/pulse/live/studio?context_type=creator_studio" in BOT, "Creator Studio Start Live routes to Studio")
    require("/pulse/live/studio?context_type=videos" in BOT, "Videos Go Live routes to Studio")
    require("/pulse/live/studio?context_type=schedule" in BOT and "/pulse/live/studio?context_type=event" in BOT, "schedule/event Live gateways route to Studio with context")
    require("context_type: document.getElementById('liveContextType')" in BOT and '"context_type": context_type' in BOT, "Studio context params are preserved into live session metadata")

    require("/api/pulse/live/start" not in NATIVE_LIVE and "Room" not in NATIVE_LIVE and "LiveKit" not in NATIVE_LIVE, "native app does not publish LiveKit host camera directly")
    require("/pulse/live/studio?context_type=native" in NATIVE_APP and "/pulse/live/studio?context_type=native" in NATIVE_LIVE, "native Go Live opens Studio")
    require('[href="/pulse/live"][data-native-live]' not in NATIVE_APP, "native bridge no longer hijacks Live discovery into native camera")

    direct_publishers = [
        "static/js/pulse_live_studio_runtime.js",
        "static/js/pulse_live_studio.js",
    ]
    require(all("LiveStudioCameraOwner" in (ROOT / path).read_text(encoding="utf-8") for path in direct_publishers), "all browser host publisher runtimes declare the Studio camera owner")

    print("live studio start path audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
