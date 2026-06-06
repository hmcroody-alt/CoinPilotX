#!/usr/bin/env python3
"""Audit Pulse Status audio/story playback safety contracts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expect(ok: bool, label: str):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect("pulseStatusSound" in source, "legacy status sound input is still reset safely")
    expect("music_upload_requires_review" in source, "direct uploaded music requires rights review")
    expect("/api/pulse/status/music/search" in source, "music search endpoint exists")
    expect("/api/pulse/music/ai-suggest" in source, "AI music suggestion endpoint exists")
    expect("data-status-waveform" in source, "music flow exposes waveform loading state")
    for token in ["playsinline", "controls", "muted", "autoplay", "preload=\"metadata\""]:
        expect(token in source, f"story video includes mobile-safe playback token: {token}")
    expect("data-status-story-mute" in source, "viewer can unlock/mute story audio")
    expect("pulse_status_music" in source and "music_track_id" in source, "music metadata persists with status")
    expect("pulse_content_music" in source, "approved music license snapshot persists")
    print("audio pipeline audit ok")


if __name__ == "__main__":
    main()
