#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
NOTIFICATIONS = (ROOT / "services" / "notification_service.py").read_text(encoding="utf-8")


def main():
    failures = []
    if "/api/brevo/webhook" not in BOT or "/webhooks/brevo" not in BOT:
        failures.append("Brevo delivery webhook route missing")
    for column in ["trace_id", "retry_count", "delivery_status", "last_webhook_event"]:
        if column not in BOT:
            failures.append(f"email log column missing: {column}")
    if "send_email_notification" not in NOTIFICATIONS:
        failures.append("Pulse notification email sender missing")
    if "BREVO_EMAIL_ENABLED" not in NOTIFICATIONS:
        failures.append("notification service does not honor Brevo email flag")
    if failures:
        raise SystemExit("notification delivery audit failed:\n- " + "\n- ".join(failures))
    print("notification delivery audit ok")


if __name__ == "__main__":
    main()
