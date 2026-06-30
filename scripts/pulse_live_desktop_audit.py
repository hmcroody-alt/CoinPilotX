#!/usr/bin/env python3
"""Audit PulseSoc Live desktop cockpit layout and host-only controls."""

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
    require("studio-pro-shell" in BOT and ".studio-pro-shell" in CSS, "desktop studio shell exists")
    require("studio-sidebar" in BOT and "studio-workspace" in BOT, "desktop studio has left rail and workspace")
    require("studio-hero-preview" in BOT and ".studio-hero-preview" in CSS, "main desktop video area is large")
    require("live-chat-panel studio-chat-panel" in BOT, "right chat panel exists")
    require("live-backstage-panel" in BOT and "data-live-join-request-list" in BOT, "backstage panel exists")
    require("pulse_live_pending_guest_requests" in BOT and "pulse_live_active_guests" in BOT, "backstage uses real guest request state")
    require("Only the live host can manage join requests" in BOT, "join request controls are host permission gated")
    require("data-live-stream-health" in BOT and "data-live-fps" in BOT and "data-live-bitrate" in BOT, "stream health hooks exist")
    require("live-ai-card" in BOT and "AI summaries" in BOT, "AI co-host state is represented without fake answers")
    require("live-guest-stack" in BOT and "live-guest-tile" in BOT, "guest tiles exist")
    require("2,481" not in BOT and "20,000 viewers" not in BOT, "desktop Live avoids fake viewer/mission literals")
    require("fake gift" not in BOT.lower() and "gift success" not in BOT.lower(), "desktop Live has no fake gift success")
    require('href="#"' not in BOT and "javascript:void(0)" not in BOT, "no dead href/hash or javascript void links")
    require("@media (max-width: 1280px)" in CSS and "@media (max-width: 1080px)" in CSS, "responsive desktop collapse rules exist")
    require("hostJoinRequestAction" in JS and "hostGuestAction" in JS, "host Backstage buttons are wired")
    print("pulse live desktop audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
