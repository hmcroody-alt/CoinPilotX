#!/usr/bin/env python3
"""Audit Pulse Live health/presence service primitives."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import live_health_service, live_presence_engine, live_stream_health_service  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    session = {"id": 7, "status": "live", "bitrate_kbps": 3200, "fps": 30}
    health = live_stream_health_service.score_stream(session, viewer_count=8, chat_count=4)
    require(health["score"] >= 80, "healthy stream receives high score")
    require(health["level"] in {"excellent", "stable", "warming"}, "health level is classified")
    hints = live_stream_health_service.recovery_hints({"score": 20, "bitrate_kbps": 0, "fps": 0})
    require(bool(hints), "recovery hints are available for weak streams")
    pulse = live_presence_engine.audience_pulse(viewer_count=6, chat_count=3, reaction_count=5, bitrate_kbps=3200, fps=30)
    require(pulse["score"] > 0 and pulse["label"] in {"ready", "warming", "surging"}, "audience pulse produces energy state")
    snapshot = live_health_service.health_snapshot(session, viewer_count=4, chat_count=2)
    require("recovery_hints" in snapshot and "auto_recovery" in snapshot, "health facade exposes self-healing hints")
    print("live stream health audit ok")


if __name__ == "__main__":
    main()
