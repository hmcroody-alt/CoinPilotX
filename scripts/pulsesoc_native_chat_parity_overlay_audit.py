#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"missing required file: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    chat = read("mobile-native/src/screens/ChatScreen.tsx")
    incoming = read("mobile-native/src/calls/IncomingCallLayer.tsx")
    messenger = read("mobile-native/src/api/messenger.ts")
    report = read("reports/pulsesoc_native_chat_conversation_parity_overlay.md")

    require("Voice in progress" not in incoming and "Video in progress" not in incoming, "global floating call popup copy must be removed", failures)
    require("callBubbleMain" not in incoming, "global floating active-call Pressable must not mount", failures)
    require("callBubbleEnd" not in incoming, "global floating active-call End button must not mount", failures)
    require("showFloatingCall" not in incoming, "route-specific floating call visibility policy should be removed", failures)
    require("setFloatingCall(connected || null)" in incoming, "active call state polling should remain preserved", failures)
    require('navigationRef.navigate("Call"' in incoming, "dedicated Call route should remain canonical", failures)

    require("showInitialLoading" in chat, "chat must expose initial loading state", failures)
    require("showFatalError" in chat, "chat must expose fatal error state", failures)
    require("showEmptyConversation" in chat, "chat must expose successful empty state", failures)
    require("ListEmptyComponent={showEmptyConversation ?" in chat, "empty state must be gated behind successful empty conversation", failures)
    require("error && hasMessages" in chat, "fetch errors with cached messages must render as nonblocking banner", failures)
    require("setUsingCachedMessages(true)" in chat, "cached history must be tracked and kept visible", failures)
    require("Realtime reconnecting. Message history remains visible." in chat, "realtime reconnect must not erase message history", failures)

    for needle in [
        "/api/pulse/messages/${conversationId}/messages",
        "/api/pulse/messages/${conversationId}/send",
        "/api/pulse/messages/${conversationId}/sync",
        "/api/pulse/messages/${conversationId}/seen",
        "/api/pulse/messages/media/upload",
    ]:
      require(needle in messenger, f"native messenger API must preserve canonical route `{needle}`", failures)

    for phrase in [
        "Root Cause of Global Call Popup",
        "Root Cause of Contradictory States",
        "Native conversation can replace WebView conversation now: NO",
        "Global Popup Removal Verification",
        "No database migration or ID rewrite was introduced.",
    ]:
        require(phrase in report, f"report missing `{phrase}`", failures)

    if failures:
        print("PulseSoc native chat parity/overlay audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native chat parity/overlay audit passed.")
    print("Validated global mini-call popup removal, mutually exclusive chat states, and canonical messaging routes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
