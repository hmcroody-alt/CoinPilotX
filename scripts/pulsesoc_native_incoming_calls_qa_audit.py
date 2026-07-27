#!/usr/bin/env python3
"""Practical QA audit for PulseSoc native incoming calls."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"Missing {label}: {needle}")


def forbid(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise AssertionError(f"Forbidden {label}: {needle}")


def main() -> int:
    layer = read("mobile-native/src/calls/IncomingCallLayer.tsx")
    qa = read("mobile-native/src/calls/incomingCallQa.ts")
    app = read("mobile-native/App.tsx")
    call_screen = read("mobile-native/src/screens/CallScreen.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    qa_report = read("reports/pulsesoc_native_incoming_calls_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    require(app, "<IncomingCallLayer", "app-shell incoming layer")
    require(layer, "ringSeenCalls", "ring-seen once tracking")
    require(layer, "!ringSeenCalls.current.has(ringing.call_id)", "ring-seen duplicate guard")
    require(layer, "ringSeenCalls.current.add(ringing.call_id)", "ring-seen guard insertion")
    require(layer, "ringSeenCalls.current.clear()", "ring-seen reset on sign-out")
    require(layer, "seedQaCallFromUrl", "QA fixture seeding hook")
    require(layer, "Linking.getInitialURL()", "initial URL fixture handling")
    require(layer, "Linking.addEventListener", "runtime URL fixture handling")
    require(layer, "ignoredCalls.current.set", "local ignore/remind suppression")
    require(layer, "navigationRef.navigate(\"Call\"", "accept/restore to CallScreen")

    for snippet in [
        "__DEV__",
        "isLocalApiBaseUrl(PULSE_API_BASE_URL) || isLocalUrl(url)",
        "qa_incoming_call",
        "qa_active_call",
        "PulseSoc QA",
    ]:
        require(qa, snippet, "dev/local QA fixture safety")

    require(call_screen, "navigation.goBack()", "minimized call previous-screen restore")
    require(linking, 'path: "pulse/calls/:callId?"', "calls deep-link route")
    require(notification_routing, "call_id", "notification call routing")

    for phrase in [
        "HTTP/1.1 200 OK",
        "browser session was signed out",
        "duplicate `ring-seen`",
        "release blockers, not current development blockers",
    ]:
        require(qa_report, phrase, "honest incoming calls QA report")

    require(progress, "Native Incoming Calls Practical QA", "progress QA completion")
    if re.search(r"<Text[^>]*>[^<]*LogiNexus", layer):
        raise AssertionError("Forbidden user-facing internal LogiNexus copy in layer")
    if re.search(r"<Text[^>]*>[^<]*LogiNexus", call_screen):
        raise AssertionError("Forbidden user-facing internal LogiNexus copy in CallScreen")

    print("PulseSoc native incoming calls practical QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
