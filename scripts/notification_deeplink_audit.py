#!/usr/bin/env python3
"""Audit PulseSoc notification deep-link map across server, web, and native."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")
WEB = (ROOT / "static" / "notifications.js").read_text(encoding="utf-8")
NATIVE = (ROOT / "mobile" / "pulse-react-native" / "services" / "push.ts").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    combined = SERVICE + "\n" + WEB + "\n" + NATIVE + "\n" + BOT

    required = {
        "message exact chat web": "/pulse/messages/{conversation_id}",
        "message exact chat native": "pulse://pulse/messages-v2?conversation=",
        "post/reaction native": "pulse://post/{post_id}",
        "comment native": "pulse://post/{post_id}/comment/{comment_id}",
        "status native": "pulse://status/{status_id}",
        "follow profile web": "/pulse/profile/{profile_id}",
        "follow profile native": "pulse://pulse/profile/{profile_id}",
        "live viewer web": "/pulse/live/{live_id}",
        "live viewer native": "pulse://live/{live_id}",
        "cohost backstage native": "pulse://pulse/live/studio",
        "crypto alert web": "/pulse/alerts/{alert_id}",
        "crypto alert native": "pulse://alerts/{alert_id}",
        "security web": "/account/security",
        "security native": "pulse://account/security",
        "purchase/order web": "/pulse/purchases/{order_id}",
        "purchase/order native": "pulse://purchase/{order_id}",
        "premium web": "/pulse/premium",
        "system fallback": "/pulse/notifications",
    }
    for label, needle in required.items():
        require(needle in combined, f"missing deep link for {label}", failures)

    for event in [
        "reaction",
        "comment",
        "follow",
        "live_started",
        "cohost_request",
        "crypto_alert_triggered",
        "account_login",
        "new_device",
        "marketplace_order",
        "premium",
    ]:
        require(event in SERVICE, f"notification event missing from server map: {event}", failures)

    for native_pattern in ["pulse://live/", "pulse://alerts/", "pulse://purchase/", "pulse://account/security", "pulse://pulse/live/studio"]:
        require(native_pattern in NATIVE + SERVICE, f"native route handler missing {native_pattern}", failures)

    require("notificationUrlFromData" in NATIVE and "native_url || payload.app_url || payload.mobile_deep_link" in NATIVE, "native tap routing must prefer native deep links", failures)
    require("noteUrl(note)" in WEB and "data-open-note" in WEB + BOT, "web notification open action must use notification deep links", failures)

    if failures:
        print("notification deeplink audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("notification deeplink audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
