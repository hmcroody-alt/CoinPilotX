#!/usr/bin/env python3
"""Behavior gate for canonical native Post and Reel publication."""

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
    camera = read("mobile-native/src/screens/CameraStudioScreen.tsx")
    feed = read("mobile-native/src/api/feed.ts")
    reels = read("mobile-native/src/api/reels.ts")
    upload = read("mobile-native/src/media/useNativeMediaUpload.ts")
    queue = read("mobile-native/src/media/useComposerMediaQueue.ts")
    backend = read("bot.py")
    preview = read("services/preview_service.py")
    report = read("reports/pulsesoc_native_post_reel_publish_repair_2026-07-19.md")

    require('pulseApi<CreatePostResponse>("/api/pulse/posts"' in feed, "Post client must reuse the production Post route")
    require('pulseApi<CreateReelResponse>("/api/pulse/reels/create"' in reels, "Reel client must reuse the production Reel route")
    require('from "../api/feed"' in camera and 'from "../api/reels"' in camera, "Camera Studio must import canonical clients")
    require("createPostFromCamera" not in camera and "createReelFromCamera" not in camera, "legacy camera-only publishing must not be reachable from Camera Studio")
    for token in ("media_ids: [mediaId]", "findExistingPostByMediaId", "findExistingReelByMediaId", "Server-confirmed", "canonical identifiers"):
        require(token in camera, f"Camera Studio missing duplicate-safe canonical behavior: {token}")
    require("mediaUpload.result && uploadResultMediaId(mediaUpload.result)" in camera, "publish retry must reuse the completed media upload")
    require("setPublishStage" in camera and "uploaded media are preserved" in camera, "publish state and failure copy must preserve the draft")
    require("Upload complete. Video processing continues." in upload, "single-media upload must report processing truthfully")
    require("Upload complete. Video processing continues." in queue, "composer upload queue must report processing truthfully")
    require('if not db_service.IS_POSTGRES:' in preview, "SQLite PRAGMA must be gated away from PostgreSQL")

    sync_start = backend.index("def pulse_sync_video_processing_rows")
    sync_end = backend.index('@webhook_app.route("/api/pulse/media/<int:media_id>/status"', sync_start)
    sync = backend[sync_start:sync_end]
    require("GLOB '[" not in sync, "media processing sync must not use SQLite GLOB")
    require("publication_context_id = safe_int" in sync, "media processing sync must safely normalize publication context")

    for phrase in ("HTTP 500", "canonical Post route", "canonical Reel route", "uploaded bytes succeeded", "Physical-device acceptance remains open"):
        require(phrase in report, f"repair report missing evidence: {phrase}")

    print("PulseSoc native Post/Reel publish audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
