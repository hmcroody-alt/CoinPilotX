#!/usr/bin/env python3
"""Audit native mobile notification channel and deep-link handling."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"FAIL: missing {label}: {needle}")


def main() -> None:
    app = (ROOT / "mobile" / "pulse-react-native" / "App.tsx").read_text()
    push = (ROOT / "mobile" / "pulse-react-native" / "services" / "push.ts").read_text()

    require(push, 'const ANDROID_MESSAGES_CHANNEL_ID = "messages"', "Android messages channel id")
    require(push, 'name: "Messages"', "Android messages channel name")
    require(push, "AndroidNotificationVisibility.PUBLIC", "lock-screen visibility")
    require(push, "getLastNotificationResponseAsync", "cold-start notification response")
    require(push, "setActiveConversationFromUrl", "active conversation foreground suppression")
    require(push, "shouldPlaySound: !isActiveConversation", "foreground sound suppression only for active conversation")
    require(app, "getInitialNotificationUrl", "App cold-start notification routing")
    require(app, 'incomingUrl.startsWith("/")', "relative notification URL support")
    require(app, "setActiveConversationFromUrl(navState.url)", "active conversation URL tracking")
    print("PASS: Mobile push channel, foreground behavior, and notification deep links are wired.")


if __name__ == "__main__":
    main()
