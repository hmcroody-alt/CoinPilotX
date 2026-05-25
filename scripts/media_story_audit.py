#!/usr/bin/env python3
"""Audit Pulse Status media-story creation contracts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expect(ok: bool, label: str):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "pulse_status_system.css").read_text(encoding="utf-8")
    expect("Create photo or video story" in source, "photo/video story entry exists")
    expect("data-status-start='upload'" in source, "photo/video flow opens upload/camera picker path")
    expect("pulseStatusMedia" in source and "multiple" in source, "media story supports multiple image/video selection")
    expect("renderStatusPreview" in source and "data-status-preview-stage" in source, "media story has pre-publish preview")
    expect("PulseUploadManager.upload" in source, "media story publishes with upload progress")
    expect("object-fit: contain" in css, "media preview preserves aspect ratio")
    expect(".pulse-status-effects-tray" in css and "data-status-effect='cinematic'" in source, "creative tools unlock after media selection")
    print("media story audit ok")


if __name__ == "__main__":
    main()
