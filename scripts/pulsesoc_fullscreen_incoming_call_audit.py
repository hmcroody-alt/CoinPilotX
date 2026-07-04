#!/usr/bin/env python3
"""Static audit for PulseSoc full-screen incoming calls across PulseSoc surfaces."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    return target.read_text(encoding="utf-8") if target.exists() else ""


def exists(path: str) -> bool:
    return (ROOT / path).exists()


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def main() -> int:
    call_js = read("static/pulsesoc_calls.js")
    global_js = read("static/js/pulsesoc_global_call_overlay.js")
    global_css = read("static/css/pulsesoc_global_call_overlay.css")
    bot = read("bot.py")
    routes = read("pulse_communications_v2/routes.py")
    report = read("reports/pulsesoc_fullscreen_incoming_call.md")

    checks = [
        check("global call overlay JS exists", exists("static/js/pulsesoc_global_call_overlay.js")),
        check("global call overlay CSS exists", exists("static/css/pulsesoc_global_call_overlay.css")),
        check("global overlay bootstraps existing call client", "pulsesoc_calls.js" in global_js and "PulseSocCalls" in global_js),
        check("global overlay connects communications realtime stream", "/api/pulse/communications/v2/realtime/stream" in global_js),
        check("global overlay loads LiveKit for accept flow", "livekit-client.umd.js" in global_js),
        check("global overlay pauses current media", "pauseActiveMedia" in global_js and "video, audio" in global_js),
        check("global overlay restores drafts", "preserveDrafts" in global_js and "restoreDrafts" in global_js),
        check("global overlay prevents duplicate visible states", "pulsesoc-global-call-interrupted" in global_js),
        check("full-screen incoming CSS exists", "data-call-mode=\"incoming\"" in global_css and "position: fixed" in global_css),
        check("safe area respected", "env(safe-area-inset-top)" in global_css and "env(safe-area-inset-bottom)" in global_css),
        check("accept and decline buttons styled separately", ".is-accept" in global_css and ".is-decline" in global_css),
        check("reduced motion support exists", "prefers-reduced-motion" in global_css),
        check("call client emits lifecycle events", all(token in call_js for token in [
            "pulsesoc:incoming-call",
            "pulsesoc:call-accepted",
            "pulsesoc:call-declined",
            "pulsesoc:call-terminal",
            "pulsesoc:call-interruption-ended",
        ])),
        check("incoming call starts status polling", "if (!state.statusTimer) startStatusPolling();" in call_js),
        check("duplicate incoming refresh is guarded", "alreadyShowing" in call_js and "incomingId" in call_js),
        check("ring seen route remains wired", "ring-seen" in call_js and "/api/calls/<path:call_id>/ring-seen" in routes),
        check("accept and decline routes remain wired", "/accept" in call_js and "/decline" in call_js and "def api_accept_call" in routes and "def api_decline_call" in routes),
        check("active call polling fallback remains", "pollActiveCalls" in call_js and "/active" in call_js),
        check("global script injected on authenticated PulseSoc surfaces", "pulsesoc_global_call_overlay.js" in bot and "call_overlay_allowed" in bot),
        check("global call CSS injected on authenticated PulseSoc surfaces", "pulsesoc_global_call_overlay.css" in bot),
        check("PulseSoc call surfaces allow camera and microphone policy", "pulse_call_surface" in bot and "camera=(self), microphone=(self)" in bot),
        check("report exists", bool(report.strip())),
    ]

    failed = [item for item in checks if not item["ok"]]
    payload = {"ok": not failed, "checks": checks, "failed": failed}
    print(json.dumps(payload, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
