#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "static/js/pulse_live_studio_runtime.js").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    require("video.defaultMuted = true" in RUNTIME, "host preview is default-muted")
    require("video.muted = true" in RUNTIME, "host preview is muted")
    require("video.volume = 0" in RUNTIME, "host preview output volume is zero")
    require('video.setAttribute("playsinline", "")' in RUNTIME, "host preview is inline")
    require("echoCancellation: true" in RUNTIME, "microphone echo cancellation is enabled")
    require("noiseSuppression: true" in RUNTIME, "microphone noise suppression is enabled")
    require("autoGainControl: true" in RUNTIME, "microphone auto gain control is enabled")
    require("unpublishLocalTracks" in RUNTIME and "unpublishTrack(track, true)" in RUNTIME, "old LiveKit publications are explicitly unpublished")
    require("duplicate_track_skipped" in RUNTIME and "localPublications(room).find" in RUNTIME, "duplicate audio/video publications are rejected")
    require("cameraStartPromise" in RUNTIME and "setConnectButtonsBusy(true)" in RUNTIME, "rapid Start Camera taps are locked")
    require('qsa(root, "[data-live-start-camera]")' in RUNTIME, "desktop and mobile camera controls share the lock")
    require('player.dataset.liveHostViewer === "1"' in RUNTIME and 'livePlayer.volume = 0' in RUNTIME, "host public-view playback stays silent")
    require("volume='0' aria-label='Muted host camera preview'" in BOT, "host preview markup declares zero-volume intent")
    require("20260613-audio-echo-v1" in BOT, "production runtime cache key is updated")
    print("live audio echo prevention audit ok")


if __name__ == "__main__":
    main()
