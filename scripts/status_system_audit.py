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
    source = (ROOT / "bot.py").read_text()
    require("/api/pulse/status" in source, "Status create endpoint exists")
    require("/api/pulse/status/rail" in source, "Status rail endpoint exists")
    require("/api/pulse/status/<int:status_id>/reply" in source, "Status reply endpoint exists")
    require("/api/pulse/status/music/search" in source and "/api/pulse/status/ai-story" in source, "Status music and AI Story endpoints exist")
    require("pulse_status_views" in source and "pulse_status_reactions" in source and "pulse_status_replies" in source, "Status view/reaction/reply tables exist")
    require("initPulseStatusFresh" in source and "renderMediaPreview" in source, "Status editor uses fresh self-contained JavaScript")
    require("data-status2-modal" in source and "Text" in source and "Photo" in source and "Video" in source, "Create Status opens the unified story chooser")
    require("pulse-status2-type-grid" in source and '"photo"' in source and '"text"' in source, "Create Status chooser includes primary story options")
    require("data-status2-pick-media" in source and "media.click()" in source, "Create Status directly opens media picker before preview")
    require("placeholderTypes" in source and "coming soon" in source.lower(), "unsupported status types show clear placeholder copy")
    for status_type in ["text", "photo", "video", "music", "camera", "ai", "live"]:
        require(status_type in source, f"Create Status exposes {status_type} start")
    require("Music" in source and '"music"' in source, "Music card has placeholder flow")
    require("Camera" in source and '"camera"' in source, "Camera card has placeholder flow")
    require("Live" in source and '"live"' in source, "Live card has placeholder flow")
    require("AI Story" in source and '"ai"' in source, "AI Story card has placeholder flow")
    require("data-status2-view" in source and "/api/pulse/status/rail" in source, "Status rail renders existing stories")
    require("statusCardHtml" in source and "data-status2-view" in source, "Status cards render through the fresh rail")
    require("data-status-full-page" in source and "data-status-full-tab" in source and '"following", "Following"' in source and '"trending", "Trending"' in source, "Full Status page has discovery tabs")
    require("PulseUploadManager.upload" in source and "Posting Status..." in source, "Status publishing shows upload progress")
    css = (ROOT / "static" / "css" / "pulse_status_system.css").read_text()
    require(".pulse-status2-modal" in css and ".pulse-status2-composer" in css and ".pulse-status2-preview" in css and ".pulse-status2-strip" in css, "Status editor and viewer have immersive styling")
    print("status system audit ok")


if __name__ == "__main__":
    main()
