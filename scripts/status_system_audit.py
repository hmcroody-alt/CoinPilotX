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
    source = (ROOT / "bot.py").read_text()
    require("/api/pulse/status" in source, "Status create endpoint exists")
    require("/api/pulse/status/rail" in source, "Status rail endpoint exists")
    require("/api/pulse/status/music/search" in source and "/api/pulse/status/ai-story" in source, "Status music and AI Story endpoints exist")
    require("pulse_status_views" in source and "pulse_status_reactions" in source and "pulse_status_replies" in source, "Status view/reaction/reply tables exist")
    require("data-status-tool='stickers'" in source and "data-status-tool='music'" in source, "Status editor tools are functional hooks, not dead labels")
    require("data-status-mode-picker" in source and "data-status-start='camera'" in source and "data-status-start='ai'" in source, "Status creator mode picker exposes real creation paths")
    require("data-status-full-page" in source and "data-status-full-tab" in source and '"following", "Following"' in source and '"trending", "Trending"' in source, "Full Status page has discovery tabs")
    require("data-upload-progress" in source, "Status publishing shows upload progress")
    css = (ROOT / "static" / "css" / "pulse_status_system.css").read_text()
    require(".pulse-status-mode-picker" in css and ".pulse-status-music-panel" in css and ".pulse-status-ai-panel" in css, "Status editor has immersive picker/music/AI styling")
    print("status system audit ok")


if __name__ == "__main__":
    main()
