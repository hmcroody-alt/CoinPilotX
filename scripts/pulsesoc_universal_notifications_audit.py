#!/usr/bin/env python3
"""Audit PulseSoc universal notification coverage and queue safety."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    failures = []
    service = read("services/notification_service.py")
    orchestrator = read("services/notification_orchestrator.py")
    bot = read("bot.py")
    alert_engine = read("services/alert_engine.py")
    heartbeat = read("services/command_center_worker/heartbeat.py")
    native_push = read("mobile/pulse-react-native/services/push.ts")
    browser_notifications = read("static/notifications.js")

    for symbol in [
        "UNIVERSAL_NOTIFICATION_EVENTS",
        "UNIVERSAL_EVENT_ALIASES",
        "dispatch_universal_notification",
        "sendUniversalNotification",
        "process_queued_email_notifications",
        "failed_email_queue",
        "_queue_email_job",
        "_quiet_hours_active",
    ]:
        require(symbol in service, f"notification service missing {symbol}", failures)

    for event in [
        "signup_welcome",
        "email_confirmation",
        "password_reset_requested",
        "password_changed",
        "account_login",
        "new_device",
        "chat_message",
        "reaction",
        "comment",
        "reply",
        "repost",
        "save",
        "follow",
        "mention",
        "crypto_alert_triggered",
        "live_started",
        "cohost_request_received",
        "cohost_accepted",
        "cohost_denied",
        "guest_removed",
        "live_replay_ready",
        "live_highlight_ready",
        "purchase",
        "payment_succeeded",
        "payment_failed",
        "premium",
        "marketplace_order",
        "admin_security_event",
        "marketing",
    ]:
        require(event in service + bot, f"missing universal event coverage for {event}", failures)

    send_email_block = service[service.find("def send_email_notification"):service.find("def send_sms_notification")]
    require("_queue_email_job" in send_email_block, "send_email_notification must queue email jobs", failures)
    require("email_service.send_email" not in send_email_block, "send_email_notification must not call Brevo synchronously", failures)
    require("schedule_email_queue_processing" in service, "email queue must schedule async processing", failures)
    require("process_queued_email_notifications" in heartbeat, "worker heartbeat must drain queued emails", failures)
    require("dispatch_event" in orchestrator and "dispatch_universal_notification" in orchestrator, "orchestrator must expose universal dispatch", failures)

    for marker in [
        "cohost_request_received",
        "Co-host request received",
        "cohost_accepted",
        "cohost_denied",
        "pulse://pulse/live/studio",
        "pulse://live/",
    ]:
        require(marker in bot, f"live/co-host notification marker missing: {marker}", failures)
    require("pulse_notify_followers(" in bot and '"live_started"' in bot, "live start must notify followers", failures)

    require("notification_service.dispatch_universal_notification" in alert_engine, "crypto alerts must use universal in-app fallback", failures)
    require("notification_service.send_email_notification" in alert_engine, "crypto alert email must use queued notification email", failures)
    require("notification_service.send_push_alert" in alert_engine, "crypto alert push must use queue-aware push helper", failures)

    for category in ["chat_message", "crypto", "live", "marketplace_order", "purchase", "payments", "admin_security", "marketing"]:
        require(f'"{category}"' in bot, f"settings page missing category {category}", failures)
    require("data-save-notification-experience" in bot and "/api/notification-preferences" in bot, "quiet-hours preferences UI/API wiring missing", failures)

    for deep_link in ["pulse://live/", "pulse://alerts/", "pulse://purchase/", "pulse://account/security", "pulse://pulse/live/studio"]:
        require(deep_link in native_push, f"native push deep-link missing {deep_link}", failures)
    require("...(payload || {})" in browser_notifications, "foreground PulseShell notification must preserve payload metadata", failures)

    if failures:
        print("pulsesoc universal notifications audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("pulsesoc universal notifications audit passed.")


if __name__ == "__main__":
    main()
