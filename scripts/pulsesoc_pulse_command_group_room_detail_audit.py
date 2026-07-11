#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    (
        "native group and room models accept authoritative detail payloads",
        ROOT / "mobile-native/src/api/groups.ts",
        [
            "PulseGroupMember",
            "PulseGroupInvitation",
            "PulseGroupAsset",
            "PulseRoomParticipant",
            "normalizeGroupMembers",
            "normalizeGroupInvitations",
            "normalizeRoomParticipants",
            "normalizeGroupAssets",
            "deriveAssetsFromPosts",
        ],
    ),
    (
        "Pulse Command domain owns group room role and provider decisions",
        ROOT / "mobile-native/src/pulseCommand/domain.ts",
        [
            "groupMemberRoleLabel",
            "groupRolePriority",
            "groupNotificationLabel",
            "groupMemberActionRules",
            "groupInvitationStateLabel",
            "groupAssetCategoryLabel",
            "roomProviderStateLabel",
            "roomParticipantRoleLabel",
            "roomParticipantAccessibilityLabel",
        ],
    ),
    (
        "GroupsScreen has native nested group detail sections",
        ROOT / "mobile-native/src/screens/GroupsScreen.tsx",
        [
            "GroupDetailSection",
            "GroupOverview",
            "GroupMembers",
            "GroupInvitations",
            "GroupAssets",
            "GroupSettings",
            "GroupMemberRow",
            "GroupInvitationRow",
            "GroupAssetCard",
        ],
    ),
    (
        "GroupsScreen has native room detail sections and provider boundaries",
        ROOT / "mobile-native/src/screens/GroupsScreen.tsx",
        [
            "selectedRoom",
            "RoomDetail",
            "RoomParticipants",
            "RoomActivity",
            "RoomProviderBoundary",
            "roomProviderStateLabel",
            "providerBoundary",
            "Live presence boundary",
        ],
    ),
    (
        "GroupsScreen keeps existing server-authoritative mutations",
        ROOT / "mobile-native/src/screens/GroupsScreen.tsx",
        [
            "joinGroup",
            "leaveGroup",
            "openGroupChat",
            "reportGroup",
            "joinRoom",
            "navigation.navigate(\"Chat\"",
            "navigation.navigate(\"SafetyHub\"",
        ],
    ),
]

REPORTS = [
    "reports/pulsesoc_pulse_command_group_detail.md",
    "reports/pulsesoc_pulse_command_group_roles_permissions.md",
    "reports/pulsesoc_pulse_command_room_detail.md",
    "reports/pulsesoc_pulse_command_groups_rooms_simulator_qa.md",
    "reports/pulsesoc_pulse_command_completion.md",
    "reports/pulsesoc_pulse_command_accessibility.md",
    "reports/pulsesoc_pulse_command_performance.md",
    "reports/pulsesoc_native_progress.md",
]

FORBIDDEN = [
    "GroupsV2",
    "GroupDetail2",
    "RoomsNew",
    "RoomSystemV2",
]


def read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    failures: list[str] = []
    for label, path, needles in CHECKS:
        try:
            text = read(path)
        except AssertionError as exc:
            failures.append(str(exc))
            continue
        missing = [needle for needle in needles if needle not in text]
        if missing:
            failures.append(f"{label}: missing {', '.join(missing)} in {path.relative_to(ROOT)}")

    screen_text = read(ROOT / "mobile-native/src/screens/GroupsScreen.tsx")
    forbidden_found = [needle for needle in FORBIDDEN if needle in screen_text]
    if forbidden_found:
        failures.append(f"forbidden duplicate group/room surfaces found: {', '.join(forbidden_found)}")

    for report in REPORTS:
        path = ROOT / report
        if not path.exists():
            failures.append(f"missing report: {report}")
            continue
        text = read(path)
        if report.endswith("group_detail.md") and "Group Detail" not in text:
            failures.append("group detail report missing Group Detail section")
        if report.endswith("room_detail.md") and "Room Detail" not in text:
            failures.append("room detail report missing Room Detail section")

    if failures:
        print("Pulse Command group/room detail audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse Command group/room detail audit passed.")
    print("Validated native group detail sections, member/role presentation rules, room detail surfaces, provider boundaries, reports, and duplicate-surface guardrails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
