#!/usr/bin/env python3
"""Audit Pulse Camera mobile gesture support."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static/js/pulse_camera_engine.js"
CSS = ROOT / "static/css/pulse_camera_engine.css"


def expect(ok, label):
    if not ok:
        raise AssertionError(f"{label} failed")
    print(f"ok - {label}")


def main():
    js = JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    for token, label in [
        ("touchstart", "touch start tracking"),
        ("touchmove", "touch move tracking"),
        ("touchend", "touch end tracking"),
        ("initialPinchDistance", "two-finger pinch zoom"),
        ("lastTap", "double tap camera flip"),
        ("showFocus", "tap focus ring"),
        ("pointerdown", "hold-to-record start"),
        ("pointerup", "hold-to-record stop"),
        ("fileInput.click", "swipe up gallery fallback"),
        ("closeCamera", "swipe down close"),
        ("setZoom", "zoom control"),
    ]:
        expect(token in js, label)
    for token, label in [
        ("touch-action: none", "camera captures gestures safely"),
        ("--pulse-camera-zoom", "GPU zoom variable"),
        ("pulse-camera-focus-ring", "focus ring styling"),
    ]:
        expect(token in css, label)
    print("pulse camera gesture audit ok")


if __name__ == "__main__":
    main()
