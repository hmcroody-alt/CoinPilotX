#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
NOTIFICATIONS = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")


def main():
    failures = []
    if "/api/brevo/webhook" not in BOT or "/webhooks/brevo" not in BOT:
        failures.append("Brevo delivery webhook route missing")
    if "/api/admin/email/diagnostics" not in BOT:
        failures.append("admin email diagnostics API missing")
    if "/api/admin/email/direct-test" not in BOT:
        failures.append("admin direct Brevo test API missing")
    if "ensure_email_logs_reporting_schema" not in BOT:
        failures.append("admin email logs page lacks schema drift guard")
    for column in ["trace_id", "retry_count", "delivery_status", "last_webhook_event"]:
        if column not in BOT:
            failures.append(f"email log column missing: {column}")
    for env_name in ["DEFAULT_FROM_EMAIL", "SUPPORT_EMAIL", "SECURITY_EMAIL", "PUBLIC_BASE_URL"]:
        if env_name not in BOT:
            failures.append(f"production email diagnostic missing env: {env_name}")
    for marker in ["sender_email_masked", "sender_email_source", "sender_domain", "using_default_sender"]:
        if marker not in BOT:
            failures.append(f"safe production email diagnostic missing marker: {marker}")
    if '"sender": (result.get("sender")' in BOT:
        failures.append("admin direct-test API exposes raw sender email instead of masked sender diagnostics")
    if "send_email_notification" not in NOTIFICATIONS:
        failures.append("Pulse notification email sender missing")
    if "BREVO_EMAIL_ENABLED" not in NOTIFICATIONS:
        failures.append("notification service does not honor Brevo email flag")
    if failures:
        raise SystemExit("notification delivery audit failed:\n- " + "\n- ".join(failures))
    print("notification delivery audit ok")


if __name__ == "__main__":
    main()
