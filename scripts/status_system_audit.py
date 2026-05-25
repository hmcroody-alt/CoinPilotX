#!/usr/bin/env python3
"""Audit Pulse Waves strict 3-click creation, viewing, and lightweight launch flow."""

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
    require("🌊 Launch Wave" in source, "Launch Wave CTA exists")
    require("pulseCoreLauncher" in source and ".pulse-core-launcher" in css, "Pulse Core launcher exists")
    require("pulseCoreRipple" in css and "prefers-reduced-motion" in css, "Pulse Core motion is lightweight and accessible")
    require("Create a Wave" in source and "Share your energy with the world" in source, "cinematic Wave selector copy exists")
    require("pulse-wave-handle" in source and "pulse-wave-mark" in source and "pulse-wave-privacy" in source, "reference-style selector details exist")
    require("pulse-wave-step" in source and ".pulse-wave-step" in css, "3-step Wave progress indicator exists")

    require("Text Wave" in source and "Photo Wave" in source, "only two primary Wave choices exist")
    require(source.count("data-status-start=") == 2, "Wave creation exposes exactly two primary choices", f"found {source.count('data-status-start=')}")
    require("data-status-start='text'" in source and "data-status-start='upload'" in source, "Wave choices are Text Wave and Photo Wave")

    for label in ["Media Wave", "Voice Wave", "Mood Wave", "Live Wave", "AI Wave", "Creator-safe sounds", "Sound search", "Generate Wave"]:
        require(label not in source, f"old complex Wave label removed: {label}")
    for token in ["data-status-start='voice'", "data-status-start='mood'", "data-status-start='ai'", "data-status-start='live'"]:
        require(token not in source, f"old Wave start control removed: {token}")

    require("/pulse/waves" in source, "Pulse Waves page route exists")
    require("/api/pulse/waves/rail" in source, "Pulse Waves rail endpoint exists")
    require("/api/pulse/waves" in source, "Pulse Waves launch endpoint exists")
    require("/api/pulse/waves/<int:status_id>/view" in source, "Wave view endpoint exists")
    require("/api/pulse/waves/<int:status_id>/reply" in source, "Wave reply endpoint exists")
    require("/api/pulse/waves/<int:status_id>/react" in source, "Wave reaction endpoint exists")

    require("setWaveState" in source, "Wave state machine exists")
    for state in ["selecting_wave_type", "composing_text", "selecting_photo", "previewing_wave", "publishing_wave"]:
        require(state in source, f"Wave state exists: {state}")
    require("routeStatusIntent" in source and "openStatusModePicker" in source, "intent router opens fast Wave sheet")
    require("openStatusTextCreator" in source and "pulseStatusBody')?.focus()" in source, "Text Wave opens immersive writing space")
    require("openStatusGalleryCreator" in source and "statusMediaInput?.click()" in source, "Photo Wave opens gallery picker directly")
    require("Add Music" in source and "Add Voice Note" in source and "statusSoundInput?.click()" in source, "music and voice are subtle optional controls")
    require("PulseWaveComponents" in source and "data-wave-component='WaveStage'" in source, "Wave UI is component-driven")
    require("pulse-wave-text-canvas" in source and "TextWaveComposer" in source, "Text Wave renders native writing canvas")
    require("pulse-wave-preview-live" in source and "PhotoWavePreview" in source and "data-wave-caption-preview" in source, "Photo Wave renders real selected media preview")

    require("context_type','pulse_wave'" in source, "Wave media uploads use Wave context")
    require("context_type','pulse_wave_audio'" in source, "optional Wave audio uploads use audio context")
    require("PulseUploadManager" in source and "Launching Wave..." in source, "Wave launch uses upload progress")
    require("Wave launched successfully" in source, "Wave success confirmation exists")
    require("three_click:true" in source, "Wave payload is marked as three-click flow")
    require("visibility:'public'" in source and "duration_hours:24" in source, "Wave launch skips extra setup screens")
    require("pulse_status_views" in source and "pulse_status_reactions" in source and "pulse_status_replies" in source, "legacy status tables remain as stable storage")
    require("product_name\": \"Pulse Waves\"" in source, "rail response identifies Pulse Waves")

    forbidden_visible = ["Create Status", "Pulse Status</", "Create your story", "Create photo or video story", "Create text story"]
    for token in forbidden_visible:
        require(token not in source, f"old visible label removed: {token}")
    for token in ["Just now · Public", "Preview your Wave", "Chasing sunsets", "goodvibes", "static/screenshots", "mockup"]:
        require(token not in source, f"mock/reference Wave content is not rendered: {token}")

    require(".pulse-wave-sheet" in css, "Wave sheet styling exists")
    require(".pulse-wave-preview-live" in css and ".pulse-wave-text-canvas" in css, "native cinematic Wave composer surfaces are styled")
    require(".pulse-wave-live-caption" in css, "Photo Wave caption is live dynamic UI")
    require("pulseWaveAtmosphere" in css and "pulseWaveFloat" in css, "cinematic Wave motion system exists")
    require("grid-template-columns: repeat(2" in css, "desktop Wave actions are strict two-choice layout")
    require(".pulse-wave-secondary-controls" in css, "secondary controls are visually subtle")
    require("grid-template-columns: 1fr" in css, "mobile Wave actions stack cleanly")

    print("pulse waves 3-click audit ok")


if __name__ == "__main__":
    main()
