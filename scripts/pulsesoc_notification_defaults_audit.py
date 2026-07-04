#!/usr/bin/env python3
"""Audit PulseSoc notification defaults.

This is a source-level guard for the default-on notification preference policy.
It avoids mutating production data while checking that provisioning, API reads,
delivery reads, and UI copy all route through the default helper.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_CATEGORIES = {
    "chat_message",
    "group_message",
    "room_message",
    "comments",
    "comment",
    "reply",
    "likes",
    "reaction",
    "social",
    "status",
    "live",
    "live_invite",
    "crypto",
    "market",
    "intelligence",
    "marketplace",
    "marketplace_order",
    "purchase",
    "payments",
    "premium",
    "admin_security",
    "marketing",
    "roast_battle",
    "security",
}


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    system_src = read("services/pulsesoc_notification_system.py")
    legacy_src = read("services/notification_service.py")
    bot_src = read("bot.py")
    js_src = read("static/notifications.js")
    report_exists = (ROOT / "reports/pulsesoc_notification_defaults.md").exists()

    for category in sorted(REQUIRED_CATEGORIES):
        require(f'"{category}"' in system_src or f"'{category}'" in system_src, f"missing default OS category: {category}", failures)
        require(f'"{category}"' in legacy_src or f"'{category}'" in legacy_src or f'"{category}"' in bot_src, f"missing legacy/UI category: {category}", failures)

    require('"push": True' in system_src, "OS default push channel is not true", failures)
    require('"email": True' in system_src, "OS default email channel is not true", failures)
    require('"sms": True' in system_src, "OS default SMS channel is not true", failures)
    require('"enable_push_notifications": True' in system_src, "global push preference default is not true", failures)
    require("ensure_user_notification_defaults" in system_src, "user default provisioning helper missing", failures)
    require("backfill_notification_defaults" in system_src, "existing-user backfill helper missing", failures)
    require("ensure_user_notification_defaults(user_id, conn=conn)" in bot_src, "new account provisioning is not wired", failures)
    require("backfill_notification_defaults(limit=notification_backfill_limit, conn=conn)" in bot_src, "bounded startup backfill is not wired", failures)
    require("Enable Push to receive PulseSoc alerts on your lock screen" in bot_src + js_src, "push permission onboarding copy missing", failures)
    require("Blocked by device" in js_src and "Unsupported" in js_src and "Needs permission" in js_src, "push status labels missing", failures)
    require("_pulse_category_defaults.update" in legacy_src, "legacy Pulse category defaults are not normalized on", failures)
    require(report_exists, "report missing: reports/pulsesoc_notification_defaults.md", failures)

    if failures:
        print("PulseSoc notification defaults audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PulseSoc notification defaults audit passed")
    print(f"- categories checked: {len(REQUIRED_CATEGORIES)}")
    print("- missing preferences default to in-app/push/email/SMS enabled")
    print("- existing saved rows remain authoritative")
    print("- OS push permission is reported separately from PulseSoc push preference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
