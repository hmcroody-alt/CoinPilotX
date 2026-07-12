#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "static/css/pulse_messages_v2.css",
    "static/js/pulse_messages_v2.js",
    "templates/pulse_messages_v2.html",
    "mobile-native/src/components/PulseCommand.tsx",
    "mobile-native/src/screens/MessengerScreen.tsx",
    "mobile-native/src/screens/ChatScreen.tsx",
    "mobile-native/src/screens/GroupsScreen.tsx",
    "mobile-native/src/screens/CallScreen.tsx",
    "mobile-native/src/screens/PulseAiScreen.tsx",
    "mobile-native/src/pulseCommand/domain.ts",
    "reports/pulsesoc_native_pulse_command_exact_parity_inventory.md",
    "reports/pulsesoc_native_pulse_command_layout_parity.md",
    "reports/pulsesoc_native_pulse_command_visual_parity.md",
    "reports/pulsesoc_native_pulse_command_interaction_parity.md",
    "reports/pulsesoc_native_pulse_command_code_reuse_audit.md",
    "reports/pulsesoc_native_pulse_command_simulator_qa.md",
    "reports/pulsesoc_native_production_ui_token_map.md",
]

REPORT_NEEDLES = {
    "reports/pulsesoc_native_pulse_command_exact_parity_inventory.md": [
        "Messenger header",
        "Chats tab",
        "Calls tab",
        "Groups tab",
        "Rooms tab",
        "conversation rows",
        "message bubbles",
        "composer",
        "UNDX",
        "offline/reconnect",
    ],
    "reports/pulsesoc_native_pulse_command_layout_parity.md": [
        "Production layout parity",
        "Top-level shell",
        "Conversation list",
        "Conversation screen",
        "Calls",
        "Groups",
        "Rooms",
    ],
    "reports/pulsesoc_native_pulse_command_visual_parity.md": [
        "Production visual parity",
        "row density",
        "bubble geometry",
        "composer geometry",
        "Remaining differences",
    ],
    "reports/pulsesoc_native_pulse_command_interaction_parity.md": [
        "Production interaction parity",
        "reply",
        "reaction",
        "delete",
        "report",
        "mute",
        "offline",
    ],
    "reports/pulsesoc_native_pulse_command_code_reuse_audit.md": [
        "What was reused",
        "What was refined",
        "What was rebuilt natively",
        "No duplicate implementation",
    ],
    "reports/pulsesoc_native_pulse_command_simulator_qa.md": [
        "Xcode Simulator",
        "iPhone 17 Pro",
        "compact",
        "Pro Max",
        "screenshots/native-pulse-command-production-parity",
    ],
}

FORBIDDEN_NATIVE_NAMES = [
    "MessengerV2",
    "ChatScreen2",
    "PulseCommandNew",
    "ConversationList2",
    "CallsV2",
    "GroupsV2",
    "RoomsV2",
    "UNDXChatNew",
]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"missing required file: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def assert_contains(text: str, needle: str, label: str, failures: list[str], *, case_sensitive: bool = True) -> None:
    haystack = text if case_sensitive else text.lower()
    target = needle if case_sensitive else needle.lower()
    if target not in haystack:
        failures.append(f"{label} missing `{needle}`")


