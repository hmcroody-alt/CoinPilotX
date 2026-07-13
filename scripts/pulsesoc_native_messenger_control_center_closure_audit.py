#!/usr/bin/env python3
"""Focused structural audit for the native screenshot-target Control Center closure."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROL = (ROOT / "mobile-native/src/components/ConversationControlCenter.tsx").read_text()
INBOX = (ROOT / "mobile-native/src/screens/MessengerScreen.tsx").read_text()
CHAT = (ROOT / "mobile-native/src/screens/ChatScreen.tsx").read_text()

failures: list[str] = []

def require(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)

order = ["conversation", "notifications", "appearance", "privacy", "media", "security", "productivity", "storage", "accessibility", "danger"]
positions = [CONTROL.find(f'key: "{key}"') for key in order]
require(all(position >= 0 for position in positions) and positions == sorted(positions), "Control Center production section order drifted")
for copy in [
    "Manage this chat experience.", "Search settings...", "View Members", "Shared Media", "Pinned Messages", "Message Stats",
    "Mute Conversation", "Notification Sound", "Theme", "Wallpaper", "Read Receipts", "Auto Download Photos",
    "Encryption Status", "Pin Conversation", "Conversation Size", "Large Text", "Clear Conversation", "Reset Conversation Settings"
]:
    require(copy in CONTROL, f"missing production control copy: {copy}")
require("end-to-end encryption is not claimed" in CONTROL, "security language must not claim E2EE")
require("participantCount" in CONTROL and "mediaBytes" in CONTROL and "messages.length" in CONTROL, "dashboard metrics must derive from conversation state")
require("LAST_CONVERSATION_KEY" in INBOX and "openControlCenter: true" in INBOX, "inbox gear must restore a real conversation context")
require('accessibilityLabel="Open Conversation Control Center"' in INBOX, "inbox gear needs an accessibility label")
require('label="Gear"' in CHAT and "setControlCenterOpen(true)" in CHAT, "chat gear must open Control Center")
require("AsyncStorage.setItem(preferenceKey" in CONTROL, "local accessibility preferences must persist")
require("Alert.alert" in CONTROL and "It does not delete messages or remote media" in CONTROL, "cache confirmation must be explicit and non-destructive")
require("LogiNexus" not in CONTROL, "internal branding must not be visible in Control Center copy")

if failures:
    print("FAIL")
    for failure in failures:
        print(f"- {failure}")
    raise SystemExit(1)
print("PASS: native Messenger screenshot-target Control Center structure, gear entry, truthful metrics, and safety boundaries")
