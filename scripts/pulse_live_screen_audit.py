#!/usr/bin/env python3
"""Audit the shared immersive PulseSoc Live screen V1."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_live_studio_runtime.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_live_studio.css").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main() -> int:
    require("live-screen-v1" in BOT and ".live-screen-v1" in CSS, "shared immersive Live screen exists")
    require("live-screen-grid" in BOT and "live-theater" in BOT, "Live screen uses desktop/tablet/mobile layout zones")
    require("live-badge" in BOT and "data-live-viewers" in BOT, "LIVE badge and real viewer count hook exist")
    require("live-host-identity" in BOT and "data-live-health" in BOT, "top header has host identity and health")
    require("live-hero-player" in BOT and "object-fit: cover" in CSS, "video remains the hero")
    require("live-guest-stack" in BOT and "data-live-guest-sidecar" in BOT, "guest stack and panel exist")
    require("data-live-join-request" in BOT and "requestJoinLive" in JS, "Join Live action is wired")
    require("data-live-chat-feed" in BOT and "data-live-chat-send" in BOT, "chat composer exists")
    require("live-view-action-bar" in BOT and "data-live-share" in BOT, "action bar and share action exist")
    require("data-live-gift-action disabled" in BOT and "data-live-poll-action disabled" in BOT, "gift/poll have safe unavailable states")
    require("Only the live host can manage join requests" in BOT and "Open Studio" in BOT, "host controls are permission-gated")
    require("live_camera_started" in BOT and "data-live-stream-health" in BOT, "stream health hooks connect to Live Health Manager")
    require("2,481" not in BOT and "fake viewer" not in BOT.lower(), "screen avoids fake viewer count literals")
    require("fake gift" not in BOT.lower() and "gift success" not in BOT.lower(), "screen avoids fake gift success")
    require('href="#"' not in BOT and "javascript:void(0)" not in BOT, "no dead href/hash or javascript void links")
    require("@media (max-width: 680px)" in CSS and "safe-area-inset-bottom" in CSS, "mobile safe-area rules exist")
    print("pulse live screen audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
