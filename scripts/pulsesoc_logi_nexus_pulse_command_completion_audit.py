#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    (
        "Messenger API exposes server-authoritative message actions",
        ROOT / "mobile-native/src/api/messenger.ts",
        [
            "PULSESOC_QA_MESSENGER_FIXTURES",
            "reactToMessage",
            "deleteMessage",
            "reportMessage",
            "pinConversation",
            "withQaConversations",
            "withQaMessages",
            "/api/pulse/messages/${messageId}/react",
            "/api/pulse/messages/${messageId}/delete",
            "/api/pulse/messages/${messageId}/report",
        ],
    ),
    (
        "Pulse Command inbox keeps chats calls groups rooms in one native surface",
        ROOT / "mobile-native/src/screens/MessengerScreen.tsx",
        [
            "PulseCommandTab",
            "CallRow",
            "GroupRow",
            "RoomRow",
            "ConversationRow",
            "listGroups",
            "listRooms",
            "joinRoom",
            "openGroupChat",
            "PulseCommandSegmentRail",
        ],
    ),
    (
        "Conversation screen supports reply reactions context sheet safety and retry",
        ROOT / "mobile-native/src/screens/ChatScreen.tsx",
        [
            "MessageActionSheet",
            "ReactionRow",
            "reactToMessage",
            "deleteMessage",
            "reportMessage",
            "replyTo",
            "retryMessage",
            "SafetyHub",
            "NativeMediaViewer",
        ],
    ),
    (
        "Calls screen reuses Pulse Command shell and preserves provider boundary",
        ROOT / "mobile-native/src/screens/CallScreen.tsx",
        [
            "PulseCommandHeader",
            "PulseCommandPanel",
            "PulseCommandMetric",
            "LogiNexusScrollContainer",
            "LogiNexusStatePanel",
            "useNativeCallRoom",
            "startConversationCall",
            "requestCallJoinToken",
            "openCallWebFallback",
            "sendCallControl",
        ],
    ),
]

REPORTS = [
    "reports/pulsesoc_logi_nexus_pulse_command_completion.md",
    "reports/pulsesoc_logi_nexus_messenger_component_map.md",
    "reports/pulsesoc_logi_nexus_messenger_simulator_qa.md",
    "reports/pulsesoc_logi_nexus_messenger_accessibility.md",
    "reports/pulsesoc_logi_nexus_messenger_performance.md",
    "reports/pulsesoc_logi_nexus_messenger_interactions.md",
    "reports/pulsesoc_logi_nexus_messenger_offline_reconnect.md",
    "reports/pulsesoc_logi_nexus_pulse_command_calls.md",
]


def read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    for label, path, needles in CHECKS:
        text = read(path)
        missing = [needle for needle in needles if needle not in text]
        if missing:
            failures.append(f"{label}: missing {', '.join(missing)} in {path.relative_to(ROOT)}")

    config_text = read(ROOT / "mobile-native/src/api/config.ts")
    if "EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FIXTURES" not in config_text:
        failures.append("local QA fixture gate is not explicit")
    if "localhost" not in config_text or "127\\.0\\.0\\.1" not in config_text:
        failures.append("local QA fixtures are not constrained to local API base URLs")

    for report in REPORTS:
        if not (ROOT / report).exists():
            failures.append(f"missing report: {report}")

    if failures:
        print("Pulse Command completion audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse Command completion audit passed.")
    print("Validated local-only populated QA fixtures, native tabbed inbox, server-backed message actions, context sheet, call shell reuse, and required reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