def main() -> int:
    failures: list[str] = []

    for rel in REQUIRED_FILES:
      try:
          read(rel)
      except AssertionError as exc:
          failures.append(str(exc))

    if failures:
        print("Pulse Command exact parity audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    production_css = read("static/css/pulse_messages_v2.css")
    for needle in [
        ".conversation",
        "min-height: 72px",
        "grid-template-columns: 48px",
        "font-size: 14px",
        ".message",
        "max-width: min(720px, 74%)",
        "border-radius: 17px",
        ".composer",
        "min-height: 46px",
    ]:
        assert_contains(production_css, needle, "production Messenger CSS", failures)

    pulse_command = read("mobile-native/src/components/PulseCommand.tsx")
    for needle in ["height: 48", "width: 48", "minHeight: 46", "minHeight: 36"]:
        assert_contains(pulse_command, needle, "PulseCommand primitives", failures)

    messenger = read("mobile-native/src/screens/MessengerScreen.tsx")
    for needle in [
        'label: "All"',
        'label: "Direct"',
        'label: "Groups"',
        'label: "Rooms"',
        'label: "AI"',
        'label: "Unread"',
        'placeholder="Search people, rooms, and messages"',
        'title="New Chat"',
        'title="Create Group"',
        'title="Start Room"',
        'Recent conversations',
        'minHeight: 70',
        'borderRadius: 12',
        'fontSize: 14',
        'fontSize: 12',
    ]:
        assert_contains(messenger, needle, "MessengerScreen", failures)

    for forbidden in ['label: "Calls"', "Active users", "active calls"]:
        if forbidden in messenger:
            failures.append(f"MessengerScreen retains non-production inbox hierarchy: {forbidden}")

    for needle in [
        "<ScrollView ref={rail} horizontal",
        "showsHorizontalScrollIndicator={false}",
        'testID="pulse-command-filter-rail"',
        "rail.current?.scrollTo",
        "accessibilityState={{ selected: active }}",
    ]:
        assert_contains(pulse_command, needle, "PulseCommand responsive segment rail", failures)

    for needle in [
        "pulsesoc.native.messenger.filter",
        "AsyncStorage.getItem(FILTER_KEY)",
        "AsyncStorage.setItem(FILTER_KEY, selectedFilter)",
        "EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FILTER",
    ]:
        assert_contains(messenger, needle, "Messenger filter persistence", failures)

    chat = read("mobile-native/src/screens/ChatScreen.tsx")
    for needle in [
        "Showing cached messages while PulseSoc reconnects.",
        "borderRadius: 17",
        "borderBottomRightRadius: 6",
        "borderBottomLeftRadius: 6",
        "fontSize: 15",
        "borderRadius: 999",
        "minWidth: 48",
        "AsyncStorage.getItem(draftKey)",
        "AsyncStorage.setItem(draftKey, draft)",
        "AttachmentActionSheet",
        'accessibilityLabel="Add attachment"',
        ">Add attachment<",
        'EXPO_PUBLIC_PULSESOC_QA_CHAT_STATE',
        "drainMessengerQueue(conversationId)",
        "enqueueMessengerMessage(conversationId",
        'delivery_status: "queued"',
        'Keyboard.addListener("keyboardWillShow"',
        "bottom: keyboardHeight",
        "const serverAccepted = message.id > 0 && Boolean(message.client_message_id)",
        'setStatusMessage("Messages reconnected.")',
        "cacheMessages(conversationId, queuedMessages)",
        'local_status: "queued"',
    ]:
        assert_contains(chat, needle, "ChatScreen", failures)

    messenger_api = read("mobile-native/src/api/messenger.ts")
    for needle in [
        "pulsesoc.native.messenger.outbound_queue",
        "export async function enqueueMessengerMessage",
        "export async function drainMessengerQueue",
        "client_message_id === clientId",
        "sendConversationMessage(item.conversationId, item.payload)",
    ]:
        assert_contains(messenger_api, needle, "Messenger outbound queue", failures)

    pulse_ai = read("mobile-native/src/screens/PulseAiScreen.tsx")
    if "Powered by LogiNexus" in pulse_ai:
        failures.append("PulseAiScreen exposes internal LogiNexus branding")

    calls = read("mobile-native/src/screens/CallScreen.tsx")
    assert_contains(calls, "Incoming, outgoing, and active PulseSoc calls will appear here.", "CallScreen production-facing empty state", failures)
    if "Active calls returned by `/api/" in calls:
        failures.append("CallScreen exposes an internal API path in user-facing copy")

    domain = read("mobile-native/src/pulseCommand/domain.ts")
    for needle in ["messageActionRules", "conversationPreview", "groupActionRules", "roomActionRules"]:
        assert_contains(domain, needle, "Pulse Command domain", failures)

    native_bundle = "\n".join(
        read(rel)
        for rel in [
            "mobile-native/src/components/PulseCommand.tsx",
            "mobile-native/src/screens/MessengerScreen.tsx",
            "mobile-native/src/screens/ChatScreen.tsx",
            "mobile-native/src/screens/GroupsScreen.tsx",
            "mobile-native/src/screens/CallScreen.tsx",
            "mobile-native/src/screens/PulseAiScreen.tsx",
        ]
    )
    for forbidden in FORBIDDEN_NATIVE_NAMES:
        if forbidden in native_bundle:
            failures.append(f"forbidden duplicate native implementation name found: {forbidden}")

    for rel, needles in REPORT_NEEDLES.items():
        report = read(rel)
        for needle in needles:
            assert_contains(report, needle, rel, failures, case_sensitive=False)

    token_map = read("reports/pulsesoc_native_production_ui_token_map.md")
    for needle in ["`static/css/pulse_messages_v2.css`", "Conversation row", "Message bubble", "Composer"]:
        assert_contains(token_map, needle, "production UI token map", failures, case_sensitive=False)

    if failures:
        print("Pulse Command exact parity audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse Command exact parity audit passed.")
    print("Validated production Messenger CSS markers, native row/bubble/composer parity markers, report coverage, and duplicate-surface guardrails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
