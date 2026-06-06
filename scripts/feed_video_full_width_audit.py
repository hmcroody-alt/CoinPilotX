#!/usr/bin/env python3
"""Audit feed/profile/saved video width guardrails."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/pulse_cinematic_media.css").read_text(encoding="utf-8")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


final = CSS[CSS.rfind("Pulse all-video full-width contract"):]
expect(".post > .media-grid:has(.pulse-media-wrap.media-kind-video)" in final, "feed media grid fills post width")
expect(".post.card" in final and "--pulse-card-media-bleed" in final, "mobile feed uses controlled edge bleed")
expect(".profile-post .pulse-media-wrap.media-kind-video" in final, "profile video posts covered")
expect(".saved-post .pulse-media-wrap.media-kind-video" in final, "saved video posts covered")
expect("horizontal" not in final.lower(), "contract does not introduce horizontal scroll language")
print("feed video full width audit ok")
