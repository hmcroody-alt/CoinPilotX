#!/usr/bin/env python3
"""Audit PulseSoc email/SMS sender identity and visible notification copy."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV = (ROOT / ".env.example").read_text(encoding="utf-8")
EMAIL_SERVICE = (ROOT / "services" / "email_service.py").read_text(encoding="utf-8")
SMS_SERVICE = (ROOT / "services" / "sms_service.py").read_text(encoding="utf-8")
NOTIFICATION_SERVICE = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
TWILIO = (ROOT / "pulse_communications_v2" / "twilio_service.py").read_text(encoding="utf-8")
SMS_PROVIDER = (ROOT / "services" / "providers" / "sms_provider.py").read_text(encoding="utf-8")
HEALTH_ENGINE = (ROOT / "services" / "notification_health_engine.py").read_text(encoding="utf-8")


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    failures = []
    expected_env = {
        "DEFAULT_FROM_EMAIL": "support@pulsesoc.com",
        "BREVO_SENDER_EMAIL": "support@pulsesoc.com",
        "BREVO_REPLY_TO": "support@pulsesoc.com",
        "BREVO_SENDER_NAME": "PulseSoc",
        "MAIL_FROM_ADDRESS": "support@pulsesoc.com",
        "MAIL_FROM_NAME": "PulseSoc",
        "SUPPORT_EMAIL": "support@pulsesoc.com",
        "PUBLIC_SUPPORT_EMAIL": "support@pulsesoc.com",
        "COMPANY_NAME": "CoinPlotXAI Inc.",
        "PRODUCT_NAME": "PulseSoc",
        "BREVO_SMS_SENDER": "PulseSoc",
        "SMS_SENDER_NAME": "PulseSoc",
        "BREVO_SMS_REPLY_EMAIL": "support@pulsesoc.com",
    }
    for key, value in expected_env.items():
        require(f"{key}={value}" in ENV, f".env.example must set {key} to {value}", failures)

    require(("no" + "reply@pulsesoc.com") not in ENV, ".env.example must not default to noreply sender", failures)
    require("OFFICIAL_SUPPORT_EMAIL = \"support@pulsesoc.com\"" in EMAIL_SERVICE, "email service official support email missing", failures)
    require("OFFICIAL_PRODUCT_NAME = \"PulseSoc\"" in EMAIL_SERVICE, "email service product name missing", failures)
    require("OFFICIAL_COMPANY_NAME = \"CoinPlotXAI Inc.\"" in EMAIL_SERVICE, "email service company name missing", failures)
    require("\"BREVO_SENDER_EMAIL\"" in EMAIL_SERVICE and "\"BREVO_REPLY_TO\"" in EMAIL_SERVICE, "Brevo sender/reply-to envs not supported", failures)
    require("payload[\"replyTo\"]" in EMAIL_SERVICE, "Brevo API payload must include replyTo", failures)
    require("\"BREVO_SENDER_EMAIL\", _clean_env(\"BREVO_SENDER_EMAIL\")" in EMAIL_SERVICE, "official Brevo sender must win over legacy aliases", failures)

    require("sms_sender_name" in SMS_SERVICE and "SMS_SENDER_NAME" in SMS_SERVICE, "Brevo SMS sender name alias missing", failures)
    require("PulseSoc verification code" in SMS_SERVICE, "Brevo SMS verification copy must say PulseSoc", failures)
    require("PulseSoc SMS test" in SMS_SERVICE, "Brevo SMS test copy must say PulseSoc", failures)
    require("PulseSoc security alert" in TWILIO, "Twilio security fallback copy must say PulseSoc", failures)
    require("PulseSoc verification code" in TWILIO, "Twilio verification fallback copy must say PulseSoc", failures)
    require("SMS_SENDER_NAME" in SMS_PROVIDER and "SMS_SENDER_NAME" in HEALTH_ENGINE, "SMS health/provider checks must support SMS_SENDER_NAME", failures)

    require("PulseSoc&trade; &bull; Built by CoinPlotXAI Inc. Support: support@pulsesoc.com" in NOTIFICATION_SERVICE, "universal notification footer identity missing", failures)
    require("PulseSoc™ • Built by CoinPlotXAI Inc. Support: support@pulsesoc.com" in BOT, "branded email footer identity missing", failures)
    require("PulseSoc Brevo test email" in BOT and "PulseSoc Brevo debug email" in BOT, "Brevo test/debug emails must use PulseSoc identity", failures)
    require("Reset your PulseSoc password" in BOT, "password reset email must use PulseSoc", failures)
    require("Verify your PulseSoc email" in BOT, "verification email must use PulseSoc", failures)
    require("PulseSoc Payment Successful" in BOT and "Your PulseSoc Receipt and Billing Details" in BOT, "payment email subjects must use PulseSoc", failures)

    banned_notification_snippets = [
        "Reset your " + "CoinPilotX password",
        "Verify your " + "CoinPilotX email",
        "Coin" + "PlotXAI Payment Successful",
        "Your Coin" + "PlotXAI Receipt",
        "Coin" + "PlotXAI Support Ticket",
        "Coin" + "PlotXAI Security Report",
        "Coin" + "PlotXAI Inc. Brevo",
        "Pulse verification" + " code",
        "Pulse SMS" + " test",
        "Coin" + "PlotXAI security alert",
    ]
    notification_sources = {
        "bot.py": BOT,
        "services/email_service.py": EMAIL_SERVICE,
        "services/sms_service.py": SMS_SERVICE,
        "pulse_communications_v2/twilio_service.py": TWILIO,
        "services/notification_service.py": NOTIFICATION_SERVICE,
    }
    for path, text in notification_sources.items():
        for snippet in banned_notification_snippets:
            require(snippet not in text, f"{path} still contains old notification identity: {snippet}", failures)

    if failures:
        print("EMAIL_SMS_IDENTITY_AUDIT failed")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("EMAIL_SMS_IDENTITY_AUDIT ok")


if __name__ == "__main__":
    main()
