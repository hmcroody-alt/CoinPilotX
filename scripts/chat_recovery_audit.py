#!/usr/bin/env python3
"""Pulse chat local recovery and pending-send audit."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import chat_health_service  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main():
    bot.init_db()
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    recovery_js = (ROOT / "static/js/pulse_chat_recovery.js").read_text(encoding="utf-8")
    expect("pulseMessengerPendingV2" in bot_source and "enqueuePending" in bot_source, "pending send queue remains active")
    expect("PulseChatRecovery?.saveThread" in bot_source, "thread messages saved to recovery cache")
    expect("PulseChatRecovery?.saveList" in bot_source, "conversation lists saved to recovery cache")
    expect("localStorage" in recovery_js and "pending" in recovery_js, "client recovery persists state locally")
    payload = chat_health_service.chat_recovery_payload(mode="offline")
    expect(payload.get("fallback_polling") and payload.get("retryable"), "server recovery payload supports fallback polling", str(payload))
    print("chat recovery audit ok")


if __name__ == "__main__":
    main()
