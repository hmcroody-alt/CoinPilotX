"""Audit Brevo email/SMS notification infrastructure for Pulse."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTIFICATION_SERVICE = (ROOT / "services" / "notification_service.py").read_text()
EMAIL_SERVICE = (ROOT / "services" / "email_service.py").read_text()
SMS_SERVICE = (ROOT / "services" / "sms_service.py").read_text()
HEALTH_ENGINE = (ROOT / "services" / "notification_health_engine.py").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
BOT = (ROOT / "bot.py").read_text()


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    failures = []
    for category in ["account", "premium", "social", "live", "status", "marketplace", "crypto", "security"]:
        require(f'"{category}"' in NOTIFICATION_SERVICE, f"missing notification category: {category}", failures)
    for event in [
        "user_signup",
        "email_verification",
        "phone_verification",
        "password_reset",
        "founder_premium_activated",
        "payment_succeeded",
        "payment_failed",
        "follow",
        "post_comment",
        "message",
        "live_started",
        "status_reaction",
        "marketplace_order",
        "crypto_price_alert",
        "new_device",
    ]:
        require(event in NOTIFICATION_SERVICE, f"missing event mapping: {event}", failures)
    for symbol in [
        "NotificationService",
        "def send_email_notification",
        "def send_sms_notification",
        "def send_in_app_channel_notification",
        "def send_multi_channel_notification",
        "sendEmailNotification",
        "sendSmsNotification",
        "sendInAppNotification",
        "sendMultiChannelNotification",
    ]:
        require(symbol in NOTIFICATION_SERVICE, f"missing service symbol: {symbol}", failures)
    for symbol in ["BREVO_NOTIFICATION_TEMPLATES", "welcome", "founder_premium_activated", "payment_receipt", "new_follower", "new_message", "crypto_alert", "security_alert"]:
        require(symbol in NOTIFICATION_SERVICE, f"missing template symbol: {symbol}", failures)
    require("SECURITY_NOTIFICATION_TYPES" in NOTIFICATION_SERVICE and '"email": True' in NOTIFICATION_SERVICE, "security notifications must stay mandatory for email/in-app", failures)
    require("duplicate_suppressed" in NOTIFICATION_SERVICE and "_recent_duplicate" in NOTIFICATION_SERVICE, "duplicate suppression missing", failures)
    require("RATE_LIMITS" in NOTIFICATION_SERVICE and "rate_limited" in NOTIFICATION_SERVICE, "rate limiting missing", failures)
    require("pulse_notification_deliveries" in NOTIFICATION_SERVICE and "_log_pulse_delivery" in NOTIFICATION_SERVICE, "Pulse delivery logging missing", failures)
    require("BREVO_EMAIL_ENABLED" in EMAIL_SERVICE and "BREVO_EMAIL_ENABLED" in ENV_EXAMPLE, "Brevo email enable flag missing", failures)
    require("BREVO_SMS_API_KEY" in SMS_SERVICE and "BREVO_SMS_ENABLED" in SMS_SERVICE and "BREVO_SMS_SENDER" in SMS_SERVICE, "Brevo SMS configuration missing", failures)
    require("Pulse verification code" in SMS_SERVICE and "Pulse SMS test" in SMS_SERVICE, "SMS copy is not Pulse-branded", failures)
    require("brevo_sms" in HEALTH_ENGINE, "notification health does not report Brevo SMS", failures)
    require("/admin/notifications" in BOT and "/admin/notification-delivery" in BOT, "admin notification visibility missing", failures)
    if failures:
        print("Brevo notification audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("brevo notification audit ok")


if __name__ == "__main__":
    main()
