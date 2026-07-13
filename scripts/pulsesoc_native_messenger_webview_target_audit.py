#!/usr/bin/env python3
"""Focused production-target audit for native Pulse Command Messenger."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    production = read("templates/pulse_messages_v2.html")
    inbox = read("mobile-native/src/screens/MessengerScreen.tsx")
    chat = read("mobile-native/src/screens/ChatScreen.tsx")
    control = read("mobile-native/src/components/ConversationControlCenter.tsx")
    api = read("mobile-native/src/api/messenger.ts")

    for token in ("Conversation Control Center", "data-open-control-center", "data-conversation-control-center", "New Chat", "Create Group", "Start Room"):
        require(token in production, f"production target missing: {token}")
    for token in ('label: "All"', 'label: "Direct"', 'label: "Groups"', 'label: "Rooms"', 'label: "AI"', 'label: "Unread"', "loadCachedConversations", "searchMessenger"):
        require(token in inbox, f"native inbox parity missing: {token}")
    for token in ("ConversationControlCenter", 'label="More"', 'qaChatState === "control-center"', "drainMessengerQueue", "enqueueMessengerMessage", "MessageActionSheet", "AttachmentActionSheet", "toggleVoiceRecording", "keyboardHeight", "syncConversation"):
        require(token in chat, f"native conversation parity missing: {token}")
    for section in ("Conversation", "Notifications", "Appearance", "Privacy", "Media", "Productivity", "Storage", "Security", "Accessibility", "Danger Zone"):
        require(section in control, f"Control Center section missing: {section}")
    for token in ("Search conversation settings", "Export Chat", "Clear Local Cache", "Report Conversation", "Blocked Users", "server-authoritative", "does not claim end-to-end encryption"):
        require(token in control, f"Control Center behavior/boundary missing: {token}")
    for token in ("sendConversationMessage", "reactToMessage", "deleteMessage", "reportMessage", "uploadMessengerMedia", "pinConversation", "markConversationSeen", "sendTyping"):
        require(f"function {token}" in api, f"existing Messenger API reuse missing: {token}")
    native = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mobile-native/src").rglob("*.ts*"))
    require("react-native-webview" not in native.lower(), "native Messenger must not add WebView")
    require("Powered by LogiNexus" not in native, "native Messenger must not expose internal branding")
    print("PulseSoc native Messenger WebView-target audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
