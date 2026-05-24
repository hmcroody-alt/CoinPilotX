#!/usr/bin/env python3
"""Pulse chat realtime recovery audit."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import websocket_orchestrator  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    recovery_js = (ROOT / "static/js/pulse_chat_recovery.js").read_text(encoding="utf-8")
    expect("EventSource" in bot_source or "WebSocket" in bot_source, "chat has realtime transport hook")
    expect("Reconnecting securely" in bot_source and "Messages syncing" in bot_source, "realtime recovery copy is present")
    expect("online" in recovery_js and "offline" in recovery_js, "client recovery watches network transitions")
    registered = websocket_orchestrator.register("chat-realtime-audit", 990001, "pulse:conversation:audit")
    expect(registered.get("ok") and registered.get("reconnect_token"), "websocket reconnect token registered", str(registered))
    ack = websocket_orchestrator.acknowledge("chat-realtime-audit", event_id=1, sequence=1)
    expect(ack.get("ok"), "delivery acknowledgement accepted", str(ack))
    replay = websocket_orchestrator.reconnect("chat-realtime-audit", registered["reconnect_token"], "pulse:conversation:audit")
    expect(replay.get("ok"), "websocket reconnect succeeds", str(replay))
    health = websocket_orchestrator.health_snapshot()
    expect((health.get("recovery") or {}).get("replay_ready") and health.get("delivery_acknowledgements", 0) >= 1, "realtime recovery advertises replay and ack support", str(health))
    print("chat realtime audit ok")


if __name__ == "__main__":
    main()
