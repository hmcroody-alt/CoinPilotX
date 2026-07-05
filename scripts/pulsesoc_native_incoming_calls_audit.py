#!/usr/bin/env python3
"""Audit the PulseSoc native full-screen incoming calls foundation."""

from pathlib import Path

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
    app = read("mobile-native/App.tsx")
    layer = read("mobile-native/src/calls/IncomingCallLayer.tsx")
    call_screen = read("mobile-native/src/screens/CallScreen.tsx")
    calls_api = read("mobile-native/src/api/calls.ts")
    report = read("reports/pulsesoc_native_incoming_calls_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    require(app, "<IncomingCallLayer", "app-shell incoming call mount")
    require(app, "authState.status === \"signedIn\"", "signed-in only mount")

    for snippet in [
        "getActiveCalls",
        "markRingSeen",
        "acceptCall",
        "declineCall",
        "endCall",
        "Notifications.addNotificationReceivedListener",
        "AppState.addEventListener",
        "navigationRef.navigate(\"Call\"",
        "Silent ignore",
        "Remind me later",
        "Incoming PulseSoc call",
        "Open active call",
        "End active call",
    ]:
        require(layer, snippet, "incoming call layer behavior")

    for endpoint in [
        "/api/calls/active",
        "/api/calls/${encodeURIComponent(callId)}/accept",
        "/api/calls/${encodeURIComponent(callId)}/decline",
        "/api/calls/${encodeURIComponent(callId)}/end",
        "/api/calls/${encodeURIComponent(callId)}/ring-seen",
    ]:
        require(calls_api, endpoint, "server-authoritative call endpoint")

    require(call_screen, "navigation.goBack()", "CallScreen minimize restores previous screen")
    require(call_screen, "minimize", "CallScreen minimize control")

    for phrase in [
        "backend remains authoritative",
        "production WebView",
        "full-screen PulseSoc call surface",
        "floating call bubble",
        "release blockers, not development blockers",
    ]:
        require(report, phrase, "incoming calls report")

    require(progress, "Full-screen incoming calls", "progress recommendation/remaining feature")
    forbid(layer, "LogiNexus", "user-facing internal LogiNexus copy in incoming call UI")
    forbid(call_screen, "LogiNexus", "user-facing internal LogiNexus copy in call screen")

    print("PulseSoc native incoming calls audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
