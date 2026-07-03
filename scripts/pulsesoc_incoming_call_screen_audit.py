#!/usr/bin/env python3
"""Static audit for the PulseSoc incoming call screen redesign."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALL_JS = ROOT / "static" / "pulsesoc_calls.js"
CSS = ROOT / "static" / "css" / "pulse_messages_v2.css"
TEMPLATE = ROOT / "templates" / "pulse_messages_v2.html"
REPORT = ROOT / "reports" / "pulsesoc_incoming_call_screen_redesign.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[tuple[str, bool]], name: str, ok: bool) -> None:
    checks.append((name, ok))


def main() -> int:
    call_js = read(CALL_JS)
    css = read(CSS)
    template = read(TEMPLATE)
    report = read(REPORT) if REPORT.exists() else ""
    checks: list[tuple[str, bool]] = []

    require(checks, "incoming buttons use icon and label structure", "data-call-accept" in call_js and "data-call-decline" in call_js and "<b>Accept</b>" in call_js and "<b>Decline</b>" in call_js)
    require(checks, "incoming fallback renders structured Pulse copy", "renderIncomingFallback" in call_js and "pulsesoc-call-incoming-copy" in call_js)
    require(checks, "incoming actions have dedicated safe-area layout", 'data-call-mode="incoming"] .pulsesoc-call-actions' in css and "env(safe-area-inset-bottom)" in css)
    require(checks, "incoming buttons are large touch targets", "min-height: 72px" in css and "width: clamp(78px" in css)
    require(checks, "decline no longer shares active end absolute rule", ".pulsesoc-call-actions .is-decline,\n.pulsesoc-call-end-primary" not in css)
    require(checks, "accept and decline have separate visual states", ".pulsesoc-call-actions .is-accept" in css and ".pulsesoc-call-actions .is-decline" in css)
    require(checks, "incoming pulse animations exist", all(token in css for token in ["pulseIncomingCore", "pulseIncomingWave", "pulseIncomingAccept", "pulseIncomingText"]))
    require(checks, "mobile and landscape incoming rules exist", "@media (max-width: 360px)" in css and "orientation: landscape" in css)
    require(checks, "cache bust points to incoming redesign", "incoming-call-screen-v4-20260703" in template)
    require(checks, "report exists", "Incoming Call Screen Redesign" in report)

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
