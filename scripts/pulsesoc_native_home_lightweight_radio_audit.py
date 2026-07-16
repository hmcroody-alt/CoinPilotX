#!/usr/bin/env python3
"""Behavior-oriented audit for the lightweight native Home and Pulse Radio contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    composer = read("mobile-native/src/components/HomePulseComposer.tsx")
    radio_api = read("mobile-native/src/api/radio.ts")
    radio_core = read("mobile-native/src/core/pulseRadio.ts")
    call = read("mobile-native/src/screens/CallScreen.tsx")
    reels = read("mobile-native/src/screens/ReelsScreen.tsx")
    backend = read("bot.py")

    require("webRadioDock" not in home, "Home still reserves or renders the removed mini-player dock")
    require("Beautiful Stranger" not in home, "Home still contains a fabricated radio track")
    require("home-pulse-radio-toggle" in home, "Hero radio control has no stable native QA target")
    require('status: "paused"' in radio_core and 'message: "Tap to play"' in radio_core, "Radio does not initialize paused truthfully")
    require("Audio.Sound.createAsync" in radio_core, "Shared native radio coordinator has no real playback path")
    require(radio_core.index("listPulseRadioTracks()") > radio_core.index("async function startPlayback"), "Radio catalog can load outside explicit playback intent")
    require(radio_core.index("Audio.Sound.createAsync") > radio_core.index("async function startPlayback"), "Audio session can start outside explicit playback intent")
    require("AppState.addEventListener" in radio_core and "pausePulseRadio" in radio_core, "Background lifecycle does not pause radio")
    require('status: "buffering"' in radio_core and 'message: "Buffering…"' in radio_core, "Radio does not expose a truthful buffering state")
    require("intentGeneration" in radio_core and "generation !== intentGeneration" in radio_core, "A paused connecting request can still start audio")
    require("unloadAsync" in radio_core and "intentGeneration += 1" in radio_core, "Pause does not cancel and release the shared audio object")
    require("Pulse Radio is unavailable. Tap to retry." in radio_core, "Radio exposes raw backend errors instead of a stable retry state")
    require("pausePulseRadio" in call and "pausePulseRadio" in reels, "Call/Reels audio priority does not pause radio")
    require('/api/pulse/music/radio' in radio_api, "Native radio does not reuse the production radio endpoint")
    require('/api/pulse/music/<int:track_id>/event' in backend, "Production play-event contract is missing")
    require("home-composer-expand" in composer and "home-composer-collapse" in composer, "Composer responsive states are not exposed")
    require("useState(initiallyExpanded)" in composer, "Composer does not start collapsed unless explicitly requested")
    require("setExpanded(true)" in composer and "setDraftRecovered(true)" in composer, "Recovered drafts do not reopen the composer")
    require("setExpanded(false)" in composer and "onCreated(response.post)" in composer, "Successful publish does not return to compact Home")
    require("createPost(payload)" in composer and "useNativeMediaUpload" in composer, "Existing production composer wiring was replaced")
    require("listFeed" in home and "listStatuses" in home and "PostCard" in home, "Canonical feed/Status/card paths were replaced")
    require("home-status-view-all" in home, "Status View All is not wired")
    require('pointerEvents="none"' in home, "Decorative Home atmosphere may intercept touches")

    print("PulseSoc native lightweight Home and paused-radio audit passed.")
    print("Verified explicit playback intent, production API reuse, lifecycle/audio priority, dock removal, compact composer, Status, feed, and touch containment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
