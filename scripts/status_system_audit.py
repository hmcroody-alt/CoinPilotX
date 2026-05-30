#!/usr/bin/env python3
"""Consolidated audit for Pulse Status create/view/publish/reply contracts."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(script: str):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="")
        raise AssertionError(f"{script} failed")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    run("pulse_status_audit.py")
    run("create_status_flow_audit.py")
    run("media_story_audit.py")
    run("mobile_story_audit.py")
    run("audio_pipeline_audit.py")
    source = (ROOT / "bot.py").read_text()
    require("/api/pulse/status" in source, "Status create endpoint exists")
    require("/api/pulse/status/rail" in source, "Status rail endpoint exists")
    require("/api/pulse/status/<int:status_id>/reply" in source, "Status reply endpoint exists")
    require("/api/pulse/status/music/search" in source and "/api/pulse/status/ai-story" in source, "Status music and AI Story endpoints exist")
    require("pulse_status_views" in source and "pulse_status_reactions" in source and "pulse_status_replies" in source, "Status view/reaction/reply tables exist")
    require("data-status-tool='stickers'" in source and "data-status-tool='music'" in source, "Status editor tools are functional hooks, not dead labels")
    require("data-status-mode-picker" in source and "Photo or Video" in source and "Text Status" in source, "Create Status opens the unified story chooser")
    require("pulse-status-mode-grid" in source and "pulse-status-choice-media" in source and "pulse-status-choice-text" in source, "Create Status chooser includes primary story options")
    require("openStatusGalleryCreator" in source and "statusMediaInput?.click()" in source and "Choose an image or video from your gallery." in source, "Create Status directly opens gallery picker before preview")
    require("statusForm?.classList.toggle('is-choosing'" in source, "Create Status hides editor tools until a story type is selected")
    require(source.count("data-status-start=") == 6, "Create Status entry exposes text, media, music, camera, AI, and live starts")
    require("Music Status" in source and "openStatusMusicCreator" in source, "Music card opens music-first flow")
    require("Camera Status" in source and "/pulse/camera?target=status" in source, "Camera card opens Pulse Camera")
    require("Live Status" in source and "/pulse/live" in source, "Live card opens go-live flow")
    require("AI Story" in source and "openStatusAiCreator" in source, "AI Story card opens AI generator")
    require("Following Stories" in source and "Trending Stories" in source and "Global Stories" in source, "Discovery cards have viewer intents")
    require("routeStatusIntent" in source and "openStatusViewerFeed(mode)" in source, "Status intent router separates creation and viewing")
    require("data-status-story-viewer" in source and "data-status-story-reply" in source and "data-status-story-react" in source, "Status viewer has reply and reaction behavior")
    require("data-status-full-page" in source and "data-status-full-tab" in source and '"following", "Following"' in source and '"trending", "Trending"' in source, "Full Status page has discovery tabs")
    require("data-upload-progress" in source, "Status publishing shows upload progress")
    css = (ROOT / "static" / "css" / "pulse_status_system.css").read_text()
    require(".pulse-status-mode-picker" in css and ".pulse-status-music-panel" in css and ".pulse-status-ai-panel" in css and ".pulse-status-story-viewer" in css, "Status editor and viewer have immersive styling")
    print("status system audit ok")


if __name__ == "__main__":
    main()
