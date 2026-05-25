#!/usr/bin/env python3
"""Audit Pulse Waves compatibility, creation, viewing, and lightweight launch flow."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message, detail=""):
    if not condition:
        raise AssertionError(f"{message}: {detail}")
    print(f"ok - {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "pulse_status_system.css").read_text(encoding="utf-8")

    require("🌊 Pulse Waves" in source, "Pulse Waves naming is visible")
    require("Launch Wave" in source and "Launch 🌊" in source, "Launch Wave CTA exists")
    require("pulseCoreLauncher" in source and ".pulse-core-launcher" in css, "Pulse Core launcher exists")
    require("pulseCoreRipple" in css and "prefers-reduced-motion" in css, "Pulse Core motion is lightweight and accessible")

    for label in ["Media Wave", "Voice Wave", "Mood Wave", "Live Wave", "AI Wave"]:
        require(label in source, f"{label} quick action exists")

    require("/pulse/waves" in source, "Pulse Waves page route exists")
    require("/api/pulse/waves/rail" in source, "Pulse Waves rail endpoint exists")
    require("/api/pulse/waves" in source, "Pulse Waves launch endpoint exists")
    require("/api/pulse/waves/ai" in source, "AI Wave endpoint alias exists")
    require("/api/pulse/waves/<int:status_id>/view" in source, "Wave view endpoint exists")
    require("/api/pulse/waves/<int:status_id>/reply" in source, "Wave reply endpoint exists")
    require("/api/pulse/waves/<int:status_id>/react" in source, "Wave reaction endpoint exists")

    require("routeStatusIntent" in source and "openStatusModePicker" in source, "intent router opens fast Wave sheet")
    require("openStatusGalleryCreator" in source and "statusMediaInput?.click()" in source, "Media Wave opens gallery picker")
    require("openStatusMusicCreator" in source and "statusSoundInput?.click()" in source, "Voice Wave opens audio picker")
    require("openStatusMoodCreator" in source and "data-wave-mood" in source, "Mood Wave has one-tap mood choices")
    require("location.href='/pulse/live'" in source, "Live Wave opens live flow")
    require("openStatusAiCreator" in source and "generateAiStatusStory" in source, "AI Wave opens AI assist")

    require("context_type','pulse_wave'" in source, "Wave media uploads use Wave context")
    require("context_type','pulse_wave_voice'" in source, "Voice Wave uploads use voice context")
    require("PulseUploadManager" in source and "Launching Wave..." in source, "Wave launch uses upload progress")
    require("Wave launched successfully" in source, "Wave success confirmation exists")
    require("pulse_status_views" in source and "pulse_status_reactions" in source and "pulse_status_replies" in source, "legacy status tables remain as stable storage")
    require("product_name\": \"Pulse Waves\"" in source, "rail response identifies Pulse Waves")

    forbidden_visible = ["Create Status", "Pulse Status</", "Create your story", "Create photo or video story", "Create text story"]
    for token in forbidden_visible:
        require(token not in source, f"old visible label removed: {token}")

    require(".pulse-wave-sheet" in css, "Wave sheet styling exists")
    require("grid-template-columns: repeat(5" in css, "desktop Wave actions are compact")
    require("grid-template-columns: 1fr" in css, "mobile Wave actions stack cleanly")

    print("pulse waves audit ok")


if __name__ == "__main__":
    main()
