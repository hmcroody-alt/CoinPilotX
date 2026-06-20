#!/usr/bin/env python3
"""Guard PulseSoc web capacity against legacy long-lived browser streams."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    realtime = (ROOT / "static/js/pulse_realtime.js").read_text(encoding="utf-8")
    messages = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")

    require('PULSE_LEGACY_SSE_ENABLED' in bot, "legacy SSE is controlled explicitly")
    require('return Response(status=204' in bot, "disabled legacy SSE releases request capacity")
    require('function startLive(){setInterval(' in bot, "Home retains polling fallback")
    require('new EventSource(`/api/pulse/live/stream' not in bot, "web pages do not occupy legacy stream workers")
    require('dataset.pulseLegacySse === "enabled"' in realtime, "browser SSE is opt-in")
    require('if (document.hidden) {' in realtime and 'disconnect();' in realtime, "background tabs release realtime connections")
    require('scheduleRealtimePoll(12000)' in (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8"), "Messages polling fallback remains active")
    require('pulse_realtime.js?v=web-capacity-20260619a' in messages, "PWA receives the capacity-safe realtime client")
    print("pulse PWA capacity audit ok")


if __name__ == "__main__":
    main()
