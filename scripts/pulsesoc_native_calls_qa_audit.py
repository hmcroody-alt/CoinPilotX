#!/usr/bin/env python3
"""Practical QA audit for the PulseSoc native calls foundation."""

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
    chat = read("mobile-native/src/screens/ChatScreen.tsx")
    call_screen = read("mobile-native/src/screens/CallScreen.tsx")
    calls_api = read("mobile-native/src/api/calls.ts")
    call_hook = read("mobile-native/src/calls/useNativeCallRoom.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    qa_report = read("reports/pulsesoc_native_calls_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    require(chat, 'accessibilityLabel="Start voice call"', "Messenger voice call button")
    require(chat, 'accessibilityLabel="Start video call"', "Messenger video call button")
    require(chat, 'callType: "audio"', "voice call navigation payload")
    require(chat, 'callType: "video"', "video call navigation payload")

    for control in [
        "Accept",
        "Decline",
        "End",
        "Mute",
        "Video Off",
        "Speaker",
        "Flip",
        "Minimize",
        "Open safe web fallback",
    ]:
        require(call_screen, control, "CallScreen control")

    for state in [
        "Call could not load.",
        "Call could not start.",
        "Call could not be answered.",
        "No active calls returned by `/api/calls/active`.",
    ]:
        require(call_screen, state, "CallScreen loading/error state")

    for endpoint in [
        "/api/pulse/comm/v2/conversations/",
        "/api/calls/${encodeURIComponent(callId)}/accept",
        "/api/calls/${encodeURIComponent(callId)}/decline",
        "/api/calls/${encodeURIComponent(callId)}/end",
        "/api/calls/${encodeURIComponent(callId)}/join-token",
    ]:
        require(calls_api, endpoint, "server-authoritative call API")

    require(call_hook, 'Platform.OS === "web"', "LiveKit unsupported web guard")
    require(call_hook, "Native LiveKit calls require an installed iOS or Android build.", "web fallback message")
    require(linking, 'path: "pulse/calls/:callId?"', "calls deep-link path")
    require(notification_routing, "call_id", "message call notification support")
    require(notification_routing, 'normalized.match(/^\\/pulse\\/calls\\/([^/?#]+)/)', "direct call notification route")

    for phrase in [
        "200 OK",
        "release blockers, not current development blockers",
        "Production WebView routes were not modified",
        "User-facing copy does not expose `LogiNexus`",
        "No critical, security, data-loss, or production-breaking issues were found",
    ]:
        require(qa_report, phrase, "calls QA report evidence")

    require(progress, "Native Calls foundation", "progress calls entry")
    forbid(call_screen, "LogiNexus", "user-facing LogiNexus copy in CallScreen")
    forbid(chat, "LogiNexus", "user-facing LogiNexus copy in ChatScreen")

    print("PulseSoc native calls practical QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
