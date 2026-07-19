#!/usr/bin/env python3
"""Audit the native PulseSoc Conversation Control Center production wiring."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "mobile-native/src/components/ConversationControlCenter.tsx"
API = ROOT / "mobile-native/src/api/messenger.ts"
ROUTES = ROOT / "pulse_communications_v2/routes.py"
SERVICE = ROOT / "pulse_communications_v2/service.py"
REPORT = ROOT / "reports/pulsesoc_native_conversation_control_center_audit.json"
MISSION_REPORT = ROOT / "reports/pulsesoc_native_conversation_control_center_report.md"


def read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def expect(results: list[dict], name: str, passed: bool, detail: str = "") -> None:
    results.append({"check": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    component = read(COMPONENT)
    api = read(API)
    routes = read(ROUTES)
    service = read(SERVICE)
    results: list[dict] = []

    expect(results, "native component exists", COMPONENT.exists(), str(COMPONENT.relative_to(ROOT)))
    expect(results, "native API exists", API.exists(), str(API.relative_to(ROOT)))
    expect(results, "backend routes exist", ROUTES.exists(), str(ROUTES.relative_to(ROOT)))
    expect(results, "backend service exists", SERVICE.exists(), str(SERVICE.relative_to(ROOT)))

    forbidden = ["Locked", "Requires a production-backed Messenger contract", "href=\"#\"", "javascript:void(0)"]
    for token in forbidden:
        expect(results, f"forbidden token absent: {token}", token not in component + api + service, token)

    required_api_helpers = [
        "getConversationControlCenter",
        "updateConversationControlSetting",
        "listConversationMembers",
        "listConversationControlMedia",
        "listConversationControlLinks",
        "listConversationPinnedMessages",
        "exportConversationControlData",
        "runConversationControlAction",
        "muteConversation",
        "archiveConversation",
        "markConversationUnread",
        "searchConversationMessages",
    ]
    for helper in required_api_helpers:
        expect(results, f"native helper present: {helper}", f"export async function {helper}" in api or f"export function {helper}" in api, helper)

    required_routes = [
        "/control-center",
        "/control-center/media",
        "/control-center/links",
        "/control-center/pins",
        "/control-center/export",
        "/control-center/action",
        "/members",
        "/pin",
        "/mute",
        "/archive",
        "/unread",
    ]
    for route in required_routes:
        expect(results, f"backend route present: {route}", route in routes, route)

    required_native_controls = [
        'Quick label="Search Chat"',
        'Quick label="Shared Media"',
        'Quick label="Members"',
        'Quick label="Audio Call"',
        'Quick label="Video Call"',
        'action("View Members"',
        'action("Shared Media"',
        'action("Pinned Messages"',
        'action("Search Chat"',
        'action("Message Stats"',
        'action("Media Storage"',
        'action("Export Chat"',
        'setting("Mute Conversation"',
        'setting("Read Receipts"',
        'danger("Clear Conversation"',
        'danger("Delete Conversation"',
    ]
    for control in required_native_controls:
        expect(results, f"native control present: {control}", control in component, control)

    quick_search_chat_count = component.count('Quick label="Search Chat"')
    expect(results, "duplicate quick Search removed", 'Quick label="Search"' not in component, "old generic Search quick action must not exist")
    expect(results, "only one quick Search Chat", quick_search_chat_count == 1, f"count={quick_search_chat_count}")
    expect(results, "server settings are patched", "updateConversationControlSetting" in component and "PATCH" in api, "settings use canonical control-center PATCH")
    expect(results, "conversation search is scoped", "conversation_id" in api and "conversation_id" in service and "conversation_clause" in service, "search endpoint filters by accessible conversation")
    expect(results, "call capability exposed", '"voice_call": True' in service and '"video_call": True' in service, "server advertises real call routes")
    expect(results, "export capability exposed", '"export_chat": True' in service, "server advertises export endpoint")
    expect(results, "unavailable copy is product-facing", "Unavailable" in component and "production-backed Messenger contract" not in component, "no developer contract copy")
    expect(results, "mission report exists", MISSION_REPORT.exists(), str(MISSION_REPORT.relative_to(ROOT)))

    passed = all(item["passed"] for item in results)
    payload = {
        "ok": passed,
        "checks": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for item in results if item["passed"]),
            "failed": sum(1 for item in results if not item["passed"]),
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    if not passed:
        for item in results:
            if not item["passed"]:
                print(f"FAIL: {item['check']} :: {item['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
