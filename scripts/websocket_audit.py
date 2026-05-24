#!/usr/bin/env python3
"""Pulse realtime/websocket fallback audit."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import realtime_engine, websocket_orchestrator  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/pulse_realtime_infra_audit.py")], cwd=str(ROOT), text=True, capture_output=True)
    expect(result.returncode == 0, "realtime infrastructure audit", result.stdout + result.stderr)
    reg = websocket_orchestrator.register("audit-ws-1", user_id=930001, channel="pulse:conversation:audit", transport="websocket")
    expect(reg.get("ok") is True, "websocket register")
    hb = websocket_orchestrator.heartbeat("audit-ws-1", user_id=930001, channel="pulse:conversation:audit", transport="websocket")
    expect(hb.get("ok") is True, "websocket heartbeat")
    realtime_engine.publish_event("pulse:conversation:audit", "message_ack", {"conversation_id": 1, "sequence": 1})
    events = realtime_engine.poll_events("pulse:conversation:audit", after_id=0, limit=5)
    expect(any(e.get("event_type") == "message_ack" for e in events), "reconnect replay buffer returns events")
    health = websocket_orchestrator.health_snapshot()
    expect("policy" in health and "status" in health, "websocket health policy present", str(health))
    print("websocket audit ok")


if __name__ == "__main__":
    main()
