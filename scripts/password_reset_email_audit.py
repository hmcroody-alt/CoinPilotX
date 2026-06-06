#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def main():
    failures = []
    if "public_url_for(\"reset_password_page\"" not in BOT:
        failures.append("password reset links do not use the public URL helper")
    if "send_password_reset_email" not in BOT:
        failures.append("password reset email sender missing")
    if "/send-reset-email" not in BOT:
        failures.append("admin password reset email route missing")
    if "password_reset" not in BOT:
        failures.append("password reset email log classification missing")
    if failures:
        raise SystemExit("password reset email audit failed:\n- " + "\n- ".join(failures))
    print("password reset email audit ok")


if __name__ == "__main__":
    main()
