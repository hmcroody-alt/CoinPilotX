#!/usr/bin/env python3
"""Audit the PulseSoc native Messenger device QA hardening pass."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"Missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    report = read("reports/pulsesoc_native_messenger_device_qa.md")
    chat = read("mobile-native/src/screens/ChatScreen.tsx")
    messenger_api = read("mobile-native/src/api/messenger.ts")
    messenger_screen = read("mobile-native/src/screens/MessengerScreen.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")

    for phrase in (
        "Do not add another major feature",
        "does not change production WebView paths",
        "Server APIs remain authoritative",
        "could not be completed in this environment",
        "No device-only item is marked as passed",
        "not device-verified",
        "Current Release Position",
    ):
        require(phrase in report, f"report must include honest QA language: {phrase}")

    for area in (
        "Long conversation scrolling performance",
        "Conversation search",
        "Pull-to-refresh",
        "Offline cache restore",
        "Send message",
        "Failed-send retry",
        "Read receipts / seen calls",
        "Typing indicator",
        "Sync polling",
        "Push deep link into conversation",
        "Image picker upload",
        "File picker upload",
        "Voice recording upload",
        "Permission denied states",
        "Large attachments",
        "Upload failure handling",
        "App foreground/background recovery",
    ):
        require(area in report, f"report must cover QA area: {area}")

    for route in (
        "/api/pulse/messages/conversations",
        "/api/pulse/messages/${conversationId}/messages",
        "/api/pulse/messages/${conversationId}/send",
        "/api/pulse/messages/${conversationId}/sync",
        "/api/pulse/messages/${conversationId}/typing",
        "/api/pulse/messages/${conversationId}/seen",
        "/api/pulse/messages/search",
        "/api/pulse/messages/media/upload",
    ):
        require(route in messenger_api, f"Messenger must reuse backend route: {route}")

    for token in (
        "try {",
        "AsyncStorage.removeItem(CONVERSATION_CACHE_KEY)",
        "AsyncStorage.removeItem(key)",
        "return []",
    ):
        require(token in messenger_api, f"cache restore must degrade safely: {token}")

    for token in (
        "AppState",
        "appState.current !== \"active\"",
        "AppState.addEventListener",
        "initialNumToRender",
        "maxToRenderPerBatch",
        "removeClippedSubviews",
        "windowSize",
        "visibleMessages",
        "uploading",
        "setUploading(true)",
        "setUploading(false)",
        "Alert.alert(\"Photos unavailable\"",
        "Alert.alert(\"Microphone unavailable\"",
        "Alert.alert(\"Attachment failed\"",
        "retryMessage",
        "markConversationSeen",
        "sendTyping",
        "syncConversation",
        "uploadMessengerMedia",
    ):
        require(token in chat, f"Chat hardening token missing: {token}")

    for token in (
        "RefreshControl",
        "searchMessenger",
        "loadCachedConversations",
    ):
        require(token in messenger_screen, f"Messenger list QA token missing: {token}")

    require("pulse/messages/:conversationId" in linking, "deep link route must target conversations")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("WebView" not in mobile_native and "react-native-webview" not in mobile_native.lower(), "native Messenger must not introduce WebView")

    print("PulseSoc native Messenger device QA audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
