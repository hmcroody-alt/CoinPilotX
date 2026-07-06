#!/usr/bin/env python3
"""Audit the PulseSoc Native Activity Inbox authenticated QA hardening report."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    required_files = [
        "mobile-native/src/api/activity.ts",
        "mobile-native/src/screens/ActivityInboxScreen.tsx",
        "mobile-native/src/navigation/linking.ts",
        "reports/pulsesoc_native_activity_inbox_qa.md",
        "reports/pulsesoc_native_progress.md",
    ]
    for file_path in required_files:
        require((ROOT / file_path).exists(), f"missing {file_path}", failures)

    activity_api = read("mobile-native/src/api/activity.ts")
    activity_screen = read("mobile-native/src/screens/ActivityInboxScreen.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    qa_report = read("reports/pulsesoc_native_activity_inbox_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for token in [
        "post|like|comment|mention|follow|reaction|share|repost|social",
        "resolved.fallback_used",
        "item.targetUrl",
    ]:
        require(token in activity_api, f"activity API missing QA fix token {token}", failures)

    require("unreadCountsByCategory" in activity_screen, "Activity Inbox screen missing derived category count helper", failures)
    require("useMemo(() => unreadCountsByCategory(items), [items])" in activity_screen, "Activity Inbox screen does not derive category counts from items", failures)

    for token in [
        'Notifications: "pulse/notifications"',
        'ActivityInboxLegacyInbox: "pulse/inbox"',
        'ActivityInboxWebActivity: "dashboard/activity"',
        'ActivityInboxWebInbox: "dashboard/inbox"',
    ]:
        require(token in linking, f"linking missing {token}", failures)

    for token in [
        "ActivityInboxLegacyInbox",
        "ActivityInboxWebActivity",
        "ActivityInboxWebInbox",
    ]:
        require(token in app_nav, f"navigator missing {token}", failures)

    for token in [
        "Delete removed one QA notification",
        "Mark read changed the remaining activity state",
        "Social Notification Grouped Into Intelligence",
        "Legacy Inbox Routes Fell Back To Home",
        "Category Counts Became Stale After Delete",
        "Native Open Routing Used Server Web Fallback",
        "No critical, security, data-loss, or production-breaking blockers were found",
    ]:
        require(token in qa_report, f"QA report missing {token}", failures)

    require("native activity inbox authenticated qa hardening" in progress.lower(), "master progress missing Activity Inbox QA hardening", failures)
    require("LogiNexus" not in activity_api + "\n" + activity_screen, "internal LogiNexus name leaked to user-facing Activity Inbox code", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("pulsesoc native activity inbox QA audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
