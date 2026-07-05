#!/usr/bin/env python3
"""Audit the PulseSoc native calls foundation.

The audit intentionally checks for reuse of the existing server-authoritative
call API and for native routing without touching the production WebView app.
"""

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


def main() -> int:
    calls_api = read("mobile-native/src/api/calls.ts")
    call_screen = read("mobile-native/src/screens/CallScreen.tsx")
    call_hook = read("mobile-native/src/calls/useNativeCallRoom.ts")
    chat = read("mobile-native/src/screens/ChatScreen.tsx")
    nav_types = read("mobile-native/src/navigation/types.ts")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    report = read("reports/pulsesoc_native_calls_progress.md")

    for endpoint in [
        "/api/calls/start",
        "/api/calls/active",
        "/api/calls/${encodeURIComponent(callId)}/accept",
        "/api/calls/${encodeURIComponent(callId)}/join-token",
        "/api/calls/${encodeURIComponent(callId)}/status",
        "/api/calls/${encodeURIComponent(callId)}/connected",
        "/api/conversations/${encodeURIComponent(String(conversationId))}/calls",
        "/api/pulse/comm/v2/conversations/${encodeURIComponent(String(conversationId))}/",
    ]:
        require(calls_api, endpoint, "call API endpoint wrapper")

    for action in [
        "mute-audio",
        "unmute-audio",
        "enable-video",
        "disable-video",
        "switch-camera",
        "speaker",
        "minimize",
        "restore",
        "visibility",
    ]:
        require(calls_api, action, "call control action")

    require(call_hook, 'Platform.OS === "web"', "web-safe LiveKit guard")
    require(call_hook, 'await import("@livekit/react-native")', "dynamic native LiveKit import")
    require(call_hook, 'await import("livekit-client")', "dynamic LiveKit client import")
    require(call_hook, "registerGlobals", "LiveKit native globals registration")

    for snippet in [
        "startConversationCall",
        "acceptCall",
        "declineCall",
        "endCall",
        "requestCallJoinToken",
        "markCallConnected",
        "sendCallControl",
        "openCallWebFallback",
    ]:
        require(call_screen, snippet, "CallScreen server-authoritative flow")

    require(chat, 'navigation.navigate("Call"', "Chat call entry points")
    require(nav_types, "Call:", "Call route type")
    require(app_nav, 'name="Call"', "Call stack route")
    require(linking, 'path: "pulse/calls/:callId?"', "Call deep-link route")
    require(notification_routing, "call_id", "existing call notification query routing")
    require(notification_routing, 'navigationRef.navigate("Call"', "native call notification route")

    for phrase in [
        "production WebView app",
        "Communications V2 call engine",
        "LiveKit token generation",
        "release blockers, not development blockers",
    ]:
        require(report, phrase, "calls progress report honesty")

    print("PulseSoc native calls audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
