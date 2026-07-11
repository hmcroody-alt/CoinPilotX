#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "templates/pulse_messages_v2.html",
    "static/js/pulse_messages_v2.js",
    "static/js/pulse_chat_recovery.js",
    "static/js/pulse_messenger_media_viewer.js",
    "static/pulsesoc_calls.js",
    "services/chat_realtime_service.py",
    "services/messenger_media_foundation.py",
    "services/chat_health_service.py",
    "mobile-native/src/api/messenger.ts",
    "mobile-native/src/api/groups.ts",
    "mobile-native/src/api/calls.ts",
    "mobile-native/src/screens/MessengerScreen.tsx",
    "mobile-native/src/screens/ChatScreen.tsx",
    "mobile-native/src/screens/CallScreen.tsx",
    "mobile-native/src/screens/GroupsScreen.tsx",
    "mobile-native/src/screens/PulseAiScreen.tsx",
    "mobile-native/src/components/PulseCommand.tsx",
    "reports/pulsesoc_pulse_command_code_reuse_map.md",
    "reports/pulsesoc_pulse_command_native_rebuild_boundaries.md",
]

REPORT_NEEDLES = [
    "Reuse unchanged",
    "Extract and share",
    "Refactor and extend",
    "Native UI rebuild",
    "Do not carry over obsolete web-only code",
    "Provider/device-only boundaries",
    "inbox",
    "search",
    "conversation row",
    "message list",
    "send",
    "typing",
    "seen/read",
    "reactions",
    "reply",
    "forward",
    "delete",
    "report",
    "block",
    "mute",
    "attachments",
    "voice",
    "calls",
    "groups",
    "rooms",
    "UNDX",
    "offline/reconnect",
    "deep links",
    "push routing",
]

NATIVE_API_NEEDLES = {
    "mobile-native/src/api/messenger.ts": [
        "/api/pulse/messages/conversations",
        "/api/pulse/messages/${conversationId}/messages",
        "/api/pulse/messages/${conversationId}/send",
        "/api/pulse/messages/${messageId}/react",
        "/api/pulse/messages/${messageId}/delete",
        "/api/pulse/messages/${messageId}/report",
        "/api/pulse/messages/${conversationId}/seen",
        "/api/pulse/messages/${conversationId}/typing",
        "/api/pulse/messages/search",
        "/api/pulse/messages/media/upload",
        "normalizeConversations",
        "normalizeMessages",
    ],
    "mobile-native/src/api/groups.ts": [
        "/api/pulse/groups?",
        "/api/pulse/groups/${encodeURIComponent(slug)}/join",
        "/api/pulse/groups/${encodeURIComponent(slug)}/chat/open",
        "/api/pulse/communications/rooms",
        "/api/pulse/messages/rooms/${encodeURIComponent(roomId)}/join",
        "normalizeGroups",
        "normalizeRooms",
    ],
    "mobile-native/src/api/calls.ts": [
        "/api/calls/start",
        "/api/calls/${encodeURIComponent(callId)}/accept",
        "/api/calls/${encodeURIComponent(callId)}/join-token",
        "/api/calls/${encodeURIComponent(callId)}/status",
        "/api/calls/active",
        "normalizeCall",
        "openCallWebFallback",
    ],
}

FORBIDDEN_NATIVE_PATTERNS = [
    "MessengerV2",
    "ChatScreen2",
    "NativeMessageService2",
    "CallsV2",
    "GroupsV2",
    "RoomsV2",
]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"missing required file: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    failures: list[str] = []
    for rel in REQUIRED_FILES:
        try:
            read(rel)
        except AssertionError as exc:
            failures.append(str(exc))

    if not failures:
        report = read("reports/pulsesoc_pulse_command_code_reuse_map.md")
        report_lower = report.lower()
        missing = [needle for needle in REPORT_NEEDLES if needle.lower() not in report_lower]
        if missing:
            failures.append(f"reuse map missing required coverage: {', '.join(missing)}")

        boundaries = read("reports/pulsesoc_pulse_command_native_rebuild_boundaries.md")
        for phrase in ["Backend-Owned Logic", "Native-Owned Presentation", "Extraction Candidates", "Current Native Boundaries"]:
            if phrase not in boundaries:
                failures.append(f"native rebuild boundaries missing section: {phrase}")

        for rel, needles in NATIVE_API_NEEDLES.items():
            text = read(rel)
            missing_needles = [needle for needle in needles if needle not in text]
            if missing_needles:
                failures.append(f"{rel} missing API/domain reuse markers: {', '.join(missing_needles)}")

        native_text = "\n".join(
            read(rel)
            for rel in [
                "mobile-native/src/api/messenger.ts",
                "mobile-native/src/api/groups.ts",
                "mobile-native/src/api/calls.ts",
                "mobile-native/src/screens/MessengerScreen.tsx",
                "mobile-native/src/screens/ChatScreen.tsx",
                "mobile-native/src/screens/CallScreen.tsx",
                "mobile-native/src/screens/GroupsScreen.tsx",
                "mobile-native/src/screens/PulseAiScreen.tsx",
            ]
        )
        found_forbidden = [pattern for pattern in FORBIDDEN_NATIVE_PATTERNS if pattern in native_text]
        if found_forbidden:
            failures.append(f"forbidden duplicate native surface names found: {', '.join(found_forbidden)}")

        web_logic = read("static/js/pulse_messages_v2.js")
        for marker in ["sortConversations", "conversationPreview", "messageDeliveryLabel", "attachmentKind", "sendMessage", "reactToMessage"]:
            if marker not in web_logic:
                failures.append(f"production WebView logic marker missing: {marker}")

    if failures:
        print("Pulse Command code reuse audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse Command code reuse audit passed.")
    print("Validated WebView/backend/native source inventory, reuse map, native rebuild boundaries, API wrapper reuse, and duplicate-surface guardrails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
