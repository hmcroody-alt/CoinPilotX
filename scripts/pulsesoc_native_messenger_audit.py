#!/usr/bin/env python3
"""Static audit for the PulseSoc native Messenger foundation."""

from __future__ import annotations

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


def require_any(text: str, needles: tuple[str, ...], label: str) -> None:
    if not any(needle in text for needle in needles):
        raise AssertionError(f"Missing {label}: one of {needles}")


def main() -> int:
    report = read("reports/pulsesoc_native_messenger_progress.md")
    api = read("mobile-native/src/api/messenger.ts")
    messenger_screen = read("mobile-native/src/screens/MessengerScreen.tsx")
    chat_screen = read("mobile-native/src/screens/ChatScreen.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    app = read("mobile-native/App.tsx")

    for phrase in (
        "Reuse-First Implementation",
        "Communications V2 compatibility",
        "Not Yet Device-Verified",
        "does not change the production WebView app",
    ):
        require(report, phrase, "Messenger progress report coverage")

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
        require(api, route, "reused Messenger API route")

    for token in (
        "AsyncStorage",
        "normalizeConversations",
        "normalizeMessages",
        "createLocalMessage",
        "cacheMessages",
    ):
        require(api, token, "native Messenger API helper")

    for token in (
        "FlatList",
        "RefreshControl",
        "searchMessenger",
        "loadCachedConversations",
        "navigation.navigate(\"Chat\"",
    ):
        require(messenger_screen, token, "conversation list behavior")

    for token in (
        "FlatList",
        'Keyboard.addListener("keyboardWillShow"',
        "keyboardHeight",
        "sendTyping",
        "markConversationSeen",
        "syncConversation",
        "uploadMessengerMedia",
        "retryMessage",
        "Image",
        "Voice message",
        "File attachment",
        "DocumentPicker",
        "ImagePicker",
        "Audio.Recording",
    ):
        require(chat_screen, token, "conversation screen behavior")

    require(linking, "pulse/messages/:conversationId", "Messenger deep link")
    require(app, 'linking={authState.status === "signedIn" ? linking : undefined}', "auth-gated navigation linking registration")

    messenger_native = "\n".join((api, messenger_screen, chat_screen, linking))
    require_any(messenger_native, ("React Native", "react-native"), "native implementation")
    if "WebView" in messenger_native or "react-native-webview" in messenger_native:
        raise AssertionError("Native Messenger foundation must not introduce WebView coupling.")

    print("PulseSoc native Messenger audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
