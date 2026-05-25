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
    expect("pulseStatusSound" in source, "status sound input exists")
    expect("audio/mpeg,audio/mp4,audio/wav,audio/ogg" in source, "status audio accepts safe browser formats")
    expect("/api/pulse/status/music/search" in source, "music search endpoint exists")
    expect("data-status-waveform" in source, "music flow exposes waveform loading state")
    expect("playsinline controls muted autoplay preload=\"metadata\"" in source, "story video uses mobile-safe playback attributes")
    expect("data-status-story-mute" in source, "viewer can unlock/mute story audio")
    expect("pulse_status_music" in source and "music_track_id" in source, "music metadata persists with status")
    print("audio pipeline audit ok")


if __name__ == "__main__":
    main()
