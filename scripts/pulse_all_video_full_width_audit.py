#!/usr/bin/env python3
"""Audit the shared Pulse all-video full-width contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_cinematic_media.css").read_text(encoding="utf-8")
STATUS = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


marker = "Pulse all-video full-width contract"
expect(marker in CSS, "final all-video full-width contract exists")
final = CSS[CSS.rfind(marker):]
for token in [
    ".post .pulse-media-wrap.media-kind-video",
    ".video-thumb .pulse-media-wrap.media-kind-video",
    ".video-detail-player.pulse-media-wrap.media-kind-video",
    ".profile-post .pulse-media-wrap.media-kind-video",
    ".saved-post .pulse-media-wrap.media-kind-video",
    ".creator-studio-preview .pulse-media-wrap.media-kind-video",
    ".live-replay-card .pulse-media-wrap.media-kind-video",
]:
    expect(token in final, f"contract covers {token}")
expect("max-width: none !important" in final, "legacy video max-width caps are overridden")
expect("width: 100% !important" in final, "videos fill available width")
expect("max-width: 480px" not in final and "max-width: 520px" not in final, "final contract has no 480/520px caps")
expect(".pulse-status-story-media .pulse-media-wrap" in STATUS, "Status viewer media fills viewer")
print("pulse all video full width audit ok")
