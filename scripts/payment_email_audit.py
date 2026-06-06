#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "services" / "email_service.py").read_text(encoding="utf-8")


def main():
    failures = []
    if "send_upgrade_confirmation_email" not in BOT or "send_payment_confirmation" not in SERVICE:
        failures.append("payment confirmation email sender missing")
    if "payment_confirmation" not in BOT:
        failures.append("admin/payment email log coverage missing")
    if "provider_message_id" not in BOT or "provider_status_code" not in BOT:
        failures.append("provider response details are not logged")
    if failures:
        raise SystemExit("payment email audit failed:\n- " + "\n- ".join(failures))
    print("payment email audit ok")


if __name__ == "__main__":
    main()
