#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
EMAIL_SERVICE = (ROOT / "services" / "email_service.py").read_text(encoding="utf-8")
REPORT = ROOT / "reports" / "production_email_delivery_incident.md"


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    failures = []
    require("BREVO_SMTP_URL" in EMAIL_SERVICE, "Brevo SMTP API URL missing", failures)
    require("sender_email_source" in EMAIL_SERVICE, "sender source diagnostics missing from email service", failures)
    require("using_default_sender" in EMAIL_SERVICE, "sender fallback diagnostic missing from email service", failures)
    require('api_key = (os.getenv("BREVO_API_KEY") or "").strip()' in EMAIL_SERVICE, "Brevo API key must be stripped before provider requests", failures)
    require("api_key_has_surrounding_whitespace" in EMAIL_SERVICE, "Brevo API key whitespace diagnostic missing", failures)
    require("sender_email_masked" in BOT, "admin diagnostics must return masked sender email", failures)
    require('"sender_masked"' in BOT, "direct-test API must return masked sender email", failures)
    require("/api/admin/email/diagnostics" in BOT, "admin email diagnostics endpoint missing", failures)
    require("/api/admin/email/direct-test" in BOT, "admin direct email test endpoint missing", failures)
    require("/api/brevo/webhook" in BOT and "/webhooks/brevo" in BOT, "Brevo webhook route missing", failures)
    require("provider_message_id" in BOT and "provider_status_code" in BOT, "provider response logging missing", failures)
    require("send_account_confirmation_email" in BOT, "signup confirmation sender missing", failures)
    require("send_password_reset_email" in BOT, "password reset sender missing", failures)
    require("retry_failed_email_queue" in BOT and "/admin/emails/retry-failed" in BOT, "failed email queue retry recovery missing", failures)
    require(REPORT.exists(), "production email incident report missing", failures)
    if failures:
        raise SystemExit("production email delivery incident audit failed:\n- " + "\n- ".join(failures))
    print("production email delivery incident audit ok")


if __name__ == "__main__":
    main()
