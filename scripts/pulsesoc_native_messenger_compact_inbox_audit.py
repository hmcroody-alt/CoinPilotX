#!/usr/bin/env python3
"""Audit the compact native PulseSoc Messenger inbox redesign."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"missing required file: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def require(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle not in text:
        failures.append(f"{label} missing `{needle}`")


def forbid(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle in text:
        failures.append(f"{label} must not contain `{needle}`")


def main() -> int:
    failures: list[str] = []

    messenger = read("mobile-native/src/screens/MessengerScreen.tsx")
    api = read("mobile-native/src/api/messenger.ts")
    report = read("reports/pulsesoc_native_messenger_compact_inbox_redesign.md")

    for needle in [
        'testID="messenger-compact-header"',
        "Messages</Text>",
        "Pulse Command</Text>",
        'accessibilityLabel="Open Messenger safety controls"',
        'accessibilityLabel="Start a new chat"',
        'testID="messenger-search-row"',
        'placeholder="Search people, rooms, messages..."',
        'accessibilityLabel="Open Conversation Control Center"',
        "⚙",
        'label: "All"',
        'label: "Direct"',
        'label: "Groups"',
        'label: "Rooms"',
        'label: "AI"',
        'label: "Unread"',
        'testID="messenger-active-rail"',
        'accessibilityLabel="Start a new direct conversation"',
        "Add</Text>",
        'title="New Chat"',
        'title="Create Group"',
        'title="Start Room"',
        "searchMessages",
        'testID="messenger-skeleton-list"',
        "ListEmptyComponent={loading ? <ConversationSkeletonList />",
        "removeClippedSubviews",
        "initialNumToRender={10}",
        "maxToRenderPerBatch={8}",
        "windowSize={7}",
        "loadSequence",
        "sequence !== loadSequence.current",
        "minHeight: 64",
        "gap: 4",
        "padding: 8",
    ]:
        require(messenger, needle, "compact Messenger inbox", failures)

    for needle in [
        "productionHeader",
        "brandLockup",
        "commandVersion",
        "Messenger V3",
        "Voice in progress",
        "ACTIVE PULSESOC CALL",
    ]:
        forbid(messenger, needle, "compact Messenger inbox", failures)

    for needle in [
        "export async function searchMessenger",
        "`${MESSENGER_API}/search?q=${encoded}`",
        "`${MESSENGER_API}/people/search?q=${encoded}`",
        "openDirectConversation",
        "directConversationRequests",
        "subscribeConversationUpdates",
        "conversationListeners",
        "normalizeConversations",
    ]:
        require(api, needle, "production Messenger API reuse", failures)

    for needle in [
        "Hero removed: YES",
        "Reserved hero space removed: YES",
        "Settings gear: PASS",
        "Search: PASS",
        "Active-contact routing: PASS",
        "Performance posture",
        "Remaining blockers",
    ]:
        require(report, needle, "compact inbox report", failures)

    if failures:
        print("PulseSoc native Messenger compact inbox audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native Messenger compact inbox audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
