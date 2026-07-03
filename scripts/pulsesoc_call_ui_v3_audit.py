#!/usr/bin/env python3
"""Static audit for PulseSoc Messenger header and active call UI V3."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "pulse_messages_v2.html"
CALL_JS = ROOT / "static" / "pulsesoc_calls.js"
MESSENGER_JS = ROOT / "static" / "js" / "pulse_messages_v2.js"
CSS = ROOT / "static" / "css" / "pulse_messages_v2.css"
REPORT = ROOT / "reports" / "pulsesoc_call_ui_v3_redesign.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def header_actions(template: str) -> str:
    start = template.find('<nav class="thread-actions"')
    end = template.find("</nav>", start)
    return template[start:end] if start >= 0 and end >= 0 else ""


def main() -> int:
    checks: list[dict] = []
    template = read(TEMPLATE)
    header = header_actions(template)
    call_js = read(CALL_JS)
    messenger_js = read(MESSENGER_JS)
    css = read(CSS)
    report = read(REPORT) if REPORT.exists() else ""

    require(checks, "conversation header exists", bool(header))
    require(checks, "header removes duplicate search action", "data-thread-search" not in header)
    require(checks, "header removes gear settings action", "control-center-action" not in header and "&#9881;" not in header)
    require(checks, "header exposes exactly one audio call action", header.count("data-thread-call-audio") == 1)
    require(checks, "header exposes exactly one video call action", header.count("data-thread-call-video") == 1)
    require(checks, "header call actions use vector icons", "call-icon" in header and "<svg" in header)
    require(checks, "header exposes one More control center entry", header.count("data-thread-more") == 1 and "data-open-control-center" in header)
    require(checks, "header has no desktop info duplicate", "data-toggle-details" not in header)
    require(checks, "header call buttons use central service", "PulseSocCalls.startAudioCall" in messenger_js and "PulseSocCalls.startVideoCall" in messenger_js)
    require(checks, "inactive header buttons are disabled safely", "[data-thread-call-audio], [data-thread-call-video], [data-thread-more]" in messenger_js and ".is-disabled" in css)
    require(checks, "presence state enriches header", "data-presence" in messenger_js and "pulsePresenceBreath" in css)
    require(checks, "mobile header keeps call actions visible", ".thread-actions .call-action {\n    display: none;" not in css and ".thread-actions .call-action{display:none" not in css)

    require(checks, "active call shell exists", "data-pulsesoc-call-shell" in call_js and ".pulsesoc-call-shell" in css)
    require(checks, "full-screen call stage exists", "data-call-stage" in call_js and ".pulsesoc-call-stage" in css and "inset: 0" in css)
    require(checks, "primary end button always exists", "pulsesoc-call-end-primary" in call_js and "data-call-end" in call_js)
    require(checks, "hidden controls panel exists", "data-call-controls-panel" in call_js and "showControls" in call_js)
    require(checks, "tap/click reveal controls exists", "toggleControls" in call_js and "data-call-interaction-zone" in call_js)
    require(checks, "controls auto-hide exists", "scheduleControlsHide" in call_js and "3000" in call_js)
    require(checks, "audio mode has dedicated visual", "data-call-audio-visual" in call_js and "pulseAudioOrb" in css)
    require(checks, "video mode keeps remote video dominant", "pulsesoc-call-remote-video" in call_js and "object-fit: cover" in css)
    require(checks, "local preview exists", "data-call-local-wrap" in call_js and ".pulsesoc-call-local-wrap" in css)
    require(checks, "mic control remains wired", "toggleMicrophone" in call_js and "data-call-toggle-mic" in call_js and "mute-audio" in call_js)
    require(checks, "camera control remains wired", "toggleCamera" in call_js and "data-call-toggle-camera" in call_js and "disable-video" in call_js)
    require(checks, "camera flip remains wired", "switchCamera" in call_js and "data-call-switch-camera" in call_js)
    require(checks, "speaker remains wired to safe device state", "switchSpeaker" in call_js and "data-call-switch-speaker" in call_js)
    require(checks, "minimize/restore remains wired", "minimizeCall" in call_js and "data-call-restore" in call_js)
    require(checks, "quality/reconnect pill remains visible", "data-call-quality" in call_js and "Reconnecting" in call_js)
    require(checks, "keyboard escape hides controls", 'event.key === "Escape"' in call_js)
    require(checks, "safe area CSS exists", "env(safe-area-inset-top)" in css and "env(safe-area-inset-bottom)" in css)
    require(checks, "reduced motion support remains", "prefers-reduced-motion" in css)
    require(checks, "no internal names exposed in Messenger UI files", "LogiNexus" not in template and "LogiNexus" not in css and "LogiNexus" not in call_js)
    require(checks, "V3 report exists", "PulseSoc Call UI V3" in report)

    failed = [check for check in checks if not check["passed"]]
    print(json.dumps({"ok": not failed, "passed": len(checks) - len(failed), "failed": failed, "total": len(checks)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
