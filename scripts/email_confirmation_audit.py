#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ACCOUNT = (ROOT / "templates" / "account.html").read_text(encoding="utf-8")


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    failures = []
    require("def send_account_confirmation_email" in BOT, "missing reusable account confirmation sender", failures)
    require("public_url_for(\"verify_email_page\"" in BOT, "confirmation link does not use production public URL helper", failures)
    require("webhook_app.test_request_context(base_url=base_url)" in BOT, "public URL helper cannot build confirmation links outside a web request", failures)
    require("PUBLIC_BASE_URL" in BOT and "https://coinpilotx.app" in BOT, "public URL helper does not support production base URL alias", failures)
    require("@webhook_app.route(\"/resend-confirmation\"" in BOT, "missing resend confirmation route", failures)
    require("login_unconfirmed" in BOT and "email_not_confirmed" in BOT, "login/mobile auth do not block unconfirmed accounts", failures)
    require("Resend Confirmation Email" in ACCOUNT, "login page does not expose resend confirmation button", failures)
    require("Check your email to confirm your account" in BOT, "missing clear confirmation guidance", failures)
    if failures:
        raise SystemExit("email confirmation audit failed:\n- " + "\n- ".join(failures))
    print("email confirmation audit ok")


if __name__ == "__main__":
    main()
