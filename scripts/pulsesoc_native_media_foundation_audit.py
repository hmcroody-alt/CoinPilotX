#!/usr/bin/env python3
"""Behavior-level static gate for the canonical PulseSoc native media foundation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"Missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    contract = read("mobile-native/src/media/mediaContract.ts")
    access = read("mobile-native/src/media/mediaAccess.ts")
    coordinator = read("mobile-native/src/core/mediaPlaybackCoordinator.ts")
    reels = read("mobile-native/src/api/reels.ts")
    feed = read("mobile-native/src/api/feed.ts")
    messenger = read("mobile-native/src/api/messenger.ts")
    save_contract = read("mobile-native/src/social/saveContract.ts")
    upload = read("mobile-native/src/media/nativeMediaUpload.ts")
    voice = read("mobile-native/src/core/voiceMessagePlayback.ts")
    radio = read("mobile-native/src/core/pulseRadio.ts")
    reel_player = read("mobile-native/src/components/ReelPlayerCard.tsx")
    status_player = read("mobile-native/src/components/StatusViewerCard.tsx")
    viewer = read("mobile-native/src/components/NativeMediaViewer.tsx")
    live = read("mobile-native/src/screens/LiveScreen.tsx")
    live_ownership = read("mobile-native/src/live/livePlaybackOwnership.ts")
    calls = read("mobile-native/src/screens/CallScreen.tsx")
    auth = read("mobile-native/src/session/auth.ts")
    report = read("reports/pulsesoc_native_media_foundation_2026-07-19.md")

    for field in ("media_id", "attachment_id", "post_id", "reel_id", "message_id", "status_id", "processing_status", "moderation_status", "music_track_id", "original_audio_id"):
        require(field in contract, f"canonical media contract missing {field}")
    require("isLikelyExpiringMediaUrl" in contract and "mediaRecordForCache" in contract, "signed URLs must be excluded from persistent caches")
    require("/api/pulse/media/${mediaId}/status" in access, "signed/processing refresh must reuse canonical media status route")

    for kind in ("call", "recording", "live", "voice", "radio", "reel", "status", "viewer", "music_preview"):
        require(kind in coordinator, f"playback ownership policy missing {kind}")
    require("PRIORITY" in coordinator and "AppState.addEventListener" in coordinator, "coordinator must enforce priority and background release")
    require("claimMediaPlayback" in live_ownership and "kind: \"live\"" in live_ownership, "Live ownership helper must delegate to the shared coordinator")
    for source in (reel_player, status_player, viewer, calls, voice, radio):
        require("claimMediaPlayback" in source, "every active native media owner must use the shared coordinator")
    require("claimLivePlaybackOwner" in live, "Live screen must use the shared Live ownership helper")
    require("!drivesPlayback || !active || !ownsPlayback || !musicPolicy.hasAttachedMusic" in reel_player, "offscreen or preempted Reels must not allocate attached-audio players")

    for route in ("/api/pulse/media/upload", "/api/pulse/media/${mediaId}/status"):
        require(route in upload, f"native upload must reuse production route {route}")
    for route in ("/api/messages/media/init", "/api/messages/media/upload", "/api/messages/media/complete"):
        require(route in messenger, f"Messenger must reuse media foundation route {route}")
    require("uploadMessengerMediaLegacy" not in messenger, "obsolete parallel Messenger uploader must not remain")

    for route in ("/api/pulse/reels/feed", "/api/pulse/reels/create", "/api/pulse/reels/${reelId}/react", "/api/pulse/reels/${reelId}/comments", "/api/pulse/reels/${reelId}/view"):
        require(route in reels, f"Reels contract missing production route {route}")
    require("/api/pulse/reels/${id}/save" in save_contract, "Reels save route must remain in the shared save contract")
    require("seen.has(reel.id)" in reels, "Reels pagination must deduplicate canonical Reel IDs")
    require("mediaRecordForCache" in reels and "mediaRecordForCache" in feed, "feed and Reels caches must strip signed credentials")
    require("clearUserScopedMediaState" in auth, "logout must clear user-scoped media caches and playback")

    for phrase in ("Production contract matrix", "No native-only media backend", "Physical-device measurements", "Reels UI release gate"):
        require(phrase in report, f"media foundation report missing {phrase}")

    native_sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mobile-native/src").rglob("*.ts*"))
    require("Upload endpoint was not found" not in native_sources, "generic missing endpoint error must not ship")
    require("react-native-webview" not in native_sources.lower(), "native media foundation must not introduce WebView")

    print("PulseSoc native media foundation audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
