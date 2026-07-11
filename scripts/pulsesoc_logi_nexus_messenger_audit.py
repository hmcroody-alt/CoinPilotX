#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    (
        "shared Pulse Command primitives exist",
        ROOT / "mobile-native/src/components/PulseCommand.tsx",
        ["PulseCommandHeader", "PulseCommandPanel", "PulseCommandSearch", "PulseCommandSegmentRail", "PulseCommandAvatar"],
    ),
    (
        "Messenger inbox adopts shared Pulse Command and virtualized list",
        ROOT / "mobile-native/src/screens/MessengerScreen.tsx",
        ["PulseCommandHeader", "PulseCommandSegmentRail", "PulseCommandSearch", "FlatList", "LogiNexusStatePanel", "getActiveCalls"],
    ),
    (
        "Conversation screen preserves native send/upload/call logic with shared command surface",
        ROOT / "mobile-native/src/screens/ChatScreen.tsx",
        ["sendConversationMessage", "uploadMessengerMedia", "sendTyping", "PulseCommandHeader", "LogiNexusStatePanel", "NativeMediaViewer"],
    ),
    (
        "Groups and rooms surface uses shared command language",
        ROOT / "mobile-native/src/screens/GroupsScreen.tsx",
        ["PulseCommandHeader", "PulseCommandSearch", "LogiNexusStatePanel", "openGroupChat", "joinRoom"],
    ),
    (
        "UNDX public surface uses Digital Intelligence Companion copy",
        ROOT / "mobile-native/src/screens/PulseAiScreen.tsx",
        ["UNDX", "Digital Intelligence Companion", "askPulseAi", "PulseCommandHeader"],
    ),
    (
        "Visible Pulse AI labels removed from primary native surfaces",
        ROOT / "mobile-native/src/screens/LoginScreen.tsx",
        ["UNDX"],
    ),
]

REPORTS = [
    "reports/pulsesoc_logi_nexus_pulse_command_transformation.md",
    "reports/pulsesoc_logi_nexus_messenger_component_map.md",
    "reports/pulsesoc_logi_nexus_messenger_simulator_qa.md",
    "reports/pulsesoc_logi_nexus_messenger_accessibility.md",
    "reports/pulsesoc_logi_nexus_messenger_performance.md",
    "reports/pulsesoc_logi_nexus_messenger_visual_comparison.md",
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

    for report in REPORTS:
        path = ROOT / report
        if not path.exists():
            failures.append(f"missing report: {report}")

    visible_label_files = [
        ROOT / "mobile-native/src/screens/LoginScreen.tsx",
        ROOT / "mobile-native/src/screens/PulseAiScreen.tsx",
        ROOT / "mobile-native/src/screens/DashboardModuleDetailScreen.tsx",
    ]
    for path in visible_label_files:
        text = read(path)
        if "Pulse AI" in text:
            failures.append(f"legacy public Pulse AI label remains in {path.relative_to(ROOT)}")

    if failures:
        print("Pulse Command LogiNexus audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse Command LogiNexus audit passed.")
    print("Validated shared command primitives, inbox, conversation, groups/rooms, UNDX labels, and required reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
